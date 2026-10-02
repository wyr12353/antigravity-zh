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

  var MENUS = __AGZH_MENUS__;
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
      var OUT = "__AGZH_COLLECTOR_FILE__";
      var buffer = new Map();
      var writing = false;
      var dirty = false;

      // 落盘串行化 + 原子替换。
      // 多条 IPC 批可能同时在飞（多窗口），无锁的 read-modify-write 会让后写者
      // 覆盖前者、静默丢数据；直接覆盖目标文件也可能被读到半截 JSON。
      var writeNow = function () {
        if (writing) { dirty = true; return; }
        writing = true;
        dirty = false;
        try {
          var existing = new Map();
          try {
            var prev = JSON.parse(fs.readFileSync(OUT, "utf-8"));
            if (prev && prev.strings) {
              prev.strings.forEach(function (s) { existing.set(s.text, s); });
            }
          } catch (e) { /* 首次无文件 */ }

          buffer.forEach(function (rec, text) {
            var hit = existing.get(text);
            if (hit) {
              hit.count += rec.count;
              if (!hit.path && rec.path) hit.path = rec.path;
            } else {
              existing.set(text, { text: text, count: rec.count, path: rec.path || "" });
            }
          });
          buffer.clear();

          var arr = [];
          existing.forEach(function (v) { arr.push(v); });
          arr.sort(function (a, b) { return b.count - a.count; });

          fs.mkdirSync(path.dirname(OUT), { recursive: true });
          // 临时文件名带上 pid：多实例同时 scan 时不会互相覆盖（writing 锁只管进程内）
          var tmp = OUT + "." + process.pid + ".tmp";
          fs.writeFileSync(tmp, JSON.stringify({ strings: arr }, null, 2), "utf-8");
          fs.renameSync(tmp, OUT);
        } catch (e) {
          console.error("[agzh] collector write failed:", e);
        } finally {
          writing = false;
          if (dirty) writeNow();
        }
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

      process.on("before-quit", writeNow);
      process.on("exit", writeNow);   // 退出路径再兜一次
    } catch (e) {
      console.error("[agzh] collector sink failed:", e);
    }
  }
})();
