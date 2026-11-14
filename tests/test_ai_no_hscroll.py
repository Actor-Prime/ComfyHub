# -*- coding: utf-8 -*-
"""消息区不允许出现横向滚动条（2026-11-14 用户报障）

用户反馈：AI 对话页消息区底部冒出横向滑块，得左右拖才能看完一句。
期望：正文按窗口宽度自动换行 / 卡片自己缩，不要横向滚动。

根因（两条，都得盯住，否则修一半还会剩 1~2px）：
1. 正文宽度按「视口-40」定型，而实际可用只有「视口-42」
   （flow 边距 12 + 卡片内边距 28 + 卡片描边 2）→ 2px 顶出视口；
2. 卡片标题行（「AI 助手 · 模型名 · 时间」）不可压缩，模型名一长，
   卡片最小宽度就被 header 锁死 → 视口再窄也压不下去。

断言方式（不写死像素）：
· 横向滚动条 range 必须为 0；
· flow_host 宽度不得超过视口；
· 每一行都落在 flow 的内容区里（右侧不越界）；
· 正文宽度必须等于 _card_body_width()（宽度预算只在一个地方算）。
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="ch_hs_")
os.environ["COMFYHUB_CHAT_DIR"] = os.path.join(_TMP, "chats")
os.environ["COMFYHUB_SKILL_DIR"] = os.path.join(_TMP, "skills")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.ui.pages.ai_page import AiPage  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  " + name)
    else:
        FAIL += 1
        print(" FAIL " + name + ("  ← " + str(detail) if detail else ""))


INST = {"id": "i1", "name": "实例甲", "port": 8188,
        "comfyui_path": "F:/A", "python_path": ""}


# 模型名故意取长一点：卡片标题行「AI 助手 · 模型名 · 时间」曾经不可压缩，
# 模型名一长就把整张卡的最小宽度锁死 —— 这条也必须在测试里能复现
LONG_MODEL = "mimo-v2.6-flash-preview-0915-experimental"


class _MW:
    def __init__(self):
        self.cfg = {"instances": [INST], "settings": {"ai_model": LONG_MODEL}}

    def current_inst(self):
        return INST


def pump(ms=350):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


# 故意准备最讨打的内容：超长不可断词（uuid / 无空格路径）、长工具参数、
# 长用户消息、提示行、出图 —— 每一类都曾经或能把行顶宽。
LONG_TOKEN = "bd4bd7ec-577b-442f-bd7f-e5d10e7c4e4e"
LONG_PATH = ("F:\\ComfyUI-aki-v3\\ComfyUI\\custom_nodes\\some_really_long_folder_"
             "name_without_any_space_at_all\\models\\checkpoints\\"
             "sdxl_base_1.0_official_release_fp16.safetensors")
AI_TEXT = (
    "结论：这次没有出图，工作流提交被 ComfyUI 拒绝（HTTP 400）。\n\n"
    "1. check_workflow 返回 ok: true，但提示有一个未知节点 " + LONG_TOKEN + "\n"
    "2. 疑似节点来自 XB_ToolBox，路径 " + LONG_PATH + "\n"
    "- 要点一：先确认插件是否加载\n"
    "- 要点二：再看日志里的 IMPORT FAILED\n\n"
    "要我按第 1 步继续定位吗？"
)


def build_content(page: AiPage):
    """按真实渲染路径铺满各类行：用户气泡 / AI 卡 / 工具时间线 / 提示 / 出图"""
    page._user_bubble("用 1009 工作流跑一张图，提示词你定（赛博朋克风格的女战士），"
                      "先检查工作流能不能跑再执行 " + LONG_TOKEN)
    page._ai_card(AI_TEXT)
    page._tool_item("inspect_workflow", {"path": LONG_PATH, "node_id": LONG_TOKEN})
    page._finish_tool_item("inspect_workflow", True,
                           "节点 33 个，" + LONG_TOKEN + " 不在已知节点表内", 412)
    page._notice("上下文较长，正在压缩较早的 12 条对话：" + LONG_PATH)
    page._err_bubble("这个服务商没有返回额度信息（有些服务商不提供用量接口）")

    pm = QPixmap(900, 600)
    pm.fill(QColor("#334455"))
    png = os.path.join(_TMP, "shot.png")
    pm.save(png)
    page._show_images([png])


def measure(page: AiPage, tag: str):
    sb = page.scroll.horizontalScrollBar()
    vw = page.scroll.viewport().width()
    fh = page.flow_host
    check("%s 横向滚动条 range 为 0" % tag, sb.maximum() == 0,
          "viewport=%d flow_host=%d max=%d" % (vw, fh.width(), sb.maximum()))
    # 横向滚动条被策略强制关掉了，maximum() 恒为 0 说明不了问题；
    # 真正的判据是 widgetResizable 下的宿主宽度：内容只要比视口宽，
    # 宿主就会被顶宽（此时用户看不到滑块，但右边是真的被裁掉了）。
    check("%s flow_host 宽度正好等于视口" % tag, fh.width() == vw,
          "viewport=%d flow_host=%d" % (vw, fh.width()))

    # 每一行都要落在 flow 内容区里（右侧留出 FLOW_PAD_R）
    limit = fh.width() - AiPage.FLOW_PAD_R
    over = []
    lay = fh.layout()
    for i in range(lay.count()):
        it = lay.itemAt(i)
        w = it.widget() if it is not None else None
        if w is None:
            continue
        if w.geometry().right() > limit:
            over.append("%s right=%d > %d" % (w.objectName() or w.__class__.__name__,
                                              w.geometry().right(), limit))
    check("%s 没有行越界" % tag, not over, "; ".join(over[:3]))

    # 正文宽度只能由宽度预算决定
    want = page._card_body_width()
    bodies = [lb for lb in fh.findChildren(QLabel) if lb.property("chatKind") == "ai"]
    check("%s 卡片正文宽度 = 预算值" % tag,
          bodies and all(lb.width() == want for lb in bodies),
          "want=%d got=%s" % (want, [lb.width() for lb in bodies]))

    # 正文不越出卡片（右侧被裁掉几个字就是这里破的）
    bad = []
    for lb in bodies:
        card = lb.parentWidget()
        inner = card.width() - 2 * AiPage.CARD_PAD - 2 * AiPage.CARD_BORDER
        if lb.width() > inner:
            bad.append("body=%d inner=%d" % (lb.width(), inner))
    check("%s 正文不越出卡片内框" % tag, not bad, "; ".join(bad[:3]))


def main():
    page = AiPage({"_MW": 1}, _MW())
    page.resize(700, 700)
    page.show()
    pump(400)

    build_content(page)
    pump(500)

    print("== A. 窄窗口（700，对话区只剩 300 多）==")
    measure(page, "窄窗口")

    print("== B. 拉宽到 1440 ==")
    page.resize(1440, 900)
    pump(500)
    measure(page, "宽窗口")

    print("== C. 再压回 620（比 A 更窄）==")
    page.resize(620, 700)
    pump(500)
    measure(page, "超窄窗口")

    print("== D. 内容不足一屏 → 竖条消失 → 视口变宽，再撑满 ==")
    page2 = AiPage({"_MW": 1}, _MW())
    page2.resize(760, 900)
    page2.show()
    pump(300)
    for _ in range(6):
        page2._ai_card("短句。")
    pump(400)
    measure(page2, "竖条消失后")
    page2.resize(760, 380)          # 撑到超一屏，竖条出现
    pump(500)
    measure(page2, "竖条出现后")
    page2.close()

    page.close()
    print("\nPASS %d / FAIL %d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
