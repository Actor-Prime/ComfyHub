# 建工作流（先查再改）
<!-- tools: list_templates, apply_template, web_search, check_workflow, inspect_workflow, run_workflow, list_models, search_models, download_model -->
用户要一个「新工作流」时，按下面的顺序做。**不要凭记忆写节点**——你写出来的
节点名和字段大概率是错的，图结构由模板保证，你只负责读懂需求、挑模板、填参数。

## 顺序
1. **先查模板**：`list_templates(query=用户需求)`。库里除了用户自己的工作流，
   还有 ComfyUI 官方模板（约 650 个）和内核 blueprints（约 116 个），
   名字直接可读（如 `video_minimax_h3_t2v`、`image_flux3_klein_t2i`）。
   返回里 `missing` 就是缺的模型，`source` 告诉你来自官方还是本地。
2. **官方模板缺模型时先说明**：把 `model_hint` 的内容转成人话告诉用户
   （缺什么文件、该放 models 下哪个目录）。不要假装能跑。
   用户要下的话：先用 `search_models` 找仓库，再 `download_model`；
   或者直接告诉用户去「模型库」页点组件包一键下载。
3. **不确定的事实先搜**：模型显存要求、节点属于哪个插件、参数范围、新版本
   改动，一律 `web_search`。搜索结果里没有的，不要编；实在查不到就说不确定。
4. **用模板生成副本**：`apply_template(name, edits)`。edits 的键用槽位名
   （prompt / negative / width / height / seed / steps / cfg / lora_name …）。
   只需要改用户提到的参数，其余保持模板原值。
5. **体检再跑**：`check_workflow` 看插件与模型是否齐 → `run_workflow` →
   `wait_job` 拿成图路径。报错就按 error-diagnosis 的思路查日志。

## 禁止
- 自己输出整份工作流 JSON（你会编出 `KSampler` 少 `model`、`cfg_scale` 这种错）。
- 把搜索结果里的工作流 JSON 直接当模板导入（来源不可信、格式多半不完整）。
- 在缺少模型的情况下直接 `run_workflow`，那只会得到一条"模型不存在"的报错。

## 说明给用户的话
- 选了官方模板时提一句「这个模板来自 ComfyUI 官方模板库」。
- 用了搜索时，给出来源链接，不要只说「网上说」。
