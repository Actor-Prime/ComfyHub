# 工作流调参
<!-- tools: list_workflows, inspect_workflow, check_workflow, patch_workflow, run_workflow -->
出图效果不对时按参数对照表调，一次只动一个变量。

调参对照：
- 出图模糊/细节糊 → 提高步数 20→28，或换采样器 euler → dpmpp_2m + karras；
- 过饱和/对比过强 → 降 CFG 7→5.5；
- 构图混乱/主体变形 → 降分辨率到 832×1216 以内，别超过模型训练分辨率；
- 人物脸部崩坏 → 降分辨率或加 hires fix（放大 1.5 倍、降噪 0.4）；
- 内容不听提示词 → 提高 CFG 至 8，或把重点词提前并加权 (word:1.2)；
- 出图全黑/纯色 → 查模型是否匹配工作流（SD1.5/SDXL 不混用），再查 VAE。
默认基线：832×1216、28 步、euler/simple、CFG 7。改动前先报当前值，改动后对比效果。
