// 用真实 JavaScript 引擎跑一遍翻译规则（数据由 tools/verify.py 写入 out/_js_check_data.json）。
// 目的：捕捉 Python 模拟层抓不到的问题，例如替换串里 $1 是否被正确解析。
//
// 实现直接从 inject/zh_runtime.js 截取，而不是抄一份：抄一份就意味着"改了运行时却
// 忘了同步这里"时回归仍给绿灯，而套这套校验存在的意义正是拦住这种情况。
// 输出：stdout 打印 JSON 结果，不做任何文件写入。
"use strict";
const fs = require("fs");
const path = require("path");

const data = JSON.parse(fs.readFileSync("out/_js_check_data.json", "utf-8"));
const runtimeFile = path.join(__dirname, "..", "inject", "zh_runtime.js");
const src = fs.readFileSync(runtimeFile, "utf-8");

// 截取"辅助函数 + 全部规则表 + translateText"整段，到 DOM 遍历部分为止。
const START = "  function durationZh";
const END = "  function inSkipZone";
const start = src.indexOf(START);
const end = src.indexOf(END);
if (start < 0 || end < 0) {
  console.error("[失败] 无法从 zh_runtime.js 截取翻译实现（代码结构可能已变）");
  process.exit(2);
}

// 补齐切片之外的依赖：引擎顶部的作用域变量，以及 patcher 注入的数据占位符。
const prelude = [
  "var EXACT = " + JSON.stringify(data.exact || {}) + ";",
  "var WORDS = " + JSON.stringify(data.words || {}) + ";",
  "var PREFIX_RULES = " + JSON.stringify(data.prefix || []) + ";",
  "var __AGZH_TEMPLATE_RULES__ = " + JSON.stringify(data.template || []) + ";",
  "var CJK_RE = /[\\u4e00-\\u9fff\\u3400-\\u4dbf]/;",
  "var SCAN = false;",                       // 关掉收集器，不产生副作用
  "var collector = { miss: function () {} };",
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
