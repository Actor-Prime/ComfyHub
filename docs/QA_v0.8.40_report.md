# ComfyHub v0.8.40 远程运维 — QA 独立验证报告

> 验证人：QA 工程师（严过关）
> **最终验证基于 commit `a440f8c`**（0.8.40 全部修复落地后的 HEAD）
> 历程：首轮 `6841948` → 复核 `8594bde`（补词表）→ **终验 `a440f8c`**（D-01/02/03/05/06 + 入口缺口全修完）
> 环境：`F:/ComfyHub/.venv_build/Scripts/python.exe`（Python 3.13.14 / PySide6 / paramiko 5.1.0），离屏 `QT_QPA_PLATFORM=offscreen`
> 约束遵守：全程**零真实网络调用**、**零 `~/.ssh` 读写**；SSH/隧道全部走 `tests/fake_ssh.py` 的脚本化桩
>
> **定位**：工程师自测是按验收条目逐条写的（我已全部复核，见 §V8，全绿）。本报告刻意换角度：
> 用「从配置默认值反向穷举字段」「用另一个 socket 反证端口是否真空出来」「离屏切英文读控件上的字」
> 这类**不能靠改状态字符串骗过**的断言，去找「功能是通的、但会骗人」的那类缺陷。
>
> **终验结论：全部阻断项已修，建议「可以发布」。** 逐项「改前 → 改后」证据见 §13。

---

## 0. 结论先行

| # | 结论 | 依据 |
|---|---|---|
| 1 | **隧道没有假活**。FAILED 之后本地端口能被另一个 socket 重新 bind 回来（不是看 `is_active()` 的自述） | §V3 42/42 |
| 2 | **字段没有丢**。18 个远程运维字段在五处落点全部到位，双向（保存→显示、控件→保存）逐字段哨兵值往返通过 | §V1 126/126 |
| 3 | ~~凭据有一处屏幕泄漏~~ → **已修**。改 SSH 密码/密钥口令时，保存对比框不再显示旧值原文 | §V2 43/43（原 40/43） |
| 4 | ~~i18n 系统性残留~~ → **已修**。英文模式下远程运维页签 40/40 无残留中文 | §V6 40/40（原 22/40） |
| 5 | 并发/锁/取消/删除中途/线程分层全部干净：6 条失败路径跑完都不泄漏实例锁 | §V4 38/38 §V7 36/36 |
| 6 | 清理策略边界安全：只删空目录，受保护文件（60 分钟内 / `.part`）一个都没动 | §V5 45/45 |
| 7 | 测试隔离性**已修好**：跑完 `test_remote_ui` 后真实 `%LOCALAPPDATA%\ComfyHub\remote` 仍是 0 个文件 | §V8-3 |
| 8 | **入口缺口已补**：`＋ 添加 ComfyUI` 现在弹 2 项菜单，选「远程」落到 `InstanceDialog(kind=remote)` | §13 A 段 21/21（原 14/19） |
| 9 | **新建的远程实例字段不丢**：uuid 与页签 15 字段约定一致，密码落盘为 `enc:v1:` | §13 B 段 |

**放行建议：可以发布。** D-01（凭据屏幕泄漏）、D-03（i18n 残留）两条 P1 阻断项已修并通过复验；
入口缺口（§13 D-12）已补；D-02/D-05/D-06 已修；D-04/D-10 已提交；D-09 测试基建已修。
只有 D-07/D-08/D-11 三条 P3 与两条既有测试缺陷留待后续，均不影响用户使用。

终验数字（全部基于 `a440f8c`，可独立复跑）：

| 文件 | 对应 | 改前 | **改后** |
|---|---|---|---|
| `tests/test_qa_v0840_fields.py` | V1 字段五处一致性 | 126/126 | **126/126** |
| `tests/test_qa_v0840_creds.py` | V2 凭据泄漏面 | 40/43 | **43/43** |
| `tests/test_qa_v0840_tunnel.py` | V3 隧道假活 | 42/42 | **42/42** |
| `tests/test_qa_v0840_state.py` | V4 状态机与并发 | 38/38 | **38/38** |
| `tests/test_qa_v0840_cleanup.py` | V5 清理策略边界 | 45/45 | **45/45** |
| `tests/test_qa_v0840_i18n.py` | V6 i18n 完整性 | 22/40 | **40/40** |
| `tests/test_qa_v0840_layers.py` | V7 线程规则与分层 | 36/36 | **36/36** |
| `tests/test_qa_v0840_entry.py` | 入口 + 新建实例全链路 | 14/19 | **21/21** |
| `tests/test_qa_v0840_i18n_save.py` | 下拉翻译不许写坏配置（新增） | — | **10/10** |
| `tests/test_qa_v0840_regress.py` | V8 全量回归（19 用例） | 25/26 | **28/28** |

缺陷清单（按严重度）：

| 编号 | 严重度 | 标题 | 状态 |
|---|---|---|---|
| D-01 | **P1 阻断** | 保存对比框把 SSH 密码/密钥口令**旧值原文**显示为明文 | ✅ 已修（`1546c64`） |
| D-03 | **P1 阻断** | 英文模式下远程运维页签大量中文残留（违反 PRD X2 / P0） | ✅ 已修（`1546c64`） |
| D-12 | **P1** | `＋ 添加 ComfyUI` 单击直连安装向导，**没有任何入口能建远程实例**（功能做了等于没做） | ✅ 已修（`05fd9fe`） |
| D-02 | P2 | `pre_ready` 是死字段：实例本来就在跑，UI 仍报「✅ 已就绪」 | ✅ 已修（`9c53ca8`） |
| D-05 | P2 | 云平台钩子 `headers` 里的 Token **明文落盘**，且界面无提示 | ✅ 已修（`9d9a28d`，加了诚实提示） |
| D-06 | P3 | `registry or registry_mod.registry`：空注册表是 falsy，DI 注入被静默换成全局单例 | ✅ 已修（`a440f8c`） |
| D-04 | P2 | `tests/test_cloud_hooks.py`（53 条断言）被 `.gitignore` 吞掉，从未入库 | ✅ 已提交（`600f828` + 我补的例外） |
| D-09 | P2 | 离屏 Qt 进程退出偶发 `0xC0000005`，按退出码判 CI 通过不可靠 | ✅ 已修（`01785ba`，见 §12） |
| D-10 | P3 | 工作区不干净：`app/i18n_en.py` 有 29 行未提交改动 | ✅ 已解决（`8594bde`） |
| D-13 | P2 | `tests/test_lang_switch.py` **永久挂死**（确认框模态），且未隔离 `APPDATA` 会写用户真实配置 | ✅ 已修（见 §13） |
| D-07 | P3 | `clean_dir` 的「目录不可写」分支在 Windows 上基本不可达（`os.access` 对目录恒 True） | ⏳ 后续（行为安全，仅诊断文案出不来） |
| D-08 | P3 | 不动任何字段也刷一条「凭证 （空）→（已更新，不回显）」假改动（0.8.39 既有） | ⏳ 后续 |
| D-11 | P3 | `size` 策略的 `total` 含受保护文件，可能删光可删的仍超标且无任何提示 | ⏳ 后续 |

---

## V1 字段五处一致性（程序化穷举）

**结论：通过。126/126 断言全绿，无字段丢失。**

### 做法（不靠眼力）
从 `app/config.py` 的 `_INST_DEFAULTS` 反向取出所有以 `ssh_` / `cloud_` / `tunnel_` 开头的键，
加上 `last_ops_channel` / `insecure_warn_muted`，共 **18 个**，逐一断言它们出现在：

① `config._INST_DEFAULTS` ② `ComfyuiPage._TRACK` ③ `ComfyuiPage._SAVE_LABELS`
④ `ComfyuiPage._snapshot()` ⑤ `remote_ops_page.collect()`

### 证据

```
== 1. 字段来源（从 config 默认值反向穷举，共 18 个）==
  ✅ 1a 字段总数 = 18（ARCH §3.2 的 13 实例字段 + 4 隧道/告警 + timeout）
== 2. ② _TRACK ==            （18 条全部 ✅，cloud_start_timeout_s 按设计 ∉）
== 3. ③ _SAVE_LABELS ==       （18 条全部 ✅）
== 4. ⑤ collect() ==          （15 条 ∈，3 条按设计 ∉）
== 5. ④ _snapshot() ==        （同上）
== 7. 往返：保存的值 → 页签显示 → 再收集回来（逐字段哨兵值）==   （15 条全部 ✅）
== 8. 反向：改控件 → _snapshot() 立即可见（改了会保存）==        （15 条全部 ✅）
== 9. 落盘往返：18 个字段全写 config.json 再读回，一个都不许丢 ==  ✅
V1 结果：126/126 通过
```

关键往返证据（举例）：

```
✅ 7 ssh_start_cmd 装载后回收集 = 哨兵值
   ['cd /srv/ComfyUI && nohup python3 main.py --port 8189 &' vs 同值]
✅ 7 cloud_start 装载后回收集 = 哨兵值
   [{'url': 'https://api.example.com/v1/start', 'method': 'PUT',
     'headers': 'Authorization: Bearer T0K', 'body': '{"id": "i-1"}'} vs 同值]
✅ 8 last_ops_channel 控件→快照  ['cloud' vs 'cloud']
```

### 关于「允许的例外只有 cloud_start_timeout_s」这一条要求的说明

**实际有 3 个例外，不是 1 个。** 另外两个是代码里**明确注释过的设计选择**，不是漏接线：

| 字段 | 在 `_TRACK`/`_SAVE_LABELS` | 在 `collect()`/`_snapshot()` | 原因（源码注释，`comfyui_page.py:1341-1344`） |
|---|---|---|---|
| `cloud_start_timeout_s` | ✗ | ✗ | 本版固定 300，不暴露 UI（ARCH §3.2） |
| `ssh_host_fingerprint` | ✓ | ✗ | 由 `remote_fingerprint` 首次连接时写入；进 `collect()` 会把后台刚记的指纹冲回旧值 |
| `insecure_warn_muted` | ✓ | ✗ | 由告警模态 / 设置页「恢复明文告警」写入，同上 |

我额外验证了这个「例外」是**安全的**，不是掩盖漏键：

```
✅ 6a _save 写回循环带 `if k in new` 守卫
✅ 6b ssh_host_fingerprint 无 UI 来源时不会被 _save 覆盖
✅ 6b insecure_warn_muted 无 UI 来源时不会被 _save 覆盖
✅ 6c 后台字段既不进 collect 也不进 snapshot（口径一致）
```

即 `_save` 的 `for k in self._TRACK: if k in new:` 缺键即跳过 → 指纹/「不再提示」不会被
每次保存冲掉。**判断：设计自洽，不算缺陷。** 建议 LEAD 更新验收口径，避免下次误判。

---

## V2 凭据泄漏面

**结论：发现 1 个 P1 缺陷（D-01）；其余 5 个检查面全部干净。**

### L1 保存对比框 —— ❌ **D-01：把旧口令原文打在屏幕上**

触发路径（真实 `_save()`，把 `diff_changes`/`confirm_changes` 换成捕获桩，拦下模态）：

> 用户在「🛠 远程运维」页签改 SSH 密码 → 点「保存设置」

用户实际在确认框列表里看到的那一行：

```
❌ L1b'''' 用户在确认框里实际看到的那一行
   [SSH 密码：OLD-PW-1a2b3c-DO-NOT-LEAK  →  （已更新，不回显）]
```

**根因（两段代码合起来才成立）**：
1. `remote_ops_page.collect()` 在输入框留空时**回填已存的明文口令**（`remote_ops_page.py:423-426`）；
2. 于是 `_loaded`（加载时的快照）里就有明文口令；`_save` 的脱敏只改 **new**、不动 **old**：

