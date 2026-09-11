/* 剪贴板弹窗（Win+V，类 Ditto）前端逻辑。
 * 由 clip_pop_api._build_pop_html 注入本文件；通过 window.pywebview.api 调用整桥：
 *   cliphist_list / cliphist_delete / cliphist_pin / cliphist_get_image / clip_pop_*
 * 键盘：↑↓ 选择、Enter 粘贴、Ctrl+Enter 纯文本、Alt+Enter 去换行、
 *       Del 删除、Ctrl/Shift 多选、Esc 关闭；右键菜单。 */
(function () {
  "use strict";

  var api = function () { return (window.pywebview && window.pywebview.api) || null; };
  var state = {
    entries: [],          // 当前列表（后端返回顺序，置顶条目已前置）
    sel: 0,               // 当前选中索引
    multi: {},            // id -> true（Ctrl/Shift 多选）
    multiOrder: [],       // 多选顺序
    query: "",
    timer: null,
    autopaste: true,
    imgCache: {},         // id -> data_url
    showPreview: false,
  };
  var els = {};

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function fmtTs(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    var p = function (n) { return String(n).padStart(2, "0"); };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
      " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }
  function call(method) {
    var a = api();
    if (!a || !a[method]) return Promise.resolve({ ok: false, err: "后端 API 不可用" });
    var args = Array.prototype.slice.call(arguments, 1);
    try {
      var p = a[method].apply(a, args);
      return (p && typeof p.then === "function") ? p : Promise.resolve(p);
    } catch (e) {
      return Promise.resolve({ ok: false, err: String(e && e.message || e) });
    }
  }
  function toast(msg, kind) {
    var t = $("pop-toast");
    t.textContent = msg;
    t.className = kind || "ok";
    t.style.display = "block";
    clearTimeout(t._tm);
    t._tm = setTimeout(function () { t.style.display = "none"; }, 2600);
  }

  /* ---------- 列表加载与渲染 ---------- */
  function load() {
    call("cliphist_list", 100, 0, state.query).then(function (r) {
      if (!r.ok) return;
      var rows = r.data || [];
      // 置顶条目前置（数据层按 ts 排序，弹窗内钉住的固定在顶部）
      rows.sort(function (a, b) {
        return (b.pinned - a.pinned) || (b.ts - a.ts);
      });
      state.entries = rows;
      state.sel = 0;
      state.multi = {};
      state.multiOrder = [];
      render();
    });
  }
  function thumb(entry) {
    if (state.imgCache[entry.id]) return Promise.resolve(state.imgCache[entry.id]);
    return call("cliphist_get_image", entry.id).then(function (r) {
      if (r.ok && r.data) state.imgCache[entry.id] = r.data.data_url;
      return state.imgCache[entry.id] || null;
    });
  }
  function render() {
    var list = $("pop-list");
    list.innerHTML = "";
    state.entries.forEach(function (e, i) {
      var div = document.createElement("div");
      div.className = "pop-item" + (i === state.sel ? " active" : "") +
        (state.multi[e.id] ? " multi" : "");
      div.dataset.idx = i;
      var pin = e.pinned ? '<span class="pin">📌</span>' : "";
      var meta = (e.kind === "image" ? "图片" : "文本") + " · " + fmtTs(e.ts);
      if (e.kind === "image") {
        div.innerHTML = pin + '<img class="thumb" alt="图片">' +
          '<div class="meta">' + meta + "</div>";
        thumb(e).then(function (url) {
          var img = div.querySelector("img.thumb");
          if (img && url) img.src = url;
        });
      } else {
        var lines = String(e.text || "");
        var shown = lines.split("\n").slice(0, 3).join("\n");
        div.innerHTML = pin + '<div class="snippet">' + esc(shown) + "</div>" +
          '<div class="meta">' + meta + "</div>";
      }
      div.addEventListener("click", function (ev) {
        if (ev.ctrlKey || ev.shiftKey) toggleMulti(e);
        else { state.sel = i; state.multi = {}; state.multiOrder = []; }
        render();
        updatePreview();
      });
      div.addEventListener("contextmenu", function (ev) {
        ev.preventDefault();
        state.sel = i;
        render();
        showCtx(ev.clientX, ev.clientY, e);
      });
      div.addEventListener("dblclick", function () {
        paste(e, false, false);
      });
      list.appendChild(div);
    });
    var selEl = list.querySelector(".pop-item.active");
    if (selEl) selEl.scrollIntoView({ block: "nearest" });
    $("pop-count").textContent =
      (state.entries.length ? state.sel + 1 : 0) + "/" + state.entries.length;
  }
  function toggleMulti(e) {
    if (state.multi[e.id]) {
      delete state.multi[e.id];
      state.multiOrder = state.multiOrder.filter(function (x) { return x !== e.id; });
    } else {
      state.multi[e.id] = true;
      state.multiOrder.push(e.id);
    }
  }
  function curEntry() { return state.entries[state.sel] || null; }

  /* ---------- 预览面板 ---------- */
  function updatePreview() {
    var pv = $("pop-preview");
    if (!state.showPreview) { pv.className = ""; pv.innerHTML = ""; return; }
    var e = curEntry();
    if (!e) { pv.className = "show"; pv.textContent = "（空）"; return; }
    pv.className = "show";
    pv.textContent = "加载中…";
    call("clip_pop_get_entry", e.id).then(function (r) {
      if (!r.ok) { pv.textContent = r.err || "加载失败"; return; }
      var d = r.data;
      pv.innerHTML = "";
      var head = document.createElement("div");
      head.style.cssText = "color:var(--muted);font-size:11px;margin-bottom:6px";
      head.textContent = (d.kind === "image" ? "图片" : "文本") + " · " + fmtTs(d.ts);
      pv.appendChild(head);
      if (d.kind === "image" && d.data_url) {
        var img = document.createElement("img");
        img.src = d.data_url;
        pv.appendChild(img);
      } else {
        pv.appendChild(document.createTextNode(d.text || "（空）"));
      }
    });
  }

  /* ---------- 粘贴 / 删除 / 置顶 ---------- */
  function paste(entry, plain, noNewline) {
    if (!entry) return;
    call("clip_pop_paste", entry.id, !!plain, !!noNewline, null);
    /* 成败提示由后端经 ClipPop.toast 直推（弹窗可能已被隐藏） */
  }
  function multiPaste() {
    var ids = state.multiOrder.slice();
    if (!ids.length) return;
    call("clip_pop_multi_paste", ids, null);
  }
  function delEntry(entry) {
    call("cliphist_delete", entry.id).then(function (r) {
      if (!r.ok) { toast(r.err || "删除失败", "warn"); return; }
      delete state.imgCache[entry.id];
      load();
    });
  }
  function togglePin(entry) {
    call("cliphist_pin", entry.id, !entry.pinned).then(function () { load(); });
  }

  /* ---------- 右键菜单 ---------- */
  var ctx = $("pop-ctx");
  function hideCtx() { ctx.style.display = "none"; }
  function ctxItem(label, dis, fn) {
    var d = document.createElement("div");
    d.className = "ctx-item" + (dis ? " dis" : "");
    d.textContent = label;
    if (!dis && fn) d.addEventListener("click", function () { hideCtx(); fn(); });
    ctx.appendChild(d);
  }
  function showCtx(x, y, e) {
    ctx.innerHTML = "";
    ctxItem("粘贴" + (e.kind === "image" ? "图片" : ""), false, function () { paste(e, false, false); });
    ctxItem("粘贴为纯文本", e.kind !== "text", function () { paste(e, true, false); });
    ctxItem("去换行粘贴", e.kind !== "text", function () { paste(e, false, true); });
    ctxItem("复制为纯文本", e.kind !== "text", function () {
      call("clip_pop_paste", e.id, true, false, false).then(function () { hide(); });
    });
    ctxItem(e.pinned ? "取消置顶" : "置顶", false, function () { togglePin(e); });
    ctxItem("打开路径", e.kind !== "image", function () {
      call("clip_pop_reveal", e.id).then(function (r) {
        if (!r.ok) toast(r.err || "打开失败", "warn");
      });
    });
    ctxItem("删除", false, function () { delEntry(e); });
    ctx.style.display = "block";
    var rect = ctx.getBoundingClientRect();
    ctx.style.left = Math.min(x, window.innerWidth - rect.width - 6) + "px";
    ctx.style.top = Math.min(y, window.innerHeight - rect.height - 6) + "px";
  }
  document.addEventListener("mousedown", function (e) {
    if (ctx.style.display === "block" && !ctx.contains(e.target)) hideCtx();
  });

  /* ---------- 键盘 ---------- */
  function move(d) {
    if (!state.entries.length) return;
    state.sel = (state.sel + d + state.entries.length) % state.entries.length;
    render();
    updatePreview();
  }
  function hide() { call("clip_pop_hide"); }
  function togglePreview() {
    state.showPreview = !state.showPreview;
    updatePreview();
  }
  document.addEventListener("keydown", function (e) {
    var inSearch = document.activeElement === els.search;
    switch (e.key) {
      case "ArrowDown": e.preventDefault(); move(1); break;
      case "ArrowUp": e.preventDefault(); move(-1); break;
      case "Enter":
        e.preventDefault();
        if (Object.keys(state.multi).length) multiPaste();
        else paste(curEntry(), e.ctrlKey, e.altKey);
        break;
      case "Delete":
        e.preventDefault();
        if (curEntry()) delEntry(curEntry());
        break;
      case "F3": e.preventDefault(); togglePreview(); break;
      case "Escape": e.preventDefault(); hide(); break;
      default:
        if (!inSearch && e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
          els.search.focus();   // 任意字符键自动落进搜索框（即输即筛）
        }
    }
  });

  /* ---------- 搜索 ---------- */
  function onSearch() {
    clearTimeout(state.timer);
    state.timer = setTimeout(function () {
      state.query = els.search.value.trim();
      load();
    }, 120);
  }

  /* ---------- 对外入口 ---------- */
  window.ClipPop = {
    start: function (cfg) {
      els.search = $("pop-search");
      els.pvBtn = $("pop-pv");
      state.autopaste = !!(cfg && cfg.autopaste);
      if (cfg && cfg.theme) document.body.className = cfg.theme === "light" ? "light" : "dark";
      if (cfg && cfg.accent) {
        document.body.style.setProperty("--accent", cfg.accent);
        document.body.style.setProperty("--accent-soft", cfg.accent + "29");
      }
      els.search.addEventListener("input", onSearch);
      els.search.addEventListener("keydown", function (e) {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault(); move(e.key === "ArrowDown" ? 1 : -1);
        }
      });
      els.pvBtn.addEventListener("click", function () {
        togglePreview();
        els.pvBtn.classList.toggle("on", state.showPreview);
      });
      load();
    },
    onShow: function (cfg) {
      /* 后端每次 show 推送：主题 / 自动粘贴开关可能已变更 */
      if (cfg.theme) document.body.className = cfg.theme === "light" ? "light" : "dark";
      if (cfg.accent) {
        document.body.style.setProperty("--accent", cfg.accent);
        document.body.style.setProperty("--accent-soft", cfg.accent + "29");
      }
      state.autopaste = !!cfg.autopaste;
      updatePreview();
      load();
      if (els.search) {
        els.search.focus();
        els.search.select();
      }
    },
    togglePreview: function () {
      togglePreview();
      if (els.pvBtn) els.pvBtn.classList.toggle("on", state.showPreview);
    },
    toast: toast,
    hide: hide,
  };
})();
