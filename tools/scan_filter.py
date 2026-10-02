# -*- coding: utf-8 -*-
"""未翻译文案的噪声过滤 —— 实机收集(scan)与静态提取(missing)共用同一套判定。

为什么需要：渲染进程的收集器只做"长度 / 按键名 / 邮箱"三层粗过滤，
界面里仍会混进代码片段、包名、版本号、快捷键残片等噪声。

本模块与 inject/zh_runtime.js 内 collector 的 isNoise() 保持等价：
JS 侧负责运行时实时拦截，本模块负责事后清洗与 merge_dict 合并前的兜底。

用法:
    python tools/scan_filter.py            # 就地清洗 out/untranslated.json（旧文件备份为 .bak）
    python tools/scan_filter.py --check    # 只报告会被过滤掉的条目，不写文件
"""
import json
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUT_FILE = PROJECT_DIR / "out" / "untranslated.json"

# 按键名（快捷键残片）
KEYNAMES = frozenset((
    "ctrl", "control", "shift", "alt", "enter", "tab", "esc", "escape", "space",
    "cmd", "meta", "option", "backspace", "delete", "del", "win", "windows",
    "cmdorctrl", "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
))

# 代码关键字：全小写单 token 命中即视为源码噪声
JS_KEYWORDS = frozenset((
    "const", "let", "var", "function", "return", "import", "export", "class",
    "async", "await", "yield", "typeof", "instanceof", "new", "this", "null",
    "undefined", "true", "false", "boolean", "number", "string", "object", "symbol",
    "bigint", "interface", "enum", "public", "private", "protected", "static",
))

_NO_LETTER_RE = re.compile(r"^[^A-Za-z\u4e00-\u9fff]*$")
_VERSION_RE = re.compile(r"^v?\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.]+)?$")
_IDENT_RE = re.compile(r"^[A-Za-z0-9_$@./\\+-]+$")
_HEX_RE = re.compile(r"^[0-9a-f]{8,}$", re.IGNORECASE)
# URL（如 https://open-vsx.org/vscode/item）
_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
# 文件名标签（如 AGENTS.md:、settings.json）
_FILENAME_RE = re.compile(
    r"^[A-Za-z0-9_.-]+\.(?:md|markdown|json|ya?ml|toml|js|mjs|cjs|ts|tsx|jsx|py|txt|log|lock|cfg|ini):?$")


def is_noise(text):
    """判断一条收集到的文案是否属于噪声（不应进词典）。"""
    t = (text or "").strip()
    if len(t) <= 2:                      # 单字符 / 快捷键残片
        return True
    if t.lower() in KEYNAMES:            # 按键名
        return True
    if "@" in t and " " not in t:        # 邮箱、账号标识
        return True
    if _NO_LETTER_RE.match(t):           # 不含 ASCII 字母与中文（纯数字、纯符号残片）
        return True
    if _VERSION_RE.match(t):             # 版本号
        return True
    if _URL_RE.match(t):                 # URL
        return True
    if _FILENAME_RE.match(t):            # 文件名标签
        return True
    if t.startswith("//") or t.startswith("/*") or t.startswith("*/"):
        return True                      # 代码注释
    if "${" in t or "`" in t:            # 模板字符串片段
        return True
    if " " not in t and ("(" in t or ")" in t) and len(t) <= 30:
        return True                      # 括号残片（如 "tokens)"）
    if t.lower() in JS_KEYWORDS:         # 源码关键字
        return True
    if _HEX_RE.match(t):                 # 十六进制/hash 片段
        return True
    if " " not in t and _IDENT_RE.match(t):
        if any(c in t for c in "/\\_$"):  # 路径、包名、snake_case 标识符
            return True
        if "-" in t and len(t) <= 40:     # kebab-case 包名
            return True
    # 代码行特征：以分号结尾、含箭头或含 "}("，且没有自然语言句子的形态
    if len(t) <= 60 and (" " not in t) and (t.endswith(";") or "=>" in t or "}(" in t):
        return True
    return False


def clean(strings):
    """把 [{text,count,path}] 拆成 (保留, 丢弃)。"""
    kept, dropped = [], []
    for item in strings or []:
        text = (item.get("text") or "") if isinstance(item, dict) else str(item)
        (dropped if is_noise(text) else kept).append(item)
    return kept, dropped


def main():
    check = "--check" in sys.argv
    if not OUT_FILE.exists():
        raise SystemExit(f"[错误] 找不到 {OUT_FILE}，请先运行 scan 并操作一遍界面")
    data = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    strings = data.get("strings", [])
    if not strings:
        raise SystemExit("[信息] 收集结果为空，无需清洗")

    kept, dropped = clean(strings)
    print(f"共 {len(strings)} 条 —— 保留 {len(kept)} 条，过滤噪声 {len(dropped)} 条")
    for item in dropped:
        text = item.get("text", "") if isinstance(item, dict) else str(item)
        print(f"  [滤除] {text[:100]}")
    if check:
        print("[提示] --check 模式未写入文件")
        return

    backup = OUT_FILE.with_suffix(".json.bak")
    backup.write_text(OUT_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    data["strings"] = kept
    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[完成] 已清洗 {OUT_FILE.name}，旧文件备份为 {backup.name}")
    print("[提示] 接着运行 python tools/merge_dict.py 逐条补充翻译")


if __name__ == "__main__":
    main()