```python
# app/ui/pages/comfyui_page.py:1534-1539
_disp = dict(new)
for _k in ("auth_value", "auth_pass", "ssh_password", "ssh_key_passphrase"):
    if _disp.get(_k) and _disp.get(_k) != (old.get(_k) or ""):
        _disp[_k] = "（已更新，不回显）"      # ← 只脱敏「新值」
changes = diff_changes(old, _disp, self._SAVE_LABELS,   # ← old 原样传进去
                       defaults=self._SAVE_EMPTY_TEXT)
```

`diff_changes` 把 `old` 列直接 `_fmt()` 上屏，所以**旧值列是明文**。

证据链（V2 逐条）：

```
❌ L1b 改密码后对比框不含新旧口令原文   [['OLD-PW-…', 'OLD-PP-…']]
✅ L1b'' 新值列是脱敏占位              [（已更新，不回显）]
❌ L1b''' 旧值列也不许出现口令原文      [OLD-PW-1a2b3c-DO-NOT-LEAK]
❌ L1b'''' 用户在确认框里实际看到的那一行
✅ L1b''''' 根因：collect() 把已存口令原文带进了 _snapshot（→ _loaded → old）
```

**为什么这条要判阻断**：这段代码自己的注释写着「**把 token/密码原文打在屏幕上没必要
（旁边可能有人看着）**」——意图明确，实现漏了一半。而且 0.8.39 只把 `auth_value`/`auth_pass`
纳入脱敏（那两个字段的输入框装载时**不回填**，`old` 恒为空 → 不泄漏），
**0.8.40 把 `ssh_password`/`ssh_key_passphrase` 加进来，而这两个字段恰恰是「留空=保持原值、
装载时回填明文」的**，于是新版本第一次把明文送进了这条泄漏路径 —— 属本版引入。

**修复（已实测有效，1 行）**：`diff_changes` 本来就支持 `secret_keys` 参数，但 `_save` 没传。

```python
changes = diff_changes(old, _disp, self._SAVE_LABELS,
                       defaults=self._SAVE_EMPTY_TEXT,
                       secret_keys=("auth_value", "auth_pass",
                                    "ssh_password", "ssh_key_passphrase"))
```

实测效果：

```
现状（不传 secret_keys）: ('SSH 密码', 'OLD-PW-LEAK', 'NEW-PW-LEAK')
建议修法（传 secret_keys）: ('SSH 密码', '已设置', '已修改')
```

**复现命令**：`.venv_build/Scripts/python.exe -u tests/test_qa_v0840_creds.py`（看 L1b 三条 ❌）

### L2 app.log / 控制台 / 异常消息 —— ✅ 干净

```
✅ L2b  SshError.__str__ 不含密码原文    [auth_password: AuthenticationException: Authentication failed.]
✅ L2b' detail 不含密码原文              [AuthenticationException: Authentication failed.]
✅ L2c  连接日志行不含密码原文           [['已记录 192.168.1.20:22 的主机指纹 SHA256:HM3f…']]
✅ L2d  密钥文件不存在 → kind=key_missing（E2b，不误报成认证失败）
✅ L2d'' 加密私钥未给口令 → auth_passphrase 且消息只含路径
✅ L2d''' 给了口令仍解不开 → auth_passphrase（口令不对）
✅ L2f  classify_auth_error 的 detail 只含异常类名与消息
```

补充：`remote_ops._hook_detail()`（`remote_ops.py:401-406`）只打 `METHOD url`，
**不打请求头** —— 云平台 Token 不会进日志。这一点做对了。

### L3 DPAPI —— ✅ 全部正确

```
✅ L3a config.json 里 ssh_password 是 enc:v1: 前缀        [enc:v1:AQAAANCMnd8BFdERj]
✅ L3b config.json 里 ssh_key_passphrase 是 enc:v1: 前缀  [enc:v1:AQAAANCMnd8BFdERj]
✅ L3c 整个 config.json 文本里搜不到口令原文
✅ L3d 内存 cfg 未被 save_config 改成密文（后续请求才能用）
✅ L3e load_config 后内存里是明文
✅ L3e' load_config 后 ssh_key_passphrase 也是明文
```

### L4 「留空 = 保持原值」三条路径 —— ✅ 全部成立

```
✅ L4-1  页签 collect()：输入框留空 → 回落到已存口令
✅ L4-1' 页签 collect()：填了就用新值
✅ L4-2  _save 二次兜底：快照里口令为空 → 回填成已存值
✅ L4-3  首次新增：无已存口令时 collect() 是空串而不是 None/异常
✅ L4-3' 首次新增：占位提示「加密保存在本机（DPAPI）」
✅ L4-3'' 已有口令时占位提示「已保存（留空则不修改）」
```

### L5 MCP 配置片段 —— ✅ 不导出隧道端口、不导出凭据

```
✅ L5-pre  隧道激活时 api_base 是回环        [http://127.0.0.1:18189]
✅ L5-pre' 同时 saved_api_base 仍是真地址     [https://real.example.com]
✅ L5a MCP 片段用的是 saved_api_base          ['"COMFYUI_URL": "https://real.example.com"']
✅ L5b MCP 片段不含隧道本地端口 18189
✅ L5c MCP 片段不含任何口令 / Token 原文
✅ L5d 清掉覆盖后 api_base 立即回落
```

`settings_page._mcp_snippets()`（`settings_page.py:81`）确实改用了 `apib.saved_api_base(inst)`。
这个改动很关键：隧道端口只在 GUI 进程活着时有效，粘到别的程序里必然连不上。

### L6 云平台请求头 Token —— ⚠ **D-05：明文落盘且无提示**（按指示给判断，不判阻断）

事实（实测）：

```
✅ L6a 云钩子请求头里的 Token 明文落在 config.json（未加密）  [明文]
✅ L6b cloud_start / cloud_stop 不在 _SECRET_FIELDS 里
      [('auth_value', 'auth_pass', 'ssh_password', 'ssh_key_passphrase')]
✅ L6c 云平台分区没有「Token 会明文落盘」的提示                [无提示]
```

**我的判断（不直接判缺陷，给 LEAD 决策）**：

- 不一致点在**同质不同待遇**：`inst["auth_value"]`（Bearer Token）是 DPAPI 加密的，
  而云钩子 `inst["cloud_start"]["headers"]` 里的 `Authorization: Bearer xxx` 是明文的。
  用户刚在上半个页面看到「加密保存在本机（DPAPI）」，下半个页面的 Token 却明文躺在
  `config.json` 里 —— 同一份文件、同一类凭据、两种待遇，容易形成错误预期。
- 缓解因素（客观存在）：① 不经过 SSH/SFTP，不出网；② 不进任何日志（见 L2 补充）；
  ③ 只有拷走 `config.json` 才泄漏，与 0.8.39 的 `remote_output_dir` 等明文同级。
- 修复成本很低：把整个 `cloud_start`/`cloud_stop` 的 `headers` 文本加进 `_SECRET_FIELDS`
  形态（或只保护 `headers` 字段值）。

**建议定级 P2，下个补丁修；本版不作为阻断项**，但若 LEAD 认为「用户预期一致性」是发布门槛，
升到 P1 也能站得住（因为它是**本版新增字段**引入的新明文）。

---

## V3 隧道假活（架构标注的最高风险）

**结论：通过。42/42。隧道不会假活。**

关键设计：**不用 `registry.is_active()` 自证清白**（那是注册表自己的说法），
而是用 `can_bind(port)` —— 让**另一个 socket** 去 bind 那个本地端口：

```python
def can_bind(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port)); return True
    except OSError:
        return False
    finally: s.close()
```

bind 得回来 = 操作系统确认没人监听 = 端口没有假活。这条断言无法被「改状态字符串」骗过。

### V3-1 正常路径

```
✅ V3-1c api_base(inst) = http://127.0.0.1:<本地端口>     [http://127.0.0.1:57163]
✅ V3-1d saved_api_base(inst) 仍是用户填的真地址          [http://192.168.1.20:8188]
✅ V3-1e inst['base_url'] 一字未改
✅ V3-1f effective_base_info 的 via_tunnel=True / is_insecure=False
✅ V3-1g 本地端口此刻不可被别人 bind（隧道真的在监听）
✅ V3-1h stop() 后端口立即可以被别人 bind
✅ V3-1i stop() 后 api_base 回落真地址
```

### V3-2 首次连接就失败（最容易被忽略的假活口：端口已 bind、覆盖还没登记）

```
✅ V3-2b 最终状态 = FAILED
✅ V3-2c registry.is_active() == False
✅ V3-2d ★本地端口可以被别人重新 bind（没有假活）  [bind ok]
✅ V3-2e api_base 回落到 base_url
✅ V3-2f 状态文案 = 「隧道重连失败，已停止」
```

### V3-3 连上后被切断 → 5 次重连用尽

```
✅ V3-3d 状态序列含 CONNECTED → RECONNECTING → FAILED
   [['connecting','connected','reconnecting','reconnecting','reconnecting',
     'reconnecting','reconnecting','failed']]
✅ V3-3e 退避实测序列 = [1,2,4,8,16]           [[1, 2, 4, 8, 16]]
✅ V3-3f registry.is_active() == False
✅ V3-3g ★本地端口可以被别人重新 bind（没有假活）  [bind ok]
✅ V3-3h api_base 回落 base_url
✅ V3-3i 重连次数上限 = 5（backoff_n 归 5）
```

### V3-4 断线期间（RECONNECTING）基址必须已回落

做法：在 `sleep_fn` 里打点 —— 那是「已 emit RECONNECTING、还没连上」的确切时刻。

```
✅ V3-4a RECONNECTING 期间 registry 已清空   [[(1,False),(2,False),(4,False),(8,False),(16,False)]]
✅ V3-4b RECONNECTING 期间 api_base 已回落 base_url
   [['http://192.168.1.20:8188']]      ← 只有一个值，说明从未指向过已关的本地端口
```

这条验证了 ARCH §12 #5 的设计取舍确实落地：重连期间宁可让用户看到「连接被拒绝」，
也不让请求打到一个已经关掉的本地端口。

### V3-5 端口冲突

```
✅ V3-5a 端口被占 → start() 返回 False
✅ V3-5b 文案含「已被占用」
✅ V3-5c 文案给出建议端口（port+1 / port+99）
   本地端口 `57167` 已被占用，隧道没建。
   换一个试试（57168 / 57266），或先关掉占用它的程序。
✅ V3-5d 不注册覆盖（失败分支绝不留 override）
✅ V3-5e 状态置 FAILED（不留在 connecting）
✅ V3-5g 同实例二次 start 复用同一个 job（不会再抢一个端口）
```

### V3-6 实例改名 / 删除

```
✅ V3-6a 覆盖已登记
✅ V3-6b 改名后 override_for 仍命中（键是 id 不是名字）
✅ V3-6c 删除实例后清覆盖 → 立即回落
✅ V3-6d job 不存在时 stop() 仍清掉残留覆盖（幽灵覆盖）
```

### V3-7 退出路径

```
✅ V3-7a stop() 幂等、不抛异常
✅ V3-7b TunnelManager.stop_all() 空跑不抛异常
✅ V3-7c 收尾后残留隧道线程数 = 0
```

### 顺带发现：D-06 注册表单例的 DI 脚枪（P3）

```
✅ V3-6-0 【脚枪】传入空 RemoteRegistry 会被 `or` 换成全局单例（__len__=0 即 falsy）
   [传入 1242121927120 实际 1242122079600]
✅ V3-6-0' 非空则尊重传入的注册表
```

`app/tunnel.py:116` / `:433`：

```python
self._registry = registry or registry_mod.registry
```

