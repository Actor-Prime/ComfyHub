# -*- coding: utf-8 -*-
"""AI 服务商预设回归（2026-11-12 用户需求：能选就别手填，选不到再自定义）。

断言：
  A. 预设数据完整性：id 唯一、名称非空、非自定义项必须有 base 与候选模型，
     且 base 必须带版本段（/v1 /v3 /v4）——否则会被 _v1_base 再补一层
  B. 域名反查：各家 base_url → 正确 id；未知域名 → custom；
     空/None → custom（不抛异常）
  C. _v1_base：已带版本段原样返回（智谱 /v4、火山 /v3 不能被补成 /v4/v1）
  D. UI：选预设 → 地址自动填且只读、模型换成该家候选；
     切自定义 → 地址可编辑且不覆盖已有内容；
     旧配置（无 ai_provider 键）按域名落位；未知地址 → 自定义
  E. 保存：ai_provider 随保存写入 setting

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_ai_provider_ui.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}  {detail}")


def main():
    from app import ai_providers as prov
    from app import ai_assist

    # ---------- A. 预设数据 ----------
    print("== A. 预设数据 ==")
    ids = [p["id"] for p in prov.all_providers()]
    check("id 唯一", len(ids) == len(set(ids)), str(ids))
    check("末尾是自定义兜底", ids[-1] == prov.CUSTOM_ID)
    bad = [p["id"] for p in prov.all_providers()
           if p["id"] != prov.CUSTOM_ID and not p["base"]]
    check("非自定义项都有 base", not bad, str(bad))
    # 候选模型允许为空（靠「测试接口」拉清单），不能有「一半空」的脏数据
    check("有候选模型的项至少给 1 个", all(p["models"] for p in prov.all_providers()
                                      if p["models"]))
    # 最要命的坑：预设 base 被 _v1_base 二次加工（智谱 /v4、Gemini /openai 会 404）
    mutated = [(p["id"], ai_assist._v1_base(p["base"]))
               for p in prov.all_providers()
               if p["id"] != prov.CUSTOM_ID
               and ai_assist._v1_base(p["base"]) != p["base"]]
    check("所有预设 base 经 _v1_base 后保持不变", not mutated, str(mutated[:3]))
    check("每项都有 hint（动态文案已在 i18n 词表）",
          all(p.get("hint") for p in prov.all_providers()))
    # 分组：国内 / 国际 / Token·编程套餐 / 自定义 四段
    # （套餐是订阅制、多数有专属端点，套餐 Key 打按量端点会 401，必须单列）
    grouped = prov.grouped()
    check("分组为 国内 + 国际 + 套餐 + 自定义",
          [g for g, _ in grouped] == ["国内", "国际", "Token/编程套餐", ""],
          str([g for g, _ in grouped]))
    dom = dict((g, len(v)) for g, v in grouped)
    check("国内 ≥ 12 家、国际 ≥ 15 家（大全要求）",
          dom["国内"] >= 12 and dom["国际"] >= 15, str(dom))
    # 国内/国际成对：五家厂商的海外站必须单独存在且 Key 不通用
    pairs = ["kimi_intl", "zhipu_intl", "qwen_intl", "minimax_intl", "siliconflow_intl"]
    check("国内厂商海外站都单列", all(i in ids for i in pairs),
          str([i for i in pairs if i not in ids]))
    # 已下线的不收：零一万物 410、商汤 404
    check("未收录已下线的零一万物/商汤",
          not any("lingyiwanwu" in (p["base"] or "") or "sensenova" in (p["base"] or "")
                  for p in prov.all_providers()))

    # ---------- B. 域名反查 ----------
    print("== B. 域名反查 ==")
    cases = [
        ("https://api.deepseek.com/v1", "deepseek"),
        ("https://api.xiaomimimo.com/v1", "mimo"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen"),
        ("https://open.bigmodel.cn/api/paas/v4", "zhipu"),
        ("https://api.moonshot.cn/v1", "kimi"),
        ("https://api.minimaxi.com/v1", "minimax"),
        ("https://api.siliconflow.cn/v1", "siliconflow"),
        ("https://ark.cn-beijing.volces.com/api/v3", "ark"),
        ("https://api.openai.com/v1", "openai"),
        # 国内厂商海外站：不能被国内域名规则抢走
        ("https://api.moonshot.ai/v1", "kimi_intl"),
        ("https://api.z.ai/api/paas/v4", "zhipu_intl"),
        ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "qwen_intl"),
        ("https://api.minimax.io/v1", "minimax_intl"),
        ("https://api.siliconflow.com/v1", "siliconflow_intl"),
        # 新增国内厂商
        ("https://api.hunyuan.cloud.tencent.com/v1", "hunyuan"),
        ("https://qianfan.baidubce.com/v2", "qianfan"),
        ("https://spark-api-open.xf-yun.com/v1", "spark"),
        ("https://api.stepfun.com/v1", "stepfun"),
        ("https://api.baichuan-ai.com/v1", "baichuan"),
        ("https://api-inference.modelscope.cn/v1", "modelscope"),
        # 新增国际
        ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini"),
        ("https://api.anthropic.com/v1", "anthropic"),
        ("https://api.x.ai/v1", "xai"),
        ("https://api.mistral.ai/v1", "mistral"),
        ("https://api.groq.com/openai/v1", "groq"),
        ("https://api.together.xyz/v1", "together"),
        ("https://openrouter.ai/api/v1", "openrouter"),
        ("https://api.deepinfra.com/v1/openai", "deepinfra"),
        ("https://api.cerebras.ai/v1", "cerebras"),
        ("https://integrate.api.nvidia.com/v1", "nvidia"),
        ("https://router.huggingface.co/v1", "hf"),
        ("https://api.novita.ai/openai", "novita"),
        ("https://api.fireworks.ai/inference/v1", "fireworks"),
        # 反例
        ("https://my-relay.example.com/v1", "custom"),
        ("https://x.openai.azure.com/openai/v1", "custom"),   # Azure 需资源名，只能自定义
        ("", "custom"),
        (None, "custom"),
        # ---- 顺序敏感组（2026-11-14 实测踩到才补的回归）----
        # 小米 MiMo 的套餐域名也含 "xiaomimimo"，必须排在按量键之前
        ("https://token-plan-cn.xiaomimimo.com/v1", "mimo_plan_cn"),
        ("https://token-plan-sgp.xiaomimimo.com/v1", "mimo_plan_sgp"),
        ("https://token-plan-ams.xiaomimimo.com/v1", "mimo_plan_ams"),
        ("https://api.xiaomimimo.com/v1", "mimo"),
        # 阿里 Token Plan 的键不能用笼统的 "token-plan"，
        # 否则会把上面三个 MiMo 域名全吃掉（实测就是这里错的）
        ("https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
         "qwen_token_plan"),
        ("https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
         "qwen_token_plan"),
        # 智谱：z.ai 的 coding 路径必须早于国内的 api/coding/paas
        ("https://open.bigmodel.cn/api/coding/paas/v4", "zhipu_plan"),
        ("https://api.z.ai/api/coding/paas/v4", "zhipu_plan_intl"),
        ("https://open.bigmodel.cn/api/paas/v4", "zhipu"),
    ]
    for url, want in cases:
        got = prov.match(url)
        check(f"match({str(url)[:46]}) → {want}", got == want, got)

    # ---------- C. _v1_base ----------
    print("== C. base 归一化 ==")
    v = [
        ("https://api.deepseek.com", "https://api.deepseek.com/v1"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1"),
        ("https://open.bigmodel.cn/api/paas/v4", "https://open.bigmodel.cn/api/paas/v4"),
        ("https://ark.cn-beijing.volces.com/api/v3", "https://ark.cn-beijing.volces.com/api/v3"),
        ("https://x.com/v1/", "https://x.com/v1"),
        ("https://x.com/v1/chat/completions", "https://x.com/v1/chat/completions"),
        # 以 /openai 结尾的兼容层（Gemini / DeepInfra / Novita）不能被补 /v1
        ("https://generativelanguage.googleapis.com/v1beta/openai",
         "https://generativelanguage.googleapis.com/v1beta/openai"),
        ("https://api.deepinfra.com/v1/openai", "https://api.deepinfra.com/v1/openai"),
        ("https://api.novita.ai/openai", "https://api.novita.ai/openai"),
    ]
    for raw, want in v:
        got = ai_assist._v1_base(raw)
        check(f"_v1_base({raw[:44]}) 保持/补齐正确", got == want, got)
    try:
        ai_assist._v1_base("")
        check("空地址抛 ValueError", False)
    except ValueError:
        check("空地址抛 ValueError", True)

    # ---------- D. UI ----------
    print("== D. UI 联动 ==")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    def make_page(settings):
        from app.ui.pages.settings_page import SettingsPage
        cfg = {"settings": dict(settings)}
        return SettingsPage(cfg, None)

    # D0 下拉结构：三段分组 + 标题项不可选 + 每项都能定位到 data
    pg0 = make_page({})
    combo = pg0.cb_ai_provider
    # 分隔线（insertSeparator）同样是 data=None 且文本为空，要和标题分开数
    titles = [combo.itemText(i) for i in range(combo.count())
              if combo.itemData(i) is None and combo.itemText(i)]
    seps = [i for i in range(combo.count())
            if combo.itemData(i) is None and not combo.itemText(i)]
    check("三个分组标题（国内/国际/套餐）", len(titles) == 3, str(titles))
    check("标题带分隔样式", all(t.startswith("——") for t in titles), str(titles))
    check("三条分隔线（国际/套餐/自定义前）", len(seps) == 3, str(len(seps)))
    check("标题项不可选（否则能选中没有 base 的假项）",
          all(not combo.model().item(i).isEnabled()
              for i in range(combo.count())
              if combo.itemData(i) is None and combo.itemText(i)))
    check("所有可选项都有 id",
          all(combo.itemData(i) for i in range(combo.count())
              if combo.itemData(i) is not None))

    # D1 旧配置无 ai_provider，地址是 deepseek → 自动落到 DeepSeek 预设
    pg = make_page({"ai_base_url": "https://api.deepseek.com/v1",
                    "ai_model": "deepseek-v4-pro", "ai_api_key": "sk-x"})
    check("旧配置按域名落到 DeepSeek 预设",
          pg.cb_ai_provider.currentData() == "deepseek", pg.cb_ai_provider.currentData())
    check("预设下地址只读", pg.ed_ai_base.isReadOnly())
    check("预设下地址=预设值",
          pg.ed_ai_base.text().rstrip("/") == "https://api.deepseek.com/v1",
          pg.ed_ai_base.text())
    check("存档模型名保留（不被预设默认覆盖）",
          pg.cb_ai_model.currentText() == "deepseek-v4-pro", pg.cb_ai_model.currentText())
    check("提示语非空", pg.lb_ai_prov_hint.text().strip() != "")

    # D2 切到智谱：地址换、模型换、地址仍只读
    i = pg.cb_ai_provider.findData("zhipu")
    pg.cb_ai_provider.setCurrentIndex(i)
    check("切智谱 → 地址自动换",
          pg.ed_ai_base.text().rstrip("/") == "https://open.bigmodel.cn/api/paas/v4",
          pg.ed_ai_base.text())
    check("切智谱 → 模型换成该家默认",
          pg.cb_ai_model.currentText() == "glm-4.7", pg.cb_ai_model.currentText())
    check("切智谱 → 模型候选来自该家",
          pg.cb_ai_model.count() == 3, str(pg.cb_ai_model.count()))
    check("切智谱 → 地址仍只读", pg.ed_ai_base.isReadOnly())

    # D3 切自定义：地址可编辑、内容保留
    i = pg.cb_ai_provider.findData("custom")
    pg.cb_ai_provider.setCurrentIndex(i)
    check("自定义 → 地址可编辑", not pg.ed_ai_base.isReadOnly())
    check("自定义 → 地址内容保留（不清空用户手上的）",
          "bigmodel" in pg.ed_ai_base.text(), pg.ed_ai_base.text())
    check("自定义 → 候选清空但仍可手输",
          pg.cb_ai_model.count() == 0 and pg.cb_ai_model.isEditable())

    # D4 未知地址 → 自定义
    pg2 = make_page({"ai_base_url": "https://my-relay.example.com/v1",
                     "ai_model": "my-model"})
    check("未知地址 → 自定义", pg2.cb_ai_provider.currentData() == "custom",
          pg2.cb_ai_provider.currentData())
    check("未知地址 → 地址可编辑且原样保留",
          not pg2.ed_ai_base.isReadOnly()
          and pg2.ed_ai_base.text() == "https://my-relay.example.com/v1",
          pg2.ed_ai_base.text())

    # D5 显式存过 ai_provider → 尊重存档（即使地址被手改过）
    pg3 = make_page({"ai_provider": "mimo",
                     "ai_base_url": "https://api.xiaomimimo.com/v1",
                     "ai_model": "mimo-v2.6-flash"})
    check("存档 ai_provider 被尊重", pg3.cb_ai_provider.currentData() == "mimo")
    check("存档模型名保留", pg3.cb_ai_model.currentText() == "mimo-v2.6-flash")

    # D6 空配置 → 自定义，全空
    pg4 = make_page({})
    check("空配置 → 自定义", pg4.cb_ai_provider.currentData() == "custom")
    check("空配置 → 地址为空可编辑",
          pg4.ed_ai_base.text() == "" and not pg4.ed_ai_base.isReadOnly())

    # D7 国内厂商海外站：切过去要换成海外端点（Key 不通用，不能串）
    pg5 = make_page({"ai_base_url": "https://api.moonshot.cn/v1",
                     "ai_model": "kimi-k2.6"})
    pg5.cb_ai_provider.setCurrentIndex(pg5.cb_ai_provider.findData("kimi_intl"))
    check("切 Kimi 国际版 → 地址变 .ai（不是 .cn）",
          pg5.ed_ai_base.text().rstrip("/") == "https://api.moonshot.ai/v1",
          pg5.ed_ai_base.text())
    check("切 Kimi 国际版 → 模型仍为 kimi-k2.6",
          pg5.cb_ai_model.currentText() == "kimi-k2.6", pg5.cb_ai_model.currentText())

    # D8 候选为空的厂商（如 OpenRouter）：模型框空着但可手输，提示到位
    pg6 = make_page({})
    pg6.cb_ai_provider.setCurrentIndex(pg6.cb_ai_provider.findData("openrouter"))
    check("OpenRouter → 候选空", pg6.cb_ai_model.count() == 0,
          str(pg6.cb_ai_model.count()))
    check("OpenRouter → 模型框仍可手输", pg6.cb_ai_model.isEditable())
    check("OpenRouter → 提示里教了模型名格式",
          "openai/gpt-5.4" in pg6.lb_ai_prov_hint.text(), pg6.lb_ai_prov_hint.text())

    # ---------- E. 保存 ----------
    print("== E. 保存写入 ai_provider ==")
    from app.ui import changes_dialog
    changes_dialog.confirm_changes = lambda *a, **k: True
    from app.config import save_config
    import app.ui.pages.settings_page as sp
    sp.save_config = lambda cfg: None       # 不落盘
    pg = make_page({"ai_base_url": "https://api.deepseek.com/v1"})
    pg.cb_ai_provider.setCurrentIndex(pg.cb_ai_provider.findData("kimi"))
    pg._save()
    check("保存后 settings.ai_provider = kimi",
          pg.cfg["settings"].get("ai_provider") == "kimi",
          str(pg.cfg["settings"].get("ai_provider")))
    check("保存后 ai_base_url = kimi 的地址",
          "moonshot" in str(pg.cfg["settings"].get("ai_base_url")),
          str(pg.cfg["settings"].get("ai_base_url")))
    check("保存后 ai_model 落在候选内",
          pg.cfg["settings"].get("ai_model") in ("kimi-k2.6", "kimi-k2-turbo-preview"),
          str(pg.cfg["settings"].get("ai_model")))

    # E2 老配置（缺 ai_provider 键）原样再保存 → 不该被判成「有改动」弹确认框
    #    （否则升级后第一次点保存就多一个骚扰弹窗）
    st_full = dict(pg.cfg["settings"])
    st_full.pop("ai_provider", None)
    pg5 = make_page(st_full)
    calls = {"n": 0}

    def _count(*a, **k):
        calls["n"] += 1
        return True

    changes_dialog.confirm_changes = _count
    pg5._save()
    check("缺 ai_provider 的老配置保存不弹确认框", calls["n"] == 0, str(calls["n"]))
    check("确实没写入（提示没有改动）", "没有改动" in pg5.lb_saved.text(),
          pg5.lb_saved.text())

    print(f"\n===== AI 服务商预设 =====\n通过 {PASS} / 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
