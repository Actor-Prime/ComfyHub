# ComfyHub 四项能力交付（2026-11-15 夜）

## 一、官方模板库接入

以前 AI 助手只认 `user/default/workflows` 里用户自己的十几个工作流，官方那套模板全在
pip 包里躺着没人用。现在全量入库：

| 来源 | 位置 | 数量 |
|---|---|---|
| 官方模板 | `comfyui_workflow_templates_json/templates` | 652（去重后） |
| 内核子图 | `ComfyUI/blueprints` | 116 |
| 用户工作流 | `user/default/workflows` | 11 |

- 全量扫描 **0.55 秒**，带 60 秒缓存，AI 每次检索不会卡
- 文件名从 slug 变成人话：`video_minimax_h3_t2v` → `Video MiniMax H3 T2V`，并自动打标签
  （t2v→文生视频、i2v→图生视频、fl2va→首尾帧…）
- **缺模型提示**：官方模板没有 `models` 字段（元数据在前端 bundle 里），改为从节点反查，
  穿透 `definitions.subgraphs`，按 class_type 归到 `diffusion_models` / `text_encoders` / `vae` /
  `loras` 等目录，再与实例已装模型（含 extra_model_paths 外挂库）比对，输出：
  ```
  缺 3/5 个模型：
  · minimax_h3_fl2va_pruned_int8_convrot.safetensors → models/diffusion_models
  · minimax_h3_video_vae_int8_convrot.safetensors → models/vae
  ```
- 检索排序修了两个坑：①「h3 文生视频」原本把 WAN 的文生视频模板排第一（用途标签的
  2-gram 碎片命中了 3 次，压过产品名）；② API 模板（需云端 Key）会抢头名。现在按
  「模板名 > 用途标签 > 节点类型 > 正文」加权 + 整词奖励，API 模板永远排最后。
- 提示词槽位补齐：官方 H3 模板的提示词在 `MiniMaxH3ImageToVideo.widgets_values[0]`，
  不在 CLIPTextEncode 里。加了白名单 + 兜底猜测（挑最像自然语言的长字符串，排除
  MarkdownNote/Primitive 等）。
- 子图双向同步：改动落在子图内部时，主图画布上的子图实例也同步刷（否则用户打开看到旧提示词）。

## 二、H3 组件包（选装）

新增 `app/component_packs.py` + 模型页顶部卡片。

**MiniMax H3 · W4A8 低显存文生视频**，5 个文件共 28.2 GB：

| 文件 | 目录 | 大小 | 来源 |
|---|---|---|---|
| minimax_h3_fl2va_pruned_w4a8_mixed | diffusion_models | 11.7 GB | Kijai/MiniMax-H3-experimental |
| qwen3vl_32b_minimax_h3_nvfp4_awq | text_encoders | 14.6 GB | Comfy-Org/MiniMax-H3 |
| minimax_h3_lightvae_lite_vae_int8_convrot | vae | 989 MB | Kijai |
| minimax_h3_audio_vae_fp32 | vae | 577 MB | Comfy-Org |
| minimax_h3_DMAD_4step_full_lora_rank_39 | loras | 350 MB | Kijai |

- 每个文件两个源：ModelScope 优先、hf-mirror 兜底
- 判断「已装」看全（含外挂模型库），实测本机 5/5 已识别，不会重复下 28 GB
- 只比大小不比 hash：12~15 GB 算 sha256 要几十秒，上游也没发布校验值
- 装完自动把随包工作流 `H3-W4A8-低显存文生视频.json` 放进实例 workflows 目录

## 三、AI 助手加 web_search

新增 `app/web_search.py` + 工具 + 技能提示词。

- 引擎顺序：**Tavily**（配了 Key 才用）→ **Bing**（cn.bing.com 免 Key，实测可用）→ DuckDuckGo
- 只回标题 + 链接 + 摘要，不抓正文（正文塞进 2B 模型上下文只会挤掉真问题）
- 新增内置技能 `workflow-build.md`：先查模板 → 缺模型先说 → 不确定就搜 → 只改参数 →
  体检再跑；明令禁止自己输出工作流 JSON
- 排错技能也声明了 web_search（查报错更省事）

## 四、下载器多线程分段

原来 `_download_threaded` 写好了但开关是 `use_threaded = False`（历史原因是 codeload
多线程反被限速）。现在按 host 白名单打开：

- 开：ModelScope / hf-mirror / huggingface / pytorch / 阿里云
- 不开：codeload.github.com、github archive、npmmirror
- 实测 ModelScope 8 线程：577 MB / 7.3 秒 ≈ **79 MB/s**（分段小样本 27 MB/s，单连接 3~4 MB/s）
- 分段失败不再直接放弃该源，改为同一源退回单连接重下（单源场景必须有这条路）

## 验证

