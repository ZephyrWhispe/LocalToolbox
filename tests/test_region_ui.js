/* 区域圈选 UI 交互回归测试（Node 运行，无需浏览器/Electron）。

   用最小 DOM 桩加载 region_capture.js 与 overlay.html 引导脚本，模拟
   「拖选 → 调整 → 确认 / 取消 / ESC」事件序列与后端调用参数，覆盖历史缺陷：

   1. 确认按钮 mousedown 冒泡到遮罩后被当作「选区外重新圈选」清空选区，
      随后 click 到达 finish() 时 selRect 已空而静默失败（点确认无反应）；
   2. 引导脚本被 pywebviewready 与 1500ms 兜底定时器双重启动 →
      两层遮罩叠放、keydown 监听重复绑定（一次 ESC 发两次取消）；
   3. call() 固定转发 6 个实参 → shot_overlay_cancel(token) 收到多余 null
      抛 TypeError（ESC / 取消按钮失效，遮罩卡死无法退出）；
   4. 前端 canvas 裁剪失败不应中断提交（降级为后端从留存 PNG 裁剪）；
   5. window_drag.js 拖动守卫：画布/输入/按钮等交互元素上的按下不冒泡到
      window（easy_drag 收不到 → 不拖窗），卡片空白/标题栏仍可拖动整窗
      （曾出现「关掉 easy_drag 后窗口完全拖不动」的反向回归）。

   运行：node test_region_ui.js（退出码 0=全部通过） */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = __dirname;
let failures = 0;

function check(name, cond, extra) {
  if (cond) {
    console.log("  [OK] " + name);
  } else {
    failures++;
    console.log("  [FAIL] " + name + (extra ? "  → " + extra : ""));
  }
}

/* ---------------- 最小 DOM 桩 ---------------- */
let idSeq = 0;

function makeCtx() {
  const noop = () => {};
  return {
    imageSmoothingEnabled: true,
    drawImage: noop, fillRect: noop, fillText: noop, clearRect: noop,
    beginPath: noop, moveTo: noop, lineTo: noop, stroke: noop, fill: noop,
    arc: noop, rect: noop, roundRect: noop, strokeRect: noop, save: noop,
    restore: noop, setLineDash: noop, putImageData: noop, clip: noop,
    arcTo: noop, closePath: noop, ellipse: noop, strokeText: noop,
    measureText: () => ({ width: 40 }),
    getImageData: () => ({ data: [0, 0, 0, 255] }),
  };
}

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = 1;
    this.id = "el" + (++idSeq);
    this.style = { cssText: "" };
    this.children = [];
    this.parentNode = null;
    this._listeners = {};
    this._classes = new Set();
    this._attrs = {};
    this.textContent = "";
    this.offsetWidth = 0;
    this.width = 0;
    this.height = 0;
    const self = this;
    this.classList = {
      contains: (c) => self._classes.has(c),
      add: (c) => { self._classes.add(c); },
      remove: (c) => { self._classes.delete(c); },
    };
  }
  set className(v) {
    this._classes = new Set(String(v || "").split(/\s+/).filter(Boolean));
  }
  get className() { return Array.from(this._classes).join(" "); }
  setAttribute(k, v) { this._attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null; }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  remove() {
    if (this.parentNode) {
      const i = this.parentNode.children.indexOf(this);
      if (i >= 0) this.parentNode.children.splice(i, 1);
      this.parentNode = null;
    }
  }
  contains(node) {
    for (let n = node; n; n = n.parentNode) if (n === this) return true;
    return false;
  }
  /* 简化选择器匹配：tag / .class / [attr='value'] / 逗号并集（够本测试用） */
  closest(selector) {
    const sels = String(selector).split(",").map((s) => s.trim()).filter(Boolean);
    for (let n = this; n; n = n.parentNode) {
      if (n.nodeType !== 1) continue;
      for (const s of sels) {
        if (matchesSimple(n, s)) return n;
      }
    }
    return null;
  }
  addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); }
  removeEventListener(t, fn) {
    const a = this._listeners[t] || [];
    const i = a.indexOf(fn);
    if (i >= 0) a.splice(i, 1);
  }
  /* 手柄命中测试用：桩坐标远离测试点，避免误判为拖柄 */
  getBoundingClientRect() {
    return { left: -9999, top: -9999, right: -9998, bottom: -9998, width: 0, height: 0 };
  }
  getContext() { return makeCtx(); }
  toDataURL() { return "data:image/png;base64,UNITTEST"; }
}