`RemoteRegistry` 定义了 `__len__`，**空表就是 falsy**，于是「传入一个空的自定义注册表」
会被静默换成模块级单例。生产路径（`TunnelManager()` 不传 `registry`）不受影响，
但任何 DI / 单测注入都会踩到（我第一版 V3 测试就是被它绊倒的，现象是「断言莫名其妙失败」）。
建议改成 `registry if registry is not None else registry_mod.registry`。

---

## V4 状态机与并发

**结论：通过。38/38。锁、取消、清理全部干净；但 `pre_ready` 没被 UI 消费（D-02）。**

### V4-1 真并发（不是顺序调用）

```
✅ V4-1a 第二条线程拿到 busy（不是排队、不是覆盖前一次）  [busy]
✅ V4-1b 忙的时候 is_busy() 认得出来
✅ V4-1c 第一条线程正常结束且是 OpsResult
✅ V4-1d 两条线程结束后锁已释放（不会永久锁死）
```

### V4-2 每条失败路径都不许泄漏实例锁（漏一条 = 该实例永久锁死，用户只能重启软件）

```
✅ V4-2 连接失败（SshError 从 session_factory 抛） → 跑完锁已释放  [unreachable]
✅ V4-2 命令抛出异常（exec_stream 抛）             → 跑完锁已释放  [auth_password]
✅ V4-2 sudo 提权要求                              → 跑完锁已释放  [sudo]
✅ V4-2 执行期被取消                               → 跑完锁已释放  [cancelled]
✅ V4-2 退出码非 0（cmd）                          → 跑完锁已释放  [cmd]
✅ V4-2 5 分钟未就绪（not_ready）                  → 跑完锁已释放  [not_ready]
✅ V4-2 busy 路径：第二次抛 OpsError(busy) 后锁仍在第一次手里（正常）
✅ V4-2' busy 路径：第一次结束后锁也释放
```

### V4-3 取消与线程残留

```
✅ V4-3a 轮询中被取消 → err_kind=cancelled
✅ V4-3b cancelled 标记位
✅ V4-3c 取消后锁已释放
✅ V4-3d exec_stream 报告 cancelled → 结果也是 cancelled
✅ V4-3e 取消后该实例立刻可以再被操作（锁没泄漏）
✅ V4-3f 没有残留的自建线程（基线比对：ssh* / tunnel* / ops* 前缀为 0）
```

### V4-4 退出码 0 但永不 ready

```
✅ V4-4a err_kind=not_ready
✅ V4-4b last_lines 恰好尾部 20 行
✅ V4-4c last_lines 是最新的 20 行（丢头留尾）   [('[ComfyUI] starting 5', '[ComfyUI] starting 24')]
✅ V4-4d 退出码 0 不算成功
✅ V4-4e 轮询在上限附近收手（没跑飞）            [0.11s]
```

### V4-5 预探针就绪 —— ⚠ **D-02：`pre_ready` 是死字段**

编排层做对了：

```
✅ V4-5a 结果 pre_ready=True（编排层记了标志）
✅ V4-5b 编排层 ok=True / state=ready
✅ V4-5c RemoteOpsWorker 的 payload 里有 pre_ready 键
```

但**消费方没人看它**：

```
✅ V4-5d 预探针就绪 vs 真正启动成功：UI 文案完全相同（= 未兑现）
   ['✅ 已就绪' == '✅ 已就绪']
✅ V4-5e 预探针就绪时 UI 仍说「✅ 已就绪」
✅ V4-5f toast 文案也没看 pre_ready（UI 层 0 处引用 pre_ready）
✅ V4-5g 对话框完成态也只看 ok
```

- `comfyui_page._ops_result_text()`（`comfyui_page.py:2151-2156`）：`if payload.get("ok"): return "✅ 已就绪"` —— 不看 `pre_ready`。
- `comfyui_page._ops_done()`（`:2143-2144`）：`show_toast(..., f"{name} 已就绪")` —— 同上。
- `remote_start_dialog.finish()`（`:91`）：`"✅ 已就绪" if ok else "已结束"` —— 同上。

与 ARCH §12 #3 的承诺（「**不会**对已运行实例谎报「启动成功」，而是记 `pre_ready=True`」）
存在落差：标志记了、**但从没显示给用户**。

**缓解事实（据实说明，避免夸大）**：`remote_ops.start()` 在预探针就绪时会往日志流打一句
「实例当前已经可达（预探针就绪）。仍会执行启动命令，但不会因为退出码 0 就报「启动成功」。」
（`remote_ops.py:336-337`），对话框和控制台都能看到。所以**不是完全无提示**，
但最终状态行 + toast 仍是「已就绪」，用户扫一眼屏幕得到的信息是错的。
定级 **P2**，建议同版修（改 3 处文案即可）：`pre_ready` 为真时状态行/toast 改成
「实例已在运行（本次启动命令已执行）」。

### V4-6 / V4-7 / V4-8

```
✅ V4-6a err_kind=still_reachable
✅ V4-6b 不是 ok（不报停止成功）
✅ V4-6c 停止动作后锁已释放
✅ V4-6d 变成不可达后立刻 ok
✅ V4-7a 实例 dict 被清空后编排不崩（有返回值）
✅ V4-7b 返回的是 OpsResult（不是异常）
✅ V4-7c 锁已释放（不会因为实例没了而锁死）
✅ V4-8a code=None 不被误判成 cmd 失败（继续轮询直到就绪）
```

---

## V5 清理策略边界（真在临时目录里造数据）

**结论：通过。45/45。不会误删。**

造的数据：正常文件 / `.part` 残留 / 10 分钟前刚改的 / 只读文件 / 空子目录 / 多层子目录 / 超大 mtime 跨度。

### V5-1 days 策略

```
✅ V5-1a 删了 3 个（40d/35d/31d）
✅ V5-1b 20 天的留着（未超 30 天）          [['fresh10m.bin','half.part','keep20.bin']]
✅ V5-1c 10 分钟前的留着（60 分钟内保护）
✅ V5-1d 40 天前的 .part 留着（半成品不删）
✅ V5-1e freed 等于删掉的字节数              [300]
```

### V5-2 size 策略（从最旧删到达标）

```
✅ V5-2a 从最旧的开始删（a_oldest 必删）
✅ V5-2c .part 不参与删除
✅ V5-2d 60 分钟内的不参与删除
✅ V5-2f 精确边界：只删最旧的 1 个就达标   [{'deleted': 1, 'freed': 100}]
✅ V5-2g 删的是最旧的那个                   [left=['a2.bin','a3.bin','a4.bin']]
```

### V5-3 只读文件

```
✅ V5-3a 两个正常文件被删                     [{'deleted': 2, 'skipped': 1}]
✅ V5-3b 只读文件计入 skipped
✅ V5-3c 不抛异常、error 为空
✅ V5-3d 只读文件本身还在
```

### V5-4 权限不足 / 读不出来

```
✅ V5-4a 不抛异常
✅ V5-4b error 字段说明「目录不可写」   [目录不可写：C:\...\case_denied]
✅ V5-4c 一个文件都没删（不可写就不动手）
✅ V5-4e scan_dir 遇到 OSError 不抛
✅ V5-4f scan_dir 的 error 字段有内容
   [目录读不出来：C:\...\case_denied（[Errno 13] Permission denied）]
✅ V5-4g 自动清理不因为某目录异常而中断
```

> 说明：V5-4b/c 是**注入 `os.access → False`** 得到的（见下面 D-07 —— Windows 上这条分支
> 真机基本走不到）。V5-4e/f 是**注入 `os.walk` 抛 OSError** 得到的，属真实可达路径。

### V5-5 目录不存在

```
✅ V5-5a exists=False
✅ V5-5b files=0 / bytes=0（界面据此显示「尚无缓存」）
✅ V5-5c error 为空（这不是错误，只是没缓存）
✅ V5-5d stats_rows 里该行 exists=False（不是 0 B 一行）
```

### V5-6 默认关 + 10 分钟节流

```
✅ V5-6a 默认（没开）不跑：ran=False
✅ V5-6c 第一次 schedule_auto 起线程（True）
✅ V5-6d 10 分钟内第二次被节流（False）
✅ V5-6e reset_throttle 后可再跑一次
```

### V5-7 ★清空后 rmdir 空目录：会不会误删用户还有东西的目录？

这是 LEAD 特别点名要评估的一条。`clean_dir()` 在最后会 `os.rmdir(d)`
（`remote_cleanup.py:266-270`，注释写明「目录自己空了也收掉：下次下载会自动重建」）。
**逐条断言：只删「真的空」的目录。**

```
✅ V5-7a 目录里只剩受保护文件时，目录不被删（fresh.bin + half.part 都在）
✅ V5-7b 受保护文件都还在                   [['fresh.bin','half.part']]
✅ V5-7c 有内容的子目录不被 rmdir
✅ V5-7d 子目录里的文件仍在
✅ V5-7e 顶层的旧文件照删
✅ V5-7f 用户刚放进去的文件不会被当缓存删掉  [['README.txt']]
✅ V5-7g 因此该目录也保留
✅ V5-7h 清空后目录本身被收掉（设计如此，避免残留空壳）
✅ V5-7i 收掉的是空目录 —— 没有数据因此丢失（文件是先删完的）
✅ V5-7j clean_all 后清理**根目录**本身仍存在（只收实例子目录）
✅ V5-7k 两个实例子目录都被收掉（都空了）
```

**评估结论：安全。** 依据有三：
1. `os.rmdir` 本身**只对空目录成功**，非空目录抛 `OSError` 被吞掉 → 有内容就删不掉（V5-7a/c/f）；
2. 删的范围是 `<remote_output_dir>/<实例名>`，**根目录本身从不被删**（V5-7j）；
3. 唯一「理论上意外」的行为是：一个**完全空**的用户目录会被收掉。但空目录没有数据可丢，
   且下次取图会 `os.makedirs` 自动重建——这个取舍是合理的。

### V5-8 统计缓存

```
✅ V5-8a 5 分钟内连续三次统计只扫盘一次（走缓存）  [first=1 total=1]
✅ V5-8b force=True 强制重扫                      [2]
```

### 顺带发现

- **D-07（P3）**：`clean_dir` 的「目录不可写」分支在 Windows 上基本不可达。
  实测：给目录加 deny-write ACL（`icacls /deny`）后 `os.access(d, os.W_OK)` 仍返回 `True`：

  ```
  icacls rc= 0
  os.access(W_OK) = True
  scan: {'files': 1, 'bytes': 10, 'exists': True, 'error': ''}
  clean: {'deleted': 1, 'skipped': 0, 'freed': 10, 'error': ''}
  ```

  即 `remote_cleanup.py:242` 的 `if not os.access(d, os.W_OK)` 在真机上几乎恒为假。
  **行为仍然是安全的**（删不掉的文件走 `skipped`，不抛、不弹窗，符合 PRD 边界 #17 的
  「不弹窗」要求），只是「error=目录不可写：<path>」这个诊断文案用户永远看不到。
  改进建议：改成「逐个 `os.remove` 失败 → 收集首次 OSError 作为 error」，或直接用 Win32 ACL 检查。

- **D-11（P3）**：`size` 策略的 `total` 用 `sum(r["bytes"])`（含**所有**文件，包括受保护的
  `.part` 与 60 分钟内的新文件），而候选集把受保护文件排除了。当受保护文件本身超过上限时，
  会出现「把所有可删的都删光、总量仍然超标」且**返回值里没有任何提示**
  （实测：`{'ran': True, 'deleted': 4, 'freed': 400}`，总量仍 > 上限）。
  建议：`auto_clean` 返回值加 `reached_limit: bool`，日志里说一句「还有 N 个文件在保护期内，
  暂不能删」。不影响正确性，只影响可解释性。

---

## V6 i18n 完整性（PRD X2，P0）