| 测试 | 结果 |
|---|---|
| tests/test_official_templates.py（新） | 41/41 |
| tests/test_component_packs.py（新） | 29/29 |
| tests/test_web_search.py（新） | 24/24 |
| tests/test_download_threaded.py（新） | 20/20 |
| test_workflow_lib.py | 38/38 |
| test_local_llm.py | 47/47 |
| test_ai_assistant.py | 164/164（顺带修好 4 项因设置页新增控件而红的旧断言） |
| test_download_resume / fixes / theme_i18n | 全绿 |

构建产物：
- 便携版 `F:\ComfyHub\dist\ComfyHub\ComfyHub.exe`
- 安装包 `F:\ComfyHub\dist\ComfyHub_Setup_0.8.30.exe`（42.3 MB，已自签）

**未升版本号**（用户没说「上传 git」）。日常交付只出本地包。

## 待你测试

1. 模型页顶部卡片是否显示「MiniMax H3 · W4A8 · 已就绪 5/5」；点「打开目录」跳对地方
2. AI 助手问「帮我做一个 H3 视频工作流」→ 应先用 list_templates 检索到官方模板 + 提示缺哪些模型
3. AI 助手问个新事实（如「H3 现在多少显存能跑」）→ 应触发 web_search 并给链接

杀软提示：安装包是自签证书，SmartScreen 会拦一次；便携版 exe 可能被 Defender 误报，
选「更多信息 → 仍要运行」或加白名单。

---

# 追加修复（2026-11-16 01:5x）

## 五、AI 对话区自动滚到底

**现象**：按回车发送后，滚动条不动，新消息插在视口外面，得自己往下拖。

**根因**：不是没写滚动，是**滚得太早**。`_user_bubble()` 里 `addWidget` 之后立刻
`setValue(sb.maximum())`，而这一刻 QScrollArea 的几何还没重算，`maximum()` 还是旧值
（常常就是当前位置），所以视觉上纹丝不动。

**修法（三件套）**：

| # | 改动 | 为什么要这么写 |
|---|---|---|
| 1 | `_send()` 开头 `_pin_bottom(1500)` | 发送 = 用户明确要看最新，**无条件**贴底，不管他当前滚在哪 |
| 2 | 五个内容追加点改调 `_follow_bottom()` | 流式输出时用户常上滚看历史，硬拽回底部很烦；只在「距底 ≤80px」或贴底窗口期内才跟 |
| 3 | `_jump()` 标志位区分程序滚动 / 用户滚动 | 否则 `_on_scroll_range` 自己 setValue 会触发 `valueChanged` → 把自己刚开的贴底窗口解除掉 |

贴着走的时间窗用 `rangeChanged` 驱动（`_pin_bottom` 的既有机制），因为高度是渐变算出来的，
只滚一次必然漏。

**测试**：`tests/test_chat_autoscroll.py`，17 项：
- A 内容确实溢出（防空转假绿）
- B 发送后到最底部
- C 用户在中间时追加内容不打扰
- D 用户在底部时追加内容跟着走
- E 用户手动滚动 → 立刻解除跟随
- F 程序滚动不被误判成用户滚动
- G `_send()` 端到端（worker 打桩）
- H 切会话仍贴底（旧行为没坏）

已做**变异验证**：删掉 `_pin_bottom(1500)` 那行后 G 段立刻红（value 3246 / max 3312）。

## 顺带发现、尚未处理

1. **400 `request (4605 tokens) exceeds the available context size (4096)`**
   —— 系统提示 + 工具 schema + 历史一起把 4096 撑爆了。要么换大上下文模型，
   要么压缩工具 schema / 裁历史。等你定。
2. **孤儿 `llama-server` 进程**：ComfyHub 被 taskkill 强杀时不走 closeEvent，
   本地 LLM 子进程留在后台，还会锁住 `dist\ComfyHub\_internal\VCRUNTIME140.dll`
   导致打包镜像 `robocopy exit=9`。本次是手动杀的，建议加「启动时清理孤儿」。
3. **实例就绪判据**：助手等 180 秒报「未就绪」是误报——日志里
   `All startup tasks have been completed` 早已出现，只是 HTTP 端口还没监听。
   可改成「日志出现该标记也算就绪」。

---

# 追加修复（2026-11-16 02:0x）：400 上下文超限 + 「当前加载」显示

## 六、为什么报 400

```
请求被拒绝（400）：request (4605 tokens) exceeds the available context size (4096 tokens)
```

**不是模型坏了，是上下文装不下。** 4096 是 llama.cpp 的默认 `-c` 值，而我们每轮请求都要带：

| 组成 | 量级 |
|---|---|
| 22 个工具 schema | 6287 字符 ≈ **2100 tokens**（最胖 `patch_workflow` 521 字符） |
| 系统提示 + 技能 + 会话摘要 | ≈ 1000~1500 tokens |
| 本轮对话历史 | ≈ 1000 tokens |
| **合计** | **4605 tokens > 4096** |

