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
  id: "video",
  title: "视频编辑",
  group: "工具与增效",
  hidden: true,
  icon: "video",
  backTo: "tools",
  mount: mount,
});

var state = { path: null, info: null };

function mount(el) {
  el.classList.add("page-flex", "canvas-page");   // v5.3：画布页铺满内容区
  var toolbar = h("div", {class: "row", style: {gap: "8px", flexWrap: "wrap"}},
    h("button", {class: "btn sm", onclick: pickFile}, "打开视频"),
    h("button", {class: "btn sm", onclick: showTrim}, "裁剪"),
    h("button", {class: "btn sm", onclick: showToGif}, "转 GIF"),
    h("button", {class: "btn sm", onclick: showExtract}, "提取帧")
  );

  var infoPanel = h("div", {id: "video-info", class: "video-info"});
  var player = h("div", {id: "video-player"});

  el.appendChild(h("div", {class: "page-head"},
    h("h2", null, "视频编辑"),
    h("div", {class: "sub"}, "查看视频信息，做裁剪 / 转 GIF / 提取帧（依赖 ffmpeg）")));
  el.appendChild(h("div", {class: "card"}, toolbar));
  el.appendChild(h("div", {class: "card fill"},
    h("div", {class: "card-title"}, "视频信息与预览"),
    infoPanel, player));

  App.on("video_trim_done", function(r) {
    if (r.ok || r.path) App.toast("裁剪完成: " + (r.path || r.data && r.data.path), "success");
    else App.toast(r.err || "裁剪失败", "error");
  });
  App.on("video_gif_done", function(r) {
    if (r.ok || r.path) App.toast("GIF 转换完成: " + (r.path || r.data && r.data.path), "success");
    else App.toast(r.err || "转换失败", "error");
  });
  App.on("video_frame_done", function(r) {
    if (r.ok || r.path) App.toast("帧提取完成: " + (r.path || r.data && r.data.path), "success");
    else App.toast(r.err || "提取失败", "error");
  });
}

function pickFile() {
  call("video_pick_file").then(function(r) {
    if (r.data) {
      state.info = r.data;
      state.path = r.data.path;
      renderInfo();
      renderPlayer();
    }
  }).catch(function(e) { App.toast(e.message || e, "error"); });
}

function renderInfo() {
  var el = document.getElementById("video-info");
  if (!el || !state.info) return;
  var info = state.info;
  el.innerHTML = "";
  var rows = [
    ["文件", info.path || ""],
    ["时长", fmtDuration(info.duration)],
    ["分辨率", info.width + " × " + info.height],
    ["帧率", info.fps + " fps"],
    ["编码", info.codec],
    ["大小", App.fmtBytes(info.size)],
  ];
  rows.forEach(function(r) {
    el.appendChild(h("div", {style: {padding: "2px 0", fontSize: "13px"}},
      h("b", {}, r[0] + ": "), r[1]));
  });
}

function renderPlayer() {
  var el = document.getElementById("video-player");
  if (!el || !state.path) return;
  el.innerHTML = "";
  
  // 优先使用后端提供的 video_url（HTTP），否则回退到 file:// 协议
  var videoSrc = state.info && state.info.video_url 
    ? state.info.video_url 
    : "file://" + state.path;
  
  var video = h("video", {
    src: videoSrc,
    controls: true,
    style: {maxWidth: "100%", maxHeight: "400px", background: "#000"},
  });
  el.appendChild(video);
}

function showTrim() {
  if (!state.path) return App.toast("请先打开视频", "warn");
  App.modal({
    title: "视频裁剪",
    inputs: [
      {label: "开始时间 (秒)", value: "0", id: "trim-start"},
      {label: "结束时间 (秒)", value: String(Math.floor((state.info.duration || 60) / 2)), id: "trim-end"},
    ],
    ok: "开始裁剪",
  }).then(function(vals) {
    if (!vals) return;
    call("video_trim", parseFloat(vals["trim-start"]), parseFloat(vals["trim-end"]))
      .then(function(r) {
        if (r.ok) App.toast("裁剪已开始...", "info");
      });
  });
}

function showToGif() {
  if (!state.path) return App.toast("请先打开视频", "warn");
  App.modal({
    title: "视频转 GIF",
    inputs: [
      {label: "帧率 (fps)", value: "10", id: "gif-fps"},
      {label: "开始时间 (秒)", value: "0", id: "gif-start"},
      {label: "结束时间 (秒，空=到末尾)", value: "", id: "gif-end"},
      {label: "宽度 (px)", value: "480", id: "gif-width"},
    ],
    ok: "开始转换",
  }).then(function(vals) {
    if (!vals) return;
    call("video_to_gif", parseInt(vals["gif-fps"]),
      parseFloat(vals["gif-start"]),
      vals["gif-end"] ? parseFloat(vals["gif-end"]) : null,
      parseInt(vals["gif-width"]))
      .then(function(r) {
        if (r.ok) App.toast("GIF 转换已开始...", "info");
      });
  });
}

function showExtract() {
  if (!state.path) return App.toast("请先打开视频", "warn");
  App.prompt("提取时间点 (秒):", "5").then(function(t) {
    if (!t) return;
    call("video_extract_frame", parseFloat(t)).then(function(r) {
      if (r.ok) App.toast("帧提取已开始...", "info");
    });
  });
}

function fmtDuration(sec) {
  if (!sec) return "0:00";
  var m = Math.floor(sec / 60);
  var s = Math.floor(sec % 60);
  return m + ":" + (s < 10 ? "0" : "") + s;
}

})();
