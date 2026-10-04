// ============================================================
// antigravity-zh 翻译运行时（注入到 dist/preload.js 末尾）
// 由 patcher.py 填充 __AGZH_*__ 占位符后追加。
// 原理：MutationObserver 监听 DOM 变化，按词典把英文替换为中文。
// 词典来源：MIMICTE/Antigravity-zh-CN (MIT) 移植 + 自维护词条。
//
// 翻译顺序：整句精确(EXACT) → 整句模板(TEMPLATE_RULES) → 子串替换(SUBSTR_RULES)
//          → 前缀规则(PREFIX_RULES) → 短词弱匹配(WORDS)
// 新增规则只改数据表，不必改流程。
// ============================================================
(function () {
  "use strict";
  if (window.__AGZH_LOADED__) return;
  window.__AGZH_LOADED__ = true;

  var EXACT = nullProto(__AGZH_EXACT__);
  var WORDS = nullProto(__AGZH_WORDS__);
  var PREFIX_RULES = __AGZH_PREFIX_RULES__;
  var SCAN = __AGZH_SCAN__;

  var CJK_RE = /[\u4e00-\u9fff\u3400-\u4dbf]/;

  // 防 JS 原型链污染：EXACT/WORDS 这类数据若直接用 [] 查找，
  // "constructor"/"toString"/"valueOf" 等键会命中 Object.prototype（恒为真值），
  // 界面上这些裸词会被替换成 "function Object() { [native code] }"。
  // 包装成无原型的对象，所有后续 [] 查找即安全。
  function nullProto(o) {
    var r = Object.create(null);
    for (var k in o) r[k] = o[k];
    return r;
  }

  // 注意：[role=combobox] 刻意不列入跳过区。
  // 设置项的下拉选择器（安全预设 / 终端命令自动执行 / 工件审阅模式…）把"当前值"
  // 渲染在 combobox 自身或其子元素里，跳过它就等于折叠状态的值永远不翻译，
  // 而展开后的选项列表（[role=listbox]/[role=option]）却会被翻译 ——
  // 表现就是"展开的是中文、不展开的那个还是英文"。
  // 可编辑输入仍由 input / textarea / [role=textbox] / [contenteditable] 兜住。
  var SKIP_SEL = "pre, code, textarea, input, script, style, .monaco-editor, [contenteditable], [role=textbox], [role=searchbox]";

  // ---------- 收集器（scan 模式）----------
  var collector = null;
  if (SCAN) {
    var KEYNAMES = nullProto({ ctrl: 1, control: 1, shift: 1, alt: 1, enter: 1, tab: 1, esc: 1, escape: 1,
      space: 1, cmd: 1, meta: 1, option: 1, backspace: 1, delete: 1, del: 1, win: 1, windows: 1,
      cmdorctrl: 1, up: 1, down: 1, left: 1, right: 1, home: 1, end: 1, pageup: 1, pagedown: 1 });
    var JS_KEYWORDS = nullProto({ const: 1, let: 1, "var": 1, "function": 1, "return": 1, "import": 1,
      "export": 1, "class": 1, async: 1, await: 1, yield: 1, typeof: 1, instanceof: 1, "new": 1,
      "this": 1, "null": 1, "undefined": 1, "true": 1, "false": 1, boolean: 1, number: 1,
      string: 1, object: 1, symbol: 1, bigint: 1, "interface": 1, "enum": 1, "public": 1,
      "private": 1, "protected": 1, "static": 1 });
    // 与 tools/scan_filter.py 的 is_noise() 等价：代码片段/包名/版本号/符号残片一律不进收集
    var NO_LETTER_RE = /^[^A-Za-z\u4e00-\u9fff]*$/;
    var VERSION_RE = /^v?\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.]+)?$/;
    var IDENT_RE = /^[A-Za-z0-9_$@./\\+-]+$/;
    var HEX_RE = /^[0-9a-f]{8,}$/i;
    var URL_RE = /^[a-z][a-z0-9+.-]*:\/\//i;
    var FILENAME_RE = /^[A-Za-z0-9_.-]+\.(?:md|markdown|json|ya?ml|toml|js|mjs|cjs|ts|tsx|jsx|py|txt|log|lock|cfg|ini):?$/;
    function isNoise(t) {
      if (t.length <= 2) return true;
      if (KEYNAMES[t.toLowerCase()]) return true;
      if (t.indexOf("@") !== -1 && t.indexOf(" ") === -1) return true;
      if (NO_LETTER_RE.test(t)) return true;
      if (VERSION_RE.test(t)) return true;
      if (URL_RE.test(t) || FILENAME_RE.test(t)) return true;
      if (t.indexOf("//") === 0 || t.indexOf("/*") === 0 || t.indexOf("*/") === 0) return true;
      if (t.indexOf("${") !== -1 || t.indexOf("`") !== -1) return true;
      if (t.indexOf(" ") === -1 && (t.indexOf("(") !== -1 || t.indexOf(")") !== -1) && t.length <= 30) return true;
      if (JS_KEYWORDS[t.toLowerCase()]) return true;
      if (HEX_RE.test(t)) return true;
      if (t.indexOf(" ") === -1 && IDENT_RE.test(t)) {
        if (/[/\\_$]/.test(t)) return true;
        if (t.indexOf("-") !== -1 && t.length <= 40) return true;
      }
      // 长代码行（无空格）：以分号结尾、含箭头或 "}(" —— 与 scan_filter.py 保持一致
      if (t.length <= 60 && t.indexOf(" ") === -1 &&
          (t.charAt(t.length - 1) === ";" || t.indexOf("=>") !== -1 || t.indexOf("}(") !== -1)) {
        return true;
      }
      return false;
    }
    var pending = new Map();
    collector = {
      miss: function (text, el) {
        var t = text.trim();
        if (isNoise(t)) return;
        if (pending.has(t)) {
          pending.get(t).count++;
        } else {
          var path = "";
          try {
            var node = el && el.parentElement ? el.parentElement : null;
            var segs = [];
            var hop = 0;
            while (node && node.tagName && hop < 4) {
              segs.unshift(node.tagName.toLowerCase() + (node.className && typeof node.className === "string" ? "." + node.className.trim().split(/\s+/).slice(0, 2).join(".") : ""));
              node = node.parentElement;
              hop++;
            }
            path = segs.join(" < ");
          } catch (e) { /* 忽略取路径失败 */ }
          pending.set(t, { count: 1, path: path });
        }
      },
      start: function () {
        setInterval(function () { collector.flush(); }, 5000);
        // 关闭窗口时用同步发送：异步 send 在渲染进程销毁前来不及送达主进程，
        // 而"点完界面顺手关掉窗口"正是最常见的路径，会丢掉最后一批。
        window.addEventListener("beforeunload", function () { collector.flush(true); });
        window.addEventListener("pagehide", function () { collector.flush(true); });
      },
      flush: function (sync) {
        if (!pending.size) return;
        var batch = [];
        pending.forEach(function (v, k) { batch.push({ text: k, count: v.count, path: v.path }); });
        try {
          var electron = require("electron");
          if (sync && electron.ipcRenderer.sendSync) {
            electron.ipcRenderer.sendSync("agzh:collector", batch);
          } else {
            electron.ipcRenderer.send("agzh:collector", batch);
          }
        } catch (e) {
          return;   // 发送失败时保留 pending，留待下次重试（否则这一批会静默丢失）
        }
        pending.clear();   // 只在发送成功后才清空
      }
    };
    collector.start();
  }

  // ---------- 工具函数 ----------
  // "3 days, 2 hours" → "3 天 2 小时"
  // tools/verify.py 的 duration_zh 是本函数在 Python 模拟层的复刻，改动需两处同步；
  // tools/js_check.js 直接截取本文件的实现，无需同步。
  function durationZh(s) {
    return s.replace(/days?/g, "天").replace(/hours?/g, "小时").replace(/minutes?/g, "分钟").replace(/,/g, "");
  }
  // "1.2s" → "1.2秒"；"2m 30s" → "2分 30秒"；"3 hours"/"2 mins"/"45 sec" → "3小时"/"2分"/"45秒"
  // 必须按"数字 + 单位"整体匹配：早先逐字符替换 s/m/h 会把 "3 hours" 变成 "3 小时our秒"。
  // 缩写（min/sec/hr）不能漏：单位表里若只有 minutes?，"2 mins" 会整体不匹配、原样留在界面上。
  function elapsedZh(s) {
    return s.replace(
      /(\d+(?:\.\d+)?)\s*(ms|milliseconds?|secs?|seconds?|s|mins?|minutes?|m|hrs?|hours?|h)\b/gi,
      function (_, num, unit) {
        var u = unit.toLowerCase();
        var zh = u === "ms" || u.indexOf("millisecond") === 0 ? "毫秒"
               : u.indexOf("sec") === 0 || u === "s" ? "秒"
               : u.indexOf("min") === 0 || u === "m" ? "分"
               : "小时";
        return num + zh;
      });
  }

  // ---------- 规则表：整句模板 ----------
  // 数据来自 dict/template_rules.json（由 patcher 注入），此处只做编译。
  // 按顺序匹配、先命中者生效（若存在宽泛的兜底规则，应排在最后）。
  var TEMPLATE_RULES = __AGZH_TEMPLATE_RULES__.map(function (r) {
    return [new RegExp(r[0], r[1] || ""), r[2]];
  });

  // 需要查词典或做单位换算的规则，无法用纯模板表达，留在代码里。
  // 放在模板规则之前执行（与它们的模式互不重叠，顺序无副作用）。
  function allowZh(action) {
    var zh = EXACT[action];
    return zh ? "允许" + zh + "吗？" : "允许 " + action + " 吗？";
  }
  function saveRuleZh(action) {
    var zh = EXACT[action];
    return zh ? "保存规则以始终允许" + zh + "吗？" : "保存规则以始终允许 " + action + " 吗？";
  }
  // 整句一次处理 "It requires X to be installed." → "需要安装 X。"
  // 早先拆成两条规则：命中即返回让 "to be installed." 永不执行（产出半中半英），
  // 而把 "to be installed." 做成独立规则又会把任何未命中词典的句子悄悄截断
  // （"There are 2 extensions to be installed." → "There are 2 extensions"）。
  // 这里也不做 "Configure" 守卫：词典条目一旦随版本漂移失效，守卫会产出半英半中。
  function requiresZh(t) {
    return t.replace(/It requires\s+(.+?)\s+to be installed\.?/g, function (_, what) {
      return "需要安装 " + (EXACT[what] || what) + "。";
    });
  }

  var FN_RULES = [
    [/^Learn more about (.+)$/, function (m) { return "了解更多关于 " + (EXACT[m[1]] || m[1]) + " 的信息"; }],
    [/^You have used some of your weekly limit, it will fully refresh in (.*)\.$/, function (m) { return "您已使用部分每周限额，它将在 " + durationZh(m[1]) + " 后完全重置。"; }],
    [/^You have used some of your 5-hour limit, it will fully refresh in (.*)\.$/, function (m) { return "您已使用部分五小时限额，它将在 " + durationZh(m[1]) + " 后完全重置。"; }],
    // 权限兜底：动作名先查词典再拼中文，查不到才退回原文。
    // 若直接用模板把 $1 原样嵌入，运行时拼接的句子会永远半中半英
    // （"允许 running this command 吗？"）。
    [/^Allow (.+)\?$/, function (m) { return allowZh(m[1]); }],
    [/^Save rule to always allow (.+)\?$/, function (m) { return saveRuleZh(m[1]); }]
  ];

  // ---------- 规则表：子串命中后做替换 ----------
  // [子串, 处理函数]；函数返回原文表示未处理。
  var SUBSTR_RULES = [
    ["of the customization budget is available", function (t) {
      t = t.replace(/(\d+(?:\.\d+)?)% of the customization budget is available\.?/g, "自定义项预算可用额度为 $1%。");
      t = t.replace(/%\s*of the customization budget is available\.?/g, "% 的自定义项预算可用额度。");
      return t.replace(/(^\s*)of the customization budget is available\.?/g, "$1的自定义项预算可用额度。");
    }],
    ["Worked for", function (t) {
      // 捕获段含逗号："Worked for 2 hours, 30 minutes" 的后半段不能留在英文里
      return t.replace(/Worked for ([\d.a-z, ]+)/gi, function (_, ts) { return "运行耗时 " + elapsedZh(ts); });
    }],
    ["Thought for", function (t) {
      return t.replace(/Thought for ([\d.a-z, ]+)/gi, function (_, ts) { return "思考耗时 " + elapsedZh(ts); });
    }],
    // 只处理"独立成句的 Working"（状态文本 "Working" / "Working..."）。
    // 早先用无边界子串命中，会把 "Working Directory: " 翻成"运行中 Directory: "——
    // 而词典里的键带尾随空格，运行时查的是 trim 后的文本，永远命中不了，
    // 于是这个错误译文成了确定性结果。
    ["Working", function (t) {
      return /^\s*Working(\.*)\s*$/.test(t) ? t.replace(/Working(\.*)/g, "运行中$1") : t;
    }],
    // PostHog 专用的整句译文（其余 "Ask questions. Get answers" 走 prefix_rules）
    ["Ask questions. Get answers", function (t) {
      return t.indexOf("PostHog") !== -1
        ? "提出问题。获取答案。该 MCP 是您的编程智能体与之对话的服务器。用英语提问，它会针对您的 PostHog 数据运行查询并返回结果。"
        : t;
    }],
    ["Configure the browser subagent", function (t) {
      // 后面常紧跟 "It requires X to be installed."，一并处理掉
      return requiresZh(t.replace(/Configure the browser subagent\.?/g, "配置浏览器子智能体。"));
    }],
    ["It requires", requiresZh],
    ["The browser subagent can be invoked by typing", function (t) {
      return t.replace(/The browser subagent can be invoked by typing \/browser in the conversation input box\./g, "您可以在对话输入框中输入 /browser 来调用浏览器子智能体。");
    }],
    ["You currently don't have any MCP Servers installed.", function (t) {
      return t.replace(/You currently don't have any MCP Servers installed\./g, "您目前尚未安装任何 MCP 服务器。");
    }],
    ["Add an MCP server above", function (t) {
      return t.replace(/Add an MCP server above or add a custom one via the MCP Config\./g, "请在上方添加 MCP 服务器，或通过 MCP 配置添加自定义服务器。");
    }],
    ["Requires manual confirmation", function (t) {
      return t.replace(/Requires manual confirmation: /g, "需要手动确认：").replace(/Requires manual confirmation/g, "需要手动确认");
    }]
  ];

  // ---------- 核心：翻译一段文本 ----------
  function translateText(text, el) {
    if (!text || typeof text !== "string") return text;
    var trimmed = text.trim();
    if (!trimmed) return text;
    if (CJK_RE.test(trimmed)) return text; // 已含中文，跳过（也防自触发循环）

    if (EXACT[trimmed]) return text.replace(trimmed, EXACT[trimmed]);

    // 整句规则：先函数式（需查词典/单位换算），再纯模板
    var m;
    for (var i = 0; i < FN_RULES.length; i++) {
      if ((m = trimmed.match(FN_RULES[i][0]))) return text.replace(trimmed, FN_RULES[i][1](m));
    }
    for (var j = 0; j < TEMPLATE_RULES.length; j++) {
      var replaced = trimmed.replace(TEMPLATE_RULES[j][0], TEMPLATE_RULES[j][1]);
      if (replaced !== trimmed) return text.replace(trimmed, replaced);
    }

    // 子串替换（返回原文说明该规则不适用，继续往下）
    for (var s = 0; s < SUBSTR_RULES.length; s++) {
      if (text.indexOf(SUBSTR_RULES[s][0]) !== -1) {
        var out = SUBSTR_RULES[s][1](text);
        if (out !== text) return out;
      }
    }

    // 前缀规则：命中即整句替换（用于被截断/拼接的长文案）
    for (var p = 0; p < PREFIX_RULES.length; p++) {
      if (text.indexOf(PREFIX_RULES[p][0]) !== -1) return PREFIX_RULES[p][1];
    }

    // ≤3 个词的短文案走弱匹配词表
    if (trimmed.split(/\s+/).length <= 3) {
      var lower = trimmed.toLowerCase();
      if (WORDS[lower]) return text.replace(trimmed, WORDS[lower]);
    }

    if (SCAN) collector.miss(text, el);
    return text;
  }

  // ---------- DOM 遍历 ----------
  function inSkipZone(el) {
    if (!el) return false;
    try {
      if (el.isContentEditable) return true;
      if (el.closest && el.closest(SKIP_SEL)) return true;
    } catch (e) {}
    return false;
  }

  function processElement(el) {
    // 属性（placeholder / title / aria-label / alt / data-tooltip）始终翻译，
    // **包括处于跳过区的元素**。跳过区要保护的是"代码与正文内容"，
    // 而这些属性是控件的可访问标签与悬停提示：早先在这里直接 return，
    // 导致聊天输入区、富文本编辑器内部按钮的 tooltip 一直是英文。
    var skip = inSkipZone(el);
    var isField = el.tagName === "INPUT" || el.tagName === "TEXTAREA";

    // placeholder 只在 input/textarea 上实现，其它元素上访问会走原型链查找；
    // 本函数对整片 DOM 的每个元素都会调用，先做标签守卫省下这次查找。
    if (isField && el.placeholder) {
      var p = translateText(el.placeholder, el);
      if (p !== el.placeholder) el.placeholder = p;
    }
    // title/aria-label/alt/data-tooltip 是 get→translate→set 的同一模式，收敛成表
    var ATTRS = ["title", "aria-label", "alt", "data-tooltip"];
    for (var ai = 0; ai < ATTRS.length; ai++) {
      var attrName = ATTRS[ai];
      var attrVal = el.getAttribute ? el.getAttribute(attrName) : null;
      if (attrVal) {
        var attrZh = translateText(attrVal, el);
        if (attrZh !== attrVal) el.setAttribute(attrName, attrZh);
      }
    }
    // 按钮的可见文本走 value property（非 attribute），单独处理
    if (el.tagName === "INPUT" && (el.type === "button" || el.type === "submit" || el.type === "reset") && el.value) {
      var v = translateText(el.value, el);
      if (v !== el.value) el.value = v;
    }

    // 属性处理完毕；跳过区内的元素不再递归处理文本与子节点
    if (skip) return;

    // 容器级拼接匹配：直接子节点全是纯文本节点且整体命中词典时折叠替换
    // （严格仅处理纯文本节点，绝对不破坏子元素span上的字号与样式类名）
    var kids = el.childNodes;
    if (kids.length > 1) {
      var allText = true;
      for (var k = 0; k < kids.length; k++) if (kids[k].nodeType !== 3) { allText = false; break; }
      if (allText) {
        var joined = el.textContent;
        var jt = joined.trim();
        if (jt && !CJK_RE.test(jt) && EXACT[jt]) {
          el.textContent = joined.replace(jt, EXACT[jt]);
          return;
        }
      }
    }

    for (var c = 0; c < kids.length; c++) processNode(kids[c]);
  }

  function processNode(node) {
    if (!node) return;
    if (node.nodeType === 3) { // TEXT_NODE
      var parent = node.parentElement || node.parentNode;
      if (parent && inSkipZone(parent)) return;
      var translated = translateText(node.textContent, parent);
      if (translated !== node.textContent) node.textContent = translated;
    } else if (node.nodeType === 1) { // ELEMENT_NODE
      var tag = node.tagName;
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "CODE" || tag === "PRE") return;
      processElement(node);
    }
  }

  function translateTitle() {
    var t = document.title;
    var tr = translateText(t);
    if (tr !== t) document.title = tr;
  }

  function start() {
    processElement(document.body);
    translateTitle();

    var observer = new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        var mu = mutations[i];
        if (mu.type === "childList") {
          for (var j = 0; j < mu.addedNodes.length; j++) processNode(mu.addedNodes[j]);
        } else if (mu.type === "characterData") {
          processNode(mu.target);
        } else if (mu.type === "attributes" && mu.target && mu.target.nodeType === 1) {
          // 不做跳过区过滤：processElement 对跳过区元素也是先译属性再 return，
          // 输入框运行中动态切换的 placeholder 同样需要被翻到
          processElement(mu.target);
        }
      }
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      // 特意不含 "value"：写 el.value 会再次触发本观察者，让同一子树被重复遍历
      attributeFilter: ["placeholder", "title", "aria-label", "alt", "data-tooltip"]
    });

    // 窗口标题（Agent Manager 会动态改标题）
    var head = document.head;
    if (head) {
      new MutationObserver(translateTitle).observe(head, { childList: true, subtree: true, characterData: true });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
