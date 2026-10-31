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

from PySide6.QtCore import QThread, Signal


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
