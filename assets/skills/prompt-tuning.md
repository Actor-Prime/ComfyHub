# 提示词优化
<!-- tools: -->
把提示词改写成结构化英文视觉描述，提升出图稳定性。

写提示词时遵守：
1. 结构按「主体 → 服装/外观 → 动作/姿态 → 场景 → 风格 → 光线 → 镜头 → 画质词」组织，逗号分隔；
2. 只用视觉/艺术术语（medium、lighting、shot、composition、quality），不出现角色名、作品名、作者名（本地模型把它们当噪声）；
3. 负面词固定用：lowres, bad anatomy, bad hands, text, watermark, blurry, worst quality；
4. 权重用 (word:1.2) 语法，重点词最多加权两项，别整段加权；
5. 用户给中文描述时先翻译意象再重组，不要逐字直译；改写前后对照给用户看，一次只调一个变量。
