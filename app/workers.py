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
            for sig_name in ("sig_done", "sig_log", "sig_progress",
                             "sig_prog", "sig_result"):
                sig = getattr(w, sig_name, None)
                if sig is not None:
                    try:
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


class ScanWorker(QThread):
    """目录扫描线程：找 main.py（含两层子目录）"""
    sig_done = Signal(str, list)   # root, candidates

    def __init__(self, root):
        super().__init__()
        self.root = root

    def run(self):
        root = self.root
        candidates = []
        search_roots = [root]
        try:
            for d1 in os.listdir(root):
                p1 = os.path.join(root, d1)
                if os.path.isdir(p1):
                    search_roots.append(p1)
                    for d2 in os.listdir(p1):
                        p2 = os.path.join(p1, d2)
                        if os.path.isdir(p2):
                            search_roots.append(p2)
        except OSError:
            pass
        from app.config import is_comfyui_dir
        for sr in search_roots:
            try:
                if is_comfyui_dir(sr):
                    candidates.append(sr)
            except Exception:
                pass
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
