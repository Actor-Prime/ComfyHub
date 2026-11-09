# -*- coding: utf-8 -*-
"""download_multi 断点保护单测（2026-11-09 烧断点事故回归）。

场景 A：所有源瞬时故障（网络抖动/代理不可用）→ 必须**保留 .part**、返回 False，
        绝不清空进度（旧策略会把 GB 级断点烧掉从头下）
场景 B：所有源明确拒绝续传（回 200 而非 206）→ 只有这种情况才清空从头下，
        且重下一次成功（保留原有"仅一次"保险丝）

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
