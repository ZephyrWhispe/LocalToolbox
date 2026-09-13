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
  id: "split",
  title: "图片分割",
  group: "工具与增效",
  hidden: true,
  icon: "scissors",
  backTo: "tools",
  mount: mount,
});

var state = { dataUrl: null, rows: 2, cols: 2, tiles: [] };

function mount(el) {
  el.classList.add("page-flex", "canvas-page");   // v5.3：画布页铺满内容区
  var toolbar = h("div", {class: "row", style: {gap: "8px", flexWrap: "wrap"}},
    h("button", {class: "btn sm", onclick: pickFile}, "选择图片"),
    /* v5.3：补 .input 类（此前是原生控件，深色主题下白底）+ .field-label 标签 */
    h("span", {class: "field-label"}, "行:"),
    h("input", {class: "input", type: "number", value: "2", min: "1", max: "20",
      style: {width: "68px"}, onchange: function(e) { state.rows = parseInt(e.target.value) || 2; }}),
    h("span", {class: "field-label"}, "列:"),
    h("input", {class: "input", type: "number", value: "2", min: "1", max: "20",
      style: {width: "68px"}, onchange: function(e) { state.cols = parseInt(e.target.value) || 2; }}),
    h("button", {class: "btn sm accent", onclick: doSplit}, "分割"),
    h("button", {class: "btn sm", onclick: saveAll}, "全部保存")
  );

  var preview = h("div", {class: "combine-preview"});
  var canvas = h("canvas", {id: "split-canvas", class: "editor-canvas"});
  preview.appendChild(canvas);
  state.canvas = canvas;

  var tileGrid = h("div", {id: "split-tiles", class: "split-tile-grid",
    style: {display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
      gap: "8px"}});
  state.tileGrid = tileGrid;

  el.appendChild(h("div", {class: "page-head"},
    h("h2", null, "图片分割"),
    h("div", {class: "sub"}, "把一张图按行列切成小块，可逐块复制或保存")));
  el.appendChild(h("div", {class: "card"}, toolbar));
  el.appendChild(h("div", {class: "card"},
    h("div", {class: "card-title"}, "原图预览"),
    preview));
  el.appendChild(h("div", {class: "card fill"},
    h("div", {class: "card-title"}, "分割结果"),
    tileGrid));
}

function pickFile() {
  call("split_pick_file").then(function(r) {
    if (r.data && r.data.data_url) {
      state.dataUrl = r.data.data_url;
      var img = new Image();
      img.onload = function() {
        var maxW = state.canvas.parentElement.clientWidth - 20;
        var scale = Math.min(maxW / img.width, 1);
        state.canvas.width = img.width * scale;
        state.canvas.height = img.height * scale;
        state.canvas.getContext("2d").drawImage(img, 0, 0, state.canvas.width, state.canvas.height);
      };
      img.src = state.dataUrl;
      App.toast("已载入图片，设置行列数后点击分割", "info");
    }
  }).catch(function(e) { App.toast(e.message || e, "error"); });
}

function doSplit() {
  if (!state.dataUrl) return App.toast("请先选择图片", "warn");
  call("split_image", state.dataUrl, state.rows, state.cols).then(function(r) {
    if (r.data && r.data.tiles) {
      state.tiles = r.data.tiles;
      renderTiles();
      App.toast("分割完成：共 " + r.data.count + " 块", "success");
    }
  }).catch(function(e) { App.toast(e.message || e, "error"); });
}

function renderTiles() {
  var el = state.tileGrid;
  el.innerHTML = "";
  state.tiles.forEach(function(url, i) {
    var card = h("div", {class: "split-tile-card",
      style: {border: "1px solid var(--border)", borderRadius: "4px", padding: "4px",
        textAlign: "center", background: "var(--card-bg)"}},
      h("img", {src: url, style: {maxWidth: "100%", maxHeight: "120px", display: "block", margin: "auto"}}),
      h("div", {style: {fontSize: "12px", marginTop: "4px"}}, "#" + (i + 1)),
      h("div", {class: "row", style: {gap: "4px", justifyContent: "center", marginTop: "4px"}},
        h("button", {class: "btn xs", onclick: function() { copyTile(i); }}, "复制"),
        h("button", {class: "btn xs", onclick: function() { saveTile(i); }}, "保存"))
    );
    el.appendChild(card);
  });
}

function copyTile(idx) {
  call("split_copy_tile", state.tiles[idx]).then(function(r) {
    if (r.ok) App.toast("已复制到剪贴板", "success");
  });
}

function saveTile(idx) {
  call("split_save_tile", state.tiles[idx]).then(function(r) {
    if (r.data && r.data.path) App.toast("已保存到 " + r.data.path, "success");
  });
}

function saveAll() {
  if (!state.tiles.length) return App.toast("请先分割图片", "warn");
  call("split_save_all", state.tiles).then(function(r) {
    if (r.data && r.data.paths) {
      App.toast("已保存 " + r.data.count + " 个文件到 " + r.data.dir, "success");
    }
  });
}

})();