**结论：❌ 不通过。22/40（基于 `8594bde`）。D-03：英文模式下远程运维页签系统性残留中文。**

> 首轮（`6841948`）19/27；`8594bde` 补了 15 条词之后重跑为 22/40。
> 断言总数从 27 涨到 40，是因为新增了 §V6-6 专门量「补的这批词到底有几条抵达用户」。

### 为什么工程师的 `tests/i18n_leftover.py` 抓不到

它走的是 `app/i18n._translate_tree()` 控件树扫描，而该函数（`app/i18n.py:123`）只扫：

> QLabel / QAbstractButton / QGroupBox / **QLineEdit** / QTabWidget / QTableWidget 表头 /
> QMainWindow·QDialog 标题 / QListWidget item

于是必然漏两类：
1. **运行期 setText**（失败后才显示的那句）—— 扫描发生在建窗时，之后再 `setText` 的内容
   不会被重扫，除非调用点自己写 `T("...")`；
2. **扫描列表外的控件** —— `QComboBox` 的 item（源码注释明确「暂不扫描」）、
   `QPlainTextEdit` 的 placeholder、非 `QLineEdit` 的 tooltip。

我的断言方式是**实测**：离屏建窗 → 切英文 → 触发真实代码路径 → 读控件上的字，看有没有中文。
比 AST 猜「包没包 T()」可靠。

### 通过的部分

```
✅ V6-1a T() 键共 23 条（新增文案确实走了 T()）
✅ V6-1b 全部 T() 键都能在词表里逐字查到
✅ V6-3 统计中… / 尚无缓存 / 总计 / 张：词表里有 + 调用点走了 T()
✅ V6-3f 英文模式实测 T('尚无缓存') 已翻译   [No cache yet]
✅ V6-3g 英文模式实测 T('总计') 已翻译       [Total]
✅ V6-3e 「张」的英文不是中文原样           [files]
✅ V6-5a 半角省略号不能命中全角省略号
✅ V6-5b 含 emoji 的键整串一致
```

LEAD 特别点名的四类（worker 回来才设置的文案）**全部正确**，工程师这一块改到位了。

### 不通过的部分（D-03）

```
❌ V6-2a 英文模式下 ssh_failure_text 的 9 种文案都不含中文
   {'auth_password': 'SSH 认证失败：用户名或密码不对。',
    'auth_passphrase': 'SSH 认证失败：私钥口令不对。',
    'auth_key': 'SSH 认证失败：远端不接受这把密钥。',
    'key_missing': '读不到密钥文件：`<path>`。检查路径是否还在、有没有被移动，或私钥是不是 PuTT…',
    'fingerprint': '这台机器的 SSH 主机指纹和上次记录的不一样。',
    'sudo': 'SSH 连接正常，但远端命令需要交互式 sudo 密码，ComfyHub 不做交互式提权',
    'timeout': 'SSH 连不上：`1.2.3.4:22` 无响应（连接超时）。',
    'unreachable': 'SSH 连不上：`1.2.3.4:22` 无响应。',
    'unknown': 'SSH 连不上：`1.2.3.4:22` 无响应。'}
❌ V6-2b 英文模式下 lb_ssh_test 失败提示不含中文   [SSH 认证失败：用户名或密码不对。]
❌ V6-2c 英文模式下「测试 SSH」按钮回到英文        [测试 SSH]
❌ V6-2d 英文模式下 SSH 成功提示不含中文          [✅ SSH 连接与认证都正常]
❌ V6-4a QComboBox 的 item 文本在英文模式下无中文
   [('认证方式','密码'), ('认证方式','密钥文件'),
    ('默认通道','用 SSH 启动'), ('默认通道','用云平台 API 启动'),
    ('平台模板','通用（自己填）'), ('平台模板','表单 POST'), ('平台模板','无鉴权 GET')]
❌ V6-4b QPlainTextEdit 的 placeholder 在英文模式下无中文
   [('启动命令','在远端执行的启动命令'), ('停止命令','在远端执行的停止命令'),
    ('请求头','一行一个 Key: Value，例如 Authorization'), ('请求体','JSON，可留空')]
❌ V6-4d 「模板只是骨架…」在词表里
❌ V6-4e 整个远程运维页签内仍显示中文的 QLabel 数 = 0
   [['✅ SSH 连接与认证都正常',
     '模板只是骨架，参数（实例 ID、区域、Token）请按平台 API 文档核对后再保存']]
```

对照组（证明差异来自控件类型而不是我漏看）：

```
✅ V6-4c 对照组：QLineEdit 的 placeholder 已被翻译  [Encrypted on this PC (DPAPI)]
```

> **自纠一处（测试自身问题，已修）**：上一版 V6-2e 为验证「词表里有但调用点没走 `T()`」，
> 手动 `lb_tunnel.setText("隧道功能还没就绪…")` 造了一条中文，结果被后面的 V6-4e 全页扫描
> 当成了**真实残留**数进去（上一版报了 3 条，实际 2 条）。现已改成**源码级取证**
> （断言源码里 `setText("<该句>")` 出现 2 次、`T("<该句>")` 出现 0 次），不再污染。
> 这类「测试自己造数据又把数据当证据」的错误在 i18n 这类扫描型测试里特别隐蔽，记在此备查。

### V6-6 ★复核 `8594bde`：补的 15 条词，只有 2 条真正抵达用户

`8594bde` 只改了一个文件 —— `app/i18n_en.py`（+29 行），**一行 UI 源码都没动**。
它把 `test_theme_i18n.py` 6a 从 56/57 修到 57/57（我已独立复跑确认 **57/57**）。

但 6a 是**静态**断言：AST 扫 `app/ui/**/*.py` 里的中文字面量，看是否都在词表里。
它回答的是「这些字有没有英文」，**不回答**「用户切到英文后眼睛看到的是不是英文」。
两者之间隔着一整层：**调用点有没有用 `T()`、控件类型在不在 `_translate_tree` 的扫描列表里**。

我逐条实测了这 15 条（离屏建窗 → 切英文 → 走真实代码路径 → 读控件上的字）：

| # | 新词条 | 落点 | 词表里有 | **运行时生效** |
|---|---|---|---|---|
| 1 | `请填写对方 ComfyUI 的访问地址，例：…` | `dialogs.py:44` QLineEdit 占位符 | ✅ | ✅ **生效** |
| 2 | 同上 | `dialogs.py:147` QLabel 校验提示 | ✅ | ❌ 运行期 setText |
| 3 | `如 C:\Users\你\.ssh\id_ed25519` | `remote_ops_page.py:144` QLineEdit 占位符 | ✅ | ✅ **生效** |
| 4 | `■ 远程停止` | `comfyui_page.py:1235` 运行期 setText | ✅ | ❌ |
| 5 | `▶ 远程启动`（**词表里根本没有**） | `comfyui_page.py:1234` 运行期 setText | ❌ | ❌ |
| 6 | `正在启动，等待实例就绪（最多 5 分钟）` | `comfyui_page.py:1244` 按钮 tooltip | ✅ | ❌ 不扫按钮 tooltip |
| 7 | `实例已在运行，如需重启请先「远程停止」` | `comfyui_page.py:1246` 按钮 tooltip | ✅ | ❌ 同上 |
| 8 | `在远端执行启动命令，或请求云平台开机` | `comfyui_page.py:1248` 按钮 tooltip | ✅ | ❌ 同上 |
| 9 | `中止本轮等待并执行停止命令（不会杀无关进程）` | `comfyui_page.py:1254` 按钮 tooltip | ✅ | ❌ 同上 |
| 10 | `在远端执行停止命令，或请求云平台关机` | `comfyui_page.py:1256` 按钮 tooltip | ✅ | ❌ 同上 |
| 11 | `在远端执行的启动命令` | `remote_ops_page.py:179` QPlainTextEdit 占位符 | ✅ | ❌ 不扫 QPlainTextEdit |
| 12 | `在远端执行的停止命令` | `remote_ops_page.py:195` 同上 | ✅ | ❌ 同上 |
| 13 | `JSON，可留空` | `remote_ops_page.py:270` 同上 | ✅ | ❌ 同上 |
| 14 | `✅ SSH 连接与认证都正常` | `remote_ops_page.py:521` 运行期 setText | ✅ | ❌ 缺 `T()` |
| 15 | `隧道功能还没就绪：请重启 ComfyHub 后再试` | `remote_ops_page.py:648/657` 运行期 setText | ✅ | ❌ 缺 `T()` |
| 16 | `套用模板会覆盖当前已填的开机/关机请求，继续？` | `remote_ops_page.py:586` QMessageBox 运行期 | ✅ | ❌ |
| 17 | `正在清理…` | `settings_page.py:1121` 运行期 setText | ✅ | ❌ 缺 `T()` |

实测输出（截取）：

```
✅ V6-6a 这 15 条新词条确实都进了合并词表（工程师那一层是对的）
✅ V6-6b 实例对话框「访问地址」占位符已英译   [http://192.168.1.20:8188 or https://xxx.plat]
❌ V6-6c 实例对话框「地址不能为空」校验提示已英译  [请填写对方 ComfyUI 的访问地址，例：http://192.168.1.20:8188]
❌ V6-6d 「■ 远程停止」切语言后不再变回中文       [■ 远程停止]
❌ V6-6e 「远程启动」切语言后不再变回中文         [▶ 远程启动]
❌ V6-6f b_stop 的 tooltip 已英译               [在远端执行停止命令，或请求云平台关机]
❌ V6-6g b_start 的 tooltip 已英译              [在远端执行启动命令，或请求云平台开机]
❌ V6-6h 「在远端执行的启动命令」占位符已英译     [在远端执行的启动命令]
❌ V6-6i 「在远端执行的停止命令」占位符已英译     [在远端执行的停止命令]
❌ V6-6j 「JSON，可留空」占位符已英译            [JSON，可留空]
✅ V6-6k 对照组：QLineEdit 的私钥路径占位符已英译  [e.g. C:\Users\you\.ssh\id_ed25519]
❌ V6-6l 「套用模板会覆盖…」确认框文案已英译      [['套用模板会覆盖当前已填的开机/关机请求，继续？']]
❌ V6-6m 「正在清理…」状态行已英译              [正在清理…]
```

**结论：commit 这 15 条词共 16 个落点，2 个生效、14 个无效。** 而且这 14 个无效落点里，
**每一个的词都老老实实躺在词表里**（`8594bde` 刚补的） —— 补词表对它们一点用都没有。

> 读表说明：表中序号只是为了逐条核对，不是 commit 的行号。
> 第 2 行是第 1 条词在**另一个调用点**的表现（同一句话，QLineEdit 占位符能翻、QLabel 校验提示不能）；
> 第 5 行 `▶ 远程启动` **不是** `8594bde` 新增的词（它压根不在词表里），列在这里是因为它和
> `■ 远程停止` 在同一个函数 `_update_remote_state` 里、属同一类问题 —— 工程师补了后者漏了前者。

### ★关键诊断：D-03 的主体不是「缺词」，是「缺 `T()`」和「扫描盲区」

我把残留逐句拿去查了合并词表，结果如下（`IN` = 词表里有）：

