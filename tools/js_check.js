// 用真实 JavaScript 引擎跑一遍翻译规则（数据由 tools/verify.py 写入 out/_js_check_data.json）。
// 目的：捕捉 Python 模拟层抓不到的问题，例如替换串里 $1 是否被正确解析。
//
// 实现直接从 inject/zh_runtime.js 截取，而不是抄一份：抄一份就意味着"改了运行时却
// 忘了同步这里"时回归仍给绿灯，而套这套校验存在的意义正是拦住这种情况。
// 输出：stdout 打印 JSON 结果，不做任何文件写入。
"use strict";
const fs = require("fs");
const path = require("path");

const dataFile = path.join(__dirname, "..", "out", "_js_check_data.json");
const data = JSON.parse(fs.readFileSync(dataFile, "utf-8"));
const runtimeFile = path.join(__dirname, "..", "inject", "zh_runtime.js");
const src = fs.readFileSync(runtimeFile, "utf-8");

// 从数据占位符的初始化一路截到 DOM 遍历之前：nullProto、CJK_RE、SKIP_SEL、
// 收集器（SCAN=false 时是死码但语法完整）、全部规则表与 translateText 都来自
// 真实源文件，本文件不再复刻任何一段逻辑。
const START = "  var EXACT = nullProto(__AGZH_EXACT__);";
const END = "  function inSkipZone";
const start = src.indexOf(START);
const end = src.indexOf(END);
if (start < 0 || end < 0 || end <= start) {
  console.error("[失败] 无法从 zh_runtime.js 截取翻译实现（代码结构可能已变）");
  process.exit(2);
}
// 切片内 EXACT/WORDS 的初始化调用了 nullProto，translateText 用到 CJK_RE，
// 两者的定义一起截取进 prelude（都在 EXACT 声明之前，可一段截下）
const protoStart = src.indexOf("  var CJK_RE");
const protoEnd = src.indexOf(START);
if (protoStart < 0 || protoEnd <= protoStart) {
  console.error("[失败] 无法从 zh_runtime.js 截取 nullProto 定义（代码结构可能已变）");
  process.exit(2);
}

// 补齐切片之外唯一的依赖：patcher 注入的数据占位符本身。
const prelude = [
  src.slice(protoStart, protoEnd),
  "var __AGZH_EXACT__ = " + JSON.stringify(data.exact || {}) + ";",
  "var __AGZH_WORDS__ = " + JSON.stringify(data.words || {}) + ";",
  "var __AGZH_PREFIX_RULES__ = " + JSON.stringify(data.prefix || []) + ";",
  "var __AGZH_TEMPLATE_RULES__ = " + JSON.stringify(data.template || []) + ";",
  "var __AGZH_SCAN__ = false;",              // 关掉收集器，不产生副作用
].join("\n");

const translate = new Function(
  prelude + "\n" + src.slice(start, end) + "\nreturn translateText;")();

const failures = [];
let passed = 0;
for (const [text, expect] of data.cases) {
  const want = expect || text;
  const got = translate(text);
  if (got === want) {
    passed++;
  } else {
    failures.push({ text: text, expect: want, got: got });
  }
}
console.log(JSON.stringify({ passed: passed, failed: failures.length, failures: failures }, null, 2));
