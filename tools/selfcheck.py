# -*- coding: utf-8 -*-
"""提交前自检，分七组：语法（Python / 可导入 / JS）、词典结构与跨文件重复键、
注入模板占位符双向一致、Python 与 JS 两侧的噪声判定等价、运行时子串规则行为、
验证用例结构、隐私泄漏。

用法:
    python tools/selfcheck.py

不需要本机安装 Antigravity（那部分是 tools/verify.py 的职责），
所以既能作为本地提交前检查，也能直接跑在 CI 上。
其中三组依赖 node（JS 语法、噪声等价、运行时规则），未安装时自动跳过。
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# stdout 被重定向成管道时编码会退化为 locale（英文 Windows 是 cp1252），而本文件
# 满屏都是中文输出，会直接抛 UnicodeEncodeError 崩掉——CI 的 windows runner 就是
# 这么挂的（ubuntu 上是 UTF-8，所以那边一直正常）。显式改成 UTF-8 并容错。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

PROJECT_DIR = Path(__file__).resolve().parent.parent
DICT_DIR = PROJECT_DIR / "dict"
INJECT_DIR = PROJECT_DIR / "inject"
TOOLS_DIR = PROJECT_DIR / "tools"

# patcher 的 data_placeholders 被 check_placeholders 使用，必须模块级导入
# （check_imports 里的函数级导入只服务它自己的"导入可用性"检查）
sys.path.insert(0, str(PROJECT_DIR))
import patcher  # noqa: E402

# 词典文件允许出现的顶层段落及其类型
SECTIONS = {"exact": dict, "words": dict, "menus": dict, "rules": list, "template": list}

# 占位符的提取/校验统一用 patcher.data_placeholders（上方已导入 patcher），
# 不在本文件重复定义正则——patcher 改提取方式时这里自动跟随。
PROVIDED_RE = re.compile(r'"(__AGZH_[A-Z_]+__)"\s*:')

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
USER_PATH_RE = re.compile(r"[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2}([^\\/\s\"']+)")
# 允许出现在仓库里的占位用户名 / 占位邮箱域
SAFE_USERS = {"example", "user", "yourname", "public", "test", "someone"}
SAFE_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "test.com", "test.local")
SCAN_SUFFIXES = {".py", ".js", ".json", ".md", ".yml", ".yaml", ".txt"}

# 噪声判定的对照语料：Python 的 scan_filter.is_noise 与 JS 的 isNoise
# 必须对每一条给出相同结论（覆盖各判定分支与真实噪声样本）。
NOISE_SAMPLES = [
    "Ctrl", "#", "Shift", "I", ".", ",", "5", "↑", "=> {", "};",
    "user@example.com",
    "2.13.0", "v1.2.3",
    "https://open-vsx.org/vscode/item",
    "AGENTS.md:", "settings.json",
    "// Greet a user by name", "/* comment */", "* item",
    "`Hello, ${name}!`",
    "const", "return", "string", "export",
    "deadbeef1234",
    "go/jetski-chat", "generative_ui", "agy-customizations", "migrate-workflows",
    "tokens)",
    "a=>b", "window.foo()", "}(){}", "x" * 50 + ";", "const x = 1;",
    "Greet a user by name", "More options", "Enter bot name (optional)",
    "This is a normal sentence.", "No agents running", "Antigravity", "work",
    "6 active conversations", "Rules: 354 tokens", "Local", "Strong",
]

# 运行时子串规则的行为断言（[输入, 期望输出]）。
# 后半段的四条是回归护栏，每一条都曾经产出过错误结果。
RULE_CASES = [
    ("Worked for 1.2s", "运行耗时 1.2秒"),
    ("Worked for 500ms", "运行耗时 500毫秒"),
    ("Worked for 2 mins", "运行耗时 2分"),
    ("Thought for 45 sec", "思考耗时 45秒"),
    ("Worked for 3 hours", "运行耗时 3小时"),
    ("Worked for 2m 30s", "运行耗时 2分 30秒"),
    ("Working", "运行中"),
    ("Working...", "运行中..."),
    # 曾经被 "Working" 无边界子串误伤（词典键带尾随空格查不到 → 产出"运行中 Directory: "）
    ("Working Directory: ", "Working Directory: "),
    # 独立的 "to be installed." 规则曾把未命中词典的整句悄悄截断
    ("There are 2 extensions to be installed.", "There are 2 extensions to be installed."),
    # 应整句处理并补上中文句号（早先守卫会让它原样返回，句号也被吃掉）
    ("It requires Chrome to be installed.", "需要安装 Chrome。"),
    ("Configure the browser subagent.", "配置浏览器子智能体。"),
]

problems = []


def fail(msg):
    problems.append(msg)
    print(f"  [失败] {msg}")


def ok(msg):
    print(f"  [通过] {msg}")


def rel(path):
    return path.relative_to(PROJECT_DIR)


def runtime_slice(start, end, what):
    """从 inject/zh_runtime.js 截取一段真实实现。

    断言跑的是源文件里的那段代码（不是另抄一份），所以测的就是真正会跑在
    渲染进程里的实现；任何单边改动都会让下面的断言失败。
    """
    src = (INJECT_DIR / "zh_runtime.js").read_text(encoding="utf-8")
    try:
        i, j = src.index(start), src.index(end)
        if j <= i:
            raise ValueError("结束标记出现在开始标记之前（会截出空切片）")
        return src[i:j]
    except ValueError as e:
        fail(f"无法从 zh_runtime.js 截取{what}：{e}（代码结构可能已变）")
        return None


def run_node(what, name, script, args=()):
    """把脚本写到 out/ 下交给 node 执行，返回其 JSON 输出；失败时返回 None。"""
    if shutil.which("node") is None:
        print(f"  [跳过] 未找到 node，无法{what}")
        return None
    out_dir = PROJECT_DIR / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    js_file = out_dir / f"_{name}.js"
    js_file.write_text(script, encoding="utf-8")
    try:
        proc = subprocess.run(["node", str(js_file), *args], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        fail(f"{what}无法执行：{e}")
        return None
    if proc.returncode != 0:
        fail(f"{what}的 node 退出码 {proc.returncode}："
             f"{proc.stderr.decode('utf-8', 'replace').strip()[:200]}")
        return None
    try:
        return json.loads(proc.stdout.decode("utf-8"))
    except json.JSONDecodeError as e:
        fail(f"无法解析{what}输出：{e}")
        return None


def check_python_syntax():
    files = sorted(PROJECT_DIR.glob("*.py")) + sorted(TOOLS_DIR.glob("*.py"))
    if not files:
        fail("没有找到任何 Python 文件（目录结构可能已变）")
        return
    bad = 0
    for path in files:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as e:
            fail(f"{rel(path)} 语法错误：第 {e.lineno} 行 {e.msg}")
            bad += 1
    if not bad:
        ok(f"{len(files)} 个 Python 文件语法正确")


def check_imports():
    """实际导入核心模块。

    语法正确不等于导入正确：形如 `re.compile("...%d..." % N)` 的写法在正则内部
    含 `%` 时会抛 ValueError，只有真正执行到那一行才会暴露。
    tools 下的模块也一并导入：merge_dict 曾调用不存在的名字，那个 NameError
    在 main 里、compile() 抓不到，导入链是能提前暴露的最后关口。
    """
    sys.path.insert(0, str(PROJECT_DIR))
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        import patcher  # noqa: F401
        import agasar   # noqa: F401
        import scan_filter  # noqa: F401
        import merge_dict   # noqa: F401
        import verify       # noqa: F401
    except Exception as e:
        fail(f"导入核心模块失败：{type(e).__name__}: {e}")
        return
    ok("核心模块可正常导入（含 tools 工具链）")


def check_js_syntax():
    if shutil.which("node") is None:
        print("  [跳过] 未找到 node")
        return
    files = sorted(INJECT_DIR.glob("*.js")) + sorted(TOOLS_DIR.glob("*.js"))
    if not files:
        fail("没有找到任何 JavaScript 文件（目录结构可能已变）")
        return
    bad = 0
    for path in files:
        proc = subprocess.run(["node", "--check", str(path)], capture_output=True)
        if proc.returncode != 0:
            fail(f"{rel(path)} 语法错误：{proc.stderr.decode('utf-8', 'replace').strip()[:200]}")
            bad += 1
    if not bad:
        ok(f"{len(files)} 个 JavaScript 文件语法正确")


def check_dicts():
    origin = {}
    dups = []
    for path in sorted(DICT_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            fail(f"{path.name} JSON 非法：{e}")
            continue
        for name, typ in SECTIONS.items():
            if name in data and not isinstance(data[name], typ):
                fail(f"{path.name} 的 {name} 应为 {typ.__name__}")
        # 未知顶层键（_ 前缀的是注释用元数据）。拼错段落名会让整段词条永不生效，
        # 而 patcher 与自检都只读已知段落，不查这里就永远发现不了。
        for key in data:
            if key.startswith("_"):
                continue
            if key not in SECTIONS:
                fail(f"{path.name} 含未知段落 {key!r}（词条不会生效；拼写正确吗？）")
        # 规则结构校验。空前缀尤其危险：运行时用 text.indexOf(前缀) 判定，
        # 空串恒真，会把所有文本都替换成同一条译文。
        for i, r in enumerate(data.get("rules", [])):
            if not (isinstance(r, list) and len(r) == 2 and all(isinstance(x, str) for x in r)):
                fail(f"{path.name} rules[{i}] 结构异常（应为 [前缀, 译文]）")
            elif not r[0]:
                fail(f"{path.name} rules[{i}] 前缀为空字符串，会让所有文本都被替换")
        for i, r in enumerate(data.get("template", [])):
            if not (isinstance(r, list) and len(r) == 3 and all(isinstance(x, str) for x in r)):
                fail(f"{path.name} template[{i}] 结构异常（应为 [正则, 标志, 译文]）")
                continue
            try:
                re.compile(r[0], re.I if "i" in r[1] else 0)
            except re.error as e:
                fail(f"{path.name} template[{i}] 正则非法：{e}")
        # 空译值：运行时按真值判断（if (EXACT[trimmed])），空串词条等于不存在
        for section in ("exact", "words"):
            for key, val in data.get(section, {}).items():
                if not val:
                    fail(f"{path.name} {section} 键 {key!r} 译值为空——运行时按真值判断，空串词条不生效")
        for section in ("exact", "words", "menus"):
            for key in data.get(section, {}):
                first = origin.setdefault((section, key), path.name)
                if first != path.name:
                    dups.append(f"{section}:{key}（{first} → {path.name}）")
    if dups:
        fail(f"{len(dups)} 个词条跨文件重复（后加载的会覆盖前者）：" + "; ".join(dups[:5]))
    else:
        ok(f"词典共 {len(origin)} 个键，无跨文件重复；规则结构合法")

    # 模板正则的 JS 侧把关：它们真正跑在 new RegExp 里，Python re 能编译不代表
    # JS 能（如 Python 风格的 (?P<name>)）。一条在 JS 里编译失败会让注入后的
    # 整个翻译引擎崩溃，而 status 仍显示已汉化。
    if shutil.which("node"):
        tpls = []
        for path in sorted(DICT_DIR.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            for r in data.get("template", []):
                tpls.append([path.name, r[0], r[1]])
        script = (
            "var tpls = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf-8'));\n"
            "var bad = [];\n"
            "tpls.forEach(function (t) {\n"
            "  try { new RegExp(t[1], t[2] || ''); } catch (e) { bad.push([t[0], t[1], String(e.message)]); }\n"
            "});\n"
            "console.log(JSON.stringify(bad));\n"
        )
        data_file = PROJECT_DIR / "out" / "_tpl_js_check.json"
        data_file.parent.mkdir(parents=True, exist_ok=True)
        data_file.write_text(json.dumps(tpls, ensure_ascii=False), encoding="utf-8")
        js_bad = run_node("模板正则 JS 编译校验", "tpl_regex", script, [str(data_file)])
        if js_bad is not None:
            if js_bad:
                for fname, pat, msg in js_bad[:5]:
                    fail(f"{fname} 模板正则 JS 无法编译：{pat!r}（{msg}）")
            else:
                ok(f"模板正则 JS 编译通过（{len(tpls)} 条）")


def check_placeholders():
    provided = set(PROVIDED_RE.findall((PROJECT_DIR / "patcher.py").read_text(encoding="utf-8")))
    if not provided:
        fail("没能从 patcher.py 解析出任何 __AGZH_*__ 占位符（解析规则可能已失效）")
        return
    used = set()
    bad = 0
    for path in sorted(INJECT_DIR.glob("*.js")):
        in_file = patcher.data_placeholders(path.read_text(encoding="utf-8"))
        used |= in_file
        missing = in_file - provided
        if missing:
            fail(f"{path.name} 使用了 patcher.py 未提供的占位符：{sorted(missing)}")
            bad += 1
    # 反向也要查：patcher 提供但没人用，通常意味着占位符名字写错或模板漏引用
    unused = provided - used
    if unused:
        fail(f"patcher.py 提供了但没有任何模板使用的占位符：{sorted(unused)}")
        bad += 1
    if not bad:
        ok(f"注入模板占位符与 patcher.py 双向一致（{len(provided)} 个）")


def check_cases():
    path = TOOLS_DIR / "verify_cases.json"
    try:
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    except (json.JSONDecodeError, KeyError, OSError) as e:
        fail(f"verify_cases.json 结构异常：{e}")
        return
    bad = [c for c in cases
           if not (isinstance(c, list) and len(c) == 2 and all(isinstance(x, str) for x in c))]
    if bad:
        fail(f"verify_cases.json 有 {len(bad)} 条用例不是 [原文, 期望] 形式")
    else:
        ok(f"验证用例 {len(cases)} 条，结构正确")


def check_runtime_rules():
    """在真实 Node 引擎里跑 zh_runtime.js 的子串规则与耗时换算。

    这些规则改错过：`elapsedZh` 曾把 "3 hours" 变成 "3 小时our秒"，
    `"Working"` 子串规则曾把 "Working Directory: " 变成 "运行中 Directory: "。
    而 verify_cases.json 覆盖不到它们（那套用例只走 exact/函数式/模板/前缀/弱匹配，
    不实现 SUBSTR_RULES），所以这里单独把关。
    """
    # 整段截取到 translateText 为止（不用"找数组结尾 ];"的方式：那依赖缩进
    # 且容易截断，曾经把 SUBSTR_RULES 截成空数组，让断言在 TypeError 上失败
    # 而不是给出有用的差异）。
    block = runtime_slice("  function durationZh", "  function translateText", "运行时规则")
    if block is None:
        return

    script = (
        # 规则表由 patcher 注入，这里给空桩
        "var __AGZH_TEMPLATE_RULES__ = [];\n"
        "var __AGZH_PREFIX_RULES__ = [];\n"
        "var EXACT = {};\n"
        "var WORDS = {};\n" + block + "\n"
        "var CASES = " + json.dumps(RULE_CASES, ensure_ascii=False) + ";\n"
        "var fails = [];\n"
        "CASES.forEach(function (c) {\n"
        "  var t = c[0], out = t;\n"
        "  for (var i = 0; i < SUBSTR_RULES.length; i++) {\n"
        "    if (t.indexOf(SUBSTR_RULES[i][0]) === -1) continue;\n"
        "    var r = SUBSTR_RULES[i][1](t);\n"
        "    if (r !== t) { out = r; break; }\n"
        "  }\n"
        "  if (out !== c[1]) fails.push([t, c[1], out]);\n"
        "});\n"
        "console.log(JSON.stringify(fails));\n"
    )
    fails = run_node("运行时规则校验", "rules_check", script)
    if fails is None:
        return
    if fails:
        for src_text, want, got in fails[:5]:
            fail(f"规则输出不符：{src_text!r} 期望 {want!r} 实际 {got!r}")
    else:
        ok(f"运行时规则行为正确（{len(RULE_CASES)} 条断言）")


def check_noise_parity():
    """校验 Python 与 JS 两侧的噪声判定仍然等价。

    README 声称"两侧等价性有实测校验"，这里就是那道校验：JS 侧代码直接从
    inject/zh_runtime.js 文本里截取（不是抄一份），所以测的是真正会跑在
    渲染进程里的实现。任何单边改动都会在这里失败。
    """
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        from scan_filter import is_noise
    except ImportError as e:
        fail(f"无法导入 scan_filter.is_noise：{e}")
        return

    # 两段真实源码拼接：nullProto 的定义（isNoise 用的 KEYNAMES/JS_KEYWORDS
    # 都经它包装）+ 收集器内部段。注意第二段必须起于 if (SCAN) { 之内、
    # 终于块内——跨越块边界会截出括号不配平的半截脚本。
    null_proto = runtime_slice("  function nullProto", "  // ---------- 收集器", "nullProto 实现")
    noise_block = runtime_slice("    var KEYNAMES", "    var pending = new Map();", "isNoise 实现")
    if null_proto is None or noise_block is None:
        return

    script = null_proto + "\n" + noise_block + (
        'var fs=require("fs");'
        'var samples=JSON.parse(fs.readFileSync(process.argv[2],"utf-8"));'
        'console.log(JSON.stringify(samples.map(function(s){return isNoise(String(s).trim());})));'
    )
    data_file = PROJECT_DIR / "out" / "_noise_parity.json"
    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_text(json.dumps(NOISE_SAMPLES, ensure_ascii=False), encoding="utf-8")

    js_results = run_node("噪声等价性校验", "noise_parity", script, [str(data_file)])
    if js_results is None:
        return

    diffs = [(s, is_noise(s), j) for s, j in zip(NOISE_SAMPLES, js_results) if is_noise(s) != j]
    if diffs:
        fail(f"噪声判定两侧不一致 {len(diffs)} 条：" +
             "; ".join(f"{s!r} py={p} js={j}" for s, p, j in diffs[:5]))
    else:
        ok(f"噪声判定两侧等价（{len(NOISE_SAMPLES)} 条语料）")


def check_privacy():
    me = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    # CI runner / 通用账号名没有检查意义，反而容易误报
    if me.lower() in {"runner", "user", "root", "admin", "administrator", "github"}:
        me = ""
    hits = []
    for path in sorted(PROJECT_DIR.rglob("*")):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        if "out" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in EMAIL_RE.finditer(text):
            # 域必须精确相等：endswith 判定会让任何以白名单域结尾的伪造域绕过检查
            if m.group(0).rpartition("@")[2].lower() in SAFE_EMAIL_DOMAINS:
                continue
            hits.append(f"{rel(path)}：邮箱 {m.group(0)}")
        for m in USER_PATH_RE.finditer(text):
            if m.group(1).lower() not in SAFE_USERS:
                hits.append(f"{rel(path)}：本机路径 {m.group(0)}")
        if len(me) >= 4 and me.lower() in text.lower():
            hits.append(f"{rel(path)}：出现本机用户名 {me}")
    if hits:
        fail("疑似隐私泄漏：" + "; ".join(hits[:5]))
    else:
        note = f"（已按用户名 {me} 检查）" if me else "（未取到有效用户名，仅查邮箱与路径）"
        ok("未发现邮箱、本机用户名或本地用户目录路径" + note)


def main():
    print("== 语法 ==")
    check_python_syntax()
    check_imports()
    check_js_syntax()
    print("== 词典 ==")
    check_dicts()
    print("== 注入模板 ==")
    check_placeholders()
    print("== 噪声判定 ==")
    check_noise_parity()
    print("== 运行时规则 ==")
    check_runtime_rules()
    print("== 验证用例 ==")
    check_cases()
    print("== 隐私 ==")
    check_privacy()
    print()
    if problems:
        print(f"自检未通过：{len(problems)} 项问题")
        return 1
    print("自检全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
