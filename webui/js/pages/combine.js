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
  id: "combine",
  title: "图片合并",
  group: "工具与增效",
  hidden: true,
  icon: "layers",
  backTo: "tools",
  mount: mount,
});

var state = { dataUrls: [], result: null, direction: "vertical", gap: 0, bgColor: "#FFFFFF" };

function mount(el) {
  var toolbar = h("div", {class: "row", style: {gap: "8px", marginBottom: "12px", flexWrap: "wrap"}},
    h("button", {class: "btn sm", onclick: pickFiles}, "选择图片"),
    h("select", {id: "combine-dir", onchange: function(e) { state.direction = e.target.value; }},
      h("option", {value: "vertical"}, "垂直拼接"),
      h("option", {value: "horizontal"}, "水平拼接"),
      h("option", {value: "grid"}, "网格拼接")),
    h("label", {}, "间距:"),
    h("input", {type: "number", value: "0", min: "0", max: "100",
      style: {width: "60px"}, onchange: function(e) { state.gap = parseInt(e.target.value) || 0; }}),
    h("label", {}, "背景色:"),
    h("input", {type: "color", value: "#FFFFFF",
      onchange: function(e) { state.bgColor = e.target.value; }}),
    h("button", {class: "btn sm accent", onclick: doCombine}, "合并"),
    h("button", {class: "btn sm", onclick: saveResult}, "保存结果")
  );

  var fileList = h("div", {id: "combine-files", class: "combine-file-list"});
  var preview = h("div", {class: "combine-preview"});
  var canvas = h("canvas", {id: "combine-canvas", class: "editor-canvas"});
  preview.appendChild(canvas);
  state.previewEl = preview;

  el.appendChild(toolbar);
  el.appendChild(fileList);
  el.appendChild(preview);
  state.fileListEl = fileList;
}

function pickFiles() {
  call("combine_pick_files").then(function(r) {
    if (r.data && r.data.data_urls) {
      state.dataUrls = r.data.data_urls;
      renderFileList();
    }
  }).catch(function(e) { App.toast(e.message || e, "error"); });
}

function renderFileList() {
  var el = state.fileListEl;
  el.innerHTML = "";
  state.dataUrls.forEach(function(url, i) {
    var item = h("div", {class: "combine-file-item", draggable: "true"},
      h("span", {}, (i + 1) + ". "),
      h("img", {src: url, style: {width: "40px", height: "40px", objectFit: "contain", marginRight: "8px"}}),
      h("button", {class: "btn xs danger", onclick: function() {
        state.dataUrls.splice(i, 1);
        renderFileList();
      }}, "移除")
    );
    item.addEventListener("dragstart", function(e) {
      e.dataTransfer.setData("text/plain", String(i));
    });
    item.addEventListener("dragover", function(e) { e.preventDefault(); });
    item.addEventListener("drop", function(e) {
      e.preventDefault();
      var from = parseInt(e.dataTransfer.getData("text/plain"));
      var to = i;
      var item = state.dataUrls.splice(from, 1)[0];
      state.dataUrls.splice(to, 0, item);
      renderFileList();
    });
    el.appendChild(item);
  });
}

function doCombine() {
  if (state.dataUrls.length < 2) return App.toast("请至少选择 2 张图片", "warn");
  call("combine_images", state.dataUrls, state.direction, state.gap, state.bgColor)
    .then(function(r) {
      if (r.data && r.data.data_url) {
        state.result = r.data.data_url;
        var canvas = document.getElementById("combine-canvas");
        var img = new Image();
        img.onload = function() {
          var maxW = canvas.parentElement.clientWidth - 20;
          var scale = Math.min(maxW / img.width, 1);
          canvas.width = img.width * scale;
          canvas.height = img.height * scale;
          canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        };
        img.src = state.result;
        App.toast("合并完成", "success");
      }
    }).catch(function(e) { App.toast(e.message || e, "error"); });
}

function saveResult() {
  if (!state.result) return App.toast("请先合并图片", "warn");
  call("combine_save", state.result).then(function(r) {
    if (r.data && r.data.path) App.toast("已保存到 " + r.data.path, "success");
  }).catch(function(e) { App.toast(e.message || e, "error"); });
}

})();
