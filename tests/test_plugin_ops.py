# -*- coding: utf-8 -*-
"""插件安装/卸载交互回归测试（2026-11-10 用户诉求）。

诉求三连：
  1. 安装完要弹「安装完成」弹窗 + 刷新列表
     （Git URL 安装路径此前只写日志无弹窗；市场装完后市场列表按钮
      不会变「已安装 ✓」）
  2. 卸载不要弹 CMD 黑窗（_trash_dir 曾裸 subprocess.run 跑 powershell，
     GUI 进程 spawn console 程序必弹黑窗——必须走 app.winsub.run）
  3. 装/卸完成后市场列表操作列状态同步刷新

断言：
  · _trash_dir 必须走 app.winsub.run；subprocess.run 换成炸弹守卫，
    裸调即炸（变异：改回裸 subprocess.run 必红）
  · _on_installed 成功 → 弹「安装完成」+ 刷已安装 + 刷市场 三者都触发
  · _on_installed 失败 → 弹「安装失败」
  · Git URL 安装（_install）完成回调走同一弹窗链路（FakeWorker 同步 emit）
  · _refresh_market_rows 有缓存时重渲染当前结果、无缓存时 no-op
  · _op_uninstall 成功 → 走 _trash_dir（隐藏窗口）+ 双列表刷新

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_plugin_ops.py
"""
import os
import subprocess
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_passed = 0
_failed = []


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  ok  {name}")
    else:
        _failed.append(name)
        print(f" FAIL {name}  {detail}")


class _MsgBox:
    """QMessageBox 桩：记录弹窗、question 恒 Yes（无头不阻塞）"""
    Yes, No = 1, 0
    calls = []

    @staticmethod
    def information(parent, title, text, *a, **k):
        _MsgBox.calls.append(("information", str(title), str(text)))

    @staticmethod
    def warning(parent, title, text, *a, **k):
        _MsgBox.calls.append(("warning", str(title), str(text)))

    @staticmethod
    def question(parent, title, text, *a, **k):
        _MsgBox.calls.append(("question", str(title), str(text)))
        return _MsgBox.Yes


class _FakeSelf:
    """PluginsPage 桩：只带被测方法触达的属性/方法，真方法按需 MethodType 绑定"""
    def __init__(self, tmp):
        self.tmp = tmp
        self._worker = None
        self._last_market_rows = None
        self.refreshed = 0
        self.market_refresh_calls = 0
        self.rendered = []
        self.tips = []
        self.mainwin = types.SimpleNamespace(
            procs=types.SimpleNamespace(is_running=lambda inst: False))
        self.lb_tip = types.SimpleNamespace(setText=self.tips.append)

    def _inst(self):
        return {"comfyui_path": self.tmp}

    def refresh_list(self):
        self.refreshed += 1

    def _log(self, m):
        pass

    def _op_row_data(self, row):
        return {"name": "DemoPlugin", "dir": os.path.join(self.tmp, "DemoPlugin"),
                "git": True, "url": ""}

    def _render_market(self, rows):
        self.rendered.append(list(rows))


def _bind(fake, *names):
    """把 PluginsPage 的真方法绑到 fake 上"""
    from app.ui.pages import plugins_page as pp
    for n in names:
        setattr(fake, n, types.MethodType(getattr(pp.PluginsPage, n), fake))


