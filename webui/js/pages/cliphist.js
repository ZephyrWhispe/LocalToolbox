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
  id: "cliphist",
  title: "剪贴板历史",
  group: "工具与增效",
  hidden: true,
  icon: "clipboard",
  backTo: "tools",
  mount: mount,
  show: show,
});

var state = { entries: [], running: false, query: "" };

function mount(el) {
  var toolbar = h("div", {class: "row", style: {gap: "8px", marginBottom: "12px", flexWrap: "wrap"}},
    h("button", {class: "btn sm", id: "cliphist-toggle", onclick: toggleMonitor}, "启动监控"),
    h("input", {type: "text", placeholder: "搜索...", id: "cliphist-search",
      style: {width: "200px"}, onkeydown: function(e) {
        if (e.key === "Enter") { state.query = e.target.value; refresh(); }
      }}),
    h("button", {class: "btn sm", onclick: refresh}, "刷新"),
    h("button", {class: "btn sm danger", onclick: clearAll}, "清空历史"),
    h("span", {id: "cliphist-status",
      style: {display: "inline-flex", alignItems: "center"}})
  );

  var list = h("div", {id: "cliphist-list", class: "cliphist-list"});
  state.listEl = list;

  el.appendChild(toolbar);
  el.appendChild(list);

  App.on("cliphist_changed", function() { refresh(); });
  refresh();
  checkState();
}

function show() {
  refresh();
  checkState();
}

function checkState() {
  call("cliphist_get_state").then(function(r) {
    state.running = r.data.running;
    updateStatus();
  });
}

function updateStatus() {
  var tag = document.getElementById("cliphist-status");
  if (tag) {
    /* 统一用 App.statusTag（原先的 .status-tag 类并没有样式） */
    tag.replaceChildren(App.statusTag(state.running ? "监控中" : "未启动",
      state.running ? "ok" : "info"));
  }
  var btn = document.getElementById("cliphist-toggle");
  if (btn) btn.textContent = state.running ? "停止监控" : "启动监控";
}

function toggleMonitor() {
  if (state.running) {
    call("cliphist_stop").then(function() {
      state.running = false;
      updateStatus();
      App.toast("剪贴板监控已停止", "info");
    });
  } else {
    call("cliphist_start").then(function() {
      state.running = true;
      updateStatus();
      App.toast("剪贴板监控已启动", "success");
      refresh();
    });
  }
}

function refresh() {
  call("cliphist_list", 100, 0, state.query).then(function(r) {
    state.entries = r.data || [];
    renderList();
  });
}

function renderList() {
  var list = state.listEl;
  if (!list) return;
  list.innerHTML = "";
  if (!state.entries.length) {
    list.appendChild(h("div", {class: "muted", style: {padding: "20px", textAlign: "center"}},
      state.running ? "暂无历史记录" : "请先启动监控"));
    return;
  }
  state.entries.forEach(function(entry) {
    var item = h("div", {class: "cliphist-item" + (entry.pinned ? " pinned" : "")});

    var kind = h("span", {class: "cliphist-kind"}, entry.kind === "image" ? "图片" : "文本");
    var time = h("span", {class: "cliphist-time"}, App.fmtTime(entry.ts));

    var content;
    if (entry.kind === "image" && entry.img_path) {
      content = h("img", {class: "cliphist-thumb", src: "file://" + entry.img_path,
        onclick: function() { pasteEntry(entry.id); }});
    } else {
      var text = (entry.text || "").substring(0, 200);
      content = h("div", {class: "cliphist-text", onclick: function() { pasteEntry(entry.id); }},
        text + (entry.text && entry.text.length > 200 ? "..." : ""));
    }

    var actions = h("div", {class: "cliphist-actions"},
      h("button", {class: "btn xs", title: "复制", onclick: function() { pasteEntry(entry.id); }}, "复制"),
      h("button", {class: "btn xs", title: entry.pinned ? "取消置顶" : "置顶",
        onclick: function() { pinEntry(entry.id, !entry.pinned); }},
        entry.pinned ? "取消置顶" : "置顶"),
      h("button", {class: "btn xs danger", title: "删除",
        onclick: function() { deleteEntry(entry.id); }}, "删除")
    );

    item.appendChild(h("div", {class: "cliphist-header"}, kind, time));
    item.appendChild(content);
    item.appendChild(actions);
    list.appendChild(item);
  });
}

function pasteEntry(id) {
  call("cliphist_paste", id).then(function(r) {
    if (r.ok) App.toast("已复制到剪贴板", "success");
    else App.toast(r.err || "复制失败", "error");
  });
}

function pinEntry(id, pinned) {
  call("cliphist_pin", id, pinned).then(function() { refresh(); });
}

function deleteEntry(id) {
  App.confirm("确定删除这条记录？").then(function(ok) {
    if (!ok) return;
    call("cliphist_delete", id).then(function() { refresh(); });
  });
}

function clearAll() {
  App.confirm("确定清空所有剪贴板历史？").then(function(ok) {
    if (!ok) return;
    call("cliphist_clear").then(function() { refresh(); App.toast("已清空", "success"); });
  });
}

})();