关键是**每轮都要重发一遍工具 schema**，所以不是「聊久了才爆」，是**第一句就爆**。
截图里两次报错的 token 数（4605 → 4367）差 238，就是历史被压缩后省下来的那点。

## 修法

1. **默认上下文 4096 → 8192**（`local_llm.DEFAULT_CTX`）
   实测：启动 2.1 秒，KV cache 约 1 GB，2B 模型 28 层 GQA 纯 CPU 完全吃得下。
2. **自动降级**：`start()` 按 `[请求值, 8192, 4096]` 依次尝试。老机器内存不够时
   llama-server 会直接退出，此时自动退档而不是报「启动失败」。
3. **降级要看得见**：上下文 < 8000 时卡片明写「内存不足已自动降级，长对话仍可能被拒」，
   免得用户以为是又出 bug。

**实测**：8192 下 7319 tokens 的请求返回 **200**（原来 4605 就 400）。

## 但——加大上下文不能让变快

纯 CPU prefill 约 **166 tokens/s**：7300 tokens 花了 **44 秒**才吐出第一个字。
加大 ctx 只解决「被拒」，慢是物理限制。真要快只能减 token：

| 手段 | 能省 | 代价 |
|---|---|---|
| 工具 schema 描述瘦身 | 800~1200 tokens（≈5~7 秒） | 描述变短，2B 模型选工具可能更不准 |
| 首轮只发相关工具（按意图分组） | 1000+ tokens | 需要两阶段路由，逻辑变复杂 |
| 历史压缩阈值调低 | 视情况 | 长对话细节丢失（已有 recall_history 兜底） |

**这三条都还没做，等你定。**

## 七、「当前加载」显示

设置页 → 本地模型卡片，服务启动后多出一行：

```
当前加载  Qwen3-VL-2B（看图 + 工具）   上下文 8192 · Q4_K - Medium · 可看图
```

数据来自 **llama-server 自己的 `/props`**，不是我们启动时传的参数：

- `default_generation_settings.n_ctx` → 上下文
- `model_ftype` → 量化类型（Q4_K - Medium）
- `model_path` → 按文件名反查回模型 id，所以「不是本程序启动的服务」也认得出来
- `modalities` → 是否支持看图

**为什么不能用下拉框的值**：下拉是「想用的」，`/props` 是「正在跑的」。
用户把下拉切到 0.6B 但服务还跑着 VL-2B 时，只有后者是真的。
顺手修了这个 bug——原来 `refresh()` 在运行中也用下拉框当前项，提示会张冠李戴。

工程约束：`/props` 是 HTTP 请求（最长 2.5 秒），**只在后台线程发**；UI 只读
`SERVER.info` 缓存。拿不到时显示「读取中…」并在后台补一次，连续失败 3 次封顶，
不会无限重试刷屏。

## 测试

`tests/test_local_llm_ctx.py`，27 项：
- A 默认 8192 + 降级序列（16384→8192→4096；4096 不往上试；0 用默认）
- B `/props` 解析：文件名反查、上下文/量化/看图能力、拿不到时退回本地记录
- C `self_check` 带上模型名与上下文
- D 卡片渲染：未运行隐藏、运行中显示、**下拉框切了也不改显示**、tooltip 给路径、
  读取中状态、探测重试有上限

回归：`test_local_llm` 47/47、`test_ai_assistant` 164/164、`test_chat_autoscroll` 17/17。

---

# 追加修复（2026-11-16 02:4x）：报错改用消息提示框

## 八、问题

设置页「测试接口」失败时，那串长报错（`拉取模型列表失败：连接被拒绝…WinError 10061…`）
被写进表单下面那行说明文字里，和「模型画像…」「用于『未识别节点/无直链模型』的疑难推敲…」
排在一起，视觉上属于「页面文案」，用户直接跳过了。

## 修法：新增可复用 toast 组件

`app/ui/toast.py` → `show_toast(parent, text, level="error", ms=0)`

| 特性 | 说明 |
|---|---|
| 位置 | 主窗口底部居中，距底 28px；窗口缩放时跟着重新居中 |
| 样式 | 深色半透明底 `rgba(22,26,33,.97)` + 语义色边框/圆形图标（error 红 / ok 绿 / info 蓝） |
| 动画 | 淡入 130ms，停留后淡出 200ms 自动消失（error 7s，其它 3.8s） |
| 交互 | `WA_TransparentForMouseEvents` —— 盖在按钮上也不吃点击 |
| 单例 | 同一时刻只留一条，新的顶掉旧的（旧的先停掉自己的定时器） |
| 换行 | 最大宽 560px，长文本自动换行，绝不超出窗口 |

**关键约束**：`parent` 必须传 `self.window()`。传页面对象的话，toast 会被 QScrollArea
裁掉——它挂在滚动内容里而不是窗口上。

## 设置页改法