function matchesSimple(el, sel) {
  if (sel.startsWith(".")) return el._classes && el._classes.has(sel.slice(1));
  const m = /^\[([\w-]+)(?:='([^']*)')?\]$/.exec(sel);
  if (m) {
    const v = el.getAttribute ? el.getAttribute(m[1]) : null;
    return m[2] === undefined ? v !== null : String(v) === m[2];
  }
  return el.tagName === sel.toUpperCase();
}

class FakeImage extends El {
  constructor() {
    super("img");
    this.naturalWidth = 1000;
    this.naturalHeight = 800;
    this.onload = null;
    this.onerror = null;
    this._src = "";
  }
  set src(v) { this._src = v; if (this.onload) this.onload(); }
  get src() { return this._src; }
}

function makeWindow() {
  const listeners = {};
  return {
    innerWidth: 1000,
    innerHeight: 800,
    _listeners: listeners,
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener(t, fn) {
      const a = listeners[t] || [];
      const i = a.indexOf(fn);
      if (i >= 0) a.splice(i, 1);
    },
  };
}

function dispatchWindow(win, type, evt) {
  evt = Object.assign({ type, preventDefault() {} }, evt || {});
  (win._listeners[type] || []).slice().forEach((fn) => fn(evt));
}

/* 带冒泡的事件派发（模拟真实 DOM：按钮上的 mousedown 会冒泡到遮罩层） */
function dispatch(target, type, evt) {
  evt = Object.assign({ type, target, preventDefault() {} }, evt || {});
  for (let n = target; n; n = n.parentNode) {
    const arr = (n._listeners && n._listeners[type]) || [];
    arr.slice().forEach((fn) => fn(evt));
  }
  return evt;
}

function findAll(root, pred, out) {
  out = out || [];
  (root.children || []).forEach((c) => {
    if (pred(c)) out.push(c);
    findAll(c, pred, out);
  });
  return out;
}

function makeDoc() {
  const body = new El("body");
  const errorEl = new El("div");
  const listeners = {};
  return {
    body,
    errorEl,
    _listeners: listeners,
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener(t, fn) {
      const a = listeners[t] || [];
      const i = a.indexOf(fn);
      if (i >= 0) a.splice(i, 1);
    },
    createElement: (tag) => (tag === "img" ? new FakeImage() : new El(tag)),
    getElementById: (id) => (id === "ov-error" ? errorEl : null),
  };
}

function loadRegionCapture(win, doc) {
  const code = fs.readFileSync(
    path.join(ROOT, "webui", "js", "pages", "region_capture.js"), "utf8");
  const sandbox = {
    window: win, document: doc, Image: FakeImage, console, Promise,
    Math, JSON, Object, Array, String, Number, Boolean, Date, RegExp,
    performance: { now: () => Date.now() },
    setTimeout, clearTimeout,
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: "region_capture.js" });
  return win.RegionCapture;
}