def main():
    import tempfile
    from PySide6.QtCore import QThread, Signal
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    from app.ui.pages import plugins_page as pp
    import app.winsub as winsub

    tmp = tempfile.mkdtemp(prefix="chub_plugin_ops_")

    # ---------------------------------------------------------- 1. 卸载不弹 CMD
    print("[1] _trash_dir 隐藏窗口（不裸调 subprocess.run）")
    sp_orig = subprocess.run
    winsub_orig = winsub.run
    calls = []

    def fake_hidden(cmd, **kw):
        calls.append((list(cmd), kw))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def boom(*a, **k):
        raise AssertionError("裸调 subprocess.run（GUI 下会弹 CMD 黑窗）")

    subprocess.run = boom          # 炸弹守卫：裸调即炸
    winsub.run = fake_hidden       # winsub.run 记录取代真执行
    try:
        pp.PluginsPage._trash_dir(os.path.join(tmp, "SomePlugin"))
        check("走 winsub.run 且传 powershell 回收站命令",
              len(calls) == 1 and "powershell" in calls[0][0][0]
              and "SendToRecycleBin" in calls[0][0][3],
              f"calls={calls}")
    except AssertionError as e:
        check("走 winsub.run 且传 powershell 回收站命令", False, str(e))
    finally:
        subprocess.run = sp_orig
        winsub.run = winsub_orig

    # ---------------------------------------------------------- 2. 回收站失败上抛
    print("[2] _trash_dir 失败时 RuntimeError")
    winsub.run = lambda cmd, **kw: types.SimpleNamespace(
        returncode=1, stdout="", stderr="access denied")
    try:
        try:
            pp.PluginsPage._trash_dir(os.path.join(tmp, "X"))
            check("失败抛 RuntimeError", False, "未抛异常")
        except RuntimeError as e:
            check("失败抛 RuntimeError", "access denied" in str(e), str(e))
    finally:
        winsub.run = winsub_orig

    # ---------------------------------------------------------- 3. 安装完成弹窗+双刷新
    print("[3] _on_installed 成功 → 弹窗 + 刷已安装 + 刷市场")
    _MsgBox.calls = []
    fake = _FakeSelf(tmp)
    fake._refresh_market_rows = lambda: setattr(
        fake, "market_refresh_calls", fake.market_refresh_calls + 1)
    _bind(fake, "_on_installed")
    pp.QMessageBox = _MsgBox
    try:
        fake._on_installed(True, "ok", "LoraManager")
        titles = [c[1] for c in _MsgBox.calls]
        check("弹「安装完成」", "安装完成" in titles, f"titles={titles}")
        check("refresh_list 被调", fake.refreshed == 1, f"{fake.refreshed}")
        check("市场列表刷新被调", fake.market_refresh_calls == 1,
              f"{fake.market_refresh_calls}")
    finally:
        pass

    # ---------------------------------------------------------- 4. 安装失败弹窗
    print("[4] _on_installed 失败 → 弹「安装失败」")
    _MsgBox.calls = []
    fake2 = _FakeSelf(tmp)
    fake2._refresh_market_rows = lambda: None
    _bind(fake2, "_on_installed")
    fake2._on_installed(False, "clone 失败", "X")
    titles = [c[1] for c in _MsgBox.calls]
    check("弹「安装失败」", "安装失败" in titles, f"titles={titles}")

    # ---------------------------------------------------------- 5. 市场行重渲染
    print("[5] _refresh_market_rows 有缓存重渲染 / 无缓存 no-op")
    fake3 = _FakeSelf(tmp)
    _bind(fake3, "_refresh_market_rows")
    fake3._last_market_rows = [{"a": 1}]
    fake3._refresh_market_rows()
    check("有缓存 → 重渲染当前结果",
          fake3.rendered == [[{"a": 1}]], f"{fake3.rendered}")
    fake3.rendered.clear()
    fake3._last_market_rows = None
    fake3._refresh_market_rows()
    check("无缓存 → no-op", fake3.rendered == [], f"{fake3.rendered}")

    # ---------------------------------------------------------- 6. Git URL 安装链路
    print("[6] _install（Git URL）完成 → 同一弹窗链路")

    class FakeInstallWorker(QThread):
        sig_log = Signal(str)
        sig_done = Signal(bool, str)

        def __init__(self, url, dest):
            super().__init__()
            self.url, self.dest = url, dest

        def start(self):        # 同步假完成：不真起线程、不真 clone
            self.sig_done.emit(True, "cloned")

    class _Input:
        @staticmethod
        def getText(*a, **k):
            return "https://github.com/demo/SomeRepo", True

    _MsgBox.calls = []
    fake4 = _FakeSelf(tmp)
    fake4._refresh_market_rows = lambda: setattr(
        fake4, "market_refresh_calls", fake4.market_refresh_calls + 1)
    _bind(fake4, "_on_installed", "_nodes_dir")
    worker_orig, input_orig = pp.InstallWorker, pp.QInputDialog
    pp.InstallWorker, pp.QInputDialog = FakeInstallWorker, _Input
    try:
        pp.PluginsPage._install(fake4)
        titles = [c[1] for c in _MsgBox.calls]
        check("Git URL 安装完成弹「安装完成」", "安装完成" in titles,
              f"titles={titles}")
        check("安装后 refresh_list", fake4.refreshed == 1, f"{fake4.refreshed}")
        check("安装后市场刷新", fake4.market_refresh_calls == 1,
              f"{fake4.market_refresh_calls}")
    finally:
        pp.InstallWorker, pp.QInputDialog = worker_orig, input_orig

    # ---------------------------------------------------------- 7. 卸载链路双刷新
    print("[7] _op_uninstall → 隐藏窗口回收站 + 双列表刷新")
    _MsgBox.calls = []
    winsub_calls = []
    winsub.run = lambda cmd, **kw: (winsub_calls.append(list(cmd)),
                                    types.SimpleNamespace(returncode=0,
                                                          stdout="", stderr=""))[1]
    fake5 = _FakeSelf(tmp)
    fake5._refresh_market_rows = lambda: setattr(
        fake5, "market_refresh_calls", fake5.market_refresh_calls + 1)
    # _trash_dir 是 staticmethod：直接挂裸函数（MethodType 绑定会把 self 当 path 传）
    fake5._trash_dir = pp.PluginsPage._trash_dir
    _bind(fake5, "_op_uninstall")
    try:
        fake5._op_uninstall(0)
        check("卸载走 winsub（powershell 回收站）",
              len(winsub_calls) == 1 and "powershell" in winsub_calls[0][0],
              f"{winsub_calls}")
        check("卸载后 refresh_list", fake5.refreshed == 1, f"{fake5.refreshed}")
        check("卸载后市场刷新（按钮变回「安装」）",
              fake5.market_refresh_calls == 1, f"{fake5.market_refresh_calls}")
        check("状态栏提示已移入回收站",
              any("回收站" in t for t in fake5.tips), f"{fake5.tips}")
    finally:
        winsub.run = winsub_orig

    print(f"\n{_passed} passed, {len(_failed)} failed")
    if _failed:
        print("失败项:", _failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
