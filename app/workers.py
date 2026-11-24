# -*- coding: utf-8 -*-
"""统一后台线程 Worker 与生命周期管理。

所有 QThread 类集中在此（单一真相源），UI 层一律通过本模块导入使用。
swap_worker() 是线程替换的唯一入口——覆盖前断开旧线程信号并放入墓碑，
绝不 wait 阻塞 UI 主线程（v0.5.46/47 两次事故的最终方案）。

QThread 生命周期三原则：
    1. 不能运行中丢引用（会 C++ 层段错误，黑匣子抓不到）
    2. 不能 wait 阻塞主线程（界面冻结）
    3. 换新前：断信号 + 墓碑持引用 + 后台自然死亡
"""
import os
import threading

from PySide6.QtCore import QObject, QThread, Signal


def swap_worker(obj, attr, worker, max_grave=20):
    """把 worker 挂到 obj.attr，旧的断信号入墓碑（后台自然结束）。"""
    old_w = getattr(obj, attr, None)
    if old_w is not None and hasattr(old_w, "isRunning") and old_w.isRunning():
        # 关键：绝不能在当前信号回调栈内 disconnect 正在发信号的 sender
        # （Qt 内部会崩在 Qt6Gui 0xc0000005）——用 QTimer 延迟到事件循环下一轮
        def _retire(w=old_w):
            import warnings
            for sig_name in ("sig_done", "sig_log", "sig_progress",
                             "sig_prog", "sig_result"):
                sig = getattr(w, sig_name, None)
                if sig is not None:
                    try:
                        # 未连接过的信号 disconnect() 会刷 shiboken
                        # RuntimeWarning（"Failed to disconnect (None)"），
                        # try/except 拦不住 warning——catch_warnings 压掉
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore", RuntimeWarning)
                            sig.disconnect()
                    except Exception:
                        pass
            grave = [x for x in getattr(obj, "_graveyard", []) if x.isRunning()]
            grave.append(w)
            obj._graveyard = grave[-max_grave:]
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, _retire)
        except Exception:
            _retire()
    setattr(obj, attr, worker)


def wait_all(obj, attrs, timeout_ms=10000):
    """对象销毁/退出前等待指定 worker 属性收尾（最多 timeout_ms）。"""
    for attr in attrs:
        w = getattr(obj, attr, None)
        if w is not None and hasattr(w, "isRunning") and w.isRunning():
            try:
                w.sig_done.disconnect()
            except Exception:
                pass
            w.wait(timeout_ms)


# 扫描剪枝目录（小写精确匹配）：全深度递归时整棵跳过。
# 只列"确定不可能是 ComfyUI 实例根"的系统/缓存目录——宁可多扫几层，不漏实例。
_SCAN_PRUNE = {
    # Windows 系统与还原目录
    "windows", "$recycle.bin", "system volume information", "recovery",
    "documents and settings", "perflogs", "$windows.~ws", "$win.~bt",
    "$windows.~bt", "$sysreset", "$sysrq", "msocache",
    # 程序安装目录（实例不会长在这里，且极深极大）
    "program files", "program files (x86)", "program files (x7)",
    "programdata",
    # 源码/依赖缓存：深且杂，绝无实例
    ".git", "node_modules", "__pycache__", "site-packages",
    ".venv_build", "venv_build", ".cache", ".gradle", ".m2", ".npm",
    # 系统 junction 链接（会重复遍历甚至死循环）
    "appdata", "application data", "local settings", "onedrive",
}