| 残留文案 | 在词表 | 真正的病 |
|---|---|---|
| `测试 SSH` | **IN** | 调用点裸 `setText`（`:517`） |
| `测试中…` / `正在连接…` / `连接失败` | **IN** | 调用点裸 `setText`（`:508/509/526`） |
| `■ 远程停止` / `启动中…` | **IN** | 运行期裸 `setText` |
| `▶ 远程启动` / `● 启动中…` / `● 停止中…` | OUT | 运行期裸 `setText` **且**缺词 |
| `SSH 连接成功：登录与认证都没问题。`（成功弹窗正文，`:523`） | OUT | 裸字面量 **且**缺词 |
| `认证方式`/`密码`/`密钥文件`/`默认通道`/`用 SSH 启动`/`用云平台 API 启动`/`平台模板` | **IN** | `QComboBox` 不扫 |
| `通用（自己填）`/`表单 POST`/`无鉴权 GET` | OUT | `QComboBox` 不扫 **且**缺词 |
| `一行一个 Key: Value，例如 Authorization` | OUT | `QPlainTextEdit` 不扫 **且**缺词 |
| `模板只是骨架，…`（`cloud_hooks.TEMPLATE_TIP`） | OUT | QLabel 是扫的，纯缺词 |
| `实例：` / `地址：`（`remote_banner.py:67/69` f-string） | OUT | 拼句 **且**缺词 |
| 9 种 `ssh_failure_text` 句子 | OUT | 裸字面量 **且**缺词 |

**给工程师的修法优先级（按性价比排序）：**

1. **先给 `_translate_tree` 加三个分支**：`QComboBox`（遍历 `count()`/`itemText`/`setItemText`）、
   `QPlainTextEdit`（`placeholderText`）、`QAbstractButton` 的 `toolTip`。
   这一处改动一次性覆盖 **至少 18 处残留**（组合框 7 + 多行输入框 4 + 按钮 tooltip 7 以上），
   比补 30 条词都管用。
   注意 `cloud_hooks.TEMPLATES` 的 `name` 与 `cb_tpl` 的选择逻辑要确认不靠显示文本做判断
   （当前 `currentData()` 用的是 id，安全）。
2. **再扫一遍运行期 `setText`**：`remote_ops_page.py:508/509/517/521/526`、
   `comfyui_page.py:1234/1235`、`settings_page.py:1121`、`remote_start_dialog.py:91/97`。
   这些词**大部分已经在表里**，只差一个 `T()`，改起来最省事。
3. **最后才补词**：`▶ 远程启动`、`● 启动中…`、`● 停止中…`、`SSH 连接成功…`、
   `一行一个 Key: Value…`、`通用（自己填）`/`表单 POST`/`无鉴权 GET`、`模板只是骨架…`、
   `实例：`/`地址：`、9 种 SSH 失败句。

> 换句话说：**`8594bde` 补的 15 条词，14 条是「即使加了也翻不过来」的**。
> 这不是工程师不努力，是**验证手段选错了** —— 静态词表覆盖率和用户可见语言是两件事。
> 建议把 `test_theme_i18n` 6a 的定位从「i18n 验收」降级为「词表完整性」，
> i18n 验收改用 §V6 这种**运行期读控件**的方式（我的 `tests/test_qa_v0840_i18n.py` 可直接复用）。

### 关于 `test_theme_i18n` 6a「补一批露下一批」的判断（LEAD 问）

**不是测试不稳定，是详情被截断了 —— 属「测试自身问题」，我已修。**

原实现：

```python
check("6a 可扫描文案全部有英文", not _missing, str(_missing[:3]))
```

- 断言本身 `not _missing` 是**确定性**的：`_found` 是按 `dict` 插入序（`pathlib.rglob` 稳定序）建的，
  同一份代码每次跑出的 `_missing` 完全一样；
- 但**详情只打前 3 条**（`[:3]`），所以工程师每次只看到 3 条，补完再跑又看到「新的 3 条」，
  看起来像「轮换」。实际是**排队**，不是**轮换**；
- 这也是为什么一次只补 3~11 条、来回好几轮 —— 不是测试骗人，是**报告信息量被自己砍掉了**。

已把 `[:3]` 改成打印全部（上限 30）+ 总条数，改完复跑 **57/57** 不变（断言逻辑一个字没动，
只改了失败时的详情输出）。改动位置：`tests/test_theme_i18n.py:265`。

**独立复核工程师给的两个数字（`8594bde` 工作区实测）：**

| 指标 | 工程师说 | 我实测 | 一致 |
|---|---|---|---|
| `test_theme_i18n` 6a | 56/57 → 57/57 | **57/57** | ✅ |
| `i18n_leftover` 残留 | 16 → 15 | **残留 15 条** | ✅ |

**那 15 条残留是什么、是否都属既有问题？** —— **不是，有 1 条是 0.8.40 新增的**：

| # | 控件 | 文案 | 归属 |
|---|---|---|---|
| 1 | QLabel | `模板只是骨架，参数（实例 ID、区域、Token）请按平台 API 文档核对后再保存` | ⚠ **0.8.40 新增**（`61b58fc` T05 引入的 `cloud_hooks.TEMPLATE_TIP`），**属 D-03** |
| 2 | QLabel | `Chinese queries auto-map: 写实→realistic · 二次元→anime · 国风→chinese style` | 既有（故意保留中文示例，可接受） |
| 3 | QLabel | `帮我启动实例` | 既有（首页引导） |
| 4 | QLabel | `做一次体检` | 既有（首页引导） |
| 5 | QLabel | `当前生效：https://hf-mirror.com（默认）` | 既有（设置页 f-string 运行期拼句） |
| 6 | QLabel | `模型画像：未知模型 · 上下文约 未知 · …` | 既有（AI 页运行期拼句） |
| 7 | QLabel | `PySide6 轻量 ComfyUI 多实例管理器（46MB 便携包）…` | 既有（关于框，词表里有但对话框是懒建的 → 切语言时还没建出来） |
| 8 | QLabel | `更新源：Gitee（主）→ GitHub（备用）· 运行形态：… · 上次检查：…` | 既有 |
| 9 | QLabel | `📦  模型 models` | 既有 |
| 10-13 | Button | `⚡ 体检` / `⚡ 队列` / `⚡ 工作流` / `⚡ 最近报错` | 既有（emoji + 词，`_tr` 整串查不到） |
| 14 | LineEdit | `搜索本地模型文件名或类别名（实时过滤）` | 既有 |
| 15 | Header | `提示` | 既有（表格表头在数据刷新后重设，扫描时还没设） |

所以：**工程师说「16 → 15」这个数字我确认；但「剩下的都属既有问题」不成立 —— 第 1 条是本版新引入的。**

### i18n 报告口径提醒（给 LEAD 汇总结论用）

`57/57` 与「英文界面仍是半中文」**不矛盾**，因为两者量的不是同一件事：

- `test_theme_i18n` 6a = 「**词表**里有没有这些字的英文」（静态、AST、不建窗）
- `test_qa_v0840_i18n.py` V6 = 「**用户切到英文后看到的字**是不是英文」（运行期、离屏建窗、读控件）

本版要验收的是 PRD X2「切英文后新增页签、按钮、提示无残留中文」—— 这是**后者**。
前者绿了不能推出后者绿，`8594bde` 正好是这个推论的实证反例。

### 逐条归因与修法（首轮清单，行号已按 `8594bde` 核准）

| 现象 | 源码位置 | 修法 |
|---|---|---|
| 9 种 SSH 失败文案全中文 | `remote_ops_page.py:530-568` `ssh_failure_text()` 全裸字面量，且这些句子**都不在 `_EN` 词表** | 按 ARCH §9 拆片段：静态片段包 `T()` 入表，`host/port/user` 用拼接 |
| SSH 成功弹窗正文也是中文 | `remote_ops_page.py:523` `QMessageBox.information(self, "ComfyHub", "SSH 连接成功：登录与认证都没问题。")` | 补词表 + `T()`（`8594bde` 漏了这句，`V6-6` 表里没有） |
| 「测试 SSH」按钮被设回中文 | `remote_ops_page.py:517` `setText("测试 SSH")` | `setText(T("测试 SSH"))`（`测试 SSH` 已在词表，缺的只是 `T()`） |
| 「✅ SSH 连接与认证都正常」 | `remote_ops_page.py:521` | `setText(T(...))`（`8594bde` 已补词表，仍缺 `T()`） |
| 「正在连接…」/「测试中…」/「连接失败」 | `:509` `:508` `:526` | **词表三条全有**，只缺 `T()`（首轮我写成「需补词表」，此处更正） |
| 「▶ 远程启动」/「● 启动中…」/「● 停止中…」 | `comfyui_page.py:1234`、`:1262-1264` | 补词表 + `T()`（`8594bde` 补了 `■ 远程停止`，漏了这三条） |
| 「隧道功能还没就绪…」 | `:648` `:657` | `8594bde` **已补词表**，仍缺 `T()` |
| 「套用模板会覆盖…继续？」 | `:586` | `8594bde` **已补词表**，仍缺 `T()` |
| 「正在清理…」 | `settings_page.py:1121` | `8594bde` **已补词表**，仍缺 `T()` |
| QComboBox item 永远中文 | `remote_ops_page.py:129-131, 220-221, 236-238`（数据在 `cloud_hooks.TEMPLATES` 的 `name`） | 需要 `_translate_tree` 支持 QComboBox（用 `itemData` 存稳定值后可以安全扫描），或改用 `QLabel`+单选；`TEMPLATES` 的 `name` 也需入表 |
| QPlainTextEdit placeholder 永远中文 | `:179, 195, 265, 270` | 同上：要么给 `_translate_tree` 加 QPlainTextEdit 分支，要么调用点自己 `setPlaceholderText(T(...))` |
| 「模板只是骨架…」 | `cloud_hooks.TEMPLATE_TIP`，由 `_tip()` 建成 QLabel（是扫的），但串**不在词表** | 补词表即可 |
| `remote_banner.py:49/54` tooltip | `b_ack/b_close` 的 tooltip 是 QPushButton，扫描只处理 QLineEdit 的 tooltip | 补 `_translate_tree` 的 tooltip 分支，或调用点 `T()` |
| `remote_banner.py:67/69` `实例：`/`地址：` | f-string 拼句（ARCH 规则要求拆片段） | 拆成 `T("实例：") + name` |
| `remote_start_dialog.py:91/97` `已结束`/`正在停止等待…` | 运行期 setText 裸字面量 | `T()` + 补词表 |

**为什么判 P1 阻断**：PRD X2 是 **P0** 级验收项，且验收标准原文点名「切英文后新增页签、
按钮、提示无残留中文；动态文案用 `T()`」。当前远程运维页签在英文模式下**一半是中文**
（SSH 分区几乎全中），这不只是个别漏词，是整块功能在英文界面下不可用。

`8594bde` 之后这个判断**不变**：补的是词表，而病在调用点和扫描器。见 §V6-6 的 2/15。

**复现命令**：

```bash
.venv_build/Scripts/python.exe -u tests/test_qa_v0840_i18n.py      # 我的运行期实测
.venv_build/Scripts/python.exe tests/test_theme_i18n.py            # 工程师的静态检查（57/57）
.venv_build/Scripts/python.exe tests/i18n_leftover.py              # 残留导出（15 条）
```

---

## V7 线程规则与分层约束

**结论：通过。36/36。**

```
== V7-1 纯逻辑模块不 import PySide6 ==
  ✅ remote_ssh / remote_fingerprint / tunnel / remote_ops / remote_registry /
     cloud_hooks / remote_cleanup   （7/7 全部不 import Qt）
== V7-2 纯逻辑模块不 import proc_manager ==   （7/7 全部不 import）
== V7-2' tunnel / remote_ssh / remote_registry 也不 import remote_ops ==  （3/3）
```

UI 层同步网络调用清单（AST 逐函数扫）：

```
== V7-3 comfyui_page.py 的同步网络调用清单
   [('_ensure_engine_list','probe(',1068), ('_start_dml_probe','probe(',1093),
     ('_test_remote','probe_reason',1417), ('_probe','probe(',1101)]
✅ V7-3a 全仓 UI 层同步 HTTP 只剩 _test_remote 一处（0.8.39 既有）
✅ V7-3b 新增的「发开机/关机请求」只做本地预检 + emit 信号，不发 HTTP
```

