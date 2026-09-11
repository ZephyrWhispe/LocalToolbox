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
})();