- **失败**：`lb_ai_state` 只写一句短的「连接失败（详情见提示）」（红色），完整报错走 toast
- **成功**：保持原样——状态行写「连接正常：N 个模型，用时 X s　当前模型：…　回复：…」。这些是多字段信息，塞 toast 反而看不清

## 踩到的坑

被顶掉的旧提示条原本用 `QTimer.singleShot(ms, self.dismiss)`，7 秒后定时器仍会回调一个
已被 `deleteLater` 的对象 → RuntimeError 噪声。改成 **QTimer 成员**，替换时先 `stop()`；
`dismiss()` 内部也加 `try/except RuntimeError` 兜底。

## 测试

`tests/test_toast.py` 19 项：位置/居中/缩放跟随/鼠标穿透/单例顶替/长文本换行不超宽/
最短停留 1200ms/超时消失/设置页失败与成功两个分支的接入。

真机渲染核对：`analysis/toast_shot.png`（1400×950，设置页，底部居中的红色提示条）。

## 顺带修打包阻塞

`build_and_launch.ps1` 新增「阶段 1b：清理孤儿 llama-server」。
ComfyHub 被强杀时不走 closeEvent，本地 LLM 子进程留成孤儿，映射着
`_internal\VCRUNTIME140.dll`，导致镜像阶段 `robocopy exit=9`（错误 32 目标端有进程锁文件），
而这时 `Get-Process` 里 ComfyHub 已经没了，很难一眼定位。脚本现在按进程名清掉它。

---

# 追加修复（2026-11-16 03:0x）：输入后「什么都没显示」

## 九、用户反馈

> 使用本地模型，启动完成后，输入对话没有显示任何内容，也不知道什么时候加载完成模型。

## 先复现，别猜

本地起 `qwen3-0.6b`（ctx 8192）真实跑一轮 `run_agent`：

```
server ready: True ctx: 8192
   8.1s  delta   {'text': 'fy'}
   ...
   8.7s  usage         {'prompt': 876, 'completion': 28}
   8.7s  assistant     {'text': '我是一个ComfyUI的出图与管理助手…'}
总耗时 8.7s，事件 30 条
```

**链路完全正常**。再看会话存档 `%APPDATA%\ComfyHub\ai_chats\*.json`：

```
[user] 你帮我生成一张JK风格的学生在街道散步的图
[user] 你再试试？
[user] 帮我启动实例
[user] 做一次体检
[user] 你启动一下试试
[user] 你启动一下试试
[assistant] 实例已成功启动，状态为 ready…（复读历史）
```

**连续 6 条用户消息，一条回复都没有。** 所以不是模型/网络坏了，是下面四件事叠在一起：

| # | 问题 | 用户感受 |
|---|---|---|
| 1 | 等待占位只有一行 12px 灰字「思考中… 0.0s」，而本地首轮要 30~60 秒 | 看不出程序在干什么 |
| 2 | 占位插入后用的是立即 `setValue`（几何未算完 → 无效），常常停在视口外 | 等于没有 |
| 3 | 等不及点「停止」→ `abandoned=True`，该轮事件全部丢弃，只剩一句 11px 灰字 | 界面一片空白 |
| 4 | `_AgentWorker.run` 只有 `finally` 没有 `except`，`run_agent` 冒出未捕获异常会被 QThread 吞掉（windowed 下 stderr 不可见） | 同样一片空白，且查不到原因 |

## 四处改动

### 1. 顶部能看出「本地模型到底能不能用」

```
● ComfyUI · :8188 (未启动)  │  ● 本地模型未启动（设置页 → 本地模型 → 启动本地服务）
● ComfyUI · :8188 (未启动)  │  ● 本地 · Qwen3-VL-2B（看图 + 工具）· 上下文 8192
```

`refresh_ai_state()` 只读 `local_llm.SERVER.running / SERVER.info` **内存缓存**，
不发 HTTP（UI 线程不能等 1~2 秒）；跟实例状态共用那个 5 秒定时器，所以别处启动/停止服务
后 5 秒内这里就跟上。

### 2. 等待占位重做（两行 + 分级预期 + 强制贴底）

```
正在请求模型… 22s                                  ← 13px，主状态
本地 CPU 推理较慢，通常需要 30~60 秒，请稍等          ← 11px，给预期
```

| 阶段 | 副行文案 |
|---|---|
| < 12s | 正在读取上下文并推理 |
| 12~40s | 本地 CPU 推理较慢，通常需要 30~60 秒，请稍等 |
| 40~90s | 已等待 N 秒。如果这条消息涉及启动实例，可能要 1~3 分钟；也可以点「停止」把问题拆小再问。 |
| > 90s | 已等待 N 秒仍未返回。建议点「停止」后重试，或在设置里换更小的模型（Qwen3-0.6B 快一倍多）。 |

并且改用 `_pin_bottom(2500)` 贴底（原来 `_scroll_bottom()` 是立即滚，布局没算完等于没滚）。

### 3. 停止要留痕

