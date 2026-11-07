# -*- coding: utf-8 -*-
"""离屏渲染一张 AI 助手页截图（发布说明/README 用）

不依赖真机桌面：offstage 构造 AiPage，填演示内容，grab() 存 PNG。
"""
import os
import sys
import tempfile
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# 注意：offscreen 平台在 Windows 上字体库为空（汉字全变豆腐块），
# 所以这里用 windows 平台离屏渲染到 QPixmap（widget.render，不是屏幕截图）。
os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ["COMFYHUB_CHAT_DIR"] = tempfile.mkdtemp(prefix="shot_chats_")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402

from app import ai_chat_store  # noqa: E402
from app.config import APP_VERSION  # noqa: E402
from app.ui.theme import apply_theme  # noqa: E402

app = QApplication.instance() or QApplication([])
# 离屏渲染不继承主程序字体，必须显式设中文字体，否则汉字全是豆腐块
app.setFont(QFont("Microsoft YaHei UI", 9))
cfg = {"settings": {"ai_model": "mimo-v2.6-flash", "theme": "dark"}}
try:
    apply_theme(app, cfg)
except Exception:
    pass

# 注意：会话要先建“空页面”，再往 store 里塞演示数据——否则 __init__ 的 _boot_chats
# 会把历史气泡渲染出来，之后 clear 掉会有残影（deleteLater 是异步的）
from app.ui.pages.ai_page import AiPage  # noqa: E402


class _FakeWin:
    def current_inst(self):
        return {"id": "inst_demo", "name": "ComfyUI", "port": 8188,
                "comfyui_path": "F:/ComfyUI-aki-v3/ComfyUI"}


page = AiPage(cfg, _FakeWin())
page.resize(1040, 700)
# 先把窗口布局跑起来，_chat_width() 才是真实宽度；否则气泡高度按旧宽度算、会叠在一起
page.show()
for _ in range(8):
    app.processEvents()

# 现在再造几条会话，只为让顶部下拉有内容（不打开，避免加载历史气泡）
c2 = ai_chat_store.new_id()
ai_chat_store.save(c2, [
    {"role": "user", "content": "插件装完 ComfyUI 起不来了"},
    {"role": "assistant", "content": "看日志是某个节点 import 失败，已定位到 requirements 缺包"},
])
ai_chat_store.rename(c2, "插件启动报错")
c1 = ai_chat_store.new_id()          # 后写 → 列表排最前，和截图内容对应
ai_chat_store.save(c1, [
    {"role": "user", "content": "按这个参考图复刻一个原创角色，用本地已有的模型"},
    {"role": "assistant", "content": "出图完成，路径已校验"},
])
ai_chat_store.rename(c1, "复刻角色出图")
page._reload_chat_list(select=c1)

# 清掉欢迎卡（连同子控件一起摘掉，否则 deleteLater 之前会留下文字残片）
for i in reversed(range(page.flow.count() - 1)):
    it = page.flow.takeAt(i)
    w = it.widget()
    if w is not None:
        w.hide()
        w.setParent(None)
        w.deleteLater()
for _ in range(6):
    app.processEvents()
page._user_bubble("按这个参考图复刻一个原创角色，用本地已有的模型")
page._tool_item("list_workflows", {})
page._finish_tool_item("list_workflows", True, "10 个工作流", 61)
page._ai_card("出图完成 ✅  工作流：Qwen-Image-2.1-三视图（本地副本）\n"
              "参数：832×1216 竖幅 · 28 步 · euler/simple\n"
              "图片路径已校验存在：output/Qwen-Image-2.1/三视图/2026-11-06/xxx_00002_.png")
page._user_bubble("把耳朵改成老虎那种，其余保持不变")
page._tool_item("patch_workflow", {"name": "Qwen-Image-2.1-三视图_ai.json"})
page._finish_tool_item("patch_workflow", True, "副本已生成 · 2 处改动", 402)
page._notice("已把早期对话压缩为 236 字摘要，后续问答会带着它继续")
page._ai_card("已按老虎耳朵改好，出图完成 ✅\n"
              "图片路径：output/Qwen-Image-2.1/三视图/2026-11-06/xxx_00003_.png")
page._user_bubble("顺便看下现在显存还剩多少")
page._tool_item("get_health", {})
page._finish_tool_item("get_health", True, "RTX 4070 Ti · 显存 10.8/11.99GB", 48)
page._ai_card("显存还剩约 1.2GB，队列空闲。要接着出图建议先清一次显存缓存。")

page.lb_inst.setText("ComfyUI · 8188")
page.scroll.verticalScrollBar().setValue(0)     # 从顶部开始截
for _ in range(12):
    app.processEvents()

out = ROOT / "docs" / f"ai_page_v{APP_VERSION}.png"
pm = page.grab()
pm.save(str(out))
page.hide()
print("saved", out, pm.width(), "x", pm.height())
