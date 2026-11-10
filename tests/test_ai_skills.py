# -*- coding: utf-8 -*-
"""AI 会话级技能回归测试（2026-11-11 skill 功能）。

三层结构：工具（ai_tools）恒开 · agent（ai_persona）恒开 · skill 按会话勾选。
断言：
  · skill markdown 解析：首「# 标题」= 名、首段 = 描述、全文 = 正文
  · list_skills 扫出厂 assets/skills，用户目录（COMFYHUB_SKILL_DIR）同名覆盖出厂
  · render：空列表返回空串；勾选的渲染成「【技能：名】+正文」；无效 id 跳过
  · chat store：skills 字段存取；旧 json 无字段时 load 出空列表（兼容）
  · _system_prompt 尾部追加技能段（skills_text 为空不追加）
  · 出厂技能正文短小（每篇 ≤ 800 字，防 system 提示膨胀烧 token）

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_ai_skills.py
"""
import json
import os
import sys
import tempfile

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


def main():
    from app import ai_skills, ai_chat_store, ai_agent

    # ---------------------------------------------------------- 1. 出厂技能
    print("[1] 出厂技能扫描")
    skills = ai_skills.list_skills()
    ids = {s["id"] for s in skills}
    check("出厂 3 个技能都在", {"prompt-tuning", "error-diagnosis",
                                "workflow-tuning"} <= ids, f"{ids}")
    by_id = {s["id"]: s for s in skills}
    pt = by_id.get("prompt-tuning", {})
    check("标题解析出技能名", pt.get("name") == "提示词优化", f"{pt.get('name')}")
    check("首段解析出描述", bool(pt.get("desc")) and len(pt["desc"]) < 130,
          f"{pt.get('desc')}")
    check("正文即全文", "负面词" in (pt.get("body") or ""), "")

    # ---------------------------------------------------------- 2. token 纪律
    print("[2] 出厂技能正文短小（≤800 字）")
    for s in skills:
        check(f"{s['id']} 正文精简", len(s["body"]) <= 800, f"{len(s['body'])} 字")

    # ---------------------------------------------------------- 3. 用户覆盖
    print("[3] 用户目录同名覆盖出厂")
    tmp = tempfile.mkdtemp(prefix="chub_skills_")
    os.environ["COMFYHUB_SKILL_DIR"] = tmp
    try:
        with open(os.path.join(tmp, "prompt-tuning.md"), "w", encoding="utf-8") as f:
            f.write("# 我的提示词规范\n用户自定义版本。\n\n自定义正文。")
        with open(os.path.join(tmp, "my-skill.md"), "w", encoding="utf-8") as f:
            f.write("# 自加技能\n用户新增。\n\n正文。")
        skills2 = ai_skills.list_skills()
        m = {s["id"]: s for s in skills2}
        check("同名 id 被用户版覆盖", "自定义正文" in m["prompt-tuning"]["body"],
              f"{m['prompt-tuning']['body'][:40]}")
        check("新技能被扫入", "my-skill" in m, f"{set(m)}")
        check("未覆盖的出厂技能仍在", "error-diagnosis" in m, "")

        # ------------------------------------------------------ 4. render
        print("[4] render 注入渲染")
        check("空列表 → 空串", ai_skills.render([]) == "")
        check("None → 空串", ai_skills.render(None) == "")
        out = ai_skills.render(["prompt-tuning", "不存在的id"])
        check("含技能头与正文", "【技能：我的提示词规范】" in out
              and "自定义正文" in out, f"{out[:60]}")
        check("无效 id 跳过不炸", "不存在" not in out and out.count("【技能") == 1, "")
    finally:
        os.environ.pop("COMFYHUB_SKILL_DIR", None)

    # ---------------------------------------------------------- 5. 会话存 skills
    print("[5] chat store skills 字段")
    os.environ["COMFYHUB_CHAT_DIR"] = tempfile.mkdtemp(prefix="chub_chats_")
    try:
        cid = ai_chat_store.new_id()
        ai_chat_store.save(cid, [{"role": "user", "content": "hi"}],
                           skills=["error-diagnosis"])
        d = ai_chat_store.load(cid)
        check("skills 存取", d.get("skills") == ["error-diagnosis"], f"{d.get('skills')}")
        ai_chat_store.save(cid, [{"role": "user", "content": "hi"}],
                           skills=["a", "b"])
        check("skills 覆盖更新", ai_chat_store.load(cid)["skills"] == ["a", "b"], "")
        ai_chat_store.save(cid, [{"role": "user", "content": "hi2"}])
        check("skills=None 沿用旧值", ai_chat_store.load(cid)["skills"] == ["a", "b"], "")
        # 旧格式 json（无 skills 字段）
        old_id = "old-style-chat"
        with open(os.path.join(os.environ["COMFYHUB_CHAT_DIR"],
                               old_id + ".json"), "w", encoding="utf-8") as f:
            json.dump({"id": old_id, "messages": []}, f)
        check("旧 json 兼容 → skills 空列表",
              ai_chat_store.load(old_id).get("skills") == [], "")
    finally:
        os.environ.pop("COMFYHUB_CHAT_DIR", None)

    # ---------------------------------------------------------- 6. system 注入
    print("[6] system 提示注入技能段")
    import types
    ctx = types.SimpleNamespace(inst={"name": "t", "port": 8188,
                                      "comfyui_path": "C:/x"}, cfg={})
    p0 = ai_agent._system_prompt(ctx)
    p1 = ai_agent._system_prompt(ctx, "【技能：测试技能】\n测试正文")
    check("无技能不追加", "测试正文" not in p0 and "【技能" not in p0, "")
    check("有技能追加到尾部", p1.endswith("【技能：测试技能】\n测试正文"), f"{p1[-40:]}")
    check("persona 仍在头部", "【助手设定】" in p1, "")

    print(f"\n{_passed} passed, {len(_failed)} failed")
    if _failed:
        print("失败项:", _failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
