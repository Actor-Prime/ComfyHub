# -*- coding: utf-8 -*-
"""AI 页右侧概要抽屉（简介 / 产物 / 技能）回归

需求（用户建议 2026-11-12）：右边来一个隐藏按钮，点开能看当前对话的简介，
还能看到这条对话产出的文件、以及挂了哪些 skill 的内容。

断言：
  A. 抽屉开合：把手常驻，点开/收起面板；状态记进 QSettings
  B. 简介页：用途/实例/工作流/模型/消息数/压缩摘要 随会话刷新
  C. 产物页：去重收集、列表条目带真实路径、空态提示
  D. 技能页：本会话技能列出，选中显示正文（不重复描述）
  E. 落盘：artifacts 随会话保存/读取，None 沿用旧值、[] 清空
  F. agent 侧产物提取：图片 / 工作流文件 / 下载模型

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_ai_right_panel.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="ch_rtpanel_")
os.environ["COMFYHUB_CHAT_DIR"] = os.path.join(_TMP, "chats")
os.environ["COMFYHUB_SKILL_DIR"] = os.path.join(_TMP, "skills")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}  {detail}")


app = QApplication.instance() or QApplication([])

from app import ai_agent, ai_chat_store  # noqa: E402
from app.ui.pages.ai_page import AiPage  # noqa: E402


class _MW:
    """最小主窗口替身：AiPage 只用到 cfg 与 current_inst"""
    def __init__(self):
        self.cfg = {"instances": []}

    def current_inst(self):
        return None


_SET = QSettings("LeiMengStudio", "ComfyHub")
_OLD_OPEN = _SET.value("ai_right_open", False, type=bool)
# 断言从「收起」开始：用户自己的真实设置可能是展开的，不重置会假红（2026-11-14）
_SET.setValue("ai_right_open", False)

try:
    page = AiPage({"instances": []}, _MW())
    page.resize(1200, 800)
    page.show()
    app.processEvents()

    # ==================================================== A. 开合
    print("[A] 抽屉开合")
    check("初始收起", not page.side_r.isVisible(), "侧栏应隐藏")
    check("把手常驻可见", page.right_handle.isVisible(), "把手应可见")
    check("收起时把手箭头朝左", page.right_handle.text() == "‹",
          page.right_handle.text())
    page._toggle_right()
    app.processEvents()
    check("点开后可见", page.side_r.isVisible(), "侧栏应显示")
    check("展开后箭头朝右", page.right_handle.text() == "›",
          page.right_handle.text())
    check("展开状态写进 QSettings",
          QSettings("LeiMengStudio", "ComfyHub").value(
              "ai_right_open", False, type=bool) is True, "应为 True")
    page._toggle_right()
    app.processEvents()
    check("再点回到收起", not page.side_r.isVisible(), "侧栏应隐藏")

    # ==================================================== B. 简介页
    print("[B] 简介页")
    page._profile = {"topic": "H3 视频专用", "workflow": "H3_i2v_15s.json",
                     "model": "h3-q8_0.gguf"}
    page._summary = "早期对话摘要：13 个分镜，1280x720。"
    page._history = [{"role": "user", "content": "跑第 3 个分镜"},
                     {"role": "assistant", "content": "已提交"}]
    page._update_session_btn()
    app.processEvents()
    check("用途上屏", page.r_topic.text() == "H3 视频专用", page.r_topic.text())
    check("工作流上屏", page.r_wf.text() == "H3_i2v_15s.json", page.r_wf.text())
    check("模型上屏", page.r_model.text() == "h3-q8_0.gguf", page.r_model.text())
    check("未绑实例显示跟随当前实例", page.r_inst.text() == "跟随当前实例",
          page.r_inst.text())
    check("消息数上屏", page.r_count.text().startswith("2"), page.r_count.text())
    check("压缩摘要上屏", "13 个分镜" in page.r_summary.toPlainText(),
          page.r_summary.toPlainText())
    page._profile = {}
    page._update_session_btn()
    app.processEvents()
    check("清空设定后回落占位文案", page.r_topic.text() == "未设定",
          page.r_topic.text())

    # ==================================================== C. 产物页
    print("[C] 产物页")
    # 注意：产物页此刻不是 QTabWidget 的当前页，isVisible() 恒 False，
    # 只能断言显隐意图（isHidden）——这是 Qt 标签页的语义，不是 bug。
    check("空态提示可见", not page.lb_art_empty.isHidden(), "应显示空态")
    check("空态时列表隐藏", page.lst_art.isHidden(), "列表应隐藏")
    page._add_artifact({"kind": "image", "path": "C:/out/a.png", "label": "a.png"})
    page._add_artifact({"kind": "image", "path": "C:/out/a.png", "label": "a.png"})
    page._add_artifact({"kind": "model", "path": "C:/m/b.safetensors"})
    app.processEvents()
    check("重复路径只记一次", len(page._artifacts) == 2, page._artifacts)
    check("列表条目数一致", page.lst_art.count() == 2, page.lst_art.count())
    check("空态自动隐藏", page.lb_art_empty.isHidden(), "应隐藏")
    check("条目带真实路径",
          page.lst_art.item(0).data(Qt.UserRole) == "C:/out/a.png",
          page.lst_art.item(0).data(Qt.UserRole))
    check("缺 label 时自动取文件名",
          page.lst_art.item(1).text().endswith("b.safetensors"),
          page.lst_art.item(1).text())
    check("有产物时打开按钮可用", page.b_art_open.isEnabled(), "应可点")
    page._add_artifact({"kind": "image", "path": ""})
    check("空路径被忽略", len(page._artifacts) == 2, page._artifacts)

    # ==================================================== D. 技能页
    print("[D] 技能页")
    from app import ai_skills
    all_sk = ai_skills.list_skills()
    check("环境里有可选技能", len(all_sk) >= 1, len(all_sk))
    if all_sk:
        sid = all_sk[0]["id"]
        page._chat_skills = [sid]
        page._update_session_btn()
        app.processEvents()
        check("技能列表列出本会话技能", page.lst_skill.count() == 1,
              page.lst_skill.count())
        body = page.txt_skill.toPlainText()
        check("正文已显示", body.strip() != "", body[:40])
        check("正文等于技能原文（不重复描述）", body.strip() == all_sk[0]["body"],
              body[:60])
        page._chat_skills = []
        page._update_session_btn()
        app.processEvents()
        check("清空技能后回到空态", not page.lb_skill_empty.isHidden(), "应显示空态")
        page._chat_skills = [sid]

    # ==================================================== E. 落盘
    print("[E] 产物随会话落盘")
    cid = page._chat_id
    page._save_chat()
    d = ai_chat_store.load(cid)
    check("save 写入 artifacts", len(d.get("artifacts") or []) == 2,
          d.get("artifacts"))
    ai_chat_store.save(cid, d["messages"], artifacts=None)      # None 沿用
    check("None 沿用旧产物", len(ai_chat_store.load(cid)["artifacts"]) == 2,
          ai_chat_store.load(cid)["artifacts"])
    ai_chat_store.save(cid, d["messages"], artifacts=[])        # [] 清空
    check("[] 清空产物", ai_chat_store.load(cid)["artifacts"] == [],
          ai_chat_store.load(cid)["artifacts"])
    ai_chat_store.save(cid, d["messages"], artifacts=[
        {"kind": "image", "path": "C:/x.png"},
        {"kind": "image", "path": "C:/x.png"}])                 # 重复去重
    check("落盘时重复路径去重", len(ai_chat_store.load(cid)["artifacts"]) == 1,
          ai_chat_store.load(cid)["artifacts"])

    # ==================================================== F. agent 侧提取
    print("[F] agent 产物提取")
    got = ai_agent._artifacts("get_job", {}, {"ok": True,
                                              "images": ["C:/out/1.png"]})
    check("图片算产物", len(got) == 1 and got[0]["kind"] == "image", got)
    got = ai_agent._artifacts("import_workflow", {},
                              {"ok": True, "path": "C:/wf/a.json"})
    check("工作流算产物", len(got) == 1 and got[0]["kind"] == "workflow", got)
    got = ai_agent._artifacts("download_model",
                              {"file_path": "loras/x.safetensors"},
                              {"ok": True, "dest": "C:/m/loras"})
    check("下载模型算产物（拼完整路径）",
          got and got[0]["path"].replace("\\", "/") == "C:/m/loras/x.safetensors", got)
    got = ai_agent._artifacts("get_health", {}, {"ok": True})
    check("纯查询不算产物", got == [], got)
    got = ai_agent._artifacts("run_workflow", {}, {"ok": False, "error": "x"})
    check("失败结果不算产物", got == [], got)

finally:
    _SET.setValue("ai_right_open", _OLD_OPEN)

print(f"\nPASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
