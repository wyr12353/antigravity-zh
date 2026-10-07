// ============================================================
// antigravity-zh 主进程补丁（注入到 dist/main.js 末尾）
// 1) 包一层 Menu.buildFromTemplate，把原生菜单标签换成中文
// 2) scan 模式下接收渲染进程收集的未翻译串并落盘
// 由 patcher.py 填充 __AGZH_*__ 占位符后追加。
// ============================================================
(function () {
  "use strict";
  if (process.__AGZH_LOADED__) return;
  process.__AGZH_LOADED__ = true;

  // MENUS 的查找走 [] 会命中 Object.prototype（"constructor" 等键恒真，
  // 会把菜单标签替换成函数源码），包装成无原型对象
  function nullProto(o) {
    var r = Object.create(null);
    for (var k in o) r[k] = o[k];
    return r;
  }
  var MENUS = nullProto(__AGZH_MENUS__);
  var SCAN = __AGZH_SCAN__;

  try {
    var electron = require("electron");
    var Menu = electron.Menu;

    if (Menu && Menu.buildFromTemplate && !Menu.__AGZH_MENU_HOOKED__) {
      // 只在需要翻译时才克隆，绝不原地修改调用方传入的 template：
      // 应用常常复用同一份模板常量，改掉它会让"按英文 label 查找/比较/持久化"
      // 的逻辑失配，而且重设菜单时拿到的是已汉化的数组，再也回不到原文。
      // 克隆时保留原型（模板里可能直接放 MenuItem 实例）。
      var translateItem = function (item) {
        if (!item || typeof item !== "object") return item;
        try {
          var label = item.label && MENUS[item.label] ? MENUS[item.label] : item.label;
          var submenu = Array.isArray(item.submenu) ? item.submenu.map(translateItem) : item.submenu;
          if (label === item.label && submenu === item.submenu) return item;

          var copy = Object.create(Object.getPrototypeOf(item));
          for (var k in item) {
            if (Object.prototype.hasOwnProperty.call(item, k)) copy[k] = item[k];
          }
          copy.label = label;
          if (submenu !== item.submenu) copy.submenu = submenu;
          return copy;
        } catch (e) {
          // 克隆可能失败（原型上是 getter-only 属性时，严格模式下赋值会抛错）。
          // 绝不能因为汉化让应用的菜单构建失败，出错就退回原对象。
          console.error("[agzh] menu item clone failed:", e);
          return item;
        }
      };
      var original = Menu.buildFromTemplate;
      Menu.buildFromTemplate = function (template) {
        if (Array.isArray(template)) template = template.map(translateItem);
        return original.call(this, template);
      };
      Menu.__AGZH_MENU_HOOKED__ = true;
    }
  } catch (e) {
    console.error("[agzh] menu hook failed:", e);
  }

  if (SCAN) {
    try {
      var fs = require("fs");
      var path = require("path");
      var electron = require("electron");
      var OUT = "__AGZH_COLLECTOR_FILE__";
      var buffer = new Map();

      // 读→合→原子写。三条注意：
      // 1) buffer.clear() 必须放在 renameSync 成功之后——写失败（目标被外部占用
      //    是常态，这个文件本来就设计给外部读）时保留批次下次重试；重试不会把
      //    count 双计，因为 existing 每次都从盘上重建。
      // 2) 旧文件读不出来分两种：ENOENT 是首次落盘（正常）；其它（损坏/被锁）
      //    绝不能拿"仅本批"的内容覆盖写，否则历史积累会被整体抹掉。
      // 3) 临时文件带 pid，多实例同 scan 时不会互踩半截 JSON。
      var writeNow = function () {
        var existing = new Map();
        try {
          var prev = JSON.parse(fs.readFileSync(OUT, "utf-8"));
          if (prev && prev.strings) {
            prev.strings.forEach(function (s) { existing.set(s.text, s); });
          }
        } catch (e) {
          if (e.code !== "ENOENT") {
            console.error("[agzh] collector: 现有数据不可读，本轮放弃写盘:", e);
            return;
          }
        }

        buffer.forEach(function (rec, text) {
          var hit = existing.get(text);
          if (hit) {
            hit.count += rec.count;
            if (!hit.path && rec.path) hit.path = rec.path;
          } else {
            existing.set(text, { text: text, count: rec.count, path: rec.path || "" });
          }
        });

        var arr = [];
        existing.forEach(function (v) { arr.push(v); });
        arr.sort(function (a, b) { return b.count - a.count; });

        // 写盘段单独兜底：目标文件设计上就供外部进程读取，Windows 上外部读者
        // 未开 FILE_SHARE_DELETE 时 renameSync 抛 EPERM 是常态。异常从 ipcMain
        // 监听器抛出会成为主进程未捕获异常，sendSync 期间还可能挂死渲染进程——
        // 必须在这里吞掉。失败时保留 buffer 下次重试，并清理残留的 tmp。
        var tmp = OUT + "." + process.pid + ".tmp";
        try {
          fs.mkdirSync(path.dirname(OUT), { recursive: true });
          fs.writeFileSync(tmp, JSON.stringify({ strings: arr }, null, 2), "utf-8");
          fs.renameSync(tmp, OUT);
        } catch (e) {
          console.error("[agzh] collector: 写盘失败，批次保留待重试:", e);
          try { fs.unlinkSync(tmp); } catch (e2) { /* tmp 可能尚未创建 */ }
          return;   // buffer 不清空
        }
        buffer.clear();   // 写盘成功才清空，失败保留批次重试
      };

      electron.ipcMain.on("agzh:collector", function (event, batch) {
        if (!Array.isArray(batch)) {
          if (event && "returnValue" in event) event.returnValue = false;
          return;
        }
        batch.forEach(function (item) {
          if (!item || !item.text) return;
          var rec = buffer.get(item.text);
          if (rec) {
            rec.count += item.count || 1;
            if (!rec.path && item.path) rec.path = item.path;
          } else {
            buffer.set(item.text, { count: item.count || 1, path: item.path || "" });
          }
        });
        writeNow();
        // 渲染进程关闭窗口时会用 sendSync，必须给出返回值否则它会一直等
        if (event && "returnValue" in event) event.returnValue = true;
      });

      // "before-quit" 是 Electron app 的事件，Node 的 process 上并不存在，
      // 挂在 process 上永远不会触发；exit 兜底仍保留在正常退出路径生效
      if (electron.app && electron.app.on) electron.app.on("before-quit", writeNow);
      process.on("exit", writeNow);
    } catch (e) {
      console.error("[agzh] collector sink failed:", e);
    }
  }
})();
