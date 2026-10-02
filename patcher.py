# -*- coding: utf-8 -*-
"""antigravity-zh — Google Antigravity 桌面版中文汉化补丁工具。

用法:
    python patcher.py patch   [--dir 安装目录] [--yes] [--launch]
    python patcher.py scan    [--dir 安装目录] [--yes] [--launch]
    python patcher.py restore [--dir 安装目录] [--yes]
    python patcher.py status  [--dir 安装目录]
    python patcher.py missing [--dir 安装目录] [--top N] [--keep]

不带子命令即按"一键汉化并启动"处理（等价 patch --yes --launch），供双击使用；
全局参数 --debug 让出错时打印完整 traceback。

原理（与 MIMICTE/Antigravity-zh-CN 同源，词典外置便于自行维护）:
  Antigravity 的界面由 language_server.exe 本地服务动态提供，无静态文件可改，
  因此解包 app.asar 到 resources/app（Electron 优先加载该目录），向
  dist/preload.js 注入 MutationObserver 翻译引擎 + 外置词典，向 dist/main.js
  注入原生菜单汉化与 scan 收集器。
"""
import argparse
import http.client
import json
import os
import re
import shutil
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from agasar import Asar, AsarError

# 打包成 exe 或重定向到非 UTF-8 控制台时，中文输出不应让程序崩掉
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except (OSError, ValueError):
            pass

PROJECT_DIR = Path(__file__).resolve().parent


def _resource_dir(name):
    """定位 inject/ 与 dict/。

    PyInstaller 打包后内嵌资源被解压到 sys._MEIPASS；若 exe 同级目录放了
    inject/ 或 dict/，优先用它——这样 exe 用户依然能自行增删词典，
    而不用重新打包。
    """
    if getattr(sys, "frozen", False):
        beside = Path(sys.executable).resolve().parent / name
        if beside.is_dir():
            return beside
        return Path(getattr(sys, "_MEIPASS", PROJECT_DIR)) / name
    return PROJECT_DIR / name


def _output_dir():
    """产物目录：源码模式在项目下，打包后写在 exe 旁边（写不动则退回 LOCALAPPDATA）。"""
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else PROJECT_DIR
    candidate = base / "out"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return candidate
    except OSError:
        # 兜底路径本身也可能不可写（LOCALAPPDATA 为空 + exe 目录只读），而本函数
        # 在导入期就被调用，抛异常会让程序直接吐栈退出（双击表现为一闪而过）。
        for root in (os.environ.get("LOCALAPPDATA"), tempfile.gettempdir()):
            if not root:
                continue
            try:
                fallback = Path(root) / "antigravity-zh" / "out"
                fallback.mkdir(parents=True, exist_ok=True)
                return fallback
            except OSError:
                continue
        return base / "out"   # 确实没有可写位置，留给后续操作去报错


INJECT_DIR = _resource_dir("inject")
DICT_DIR = _resource_dir("dict")
OUT_DIR = _output_dir()

# 复用 tools/scan_filter.py 的噪声判定（打包时随 --add-data 一起内嵌；
# 缺失时退回"白名单"式的保守判定，不让 missing 直接崩掉）。
try:
    sys.path.insert(0, str(_resource_dir("tools")))
    from scan_filter import is_noise as _is_noise
except Exception:
    # 不只 ImportError：scan_filter.py 若有语法/名字错误也会走到这里。
    # 降级为"不做噪声过滤"，而不是让 patcher 在导入期直接崩掉。
    _is_noise = None

DEFAULT_INSTALL = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity"

ASAR_REL = Path("resources") / "app.asar"
BACKUP_REL = Path("resources") / "app.asar.zh-orig"
UNPACKED_REL = Path("resources") / "app.asar.unpacked"
APP_REL = Path("resources") / "app"
MARKER_NAME = ".agzh-patched.json"

LOG_REL = Path(os.environ.get("APPDATA", "")) / "Antigravity" / "logs" / "main.log"

