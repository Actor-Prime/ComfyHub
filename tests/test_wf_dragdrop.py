# -*- coding: utf-8 -*-
"""工作流页拖放回归（2026-10-31 拖 .json 进窗口即扫描）

验证点：
  1. _drop_path：只认本地 .json 文件（拒目录/拒其他扩展名/拒无 URL）
  2. dropEvent：落位到 ed_wf + 切回智能安装 Tab + 自动触发扫描
  3. 非法拖放：ignore，不触发扫描
  4. ed_wf 存在性守卫（历史上该控件只被引用从未创建，三个入口全崩）
用法：cd F:/ComfyHub && .venv_build/Scripts/python.exe tests/test_wf_dragdrop.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QMimeData, QUrl                      # noqa: E402
from PySide6.QtWidgets import QApplication                      # noqa: E402

app = QApplication(sys.argv[:1])
from app.ui.main_window import MainWindow                       # noqa: E402

res = []


def check(n, ok, d=""):
    res.append((n, ok, d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


class FakeEvent:
    """dropEvent 只用 mimeData()/acceptProposedAction()/ignore() 三个口，
    鸭子类型模拟，避开 QDropEvent 构造器的版本差异"""
    def __init__(self, md):
        self._md = md
        self.accepted = None

    def mimeData(self):
        return self._md

    def acceptProposedAction(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


win = MainWindow()
page = win.workflow_page
calls = []
page._scan = lambda: calls.append(page.ed_wf.text())   # 隔离：不真起扫描线程

def _urls(paths):
    m = QMimeData()
    m.setUrls([QUrl.fromLocalFile(p) for p in paths])
    return m


# ---- 1. 载荷识别 ----
tmp = tempfile.mkdtemp()
wf = os.path.join(tmp, "我的工作流.json")
open(wf, "w", encoding="utf-8").write("{}")
txt = os.path.join(tmp, "a.txt")
open(txt, "w", encoding="utf-8").write("x")

md = QMimeData()
md.setUrls([QUrl.fromLocalFile(wf)])
check("_drop_path 认 .json", page._drop_path(md) == wf, str(page._drop_path(md)))

md2 = QMimeData()
md2.setUrls([QUrl.fromLocalFile(txt)])
check("_drop_path 拒 .txt", page._drop_path(md2) is None)
check("_drop_path 拒目录", page._drop_path(_urls([tmp])) is None)
check("_drop_path 拒无URL", page._drop_path(QMimeData()) is None)

# ---- 2. 合法拖放：导入工作流库 + 落位 + 切 Tab + 自动扫描 ----
check("ed_wf 存在（历史崩溃修复）", hasattr(page, "ed_wf"))
wfdir = os.path.join(tmp, "wfdir")
os.makedirs(wfdir, exist_ok=True)
page._workflows_dir = lambda: wfdir    # 隔离：不写真实实例目录
page.tabs.setCurrentIndex(1)           # 先故意停在「启动模式」Tab
ev = FakeEvent(_urls([wf]))
page.dropEvent(ev)
imp = os.path.join(wfdir, os.path.basename(wf))
check("drop 被接受", ev.accepted is True)
check("已拷入实例工作流库", os.path.isfile(imp), imp)
check("路径落位=库内副本", page.ed_wf.text() == imp, page.ed_wf.text())
check("自动切回智能安装 Tab", page.tabs.currentIndex() == 0)
check("自动触发扫描", calls == [imp], str(calls))

# ---- 2b. 拖入语义：同名同内容去重 / 同名新内容覆盖更新 ----
calls.clear()
page.dropEvent(FakeEvent(_urls([wf])))
check("重复拖入不产生副本", len(os.listdir(wfdir)) == 1,
      str(os.listdir(wfdir)))
check("重复拖入仍可扫描", calls == [imp], str(calls))
with open(wf, "w", encoding="utf-8") as f:
    f.write('{"updated": true}')
page.dropEvent(FakeEvent(_urls([wf])))
check("同名新内容=覆盖更新", "updated" in open(imp, encoding="utf-8").read())
check("更新后库内仍一个文件", len(os.listdir(wfdir)) == 1)

# ---- 3. 非法拖放：忽略且不扫 ----
calls.clear()
ev2 = FakeEvent(_urls([txt]))
page.dropEvent(ev2)
check("非法拖放被忽略", ev2.accepted is False)
check("非法拖放不触发扫描", calls == [])

# ---- 4. 悬停高亮样式能来回切换 ----
page._set_drop_hint(True)
check("拖入高亮样式生效", "dashed" in page.ed_wf.styleSheet())
page._set_drop_hint(False)
check("离开恢复默认样式", page.ed_wf.styleSheet() == "")

print("=" * 44)
bad = [n for n, ok, _ in res if not ok]
print(f"结果: {len(res) - len(bad)}/{len(res)} 通过")
for n in bad:
    print("  ❌", n)
sys.exit(1 if bad else 0)
