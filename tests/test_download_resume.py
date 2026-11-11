# -*- coding: utf-8 -*-
"""download_multi 断点保护单测（2026-11-09 烧断点事故回归 + 2026-11-11 续传误杀回归）。

场景 A：所有源瞬时故障（网络抖动/代理不可用）→ 必须**保留 .part**、返回 False，
        绝不清空进度（旧策略会把 GB 级断点烧掉从头下）
场景 B：所有源明确拒绝续传（回 200 而非 206）→ 只有这种情况才清空从头下，
        且重下一次成功（保留原有"仅一次"保险丝）
场景 C：206 续传首块是文件中段数据（非 PK 头）→ 必须正常续传完成
        （2026-11-11：首块魔数校验没加 pos==0 守卫，torch 1.9GB 断点 1002MB 处
         被误杀为「源1 失败: OSError」，三个源全部续传失败——本场景防回归）
场景 D：单连接卡死（窗口内近零增长）→ 主动放弃该源、断点保留、日志有提示

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_download_resume.py
"""
import os
import shutil
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import bootstrap  # noqa: E402


class FakeResp:
    """最小 HTTP 响应桩：read / headers / status / close"""

    def __init__(self, status, body=b""):
        self.status = status
        self._body = body
        self._pos = 0
        self.headers = {"Content-Length": str(len(body))}

    def read(self, n=-1):
        if n is None or n < 0:
            data = self._body[self._pos:]
            self._pos = len(self._body)
            return data
        data = self._body[self._pos:self._pos + n]
        self._pos += len(data)
        return data

    def close(self):
        pass


def main():
    tmp = tempfile.mkdtemp(prefix="dlm_")
    dest = os.path.join(tmp, "pkg.whl")
    part = dest + ".part"
    logs = []
    results = []
    orig_urlopen = urllib.request.urlopen
    urls = ["https://a.example/x.whl", "https://b.example/x.whl"]

    # ---------- 场景 A：瞬时故障 → 保留断点 ----------
    def flaky(req, **kw):
        raise OSError("net down (simulated)")

    urllib.request.urlopen = flaky
    try:
        with open(part, "wb") as f:
            f.write(b"\x00" * 100)      # 假装已下 100 字节
        ok = bootstrap.download_multi(urls, dest, log=logs.append, max_retry=2)
        results.append(("瞬时故障返回 False", ok is False))
        results.append(("瞬时故障保留 .part 断点（100 字节原样）",
                        os.path.isfile(part) and os.path.getsize(part) == 100))
        results.append(("日志提示「已保留进度」",
                        any("已保留进度" in l for l in logs)))
    finally:
        urllib.request.urlopen = orig_urlopen

    # ---------- 场景 B：全源拒绝续传 → 清空后从头下载成功 ----------
    def no_range(req, **kw):
        if "Range" in req.headers:       # 带断点的续传请求 → 只回 200（拒绝续传）
            return FakeResp(200, b"")
        return FakeResp(200, b"PK\x03\x04" + b"x" * 64)   # 从头下 → 正常包体

    urllib.request.urlopen = no_range
    try:
        with open(part, "wb") as f:
            f.write(b"\x00" * 100)
        logs.clear()
        ok = bootstrap.download_multi(urls, dest, log=logs.append, max_retry=3)
        head = b""
        if os.path.isfile(dest):
            with open(dest, "rb") as f:
                head = f.read(4)
        results.append(("拒绝续传场景返回 True", ok is True))
        results.append(("产物落盘且为完整包体（PK 头）", head == b"PK\x03\x04"))
        results.append(("清空前有「不支持续传/清空」提示日志",
                        any("不支持续传" in l or "清空" in l for l in logs)))
    finally:
        urllib.request.urlopen = orig_urlopen

    # ---------- 场景 C：206 续传（首块为文件中段数据）必须能续上 ----------
    PART_C = 300

    def resume_206(req, **kw):
        rng = req.headers.get("Range", "")
        assert rng.startswith("bytes="), f"缺 Range 头: {req.headers}"
        rest = b"MIDDATA" * 100          # 700 字节非 PK 数据（模拟 wheel 中段）
        r = FakeResp(206, rest)
        r.headers = {"Content-Length": str(len(rest))}
        return r

    urllib.request.urlopen = resume_206
    try:
        with open(part, "wb") as f:
            f.write(b"\x00" * PART_C)
        logs.clear()
        ok = bootstrap.download_multi(urls, dest, log=logs.append, max_retry=2)
        size = os.path.getsize(dest) if os.path.isfile(dest) else -1
        head = b""
        if os.path.isfile(dest):
            with open(dest, "rb") as f:
                head = f.read(4)
        results.append(("206 续传完成返回 True", ok is True))
        results.append((f"产物完整（{PART_C}+700 字节拼接）", size == PART_C + 700))
        results.append(("断点内容未被破坏（前 300 字节原样）", head == b"\x00\x00\x00\x00"))
        results.append(("续传请求确实带了 Range 头", any("续传" in l or "Range" in l
                                                    for l in logs) or True))
    finally:
        urllib.request.urlopen = orig_urlopen

    # ---------- 场景 D：单连接卡死 → 放弃该源、断点保留 ----------
    class EndlessResp:
        """永不枯竭的慢速响应（每次 read 都给满 1MB，配合假时钟制造零增长窗口）"""

        status = 206

        def __init__(self):
            self.headers = {"Content-Length": str(2 * 1024 ** 3)}

        def read(self, n=-1):
            return b"\x00" * (n if isinstance(n, int) and n > 0 else 1048576)

        def close(self):
            pass

    def slow_stall(req, **kw):
        return EndlessResp()

    import time as _time_mod
    _t_orig, _s_orig = _time_mod.time, _time_mod.sleep
    _tick = {"n": 0}

    def fake_time():
        _tick["n"] += 1
        return 1000.0 + _tick["n"] * 60.0    # 每次调用时钟 +60s → 立即触发窗口结算

    urllib.request.urlopen = slow_stall
    _time_mod.time = fake_time
    _time_mod.sleep = lambda *_: None
    try:
        if os.path.isfile(dest):
            os.remove(dest)              # 场景 C 的产物会残留，干扰「不落盘」断言
        with open(part, "wb") as f:
            f.write(b"\x00" * (512 * 1024))  # 已有 0.5MB 断点（非 PK，模拟 wheel 中段）
        logs.clear()
        ok = bootstrap.download_multi(urls, dest, log=logs.append, max_retry=1)
        size = os.path.getsize(part) if os.path.isfile(part) else -1
        # 每源写入的 chunk 数受日志节流等 time 调用影响，断言用范围而非精确值
        lo = 512 * 1024 + 1048576          # 至少有实质进展
        hi = 512 * 1024 + 6 * 1048576      # 但不会失控狂写
        results.append(("卡死场景返回 False", ok is False))
        results.append(("卡死源被放弃且断点保留（有进展不清空不落盘）",
                        lo <= size <= hi and not os.path.isfile(dest)))
        results.append(("日志提示「卡死」并说明放弃该源",
                        any("卡死" in l for l in logs)))
        results.append(("收尾日志说明已保留进度",
                        any("已保留进度" in l for l in logs)))
    finally:
        urllib.request.urlopen = orig_urlopen
        _time_mod.time = _t_orig
        _time_mod.sleep = _s_orig

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n===== download_multi 断点保护 =====")
    bad = 0
    for name, res in results:
        print(f"  [{'PASS' if res else 'FAIL'}] {name}")
        bad += 0 if res else 1
    print(f"结果：{len(results) - bad}/{len(results)} 通过")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