`_stop()` 的提示改成「已停止本轮回答（未完成的输出不保留）。可以点消息上的「重发」再试一次。」
并 `_pin_bottom(1200)`；`_notice` 内部也从无效的立即滚改成 `_follow_bottom()`。

### 4. 异常必须可见

`_AgentWorker.run` 加 `except` → `sig_event.emit({"kind": "error", ...})`。
以前这条路径是黑洞：异常进了 stderr，windowed 打包下看不到，用户只看到「占位消失、什么都没有」。

## 顺带修的显示瑕疵

AI 卡片标题把 `settings.ai_model` **原样**当模型名显示，而该值可能是完整路径
（`llama-server` 的 `/v1/models` 返回的就是 `model_path`，被自动填进配置后）：

```
AI 助手 · C:/Users/.../Qwen3-VL-2B-Instruct-Q4_K_M.gguf · 02:04     ← 又长又没法看
AI 助手 · Qwen3-VL-2B（看图 + 工具） · 02:04                        ← 现在
```

新增 `_model_label()`：含路径分隔符时取 basename，再按文件名反查 `local_llm.MODELS`
拿到产品名；裸 id（`mimo-v2.6-flash`）原样保留。

## 测试

`tests/test_ai_feedback.py` 21 项：
- A 顶部状态四态（本地未启动 / 本地就绪含上下文 / 云端 / 未启用），颜色点同步
- B 占位分级（<12s / 12~40s / 40~90s / >90s）+ 主行计时 + 停止清理
- C 停止后消息流里出现「已停止」「重发」
- D `run_agent` 抛异常 → error 事件（含原始类型）+ 仍发 done

`test_ai_assistant` 的 10g/10h 断言按语义更新（建议从主行挪到副行，不再锁死措辞）→ 165/165。
回归 `test_ai_stop` 22/22、`test_chat_autoscroll` 17/17、`test_local_llm_ctx` 27/27、`test_toast` 19/19。

真机渲染核对：`analysis/ai_waiting.png`（等待 22 秒时的实际界面）。

---

# 追加修复（2026-11-16 03:1x）：AI 谎报「已启动」

## 十、用户反馈

> AI 对话显示已启动，但是工作台和控制台页面没有显示任何启动了的现象。

## 事实核对

| 检查项 | 结果 |
|---|---|
| 端口 8188 | **未监听** |
| `user/comfyui_8188.log` | 最后写入 **02:17**（早就结束了） |
| 工作台底部 | 「未运行 · ComfyUI」（**正确**） |
| 控制台 | 空白（实例没跑，本来就没有 stdout —— **也正确**） |
| AI 页顶部 | 「ComfyUI · 8188 (未启动)」（**正确**） |

**所以不是界面不同步，是 AI 在说假话。** 会话存档显示：连续 6 条用户消息之后，
那条 assistant 的内容和几轮前的成功记录**一字不差** —— 本地 2B 模型看到历史里有
「实例已成功启动，状态为 ready，可访问地址 http://127.0.0.1:8188」，直接抄了一遍，
**根本没调用任何工具**。

## 三道防线

### ① 代码侧实测对账（核心，不依赖模型自觉）

`AiPage._verify_state_claim(text)`：本轮 assistant 正文出现状态结论时，拿实际进程状态对一遍。

```
AI 说：实例已成功启动，状态为 ready，可访问地址：http://127.0.0.1:8188
────────────────────────────────────────────────────────────
⚠ 上面这句与实测不符：当前实例**并没有运行**（端口未监听）。以工作台底部的状态为准；
  我这可能是复述了历史里的旧结论，可以让我「用工具重新查一次」。
```

- 正向（已启动/启动成功/ready）× 实测未运行 → 警示
- 反向（已停止/关闭成功）× 实测仍在运行 → 警示
- 拿不到进程状态（`--mcp` 外挂模式没有主界面句柄）→ **不提示**
  —— 宁可不报，也不能误报

### ② AI 的操作要写进控制台

原来控制台只收实例自身的 stdout，AI 发的启停指令零留痕，所以用户看到「控制台什么都没有」
无从判断。现在 `control_instance` 的 start/stop 都会写一行：

```
[AI] 启动实例 ComfyUI …
[AI] 停止实例 ComfyUI：本来就没在运行
```

### ③ 系统提示加固

新增第 8 条：

> **禁止复述历史里的状态结论**：像「实例已启动」「状态为 ready」「可访问
> http://127.0.0.1:8188」这种话，只有在**本次对话里真正调用过** control_instance
> 或 get_health、且返回值显示成功之后才允许说。没调工具就先调工具。

## 测试

`tests/test_state_claim.py` 15 项：
- A 说「已启动」但实测没跑 → 出现警示卡（含「并没有运行」「工作台」「复述」三个要点）
- B 说法属实 → 不打扰
- C 无关回复 → 零干扰
- D 反向（说已停止但还在跑）→ 也纠正
- E 没有 procs 句柄 → 闭嘴不误报
- F 走 `_on_event` 端到端触发，且原回复仍保留
- G `control_instance` 真的调了启动 + 控制台留痕