# missing 子命令的候选启发式规则。
# UI_TEXT_RE 从 bundle 里提取字符串字面量，必须正确处理转义：早先用
# "([^"\\]{4,300})" 时，一旦遇到含 \" 的字符串，引号配对就整体错位——之后提取到的
# 是 ",description:" 这类碎片而非字符串内容，导致成片的界面文案被系统性漏报。
# 长度上限 300 是实测结果：旧上限 140 漏掉了 141 字符的设置项说明。
MAX_TEXT_LEN = 300
# 起始引号前面不能是单词字符/引号/反斜杠：否则前一个字符串的**结束引号**
# 会被当成起始引号，匹配出 ",description:" 这类跨字符串碎片（实测占命中数的 45%）。
UI_TEXT_RE = re.compile(r'(?<![\w"\\])"((?:[^"\\]|\\.){4,' + str(MAX_TEXT_LEN) + r'})"')
UI_OK_RE = re.compile(r"^[A-Z][A-Za-z0-9 ,'()/.?:+!&%-]{8," + str(MAX_TEXT_LEN) + r"}$")
UI_BAD = ("_", "=", "$", "{", "}", ";", "()", "=>", "console")
# 明显属于开发者错误/内部实现的信息，单独归到 suspect 组，不混进待翻译清单
DEV_NOISE = ("must not", "cannot be", "Cannot ", "is not allowed", "failed to resolve", "resolve timeout",
             "reducer", "actor ", "frames", "iframe", "Dogfood", "ABFS", "CitC", "google3",
             "expected ", "Unexpected ", "Invalid ", "Unsupported ", "deprecated", "stack trace",
             "background color", "foreground color", "The color of", "hover background",
             "must be ", "should be ", "is not a ", "does not exist",
             # 实机 bundle 里混入的框架/内部错误信息（v2.15.1 实测，归入 suspect 组）
             "No character metrics", "No function handler", "No insert provided", "No main workspace URI",
             "No native storage bridge", "No notFoundComponent", "No shared path provided",
             "No step index", "No steps with timestamps", "No model specified for", "No matching ",
             "Run command step not found", "Search skipped because", "streaming request bodies",
             "should not be mutated", "pruned to save", "allowProposedApi",
             "already an extension with this id", "Not referenced by any feature",
             "requires one of the known fields")

RUNTIME_MARKER = "// >>> antigravity-zh runtime"
MAINPATCH_MARKER = "// >>> antigravity-zh main patch"

# 注入模板里的数据占位符：只认"等号右边的 __AGZH_*__"。
# 模板里的运行时标志（window.__AGZH_LOADED__ / process.__AGZH_LOADED__）不是数据
# 占位符，若按 __AGZH_[A-Z_]+__ 泛匹配会被误判为"未填充"而中止打补丁。
DATA_PLACEHOLDER_RE = re.compile(r'=\s*"?(__AGZH_[A-Z_]+__)"?')

# 正则元字符：用于从模板规则里截出"字面前缀"
RE_META = re.compile(r"[\\^$.*+?()\[\]{}|]")

# 明显是技术常量/代码枚举的形态。只服务于 missing 的候选筛选（把噪声推进
# other 组），不参与运行时收集，所以不必与 scan_filter.is_noise 等价。
TECH_FORM_RE = re.compile(
    r"^(?:[A-Z0-9_]{4,}|[A-Za-z0-9]+(?:[A-Z][a-z0-9]+)+|[a-z0-9]+(?:-[a-z0-9]+)+)$")
TECH_WORDS = ("href", "src", "innerhtml", "stylesheet", "base64", "charset", "px ", "rgba(")


def _looks_technical(s):
    """判断候选是否更像代码常量/标识符而非界面文案。"""
    # 全大写：常量或枚举短语（ANTHROPIC、APPLET BASE …）。
    # 但 "NEW CONVERSATION" 这类全大写界面文案要豁免——判成技术串后，
    # 会再配合"短串直接丢"的规则让它从 missing 里彻底消失。
    if s.upper() == s and any(c.isalpha() for c in s) and len(s) > 6:
        return len(s.split()) > 3
    if " " not in s and TECH_FORM_RE.match(s):
        return True                      # camelCase / kebab-case / 纯标识符
    low = s.lower()
    if " " not in s and any(w in low for w in TECH_WORDS):
        return True
    # SVG path 数据（"M12 3c-4.97 0-9 4.03…"）：以路径命令开头且数字/符号密集。
    # bundle 里内联的图标路径会被提取成候选，实测某个区间里占了 21%。
    if len(s) > 40 and re.match(r"^[MmLlHhVvCcSsQqTtAaZz][\d\s.,-]", s):
        if len(re.findall(r"[\d.,-]", s)) > len(s) * 0.35:
            return True
    return False


def literal_prefix(pattern):
    """取模板规则的字面前缀（首个正则元字符之前的部分）。

    bundle 里常把 "Add New Handler to " + 变量 拆成两个字符串常量，
    静态提取只能看到片段。用字面前缀判断"该片段已被某条模板规则覆盖"，
    避免每次 missing 都刷出一批其实已翻译的假告警。
    """
    p = pattern[1:] if pattern.startswith("^") else pattern
    m = RE_META.search(p)
    return p[: m.start()] if m else p