/* ---------------- 场景 1~4：region_capture.js 交互 ---------------- */
function testRegionCapture() {
  console.log("\n[1/4] 圈选交互：拖选 → 确认 / 取消 / ESC");

  /* 场景 1：拖选后点「确认」按钮 → onDone 收到正确矩形 */
  {
    const win = makeWindow();
    const doc = makeDoc();
    const RC = loadRegionCapture(win, doc);
    const done = [];
    let cancelled = 0;
    RC.start({
      imgSrc: "data:image/png;base64,AAA",
      onDone: (url, w, h, rect) => done.push({ url, w, h, rect }),
      onCancel: () => { cancelled++; },
    });
    const overlay = doc.body.children[0];
    dispatch(overlay, "mousedown", { button: 0, clientX: 100, clientY: 100 });
    dispatchWindow(win, "mousemove", { clientX: 300, clientY: 250 });
    dispatchWindow(win, "mouseup", {});
    const buttons = findAll(overlay, (c) => c.tagName === "BUTTON");
    const okBtn = buttons.find((b) => String(b.textContent).startsWith("确认"));
    check("调整模式出现确认按钮", !!okBtn);
    // 真实点击序列：mousedown（冒泡到遮罩）→ mouseup → click
    dispatch(okBtn, "mousedown", { button: 0, clientX: 150, clientY: 300 });
    dispatchWindow(win, "mouseup", {});
    dispatch(okBtn, "click", {});
    check("点确认后 onDone 被调用（历史缺陷：静默无反应）", done.length === 1,
      "done=" + done.length);
    if (done.length) {
      const r = done[0].rect;
      check("确认回调矩形正确 200×150",
        r.x === 100 && r.y === 100 && r.w === 200 && r.h === 150,
        JSON.stringify(r));
      check("提交后有「正在生成截图」状态提示",
        findAll(doc.body, (c) => String(c.textContent).indexOf("正在生成截图") >= 0).length > 0);
    }
    check("确认路径不触发 onCancel", cancelled === 0);
    // 提交后 ESC 仍可退出（后端无响应兜底）
    dispatchWindow(win, "keydown", { key: "Escape" });
    check("提交后按 ESC 仍可取消退出", cancelled === 1, "cancelled=" + cancelled);
  }

  /* 场景 2：拖选后点「取消」按钮 → onCancel 触发且遮罩移除 */
  {
    const win = makeWindow();
    const doc = makeDoc();
    const RC = loadRegionCapture(win, doc);
    let cancelled = 0;
    RC.start({
      imgSrc: "data:image/png;base64,AAA",
      onDone: () => {},
      onCancel: () => { cancelled++; },
    });
    const overlay = doc.body.children[0];
    dispatch(overlay, "mousedown", { button: 0, clientX: 50, clientY: 50 });
    dispatchWindow(win, "mousemove", { clientX: 200, clientY: 180 });
    dispatchWindow(win, "mouseup", {});
    const cancelBtn = findAll(overlay, (c) => c.tagName === "BUTTON")
      .find((b) => String(b.textContent).startsWith("取消"));
    check("调整模式出现取消按钮", !!cancelBtn);
    dispatch(cancelBtn, "mousedown", { button: 0, clientX: 60, clientY: 320 });
    dispatchWindow(win, "mouseup", {});
    dispatch(cancelBtn, "click", {});
    check("点取消后 onCancel 触发", cancelled === 1, "cancelled=" + cancelled);
    check("取消后遮罩已移除", doc.body.children.indexOf(overlay) < 0);
  }

  /* 场景 3：Enter 键确认（键盘路径） */
  {
    const win = makeWindow();
    const doc = makeDoc();
    const RC = loadRegionCapture(win, doc);
    const done = [];
    RC.start({
      imgSrc: "data:image/png;base64,AAA",
      onDone: (url, w, h, rect) => done.push({ w, h, rect }),
      onCancel: () => {},
    });
    const overlay = doc.body.children[0];
    dispatch(overlay, "mousedown", { button: 0, clientX: 10, clientY: 10 });
    dispatchWindow(win, "mousemove", { clientX: 110, clientY: 90 });
    dispatchWindow(win, "mouseup", {});
    dispatchWindow(win, "keydown", { key: "Enter" });
    check("Enter 确认触发 onDone", done.length === 1 && done[0].w === 100 && done[0].h === 80,
      JSON.stringify(done[0] || null));
  }

  /* 场景 4：canvas 裁剪异常时降级回传空串（后端从留存 PNG 裁剪） */
  {
    const win = makeWindow();
    const doc = makeDoc();
    const RC = loadRegionCapture(win, doc);
    const done = [];
    const orig = El.prototype.toDataURL;
    El.prototype.toDataURL = () => { throw new Error("out of memory"); };
    try {
      RC.start({
        imgSrc: "data:image/png;base64,AAA",
        onDone: (url, w, h, rect) => done.push({ url, w, h }),
        onCancel: () => {},
      });
      const overlay = doc.body.children[0];
      dispatch(overlay, "mousedown", { button: 0, clientX: 10, clientY: 10 });
      dispatchWindow(win, "mousemove", { clientX: 60, clientY: 60 });
      dispatchWindow(win, "mouseup", {});
      dispatchWindow(win, "keydown", { key: "Enter" });
      check("canvas 裁剪异常仍提交（降级空串）",
        done.length === 1 && done[0].url === "" && done[0].w === 50,
        JSON.stringify(done[0] || null));
    } finally {
      El.prototype.toDataURL = orig;
    }
  }
}

/* ---------------- 场景 5~7：overlay.html 引导脚本 ---------------- */
function extractBootScript() {
  const html = fs.readFileSync(path.join(ROOT, "webui", "overlay.html"), "utf8");
  const scripts = [];
  const re = /<script>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(html)) !== null) scripts.push(m[1]);
  const boot = scripts[scripts.length - 1];
  return boot.replace("/*__CFG_JSON__*/", JSON.stringify({
    img: "data:image/jpeg;base64,AAA", mode: "shot", token: "3", box: [0, 0],
  }));
}

