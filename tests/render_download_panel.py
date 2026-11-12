# -*- coding: utf-8 -*-
"""下载面板视觉验片：长文件名 / 长失败文案 / 任务溢出时的自适应

模拟「不同电脑分辨率」的两种差异：
  ① 系统字体更大（用户把「文本和其他项目」调到 125%/150%）
  ② 任务多到超出屏幕高度

用法：cd F:/ComfyHub && .venv_build/Scripts/python.exe tests/render_download_panel.py [字号pt]
输出：analysis/dl_panel.png + 越界诊断
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

pt = float(sys.argv[1]) if len(sys.argv) > 1 else 0
if pt:
    f = QFont(app.font())
    f.setPointSizeF(pt)
    app.setFont(f)

from app.ui.download_center import DownloadCenter, _TaskRow, panel_width  # noqa: E402

dl = DownloadCenter()
dl.resize(90, 30)
dl.show()
dl._pump = lambda: None

print("字体 pt=%.1f → 面板宽=%d" % (app.font().pointSizeF(), panel_width(app.font())))

rows = [
    ("animeinoutuneVA_16.safetensors", "down"),
    ("animagineXL_v31Opt.safetensors", "fail"),
    ("vae-ft-mse-840000-ema-pruned.safetensors", "ok"),
    ("H3-PK-Parasyste-Turbo-Q8_0-very-long-name-2026.safetensors", "fail2"),
    ("a.safetensors", "down"),
    ("b.safetensors", "down"),
    ("c.safetensors", "down"),
    ("d.safetensors", "down"),
    ("e.safetensors", "down"),
]
for i, (name, kind) in enumerate(rows):
    tid = "t%d" % (i + 1)
    row = _TaskRow(tid, name, source="ai" if i % 2 else "ui")
    row.setParent(dl._panel)
    dl._rows_v.insertWidget(dl._rows_v.count() - 1, row)
    dl._rows[tid] = row
    if kind == "down":
        row.set_state("下载中")
        row.set_progress(3_400_000_000, 6_800_000_000)
    elif kind == "ok":
        row.set_state("完成")
    else:
        row.set_fail(
            "连接超时（网络不通或镜像站无响应）",
            "URLError: <urlopen error [WinError 10060] 由于连接方在一段时间后没有正确"
            "答复或连接的主机没有反应，连接尝试失败。>")

dl._empty.setVisible(False)
dl._scroll.setVisible(True)
dl._refresh_head()
dl._menu.adjustSize()
dl._menu.popup(dl.mapToGlobal(dl.rect().bottomLeft()))
dl._menu.adjustSize()


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.02)


pump(1.2)
pm = dl._menu.grab()
out = os.path.join(ROOT, "analysis", "dl_panel.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
pm.save(out)
print("saved", out, pm.width(), "x", pm.height())

# ---- 越界诊断：行内每个文本控件的「最小需求宽度」是否超过可用宽度 ----
panel_w = dl._menu.width()
bad = 0
for tid, row in dl._rows.items():
    for nm in ("lb_name", "lb_state", "lb_num"):
        lb = getattr(row, nm, None)
        if lb is None:
            continue
        # minimumSizeHint 是控件压不动的下限；> 行宽说明会顶破面板
        need = lb.minimumSizeHint().width()
        if need > panel_w:
            bad += 1
            print(f"  OVERFLOW {tid}.{nm}: min_w={need} > panel={panel_w}")
print("越界控件数:", bad)
print("面板高度: %d（限高 %d）" % (pm.height(), dl._scroll.maximumHeight()))