回归：`test_ai_assistant` 165/165、`test_ai_feedback` 21/21、`test_ai_stop` 22/22、
`test_chat_autoscroll` 17/17。

真机渲染核对：`analysis/ai_state_warn.png`。

## 一条值得记住的原则

**小模型「说得好听」不能当事实。** 凡是模型能断言的外部状态（实例在不在跑、文件在不在、
端口通不通），都要有一次代码侧的实测对账，否则用户被误导时毫无察觉——
这次的账是「AI 页顶部写着未启动，正文却说已启动」，用户只能自己发现矛盾。

---

# 追加修复（2026-11-16 19:1x）：不能调工具的本地模型不进清单

## 十一、用户反馈

> 有用户反馈，无法调用工具的本地 AI 不要放进去，没用又浪费空间。

## 先实测，再删

裸提示（无系统提示、无历史）下测，0.6B **能**选对工具 —— 但那是假象。用**真实链路**
（完整系统提示 + 22 个工具 schema + 4 条历史，问「帮我启动实例」）重测：

| 模型 | 工具调用 | 结果 |
|---|---|---|
| `qwen3-0.6b` | **0 次**（两次请求都是 0） | 直接凭记忆瞎答，完全不会调 |
| `qwen3-vl-2b` | 1 次 `control_instance` | 正常 |

结论：0.6B 这个量级撑不住真实上下文。裸 prompt 能选对不代表能用，
**以后评估本地模型必须走真实链路**。

## 改动

### ① 从清单移除 Qwen3-0.6B

`local_llm.MODELS` 现在只剩 3 个，注释里写明实测证据，防止以后「好心」加回来：

| 模型 | 体积 | 用途 |
|---|---|---|
| Qwen3-1.7B（严谨） | 1.1 GB | 工具调用最强，诊断报错 / 读日志 |
| Qwen3-1.7B（创作向） | 1.1 GB | 去偏版，写角色设定不被拒答；工具调用略弱 |
| Qwen3-VL-2B（看图 + 工具） | 1.9 GB（含 mmproj） | 能看报错截图，也支持工具调用 |

### ② 「清理残留模型」——真正解决"浪费空间"

清单变了，磁盘上旧的 0.6B 权重就成了孤儿：`has_model` 查不到、下拉里选不到，纯粹占地方。
新增 `orphans()` / `remove_orphans()`，卡片上多一个按钮（**有残留才显示**）：

```
本机实测扫出：Qwen3-0.6B-Q4_K_M.gguf   378 MB
```

- 只认 `.gguf` / `.safetensors`，别的一概不动
- 删除前弹确认框，逐条列出文件名与体积
- 目录不存在 / 文件被占用都安全跳过，不中断

### ③ 默认选「已下载」的模型

原来 `saved` 指向已移除的模型时一律回落到列表第一项（1.7B，还要再下 1.1 GB），
现在优先选磁盘上已经有的那个。

## 连带清理

- 等待占位 >90 秒的文案原来写「换 Qwen3-0.6B 快一倍多」，改成不点名型号
- `i18n_en` 删掉 0.6B 的模型名翻译
- `test_local_llm` 里「模型清单 4 个」「下拉有四项」这种**写死数量**的断言，
  改成与 `len(MODELS)` 对齐 —— 清单类断言写死数字，一增删就误报

## 测试

`tests/test_model_prune.py` 19 项：
- A 清单不含 0.6B、未知 id 安全回落、每个模型都有用途说明
- B 残留识别（清单内的不算、非权重文件不碰、字节数正确、删除后清单内文件保留、
  清完无残留、目录不存在安全返回空）
- C 卡片默认选已下载的模型、无残留时按钮隐藏、有残留时按钮可见且 tooltip 点名文件

回归：`test_local_llm` 48/48、`test_ai_feedback`、`test_local_llm_ctx`、
`test_ai_assistant` 165/165 全绿。

真机渲染核对：`analysis/local_llm_prune.png`。

---

# 追加修复（2026-11-16 19:5x）：小模型「不会调工具」的真凶是提示太长

## 十二、用户提问

> 这几个都是可以识别图和调用工具的吗？

答案分两半，**看图**这半是清楚的，**调工具**那半让我把上一轮的结论推翻了。

## 先看图：只有第三个能看

| 模型 | 看图 | 依据 |
|---|---|---|
| Qwen3-1.7B（纯文本 · 工具） | ❌ 不能 | `vision=False`，没有 mmproj |
| Qwen3-1.7B 创作向（纯文本） | ❌ 不能 | 同上 |
| Qwen3-VL-2B（看图 + 工具） | ✅ 能 | 带 819 MB 的 `mmproj-F16.gguf` |

