# -*- coding: utf-8 -*-
"""把 out/untranslated.json（scan 收集的未翻译英文）逐条并入 dict/common.json。

用法:
    python tools/merge_dict.py          # 交互式，逐条输入中文翻译
    python tools/merge_dict.py --list   # 只浏览收集结果，不修改词典

合并后记得重跑:  python patcher.py patch
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan_filter import is_noise  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUT_FILE = PROJECT_DIR / "out" / "untranslated.json"
DICT_DIR = PROJECT_DIR / "dict"
DICT_FILE = DICT_DIR / "common.json"


def load_known_keys():
    """收集**所有**词典文件里已有的 exact 键。

    只认 common.json 是不够的：patch 按文件名排序合并、后者覆盖前者，
    ui_extra.json 排在最后，若把已存在的键再写进 common.json，
    新译文会被 ui_extra.json 的同名旧译文盖掉，等于白改。
    """
    known = set()
    for path in sorted(DICT_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        known.update(data.get("exact", {}))
    return known


def main():
    list_only = "--list" in sys.argv

    if not OUT_FILE.exists():
        raise SystemExit(f"[错误] 找不到 {OUT_FILE}，请先运行 scan 并操作一遍界面")
    data = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    strings = data.get("strings", [])
    if not strings:
        raise SystemExit("[信息] 收集结果为空，没有需要翻译的新文案")

    if not DICT_FILE.exists():
        raise SystemExit(f"[错误] 找不到 {DICT_FILE}")
    dicts = json.loads(DICT_FILE.read_text(encoding="utf-8"))
    exact = dicts.setdefault("exact", {})
    known = load_known_keys()

    kept, noise = clean(strings)
    strings = kept
    if noise:
        print(f"[信息] 已自动滤除 {len(noise)} 条噪声（代码片段/快捷键残片/包名等）")
        # 列出明细：噪声判定会误杀合法文案（如 "user-name"、"e.g. (optional)"），
        # 不把滤除内容显示出来，这类误杀就永远发现不了。
        for item in noise[:10]:
            print(f"        [滤除] {str(item.get('text', ''))[:70]}")
        if len(noise) > 10:
            print(f"        …（其余 {len(noise) - 10} 条未列出）")
        print()

    print(f"共收集到 {len(strings)} 条未翻译文案（按出现次数排序）。\n")
    added = 0
    skipped = 0
    for item in strings:
        text = item.get("text", "").strip()
        count = item.get("count", 1)
        path = item.get("path", "")
        if not text:
            continue
        if text in known:            # 任何一份词典已覆盖（含 ui_extra.json 等）
            skipped += 1
            continue
        print(f"[{count} 次] {text[:120]}")
        if path:
            print(f"        位置: {path[:120]}")
        if list_only:
            continue
        answer = input("        中文 (回车跳过 / q 退出): ").strip()
        if answer == "q":
            break
        if answer:
            exact[text] = answer
            known.add(text)
            added += 1
        print()

    if skipped:
        print(f"[信息] 另有 {skipped} 条已在词典中，已自动跳过\n")
    if list_only or added == 0:
        print("未修改词典。")
        return

    backup = DICT_FILE.with_suffix(".json.bak")
    backup.write_text(DICT_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    DICT_FILE.write_text(
        json.dumps(dicts, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[完成] 新增 {added} 条词条 -> {DICT_FILE}")
    print(f"[完成] 旧词典已备份为 {backup.name}（{time.strftime('%H:%M')}）")
    print("[提示] 运行 python patcher.py patch 应用新词典")


if __name__ == "__main__":
    main()
