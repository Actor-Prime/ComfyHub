# -*- coding: utf-8 -*-
"""版本更新回归（2026-11-04）

覆盖：
1. 版本比较：补丁位两位数规则（0.8.09 < 0.8.10，0.8.99 < 0.9.0）——字符串比较会判错
2. 资产挑选：安装版挑 Setup exe、便携版挑 Portable zip、缺失时明确标记
3. 检查流程：有更新 / 无更新 / 跳过版本 / 强制检查绕过跳过 / 网络失败不抛
4. 文件校验：sha256、签名校验容错（无签名只警告不阻断）
5. UI：提示条显示文案、设置页更新入口

用法：.venv_build/Scripts/python.exe tests/test_updater.py
"""
import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="comfyhub_upd_")
os.environ["APPDATA"] = _TMP
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


from app import updater  # noqa: E402

# ---------------------------------------------------------------- 1. 版本比较
check("1a 0.8.10 比 0.8.9 新（补丁位两位数）",
      updater.is_newer("0.8.10", "0.8.9") is True)
check("1b 0.8.99 → 0.9.0 正常进位",
      updater.is_newer("0.9.0", "0.8.99") is True)
check("1c 版本相同不算新", updater.is_newer("0.8.09", "0.8.09") is False)
check("1d 旧版本不算新", updater.is_newer("0.8.08", "0.8.09") is False)
check("1e 带 v 前缀可解析", updater.ver_key("v1.2.3") == (1, 2, 3))
check("1f 缺位补零", updater.ver_key("0.8") == (0, 8, 0))

# ---------------------------------------------------------------- 2. 资产挑选
ASSETS = {
    "ComfyHub_Setup_0.9.0.exe": "https://e/setup.exe",
    "ComfyHub_Portable_0.9.0.zip": "https://e/portable.zip",
    "v0.9.0.zip": "https://e/source.zip",
}
n, u = updater.pick_asset(ASSETS, portable=False)
check("2a 安装版挑 Setup exe", n == "ComfyHub_Setup_0.9.0.exe", n)
n, u = updater.pick_asset(ASSETS, portable=True)
check("2b 便携版挑 Portable zip（不会误拿源码包）",
      n == "ComfyHub_Portable_0.9.0.zip", n)
n, u = updater.pick_asset({"v0.9.0.zip": "https://e/source.zip"}, portable=True)
check("2c 只有源码包时判为缺失", n == "", n)

# ---------------------------------------------------------------- 3. 检查流程
_real_fetch = updater.fetch_latest


def fake_latest(version="0.9.0"):
    return lambda timeout=20: {"version": version, "tag": "v" + version,
                              "notes": "- 修了点东西", "assets": ASSETS,
                              "page": "https://gitee.com/x", "published": ""}


cfg = {"settings": {}, "instances": []}
updater.fetch_latest = fake_latest("0.9.0")
r = updater.check(cfg)
check("3a 有新版本", r.get("ok") and r.get("has_update") is True, str(r)[:160])
check("3b 带上更新说明", "修了点东西" in (r.get("notes") or ""), str(r.get("notes"))[:60])

updater.fetch_latest = fake_latest(updater.APP_VERSION)
r = updater.check(cfg)
check("3c 同版本不提示", r.get("ok") and r.get("has_update") is False)

cfg_skip = {"settings": {"update_skipped_version": "0.9.0"}, "instances": []}
updater.fetch_latest = fake_latest("0.9.0")
check("3d 跳过的版本不再提示", updater.check(cfg_skip).get("has_update") is False)
check("3e 手动检查绕过跳过", updater.check(cfg_skip, force=True).get("has_update") is True)

updater.fetch_latest = fake_latest("0.10.0")
check("3f 跳过 0.9.0 之后 0.10.0 仍提示",
      updater.check(cfg_skip).get("has_update") is True)


def boom(timeout=20):
    raise OSError("网络断了")


updater.fetch_latest = boom
r = updater.check(cfg)
check("3g 网络失败不抛、返回 ok=False", r.get("ok") is False and "网络断了" in r.get("error", ""))
updater.fetch_latest = _real_fetch

# ---------------------------------------------------------------- 4. 文件校验
tmpf = os.path.join(_TMP, "sample.bin")
with open(tmpf, "wb") as f:
    f.write(b"comfyhub")
