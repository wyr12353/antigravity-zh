# -*- coding: utf-8 -*-
"""验证翻译引擎对给定句式的处理结果（纯 Python，不需要 Node）。

数据源可以是**已注入补丁的安装目录**，也可以是 dict/*.json（--from-dict，供 CI 用）。
跑 tools/verify_cases.json 里的用例并报告通过率，用于改词典/规则后的回归检查。

用法:
    python tools/verify.py [--dir 安装目录] [--verbose] [--from-dict]

覆盖范围：词典精确匹配、函数式规则、模板规则、前缀规则、弱匹配词。
**不含**注入运行时的子串替换类规则（它们的单层断言在 tools/selfcheck.py 里，
用真实 Node 引擎直接跑 zh_runtime.js 的规则表）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 同 tools/selfcheck.py 里的保护：管道输出时 locale 编码（cp1252）放不下中文。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

TOOLS_DIR = Path(__file__).resolve().parent
CASES_FILE = TOOLS_DIR / "verify_cases.json"
DEFAULT_INSTALL = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity"
PRELOAD_REL = Path("resources") / "app" / "dist" / "preload.js"

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def grab(name, src):
    """从注入产物里取出 `var NAME = <json>` 的 JSON 值。

    用 raw_decode 精确解析出第一个完整 JSON 值，而不是找分号截断——
    词典译文里一旦出现 ASCII 分号，按分隔符截取就会解析失败。
    """
    norm = src.replace("\r\n", "\n")
    head = "var " + name + " = "
    start = norm.find(head)
    if start < 0:
        raise SystemExit(f"[错误] 注入产物里找不到 {name}，安装目录可能未打补丁")
    try:
        value, _end = json.JSONDecoder().raw_decode(norm[start + len(head):].lstrip())
    except json.JSONDecodeError as e:
        raise SystemExit(f"[错误] 解析 {name} 失败: {e}")
    return value


def duration_zh(s):
    """与 zh_runtime.js 的 durationZh 使用同一套正则。

    早先这里是链式 replace，与 JS 的正则实现在边界上会分叉。
    js_check.js 那边不需要同步——它直接截取 zh_runtime.js 的实现。
    """
    s = re.sub(r"days?", "天", s)
    s = re.sub(r"hours?", "小时", s)
    s = re.sub(r"minutes?", "分钟", s)
    return s.replace(",", "")


def run_js_check(dicts, cases):
    """把同一批用例交给真实 Node 引擎跑一遍，捕捉 Python 模拟层抓不到的问题
    （例如模板里的 $1 在字符串 pattern 下不会被解析）。node 不可用时跳过。"""
    if shutil.which("node") is None:
        print("\n[跳过] 未找到 node，跳过真实引擎校验")
        return 0
    project_dir = TOOLS_DIR.parent
    out_dir = project_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "_js_check_data.json").write_text(json.dumps({
        "exact": dicts["exact"], "words": dicts["words"],
        "prefix": dicts["rules"], "template": dicts["template"], "cases": cases,
    }, ensure_ascii=False), encoding="utf-8")

    try:
        proc = subprocess.run(["node", str(TOOLS_DIR / "js_check.js")],
                              cwd=str(project_dir), capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        print("\n[失败] 真实引擎校验超时（120 秒）")
        return 1
    except OSError as e:
        print(f"\n[失败] 无法执行 node：{e}")
        return 1
    # 只有"node 不存在"才算跳过（上面已判）；崩溃/异常退出必须报错，
    # 否则这一层校验会静默消失、回归给出假绿灯。
    if proc.returncode != 0:
        print(f"\n[失败] js_check.js 退出码 {proc.returncode}："
              f"{proc.stderr.decode('utf-8', 'replace').strip()[:200]}")
        return 1
    try:
        result = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        print(f"\n[失败] 无法解析 js_check.js 的输出：{e}")
        return 1

    js_failed = result.get("failed", 0)
    total = result.get("passed", 0) + js_failed
    print(f"Node 真实引擎: {result['passed']}/{total} 通过"
          + ("，全部正确" if js_failed == 0 else f"，{js_failed} 条未通过"))
    for item in result.get("failures", []):
        print(f"  原文: {item['text']}\n  期望: {item['expect']}\n  实际: {item['got']}\n")
    return js_failed


def load_source(args):
    """返回 {exact, words, rules, template}（rules 即前缀规则）。"""
    if args.from_dict:
        # 直接从词典文件组装，绕开安装目录 —— 这样 CI 上也能跑这套回归
        sys.path.insert(0, str(TOOLS_DIR.parent))
        from patcher import load_dicts  # noqa: E402
        print("数据源: dict/*.json")
        return load_dicts()

    install = Path(args.dir or DEFAULT_INSTALL).resolve()
    preload_path = install / PRELOAD_REL
    if not preload_path.exists():
        raise SystemExit(f"[错误] 未找到 {preload_path}，请先运行 python patcher.py patch")
    src = preload_path.read_text(encoding="utf-8")
    print("数据源: 已注入的 preload.js")
    return {"exact": grab("EXACT", src), "words": grab("WORDS", src),
            "rules": grab("PREFIX_RULES", src), "template": grab("TEMPLATE_RULES", src)}


def make_translate(dicts):
    """按运行时顺序复刻一套翻译流程（Python 版）。"""
    exact, words = dicts["exact"], dicts["words"]
    template_rules, prefix_rules = dicts["template"], dicts["rules"]

    def learn(m):
        # or 兜底：与 JS 的 EXACT[m[1]] || m[1] 一致，空串译值走原文
        return "了解更多关于 " + (exact.get(m.group(1)) or m.group(1)) + " 的信息"

    def limit(prefix_zh, m):
        return prefix_zh + duration_zh(m.group(1)) + " 后完全重置。"

    # 与 zh_runtime.js 的 FN_RULES 一一对应。运行时那 5 条由 JS 函数实现，
    # 这里用等价的正则 + 字符串拼接表达。词典查找都用 .get(...)+truthy，
    # 与 JS 的真值判断一致（空串译值视为未命中）。
    fn_rules = (
        (r"^Learn more about (.+)$", learn),
        (r"^You have used some of your weekly limit, it will fully refresh in (.*)\.$",
         lambda m: limit("您已使用部分每周限额，它将在 ", m)),
        (r"^You have used some of your 5-hour limit, it will fully refresh in (.*)\.$",
         lambda m: limit("您已使用部分五小时限额，它将在 ", m)),
        (r"^Allow (.+)\?$",
         lambda m: ("允许" + exact[m.group(1)] + "吗？") if exact.get(m.group(1))
         else ("允许 " + m.group(1) + " 吗？")),
        (r"^Save rule to always allow (.+)\?$",
         lambda m: ("保存规则以始终允许" + exact[m.group(1)] + "吗？") if exact.get(m.group(1))
         else ("保存规则以始终允许 " + m.group(1) + " 吗？")),
    )

    def translate(text):
        trimmed = text.strip()
        if not trimmed or CJK_RE.search(trimmed):
            return text
        # 与 JS 真实语义对齐的三处细节：
        #   - 词典按真值判断，空串译值视为未命中（JS: if (EXACT[trimmed])）
        #   - String.replace(子串) 只替换第一处 → 统一 count=1
        #   - 模板命中但替换结果与原文相同时继续尝试后续规则（JS 的 !== 比较）
        def sub(out):
            return text.replace(trimmed, out, 1)

        hit = exact.get(trimmed)
        if hit:
            return sub(hit)

        for pattern, build in fn_rules:
            m = re.match(pattern, trimmed)
            if m:
                return sub(build(m))

        for pattern, flags, tpl in template_rules:
            m = re.match(pattern, trimmed, re.I if "i" in flags else 0)
            if m:
                # 用 re.sub 按捕获组精确替换：早先的循环 replace("$1", ...) 会把
                # "$10" 当成 "$1" 后接 "0" 处理，产出错误译文。组号越界时 JS 的
                # String.replace 保留字面 "$n"，这里对齐而不是裸抛 IndexError。
                def group(mm):
                    idx = int(mm.group(1))
                    return m.group(idx) if idx <= m.re.groups else mm.group(0)
                replaced = re.sub(r"\$(\d+)", group, tpl)
                if replaced != trimmed:
                    return sub(replaced)

        for prefix, translation in prefix_rules:
            if prefix in text:
                return translation

        if len(trimmed.split()) <= 3:
            word = words.get(trimmed.lower())
            if word:
                return sub(word)
        return text

    return translate


def main():
    parser = argparse.ArgumentParser(description="验证汉化引擎对给定句式的翻译结果")
    parser.add_argument("--dir", help="Antigravity 安装目录")
    parser.add_argument("--verbose", action="store_true", help="打印全部用例（默认只打印失败项）")
    parser.add_argument("--from-dict", action="store_true",
                        help="直接读 dict/*.json，不依赖已打补丁的安装目录（供 CI 使用）")
    args = parser.parse_args()

    dicts = load_source(args)
    print(f"引擎字典: 精确 {len(dicts['exact'])} | 弱匹配 {len(dicts['words'])} | "
          f"前缀规则 {len(dicts['rules'])} | 模板规则 {len(dicts['template'])}\n")

    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    translate = make_translate(dicts)

    passed = failed = 0
    failures = []
    for text, expect in cases:
        got = translate(text)
        want = expect if expect else text
        if got == want:
            passed += 1
            if args.verbose:
                print(f"[通过] {text}")
        else:
            failed += 1
            failures.append((text, want, got))

    if failures:
        print(f"---- 未通过 {len(failures)} 条 ----")
        for text, want, got in failures:
            print(f"  原文: {text}\n  期望: {want}\n  实际: {got}\n")

    print(f"Python 模拟层: {passed}/{passed + failed} 通过"
          + ("，全部正确" if failed == 0 else f"，{failed} 条未通过"))
    js_failed = run_js_check(dicts, cases)
    return 1 if (failed or js_failed) else 0


if __name__ == "__main__":
    sys.exit(main())