class ScanWorker(QThread):
    """目录扫描线程：全深度递归找 ComfyUI 实例（main.py + comfy/）。
    2026-10-24 用户要求：选盘根（如 F:\\）必须扫描全部子文件夹，不再限两层。
    · 剪枝 _SCAN_PRUNE：系统/缓存目录整棵跳过（否则全盘扫描几万个文件夹白扫）
    · 发现实例即收录、且不下钻其内部（插件/模型目录里可能有伪装候选）
    · sig_progress：每检查 ~100 个文件夹发一次，UI 显示"已检查 N 个"防假死观感"""
    sig_done = Signal(str, list)   # root, candidates
    sig_progress = Signal(int)     # 已检查文件夹数

    def __init__(self, root):
        super().__init__()
        self.root = root

    @staticmethod
    def _is_inst(d):
        try:
            from app.config import is_comfyui_dir
            return bool(is_comfyui_dir(d))
        except Exception:
            return False

    def run(self):
        root = self.root
        candidates = []
        count = 0
        # root 自身就是实例：直接返回（不再下钻，等价旧版"(此目录)"候选）
        if self._is_inst(root):
            self.sig_progress.emit(1)
            self.sig_done.emit(root, [root])
            return
        try:
            for dirpath, dirnames, _files in os.walk(root, topdown=True):
                keep = []
                for d in dirnames:
                    if d.lower() in _SCAN_PRUNE:
                        continue
                    p = os.path.join(dirpath, d)
                    if self._is_inst(p):
                        candidates.append(p)   # 收录实例、剪掉其子树（不下钻）
                    else:
                        keep.append(d)
                dirnames[:] = keep              # 就地修改 = os.walk 跳过整棵子树
                count += 1
                if count % 100 == 0:
                    self.sig_progress.emit(count)
        except OSError:
            pass
        if count:
            self.sig_progress.emit(count)
        self.sig_done.emit(root, candidates)


class SetupWorker(QThread):
    """全自动一键安装线程（bootstrap.full_setup）"""
    sig_log = Signal(str)
    sig_progress = Signal(int, int)     # step, total
    sig_done = Signal(bool, dict, str)  # ok, result, errmsg

    def __init__(self, target_dir, python_exe, mirror, proxy, install_torch_flag):
        super().__init__()
        self.target_dir = target_dir
        self.python_exe = python_exe
        self.mirror = mirror
        self.proxy = proxy
        self.install_torch_flag = install_torch_flag

    def run(self):
        from app import bootstrap
        try:
            res = bootstrap.full_setup(
                self.target_dir, self.python_exe, self.mirror, self.proxy,
                log=lambda m: self.sig_log.emit(str(m)),
                install_torch_flag=self.install_torch_flag,
                progress=lambda s, t: self.sig_progress.emit(s, t))
            self.sig_done.emit(True, res, "")
        except Exception as e:
            self.sig_done.emit(False, {}, str(e))


class StepWorker(QThread):
    """通用后台执行器：跑 fn(*args)，返回 (ok, result, err)"""
    sig_done = Signal(bool, object, str)

    def __init__(self, fn, args=()):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            r = self.fn(*self.args)
            self.sig_done.emit(True, r, "")
        except Exception as e:
            self.sig_done.emit(False, None, str(e))


class BgWorker(QThread):
    """轻量后台执行器：跑 fn()，返回单值（异常返回 None）"""
    sig_done = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.sig_done.emit(self.fn())
        except Exception:
            self.sig_done.emit(None)


class RmWorker(QThread):
    """后台逐文件删除：实时上报进度（当前文件 / 已删 / 总数）"""
    sig_prog = Signal(str, int, int)   # 当前文件, 已删数, 总数
    sig_done = Signal(bool, str)       # ok, err

    def __init__(self, root, files):
        super().__init__()
        self.root = root
        self.files = files

    def run(self):
        import shutil as _sh
        total = len(self.files)
        done = 0
        try:
            for fp in self.files:
                try:
                    os.remove(fp)
                except OSError:
                    pass
                done += 1
                if done % 100 == 0 or done == total:
                    self.sig_prog.emit(fp, done, total)
            for root, dirs, _f in os.walk(self.root, topdown=False):
                for d in dirs:
                    try:
                        os.rmdir(os.path.join(root, d))
                    except OSError:
                        pass
            try:
                _sh.rmtree(self.root, ignore_errors=True)
            except OSError:
                pass
            self.sig_done.emit(True, "")
        except Exception as e:
            self.sig_done.emit(False, str(e))


