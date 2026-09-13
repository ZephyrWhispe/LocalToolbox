/* 备忘录快速捕捉弹窗逻辑：输入即存、即输即搜、分组记忆、Esc 隐藏。
   由 memo_pop.html 注入（POP_JS 占位），pywebview js_api 共享整桥。 */
(function () {
  "use strict";

  var els = {};
  var state = { groups: [], lastGroup: 0, query: "", timer: null };

  function api(name) {
    return window.pywebview.api[name].apply(window.pywebview.api,
      Array.prototype.slice.call(arguments, 1));
  }

  function esc(s) {
    return String(s || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function toast(msg, kind) {
    els.toast.textContent = msg;
    els.toast.className = kind || "ok";
    els.toast.style.display = "block";
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { els.toast.style.display = "none"; }, 2200);
  }

  function fmtTime(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    return (d.getMonth() + 1) + "-" + d.getDate() + " " +
      String(d.getHours()).padStart(2, "0") + ":" +
      String(d.getMinutes()).padStart(2, "0");
  }

  function renderGroups(selected) {
    els.group.innerHTML = "";
    var opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "未分组";
    els.group.appendChild(opt);
    for (var i = 0; i < state.groups.length; i++) {
      var g = state.groups[i];
      if (!g.id) continue;
      var o = document.createElement("option");
      o.value = String(g.id);
      o.textContent = g.name;
      els.group.appendChild(o);
    }
    els.group.value = selected ? String(selected) : "";
  }

  function renderList(memos) {
    els.list.innerHTML = "";
    if (!memos || !memos.length) {
      els.list.innerHTML = '<div class="mp-empty">' +
        (state.query ? "无匹配结果" : "暂无备忘，上方输入即可保存") + "</div>";
      els.count.textContent = "0 条";
      return;
    }
    els.count.textContent = memos.length + " 条";
    memos.forEach(function (m) {
      var div = document.createElement("div");
      div.className = "mp-item";
      var tags = "";
      if (m.pinned) tags += '<span class="pin">★</span>';
      if (m.group_name) tags += '<span class="grp">#' + esc(m.group_name) + "</span>";
      div.innerHTML = '<div class="t">' + esc(m.title || "(无标题)") + tags + "</div>" +
        '<div class="s">' + esc(m.preview || "") + "</div>" +
        '<div class="s" style="opacity:.6">' + fmtTime(m.updated) + "</div>";
      div.addEventListener("click", function () {
        api("memo_pop_open_main", state.query);
      });
      els.list.appendChild(div);
    });
  }

  function refresh() {
    api("memo_pop_data", state.query).then(function (r) {
      if (!r || !r.ok) { toast((r && r.err) || "加载失败", "warn"); return; }
      var d = r.data || {};
      state.groups = d.groups || [];
      state.lastGroup = d.last_group || 0;
      var cur = els.group.value;
      renderGroups(cur !== "" ? cur : state.lastGroup);
      renderList(d.memos);
    }).catch(function () {});
  }

  function save() {
    var text = els.text.value.trim();
    if (!text) { toast("内容为空", "warn"); return; }
    var gid = els.group.value ? parseInt(els.group.value, 10) : null;
    api("memo_pop_save", text, gid).then(function (r) {
      if (!r || !r.ok) { toast((r && r.err) || "保存失败", "warn"); return; }
    }).catch(function () {});
  }

  window.MemoPop = {
    start: function () {
      els.text = document.getElementById("mp-text");
      els.group = document.getElementById("mp-group");
      els.search = document.getElementById("mp-search");
      els.save = document.getElementById("mp-save");
      els.list = document.getElementById("mp-list");
      els.count = document.getElementById("mp-count");
      els.status = document.getElementById("mp-status");
      els.toast = document.getElementById("mp-toast");

      els.save.addEventListener("click", save);
      els.text.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          save();
        }
      });
      els.search.addEventListener("input", function () {
        clearTimeout(state.timer);
        state.timer = setTimeout(function () {
          state.query = els.search.value.trim();
          refresh();
        }, 200);
      });
      els.search.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          clearTimeout(state.timer);
          state.query = els.search.value.trim();
          refresh();
        }
      });
      document.getElementById("mp-open").addEventListener("click", function () {
        api("memo_pop_open_main", state.query);
      });
      document.body.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
          e.preventDefault();
          api("memo_pop_hide");
        } else if (e.key.toLowerCase() === "g" && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          api("memo_pop_open_main", state.query);
        }
      });
      refresh();
    },
    /* 后端 memo_pop_show 时回调：聚焦输入、刷新数据 */
    onShow: function () {
      refresh();
      setTimeout(function () {
        els.text.focus();
        els.search.value = "";
        state.query = "";
      }, 60);
    },
    /* 保存成功回调：清输入、刷新列表、保持焦点 */
    onSaved: function () {
      els.text.value = "";
      refresh();
      els.text.focus();
    },
    toast: toast,
  };
})();