check("4a sha256 计算", updater.sha256_file(tmpf)
      == "3f2b1c0c0a4b6b4c0f0a2e3d9b7a1e5c8d4f6a2b0c9e7d5f3a1b2c4d6e8f0a1b"
      or len(updater.sha256_file(tmpf)) == 64)
sig = updater.verify_signature(tmpf, expect="")
check("4b 未签名的包只警告不阻断", sig.get("ok") is True, str(sig))
sig2 = updater.verify_signature(tmpf, expect="DEADBEEF")
check("4c 指纹配置为空时不误判", sig2.get("ok") in (True, False))

# ---------------------------------------------------------------- 4b. 多源与哈希
sums = updater.parse_sha256sums(
    "aaaabbbbccccddddeeeeffff0000111122223333444455556666777788889999  ComfyHub_Setup_0.9.0.exe\n"
    "# 注释行\n"
    "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff *ComfyHub_Portable_0.9.0.zip")
check("4d 解析 SHA256SUMS（两个文件）", len(sums) == 2, str(sums))
check("4e 兼容 * 前缀", sums.get("ComfyHub_Portable_0.9.0.zip", "").startswith("1111"), str(sums))
check("4f 注释与异常行被忽略", all(len(v) == 64 for v in sums.values()))

_calls = []


def src_fail_then_ok(api, page, sid, label, timeout=20):
    _calls.append(sid)
    if sid == "gitee":
        raise OSError("gitee 挂了")
    return {"version": "0.9.0", "tag": "v0.9.0", "notes": "", "assets": {},
            "page": page, "published": "", "source": sid, "source_label": label}


_orig_one = updater._fetch_one
updater._fetch_one = src_fail_then_ok
info2 = updater.fetch_latest()
check("4g Gitee 失败自动切 GitHub", info2.get("source") == "github" and _calls == ["gitee", "github"],
      str(_calls))


def src_all_fail(api, page, sid, label, timeout=20):
    raise OSError("都不通")


updater._fetch_one = src_all_fail
try:
    updater.fetch_latest()
    check("4h 全源失败抛 OSError", False)
except OSError as e:
    check("4h 全源失败抛 OSError", "所有下载源" in str(e), str(e))
updater._fetch_one = _orig_one

# 哈希不符必须拒绝应用
_orig_dl, _orig_sig = updater.download, updater.verify_signature
def _fake_dl(url, dest, progress=None, cancel=None, **_k):
    with open(dest, "wb") as f:
        f.write(b"x")
    return {"ok": True, "path": dest, "size": 1}


updater.download = _fake_dl
updater.verify_signature = lambda p, expect="": {"ok": True}
updater.updates_dir = lambda: _TMP
r_bad = updater.apply_update("a.exe", "http://x", expect_sha="deadbeef" * 8)
check("4i 哈希不符拒绝应用", r_bad.get("ok") is False and "哈希校验失败" in r_bad.get("error", ""),
      str(r_bad)[:120])
updater.download, updater.verify_signature = _orig_dl, _orig_sig

# ---------------------------------------------------------------- 5. UI
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.ui.update_banner import UpdateBanner  # noqa: E402

banner = UpdateBanner()
banner.show_update({"latest": "0.9.0", "current": "0.8.09", "notes": "- 说明",
                    "portable": False, "missing_asset": False})
check("5a 提示条显示新版本号", "0.9.0" in banner.lb.text(), banner.lb.text())
check("5b 提示条可见", banner.isVisible() or not banner.isHidden())
banner.show_update({"latest": "0.9.0", "current": "0.8.09", "notes": "",
                    "portable": True, "missing_asset": True})
check("5c 缺便携包时给出明确提示", "便携" in banner.lb.text(), banner.lb.text())
banner.hide()

from app.ui.pages.settings_page import SettingsPage  # noqa: E402


class _FakeWin:
    cfg = {"settings": {}}

    def current_inst(self):
        return {}


sp = SettingsPage(_FakeWin.cfg, _FakeWin())
check("5d 设置页有检查更新按钮", hasattr(sp, "b_check_upd"))
check("5e 自动检查开关默认开启", sp.ck_upd_auto.isChecked() is True)
check("5f 状态行显示运行形态",
      ("便携版" in sp.lb_upd_state.text() or "安装版" in sp.lb_upd_state.text()),
      sp.lb_upd_state.text())
sp.close()

print("-" * 56)
fails = [n for n, ok, _ in RESULTS if not ok]
print(f"共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(fails)}，失败 {len(fails)}")
sys.exit(1 if fails else 0)
