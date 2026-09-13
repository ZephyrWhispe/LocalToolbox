(function(){
"use strict";
var h = App.h;
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


App.registerPage({
  id: "batch",
  title: "批量处理",
  group: "工具与增效",
  hidden: true,
  icon: "copy",
  backTo: "tools",
  mount: mount,
});

var state = { files: [], operations: [], running: false };

function mount(el) {
  el.classList.add("page-flex", "canvas-page");   // v5.3：画布页铺满内容区
  var toolbar = h("div", {class: "row", style: {gap: "8px", flexWrap: "wrap"}},
    h("button", {class: "btn sm", onclick: pickFiles}, "选择文件"),
    h("button", {class: "btn sm", onclick: pickDir}, "选择目录"),
    h("button", {class: "btn sm", onclick: addResizeOp}, "添加: 调整大小"),
    h("button", {class: "btn sm", onclick: addEffectOp}, "添加: 特效"),
    h("button", {class: "btn sm", onclick: addWatermarkOp}, "添加: 水印"),
    h("button", {class: "btn sm", onclick: addConvertOp}, "添加: 格式转换"),
    /* v5.3：.divider 只在 #titlebar / .editor-toolbar 下有样式，这里用内联
       复刻同一配方（此前是一个零尺寸的隐形元素） */
    h("span", {style: {width: "1px", height: "18px", background: "var(--border)",
      margin: "0 4px", flex: "none"}}),
    h("button", {class: "btn sm accent", onclick: startBatch}, "开始处理"),
    h("button", {class: "btn sm danger", onclick: stopBatch}, "取消")
  );

  var fileList = h("div", {id: "batch-files", class: "batch-file-list",
    style: {maxHeight: "150px", overflow: "auto"}});
  var opList = h("div", {id: "batch-ops", class: "batch-op-list",
    style: {maxHeight: "150px", overflow: "auto"}});
  var progress = h("div", {id: "batch-progress", class: "batch-progress",
    style: {display: "none"}},
    h("div", {id: "batch-progress-bar", class: "progress-bar"},
      h("div", {id: "batch-progress-fill"})),
    h("div", {id: "batch-progress-text",
      style: {marginTop: "4px", fontSize: "13px", color: "var(--muted)"}}, "就绪")
  );

  state.fileListEl = fileList;
  state.opListEl = opList;
  state.progressEl = progress;

  el.appendChild(h("div", {class: "page-head"},
    h("h2", null, "批量处理"),
    h("div", {class: "sub"}, "选文件 → 搭一条操作流水线（调整大小 / 特效 / 水印 / 格式转换）→ 一次处理完")));
  el.appendChild(h("div", {class: "card"}, toolbar));
  el.appendChild(h("div", {class: "card"},
    h("div", {class: "card-title"}, "待处理文件"),
    fileList));
  el.appendChild(h("div", {class: "card fill"},
    h("div", {class: "card-title"}, "处理流水线（按顺序执行）"),
    opList));
  el.appendChild(progress);

  App.on("batch_progress", function(d) { updateProgress(d); });
  App.on("batch_done", function(d) { onDone(d); });
}

function pickFiles() {
  call("batch_pick_files").then(function(r) {
    if (r.data && r.data.paths) {
      state.files = r.data.paths;
      renderFiles();
    }
  });
}

function pickDir() {
  call("batch_pick_dir").then(function(r) {
    if (r.data && r.data.paths) {
      state.files = r.data.paths;
      renderFiles();
    }
  });
}

function renderFiles() {
  var el = state.fileListEl;
  el.innerHTML = "";
  state.files.forEach(function(f, i) {
    el.appendChild(h("div", {style: {padding: "2px 0", fontSize: "13px"}},
      (i + 1) + ". " + f));
  });
}

/* 原生 prompt()/confirm() 在 WebView2 里被禁用（静默返回 null）→ 统一改用 App.modal */
var EFFECTS = {
  blur: {label: "模糊半径 (1-20)", def: "5", key: "radius", num: true},
  sharpen: {label: "强度 (0-100，可留空)", def: "", key: "value", num: true},
  brightness: {label: "亮度值 (-100~100)", def: "0", key: "value", num: true},
  contrast: {label: "对比度值 (-100~100)", def: "0", key: "value", num: true},
  saturation: {label: "饱和度值 (-100~100)", def: "0", key: "value", num: true},
  grayscale: null, invert: null, sepia: null,
  round_corners: {label: "圆角半径 (px)", def: "20", key: "radius", num: true},
};

function addResizeOp() {
  App.modal({
    title: "调整大小",
    body: "按目标宽度等比缩放（像素）",
    inputs: [{label: "目标宽度 (px)", value: "800", id: "w"}],
    okText: "添加",
  }).then(function(v) {
    if (!v) return;
    var w = parseInt(v.w, 10);
    if (!w || w < 1 || w > 20000) { App.toast("宽度需为 1-20000 的数字", "error"); return; }
    state.operations.push({type: "resize", width: w});
    renderOps();
  });
}

function addEffectOp() {
  App.modal({
    title: "添加特效",
    body: "可用特效：" + Object.keys(EFFECTS).join(" / "),
    inputs: [{label: "特效名称", value: "sharpen", id: "eff"}],
    okText: "下一步",
  }).then(function(v) {
    if (!v) return;
    var eff = String(v.eff || "").trim().toLowerCase();
    if (!EFFECTS.hasOwnProperty(eff)) {
      App.toast("不支持的特效：" + eff, "error", 6000);
      return;
    }
    var meta = EFFECTS[eff];
    if (!meta) {                       // 无参数特效
      state.operations.push({type: "effect", effect: eff, params: {}});
      renderOps();
      return;
    }
    App.modal({
      title: "特效参数 · " + eff,
      inputs: [{label: meta.label, value: meta.def, id: "val"}],
      okText: "添加",
    }).then(function(v2) {
      if (!v2) return;
      var params = {};
      var raw = String(v2.val == null ? "" : v2.val).trim();
      if (raw !== "") {
        var num = Number(raw);
        if (isNaN(num)) { App.toast("参数需为数字", "error"); return; }
        params[meta.key] = num;
      }
      state.operations.push({type: "effect", effect: eff, params: params});
      renderOps();
    });
  });
}

function addWatermarkOp() {
  App.modal({
    title: "添加文字水印",
    inputs: [{label: "水印文字", value: "", id: "text"}],
    okText: "添加",
  }).then(function(v) {
    if (!v) return;
    var text = String(v.text || "").trim();
    if (!text) { App.toast("水印文字不能为空", "warn"); return; }
    state.operations.push({type: "watermark_text", text: text, font_size: 36,
      color: "#FFFFFF", opacity: 128, position: "bottom-right"});
    renderOps();
  });
}

function addConvertOp() {
  App.modal({
    title: "格式转换",
    inputs: [{label: "目标格式 (png/jpg/webp/bmp)", value: "jpg", id: "fmt"}],
    okText: "添加",
  }).then(function(v) {
    if (!v) return;
    var fmt = String(v.fmt || "").trim().toLowerCase();
    if (["png", "jpg", "jpeg", "webp", "bmp"].indexOf(fmt) < 0) {
      App.toast("目标格式需为 png / jpg / webp / bmp", "error");
      return;
    }
    state.operations.push({type: "convert", format: fmt});
    renderOps();
  });
}

function renderOps() {
  var el = state.opListEl;
  el.innerHTML = "";
  state.operations.forEach(function(op, i) {
    var label = op.type;
    if (op.type === "resize") label = "调整大小 → " + op.width + "px";
    else if (op.type === "effect") {
      label = "特效: " + op.effect;
      if (op.params && Object.keys(op.params).length) {
        label += " (" + Object.entries(op.params).map(function(kv) { return kv[0] + "=" + kv[1]; }).join(", ") + ")";
      }
    }
    else if (op.type === "watermark_text") label = "水印: " + op.text;
    else if (op.type === "convert") label = "转换: " + op.format;
    el.appendChild(h("div", {style: {padding: "2px 0", fontSize: "13px"}},
      (i + 1) + ". " + label + " ",
      h("button", {class: "btn xs danger", onclick: function() {
        state.operations.splice(i, 1);
        renderOps();
      }}, "移除")
    ));
  });
}

function startBatch() {
  if (!state.files.length) return App.toast("请先选择文件", "warn");
  if (!state.operations.length) return App.toast("请添加处理操作", "warn");
  /* 用系统目录选择器（原生 prompt() 在 WebView2 里不可用） */
  App.toast("请选择输出目录…", "info", 2500);
  call("batch_pick_dir").then(function(r) {
    var paths = (r.data && r.data.paths) || [];
    if (!paths.length) return;
    state.running = true;
    state.progressEl.style.display = "block";
    call("batch_start", state.files, state.operations, paths[0]).then(function(r2) {
      if (!r2.ok) {
        state.running = false;
        state.progressEl.style.display = "none";
        App.toast(r2.err || "启动失败", "error");
      }
    });
  });
}

function stopBatch() {
  call("batch_stop");
  state.running = false;
}

function updateProgress(d) {
  var pct = d.total > 0 ? Math.round(d.current / d.total * 100) : 0;
  var fill = document.getElementById("batch-progress-fill");
  var text = document.getElementById("batch-progress-text");
  if (fill) fill.style.width = pct + "%";
  if (text) text.textContent = d.current + " / " + d.total + " — " + (d.path || "");
}

function onDone(d) {
  state.running = false;
  var text = document.getElementById("batch-progress-text");
  if (text) text.textContent = "完成！处理 " + (d.count || 0) + " 个文件" +
    (d.errors && d.errors.length ? "，" + d.errors.length + " 个失败" : "");
  App.toast("批量处理完成", "success");
}

})();
