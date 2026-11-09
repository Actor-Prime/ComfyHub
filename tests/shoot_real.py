# -*- coding: utf-8 -*-
"""真机逐页截图（对已打包的 ComfyHub.exe）：uiautomation 点导航 + PIL 抓窗口

用法：.venv_build/Scripts/python.exe tests/shoot_real.py [输出目录]
"""
import os
import sys
import time

import uiautomation as auto
from PIL import ImageGrab

OUT = sys.argv[1] if len(sys.argv) > 1 else r"F:\ComfyHub\analysis\ui_real"
os.makedirs(OUT, exist_ok=True)

PAGES = ["工作台", "ComfyUI", "插件管理", "环境管理", "模型库",
         "工作流", "AI 助手", "控制台", "系统设置"]


def main():
    win = auto.WindowControl(searchDepth=1, Name="ComfyHub — ComfyUI 多实例管理器")
    if not win.Exists(3):
        print("找不到 ComfyHub 窗口")
        return 1
    win.SetActive()
    win.SetFocus()
    time.sleep(0.5)
    r = win.BoundingRectangle
    print("窗口", r)

    # 导航是 ListControl 里的 ListItem
    items = {}
    for c, _ in auto.WalkControl(win, maxDepth=6):
        if c.ControlTypeName == "ListItemControl":
            try:
                items[c.Name.strip()] = c
            except Exception:
                pass
    print("导航项:", list(items.keys()))

    for i, name in enumerate(PAGES):
        it = items.get(name)
        if it is None:
            # 导航项名带 emoji 前缀 + 换行（"🏠\n工作台"），按后缀兜底匹配
            for k, v in items.items():
                if k.split("\n")[-1].strip() == name:
                    it = v
                    break
        if it is None:
            print(f"  跳过 {name}（没找到导航项）")
            continue
        # QListWidget 的 item 没有可用的 Invoke 语义（点了不切换），按真实矩形中心点击
        rr = it.BoundingRectangle
        auto.Click(rr.left + (rr.right - rr.left) // 2,
                   rr.top + (rr.bottom - rr.top) // 2)
        time.sleep(1.2)
        r = win.BoundingRectangle
        p = os.path.join(OUT, f"{i:02d}_{name.replace(' ', '')}.png")
        ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom)).save(p)
        print("  saved", os.path.basename(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
