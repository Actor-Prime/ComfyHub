# ComfyHub 版权合规铁律（COMPLIANCE）

> 立规日期：2026-10-23　适用：所有代码、资源、数据源、第三方依赖、竞品参考
> **违反本文任何一条的改动，不得合入主分支、不得发布。**

---

## 一、三条铁律

### 铁律 1：零版权风险代码
**禁止**引入任何**传染性许可**（GPL / AGPL / SSPL）的代码到本项目（MIT 许可）。
- 允许：MIT、BSD-2/3、Apache-2.0、ISC、Zlib、Unlicense、CC0、PSF、HPND
- 有条件允许：**LGPL**（仅动态链接 + 允许用户替换库，见 3.2）
- **禁止**：GPL、AGPL、SSPL、以及许可不明（"未标注"且查不到）的库

### 铁律 2：可借鉴，不可抄
| 允许 | 禁止 |
|---|---|
| 学习竞品的**功能设计思路**（功能创意不受版权保护）| 复制竞品的**源代码**（含反编译所得）|
| 参考**界面布局范式**（表格+工具栏是通用模式）| 复制竞品的**界面截图/图标/文案**|
| 使用**公开 API / 公开数据格式** | 复制竞品特有的**文案措辞**（说明文字逐字照搬）|
| 独立实现相同功能 | 拷贝竞品的**配置字段命名体系**（整体照搬）|

> 判定标准：**"如果对方看到你的代码，能不能指出某段是从他那里抄的？"** 能指出 → 不许提交。

### 铁律 3：来源必须可追溯
每一份**非自己写的**东西都要有记录：
- 第三方库 → 记入 `THIRD_PARTY_LICENSES.txt`（自动生成）
- 图片/图标/字体 → 记入 `assets/SOURCES.md`（含来源方式、日期、生成工具/授权）
- 外部数据源 → 在代码注释 + UI 上标注来源
- 参考过的竞品 → 记入 `docs/COMPETITORS.md`（只记功能观察，不记代码）

---

## 二、当前项目许可清单（自动盘点 2026-10-23）

### 2.1 项目自身
| 项 | 许可 |
|---|---|
| ComfyHub 源码 | **MIT**（Copyright 2026 LeiMengStudio）|

### 2.2 运行时依赖
| 包 | 许可 | 判定 |
|---|---|---|
| **PySide6 / PySide6-Essentials / Addons / shiboken6** | **LGPL-3.0** | ⚠️ 有条件允许（见 3.1）|
| psutil | BSD-3-Clause | ✅ |
| Pillow | HPND（PIL 许可）| ✅ |
| pywin32-ctypes | BSD-3-Clause | ✅ |

### 2.3 构建工具
| 包 | 许可 | 判定 |
|---|---|---|
| PyInstaller | GPLv2 **+ Bootloader 例外条款** | ✅ 例外条款明确允许打包专有程序 |
| pyinstaller-hooks-contrib | Apache-2.0 | ✅ |
| pefile / altgraph | MIT | ✅ |

### 2.4 资源
| 文件 | 来源 | 判定 |
|---|---|---|
| assets/icon.ico、logo*.png、banner.* | **AI 生成**（v0.5.50 起，自用原创）| ✅（需补记录，见 3.3）|

### 2.5 数据源（只索引，不镜像）
| 数据 | 来源 | 用法 |
|---|---|---|
| 插件目录（6036 条）| ComfyUI-Manager `custom-node-list.json`（MIT 项目公开数据）| 运行时拉取，本地缓存 12h，**不打包进 exe** |
| 下载量/星数 | ComfyUI 官方 Registry `api.comfy.org`（公开 API）| 同上 |

> **关键**：这些数据**不随程序分发**，只在用户机器上运行时拉取 → 不构成"再分发"。

### 2.6 明确不使用的东西
| 项 | 原因 |
|---|---|
| ComfyUI 本体 | 不打包、不分发，只管理用户自己的安装 |
| 用户安装的插件 | 同上，用户自行安装，我们不分发 |
| 竞品代码 | **零复制**（只借鉴功能设计）|

---

## 三、有条件允许项的具体要求

### 3.1 PySide6（LGPL-3.0）合规做法
LGPL 允许闭源/宽松许可项目使用，但必须满足：

| 要求 | 我们的做法 |
|---|---|
| **允许用户替换库** | 打包用 **onedir**（不用 onefile），`_internal/PySide6/*.dll` 可被用户替换 ✅ |
| 附许可文本 | 发布包含 `THIRD_PARTY_LICENSES.txt`（含 LGPL-3.0 全文 + Qt/PySide6 声明）|
| 声明修改 | 我们未修改 PySide6 源码 → 无需额外声明 |
| 提供源码获取方式 | 文本中给出 `https://www.qt.io/` 与 `https://pypi.org/project/PySide6/` |

**禁止**：改用 `--onefile` 打包（会把 DLL 压进 exe，用户无法替换 → 违反 LGPL）。

### 3.2 引入新依赖前的检查流程
```
1. 查 PyPI 上的 License 字段 + Classifier
2. 查 GitHub 仓库的 LICENSE 文件（PyPI 字段有时不准）
3. 判定：
   - MIT/BSD/Apache/ISC/Zlib → 通过
   - LGPL → 仅当动态链接且能替换才通过
   - GPL/AGPL/SSPL/无许可 → 拒绝，找替代品
4. 通过后加入 THIRD_PARTY_LICENSES.txt 并提交
```

### 3.3 资源来源规则
| 来源 | 允许 | 要求 |
|---|---|---|
| 自己绘制 / AI 生成 | ✅ | 记入 `assets/SOURCES.md`：日期 + 工具 + 提示词摘要 |
| 明确 CC0 / MIT 图标库 | ✅ | 记录库名 + 链接 + 许可 |
| 网络搜到的图片 | ❌ | 一律不用（来源不可追溯）|
| 竞品界面截图 | ❌ | 绝不用于界面素材 |

---

## 四、发布前检查清单（每次出包必须过）

- [ ] `APP_VERSION` 与 Release tag 一致
- [ ] `THIRD_PARTY_LICENSES.txt` 存在且包含全部依赖（`python tools/gen_licenses.py` 重新生成）
- [ ] 打包产物是 **onedir**（`dist/ComfyHub/_internal/` 下有 PySide6 dll）
- [ ] 程序内「关于」或 README 里有：
  - 项目许可（MIT）
  - 第三方库声明指向 `THIRD_PARTY_LICENSES.txt`
  - 数据源说明（插件清单来自 ComfyUI-Manager，下载量来自 ComfyUI Registry）
- [ ] `assets/SOURCES.md` 已更新（新增图片必须有记录）
- [ ] 没有新增 GPL/AGPL 依赖
- [ ] 没有复制来的代码/文案（抽查新增文件的注释与命名风格）

---

## 五、借鉴竞品的记录方式

看到竞品有某个功能想做时：

```
在 docs/COMPETITORS.md 里记：
  · 竞品名 + 版本
  · 功能点（一句话描述"它做了什么"）
  · 我们的实现思路（怎么自己实现，不抄结构）
  · 明确不参考的部分（如具体字段命名、文案）
```

**已记录**：ComfyUI管理大师 V3.6.0、ComfyUI_TE模式启动器 1.0.0、机智启动器（Mie Package Launcher）1.0.0

---

## 六、违规处理

发现违反本铁律的提交：
1. **立即回滚**（`git revert`），不讨论
2. 记录到 `docs/COMPLIANCE.md` 的"历史问题"章节
3. 若已发布 → 重新出包并公告

**历史问题**：暂无