原来名字叫「严谨 / 创作向」，完全没提「不能看图」——用户会以为是通用型号。
已改成 `Qwen3-1.7B（纯文本 · 工具）` / `Qwen3-1.7B 创作向（纯文本）`，
note 首句就写「不能看图（附图片会被忽略）」。

## 再说调工具：我上一轮的判断错了

上一轮我删掉 0.6B 时写过「这个量级撑不住真实上下文」。这轮给 1.7B 做同样的
真实链路测试，结果是 **3/15**——几乎一样烂。

但换个测法就露馅了。**同一模型、同一 22 个工具 schema、同一 4 条历史，
只改系统提示长度：**

| 系统提示 | 字数 | prompt_tokens | 工具调用命中 |
|---|---|---|---|
| A 完整版（两个剧本 + 8 条注意事项 + 环境画像 + 技能包） | 1886 | 3450 | **3/15** |
| B 精简版（硬规则 6 条 + 工作流 5 条） | 855 | 2521 | **18/18** |

再走 `run_agent`（产品真实链路）复测 1.7B：**10/10**，VL-2B 也是 **10/10**。

**所以不是模型不会调工具，是我们喂了 3450 tokens 的说明书把它的注意力稀释了。**
云端大模型不受影响（上下文和注意力都够），所以按服务商分流。

## 改法

```python
_is_local_ai(cfg)   # provider=local，或 base_url 含 127.0.0.1 / localhost / [::1]
    → _system_prompt_local(ctx)   # 855 字，硬规则 + 工作流
否则 → 原完整版（1850 字，含剧本 A/B、环境画像、技能包）
```

精简版**刻意不塞**这四块（都是稀释源）：

| 去掉的 | 理由 |
|---|---|
| 【本机环境】显卡/torch/磁盘/最近崩溃 | 需要实时数据让它调 get_health 就好 |
| 技能包正文 | 长度不确定，且本地模型读不完 |
| `tool_note` | schema 已经通过 API 传了，重复 |
| 会话画像 | 对 1.7B 是噪声 |

用户自己写的助手设定保留（那是他的原话），但截到 400 字，避免又把长度撑回去。

## 精简后冒出来的新问题：擅自启停实例

VL-2B 在精简提示下，问「现在实例是什么状态」会这么干：

```
get_health → control_instance → get_health → control_instance → ...（8 次）
```

因为完整版第 1 条写着「实例没跑就 control_instance 自己拉起来」——之前提示太长它没执行，
一精简就认真执行了。用户只是想知道状态，AI 却去启动一个要跑 90 秒的 ComfyUI。

补第 7 条：

> 用户没明确说「启动 / 重启 / 停止」时，**只报告状态，不要自己启停实例**。
> 查状态和拉实例是两件事。实例确实没跑时，先如实说，再问一句要不要启动。

重测 VL-2B：**10/10，误调 control_instance 0 次**。

## 0.6B 仍然该删（这次两侧都测了）

| 提示 | 0.6B 命中 |
|---|---|
| 完整版 1850 字 | 0/2 |
| 精简版 975 字 | **1/18** |

所以结论要修正成「**<1B 撑不住 22 个工具 schema**」，而不是「小模型都不行」。
`MODELS` 里的注释已按新数据重写。

## 顺带修：分段下载单段超时不该整轮作废

下 1.1GB 的 Qwen3-1.7B 时踩到：8 线程跑到 **98%（1045 MB）**，第 7 段 `TimeoutError`，
`_download_threaded` 返回 False，上层 `pos=0` 回退单连接**从 0 重下**——已下的全白费，
而 hf-mirror 单连接只有 2~4 MB/s，白等 6 分钟。

修法：worker 段内重试 3 次（`_SEG_RETRY`），每次从该段当前偏移 `pos` 续。
分段各自写自己的偏移，互不影响，所以不用删 `.part`。

## 测试

新增 `tests/test_local_prompt.py` 31 项：
- A 本地/云端判定（127.0.0.1 / localhost / [::1] / provider=local / 各种云厂商 / 空值 / None）
- B 精简提示边界：**<1200 字**、9 条硬约束逐条在、4 类稀释内容逐条不在
- C 用户设定保留 + 超长截断 400 字
- D 云端仍是完整版（含剧本、吃技能包、吃画像），本地不吃

`tests/test_download_threaded.py` 加 E 段（`_FlakyHandler` 让第一个真实分段首次请求断连）→ 24/24。

**两处都做了变异验证**：
- `_is_local_ai` 恒返回 True → A/D 段 11 项红
- `_SEG_RETRY=1` → E 段红（日志出现「分段未成功，本源改用单连接重下」）

回归：`test_local_llm` 48/48、`test_ai_assistant` 165/165、`test_ai_stop` 22/22、
`test_workflow_lib` 38/38、`test_download_resume` 14/14、`test_download_fixes` 14/14，
其余 `test_model_prune` / `test_local_llm_ctx` / `test_ai_feedback` / `test_state_claim` /
`test_chat_autoscroll` / `test_toast` 全部通过。

