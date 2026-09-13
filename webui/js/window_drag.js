/* 窗口拖动守卫（配合 pywebview easy_drag）。

   无边框主窗口的“按住任意区域拖动整窗”由 pywebview 的 easy_drag 提供
   （它在 window 上挂 mousedown）。这对本应用是一把双刃剑：画布绘制、
   输入框选择文本等拖拽也会被当成拖动窗口，导致“一划线整个窗口跟着鼠标跑”。

   本模块在 document 冒泡阶段做拦截：按下点位于交互元素（画布 / 表单控件 /
   按钮 / 链接 / 可编辑内容 …）时 stopPropagation，事件不再到达 window，
   easy_drag 收不到 mousedown 就不会启动拖窗；其余区域（标题栏、卡片空白、
   页面背景）保持原行为——按住即可拖动窗口。

   为什么挂在 document 而不是 body：
   pywebview 在 body 上另有 .pywebview-drag-region 显式拖拽区的监听，
   它在 document 之前执行，不受本拦截影响（本应用未使用该类）。

   新增“按下后需自行处理拖拽”的元素时：给它加 no-drag 类，或在下方列表登记。 */
(function () {
  "use strict";

  var INTERACTIVE = [
    "canvas",           // 截图/图片编辑画布、OCR 叠层
    "video", "img", "iframe", "embed",
    "input", "textarea", "select", "option", "button", "a", "label",
    "[contenteditable='true']", "[contenteditable='']",
    ".no-drag",         // 手动登记：需要自行处理拖拽的元素
    ".logo",            // 标题栏 logo：点击不拖窗（与窗口按钮一致）
  ].join(",");

  document.addEventListener("mousedown", function (e) {
    var t = e.target;
    if (t && typeof t.closest === "function" && t.closest(INTERACTIVE)) {
      // 冒泡阶段：元素自身的处理器已执行，这里只阻断继续冒泡到 window
      e.stopPropagation();
    }
  }, false);

  /* ---------------- v5.3 边缘缩放 ----------------
     主窗口是无边框（FormBorderStyle.None），系统不提供缩放边框，
     **用户此前无法拖边调整窗口大小**（只能最大化，或改配置里的几何记忆）。

     实现方式与 pywebview 自带 easy_drag 同路数：mousemove 逐帧调桥接，
     后端用 pywebview 的 window.move/resize 落地。刻意不用 WM_NCLBUTTONDOWN ——
     那条路在 WebView2 上因鼠标捕获不在本进程而拖不动（见 win_begin_drag 注释）。

     坐标只传"增量"（screenX/Y 差值），避免与 DPI / pywebview 坐标系换算打架。 */
  var EDGE = 5;                 // 边缘命中宽度（CSS px）
  var MIN_W = 680, MIN_H = 460; // 与后端 win_resize_by 的下限保持一致
  var rz = null;

  function edgeAt(x, y) {
    var w = window.innerWidth, h = window.innerHeight;
    var L = x <= EDGE, R = x >= w - EDGE, T = y <= EDGE, B = y >= h - EDGE;
    if (!L && !R && !T && !B) return "";
    return (T ? "top" : B ? "bottom" : "") + (L ? "left" : R ? "right" : "");
  }

  function onRzMove(e) {
    if (!rz) return;
    var dx = e.screenX - rz.sx, dy = e.screenY - rz.sy;
    var w = rz.w + (rz.R ? dx : 0) + (rz.L ? -dx : 0);
    var h = rz.h + (rz.B ? dy : 0) + (rz.T ? -dy : 0);
    if (w < MIN_W) { w = MIN_W; if (rz.L) dx = rz.w - MIN_W; }
    if (h < MIN_H) { h = MIN_H; if (rz.T) dy = rz.h - MIN_H; }
    var dw = w - rz.sw, dh = h - rz.sh;
    var mx = rz.L ? dx - rz.smx : 0, my = rz.T ? dy - rz.smy : 0;
    rz.sw = w; rz.sh = h;
    if (rz.L) rz.smx = dx;
    if (rz.T) rz.smy = dy;
    if (mx || my) App.tryCall("win_move_by", mx, my);
    if (dw || dh) App.tryCall("win_resize_by", dw, dh);
  }

  function onRzUp() {
    rz = null;
    window.removeEventListener("mousemove", onRzMove);
    window.removeEventListener("mouseup", onRzUp);
  }

  // 捕获阶段：抢在 easy_drag 的 window mousedown（以及上面的拦截）之前处理边缘
  document.addEventListener("mousedown", function (e) {
    if (e.button !== 0) return;
    var edge = edgeAt(e.clientX, e.clientY);
    if (!edge) return;
    e.preventDefault();
    e.stopPropagation();
    rz = {
      L: edge.indexOf("left") >= 0, R: edge.indexOf("right") >= 0,
      T: edge.indexOf("top") >= 0, B: edge.indexOf("bottom") >= 0,
      sx: e.screenX, sy: e.screenY,
      w: window.innerWidth, h: window.innerHeight,
      sw: window.innerWidth, sh: window.innerHeight, smx: 0, smy: 0,
    };
    window.addEventListener("mousemove", onRzMove);
    window.addEventListener("mouseup", onRzUp);
  }, true);
})();