- `remote_ops_page._send_hook()`（`:595-606`）只做 `cloud_hooks.precheck()`（纯本地校验）
  然后 `self.sig_hook_request.emit(...)` → 真正的 HTTP 在 worker 里。**做对了。**
- 唯一残留：`comfyui_page._test_remote()`（`:1417-1432`）在 UI 线程同步
  `probe_reason(inst, timeout=8, saved=True)`，最长冻界面 8 秒。这是 **0.8.39 就有的**
  （本版只是加了 `saved=True` 关键字），不属本版回归，但顺手记一笔（P3，建议后续挪进 worker）。

隧道/SSH 连接是否在 UI 线程：

```
✅ V7-4a TunnelJob.start() 立刻返回（连接在后台线程）   [2ms]
✅ V7-4b 连接跑在名为 tunnel-<id> 的后台线程里          [['tunnel-layer1']]
✅ V7-4c 后台线程最终把状态推到 CONNECTED              [connected]
✅ V7-4d SshTestWorker 是 QThread  [['SshTestWorker','QThread','QObject','Object']]
✅ V7-4e RemoteOpsWorker 是 QThread
✅ V7-4f 「测试 SSH」走 SshTestWorker 而不是直接 session.connect
```

清理统计是否在 UI 线程：

```
✅ V7-5a 设置页用 CleanupStatsWorker / ensure_scan_async 而不是直接 scan_dir
✅ V7-5b CleanupStatsWorker 是 QThread
✅ V7-5c CleanupWorker 是 QThread
✅ V7-5d 设置页没有在 UI 线程调 stats_rows(force=True)
✅ V7-5e remote_cleanup 自身不 import Qt
```

---

## V8 全量回归

**结论：指定 15 个用例全绿；`compileall` 干净；测试隔离性已修好。**

### V8-1 指定 15 个用例

```
✅ test_api_base           rc=0  结果：27/27 通过
✅ test_remote_config_v084 rc=0  （全绿）
✅ test_remote_ssh         rc=0  结果：50/50 通过
✅ test_remote_ops         rc=0  结果：47/47 通过
✅ test_tunnel             rc=0  结果：65/65 通过
✅ test_cloud_hooks        rc=0  通过 53/53  OK 全部通过
✅ test_remote_cleanup     rc=0  通过 47/47  OK 全部通过
✅ test_remote_ui          rc=0  OK 全部通过
✅ test_ai_conn            rc=0  AI 接入回归：23/23 通过
✅ test_task_notify        rc=0  结果：7/7 通过
✅ test_batch_runner       rc=0  结果：6/6 通过
✅ test_secret_store       rc=0  共 19 项，全部通过
✅ test_plugin_deps        rc=0  PASS 55 / FAIL 0
✅ test_close_behavior     rc=0  关闭行为回归：30/30 通过
✅ test_styled_bg          rc=0  通过 11 / 失败 0
```

### V8-2 编译

```
✅ V8-2a compileall 返回 0
✅ V8-2b compileall 无输出（无语法错/无警告）
```

### V8-3 ★测试隔离性实测（LEAD 特别要求复核）

```
✅ V8-3a test_remote_ui 本身跑通  [rc=0 OK 全部通过]
✅ V8-3b 跑完后真实 remote 目录**没有新增任何文件**
    实测路径：C:\Users\10696\AppData\Local\ComfyHub\remote（跑前 0 个文件，跑后 0 个）
✅ V8-3c 该目录要么不存在、要么为空（当前 0 个文件）
```

**复核结论：`os.environ["LOCALAPPDATA"] = _TMP` 的补丁有效，不再污染真实用户目录。**
（补充：该目录本身是存在的，只是空的。它可能是修好之前遗留的空壳；因为
`remote_output_dir()` 只算路径不建目录、`scan_dir()` 不建目录，所以**本次运行没有创建它**。）

### V8-4 已知基线失败复核

| 用例 | 实测 | 与 LEAD 给的基线是否一致 |
|---|---|---|
| `test_instance_mount_ui` | `rc=1` / `AttributeError: 'FakeProcs' object has no attribute 'is_own'` | ✅ 一致 |
| `test_save_diff` | `rc=1` / 同上 | ✅ 一致 |
| `test_lang_switch` | 挂住直到 45s 超时（模态阻塞） | ✅ 一致 |
| `test_settings_first_save` | 输出 `ALL PASS`，退出码 `3221225477`（0xC0000005） | ✅ 一致（断言通过、退出时崩溃） |
| `test_ui_state` | `rc=1` / 差的正是「空的发布者父键也删了」1 项 | ✅ 一致 |
| `test_open_web` | `rc=1` / `AssertionError: 测试机需至少一个实例配置` | ✅ 一致 |

**这 6 项与基线完全对得上，不构成本版新缺陷。**

### 顺带发现：D-09（P2）离屏 Qt 退出偶发 `0xC0000005`

同一份代码重复跑，退出码不稳定（断言在崩溃前已全部通过）：

```
test_plugin_deps           4 次退出码 = [0, 0, 0, 0]        非零次数=0
test_close_behavior        4 次退出码 = [0, 3221225477, 0, 0]  非零次数=1   ← 偶发
test_remote_ui             4 次退出码 = [0, 0, 0, 0]        非零次数=0
test_ai_conn               4 次退出码 = [0, 0, 0, 0]        非零次数=0
test_styled_bg             4 次退出码 = [0, 0, 0, 0]        非零次数=0
test_settings_first_save   4 次退出码 = [3221225477 × 4]     非零次数=4   ← 必现
```

同一现象在两个不同用例里出现过（V8 连跑两次，一次报 `test_plugin_deps`、一次报
`test_close_behavior`），但两者的断言输出都是「全部通过」。

**影响**：CI 若用退出码判通过，会出现「明明全绿却红」的假警报（或反之被人忽略真崩溃）。
这是**测试进程 teardown 的问题**，不是打包版功能问题。
**建议**：① 测试脚本末尾用 `os._exit(0)` 绕过解释器 teardown；
② 或在 V8 汇总里以「输出含『全部通过 / PASS n / FAIL 0』」为主判据、退出码为辅。

---

## 9. 本次改动清单（仅测试与报告，未动业务源码）

```
 M  .gitignore                              ← 补 tests/*.py 入库例外（D-04）
 M  tests/test_theme_i18n.py                ← 6a 失败详情 [:3] → 全量（§V6-6，只看不改断言）
 M  tests/test_settings_first_save.py       ← D-09 收尾（§12）
 M  tests/test_close_behavior.py            ← D-09 收尾
 M  tests/test_plugin_deps.py               ← D-09 收尾
 M  tests/test_remote_ui.py                 ← D-09 收尾
 M  tests/test_lang_switch.py               ← D-13 救活：确认框打桩 + APPDATA 隔离 + D-09 收尾
 M  tests/test_qa_v0840_creds.py            ← V2；终验时 L6c 改正向（§13.5）
 M  tests/test_qa_v0840_tunnel.py           ← V3；终验时 V3-6-0 改正向（§13.5）
 M  tests/test_qa_v0840_state.py            ← V4；终验时 V4-5d/5e/5f 改正向（§13.5）
 M  tests/test_qa_v0840_i18n.py             ← V6；终验时 V6-2e' 改正向（§13.5）
 M  tests/test_qa_v0840_regress.py          ← V8；用例 15 → 19，基线清单移除已修项
 M  tests/test_qa_v0840_entry.py            ← 入口/全链路；打桩改用 _pick_add_action（§13.4①）
 ?? tests/_qa_exit.py                       ← D-09 统一收尾模块（含根因对照数据）
 ?? tests/test_qa_v0840_i18n_save.py        ← 下拉翻译不许写坏配置（§13.3，终验新增）
 ?? tests/test_qa_v0840_fields.py           ← V1
 ?? tests/test_qa_v0840_cleanup.py          ← V5
 ?? tests/test_qa_v0840_layers.py           ← V7
 ?? docs/QA_v0.8.40_report.md               ← 本报告
```

`.gitignore` 增补内容：

```gitignore
# 0.8.40 远程运维（第二批）——之前漏了例外，53 条断言一直没入库
!tests/test_cloud_hooks.py
!tests/test_remote_cleanup.py
!tests/test_remote_ui.py
# QA 独立验证（v0.8.40：V1-V8）
!tests/test_qa_v0840_*.py
```

> **D-04 说明**：`tests/test_cloud_hooks.py`（**53 条断言，全绿**）此前被 `.gitignore:22`
> 的 `test_*.py` 吞掉，`git ls-files` 里查不到 —— 也就是说云端钩子这块的回归覆盖
> **只存在于工程师的本机磁盘上**，一旦别人 clone 或 CI 重新检出就消失。
> 我按 LEAD 指示补了例外（只改 `.gitignore`，属测试基建，不触及业务逻辑）。
> 请工程师确认后一并提交，否则这 53 条断言仍不会入库。

**未做任何业务源码修改**（发现的缺陷全部只报告，见 §10 路由）。
对 `tests/test_theme_i18n.py` 的唯一改动是**失败详情输出**（`[:3]` → 全量 + 总条数），
断言表达式 `not _missing` 一个字没动，改前改后都是 57/57。

---

## 10. 放行建议

### 结论（终验 `a440f8c`）：**可以发布**

> 本节是「首轮」的判定与依据，**保留原文不改**，方便回看当时的取舍。
> 终验时所有阻断项已修完并通过复验，**放行结论已从「有条件发布」升级为「可以发布」**，
> 逐项证据见 §13。下面是当时的判定过程。

### 首轮结论：**有条件发布 —— 必须修 2 条，需提交 1 条**

> **`8594bde` 之后的更新**：D-10（i18n_en.py 未提交）已由该 commit 解决；
> 但 **D-03 仍不通过** —— 该 commit 补的 15 条词只有 2 条抵达用户（§V6-6）。
> 放行结论**维持「有条件发布」**，必须修项仍是 D-01 + D-03。

#### 必须修（阻断发布）—— 两条均已修并复验通过

| 编号 | 内容 | 修复量 | 复验结果 |
|---|---|---|---|
| **D-01** | 保存对比框把 SSH 密码/密钥口令**旧值原文**显示为明文（`comfyui_page.py:1531-1544`） | 小（新旧两侧同时脱敏） | ✅ V2 **43/43** |
| **D-03** | 英文模式下远程运维页签系统性残留中文（违反 PRD X2/P0）。工程师**否决了「全局翻 QComboBox」的做法**（会写坏 `vram_mode` 等 `currentText()` 落盘的配置），改为逐控件登记 + 运行期 `T()` + `ssh_failure_text` 模板化 | 中等 | ✅ V6 **40/40**；新增配置安全用例 10/10 |
| **D-12** | （复核时新增）`＋ 添加 ComfyUI` 单击直连向导，**没有入口能建远程实例** | 小（拆 `_open_add_menu` + `_pick_add_action`） | ✅ 入口/全链路 **21/21** |

#### 需要提交（不是代码问题）

| 编号 | 内容 | 状态 |
|---|---|---|
| **D-04** | `.gitignore` 例外已由我补好；`tests/test_cloud_hooks.py` 等 3 个文件需 `git add` | ✅ 已提交（`600f828`） |
| ~~D-10~~ | `app/i18n_en.py` 未提交改动 | ✅ `8594bde` 已解决 |

#### 强烈建议同版修（不阻断，但用户可见）—— 已修

| 编号 | 内容 | 状态 |
|---|---|---|
| **D-02** | `pre_ready` 死字段：实例本来就在跑，UI 仍报「✅ 已就绪」+ toast「已就绪」 | ✅ `9c53ca8` 已修，UI 现说「实例本来就在运行（未计入本次启动）」 |