## 一条该记住的方法论

**同一句「小模型不会用工具」，可以是模型的问题，也可以是提示的问题。**
这次差点把 1.7B 也删了 —— 只因为测试用的是产品里那份 1886 字的完整提示。
以后评估任何小模型，必须：
1. 用**产品真实会发的那份提示**测（裸测和精简提示都会给假结论）；
2. 至少 5 个问题 × 2 轮，一次 0/2 不能给一个量级判死刑；
3. 出现「不会用工具」先怀疑提示长度，再怀疑模型。

---

# 追加（2026-11-16 21:2x）：不能识图的模型也删掉

## 十三、用户口径

> 不能识图的也删掉

前一轮我把清单从 4 个减到 3 个（删了 0.6B）。剩下的两个 Qwen3-1.7B 虽然
工具调用实测 18/18，但**都不能看图**——而 VL-2B 是功能超集（能看图 + 工具 18/18）。
两个 1.1 GB 的纯文本模型留在清单里只是白占 2.2 GB。

## 现在的清单

| 模型 | 体积 | 看图 | 工具 | 状态 |
|---|---|---|---|---|
| **Qwen3-VL-2B（看图 + 工具）** | 1.03 GB + mmproj 819 MB | ✅ | 18/18 | **唯一保留** |

已移除（`MODELS` 里留了注释和实测数据，防止以后「好心」加回来）：

| 模型 | 移除原因 |
|---|---|
| Qwen3-0.6B | 完整提示 0/2、精简提示 1/18，撑不住 22 个工具 schema |
| Qwen3-1.7B（纯文本 · 工具） | 工具 18/18 但不识图 |
| Qwen3-1.7B 创作向（纯文本） | 不识图，且工具调用未实测 |

新规则写进注释：**以后加模型必须同时满足「能看图」+「真实链路工具调用达标」**。

## UI 配合改动

只剩一个模型时，下拉框还弹列表会让人以为漏加载了。改成 `count() > 1` 才可点：

```python
self.cb_model.setEnabled(installed and not busy and self.cb_model.count() > 1)
```

文本仍可见，只是选不动。

## 磁盘上会多出「残留」

清单删了，磁盘上那两个文件就成了孤儿（`has_model` 查不到、下拉里选不到）。
本机实测：

```
Qwen3-0.6B-Q4_K_M.gguf     378.3 MB
Qwen3-1.7B-Q4_K_M.gguf    1056.1 MB        ← 上一轮为实测刚下的
```

**没有自动删**——卡片上「清理残留模型」按钮会显示（tooltip 逐条列出文件名与体积），
用户点确认才删。这是有意的：删清单是我的判断，删用户磁盘文件得他自己点头。

## 踩到两个测试写法隐患（都值得记）

1. **假红**：`test_local_llm` 里 `fake = dict(L.MODELS[0])` 隐含假设「第一项是纯文本模型」。
   现在 `MODELS[0]` 是 VL-2B（vision=True 带 mmproj），复制出来的 fake 也要求 mmproj，
   「完整文件算就绪」这条直接假红 → 改成显式构造纯文本桩对象。
2. **假绿**：`test_local_llm_ctx` 用 `findData("qwen3-1.7b")` 模拟「用户切到别的模型」。
   模型删了之后返回 `-1`，`setCurrentIndex(-1)` 等于没切——测试照样全绿，但断言
   「按实际运行显示」根本没被验证。改成临时 `addItem` 塞一个假项，并加一条
   `下拉确实切到了别的项（不是 -1）` 守卫。

**教训：测试里凡是「按 id/顺序去清单里取」的地方，清单一变就会假红或假绿。
桩对象要显式构造，场景要显式造出来。**

## 测试与构建

- `test_model_prune` 新增两条：`纯文本 1.7B 已全部移除`、`清单内每个模型都能看图（带 vision + mmproj）`
- `test_local_llm` / `test_local_llm_ctx` 修掉上面的假红/假绿
- `i18n_en` 删掉两个 1.7B 的模型名与 note 翻译（避免以后加回同名模型匹配到过期翻译）
- 回归：`test_local_llm` 48/48、`test_local_llm_ctx`、`test_model_prune`、`test_local_prompt`、
  `test_ai_assistant` 165/165、`test_ai_stop` 22/22、`test_ai_feedback`、`test_state_claim`、
  `test_toast`、`test_chat_autoscroll` 全绿
- 真机渲染核对：`analysis/local_llm_only_vl.png`

## 一个要提醒的风险

只剩 VL-2B 意味着**最低内存门槛抬到约 3 GB**（权重 1 GB + mmproj 819 MB + KV cache）。
之前 1.7B 只要约 1.5 GB，低配机能跑。如果以后有用户反馈「机器跑不动」，
要么加回一个 1.7B 纯文本版，要么接受「本地模型需要 8 GB 内存以上」。