# 与版本强耦合的个别字面量替换。这是唯一无法靠词典/注入覆盖的地方：
# 目标文件在独立的 WebContentsView 里（data: URL），翻译引擎注入不到，
# 只能直接改源文件。目标字符串不存在时 patch 会提示出来，便于发现版本漂移。
# 沿革：dist/loadingOverlay.js 原本是 ">Loading Antigravity<" 文本，2.18.1 起
# 改成纯 SVG logo（无文字），该条替换已随之移除。
TARGETED_REPLACES = {
    Path("dist") / "tray.js": [
        # 托盘"运行中智能体数"是运行时拼接的：updateTrayAgentCount() 直接改
        # countItem.label，绕过了 Menu.buildFromTemplate 钩子，词典对它无效，
        # 只能做字面量替换。托盘其余项（Open Antigravity / Quit）走词典。
        ("(count > 0 ? `${count}` : 'No') +\n"
         "                    ' agent' +\n"
         "                    (count === 1 ? '' : 's') +\n"
         "                    ' running';",
         "count > 0 ? `${count} 个智能体正在运行` : '没有正在运行的智能体';"),
    ],
}


def find_install(user_dir):
    install = Path(user_dir or DEFAULT_INSTALL).resolve()
    exe = install / "Antigravity.exe"
    resources = install / "resources"
    if not exe.exists():
        raise SystemExit(f"[错误] 未找到 {exe}，可用 --dir 指定 Antigravity 安装目录")
    if not resources.exists():
        raise SystemExit(f"[错误] 目录结构异常，缺少 {resources}")
    return install


def asar_source(install):
    """返回补丁数据源：优先当前 app.asar，其次原版备份。"""
    asar = install / ASAR_REL
    backup = install / BACKUP_REL
    if asar.exists():
        return asar, "当前 app.asar"
    if backup.exists():
        return backup, "原版备份 app.asar.zh-orig"
    raise SystemExit("[错误] 找不到 app.asar，也没有备份，无法继续")


def read_version(source):
    try:
        with Asar(source) as a:
            return a.version()
    except (AsarError, ValueError, struct.error, OSError) as e:
        # ValueError 覆盖 json.JSONDecodeError 与 UnicodeDecodeError
        raise SystemExit(f"[错误] 读取 asar 版本失败（文件损坏或不是 asar）: {e}")


# Antigravity 的伴生进程：taskkill 掉主进程后 language_server / webm_encoder
# 仍会在释放文件句柄，紧接着的目录删除或改名可能因此失败。
APP_PROCESSES = ("Antigravity.exe", "language_server.exe", "webm_encoder.exe")


def running_processes():
    """返回仍在运行的 Antigravity 相关进程名（空列表表示全部已退出）。"""
    # tasklist 在中文 Windows 上输出 GBK，这里只按字节匹配进程名，避免解码问题
    try:
        result = subprocess.run(["tasklist"], capture_output=True, check=False)
    except OSError:
        return []
    out = result.stdout or b""
    return [name for name in APP_PROCESSES if name.encode() in out]


def _retry(func, attempts=5, delay=0.6):
    """对可能因文件被占用而短暂失败的 IO 操作做重试。"""
    last = None
    for i in range(attempts):
        try:
            return func()
        except OSError as e:
            last = e
            if i < attempts - 1:          # 最后一轮失败直接抛，不再空等
                time.sleep(delay)
    if last is None:
        raise ValueError("attempts 必须为正整数")
    raise last


def ensure_closed(auto_yes):
    running = running_processes()
    if not running:
        return
    print(f"[提示] Antigravity 正在运行（{'、'.join(running)}），补丁需要先关闭它。")
    if not auto_yes:
        answer = input("现在关闭 Antigravity 并继续? [Y/n] ").strip().lower()
        if answer and not answer.startswith("y"):   # 只有明确同意才继续
            raise SystemExit("[中止] 请关闭 Antigravity 后重试。")
    # 伴生进程（language_server / webm_encoder）同样占着 resources/app 下的文件句柄，
    # 一并结束；它们没有可点的退出入口，只杀主进程会让用户卡在"仍有进程未退出"。
    for name in APP_PROCESSES:
        r = subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, check=False)
        if r.returncode != 0:
            print(f"[提示] 结束 {name} 时 taskkill 返回 {r.returncode}（可能已退出或权限不足）")
    # 等最多 10 秒让句柄释放。超时不中止：真正锁住 resources/app 的是主进程，
    # 残留的伴生进程交给后续的 _retry 与错误提示兜底。
    for _ in range(20):
        time.sleep(0.5)
        if not running_processes():
            break
    leftover = running_processes()
    if leftover:
        print(f"[提示] 仍有进程未退出（{'、'.join(leftover)}），继续尝试；若卡住请手动结束它们")
    else:
        print("[完成] 已关闭 Antigravity。")