#### 可下个补丁（记录在案）

| 编号 | 内容 | 状态 |
|---|---|---|
| D-05 | 云平台钩子 `headers` 的 Token 明文落盘且无提示 | ✅ `9d9a28d` 已修（加了诚实提示，未改加密） |
| D-06 | `registry or 全局单例` 的 DI 脚枪（空注册表 falsy） | ✅ `a440f8c` 已修 |
| D-07 | `clean_dir` 的「目录不可写」分支在 Windows 上基本不可达（行为仍安全，只是诊断文案出不来） | ⏳ 后续 |
| D-08 | 未改动凭据也刷一条「凭证（空）→（已更新，不回显）」假改动（0.8.39 既有） | ⏳ 后续 |
| D-09 | 离屏 Qt 退出偶发 `0xC0000005`，按退出码判 CI 不可靠 | ✅ QA 已修（§12） |
| D-11 | `size` 策略 `total` 含受保护文件，可能删光仍超标且无提示 | ⏳ 后续 |
| — | `_test_remote` 在 UI 线程同步 `probe_reason(timeout=8)`，最长冻界面 8s（0.8.39 既有） | ⏳ 后续 |

### 当时为什么不是「可以发布」

因为 **D-01 与 D-03 都是「用户直接看见」的问题**，且都在本版新增的代码路径上：
- D-01：改密码这个动作本身就会把旧密码打在屏幕上（工程师自己在注释里写明这是要避免的）；
- D-03：英文用户在远程运维页签里几乎无法使用 SSH 分区。

按 LEAD 的指示「凭据泄漏…宁可判不可发布」，D-01 已经触线。但它止于**屏幕**
（不出盘、不进日志、不进网络，DPAPI 完好），且修复是 1 行 —— 所以给「有条件」
而不是「不可发布」。

**现在这两条都已修完并复验通过**，加上 D-12 入口缺口补齐，所以终验给「可以发布」。

### 为什么不是「不可发布」

三块「致命」面里的另外两块都验干净了：

| 检查面 | 结论 | 证据强度 |
|---|---|---|
| 隧道假活 | **不存在**。FAILED 后本地端口可被另一 socket 重新 bind（不看 `is_active()` 自述） | V3-2d / V3-3g，42/42 |
| 字段丢失 | **不存在**。18 字段五处齐 + 双向往返 + 落盘往返 | V1，126/126 |
| 凭据泄漏 | **存在，但止于屏幕**（对比框旧值列）；落盘/日志/异常/结构化导出全部干净 | V2 L2/L3/L5 |

---

## 11. 复核记录（`8594bde`）

**为什么复核**：我首轮验的是 `6841948`；期间工程师又提交了 `8594bde`
（「0.8.40 补词表：T05 遗漏的 11 条远程文案」），只改 `app/i18n_en.py`（+29 行 / 15 条词）。
跨提交的测试结果不算数，因此按依赖关系重跑。

**重跑范围（按 LEAD 要求「只重跑受影响部分」）**：

| 项 | 是否重跑 | 理由 | 结果 |
|---|---|---|---|
| V1 字段一致性 | 否 | 只涉及 `config.py` 与 `_TRACK`，`8594bde` 未动 | 126/126（沿用） |
| V2 凭据泄漏 | 否 | 文案改动不影响 DPAPI / diff / 日志路径 | 40/43（沿用） |
| V3 隧道假活 | 否 | 纯 socket/paramiko 层，与文案无关 | 42/42（沿用） |
| V4 状态机并发 | 否 | 同上 | 38/38（沿用） |
| V5 清理边界 | 否 | 纯文件系统层 | 45/45（沿用） |
| **V6 i18n** | **是（整段重做）** | 该 commit 就是改词表 + 文案 | **22/40** |
| V7 分层约束 | 否 | import 关系未变（`compileall` 已复验） | 36/36（沿用） |
| **V8 全量回归** | **是** | 必须确认其他用例没被新 commit 影响 | **25/26** |

**V8 重跑明细（`8594bde`）**：15 个用例中 14 个 rc=0 且断言全绿；
唯一 ❌ 仍是 `test_plugin_deps rc=3221225477`（输出 `PASS 55 / FAIL 0`）—— 即 D-09 的
退出期 `0xC0000005` 偶发崩溃，与内容无关。`compileall -q app` 返回 0 且无输出。
测试隔离性复验：跑完 `test_remote_ui` 后真实
`C:\Users\10696\AppData\Local\ComfyHub\remote` 仍是 **0 个文件**（跑前 0 / 跑后 0）。

**独立复核的两个数字**：

| 指标 | 工程师说 | 我实测 | 结论 |
|---|---|---|---|
| `test_theme_i18n` 6a | 56/57 → 57/57 | **57/57** | 数字对，但它量的是**词表覆盖率**，不是界面语言 |
| `i18n_leftover` 残留 | 16 → 15 | **15** | 数字对，但**不全是既有问题** —— 第 1 条 `模板只是骨架…` 是本版 T05 新引入 |

**结论**：`8594bde` 修好了它自己声称的东西（词表完整性），**没有修好 D-03**
（英文界面残留），且新增了一条 D-03 内部证据：commit 补的 15 条词共 16 个落点，**14 个「补了也不会生效」**。

---

## 12. D-09 修复记录：让退出码可信（仅测试侧，2026-11-24）

### 12.1 修复前的实测复发率

| 用例 | `rc=0` 命中 | 说明 |
|---|---|---|
| `test_settings_first_save` | **0/10** | 每次 `ALL PASS` 打印完就崩 |
| `test_close_behavior` | 6/10 | 随机 |
| `test_plugin_deps` | 9/10 | 随机 |
| `test_remote_ui` | 偶发 | `os._exit(0)` 之后 |

Git Bash 里看到的是 `139`（`Segmentation fault`）或偶发 `127`；换成 Windows 原生口径就是 `0xC0000005`。

### 12.2 根因（对照组实测，不是猜的）

`os._exit()` → CRT `_exit()` → `ExitProcess()`，而 **`ExitProcess` 会依次调用各已加载 DLL 的
`PROCESS_DETACH`**。Qt 的静态析构在这时去访问一个 Python 解释器已经开始回收的
**QApplication 实例** → 访问冲突。

`SettingsPage` 构造场景，各 30 次（`rc=0` 命中率）：

```
什么都不做                        1/30      ← 基线本来就是坏的
app.quit()                        0/30      ← 反而更糟：只退事件循环，对象还在
sleep 0.1s / 1s / 5s              0/12      ← 不是「等一会儿就好」，排除「退出太快」
page.close()                      2/30
删掉所有 top-level 控件            5/30
shiboken6.delete(QApplication)    30/30     ← 对简单控件树有效
```

**关键排除项是 `sleep 0.1/1/5s` 全 0/12** —— 证明这不是竞态或时序问题，
而是**对象生命周期**问题（QApplication 活到了 DLL 卸载阶段）。

### 12.3 但「优雅销毁 QApplication」并不普遍安全

把 `shiboken6.delete(QApplication)` 用到 `test_close_behavior` 上，崩溃点**前移到了 `delete()` 内部**
（`QA_EXIT_DEBUG=1` 的分步日志最后一条停在 `delete QApplication`）：

- 单个 `MainWindow` / 单个 `SettingsPage`：销毁 app 后 10~30 次全干净；
- 同进程里存在**多个** `MainWindow`（该用例建了好几个）时：销毁过程中 Qt 会回调 Python 侧已失效的
  槽/定时器 → 访问冲突。

这是 PySide6 在解释器收尾阶段的自身限制：段错误一旦发生，当前进程就没了，脚本没法 catch 兜住。
**所以「在测试侧优雅销毁」这条路走不通**（能做到 30/30 的场景，换个用例又会崩）。

### 12.4 采用的方案

`kernel32!TerminateProcess` 与 `ExitProcess` 的唯一实质区别就是**不跑 DllMain detach**——
进程直接结束，Qt 的静态析构根本没机会执行，访问冲突也就不存在。
退出码是**精确传入**的：`code=1 → 1`、`code=2 → 2`、`code=3 → 3`（实测）。

实测（3 个 `MainWindow` 同时存在）：`TerminateProcess` **20/20** 干净。

### 12.5 改了什么

新增 `tests/_qa_exit.py`（统一收尾，含根因与对照数据的完整说明），把 8 个脚本的收尾接上去：

| 文件 | 原收尾 | 改为 |
|---|---|---|
| `tests/test_settings_first_save.py` | `os._exit(1/0)` ×2 | `clean_exit(1/0)` |
| `tests/test_close_behavior.py` | `sys.exit(1 if fails else 0)` | `clean_exit(...)` |
| `tests/test_plugin_deps.py` | `sys.exit(main())` | `clean_exit(main())` |
| `tests/test_remote_ui.py` | `os._exit(0)` | `clean_exit(0)` |
| `tests/test_qa_v0840_fields.py` | `sys.exit(...)` | `clean_exit(...)` |
| `tests/test_qa_v0840_state.py` | `sys.exit(...)` | `clean_exit(...)` |
| `tests/test_qa_v0840_creds.py` | `sys.exit(...)` | `clean_exit(...)` |
| `tests/test_qa_v0840_i18n.py` | `sys.exit(...)` | `clean_exit(...)` |

只有 `creds`/`i18n` 两个 QA 用例会建 `QApplication`，其余 4 个（`cleanup`/`layers`/`regress`/`tunnel`）
不建 Qt，退出码本来就稳，未改。

`tests/_qa_exit.py` 还留了两个排障开关：
`QA_EXIT_GRACEFUL=1` 走优雅销毁（想看收尾期问题用）、`QA_EXIT_DEBUG=1` 打分步日志。

### 12.6 修复后的实测

| 用例 | 改前 | 改后（各 10 次） | 期望码 |
|---|---|---|---|
| `test_settings_first_save` | 0/10 | **10/10 `rc=0`** | 0（30/30 断言通过） |
| `test_close_behavior` | 6/10 | **10/10 `rc=0`** | 0（30/30 通过） |
| `test_plugin_deps` | 9/10 | **10/10 `rc=0`** | 0（PASS 55 / FAIL 0） |
| `test_remote_ui` | 偶发 | **10/10 `rc=0`** | 0 |
| `test_qa_v0840_fields` | — | **6/6 `rc=0`** | 0（126/126） |
| `test_qa_v0840_state` | — | **6/6 `rc=0`** | 0（38/38） |
| `test_qa_v0840_creds` | — | **6/6 `rc=1`** | 1（40/43，D-01 未修） |
| `test_qa_v0840_i18n` | — | **6/6 `rc=1`** | 1（有失败项） |

**最后两行是关键**：非零码**没有被洗成 0**。断言失败时退出码照样是 1，
只有「进程退出期的崩溃」被绕过，所以退出码从「随机不可信」变成「可信」。

### 12.7 影响面（说清楚没掩盖什么）

- **不掩盖断言失败**：`clean_exit(code)` 的 `code` 由断言结果算出并原样传出（上表后两行即证）。
- **不掩盖结果输出**：调用前 `flush` 过 stdout/stderr，汇总行写在退出之前。
- **唯一被绕过的是「进程退出期崩溃」**，而那正是 D-09 本身——它是 Qt/PySide6 的收尾问题，
  与本版的远程运维功能无关，也不影响包体行为。
- **未改任何业务源码**，改动全部落在 `tests/`。

---

## 13. 增量复核记录（终验 `a440f8c`）

> 本节只记「修完之后」的复验，不重写前文。前文的结论保留原样，作为「改前」对照。

### 13.1 逐项「改前 → 改后」

