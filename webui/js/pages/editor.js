(function(){
"use strict";
var h = App.h, tryCall = App.tryCall;
/* 兼容层：本页按 {ok, data} 约定书写（早期 App.call 的返回形式）。
   现在 App.call 直接返回 data 且在失败时抛错 —— 在这里统一适配，
   失败返回 {ok:false} 并弹 toast，避免整页功能静默失效。 */
var call = function (name) {
  var args = Array.prototype.slice.call(arguments, 1);
  return App.tryCall.apply(App, [name].concat(args)).then(function (r) {
    if (!r.ok) App.toast(r.err, "error");
    return r;
  });
};


var state = {
  dataUrl: "",
  path: "",
  dirty: false,
  history: [],
  historyIdx: -1,
  maxHistory: 12,
  mode: "filter", // "filter" = 滤镜编辑模式，"annotate" = 标注模式
};

App.registerPage({
  id: "editor",
  title: "图片编辑",
  group: "工具与增效",
  hidden: true,
  icon: "image",
  backTo: "tools",
  mount: mount,
});

function mount(el) {
  var bar = h("div", {class: "editor-toolbar"},
    h("button", {class: "btn sm", onclick: openFile}, "打开图片"),
    h("button", {class: "btn sm", onclick: saveFile}, "保存"),
    h("button", {class: "btn sm", onclick: saveAs}, "另存为"),
    h("span", {class: "divider"}),
    h("button", {class: "btn sm", onclick: undo}, "撤销"),
    h("button", {class: "btn sm", onclick: redo}, "重做"),
    h("span", {class: "divider"}),
    h("button", {class: "btn sm", id: "mode-toggle-btn", onclick: toggleMode}, "切换到标注模式"),
    h("button", {class: "btn sm", onclick: function(){ showEffectsPanel(); }}, "特效"),
    h("button", {class: "btn sm", onclick: function(){ showWatermarkPanel(); }}, "水印"),
    h("button", {class: "btn sm", onclick: function(){ showResizePanel(); }}, "调整大小"),
    h("button", {class: "btn sm", onclick: function(){ showFormatPanel(); }}, "格式转换"),
    h("button", {class: "btn sm", onclick: function(){ showPalettePanel(); }}, "调色板"),
    h("span", {class: "divider"}),
    h("button", {class: "btn sm accent", onclick: pinToScreen}, "贴图"),
    h("button", {class: "btn sm", onclick: uploadImage}, "上传"),
  );

  // 标注工具栏（默认隐藏）
  var annotateBar = h("div", {class: "editor-annotate-toolbar", style: {display: "none"}},
    h("span", {style: {fontSize: "12px", marginRight: "8px"}}, "标注工具:"),
    h("button", {class: "btn sm tool-btn active", "data-tool": "brush", onclick: selectTool}, "画笔"),
    h("button", {class: "btn sm tool-btn", "data-tool": "highlight", onclick: selectTool}, "高亮"),
    h("button", {class: "btn sm tool-btn", "data-tool": "rect", onclick: selectTool}, "矩形"),
    h("button", {class: "btn sm tool-btn", "data-tool": "ellipse", onclick: selectTool}, "椭圆"),
    h("button", {class: "btn sm tool-btn", "data-tool": "arrow", onclick: selectTool}, "箭头"),
    h("button", {class: "btn sm tool-btn", "data-tool": "text", onclick: selectTool}, "文字"),
    h("button", {class: "btn sm tool-btn", "data-tool": "mosaic", onclick: selectTool}, "马赛克"),
    h("button", {class: "btn sm tool-btn", "data-tool": "crop", onclick: selectTool}, "裁剪"),
    h("button", {class: "btn sm tool-btn", "data-tool": "eraser", onclick: selectTool}, "橡皮"),
    h("span", {class: "divider"}),
    h("input", {type: "color", id: "annotate-color", value: "#ff4d4f", style: {width: "32px", height: "28px"}}),
    h("input", {type: "range", id: "annotate-size", min: "1", max: "20", value: "4", style: {width: "80px"}}),
  );

  var canvasWrap = h("div", {class: "editor-canvas-wrap"});
  var canvas = h("canvas", {class: "editor-canvas"});
  canvasWrap.appendChild(canvas);
  state.canvas = canvas;
  state.ctx = canvas.getContext("2d");

  var infoBar = h("div", {class: "editor-info"}, "未打开图片");
  state.infoBar = infoBar;

  var effectsPanel = h("div", {class: "editor-panel", style: {display: "none"}});
  state.effectsPanel = effectsPanel;

  el.appendChild(bar);
  el.appendChild(annotateBar);
  state.annotateBar = annotateBar;
  el.appendChild(canvasWrap);
  el.appendChild(effectsPanel);
  el.appendChild(infoBar);
  
  // 初始化标注状态
  state.annotateState = {
    tool: "brush",
    color: "#ff4d4f",
    size: 4,
    drawing: false,
    start: null,
    cur: null,
    pts: [],
    snap: null,
    stepN: 0,
    cropRect: null,
  };
  
  // 绑定画布事件（标注模式）
  canvas.addEventListener("mousedown", onCanvasMouseDown);
  canvas.addEventListener("mousemove", onCanvasMouseMove);
  canvas.addEventListener("mouseup", onCanvasMouseUp);
  // 在画布外释放也要结束描边，避免 drawing 卡住导致后续鼠标移动持续绘制
  window.addEventListener("mouseup", onCanvasMouseUp);
}

function openFile() {
  call("editor_open").then(function(r) {
    if (r.data && r.data.data_url) {
      loadImage(r.data.data_url, r.data.path);
    }
  }).catch(function(e) { App.toast(e.message || e, "error"); });
}

function loadImage(dataUrl, path) {
  state.dataUrl = dataUrl;
  state.path = path || "";
  state.dirty = false;
  state.history = [dataUrl];
  state.historyIdx = 0;
  drawImage(dataUrl);
  call("editor_info", dataUrl).then(function(r) {
    if (r.data) {
      state.infoBar.textContent = "%s × %s  |  %s  |  %s".format(
        r.data.width, r.data.height, r.data.mode,
        App.fmtBytes(r.data.size_bytes));
    }
  });
}

function drawImage(dataUrl) {
  var img = new Image();
  img.onload = function() {
    var c = state.canvas, ctx = state.ctx;
    var maxW = c.parentElement.clientWidth - 20;
    var maxH = window.innerHeight - 200;
    var scale = Math.min(maxW / img.width, maxH / img.height, 1);
    c.width = img.width * scale;
    c.height = img.height * scale;
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.drawImage(img, 0, 0, c.width, c.height);
    state._imgScale = scale;
    state._imgW = img.width;
    state._imgH = img.height;
  };
  img.src = dataUrl;
}

function pushHistory(dataUrl) {
  state.history = state.history.slice(0, state.historyIdx + 1);
  state.history.push(dataUrl);
  if (state.history.length > state.maxHistory) state.history.shift();
  state.historyIdx = state.history.length - 1;
  state.dirty = true;
}

function undo() {
  if (state.historyIdx > 0) {
    state.historyIdx--;
    state.dataUrl = state.history[state.historyIdx];
    drawImage(state.dataUrl);
  }
}

function redo() {
  if (state.historyIdx < state.history.length - 1) {
    state.historyIdx++;
    state.dataUrl = state.history[state.historyIdx];
    drawImage(state.dataUrl);
  }
}

function saveFile() {
  if (!state.dataUrl) return App.toast("没有可保存的图片", "warn");
  if (state.path) {
    call("editor_save", state.dataUrl, state.path).then(function() {
      App.toast("已保存", "success");
      state.dirty = false;
    }).catch(function(e) { App.toast(e.message || e, "error"); });
  } else {
    saveAs();
  }
}

function saveAs() {
  if (!state.dataUrl) return App.toast("没有可保存的图片", "warn");
  /* 原生 prompt() 在 WebView2 里被禁用（静默返回 null）→ 用 App.modal */
  App.modal({
    title: "另存为",
    inputs: [{label: "格式 (png/jpg/bmp/webp)", value: "png", id: "fmt"}],
    okText: "选择位置并保存",
  }).then(function(v) {
    if (!v) return;
    /* App.modal 多输入返回值数组（此前误用 v.fmt 取对象属性，恒为空导致转换被拦） */
    var fmt = String(v[0] || "").trim().toLowerCase();
    if (["png", "jpg", "jpeg", "bmp", "webp"].indexOf(fmt) < 0) {
      App.toast("格式需为 png / jpg / bmp / webp", "error");
      return;
    }
    call("editor_save_as", state.dataUrl, fmt).then(function(r) {
      if (r.data && r.data.path) {
        state.path = r.data.path;
        state.dirty = false;
        App.toast("已保存到 " + r.data.path, "success");
      }
    }).catch(function(e) { App.toast(e.message || e, "error"); });
  });
}

function showEffectsPanel() {
  if (!state.dataUrl) return App.toast("请先打开图片", "warn");
  var p = state.effectsPanel;
  p.innerHTML = "";
  p.style.display = "block";

  var effects = [
    {name: "blur", label: "模糊", params: [{key: "radius", label: "半径", def: 3, min: 1, max: 20}]},
    {name: "sharpen", label: "锐化", params: [{key: "factor", label: "强度", def: 1.5, min: 0.5, max: 5}]},
    {name: "brightness", label: "亮度", params: [{key: "factor", label: "强度", def: 1.2, min: 0.1, max: 3}]},
    {name: "contrast", label: "对比度", params: [{key: "factor", label: "强度", def: 1.3, min: 0.1, max: 3}]},
    {name: "saturation", label: "饱和度", params: [{key: "factor", label: "强度", def: 1.5, min: 0, max: 3}]},
    {name: "grayscale", label: "灰度", params: []},
    {name: "invert", label: "反色", params: []},
    {name: "sepia", label: "复古", params: []},
    {name: "round_corners", label: "圆角", params: [{key: "radius", label: "半径", def: 20, min: 1, max: 200}]},
  ];

  effects.forEach(function(eff) {
    var row = h("div", {class: "effect-row"});
    var btn = h("button", {class: "btn sm", onclick: function() {
      var params = {};
      eff.params.forEach(function(p) {
        var input = row.querySelector("[data-key='" + p.key + "']");
        if (input) params[p.key] = parseFloat(input.value);
      });
      applyEffect(eff.name, params);
    }}, eff.label);
    row.appendChild(btn);
    eff.params.forEach(function(p) {
      var label = h("span", {}, p.label + ":");
      var input = h("input", {type: "range", "data-key": p.key,
        min: p.min, max: p.max, step: 0.1, value: p.def,
        style: {width: "100px", verticalAlign: "middle"}});
      var val = h("span", {}, String(p.def));
      input.oninput = function() { val.textContent = input.value; };
      row.appendChild(label);
      row.appendChild(input);
      row.appendChild(val);
    });
    p.appendChild(row);
  });

  p.appendChild(h("button", {class: "btn sm", onclick: function() {
    p.style.display = "none";
  }}, "关闭面板"));
}

function applyEffect(name, params) {
  call("editor_apply_effect", state.dataUrl, name, params).then(function(r) {
    if (r.data && r.data.data_url) {
      state.dataUrl = r.data.data_url;
      pushHistory(state.dataUrl);
      drawImage(state.dataUrl);
    }
  }).catch(function(e) { App.toast(e.message || e, "error"); });
}

function showWatermarkPanel() {
  if (!state.dataUrl) return App.toast("请先打开图片", "warn");
  App.prompt("水印文字:").then(function(text) {
    if (!text) return;
    call("editor_watermark_text", state.dataUrl, text, 36, "#FFFFFF", 128, "bottom-right")
      .then(function(r) {
        if (r.data && r.data.data_url) {
          state.dataUrl = r.data.data_url;
          pushHistory(state.dataUrl);
          drawImage(state.dataUrl);
          App.toast("水印已添加", "success");
        }
      }).catch(function(e) { App.toast(e.message || e, "error"); });
  });
}

function showResizePanel() {
  if (!state.dataUrl) return App.toast("请先打开图片", "warn");
  App.prompt("目标宽度 (px):").then(function(w) {
    if (!w) return;
    call("editor_resize", state.dataUrl, parseInt(w)).then(function(r) {
      if (r.data && r.data.data_url) {
        state.dataUrl = r.data.data_url;
        pushHistory(state.dataUrl);
        drawImage(state.dataUrl);
        App.toast("已调整大小", "success");
      }
    }).catch(function(e) { App.toast(e.message || e, "error"); });
  });
}

function showFormatPanel() {
  if (!state.dataUrl) return App.toast("请先打开图片", "warn");
  App.modal({
    title: "转换格式",
    inputs: [{label: "目标格式 (png/jpg/bmp/webp)", value: "jpg", id: "fmt"}],
    okText: "开始转换",
  }).then(function(v) {
    if (!v) return;
    /* App.modal 多输入返回值数组（此前误用 v.fmt 取对象属性，恒为空导致转换被拦） */
    var fmt = String(v[0] || "").trim().toLowerCase();
    if (["png", "jpg", "jpeg", "bmp", "webp"].indexOf(fmt) < 0) {
      App.toast("格式需为 png / jpg / bmp / webp", "error");
      return;
    }
    call("editor_convert_format", state.dataUrl, fmt).then(function(r) {
      if (r.data && r.data.data_url) {
        state.dataUrl = r.data.data_url;
        pushHistory(state.dataUrl);
        drawImage(state.dataUrl);
        App.toast("已转换格式", "success");
      }
    }).catch(function(e) { App.toast(e.message || e, "error"); });
  });
}

function showPalettePanel() {
  if (!state.dataUrl) return App.toast("请先打开图片", "warn");
  call("editor_extract_palette", state.dataUrl, 8).then(function(r) {
    if (!r.data) return;
    var colors = r.data;
    var html = colors.map(function(c) {
      return '<span style="display:inline-block;width:32px;height:32px;background:' +
        c.hex + ';border-radius:4px;margin:2px;cursor:pointer;vertical-align:middle" ' +
        'title="' + c.hex + ' (' + c.percent + '%)" ' +
        'onclick="App.copyText(\'' + c.hex + '\')"></span>';
    }).join("");
    App.toast("已提取 " + colors.length + " 种主色调，点击色块复制 HEX");
    var panel = h("div", {class: "palette-panel", html: html});
    state.effectsPanel.innerHTML = "";
    state.effectsPanel.style.display = "block";
    state.effectsPanel.appendChild(panel);
  }).catch(function(e) { App.toast(e.message || e, "error"); });
}

function pinToScreen() {
  if (!state.dataUrl) return App.toast("请先打开图片", "warn");
  call("pin_image", state.dataUrl).then(function(r) {
    if (r.data) App.toast("已贴图 (ID: " + r.data.pin_id + ")", "success");
  }).catch(function(e) { App.toast(e.message || e, "error"); });
}

function uploadImage() {
  if (!state.dataUrl) return App.toast("请先打开图片", "warn");
  /* v5.3：配置键名此前写错（upload_provider），而后端 config.DEFAULTS 里叫
     upload_service —— 读取永远拿到 undefined，于是"自定义图床"的设置在图片编辑器的
     上传功能里被静默忽略、始终走 imgur。由 scripts/check_contract.py 查出。 */
  var provider = App.state.cfg.upload_service || "imgur";
  call("upload_image", state.dataUrl, provider).then(function(r) {
    if (r.data && r.data.url) {
      App.copyText(r.data.url);
      App.toast("已上传: " + r.data.url + " (链接已复制)", "success");
    } else {
      App.toast(r.err || "上传失败", "error");
    }
  }).catch(function(e) { App.toast(e.message || e, "error"); });
}

// ==================== 标注模式功能 ====================

function toggleMode() {
  if (state.mode === "filter") {
    state.mode = "annotate";
    document.getElementById("mode-toggle-btn").textContent = "切换到滤镜模式";
    state.annotateBar.style.display = "flex";
    // 切换到标注模式时，将当前图片加载到画布并启用交互
    if (state.dataUrl) {
      loadToCanvasForAnnotation(state.dataUrl);
    }
  } else {
    state.mode = "filter";
    document.getElementById("mode-toggle-btn").textContent = "切换到标注模式";
    state.annotateBar.style.display = "none";
    // 切换回滤镜模式时，将画布内容转回 dataUrl
    if (state.canvas && state.ctx) {
      canvasToDataUrl();
    }
  }
}

function loadToCanvasForAnnotation(dataUrl) {
  var img = new Image();
  img.onload = function() {
    var c = state.canvas;
    var ctx = state.ctx;
    var maxW = c.parentElement.clientWidth - 20;
    var maxH = window.innerHeight - 200;
    var scale = Math.min(maxW / img.width, maxH / img.height, 1);
    c.width = img.width * scale;
    c.height = img.height * scale;
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.drawImage(img, 0, 0, c.width, c.height);
    state._imgScale = scale;
    state._imgW = img.width;
    state._imgH = img.height;
    // 保存初始状态用于撤销
    pushUndo();
  };
  img.src = dataUrl;
}

function canvasToDataUrl() {
  try {
    var dataUrl = state.canvas.toDataURL("image/png");
    state.dataUrl = dataUrl;
    pushHistory(dataUrl);
  } catch(e) {
    console.error("Canvas to data URL failed:", e);
  }
}

function selectTool(e) {
  var btn = e.target;
  var tool = btn.getAttribute("data-tool");
  if (!tool) return;
  
  // 更新按钮状态
  var buttons = state.annotateBar.querySelectorAll(".tool-btn");
  buttons.forEach(function(b) { b.classList.remove("active"); });
  btn.classList.add("active");
  
  state.annotateState.tool = tool;
  
  // 如果是步进工具，重置计数
  if (tool === "step") {
    state.annotateState.stepN = 0;
  }
}

function getCanvasPos(e) {
  var rect = state.canvas.getBoundingClientRect();
  var scaleX = state.canvas.width / rect.width;
  var scaleY = state.canvas.height / rect.height;
  return {
    x: (e.clientX - rect.left) * scaleX,
    y: (e.clientY - rect.top) * scaleY,
  };
}

function onCanvasMouseDown(e) {
  if (state.mode !== "annotate") return;
  var pos = getCanvasPos(e);
  var as = state.annotateState;
  
  as.drawing = true;
  as.start = pos;
  as.cur = pos;
  as.pts = [pos];
  
  // 保存快照（用于形状预览）
  as.snap = state.ctx.getImageData(0, 0, state.canvas.width, state.canvas.height);
  
  // 文字工具：点击即输入
  if (as.tool === "text") {
    drawText(pos.x, pos.y);
    as.drawing = false;
  }
}

function onCanvasMouseMove(e) {
  if (state.mode !== "annotate" || !state.annotateState.drawing) return;
  var pos = getCanvasPos(e);
  var as = state.annotateState;
  
  as.cur = pos;
  as.pts.push(pos);
  
  var ctx = state.ctx;
  
  // 恢复快照
  if (as.snap) {
    ctx.putImageData(as.snap, 0, 0);
  }
  
  // 根据工具绘制
  switch(as.tool) {
    case "brush":
      drawBrush(ctx, as.pts);
      break;
    case "highlight":
      drawHighlight(ctx, as.pts);
      break;
    case "rect":
      drawShape(ctx, as.start.x, as.start.y, pos.x, pos.y, "rect");
      break;
    case "ellipse":
      drawShape(ctx, as.start.x, as.start.y, pos.x, pos.y, "ellipse");
      break;
    case "arrow":
      drawArrow(ctx, as.start.x, as.start.y, pos.x, pos.y);
      break;
    case "mosaic":
      applyMosaic(ctx, as.start.x, as.start.y, pos.x, pos.y);
      break;
    case "crop":
      drawCropRect(ctx, as.start.x, as.start.y, pos.x, pos.y);
      break;
    case "eraser":
      eraseArea(ctx, pos.x, pos.y);
      break;
  }
}

function onCanvasMouseUp(e) {
  if (state.mode !== "annotate" || !state.annotateState.drawing) return;
  var as = state.annotateState;
  as.drawing = false;
  
  // 完成绘制，保存到历史
  if (as.tool !== "crop") {
    canvasToDataUrl();
  } else {
    // 裁剪工具：执行裁剪
    executeCrop();
  }
}

function drawBrush(ctx, pts) {
  if (pts.length < 2) return;
  var colorInput = document.getElementById("annotate-color");
  var sizeInput = document.getElementById("annotate-size");
  var color = colorInput ? colorInput.value : "#ff4d4f";
  var size = sizeInput ? parseInt(sizeInput.value) : 4;
  
  ctx.strokeStyle = color;
  ctx.lineWidth = size;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (var i = 1; i < pts.length; i++) {
    ctx.lineTo(pts[i].x, pts[i].y);
  }
  ctx.stroke();
}

function drawHighlight(ctx, pts) {
  if (pts.length < 2) return;
  ctx.strokeStyle = "rgba(255, 255, 0, 0.3)";
  ctx.lineWidth = 20;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (var i = 1; i < pts.length; i++) {
    ctx.lineTo(pts[i].x, pts[i].y);
  }
  ctx.stroke();
}

function drawShape(ctx, x1, y1, x2, y2, type) {
  var colorInput = document.getElementById("annotate-color");
  var sizeInput = document.getElementById("annotate-size");
  var color = colorInput ? colorInput.value : "#ff4d4f";
  var size = sizeInput ? parseInt(sizeInput.value) : 4;
  
  ctx.strokeStyle = color;
  ctx.lineWidth = size;
  ctx.beginPath();
  
  if (type === "rect") {
    ctx.rect(x1, y1, x2 - x1, y2 - y1);
  } else if (type === "ellipse") {
    var cx = (x1 + x2) / 2;
    var cy = (y1 + y2) / 2;
    var rx = Math.abs(x2 - x1) / 2;
    var ry = Math.abs(y2 - y1) / 2;
    ctx.ellipse(cx, cy, rx, ry, 0, 0, 2 * Math.PI);
  }
  ctx.stroke();
}

function drawArrow(ctx, x1, y1, x2, y2) {
  var colorInput = document.getElementById("annotate-color");
  var sizeInput = document.getElementById("annotate-size");
  var color = colorInput ? colorInput.value : "#ff4d4f";
  var size = sizeInput ? parseInt(sizeInput.value) : 4;
  
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = size;
  
  // 主线
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
  
  // 箭头
  var angle = Math.atan2(y2 - y1, x2 - x1);
  var arrowLen = 15;
  ctx.beginPath();
  ctx.moveTo(x2, y2);
  ctx.lineTo(x2 - arrowLen * Math.cos(angle - Math.PI / 6), y2 - arrowLen * Math.sin(angle - Math.PI / 6));
  ctx.lineTo(x2 - arrowLen * Math.cos(angle + Math.PI / 6), y2 - arrowLen * Math.sin(angle + Math.PI / 6));
  ctx.closePath();
  ctx.fill();
}

function drawText(x, y) {
  App.prompt("输入文字:").then(function(text) {
    if (!text) return;
    var ctx = state.ctx;
    var colorInput = document.getElementById("annotate-color");
    var color = colorInput ? colorInput.value : "#ff4d4f";
    
    ctx.font = "bold 24px 'Microsoft YaHei UI', sans-serif";
    ctx.fillStyle = color;
    ctx.fillText(text, x, y);
    canvasToDataUrl();
  });
}

function applyMosaic(ctx, x1, y1, x2, y2) {
  var blockSize = 10;
  var sx = Math.min(x1, x2);
  var sy = Math.min(y1, y2);
  var sw = Math.abs(x2 - x1);
  var sh = Math.abs(y2 - y1);
  
  var imageData = ctx.getImageData(sx, sy, sw, sh);
  var data = imageData.data;
  
  for (var y = 0; y < sh; y += blockSize) {
    for (var x = 0; x < sw; x += blockSize) {
      var r = 0, g = 0, b = 0, count = 0;
      // 计算块内平均颜色
      for (var dy = 0; dy < blockSize && y + dy < sh; dy++) {
        for (var dx = 0; dx < blockSize && x + dx < sw; dx++) {
          var idx = ((y + dy) * sw + (x + dx)) * 4;
          r += data[idx];
          g += data[idx + 1];
          b += data[idx + 2];
          count++;
        }
      }
      r = Math.round(r / count);
      g = Math.round(g / count);
      b = Math.round(b / count);
      // 填充块
      for (var dy = 0; dy < blockSize && y + dy < sh; dy++) {
        for (var dx = 0; dx < blockSize && x + dx < sw; dx++) {
          var idx = ((y + dy) * sw + (x + dx)) * 4;
          data[idx] = r;
          data[idx + 1] = g;
          data[idx + 2] = b;
        }
      }
    }
  }
  
  ctx.putImageData(imageData, sx, sy);
}

function drawCropRect(ctx, x1, y1, x2, y2) {
  var color = "rgba(79, 156, 255, 0.3)";
  ctx.fillStyle = color;
  ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
  ctx.strokeStyle = "#4f9cff";
  ctx.lineWidth = 2;
  ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
}

function executeCrop() {
  var as = state.annotateState;
  if (!as.start || !as.cur) return;
  
  var x1 = Math.min(as.start.x, as.cur.x);
  var y1 = Math.min(as.start.y, as.cur.y);
  var w = Math.abs(as.cur.x - as.start.x);
  var h = Math.abs(as.cur.y - as.start.y);
  
  if (w < 10 || h < 10) return; // 太小忽略
  
  // 创建临时canvas进行裁剪
  var tempCanvas = document.createElement("canvas");
  tempCanvas.width = w;
  tempCanvas.height = h;
  var tempCtx = tempCanvas.getContext("2d");
  tempCtx.drawImage(state.canvas, x1, y1, w, h, 0, 0, w, h);
  
  // 调整主canvas大小
  state.canvas.width = w;
  state.canvas.height = h;
  state.ctx.drawImage(tempCanvas, 0, 0);
  
  canvasToDataUrl();
  App.toast("已裁剪", "success");
}

function eraseArea(ctx, x, y) {
  var sizeInput = document.getElementById("annotate-size");
  var size = sizeInput ? parseInt(sizeInput.value) : 20;
  ctx.clearRect(x - size/2, y - size/2, size, size);
}


if (!String.prototype.format) {
  String.prototype.format = function() {
    var args = arguments;
    return this.replace(/%s/g, function() { return args[Array.prototype.indexOf.call(arguments, arguments[0])] || arguments[0] || ""; });
  };
}

})();
