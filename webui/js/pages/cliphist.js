/* 剪贴板历史页（v5.2 升级为一级页面，归入「互联与协作」组）。
   设计原则：单主任务 + 浮动上下文——工具栏仅 4 控件；多选操作条选中时才浮现；
   监控开启时 2s 轮询 + 快照比对（无变化不重绘）。 */
(function () {
  "use strict";

  var h = App.h;
  /* 兼容层：失败返回 {ok:false} 并弹 toast，避免整页功能静默失效 */
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
    group: "互联与协作",
    icon: "clipboard",

    mount: mount,
    show: function () { refresh(); },
  });

  var state = {
    entries: [], sel: new Set(), anchor: -1,
    running: false, query: "", kind: "", group: "all", groups: [],
  };
  var refs = {};
  var pageCtx = null;   // v5.3：页面上下文（挂载时注入，供"开启监控"后沿用同一轮询）
  var lastKey = "";
  var searchTimer = null;

  /* ---------------- 相对时间 ---------------- */
  function fmtRel(ts) {
    var d = ts * 1000, diff = Date.now() - d;
    if (diff < 60 * 1000) return "刚刚";
    if (diff < 3600 * 1000) return Math.floor(diff / 60000) + " 分钟前";
    var dt = new Date(d), now = new Date();
    var hm = function (x) {
      var p = function (n) { return String(n).padStart(2, "0"); };
      return p(x.getHours()) + ":" + p(x.getMinutes());
    };
    var day = function (x) { return x.toDateString(); };
    if (day(dt) === day(now)) return "今天 " + hm(dt);
    var yest = new Date(now); yest.setDate(now.getDate() - 1);
    if (day(dt) === day(yest)) return "昨天 " + hm(dt);
    var p2 = function (n) { return String(n).padStart(2, "0"); };
    return dt.getFullYear() + "-" + p2(dt.getMonth() + 1) + "-" + p2(dt.getDate())
      + " " + hm(dt);
  }

  /* ---------------- 数据 ---------------- */
  function refresh() {
    return call("cliphist_list", 200, 0, state.query, state.kind, state.group).then(function (r) {
      if (r.ok) {
        state.entries = r.data || [];
        /* 置顶条目优先（后端仅按时间排序） */
        state.entries.sort(function (a, b) {
          return (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0);
        });
        renderList();
      }
    });
  }

  async function loadGroups() {
    var r = await App.tryCall("cliphist_groups");
    if (!r.ok) return;
    state.groups = r.data || [];
    if (refs.groupSel) {
      var cur = state.group;
      refs.groupSel.innerHTML = "";
      refs.groupSel.appendChild(h("option", { value: "all" }, "全部分组"));
      refs.groupSel.appendChild(h("option", { value: "0" }, "未分组"));
      state.groups.forEach(function (g) {
        refs.groupSel.appendChild(h("option", { value: String(g.id) },
          g.name + "（" + g.count + "）"));
      });
      refs.groupSel.value = cur;
      if (refs.groupSel.value !== cur) { refs.groupSel.value = "all"; }
    }
  }

  function showGroupMenu(x, y) {
    /* 移动所选条目到分组（Ditto 式归组） */
    var ids = [...state.sel];
    if (!ids.length) { App.toast("请先选择条目", "warn"); return; }
    var items = [];
    state.groups.forEach(function (g) {
      items.push({ label: "移入「" + g.name + "」", icon: "folder",
        onclick: function () {
          call("cliphist_group_set", ids, g.id).then(function (r) {
            if (r.ok) { App.toast("已移动 " + ids.length + " 条", "ok"); state.sel.clear(); refresh(); loadGroups(); }
          });
        } });
    });
    if (items.length) items.push("sep");
    items.push({ label: "移出分组（未分组）", icon: "x",
      onclick: function () {
        call("cliphist_group_set", ids, 0).then(function (r) {
          if (r.ok) { App.toast("已移出", "ok"); state.sel.clear(); refresh(); loadGroups(); }
        });
      } });
    App.contextMenu(items, x, y);
  }

  /* 轮询：监控开启时每 2s 拉一次，快照比对无变化不重绘（沿用设备面板模式）。
     v5.3：改用页面上下文定时器 —— 此前是递归 setTimeout，**离开页面后永不停止**
     （原注释写着"离开页面停止轮询"，与实际行为相反）。现在离开页面整段跳过，
     回到页面由 show() → refresh() 全量补齐，不会漏条目。 */
  var pollWired = false;
  function poll(ctx) {
    if (pollWired || !ctx) return;
    pollWired = true;
    ctx.every(2000, function () {
      if (!state.running) return;
      App.tryCall("cliphist_list", 200, 0, state.query, state.kind, state.group).then(function (r) {
        if (!r.ok) return;
        var key = JSON.stringify(r.data);
        if (key === lastKey) return;
        lastKey = key;
        state.entries = (r.data || []).sort(function (a, b) {
          return (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0);
        });
        renderList();
      });
    });
  }

  function refreshState() {
    return call("cliphist_get_state").then(function (r) {
      if (r.ok) {
        state.running = r.data.running;
        updateStatus();
      }
    });
  }

  function updateStatus() {
    if (refs.tgl) refs.tgl.checked = state.running;
    if (refs.tagBox) {
      refs.tagBox.replaceChildren(App.statusTag(
        state.running ? "监控中" : "未启动", state.running ? "ok" : "info"));
    }
  }

  /* ---------------- 渲染 ---------------- */
  function renderList() {
    if (!refs.list) return;
    refs.list.innerHTML = "";
    if (!state.entries.length) {
      refs.list.appendChild(h("div", { class: "empty" },
        state.running ? (state.query ? "无匹配记录" : "暂无历史记录")
          : "监控未启动——打开右上角开关开始记录"));
      renderFloatBar();
      renderStatus();
      return;
    }
    state.entries.forEach(function (entry, i) {
      refs.list.appendChild(itemEl(entry, i));
    });
    renderFloatBar();
    renderStatus();
  }

  function itemEl(entry, idx) {
    var selected = state.sel.has(entry.id);
    var item = h("div", {
      class: "cliphist-item" + (selected ? " selected" : ""),
      onclick: function (ev) { selectEntry(entry, idx, ev); },
      oncontextmenu: function (ev) {
        ev.preventDefault();
        if (!selected) { state.sel = new Set([entry.id]); renderList(); }
        entryMenu(entry, ev.clientX, ev.clientY);
      },
    });

    var kindTag = h("span", null, entry.kind === "image" ? "图片" : "文本");
    item.appendChild(h("div", { class: "ch-head" },
      kindTag,
      h("span", null, "·"),
      h("span", null, fmtRel(entry.ts)),
      entry.pinned ? h("span", { class: "cliphist-star", title: "已置顶" }, "★") : null,
    ));

    if (entry.kind === "image" && entry.img_path) {
      item.appendChild(h("img", {
        class: "ch-img", src: "file://" + entry.img_path,
        onclick: function (ev) { ev.stopPropagation(); copyEntry(entry); },
      }));
    } else {
      var text = entry.text || "";
      var node = h("div", { class: "ch-text" },
        text + (text.length > 400 ? "…" : ""));
      item.appendChild(node);
    }

    item.appendChild(h("div", { class: "ch-actions" },
      h("button", { class: "btn xs", title: "复制到剪贴板",
        onclick: function (ev) { ev.stopPropagation(); copyEntry(entry); } }, "复制"),
      h("button", { class: "btn xs", title: entry.pinned ? "取消置顶" : "置顶",
        onclick: function (ev) {
          ev.stopPropagation(); pinEntry(entry.id, !entry.pinned);
        } }, entry.pinned ? "取消置顶" : "置顶"),
      h("button", { class: "btn xs danger", title: "删除",
        onclick: function (ev) {
          ev.stopPropagation(); deleteEntry(entry.id);
        } }, "删除"),
    ));
    return item;
  }

  /* 浮动多选操作条：未选中时完全隐藏（设计原则 4：上下文浮现） */
  function renderFloatBar() {
    if (!refs.floatBar) return;
    var n = state.sel.size;
    if (!n) { refs.floatBar.style.display = "none"; return; }
    refs.floatBar.style.display = "flex";
    refs.floatSel.textContent = "已选 " + n + " 条";
    var texts = [...state.sel].map(function (id) {
      var e = state.entries.find(function (x) { return x.id === id; });
      return e && e.kind === "text" ? e : null;
    }).filter(Boolean);
    refs.floatMerge.style.display = texts.length > 1 ? "" : "none";
  }

  function renderStatus() {
    if (refs.status) {
      refs.status.textContent = "共 " + state.entries.length + " 条记录"
        + (state.kind ? "（" + (state.kind === "text" ? "文本" : "图片") + "）" : "");
    }
  }

  /* ---------------- 选择 ---------------- */
  function selectEntry(entry, idx, ev) {
    if (ev.ctrlKey || ev.metaKey) {
      if (state.sel.has(entry.id)) state.sel.delete(entry.id);
      else state.sel.add(entry.id);
      state.anchor = idx;
    } else if (ev.shiftKey && state.anchor >= 0) {
      var a = Math.min(state.anchor, idx), b = Math.max(state.anchor, idx);
      state.sel = new Set(state.entries.slice(a, b + 1).map(function (x) { return x.id; }));
    } else {
      state.sel = new Set([entry.id]);
      state.anchor = idx;
    }
    renderList();
  }

  /* ---------------- 动作 ---------------- */
  function copyEntry(entry) {
    call("cliphist_paste", entry.id).then(function (r) {
      if (r.ok) App.toast("已复制到剪贴板", "success");
    });
  }

  function mergeCopy() {
    var ids = [...state.sel];
    call("cliphist_merge_copy", ids).then(function (r) {
      if (r.ok) {
        App.toast("已合并复制 " + r.data.count + " 条文本", "success");
        state.sel.clear();
        renderList();
      }
    });
  }

  function copySelected() {
    var ids = [...state.sel];
    if (ids.length === 1) { copyEntryById(ids[0]); return; }
    call("cliphist_merge_copy", ids).then(function (r) {
      if (r.ok) {
        App.toast("已复制 " + r.data.count + " 条文本", "success");
        state.sel.clear();
        renderList();
      }
    });
  }

  function copyEntryById(id) {
    call("cliphist_paste", id).then(function (r) {
      if (r.ok) App.toast("已复制到剪贴板", "success");
    });
  }

  function pinEntry(id, pinned) {
    call("cliphist_pin", id, pinned).then(function () { refresh(); });
  }

  function deleteEntry(id) {
    App.confirm("确定删除这条记录？").then(function (ok) {
      if (!ok) return;
      call("cliphist_delete", id).then(function () {
        state.sel.delete(id);
        refresh();
      });
    });
  }

  function deleteSelected() {
    var n = state.sel.size;
    App.confirm("删除选中的 " + n + " 条记录？").then(function (ok) {
      if (!ok) return;
      var ids = [...state.sel];
      var next = function () {
        if (!ids.length) { refresh(); return; }
        call("cliphist_delete", ids.shift()).then(next);
      };
      next();
    });
  }

  function clearAll() {
    App.confirm("确定清空所有剪贴板历史？（置顶条目也会删除）").then(function (ok) {
      if (!ok) return;
      call("cliphist_clear").then(function () {
        state.sel.clear();
        refresh();
        App.toast("已清空", "success");
      });
    });
  }

  /* v5.4：相同内容合并（每组重复文本仅保留一条：置顶优先，其次最新） */
  function doDedup() {
    var n = state.entries.length;
    App.confirm("合并相同内容",
      "将扫描全部 " + n + " 条可见记录，每组重复文本仅保留一条" +
      "（置顶优先，其次最新），其余删除。确定执行？").then(function (ok) {
      if (!ok) return;
      call("cliphist_dedup").then(function (r) {
        if (!r.ok) return;
        state.sel.clear();
        refresh();
        loadGroups();
        App.toast(r.data.removed
          ? "已合并删除 " + r.data.removed + " 条重复内容" : "没有重复内容", "success");
      });
    });
  }

  function toggleMonitor() {
    var callName = state.running ? "cliphist_stop" : "cliphist_start";
    call(callName).then(function () {
      state.running = !state.running;
      updateStatus();
      refresh();
      poll(pageCtx);
    });
  }

  /* 右键菜单（条目）——v5.4 O1：加入分组操作；v5.4 O4：自定义命令 */
  function entryMenu(entry, x, y) {
    var gItems = state.groups
      .filter(function (g) { return g.id !== (entry.group_id || 0); })
      .map(function (g) {
        return { label: "移入「" + g.name + "」", icon: "folder",
          onclick: function () {
            App.tryCall("cliphist_group_set", [entry.id], g.id).then(function (r) {
              if (r.ok) { App.toast("已移入「" + g.name + "」", "ok"); refresh(); loadGroups(); }
              else App.toast(r.err, "error");
            });
          } };
      });
    var cmds = ((App.state.cfg || {}).clip_cmds) || [];
    var cmdItems = (entry.kind === "text" && cmds.length) ? cmds.map(function (c, i) {
      return { label: "执行「" + c.name + "」", icon: "terminal",
        onclick: function () {
          App.tryCall("clip_exec_cmd", i, entry.id).then(function (r) {
            if (r.ok) App.toast("已执行「" + r.data.name + "」", "ok");
            else App.toast(r.err, "error");
          });
        } };
    }) : [];
    App.contextMenu([
      { label: "复制", icon: "copy", onclick: function () { copyEntry(entry); } },
      { label: entry.pinned ? "取消置顶" : "置顶", icon: "star",
        onclick: function () { pinEntry(entry.id, !entry.pinned); } },
      "sep",
      { label: "移动到分组", icon: "folder",
        disabled: !gItems.length && !(entry.group_id || 0),
        onclick: gItems.length ? function () { App.contextMenu(gItems, x + 12, y); } : null },
      { label: "移出分组（未分组）", disabled: !(entry.group_id || 0),
        onclick: function () {
          App.tryCall("cliphist_group_set", [entry.id], 0).then(function (r) {
            if (r.ok) { App.toast("已移出", "ok"); refresh(); loadGroups(); }
          });
        } },
    ].concat(cmdItems.length ? ["sep"].concat(cmdItems) : [], [
      "sep",
      { label: "删除", icon: "trash", danger: true,
        onclick: function () { deleteEntry(entry.id); } },
    ]), x, y);
  }

  /* ---------------- 挂载 ---------------- */
  function mount(el, ctx) {
    el.innerHTML = "";
    el.classList.add("page-flex", "cliphist-page");
    refs = {};
    state.sel = new Set();
    state.anchor = -1;

    el.appendChild(App.h("div", { class: "page-head" },
      App.h("h2", null, "剪贴板历史"),
      App.h("div", { class: "sub" },
        "自动记录本机复制内容；跨机同步请用「剪贴板同步」"),
    ));

    /* 工具栏：仅 4 控件 */
    refs.search = App.h("input", {
      class: "input grow", placeholder: "搜索历史…（Ctrl+F）",
      oninput: function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () {
          state.query = refs.search.value.trim();
          refresh();
        }, 300);
      },
    });
    refs.kindSel = App.h("select", {
      class: "input", style: { width: "auto", flex: "none" },
      onchange: function () { state.kind = refs.kindSel.value; refresh(); },
    },
      App.h("option", { value: "" }, "全部类型"),
      App.h("option", { value: "text" }, "文本"),
      App.h("option", { value: "image" }, "图片"),
    );
    /* v5.4 O1：分组过滤下拉 + 分组管理（Ditto 式归组） */
    refs.groupSel = App.h("select", {
      class: "input", style: { width: "auto", flex: "none", maxWidth: "140px" },
      onchange: function () { state.group = refs.groupSel.value; refresh(); },
    },
      App.h("option", { value: "all" }, "全部分组"));
    refs.groupMgrBtn = App.h("button", {
      class: "btn", style: { flex: "none" }, title: "分组管理（新增/删除）",
      onclick: function (ev) {
        var items = state.groups.map(function (g) {
          return { label: "删除分组「" + g.name + "」（" + g.count + " 条）",
            icon: "trash", danger: true,
            onclick: function () {
              App.confirm("删除分组「" + g.name + "」？\n组内条目将移回未分组。")
                .then(function (ok) {
                  if (!ok) return;
                  App.tryCall("cliphist_group_delete", g.id).then(function (r) {
                    if (r.ok) { loadGroups(); refresh(); App.toast("已删除分组", "ok"); }
                    else App.toast(r.err, "error");
                  });
                });
            } };
        });
        items.push("sep");
        items.push({ label: "+ 新建分组", icon: "plus", onclick: function () {
          App.prompt("新建分组", "", "分组名称（≤24 字）：").then(function (name) {
            if (name === null || !String(name).trim()) return;
            App.tryCall("cliphist_group_add", String(name).trim()).then(function (r) {
              if (!r.ok) { App.toast(r.err, "error"); return; }
              loadGroups(); App.toast("分组已创建", "ok");
            });
          });
        } });
        App.overflowMenu(ev.currentTarget, items);
      },
    }, "分组");
    /* v5.4 O4：自定义命令模板管理（≤5 条，{text} 变量） */
    refs.cmdMgrBtn = App.h("button", {
      class: "btn", style: { flex: "none" }, title: "自定义命令（条目右键执行）",
      onclick: function (ev) {
        var cmds = ((App.state.cfg || {}).clip_cmds) || [];
        var items = cmds.map(function (c, i) {
          return { label: "删除「" + c.name + "」：" + c.cmd.slice(0, 40),
            icon: "trash", danger: true,
            onclick: function () {
              var next = cmds.slice();
              next.splice(i, 1);
              App.tryCall("clip_cmds_set", next).then(function (r) {
                if (!r.ok) { App.toast(r.err, "error"); return; }
                if (App.state.cfg) App.state.cfg.clip_cmds = r.data;
                App.toast("已删除命令模板", "ok");
              });
            } };
        });
        if (items.length) items.push("sep");
        items.push({
          label: cmds.length >= 5 ? "已达上限（5 条），请先删除" : "+ 新建命令", icon: "plus",
          disabled: cmds.length >= 5,
          onclick: function () {
            App.modal({
              title: "新建自定义命令",
              okText: "保存",
              inputs: [
                { label: "命令名称（右键菜单显示）", value: "", id: "name",
                  placeholder: "如：百度搜索" },
                { label: "命令模板（{text} = 条目文本）", value: "", id: "cmd",
                  placeholder: "如 cmd /c start https://www.baidu.com/s?wd={text}" },
              ],
            }).then(function (v) {
              if (!v) return;
              /* App.modal 多输入返回值数组（按 inputs 顺序） */
              var name = String(v[0] || "").trim();
              var cmd = String(v[1] || "").trim();
              if (!name || !cmd) { App.toast("名称与命令体均不能为空", "warn"); return; }
              var next = cmds.concat([{ name: name, cmd: cmd }]);
              App.tryCall("clip_cmds_set", next).then(function (r) {
                if (!r.ok) { App.toast(r.err, "error"); return; }
                if (App.state.cfg) App.state.cfg.clip_cmds = r.data;
                App.toast("命令模板已保存，右键条目即可执行", "ok");
              });
            });
          },
        });
        App.overflowMenu(ev.currentTarget, items);
      },
    }, "命令");
    refs.tagBox = App.h("input", { style: { display: "inline-flex", flex: "none" } });
    refs.tgl = App.h("input", {
      type: "checkbox",
      onchange: function () { toggleMonitor(); },
    });

    el.appendChild(App.h("div", { class: "card cliphist-toolbar" },
      App.h("div", { class: "row" },
        refs.search,
        refs.kindSel,
        refs.groupSel,
        refs.groupMgrBtn,
        App.h("label", { class: "switch", style: { flex: "none" } },
          refs.tgl, App.h("span", { class: "track" }), "监控"),
        refs.tagBox,
        /* v5.4：「清理」下拉（合并去重 + 清空全部），常驻按钮数保持不增 */
        App.h("button", {
          class: "btn", style: { flex: "none" },
          onclick: function (ev) {
            App.overflowMenu(ev.currentTarget, [
              { label: "合并相同内容", icon: "copy",
                hint: "每组重复文本仅保留一条（置顶优先）",
                onclick: doDedup },
              "sep",
              { label: "清空全部…", icon: "trash", danger: true, onclick: clearAll },
            ]);
          },
        }, "清理 ⋯"),
      ),
    ));

    /* 列表（滚动区） */
    refs.list = App.h("div", { class: "cliphist-list" });
    var listCard = App.h("div", { style: { flex: "1", minHeight: "0", display: "flex", flexDirection: "column" } },
      refs.list);
    el.appendChild(listCard);

    /* 浮动多选操作条 */
    refs.floatSel = App.h("span", { class: "hint", style: { margin: "0" } });
    refs.floatMerge = App.h("button", { class: "btn sm", onclick: mergeCopy }, "合并复制");
    refs.floatBar = App.h("div", { class: "cliphist-floatbar", style: { display: "none" } },
      refs.floatSel,
      refs.floatMerge,
      App.h("button", { class: "btn sm", onclick: copySelected }, "复制"),
      App.h("button", { class: "btn sm danger", onclick: deleteSelected }, "删除"),
      App.h("button", { class: "btn sm", onclick: function () {
        state.sel.clear(); renderList();
      } }, "取消"),
    );
    listCard.style.position = "relative";
    listCard.appendChild(refs.floatBar);

    /* 状态行 */
    refs.status = App.h("div", { class: "hint", style: { flex: "none" } });

    /* 快捷键：Ctrl+F 搜索 / Delete 删除选中 / Ctrl+C 复制 / Esc 取消 */
    el.addEventListener("keydown", function (e) {
      var inInput = /input|textarea|select/i.test(e.target.tagName || "");
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
        e.preventDefault(); refs.search.focus(); refs.search.select();
      } else if (e.key === "Delete" && !inInput && state.sel.size) {
        e.preventDefault(); deleteSelected();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c"
                 && !inInput && state.sel.size) {
        e.preventDefault(); copySelected();
      } else if (e.key === "Escape" && state.sel.size) {
        state.sel.clear(); renderList();
      }
    });

    /* 监控捕获到新内容时立即刷新（后端事件推送）。轮询已交给 ctx.every，
       离开页面会自动跳过，见 poll() */
    App.on("cliphist_changed", function () {
      /* 监控捕获新内容时立即刷新（后端事件推送） */
      if (state.running) refresh();
    });

    pageCtx = ctx;   // v5.3：注入页面上下文（轮询与后续"开启监控"都走它）
    loadGroups();
    refreshState().then(function () {
      refresh();
      poll(ctx);
    });
  }
})();
