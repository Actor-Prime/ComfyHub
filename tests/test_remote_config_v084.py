# -*- coding: utf-8 -*-
"""0.8.40 T01：配置层与打包基线

覆盖（ARCH §7 T01 验收 ①-⑤）：
  ① 0.8.39 老配置（无任何新字段）加载后自动补齐：ssh_port / tunnel_local_port /
     ssh_start_cmd / remote_cleanup_policy，且整次加载只落盘一次
  ② ssh_password 落盘是 DPAPI 密文、内存是明文；换机器解不开 → 字段变 "" 不抛异常
  ③ 脏值被 clamp：tunnel_local_port=70000 / remote_cleanup_days=0 / max_gb=9999
  ④ cloud_start / cloud_stop 深拷贝（两个实例不共享同一个 dict）
  ⑤ 打包基线：requirements 有 paramiko、spec 的 upx_exclude 三类、版本号 0.8.40
  ⑥ UI 一致性：每个新增实例字段都出现在 _TRACK / _SAVE_LABELS / _snapshot 三处
     （漏一处就是「改了不保存」）

用法：.venv_build/Scripts/python.exe -u tests/test_remote_config_v084.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ★ 必须在 import app.* 之前重定向数据目录（config.CONFIG_PATH 在导入期计算）
_TMP = tempfile.mkdtemp(prefix="comfyhub_cfg084_")
os.environ["APPDATA"] = _TMP
_CFG_DIR = os.path.join(_TMP, "ComfyHub")
os.makedirs(_CFG_DIR, exist_ok=True)

res = []


def check(n, ok, d=""):
    res.append((n, bool(ok), d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


# 一份真实的 0.8.39 配置：没有任何 0.8.40 新字段
_OLD_CFG = {
    "version": 1,
    "last_instance_id": "inst_l",
    "instances": [
        {"id": "inst_l", "name": "本机实例", "kind": "local",
         "comfyui_path": r"F:\ComfyUI", "python_path": r"F:\ComfyUI\python.exe",
         "port": 8188, "vram_mode": "自动", "extra_args": ""},
        {"id": "inst_r", "name": "云镜像", "kind": "remote",
         "base_url": "http://192.168.1.20:8188", "auth_type": "bearer",
         "auth_value": "tok", "tls_verify": True, "port": 8188,
         "comfyui_path": "", "python_path": "", "vram_mode": "自动",
         "extra_args": ""},
    ],
    "settings": {"ai_api_key": ""},
}
_CFG_FILE = os.path.join(_CFG_DIR, "config.json")
with io.open(_CFG_FILE, "w", encoding="utf-8") as f:
    json.dump(_OLD_CFG, f, ensure_ascii=False, indent=2)

from app import config  # noqa: E402

print("=== 1. 老配置自愈（补齐 + 只落盘一次）===")
_writes = []
_orig_save = config.save_config


def _counting_save(cfg):
    _writes.append(1)
    return _orig_save(cfg)


config.save_config = _counting_save
try:
    cfg = config.load_config()
finally:
    config.save_config = _orig_save

inst = cfg["instances"][0]
check("ssh_port 补齐为 22", inst.get("ssh_port") == 22, inst.get("ssh_port"))
check("ssh_auth 补齐为 password", inst.get("ssh_auth") == "password", inst.get("ssh_auth"))
check("tunnel_local_port 补齐为 8189", inst.get("tunnel_local_port") == 8189)
check("tunnel_remote_host/port 补齐",
      (inst.get("tunnel_remote_host"), inst.get("tunnel_remote_port")) == ("127.0.0.1", 8188))
check("ssh_start_cmd 非空且含 nohup", "nohup" in str(inst.get("ssh_start_cmd")))
check("ssh_stop_cmd 非空且含 pkill", "pkill" in str(inst.get("ssh_stop_cmd")))
check("cloud_start/cloud_stop 补成空 dict",
      inst.get("cloud_start") == {} and inst.get("cloud_stop") == {})
check("cloud_start_timeout_s=300", inst.get("cloud_start_timeout_s") == 300)
check("insecure_warn_muted=False", inst.get("insecure_warn_muted") is False)
check("last_ops_channel=ssh", inst.get("last_ops_channel") == "ssh")
check("settings.remote_cleanup_policy==days",
      cfg["settings"].get("remote_cleanup_policy") == "days")
check("settings.remote_cleanup_days==30", cfg["settings"].get("remote_cleanup_days") == 30)
check("settings.remote_cleanup_max_gb==10", cfg["settings"].get("remote_cleanup_max_gb") == 10)
check("settings.remote_cleanup_enabled 默认关",
      cfg["settings"].get("remote_cleanup_enabled") is False)
check("整次加载只落盘一次", len(_writes) == 1, f"{len(_writes)} 次")
with io.open(_CFG_FILE, encoding="utf-8") as f:
    disk = json.load(f)
check("盘上已有新字段", disk["instances"][0].get("ssh_port") == 22)
check("盘上也有 settings 新字段",
      disk["settings"].get("remote_cleanup_policy") == "days")

print("=== 2. 二次加载不再落盘（自愈幂等）===")
_writes2 = []
config.save_config = lambda c: _writes2.append(1)
try:
    config.load_config()
finally:
    config.save_config = _orig_save
check("已自愈的配置不再写盘", len(_writes2) == 0, f"{len(_writes2)} 次")

print("=== 3. DPAPI：落盘密文、内存明文 ===")
cfg = config.load_config()
cfg["instances"][0]["ssh_password"] = "abc123"
cfg["instances"][0]["ssh_key_passphrase"] = "pp-456"
config.save_config(cfg)
with io.open(_CFG_FILE, encoding="utf-8") as f:
    raw = json.load(f)
_on_disk = raw["instances"][0].get("ssh_password") or ""
if sys.platform == "win32":
    check("ssh_password 落盘是 enc:v1: 密文", _on_disk.startswith("enc:v1:"), _on_disk[:16])
    check("ssh_key_passphrase 落盘是密文",
          str(raw["instances"][0].get("ssh_key_passphrase") or "").startswith("enc:v1:"))
    with io.open(_CFG_FILE, encoding="utf-8") as f:
        pass
    cfg2 = config.load_config()
    check("内存里是明文", cfg2["instances"][0].get("ssh_password") == "abc123",
          cfg2["instances"][0].get("ssh_password"))
    check("口令也是明文", cfg2["instances"][0].get("ssh_key_passphrase") == "pp-456")
else:
    check("非 Windows 平台原样（跳过 DPAPI 断言）", True)

print("=== 4. 换机器解不开 → 当没填过，不抛异常 ===")
raw["instances"][0]["ssh_password"] = "enc:v1:QUJDREVGR0g="   # 合法 base64 但解不开
with io.open(_CFG_FILE, "w", encoding="utf-8") as f:
    json.dump(raw, f, ensure_ascii=False, indent=2)
try:
    cfg3 = config.load_config()
    ok = cfg3["instances"][0].get("ssh_password") == ""
    err = ""
except Exception as e:                      # noqa: BLE001
    ok, err = False, f"{type(e).__name__}: {e}"
check("解不开的密文变空串且不抛异常", ok, err)

print("=== 5. clamp 与归一 ===")
raw["instances"][0]["ssh_port"] = 70000
raw["instances"][0]["tunnel_local_port"] = 70000
raw["instances"][0]["tunnel_remote_port"] = 0
raw["instances"][0]["ssh_auth"] = "telnet"
raw["instances"][0]["last_ops_channel"] = "carrier-pigeon"
raw["instances"][0]["ssh_host"] = "  192.168.1.20  "
raw["settings"]["remote_cleanup_days"] = 0
raw["settings"]["remote_cleanup_max_gb"] = 9999
raw["settings"]["remote_cleanup_policy"] = "weekly"
with io.open(_CFG_FILE, "w", encoding="utf-8") as f:
    json.dump(raw, f, ensure_ascii=False, indent=2)
cfg4 = config.load_config()
i0 = cfg4["instances"][0]
st4 = cfg4["settings"]
check("ssh_port 70000 → 65535", i0["ssh_port"] == 65535, i0["ssh_port"])
check("tunnel_local_port 70000 → 65535", i0["tunnel_local_port"] == 65535, i0["tunnel_local_port"])
check("tunnel_remote_port 0 → 1", i0["tunnel_remote_port"] == 1, i0["tunnel_remote_port"])
check("ssh_auth 非法值回落 password", i0["ssh_auth"] == "password", i0["ssh_auth"])
check("last_ops_channel 非法值回落 ssh", i0["last_ops_channel"] == "ssh")
check("ssh_host 已 strip", i0["ssh_host"] == "192.168.1.20", repr(i0["ssh_host"]))
check("remote_cleanup_days 0 → 1", st4["remote_cleanup_days"] == 1, st4["remote_cleanup_days"])
check("remote_cleanup_max_gb 9999 → 500", st4["remote_cleanup_max_gb"] == 500)
check("remote_cleanup_policy 非法值回落 days", st4["remote_cleanup_policy"] == "days")
# 下界/上界都走一遍（纯函数，不碰盘）
n = {"ssh_port": -5, "tunnel_local_port": 80, "tunnel_remote_port": 99999,
     "cloud_start_timeout_s": 0, "cloud_start": "坏值", "insecure_warn_muted": 1}
config._normalize_inst_ops(n)
check("ssh_port 下界 → 1", n["ssh_port"] == 1, n["ssh_port"])
check("tunnel_local_port 80 → 1024", n["tunnel_local_port"] == 1024)
check("tunnel_remote_port 99999 → 65535", n["tunnel_remote_port"] == 65535)
check("cloud_start_timeout_s 0 → 1", n["cloud_start_timeout_s"] == 1)
check("cloud_start 非 dict → {}", n["cloud_start"] == {})
check("insecure_warn_muted 归一为 bool", n["insecure_warn_muted"] is True)
_n2 = {"insecure_warn_muted": "false"}
config._normalize_inst_ops(_n2)
check('字符串 "false" 不能变成「已忽略告警」', _n2["insecure_warn_muted"] is False,
      repr(_n2["insecure_warn_muted"]))
_n3 = {"insecure_warn_muted": "true"}
config._normalize_inst_ops(_n3)
check('字符串 "true" → True', _n3["insecure_warn_muted"] is True)

print("=== 6. cloud_start/cloud_stop 深拷贝（不共享默认对象）===")
d1 = config.default_instance()
d2 = config.default_instance()
d1["cloud_start"]["url"] = "http://a"
check("两个默认实例的 cloud_start 互不影响", d2["cloud_start"] == {}, d2["cloud_start"])
check("instance_ops_defaults 也深拷贝",
      config.instance_ops_defaults()["cloud_start"] == {})
check("默认实例已预填启动命令", "nohup" in config.default_instance()["ssh_start_cmd"])

print("=== 7. 打包基线 ===")
check("APP_VERSION == 0.8.40", config.APP_VERSION == "0.8.40", config.APP_VERSION)
with io.open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
    _req = f.read()
check("requirements 有 paramiko", "paramiko" in _req, _req)
_req_active = "\n".join(ln for ln in _req.splitlines()
                        if ln.strip() and not ln.strip().startswith("#")).lower()
check("requirements 只写直接依赖（不写传递依赖）",
      all(x not in _req_active for x in ("cryptography", "bcrypt", "pynacl")),
      _req_active)
_spec_path = os.path.join(ROOT, "packaging", "ComfyHub.spec")
if os.path.isfile(_spec_path):
    with io.open(_spec_path, encoding="utf-8") as f:
        _spec = f.read()
    check("spec: upx=True 仍开着", "upx=True" in _spec)
    for pat in ("_rust*.pyd", "_bcrypt*.pyd", "libcrypto-*.dll"):
        check(f"spec: upx_exclude 含 {pat}", pat in _spec)
else:
    # packaging/ComfyHub.spec 未被 git 跟踪（.gitignore 的 *.spec：spec 属打包产物），
    # 干净克隆里不存在 → 不在这里断言，真正的防线是下面的 build_exe.bat
    print("  ⏭  packaging/ComfyHub.spec 不存在（未入库的打包产物），跳过其断言")
# ★ 真正出包的是 build_exe.bat（CLI 参数，不走 packaging/ComfyHub.spec），
#   所以 upx 排除必须同时落在那里，否则这版修复永远进不了安装包。
with io.open(os.path.join(ROOT, "packaging", "build_exe.bat"), encoding="utf-8") as f:
    _bat = f.read()
for pat in ("_rust*.pyd", "_bcrypt*.pyd", "libcrypto-*.dll"):
    check(f"build_exe.bat: --upx-exclude {pat}", f'--upx-exclude "{pat}"' in _bat)
# cmd 的 ^ 续行是「同一命令行」，中间插 rem 会被当成 pyinstaller 的参数 → 打包直接失败
_lines = _bat.splitlines()
_start = next((i for i, ln in enumerate(_lines) if "pyinstaller.exe ^" in ln), -1)
_end = next((i for i, ln in enumerate(_lines) if 'main.py"' in ln), -1)
_inner = _lines[_start + 1:_end] if 0 <= _start < _end else []
check("build_exe.bat: 续行块里没有 rem 注释",
      bool(_inner) and not any(ln.strip().lower().startswith("rem")
                               for ln in _inner),
      f"start={_start} end={_end}")
with io.open(os.path.join(ROOT, "installer", "ComfyHub.nsi"), encoding="utf-8") as f:
    _nsi = f.read()
check("nsi: APP_VERSION 0.8.40", '!define APP_VERSION    "0.8.40"' in _nsi)
check("nsi: VIProductVersion 0.8.40.0", '"0.8.40.0"' in _nsi)
check("nsi: 不再有 0.8.39", "0.8.39" not in _nsi)
with io.open(os.path.join(ROOT, "packaging", "installer.iss"), encoding="utf-8") as f:
    _iss = f.read()
check("iss: MyAppVersion 0.8.40", '#define MyAppVersion "0.8.40"' in _iss, _iss[:220])
check("iss: 不再有 0.5.84", "0.5.84" not in _iss)

print("=== 8. UI 三处一致性（_TRACK / _SAVE_LABELS / _snapshot）===")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])
from app.ui.pages.comfyui_page import ComfyuiPage  # noqa: E402

_editable = [k for k in config._INST_DEFAULTS if k != "cloud_start_timeout_s"]
_missing_track = [k for k in _editable if k not in ComfyuiPage._TRACK]
_missing_label = [k for k in _editable if k not in ComfyuiPage._SAVE_LABELS]
check("14 个可编辑字段全部进 _TRACK", not _missing_track, "、".join(_missing_track))
check("全部有中文标签", not _missing_label, "、".join(_missing_label))
check("ssh_password/ssh_key_passphrase 留空文案为「未填写」",
      ComfyuiPage._SAVE_EMPTY_TEXT.get("ssh_password") == "未填写"
      and ComfyuiPage._SAVE_EMPTY_TEXT.get("ssh_key_passphrase") == "未填写")
check("cloud_start/cloud_stop 留空文案为「未配置」",
      ComfyuiPage._SAVE_EMPTY_TEXT.get("cloud_start") == "未配置"
      and ComfyuiPage._SAVE_EMPTY_TEXT.get("cloud_stop") == "未配置")


class _FakeProcs:
    """最小进程管理器桩：远程实例不参与本机进程判定"""

    def is_own(self, inst):
        return False

    def is_running(self, inst):
        return False

    def invalidate(self, inst=None):
        return None


class _FakeMainWin:
    def __init__(self, inst):
        self._inst = inst
        self.procs = _FakeProcs()

    def current_inst(self):
        return self._inst


_inst = dict(_OLD_CFG["instances"][1])
_inst.update({
    "ssh_host": "192.168.1.20", "ssh_user": "root", "ssh_port": 2222,
    "ssh_password": "pw", "ssh_start_cmd": "cd ~/ComfyUI && nohup python main.py &",
    "tunnel_local_port": 8190, "cloud_start": {"url": "http://x/api/start"},
})
page = None
try:
    page = ComfyuiPage({"version": 1, "instances": [_inst], "settings": {}},
                       _FakeMainWin(_inst))
    snap = page._snapshot()
    # 0.8.40 T05 起：页签是唯一取值入口（collect() 15 个可编辑字段），
    # 两个「不由 UI 回写」的字段故意不在 _snapshot 里：
    #   ssh_host_fingerprint 由 remote_fingerprint 首次连接写入、
    #   insecure_warn_muted  由明文告警模态 / 设置页写。
    _ui_skip = {"ssh_host_fingerprint", "insecure_warn_muted"}
    _missing_snap = [k for k in _editable if k not in _ui_skip and k not in snap]
    check("可编辑字段全部进 _snapshot（除 2 个非 UI 字段）",
          not _missing_snap, "、".join(_missing_snap))
    check("页签 collect() 覆盖同一个可编辑集合",
          set(page.remote_ops.collect()) == set(_editable) - _ui_skip,
          sorted(set(page.remote_ops.collect()) ^ (set(_editable) - _ui_skip)))
    check("_snapshot 保留实例真值",
          snap.get("ssh_host") == "192.168.1.20" and snap.get("ssh_port") == 2222
          and snap.get("tunnel_local_port") == 8190, str(snap.get("ssh_port")))
    check("_snapshot 的 cloud_start 是真值（四字段归一）",
          snap.get("cloud_start", {}).get("url") == "http://x/api/start"
          and set(snap.get("cloud_start") or {}) == {"url", "method", "headers", "body"},
          str(snap.get("cloud_start")))
    check("_snapshot 的 cloud_start 不与实例共享对象",
          snap.get("cloud_start") is not _inst.get("cloud_start"))
    check("加载后 _loaded 与 _snapshot 一致（不会刷假改动）",
          page._loaded == page._snapshot())
    check("只读实例不改配置",
          _inst.get("ssh_password") == "pw" and _inst.get("ssh_host") == "192.168.1.20")
finally:
    if page is not None:
        page.deleteLater()
    _app.processEvents()

print()
bad = [n for n, ok, _ in res if not ok]
print(f"结果：{len(res) - len(bad)}/{len(res)} 通过" +
      ("" if not bad else "  失败：" + "、".join(bad)))
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if bad else 0)