# ================================================================ 远程运维
class SshTestWorker(QThread):
    """「测试 SSH」：真实建一次连接并认证（网络 IO 绝不能在 UI 线程）

    `sig_done(ok, kind)`：kind 是 `remote_ssh.SshError.kind`（成功时空串），
    失败详情放在 `self.detail` 里（文案由 UI 按 kind 查，worker 不认识文案）。
    """

    sig_done = Signal(bool, str)

    def __init__(self, inst, parent=None):
        super().__init__(parent)
        self.inst = dict(inst or {})
        self.kind = ""
        self.detail = ""

    def run(self):
        from app import remote_ssh
        try:
            sess = remote_ssh.manager.session(self.inst)
            if not sess.ensure_alive():
                raise remote_ssh.SshError("unreachable", "SSH 会话建不起来")
            self.kind = ""
            self.detail = ""
            self.sig_done.emit(True, "")
        except Exception as e:              # noqa: BLE001 - 任何异常都要变成可展示结果
            self.kind = str(getattr(e, "kind", "") or "unknown")
            self.detail = str(getattr(e, "detail", "") or e)[:300]
            self.sig_done.emit(False, self.kind)


class RemoteOpsWorker(QThread):
    """包装 `remote_ops.start/stop`（耗时轮询、SSH IO 全在这条线程里）

    · `cancel()` 置 `threading.Event`：启动等待中被打断 → `cancelled`
    · `sig_done` 的 payload 是纯 dict（UI 只按 `err_kind` 查文案，绝不匹配字符串）
    · 独立实例锁在 remote_ops 里；「启动等待中点停止」由 UI 先 cancel 再起 stop worker，
      这里对 `busy` 做短暂重试（最多 5 秒），避免用户点了没反应
    """

    sig_log = Signal(str)
    sig_state = Signal(str)
    sig_done = Signal(dict)

    def __init__(self, inst, action: str = "start", channel: str = "ssh",
                 parent=None):
        super().__init__(parent)
        self.inst = dict(inst or {})
        self.action = "stop" if str(action).lower() == "stop" else "start"
        self.channel = str(channel or "ssh")
        self._cancel = threading.Event()
        self.kind = ""
        self.detail = ""

    # ---------- 控制 ----------
    def cancel(self):
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    # ---------- 线程体 ----------
    def _on_state(self, text: str):
        self.sig_state.emit(str(text))

    def _on_log(self, text: str):
        self.sig_log.emit(str(text))

    def run(self):
        from app import remote_ops
        payload = {
            "ok": False, "err_kind": "", "detail": "", "action": self.action,
            "channel": self.channel, "state": remote_ops.ST_IDLE,
            "pre_ready": False, "elapsed": 0.0, "last_lines": [],
            "code": None, "detached": False, "cancelled": False,
            "iid": str(self.inst.get("id") or ""),
        }
        try:
            r = self._run_ops(remote_ops)
            payload.update({
                "ok": bool(r.ok), "err_kind": r.err_kind, "detail": r.detail,
                "state": r.state, "pre_ready": bool(r.pre_ready),
                "elapsed": float(r.elapsed), "last_lines": list(r.last_lines),
                "code": r.code, "detached": bool(r.detached),
                "cancelled": bool(r.cancelled),
            })
        except Exception as e:              # noqa: BLE001 - OpsError / 注入桩异常
            self.kind = str(getattr(e, "kind", "") or "unknown")
            self.detail = str(getattr(e, "detail", "") or e)[:300]
            payload.update({"ok": False, "err_kind": self.kind,
                            "detail": self.detail,
                            "state": remote_ops.ST_FAILED})
        self.sig_done.emit(payload)

    def _run_ops(self, remote_ops):
        """跑一次 ops；`busy` 短暂重试（等上一个动作放锁）"""
        deadline = 5.0
        waited = 0.0
        while True:
            try:
                if self.action == "stop":
                    return remote_ops.ops.stop(
                        self.inst, self.channel, on_state=self._on_state,
                        on_log=self._on_log, cancel=self.is_cancelled)
                return remote_ops.ops.start(
                    self.inst, self.channel, on_state=self._on_state,
                    on_log=self._on_log, cancel=self.is_cancelled)
            except remote_ops.OpsError as e:
                if e.kind != remote_ops.ERR_BUSY or waited >= deadline:
                    raise
                # 上一个动作（多半是「启动等待」）正在收尾：小步等它放锁
                self._sleep(0.2)
                waited += 0.2

    def _sleep(self, seconds: float):
        step = 0.02
        left = float(seconds)
        while left > 0 and not self._cancel.is_set():
            self.msleep(int(step * 1000))
            left -= step


