# antigravity-zh

Google Antigravity 桌面版（Windows）中文汉化工具。

[![Release](https://img.shields.io/github/v/release/wyr12353/antigravity-zh?color=blue&logo=github)](https://github.com/wyr12353/antigravity-zh/releases)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows)](https://github.com/wyr12353/antigravity-zh)
[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python)](https://github.com/wyr12353/antigravity-zh)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## ✨ 特性

- **免配置开箱即用**：提供预编译单文件 `.exe`，无需配置 Python 或 Node.js 环境，双击即汉化并启动。
- **词典外置与自维护**：词典与核心注入逻辑解耦，支持直接编辑 `dict/*.json` 自行增删词条。
- **一键安全还原**：自动备份官方原版，随时可一键无损还原。
- **跟手自动适配**：官方客户端更新后，只需重新运行一次汉化即可完成重新注入。

---

## 🚀 快速使用

### 方式一：下载即用（推荐）

1. 前往 [Releases](https://github.com/wyr12353/antigravity-zh/releases) 下载最新构建：
   - **`antigravity-zh.exe`**：单文件版，直接双击自动完成汉化并启动客户端。
   - **`antigravity-zh-with-dict.zip`**：附带完整 `dict/` 词典目录，便于自行扩充词条。

2. 快捷命令行操作（可选）：
   ```powershell
   .\antigravity-zh.exe                 # 汉化并启动客户端
   .\antigravity-zh.exe status          # 查看当前版本与汉化状态
   .\antigravity-zh.exe restore --yes   # 一键还原为官方原版
   ```

> 💡 **词典读取规则**：若 `antigravity-zh.exe` 同级目录下存在 `dict/` 文件夹，将优先加载外部词典；否则使用内嵌词典。

---

### 方式二：源码运行

环境要求：**Windows** + **Python 3.9+**（纯标准库，无任何第三方依赖）。

```bash
# 常用命令
python patcher.py patch --yes --launch   # 汉化并启动客户端
python patcher.py status                 # 查看状态与版本匹配
python patcher.py restore --yes          # 还原官方原版
```

---

## 📖 常用命令清单

| 命令 | 说明 |
| :--- | :--- |
| `patch --yes --launch` | 执行汉化并启动客户端（跳过确认提示） |
| `status` | 查看客户端版本、补丁注入状态及词典规模 |
| `restore --yes` | 还原为官方原版状态 |
| `scan --yes --launch` | 开启新词收集模式，交互使用后将未翻译文案导出至 `out/` |
| `missing` | 静态扫描提取当前客户端 bundle 中尚未收录的英文文案 |

---

## ✏️ 自定义与扩充词典

词典与匹配规则存放在 **`dict/`** 目录（标准 JSON 格式）：

1. 编辑已有文件或新增 `.json` 词典文件（键为英文原文，值为中文译文）。
2. 修改完成后，重新执行汉化命令即可生效：
   ```bash
   python patcher.py patch --yes
   ```

---

## 💡 常见问题

- **客户端更新后汉化失效？**  
  官方更新会覆盖客户端文件，只需重新运行一次本工具的汉化命令即可重新适配。
- **如何彻底卸载汉化？**  
  运行 `restore --yes` 命令即可完全恢复官方原版状态。

---

## 致谢与声明

- 词典与方案源自 [MIMICTE/Antigravity-zh-CN](https://github.com/MIMICTE/Antigravity-zh-CN)（MIT License）。
- 本工具仅修改本地已安装客户端的前端界面显示文案，不触碰任何网络与账号逻辑，仅供个人学习研究使用。
