# antigravity-zh — Antigravity 桌面版中文汉化工具

自维护版 Google Antigravity（Windows 桌面版）中文汉化补丁工具。
词典与补丁逻辑分离，界面出现新英文文案时可自行收集、翻译、重新打补丁。

> 词典与方案源自 [MIMICTE/Antigravity-zh-CN](https://github.com/MIMICTE/Antigravity-zh-CN)（MIT），
> 在其基础上重构为"词典外置 + 收集式维护"的个人维护版。仅改前端显示文本，仅供个人学习使用。

**平台**：Windows（依赖 `tasklist` / `taskkill` 与 `%LOCALAPPDATA%` 安装路径）。
**依赖**：Python 3.9+，纯标准库；`tools/verify.py` 在检测到 node 时会额外跑一层真实引擎校验。

## 与上游的差异

| 能力 | 说明 |
|---|---|
| 词典外置 | `dict/*.json` 可自行增删，按文件名排序合并，后者覆盖前者；跨文件同名键会被自检判为**失败**（避免同一原文出现两种译法） |
| 实机收集 | `scan` 子命令把未翻译文案实时落盘，配合 `tools/merge_dict.py` 形成维护闭环 |
| 同源滤噪 | `tools/scan_filter.py` 与运行时收集器共用一套判定，`tools/selfcheck.py` 用 46 条语料双跑比对两侧结果 |
| 静态提取 | `missing` 子命令不点界面也能从界面 bundle 出待翻译清单 |
| 双引擎验证 | `tools/verify.py` 用 Python 模拟层 + 真实 Node 引擎跑同一批用例 |
| 原生菜单 | 含 Windows 系统托盘菜单，以及绕过 `Menu.buildFromTemplate` 钩子的动态计数文案 |
| 盲区修复 | 下拉选择器（`[role=combobox]`）的折叠状态值、模板拼接句的兜底翻译 |
| 版本漂移提示 | 与版本耦合的字面量替换未命中时会明确报警，不静默失效 |

## 免责声明

本项目与 Google 无任何关联，未获其授权或认可。工具只修改**本机已安装客户端**的界面显示文本，
不触碰网络与账号逻辑，仓库内也不包含 Google 的任何代码或资源。修改客户端界面可能与
Antigravity 的服务条款存在冲突，请自行判断是否使用；本项目仅供个人学习研究。

## 一键版（免 Python）

从 Releases 下载，不需要装 Python：

| 产物 | 适用场景 |
|---|---|
| `antigravity-zh.exe` | 双击即用，词典内嵌在 exe 里（约 8.6 MB） |
| `antigravity-zh-with-dict.zip` | 解压后 exe 与 `dict/` 并列，可自行增删词典 |

**双击 exe** = 汉化并启动（等价 `patch --yes --launch`）。也可以带参数当命令行用：

```
antigravity-zh.exe status            查看版本与补丁状态
antigravity-zh.exe restore --yes     还原官方原版
antigravity-zh.exe missing --top 40  列出词典尚未覆盖的文案
```

词典查找顺序是「**exe 同级目录的 `dict/` 优先，找不到才用内嵌词典**」，
所以要用自己的词典就下载 zip 版，改 `dict/*.json` 后重新运行 exe 即可。

> ⚠️ 同级 `dict/` 是**整体替换**内嵌词典，不是叠加：exe 旁边一旦存在 `dict/`，
> 内嵌的那份就完全不再被读取。所以想加几条词，请从 `antigravity-zh-with-dict.zip`
> 出发改（里面是完整词典）；只丢一个只含新增条目的文件夹进去，会静默丢掉其余全部词条。

> **关于杀毒软件误报**
>
> exe 由 PyInstaller 打包，可能被部分杀软按启发式拦截。原因是这类工具的行为特征
> 本身就是"结束进程 + 改写其他程序的安装目录"，叠加 PyInstaller 通用外壳的静态特征
> （上游项目的 FAQ 里也说明过同类问题）。
>
> 它不修改网络与账号逻辑，始终保留官方原版备份且可一键还原。如果不放心，可以对照
> `.github/workflows/build-exe.yml` 自行构建（构建在 GitHub Actions 上完成、产物可复现），
> 或直接用源码版 `python patcher.py patch --yes`。

### 自己构建 exe

```bash
pip install "pyinstaller>=6.0,<7.0"
pyinstaller -F --noupx --name antigravity-zh --add-data "inject;inject" --add-data "dict;dict" --add-data "tools/scan_filter.py;tools" patcher.py
```

`--noupx` 是必须的：UPX 压缩会显著提高杀软误报率。
`tools/scan_filter.py` 也要打进去——`missing` 会用它的噪声判定筛候选，
缺了它只会静默降级（少一层过滤且不提示）。

产物是 `dist\antigravity-zh.exe`；`build\` 与 `antigravity-zh.spec` 是中间文件，
两者都已被 `.gitignore` 忽略，可以随手删掉。

### 发版约定

- **tag 跟随 Antigravity 客户端版本**：`v2.19.1` 表示适配客户端 2.19.1；
- **工具自身的修复用第四位补丁号**：`v2.19.1.1` 表示客户端版本不变、仅工具修复；
  客户端没更新也可以发版；
- 打 tag 并推送后 CI 自动构建 exe 并发布 Release（附 `SHA256SUMS.txt` 校验和），
  无需手动上传任何产物；
- Release 说明第一行是该版本适配的客户端版本范围（由 CI 依据 tag 自动生成）。

## 快速上手

```bash
python patcher.py patch --yes --launch   # 汉化并启动
python patcher.py status                 # 查看状态
python patcher.py restore --yes          # 还原官方原版
```

要求：Python 3.9+（纯标准库，无第三方依赖，**不需要 Node**）。
Antigravity 运行中会被自动关闭（`--yes` 免询问）。

| 子命令 | 用途 |
|---|---|
| `patch` | 打汉化补丁（解包 asar + 注入引擎与词典） |
| `scan` | 打补丁并开启收集模式，实机点界面收集未翻译文案 |
| `missing` | 不点界面，直接从界面 bundle 找出词典未覆盖的文案 |
| `restore` | 还原官方原版 |
| `status` | 查看版本、补丁状态、词典规模 |

## Antigravity 更新后怎么办

官方自动更新会覆盖补丁，界面回到英文。此时**不需要改本工具**，只需重跑：

```bash
python patcher.py patch --yes
```

（或者直接双击 `patcher.py`：不带子命令即按"一键汉化并启动"处理，适合不习惯命令行的场景。）

补丁会从新版 app.asar 重新解包注入。`status` 可随时确认版本是否匹配。

> 机制说明：patch 会把官方 `app.asar` **改名**为 `app.asar.zh-orig` 并解包到 `resources/app`。
> Electron 的加载顺序是 `app.asar` 优先于 `app` 目录，所以必须移走 asar 才能让汉化目录生效——
> 只复制备份、保留 asar 原位是无效的。

## 界面出现新的英文文案（维护闭环）

方式一：**实机收集**（准确，只收真实渲染出的文本）

```bash
python patcher.py scan --yes --launch    # 1. 打收集模式补丁并启动
# 2. 正常使用 Antigravity，把想汉化的界面各点一遍
python tools/scan_filter.py              # 3. 清洗收集产物里的代码片段/包名/版本号噪声
python tools/merge_dict.py               # 4. 逐条输入中文（回车跳过不要的）
python patcher.py patch --yes --launch   # 5. 重新打补丁生效
```

未翻译文案实时记录在 `out/untranslated.json`（含出现次数和 DOM 位置）。
`scan` 会自动把上一轮收集结果归档为 `out/untranslated.prev.json`，避免新旧数据混在一起。
收集器（`inject/zh_runtime.js`）与 `tools/scan_filter.py` 使用同一套噪声判定：
长度 ≤2 的残片、按键名、邮箱、纯符号、版本号、URL、文件名标签（`AGENTS.md:`）、
代码注释与模板串、JS 关键字、路径/包名（含 `/ \ _ $ -`）、括号残片都不入收集；
`merge_dict.py` 合并前还会再兜底过滤一次。
少量代码标识符仍可能漏网（如 `greet`、`name`），交互时回车跳过即可。

方式二：**静态提取**（不用点界面，覆盖面广但有噪声）

```bash
python patcher.py missing --top 40       # 从当前版本的界面 bundle 提取并做差集
```

结果写入 `out/missing.json`，分三组：`ui`（疑似界面文案，重点看这个）、
`suspect`（疑似开发者错误信息）、`other`。挑出要翻译的补进 `dict/*.json` 即可。
Antigravity 的 bundle 里混有整个 VS Code/Chromium 的内部字符串，所以 `ui` 组仍需人工过一遍。

### 验证词典与规则

```bash
python tools/verify.py            # 跑 tools/verify_cases.json 里的用例，报告通过率
python tools/verify.py --verbose  # 打印每条译文
python tools/verify.py --from-dict  # 直接读 dict/*.json，不依赖安装目录（CI 用）
```

验证分两层：

1. **Python 模拟层** — 读取真实字典与规则（默认取自已注入补丁的安装目录，
   `--from-dict` 时改取 `dict/*.json`），按运行时顺序逐条核对；
2. **Node 真实引擎层** — 把同一批用例交给 `tools/js_check.js` 用真实 JavaScript 引擎跑一遍
   （node 不可用时自动跳过），能捕捉模拟层抓不到的替换语义问题（例如模板里的 `$1`
   在字符串 pattern 下不会被解析这类 bug）。这一层的翻译实现**直接从 `inject/zh_runtime.js`
   截取**，不另抄一份，所以改了运行时却忘了改测试的情况不会发生。

`--from-dict` 跳过第一层的数据源（安装目录），直接由 `dict/*.json` 组装，
这样 CI 上也能跑这套回归；该模式下 `--dir` 不生效。

新增用例只需在 `tools/verify_cases.json` 的 `cases` 里加一行 `[原文, 期望译文]`
（期望留空表示该句应保持英文）。

## 直接改词典

编辑 `dict/*.json`（JSON，键为英文原文、值为中文）。括号里是该文件承载的**段落名**，
它和文件名并不总是一致：

- `common.json`（`exact` / `words`）— 整句精确匹配；`words` 是 ≤3 词的小写弱匹配
- `permissions.json`（`exact` / `rules`）— 权限弹窗专项（按钮、开关说明、权限条目）
- `prefix_rules.json`（**`rules`**）— 前缀规则（命中即整句替换），适合被截断/拼接的长文案
- `template_rules.json`（**`template`**）— 整句模板规则 `[正则, 标志, 译文模板]`，处理运行时
  拼接的句子（如 `Select model, current: <模型>`），模板里 `$1` 引用捕获组；**按顺序匹配，先命中者生效**
- `menus.json`（`menus`）— 原生菜单标签（Windows 上是系统托盘菜单；macOS 上是顶部菜单栏）
- `ui_extra.json`（`exact`）— 早期批量提取的界面文案
- `ui_v2191.json`（`exact`）— 按 `missing` 候选批量补译的界面文案（设置项说明、主题颜色描述、错误提示等）

> `prefix_rules.json` 承载的段落名是 `rules`（历史命名），跟文件名并不字面一致。而模板
> 规则那个文件原先也叫 `rules.json`，于是出现"文件名叫 rules、里面装的却是 `template`"
> 这种最容易看走眼的误导，已改名为 `template_rules.json`。

多个文件按**文件名顺序**合并，后者覆盖前者（即上面自上而下的顺序，越靠后优先级越高）。
**新增词典文件时文件名就决定了它的覆盖优先级** —— 取名过于靠前（如 `aaa_fix.json`）
会被后面的文件静默覆盖。改完重跑 `patch` 即生效，建议再跑一次 `python tools/verify.py` 确认无回归。

> `ui_extra.json` 现在是**直接维护的词典文件**。早期它由 `tools/generate_extended_dict.py`
> 生成，该脚本已退役——它的 752 条映射与 23 条模板规则已全部落在词典里（无一缺失），
> 且词典中的版本还修正过 3 条译文。所以现在可以直接编辑它，就像其他词典文件一样。

## 目录结构

```
patcher.py             CLI：patch / scan / missing / restore / status
agasar.py              asar 解析/解包（带条目路径遏制校验）
inject/zh_runtime.js   渲染进程翻译引擎（MutationObserver + 规则表）
inject/zh_main_patch.js 主进程补丁（原生菜单汉化 + scan 收集落盘）
dict/*.json            词典与规则（可自行增删文件，patch 时自动合并）
tools/merge_dict.py    收集结果 → 词典的交互式合并（合并前自动滤除噪声）
tools/scan_filter.py   未翻译文案的噪声过滤（可单独运行清洗 out/untranslated.json）
tools/verify.py        用真实注入产物回归验证词典与规则
tools/js_check.js      真实 Node 引擎校验（由 verify.py 调用，非独立入口）
tools/verify_cases.json 验证用例（改这里即可扩充测试）
tools/selfcheck.py     提交前自检（七组：语法/词典/占位符/噪声等价/运行时规则/用例/隐私）
LICENSE                MIT（版权归属见文件头）
.gitignore             忽略运行产物与缓存
.gitattributes         统一 LF 行尾
.github/dependabot.yml 每月自动跟进 actions 版本并提 PR
.github/workflows/check.yml  CI：跑 tools/selfcheck.py 与 tools/verify.py --from-dict
.github/workflows/build-exe.yml  CI：打 tag 时构建单文件 exe 并发布 Release
out/                   运行产物（已 gitignore，可随时重建）
  missing.json         missing 子命令产物（待翻译文案，分 ui/suspect/other）
  untranslated.json    scan 收集产物（scan 启动时会把上一轮归档为 untranslated.prev.json）
```

## 工作原理（为什么这么做）

Antigravity 桌面版（v2.x）的 `app.asar` 只是约 4.5MB 的 Electron 壳，
真实界面（Agent Manager 与编辑器）由 `resources/bin/language_server.exe`
内嵌并通过 `https://127.0.0.1:<动态端口>/` 动态提供，没有静态网页文件可改。
因此本工具：

1. 把 `app.asar` 备份为 `app.asar.zh-orig` 并解包到 `resources/app`
   （Electron 优先加载 `app/` 目录，`app.asar.unpacked` 内容一并并入）；
2. 向 `dist/preload.js` 追加翻译引擎：MutationObserver 监听 DOM，按
   **整句精确 → 函数式规则 → 模板规则 → 子串替换 → 前缀规则 → 短词弱匹配**
   的顺序替换文本。其中词典类规则（精确/弱匹配、模板、前缀）分别来自 `dict/*.json`，
   新增或修改它们不必动 JS；函数式规则（耗时单位换算、权限句拼装等）写在
   `inject/zh_runtime.js` 里，改这类规则需要改 JS。
   覆盖 `title` / `placeholder` / `aria-label` / `alt` 与窗口标题，
   跳过 `pre` / `code` / 输入框 / Monaco 编辑器等代码与输入区
   （注意 `[role=combobox]` 刻意**不**跳过：设置项的下拉选择器把"当前值"渲染在
   它内部，跳过它会让折叠状态的值永远无法翻译，而展开的选项列表却会翻译）；
3. 向 `dist/main.js` 追加主进程补丁：包装 `Menu.buildFromTemplate` 汉化原生菜单；
4. 写入 `app/.agzh-patched.json` 标记供 `status` 比对版本。

已知限制：

- 对话消息流（用户消息、模型回复、思维链正文）整体跳过翻译：它们是"内容"而非
  界面，流式渲染会把一句话拆成大量碎片节点，短词条与弱匹配词表会命中碎片、
  产出中英混杂的乱翻文本（因此旧的"Thought 流无法实时汉化"限制已升级为
  "整段内容保持原文"）；消息内的按钮提示与"运行/思考耗时"时间戳仍会翻译；
- 窗体内嵌 iframe 页面的文案视实现可能不被覆盖（遇到时用 `scan` 确认范围）；
- 从 marketplace 安装的技能，其描述由服务端下发、不在本地 bundle 里，
  `missing` 静态提取拿不到，只能用 `scan` 实机收集。

## 风险与还原

补丁只改本地显示文本，不触碰网络与账号逻辑。官方更新或 `restore`
都会回到原版状态；`app.asar.zh-orig` 始终是最近一次官方原版。
若 Antigravity 启动异常，先 `restore` 确认原版可正常运行。