def load_dicts():
    """合并 dict/*.json 为一份 {exact, words, rules, template, menus}。

    文件名排序，后者覆盖前者。同名键被覆盖时给出提示——这类"改了却不生效"
    的问题（例如新译文写进 common.json，却被 ui_extra.json 的同名旧条目盖掉）
    最难排查，所以在合并阶段就点出来。
    """
    merged = {"exact": {}, "words": {}, "rules": [], "template": [], "menus": {}}
    origin = {}
    dupes = []
    count = 0
    for path in sorted(DICT_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        # 结构校验：模板/前缀规则写错元数会在运行时才炸，这里给出文件与序号
        for i, r in enumerate(data.get("rules", [])):
            if not (isinstance(r, list) and len(r) == 2 and all(isinstance(x, str) for x in r)):
                raise SystemExit(f"[错误] {path.name} 的 rules[{i}] 结构应为 [前缀, 译文]：{r!r}")
        for i, r in enumerate(data.get("template", [])):
            if not (isinstance(r, list) and len(r) == 3 and all(isinstance(x, str) for x in r)):
                raise SystemExit(f"[错误] {path.name} 的 template[{i}] 结构应为 [正则, 标志, 译文]：{r!r}")
        for section in ("exact", "words", "menus"):
            for key in data.get(section, {}):
                first = origin.setdefault((section, key), path.name)
                if first != path.name:
                    dupes.append(f"{key}（{first} → {path.name}）")
        merged["exact"].update(data.get("exact", {}))
        merged["words"].update(data.get("words", {}))
        merged["rules"].extend(data.get("rules", []))
        merged["template"].extend(data.get("template", []))
        merged["menus"].update(data.get("menus", {}))
        count += 1
    if count == 0:
        raise SystemExit(f"[错误] {DICT_DIR} 下没有词典 JSON")
    if dupes:
        shown = "; ".join(dupes[:5]) + ("…" if len(dupes) > 5 else "")
        print(f"[提示] {len(dupes)} 个词条在多份词典中重复（后加载的生效）: {shown}")
    return merged


def dict_summary(dicts):
    """词典规模的一行摘要（词条数不含规则）。"""
    return (f"精确 {len(dicts['exact'])} | 弱匹配 {len(dicts['words'])} | "
            f"前缀规则 {len(dicts['rules'])} | 模板规则 {len(dicts['template'])} | "
            f"菜单 {len(dicts['menus'])}")


def dict_counts(dicts):
    """返回 (词条数, 规则数)：词条 = 精确 + 弱匹配 + 菜单，规则 = 前缀 + 模板。"""
    return (len(dicts["exact"]) + len(dicts["words"]) + len(dicts["menus"]),
            len(dicts["rules"]) + len(dicts["template"]))


def build_js(template_name, replacements):
    """用真实数据填充模板里的数据占位符。

    双向检查模板与调用方是否脱节：模板引用了没提供的数据、或提供了模板里
    没有的占位符，都在打补丁前报错，而不是注入半成品让应用起不来。
    """
    text = (INJECT_DIR / template_name).read_text(encoding="utf-8")
    unknown = set(DATA_PLACEHOLDER_RE.findall(text)) - set(replacements)
    if unknown:
        raise SystemExit(f"[错误] {template_name} 含未提供的占位符: {', '.join(sorted(unknown))}")
    for key, value in replacements.items():
        if key not in text:
            raise SystemExit(f"[错误] {template_name} 缺少占位符 {key}，模板与 patcher 不匹配")
        text = text.replace(key, value)
    return text


def inject_block(app_dir, rel_path, block, marker, label):
    """把代码块追加到文件末尾；若已存在同名块则截断后重写（支持词典热更新）。

    返回是否注入成功。注入失败却照旧写"已汉化"标记是最坏的情况：
    界面全英文，而 status 报告一切正常。
    """
    file_path = app_dir / rel_path
    if not file_path.exists():
        print(f"[跳过] 未找到 {rel_path}，无法注入{label}")
        return False
    content = file_path.read_text(encoding="utf-8")
    if marker in content:
        content = content[: content.index(marker)].rstrip()
        action = "热更新"
    else:
        action = "注入"
    file_path.write_text(content + "\n\n" + marker + "\n" + block + "\n", encoding="utf-8")
    print(f"[成功] {action}{label} -> {rel_path}")
    return True


def apply_targeted_replaces(app_dir):
    """按版本做少量字面量替换；未匹配的替换会提示出来，便于发现版本漂移。"""
    for rel, pairs in TARGETED_REPLACES.items():
        target = app_dir / rel
        if not target.exists():
            continue
        content = original = target.read_text(encoding="utf-8")
        missed = []
        for old, new in pairs:
            if old in content:
                content = content.replace(old, new)
            else:
                missed.append((old.splitlines() or [old])[0][:48])
        if content != original:
            target.write_text(content, encoding="utf-8")
            print(f"[成功] 字面量替换 -> {rel}")
        if missed:
            print(f"[提示] {rel} 有 {len(missed)} 处字面量未匹配"
                  f"（该版本文案可能已变）: {' | '.join(missed)}")


def do_patch(install, scan, auto_yes, launch):
    ensure_closed(auto_yes)

    source, source_note = asar_source(install)
    version = read_version(source)
    print(f"[信息] Antigravity 版本 {version}（数据源: {source_note}）")

    asar_path = install / ASAR_REL
    backup_path = install / BACKUP_REL
    app_dir = install / APP_REL

    # 1. 保存/刷新原版备份（asar 存在说明是未打补丁或刚更新过的官方原版）
    if asar_path.exists():
        try:
            _retry(lambda: os.replace(asar_path, backup_path))
        except OSError as e:
            raise SystemExit(f"[错误] 备份 app.asar 失败：{e}\n"
                             "        Antigravity 可能仍在运行，请彻底退出后重试。")
        print("[完成] 原版已备份: app.asar -> app.asar.zh-orig")

    # 2. 全新解包（旧 app 目录整体移除，避免跨版本残留）
    if app_dir.exists():
        try:
            _retry(lambda: shutil.rmtree(app_dir))
        except OSError as e:
            # 此时 app.asar 已改名为备份、app 目录只删了一半，属于最危险的状态，
            # 必须给出可执行的恢复路径而不是抛栈。
            raise SystemExit(
                f"[错误] 无法删除 {APP_REL}：{e}\n"
                "        Antigravity 可能仍在运行或目录被占用，请彻底退出后重试。\n"
                "        若 Antigravity 已无法启动，运行 python patcher.py restore 还原官方原版。")
    with Asar(backup_path) as a:
        count, total = a.extract_to(app_dir)
    print(f"[完成] 解包 {count} 个文件 / {total} 字节 -> {APP_REL}")

    # 3. 并入 app.asar.unpacked（native 模块等不在 asar 内的文件）
    unpacked = install / UNPACKED_REL
    if unpacked.exists():
        shutil.copytree(unpacked, app_dir, dirs_exist_ok=True)
        print("[完成] 并入 app.asar.unpacked 内容")

    # 4. 注入
    dicts = load_dicts()
    entries, rules = dict_counts(dicts)
    print(f"[信息] 词典规模: {dict_summary(dicts)}")
    print(f"[信息] 合计词条 {entries} 条 / 规则 {rules} 条")

    runtime = build_js("zh_runtime.js", {
        "__AGZH_EXACT__": json.dumps(dicts["exact"], ensure_ascii=False, sort_keys=True),
        "__AGZH_WORDS__": json.dumps(dicts["words"], ensure_ascii=False, sort_keys=True),
        "__AGZH_PREFIX_RULES__": json.dumps(dicts["rules"], ensure_ascii=False),
        "__AGZH_TEMPLATE_RULES__": json.dumps(dicts["template"], ensure_ascii=False),
        "__AGZH_SCAN__": "true" if scan else "false",
    })
    mainpatch = build_js("zh_main_patch.js", {
        "__AGZH_MENUS__": json.dumps(dicts["menus"], ensure_ascii=False, sort_keys=True),
        "__AGZH_SCAN__": "true" if scan else "false",
        "__AGZH_COLLECTOR_FILE__": (OUT_DIR / "untranslated.json").as_posix(),
    })

    injected = {
        "preload": inject_block(app_dir, Path("dist") / "preload.js", runtime, RUNTIME_MARKER, "界面翻译引擎"),
        "main": inject_block(app_dir, Path("dist") / "main.js", mainpatch, MAINPATCH_MARKER, "主进程补丁"),
    }
    if not all(injected.values()):
        raise SystemExit("[错误] 关键文件注入失败（客户端结构可能已变），补丁未生效；"
                         "请反馈 Antigravity 版本号以便适配")
    apply_targeted_replaces(app_dir)

    # scan 模式归档上一轮收集结果（收集文件是累加的，不归档会让旧计数一直滞留）。
    # 放在注入成功之后：否则注入失败时上一轮收集已被改名，而补丁并没有生效。
    if scan:
        collector = OUT_DIR / "untranslated.json"
        if collector.exists():
            _retry(lambda: os.replace(collector, OUT_DIR / "untranslated.prev.json"))
            print("[完成] 上一轮收集结果已归档 -> out/untranslated.prev.json")

    # 5. 写补丁标记
    marker = {
        "version": version,
        "scan": scan,
        "entries": entries,
        "rules": rules,
        "injected": injected,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (app_dir / MARKER_NAME).write_text(
        json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[完成] {'scan' if scan else 'patch'} 补丁完成（基于 v{version}）。")
    if scan:
        print("------- scan 模式使用说明 -------")
        print("1. 启动 Antigravity，把要汉化的界面各点一遍（设置、右键菜单、对话框…）")
        print(f"2. 未翻译英文会实时记录到 {OUT_DIR / 'untranslated.json'}")
        print("3. 运行 python tools/merge_dict.py 逐条补充翻译")
        print("4. 重新运行 python patcher.py patch 应用新词典")
        print("--------------------------------")
    if launch:
        launch_app(install)
    else:
        print("[提示] 现在可以启动 Antigravity 查看效果。")


def _pid_alive(pid):
    try:
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                                capture_output=True, check=False)
    except OSError:
        return False
    return str(pid).encode() in (result.stdout or b"")


def launch_app(install):
    """启动 Antigravity，并确认它真的起来了。

    直接 Popen 在某些环境下（受限会话、作业对象限制）会让 Electron 静默退出，
    表现为"补丁说已启动但界面没出现"；那种情况改由资源管理器（桌面 shell）代为启动。
    判定用 PID 而不是进程名：冷启动可能超过 5 秒，按名字判定会误判失败并再启一个实例。
    """
    exe = install / "Antigravity.exe"
    pid = None
    try:
        proc = subprocess.Popen([str(exe)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_GROUP
        pid = proc.pid
    except OSError as e:
        print(f"[提示] 直接启动失败：{e}")

    if pid is not None:
        for _ in range(30):        # 最多等 15 秒
            time.sleep(0.5)
            if _pid_alive(pid):
                print("[完成] 已启动 Antigravity。")
                return

    print("[提示] 直接启动未成功，改由资源管理器启动…")
    try:
        subprocess.Popen(["explorer.exe", str(exe)])
    except OSError as e:
        print(f"[提示] 无法通过资源管理器启动：{e}")
        print("[提示] 请手动双击 Antigravity 图标运行。")
        return
    for _ in range(30):
        time.sleep(0.5)
        if running_processes():
            print("[完成] 已启动 Antigravity。")
            return
    print("[提示] 未能确认 Antigravity 已启动，请手动双击图标运行。")


def do_restore(install, auto_yes):
    ensure_closed(auto_yes)
    asar_path = install / ASAR_REL
    backup_path = install / BACKUP_REL
    app_dir = install / APP_REL

    # 顺序很重要：先恢复 asar，再删汉化目录。
    # 反过来的话，一旦目录删到一半失败（文件被占用），安装目录会同时失去
    # app.asar 和完整的 resources/app，Antigravity 直接起不来且无从恢复。
    if asar_path.exists():
        # 当前 asar 已是官方版本（例如刚被官方更新覆盖），无需还原
        print("[信息] app.asar 已在原位，无需还原")
        if backup_path.exists():
            print(f"[提示] 仍保留旧备份 {BACKUP_REL.name}，确认无碍后可手动删除")
    elif backup_path.exists():
        try:
            _retry(lambda: os.replace(backup_path, asar_path))
        except OSError as e:
            raise SystemExit(f"[错误] 还原 app.asar 失败：{e}")
        print("[完成] 已还原官方 app.asar")
    else:
        raise SystemExit("[错误] 既没有 app.asar 也没有备份，无法还原")

    if app_dir.exists():
        try:
            _retry(lambda: shutil.rmtree(app_dir))
        except OSError as e:
            # 此时 asar 已经回到原位，应用可以正常启动，所以这里只提示不致命
            raise SystemExit(f"[错误] 无法删除 {APP_REL}：{e}\n"
                             "        app.asar 已还原，Antigravity 可直接启动；"
                             "残留目录可在彻底退出应用后手动删除。")
        print("[完成] 已移除汉化目录 resources/app")
    else:
        print("[信息] 未发现汉化目录。")
    print("[完成] restore 结束，Antigravity 已恢复原版状态。")


def do_status(install):
    source, source_note = asar_source(install)
    current = read_version(source)
    app_dir = install / APP_REL
    marker_path = app_dir / MARKER_NAME

    print(f"安装目录   : {install}")
    print(f"当前版本   : {current}（{source_note}）")
    dicts = load_dicts()
    print(f"词典规模   : {dict_summary(dicts)}")

    if not marker_path.exists():
        print("补丁状态   : 未汉化（运行 python patcher.py patch）")
        return
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    print(f"补丁状态   : 已汉化 @ v{marker.get('version')}（{marker.get('time')}）")
    if marker.get("entries") is not None:
        print(f"补丁词典   : 词条 {marker.get('entries')} 条 / 规则 {marker.get('rules', '?')} 条")
    # 注入结果在打补丁时已写进 marker，这里读出来核对，
    # 避免"标记写着已汉化、实际某个文件没注入成功"这种最难排查的状态。
    injected = marker.get("injected")
    if isinstance(injected, dict) and not all(injected.values()):
        bad = "、".join(k for k, v in injected.items() if not v)
        print(f"[警告] 以下注入未成功：{bad} —— 请重跑 python patcher.py patch")
    if marker.get("version") != current:
        print("[提示] Antigravity 已更新，当前补丁基于旧版本 —— 请重跑 python patcher.py patch")
    elif marker.get("scan"):
        print("[提示] 当前处于 scan 收集模式 —— 收集完成后请重跑 python patcher.py patch")
    else:
        print("一致性     : 补丁与当前版本匹配，一切正常")


def do_missing(install, top, keep):
    """从当前运行的界面 bundle 提取 UI 文案，列出词典尚未覆盖的条目。"""
    source, _ = asar_source(install)
    version = read_version(source)

    if not LOG_REL.exists():
        raise SystemExit(f"[错误] 找不到日志 {LOG_REL}，请先启动一次 Antigravity")
    log_text = LOG_REL.read_text(encoding="utf-8", errors="replace")
    ports = re.findall(r"Local:\s+https://127\.0\.0\.1:(\d+)/", log_text)
    if not ports:
        raise SystemExit("[错误] 日志里没有本地服务端口记录，请先启动 Antigravity")

    bundle_path = OUT_DIR / "main-bundle.js"
    # 本地服务用自签名证书，跳过校验；用 urllib 而不是调用系统 curl，
    # 免去对 curl.exe 的依赖（与"纯标准库"的定位一致）。
    #
    # 日志是跨多次启动追加的，最后一条端口未必属于当前实例；端口也可能已被
    # 别的本地服务占用。所以从最新的往前逐个探测，并校验响应确实像 JS bundle。
    ctx = ssl._create_unverified_context()
    data = None
    used_port = None
    tried = []
    for candidate in reversed(ports[-5:]):
        tried.append(candidate)
        try:
            with urllib.request.urlopen(f"https://127.0.0.1:{candidate}/main.js",
                                        context=ctx, timeout=8) as resp:
                ctype = resp.headers.get("content-type", "") or ""
                body = resp.read()
        except (OSError, http.client.HTTPException):
            # HTTPException（BadStatusLine / IncompleteRead 等）不继承 OSError：
            # 端口被别的本地服务占用并返回畸形响应时，要继续试下一个而不是抛栈退出。
            continue
        head = body[:4096]
        # 三重校验：体积、content-type、内容特征。只靠体积+关键词会把
        # 404 页或别的服务返回的 JS 当成界面 bundle 落盘。
        if (len(body) > 100_000 and "javascript" in ctype.lower()
                and (b"function" in head or b"=>" in head)):
            data, used_port = body, candidate
            break
    if data is None:
        raise SystemExit(f"[错误] 无法从日志记录的端口取到界面 bundle（已尝试 {', '.join(tried)}）\n"
                         "        请确认 Antigravity 正在运行")
    bundle_path.write_bytes(data)
    print(f"[完成] 已从本地服务（端口 {used_port}）下载界面 bundle：{len(data) // 1024} KB")

    text = bundle_path.read_text(encoding="utf-8", errors="replace")
    candidates = set()
    for m in UI_TEXT_RE.finditer(text):
        s = m.group(1)
        if not UI_OK_RE.match(s):
            continue
        # 片段特征：带首尾空格或以连字符结尾（如 "Download "、"Start Best-of-"），
        # 这些是 bundle 里被拼接的字符串碎片，不是完整文案
        if s != s.strip() or s.endswith("-"):
            continue
        if any(bad in s for bad in UI_BAD):
            continue
        # 短串（1~2 个词）改走噪声判定。
        # 早先用"必须以界面动词开头"的白名单过滤，结果把 "Action required"、
        # "Agent Edits"、"About embedding" 这类真实按钮文案一并丢掉，
        # 而它们恰恰是新版本最可能新增的形态。
        if s.count(" ") < 2 and (_looks_technical(s) or (_is_noise is not None and _is_noise(s))):
            continue
        candidates.add(s)

    dicts = load_dicts()
    known = set(dicts["exact"])
    templates = []
    for p, f, _t in dicts["template"]:
        try:
            templates.append(re.compile(p, re.I if "i" in (f or "") else 0))
        except re.error as e:
            # 词典里的模板正则是给 JS new RegExp 用的，可能含 Python 不认的语法
            print(f"[提示] 模板规则 Python 无法编译，已跳过：{p}（{e}）")
    prefixes = [r[0] for r in dicts["rules"]]
    tpl_prefixes = [p for p in (literal_prefix(r[0]) for r in dicts["template"]) if len(p) >= 8]

    ui, suspect, other = [], [], []
    for s in sorted(candidates):
        if s in known or any(r.match(s) for r in templates) or any(p in s for p in prefixes):
            continue
        # 已被模板规则覆盖的拼接片段（bundle 里的片段可能比模板前缀少一个尾随空格）
        if len(s) >= 8 and any(s.startswith(p) or p.startswith(s) for p in tpl_prefixes):
            continue
        if any(noise in s for noise in DEV_NOISE):
            suspect.append(s)
        elif _looks_technical(s) or (_is_noise is not None and _is_noise(s)):
            other.append(s)         # 包名/路径/代码常量等技术形态
        else:
            # 默认按界面文案对待。早先只有"以界面动词开头"的候选才进 ui 组，
            # 而这类常见动词早已被词典覆盖并在上面被过滤掉，剩下的新文案
            # 天生不会落在白名单里 —— ui 组因此长期恒为 0，让人误以为已全覆盖。
            ui.append(s)

    out = OUT_DIR / "missing.json"
    out.write_text(json.dumps({"version": version, "ui": ui, "suspect": suspect, "other": other},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[完成] 疑似界面文案 {len(ui)} 条 | 疑似开发者信息 {len(suspect)} 条 | 技术形态 {len(other)} 条")
    print(f"        全部写入 {out}（ui 是主清单，另两组供交叉核对）")
    for s in ui[:top]:
        print("  " + s[:110])
    if len(ui) > top:
        print(f"  …（其余 {len(ui) - top} 条见 missing.json 的 ui 组）")
    if not keep:
        bundle_path.unlink(missing_ok=True)
        print("[提示] 已删除下载的 bundle（加 --keep 可保留复查）")
    print("[提示] 挑出要翻译的条目补进 dict/*.json 后，重跑 python patcher.py patch")


def _pause():
    """双击运行时让窗口停住，否则报错信息会一闪而过。"""
    try:
        input("\n按回车键退出…")
    except EOFError:
        pass


DIR_HELP = "Antigravity 安装目录（默认 %%LOCALAPPDATA%%\\Programs\\antigravity）"


def main():
    # --dir 同时挂在主 parser 与各子 parser 上：文档写的是 `patch [--dir 安装目录]`，
    # 只挂主 parser 的话 `patcher.py patch --dir X` 会报 unrecognized arguments。
    # 子 parser 用 SUPPRESS 作为默认值，避免它把主 parser 已解析的值覆盖成 None。
    sub_common = argparse.ArgumentParser(add_help=False)
    sub_common.add_argument("--dir", default=argparse.SUPPRESS, help=DIR_HELP)

    parser = argparse.ArgumentParser(description="Google Antigravity 桌面版中文汉化工具")
    parser.add_argument("--dir", help=DIR_HELP)
    parser.add_argument("--debug", action="store_true", help="出错时打印完整 traceback")
    sub = parser.add_subparsers(dest="command")

    for name in ("patch", "scan"):
        p = sub.add_parser(name, parents=[sub_common],
                           help="打汉化补丁" if name == "patch" else "打补丁并收集未翻译文案")
        p.add_argument("--yes", action="store_true", help="自动关闭正在运行的 Antigravity，不询问")
        p.add_argument("--launch", action="store_true", help="完成后自动启动 Antigravity")

    r = sub.add_parser("restore", parents=[sub_common], help="还原官方原版")
    r.add_argument("--yes", action="store_true", help="自动关闭正在运行的 Antigravity，不询问")

    sub.add_parser("status", parents=[sub_common], help="查看版本与补丁状态")

    m = sub.add_parser("missing", parents=[sub_common], help="从当前界面 bundle 找出词典未覆盖的文案")
    m.add_argument("--top", type=int, default=30, help="控制台打印条数（默认 30）")
    m.add_argument("--keep", action="store_true", help="保留下载的界面 bundle 便于复查")

    args = parser.parse_args()

    # 无子命令 = 双击 exe：按"一键汉化并启动"处理，结束后暂停窗口
    oneclick = args.command is None
    if oneclick:
        if args.dir:
            # 只给 --dir 而不给子命令：多半是想查看状态，绝不能静默执行
            # "一键汉化并启动"（会关进程、改安装目录）。
            raise SystemExit("[提示] 请指定子命令：patch / scan / restore / status / missing")
        print("=" * 54)
        print("  Antigravity 中文汉化（一键版）")
        print("=" * 54)
        args.command, args.yes, args.launch = "patch", True, True

    try:
        install = find_install(args.dir)
        if args.command == "patch":
            do_patch(install, scan=False, auto_yes=args.yes, launch=args.launch)
        elif args.command == "scan":
            do_patch(install, scan=True, auto_yes=args.yes, launch=args.launch)
        elif args.command == "restore":
            do_restore(install, auto_yes=args.yes)
        elif args.command == "status":
            do_status(install)
        elif args.command == "missing":
            do_missing(install, top=args.top, keep=args.keep)
    except SystemExit as e:
        if not oneclick:
            raise
        if e.code:
            print(e.code)
        _pause()
        return 1 if e.code else 0
    except Exception as e:
        if getattr(args, "debug", False):
            raise                    # --debug 时保留完整 traceback，便于定位
        print(f"[错误] 执行失败：{e}")
        if oneclick:
            _pause()
        return 1

    if oneclick:
        _pause()
    return 0


if __name__ == "__main__":
    sys.exit(main())