| 项 | 改前 | 改后 | 复验命令 | 判 |
|---|---|---|---|---|
| **D-01** 对比框旧口令明文 | V2 40/43 | **43/43** | `test_qa_v0840_creds.py` | ✅ |
| **D-03** i18n 残留 | V6 22/40 | **40/40** | `test_qa_v0840_i18n.py` | ✅ |
| **D-12** 入口缺口 | 14/19（A1–A5 红） | **21/21** | `test_qa_v0840_entry.py` | ✅ |
| **D-02** `pre_ready` 死字段 | V4 38/38（但 3 条断言是在**记录缺陷**） | **38/38**，断言已改正向 | `test_qa_v0840_state.py` | ✅ |
| **D-06** 空注册表被 `or` 替换 | V3 42/42（同样有 1 条记录缺陷） | **42/42**，断言已改正向 | `test_qa_v0840_tunnel.py` | ✅ |
| **D-05** Token 明文无提示 | L6c 判「无提示」 | 有提示，L6c 改正向 | `test_qa_v0840_creds.py` | ✅ |
| **D-09** 退出码不可信 | 4 个用例 0/10~9/10 | **10/10 退出码正确** | 见 §12 | ✅ |
| **D-13** `test_lang_switch` 挂死 | 永久超时（rc=124） | **10/10 通过** | `test_lang_switch.py` | ✅ |
| 全量回归 | 25/26 | **28/28**（19 用例 + 编译 + 隔离 + 基线对账） | `test_qa_v0840_regress.py` | ✅ |

**V1/V5/V7 未受影响**（126/126、45/45、36/36，改动不触及字段/清理/分层），终验时已复跑确认。

### 13.2 D-01 的修法我核过源码，不是「碰巧过」

`comfyui_page._save()` 现在是**新旧两侧都遮**：

```python
_old_disp = dict(old)
for _k in ("auth_value", "auth_pass", "ssh_password", "ssh_key_passphrase"):
    _o, _n = old.get(_k) or "", _disp.get(_k) or ""
    if _o and _n != _o:
        _old_disp[_k] = "（已设置，不回显）"      # 旧值也遮
    if _n and _n != _o:
        _disp[_k] = "（已更新，不回显）"
changes = diff_changes(_old_disp, _disp, ...)      # 两侧都传脱敏后的
```

工程师没用 `secret_keys=` 参数，而是直接遮值 —— **功能等价，且更直白**（`diff_changes` 的 `secret_keys` 只输出「已设置/已修改」，这里是「已设置，不回显/已更新，不回显」）。
关键点：**旧值那一侧确实被遮了**，不是只改 `_disp`。

### 13.3 D-03 的修法：工程师否掉了我的建议，我认为他改得对

我在 §V6-6 建议「给 `_translate_tree` 加 QComboBox 分支」。工程师**没有照做**，理由是：

> `app/i18n.py` 模块 docstring 原本就记着「QComboBox 暂不扫描：页面逻辑里有按
> `currentText()` 取值的写法（如镜像源下拉），翻译会改掉存储值」。

我复核了他的判断，**成立且比我原建议更对**：全项目 `vram_mode` / `cross_attn` /
`preview_method` / `precision` / `cuda_malloc` / `mirror` / `ai_model` / `deps_mode`
都是 `currentText()` 直接落盘。全局翻译一个下拉，用户切一次英文再点保存，
`vram_mode` 就从「自动」变成「Auto」写进配置 —— **P0 级配置损坏，比 D-03 严重得多**。

他改成**逐控件显式登记** `setProperty("i18n_items", True)`，只翻「item 存 itemData、
取值走 `currentData()`」的下拉（远程页 4 个 + 实例对话框 `cb_kind`）。

**这个新风险面我单独写了用例验**（`test_qa_v0840_i18n_save.py`，10/10）：

| 断言 | 内容 | 结果 |
|---|---|---|
| A1/A2 | 确实有登记的下拉（否则 D-03 是假绿），且它们英文模式下真的翻了 | ✅ 登记 5 个 / 未登记 24 个 |
| **A3★** | **未登记的下拉，切英文前后选项一字不差** | ✅ 24 个全部不变 |
| **B1★** | 英文模式下保存，5 个 `currentText()` 落盘的枚举值一个都没变 | ✅ |
| **B3★** | 设置页 `mirror`（中文枚举、`currentText()` 落盘）保存后仍是「阿里云」 | ✅ |
| C1/C2 | EN→ZH→EN 来回切换可逆、不漂移 | ✅ |
| C3 | 没有「该翻却没翻」的登记下拉 | ✅ |

**结论：这个 P0 风险面已经被堵住了**，不是靠「没测出来」。

### 13.4 我对工程师诊断的独立复现（不采信结论，只采信数据）

**① `QMenu.exec` 类级打桩无效 —— 他说对了，我复现了：**

```
QMenu.__dict__['exec'] is fake : True     # 赋值确实写进类字典
type(m).exec           is fake : True     # Python 层查得到
m.exec                 is fake : False    # 实例取到的仍是 C++ 槽
m.exec()                       : None     # 走的是真模态
```

Shiboken 解析槽函数时先查 C++ 方法表，类级 Python 属性被忽略。
**只有 Python 子类能这么打桩**（`InstanceDialog` / `SetupWizard` 可以，实测假函数确实被调用）。

这解释了一个很隐蔽的事实：我那版用例在**改前**（没有菜单）是安全的，
一旦工程师把菜单做出来，它就会**卡死在真模态上**（无头环境没人点）——
**卡死比失败更糟**，因为看起来像 CI 卡住而不是用例红了。已在 §13.5 记录补救。

**② `test_lang_switch` 挂死是既有问题 —— 他说对了，我用独立 worktree 复现了：**

```bash
git worktree add --detach /tmp/ch_base c73f245~1     # 0.8.39 e29d77c，0.8.40 之前
cd /tmp/ch_base && python tests/test_lang_switch.py  # rc=124（超时挂死）
cd F:/ComfyHub  && python tests/test_lang_switch.py  # rc=124（同样挂死）
```

两个提交上同样挂死 → **与 0.8.40 无关**。根因：该用例写于 2026-11-02，
而 2026-11-06 给保存加了「本次将保存 N 处修改」确认框；切语言本身是一处改动 → 弹真模态 → 无头无人点。

### 13.5 我自己犯的错（4 处同类，全部改正向）

这是本轮最值得记的一条**方法论教训**：我写的断言里有 4 处把「**缺陷现状**」当成了「**期望行为**」。
工程师一修好，它们就变红 —— 红的不是代码，是我的断言。

| # | 断言原文（错） | 为什么会红 | 改成 |
|---|---|---|---|
| 1 | `V6-2e'` 要求「裸 `setText` 两处、`T()` 零处」 | 补了 `T()` 就红 | `T()` ≥1 且裸 `setText` == 0 |
| 2 | `V3-6-0` 要求「注入的空注册表被换成全局单例」 | D-06 修了就不换了 → 红 | 断言注入的注册表被尊重 |
| 3 | `V4-5d/5e/5f` 要求「UI 文案**完全相同**」「UI 层 0 处引用 `pre_ready`」 | D-02 修了就区分了 → 红 | 断言文案不同、不许谎报「已就绪」、必须引用 `pre_ready` |
| 4 | `L6c` 要求「**没有** Token 明文落盘提示」 | D-05 加了提示 → 红 | 断言提示存在 |

**教训**：写「证伪型」断言时，必须**断言期望行为**，而不是**断言当前缺陷还在**。
前者修完转绿，后者修完转红，且两者在修复前看起来一模一样（都是红/都是绿）。
我这次的写法把「确认缺陷存在」和「验证缺陷已修」混为一谈了。
**已在每条断言旁加注释说明「这条守的是修复后的行为」**，避免下一轮的人又改回去。

### 13.6 顺手修好的两件测试基建（D-13）

`test_lang_switch.py` 除了挂死，还有第二个更隐蔽的问题：**它没有重定向 `APPDATA`**，
末尾 `save_config()` 会写**用户真实的 config.json**（语言被写成 en 再写回 zh）。
一个测试能改用户配置，这在 CI 上会互相污染。已一并修：

- 补 `confirm_changes` 打桩（决定它到底验什么：只验语言切换，不验确认框）；
- import `app.*` 之前重定向 `APPDATA` / `LOCALAPPDATA` 到临时目录；
- 收尾改用 `clean_exit`（D-09）。

修完 **10/10 通过**，已并入 V8。这条很重要 —— 它原本是**语言切换唯一的回归用例**，
而语言切换正是本版的核心改动区，它挂着等于这块没有回归网。

### 13.7 终验全量结果（`a440f8c`）

```
V8 结果：28/28 通过
  ├─ 19 个用例全部 rc=0
  │    api_base 27/27 · remote_config_v084 70/70 · remote_ssh 50/50 · remote_ops 47/47
  │    tunnel 65/65 · cloud_hooks OK · remote_cleanup OK · remote_ui OK · ai_conn 23/23
  │    task_notify 7/7 · batch_runner 6/6 · secret_store 19 项 · plugin_deps 55/0
  │    close_behavior 30/30 · styled_bg 11/0
  │    test_qa_v0840_entry 21/21 · test_qa_v0840_i18n_save 10/10
  │    test_lang_switch 10/10 · test_settings_first_save ALL PASS
  ├─ compileall -q app → rc=0 且无输出
  ├─ 隔离性：真实 %LOCALAPPDATA%\ComfyHub\remote 跑前 0 个文件 / 跑后 0 个
  └─ 基线对账：4 条既有失败与预期一致（FakeProcs 缺 is_own ×2 / 发布者父键 / 需真实实例）
```

**残余项（不影响发布）**：

| 编号 | 说明 |
|---|---|
| D-07 P3 | `clean_dir` 的「目录不可写」诊断分支在 Windows 上不可达（`os.access` 对目录恒 True）；行为仍安全，只是那句提示出不来 |
| D-08 P3 | 不动字段也刷一条假「凭证」改动（0.8.39 既有） |
| D-11 P3 | `size` 策略 `total` 含受保护文件，可能删光可删的仍超标且无提示 |
| — | `test_instance_mount_ui` / `test_save_diff` 仍因 `FakeProcs` 缺 `is_own` 失败（既有）；`test_ui_state` 发布者父键 1 项（既有）；`test_open_web` 需真实实例（既有） |

---

## 附录：复现命令

```bash
cd F:/ComfyHub
PY=.venv_build/Scripts/python.exe

# 单点验证（每条都能独立跑，互不污染）
$PY -u tests/test_qa_v0840_fields.py     # V1  期望 126/126
$PY -u tests/test_qa_v0840_creds.py      # V2  期望 40/43（D-01 未修）
$PY -u tests/test_qa_v0840_tunnel.py     # V3  期望  42/42
$PY -u tests/test_qa_v0840_state.py      # V4  期望  38/38
$PY -u tests/test_qa_v0840_cleanup.py    # V5  期望  45/45
$PY -u tests/test_qa_v0840_i18n.py       # V6  期望 22/40（D-03 未修）
$PY -u tests/test_qa_v0840_layers.py     # V7  期望  36/36
$PY -u tests/test_qa_v0840_regress.py    # V8  期望 15/15 用例 rc=0（偶尔 1 条 0xC0000005）

# 对照（工程师的静态检查，跑出来应该是全绿——这正是问题所在）
$PY tests/test_theme_i18n.py             # 期望 57/57
$PY tests/i18n_leftover.py               # 期望「残留 15 条」
```

验收门槛（修完后应该变成）：

```
V1 126/126   V2 43/43   V3 42/42   V4 38/38
V5  45/45    V6 40/40   V7 36/36   V8 全绿
```

> V6 从 27 条涨到 40 条，是复核 `8594bde` 时新增的 §V6-6（15 条新词逐条实测量化）。
> 这些残留要一起转绿，靠的是**改调用点与扫描器**，不是继续补词。