function runBootScript({ apiReady }) {
  const win = makeWindow();
  const doc = makeDoc();
  const state = { started: 0, captured: null, calls: [] };
  const timers = [];
  win.RegionCapture = {
    start: (opts) => { state.started++; state.captured = opts; },
  };
  const api = {
    shot_overlay_done: function () { state.calls.push(["done", Array.prototype.slice.call(arguments)]); return Promise.resolve({ ok: true }); },
    shot_overlay_cancel: function () { state.calls.push(["cancel", Array.prototype.slice.call(arguments)]); return Promise.resolve({ ok: true }); },
  };
  if (apiReady) win.pywebview = { api };
  const sandbox = {
    window: win, document: doc, console, Promise,
    Math, JSON, Object, Array, String, Number, Boolean, Date, RegExp,
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: () => {},
  };
  vm.createContext(sandbox);
  vm.runInContext(extractBootScript(), sandbox, { filename: "overlay-boot.js" });
  return { win, doc, state, api, timers };
}

function testOverlayBoot() {
  console.log("\n[2/4] 引导脚本：单次启动 + 后端调用参数个数");

  /* 场景 5：API 已就绪 → 立即启动一次；onDone 转发 6 个实参 */
  {
    const h = runBootScript({ apiReady: true });
    check("API 就绪时立即启动一次", h.state.started === 1, "started=" + h.state.started);
    h.state.captured.onDone("data:image/png;base64,X", 30, 20,
      { x: 5, y: 6, w: 30, h: 20 });
    const call = h.state.calls.find((c) => c[0] === "done");
    check("shot_overlay_done 参数 6 个且内容正确",
      !!call && call[1].length === 6 &&
      call[1][0] === "" && call[1][1] === 5 && call[1][2] === 6 &&
      call[1][3] === 30 && call[1][4] === 20 && call[1][5] === "3",
      JSON.stringify(call || null));
    h.state.captured.onCancel();
    const cancel = h.state.calls.find((c) => c[0] === "cancel");
    check("shot_overlay_cancel 只传 1 个实参（历史缺陷：多传 5 个 null）",
      !!cancel && cancel[1].length === 1 && cancel[1][0] === "3",
      JSON.stringify(cancel || null));
  }

  /* 场景 6：API 延迟就绪 → pywebviewready 与兜底定时器都触发，仍只启动一次 */
  {
    const h = runBootScript({ apiReady: false });
    check("API 未就绪时不启动", h.state.started === 0);
    h.win.pywebview = { api: h.api };
    dispatchWindow(h.win, "pywebviewready", {});
    check("ready 事件后启动一次", h.state.started === 1, "started=" + h.state.started);
    h.timers.filter((t) => t.ms === 1500).forEach((t) => t.fn());
    check("1500ms 兜底定时器不再重复启动（历史缺陷：双层遮罩）",
      h.state.started === 1, "started=" + h.state.started);
    h.timers.filter((t) => t.ms === 3000).forEach((t) => t.fn());
    check("正常启动后不显示初始化失败提示",
      String(h.doc.errorEl.style.display || "") !== "block");
  }

  /* 场景 7：后端返回 ok:false → 顶部错误条提示（可见失败原因） */
  {
    const h = runBootScript({ apiReady: true });
    h.api.shot_overlay_done = function () {
      return Promise.resolve({ ok: false, err: "空选区" });
    };
    h.state.captured.onDone("", 0, 0, { x: 0, y: 0, w: 0, h: 0 });
    return new Promise((resolve) => {
      setImmediate(() => {
        check("后端 ok:false 时显示错误条",
          String(h.doc.errorEl.style.display) === "block" &&
          String(h.doc.errorEl.textContent).indexOf("空选区") >= 0,
          "display=" + h.doc.errorEl.style.display +
          " text=" + h.doc.errorEl.textContent);
        resolve();
      });
    });
  }
}