class TunnelBridge(QObject):
    """把 `TunnelJob` 的线程内回调转成 **Qt 排队信号**

    为什么必须过信号：`TunnelJob` 在后台线程跑，回调里如果直接 `setText` 就是
    跨线程碰控件（Qt 在 Windows 上会随机崩，且抓不到栈）。这里回调只 `emit`，
    主线程收到的是排队连接，天然安全。

    `sig_state(iid, state, text)` / `sig_log(iid, text)` 都带实例 id：一个 bridge
    服务所有实例，主窗据此分辨「哪个实例的隧道变了」。
    """

    sig_state = Signal(str, str, str)
    sig_log = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._iid = ""

    def state_cb(self, iid: str):
        """返回可直接传给 `TunnelManager.start(on_state=...)` 的回调

        `TunnelJob._emit_state` 的调用约定是 `cb(state, text)`（见 tunnel.py）。
        """
        iid = str(iid or "")

        def _cb(state: str, text: str):
            self.sig_state.emit(iid, str(state or ""), str(text))
        return _cb

    def log_cb(self, iid: str):
        iid = str(iid or "")

        def _cb(text: str):
            self.sig_log.emit(iid, str(text))
        return _cb


# ================================================================ 远程产物清理
class CleanupStatsWorker(QThread):
    """后台统计远程产物占用（几十 GB 的 os.walk 绝不能在 UI 线程）

    `force=True` 强制重扫（「重新统计」按钮）；结果 dict 直接来自
    `remote_cleanup.stats_rows()`，UI 只负责画表。
    """

    sig_done = Signal(dict)

    def __init__(self, insts, cfg=None, force: bool = False, parent=None):
        super().__init__(parent)
        self.insts = list(insts or [])
        self.cfg = cfg
        self.force = bool(force)

    def run(self):
        from app import remote_cleanup
        try:
            res = remote_cleanup.stats_rows(self.insts, self.cfg, force=self.force)
        except Exception as e:          # noqa: BLE001 - 后台线程不能死
            res = {"rows": [], "total": {"files": 0, "bytes": 0},
                   "root": "", "error": str(e)[:200]}
        self.sig_done.emit(res)


class CleanupWorker(QThread):
    """后台清理：`path` 非空 = 清一个实例；为空 = 全部清空"""

    sig_done = Signal(dict)

    def __init__(self, path: str = "", insts=None, cfg=None,
                 min_age_s: float = 0.0, parent=None):
        super().__init__(parent)
        self.path = str(path or "")
        self.insts = list(insts or [])
        self.cfg = cfg
        self.min_age_s = float(min_age_s)

    def run(self):
        from app import remote_cleanup
        try:
            if self.path:
                res = remote_cleanup.clean_dir(self.path, self.min_age_s)
            else:
                res = remote_cleanup.clean_all(self.insts, self.cfg)
        except Exception as e:          # noqa: BLE001
            res = {"deleted": 0, "skipped": 0, "freed": 0,
                   "errors": [str(e)[:200]], "dir": self.path}
        self.sig_done.emit(res)