/* ---------------- 场景 8：窗口拖动守卫（window_drag.js） ---------------- */
function testWindowDragGuard() {
  console.log("\n[3/4] 窗口拖动守卫：交互元素不被整窗拖动劫持");

  const code = fs.readFileSync(
    path.join(ROOT, "webui", "js", "window_drag.js"), "utf8");
  const doc = makeDoc();
  const sandbox = {
    window: makeWindow(), document: doc, console, Promise,
    Math, JSON, Object, Array, String, Number, Boolean, Date, RegExp,
    setTimeout, clearTimeout,
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: "window_drag.js" });

  /* 触发 document mousedown，返回是否被 stopPropagation（=不会到达 window） */
  const fire = (target) => {
    let stopped = false;
    (doc._listeners["mousedown"] || []).slice().forEach((fn) => fn({
      type: "mousedown",
      target,
      stopPropagation() { stopped = true; },
    }));
    return stopped;
  };

  // 交互元素：必须拦截（否则 easy_drag 会拖走窗口，画布/输入被劫持）
  check("画布上按下被拦截（可正常绘制）", fire(new El("canvas")) === true);
  check("按钮上按下被拦截（点击不受影响）", fire(new El("button")) === true);
  check("输入框上按下被拦截（可选择文本）", fire(new El("input")) === true);
  const noDrag = new El("div");
  noDrag.className = "no-drag";
  check(".no-drag 元素被拦截", fire(noDrag) === true);
  const logo = new El("span");
  logo.className = "logo";
  check("标题栏 logo 被拦截", fire(logo) === true);

  // 非交互区域：不得拦截（easy_drag 据此拖动整窗）
  const card = new El("div");
  card.className = "card";
  check("卡片空白处不被拦截（可拖动窗口）", fire(card) === false);
  check("标题栏不被拦截（可拖动窗口）", fire(new El("header")) === false);
  const spanInCard = new El("span");
  card.appendChild(spanInCard);
  check("卡片内文字不被拦截（可拖动窗口）", fire(spanInCard) === false);

  // 嵌套：卡片内的画布仍要拦截；按钮内的图标同理（closest 向上匹配）
  const inner = new El("div");
  card.appendChild(inner);
  const nestedCanvas = new El("canvas");
  inner.appendChild(nestedCanvas);
  check("卡片内画布被拦截（嵌套路径正确）", fire(nestedCanvas) === true);
  const btn = new El("button");
  const iconSpan = new El("span");
  btn.appendChild(iconSpan);
  check("按钮内图标被拦截（closest 生效）", fire(iconSpan) === true);
}

/* ---------------- 场景 9：JS 语法自检 ---------------- */
function testSyntax() {
  console.log("\n[6/6] 语法自检");
  const files = ["webui/js/pages/region_capture.js", "webui/js/pages/screenshot.js",
                 "webui/js/window_drag.js", "webui/js/pages/clash.js",
                 "webui/js/pages/v2ray.js", "webui/js/pages/pan.js"];
  for (const f of files) {
    try {
      new vm.Script(fs.readFileSync(path.join(ROOT, f), "utf8"), { filename: f });
      check(f + " 语法正确", true);
    } catch (e) {
      check(f + " 语法正确", false, e.message);
    }
  }
  try {
    new vm.Script(extractBootScript(), { filename: "overlay-boot.js" });
    check("overlay.html 引导脚本语法正确", true);
  } catch (e) {
    check("overlay.html 引导脚本语法正确", false, e.message);
  }
  /* App.fmtDate 收「秒」，传毫秒会显示成 58674 年 → 静态拦住 */
  const bad = [];
  for (const f of fs.readdirSync(path.join(ROOT, "webui", "js", "pages"))) {
    if (!f.endsWith(".js")) continue;
    const src = fs.readFileSync(path.join(ROOT, "webui", "js", "pages", f), "utf8");
    const m = src.match(/fmtDate\([^)]*\*\s*1000/);
    if (m) bad.push(f + ": " + m[0]);
  }
  check("fmtDate 未被误传毫秒", bad.length === 0, bad.join(" | "));
}

/* ---------------- 场景 10：页面注册自检（Clash 面板独立页面） ---------------- */
function testPageRegistration() {
  console.log("\n[5/6] 页面注册自检");

  const load = (file) => {
    const pages = [];
    const App = {
      registerPage: (p) => pages.push(p),
      on: () => {},
      h: () => ({}), icon: () => "", esc: (x) => String(x),
      statusTag: () => ({}), row: () => ({}), svcCard: () => ({}),
      sec: () => ({}), makeLog: () => () => {}, fmtBytes: () => "",
      fmtDate: () => "", modal: async () => null, toast: () => {},
      tryCall: async () => ({ ok: true, data: {} }), prompt: async () => null,
      confirm: async () => true, navigate: async () => true,
      state: { cfg: {} }, pages: [],
    };
    const sandbox = {
      window: { App }, App, document: makeDoc(), console, Promise, Math, JSON,
      Object, Array, String, Number, Boolean, Date, RegExp, Set,
      setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
    };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(path.join(ROOT, "webui", "js", "pages", file), "utf8"),
                    sandbox, { filename: file });
    return pages;
  };

  const clash = load("clash.js");
  check("clash.js 注册单个 Clash 页（v2ray 式页签管理）",
        clash.length === 1 && clash[0].id === "clash", JSON.stringify(clash.map((p) => p.id)));
  check("归入「代理网络」分组（v5.0 重排）", clash.every((p) => p.group === "代理网络"),
        JSON.stringify(clash.map((p) => p.group)));
  check("页面带 mount / show",
        clash.every((p) => typeof p.mount === "function" && typeof p.show === "function"));
  const v2 = load("v2ray.js");
  check("v2ray.js 仍注册 V2rayN 页", v2.length === 1 && v2[0].id === "v2ray",
        JSON.stringify(v2.map((x) => x.id)));
  const routing = load("routing.js");
  check("routing.js 注册单个分流规则页（v2ray 式页签管理）",
        routing.length === 1 && routing[0].id === "routing",
        JSON.stringify(routing.map((p) => p.id)));
  check("分流规则页归入「代理网络」分组（v5.0 重排）",
        routing.every((p) => p.group === "代理网络"),
        JSON.stringify(routing.map((p) => p.group)));
  check("分流规则页带 mount / show",
        routing.every((p) => typeof p.mount === "function" && typeof p.show === "function"));
  const html = fs.readFileSync(path.join(ROOT, "webui", "index.html"), "utf8");
  check("index.html 已加载 clash.js", html.indexOf("js/pages/clash.js") >= 0);
  const iClash = html.indexOf("js/pages/clash.js");
  const iRouting = html.indexOf("js/pages/routing.js");
  check("index.html 在 clash.js 之后加载 routing.js",
        iClash >= 0 && iRouting > iClash, "clash@" + iClash + " routing@" + iRouting);

  /* v4.5 工具箱重构：合并/新增页注册、旧页删除 */
  const toolsPages = load("tools.js");
  const tids = toolsPages.map((p) => p.id);
  const wantNew = ["tools", "tool-text", "tool-files", "tool-uuid", "tool-radix"];
  for (const want of wantNew) {
    check("工具箱注册 " + want, tids.indexOf(want) >= 0, JSON.stringify(tids));
  }
  const gone = ["tool-format", "tool-codec", "tool-hash", "tool-verify",
                "tool-snapshot", "tool-export", "tool-ocr"];  // v5.1：OCR 独立成页
  const kept = tids.filter((x) => gone.includes(x));
  check("旧工具页已合并删除", kept.length === 0, "残留 " + JSON.stringify(kept));
  check("工具页总数 15（目录 + 14 工具页，tool-* 均 hidden；v5.1 OCR 独立）",
        toolsPages.length === 15 &&
        toolsPages.filter((p) => p.id !== "tools").every((p) => p.hidden),
        "n=" + toolsPages.length);
  const uiSrc = fs.readFileSync(path.join(ROOT, "webui", "js", "ui.js"), "utf8");
  check("ui.js 导出 toolOut / toolLauncher",
        uiSrc.indexOf("function toolOut(") >= 0 &&
        uiSrc.indexOf("function toolLauncher(") >= 0 &&
        uiSrc.indexOf("toolOut, toolLauncher,") >= 0);
  const toolsSrc = fs.readFileSync(path.join(ROOT, "webui", "js", "pages", "tools.js"), "utf8");
  check("tools.js 暴露 App.TOOL_CATALOG（Ctrl+K 数据源）",
        toolsSrc.indexOf("App.TOOL_CATALOG = ALL_TOOLS") >= 0);
}

async function main() {
  console.log("=".repeat(60));
  console.log("区域圈选 UI 交互回归测试（Node DOM 桩）");
  console.log("=".repeat(60));
  testRegionCapture();
  await testOverlayBoot();
  testWindowDragGuard();
  testPageRegistration();
  testSyntax();
  console.log("\n" + "=".repeat(60));
  if (failures) {
    console.log("[FAIL] " + failures + " 项未通过");
    process.exit(1);
  }
  console.log("[OK] 全部通过");
}

main();
