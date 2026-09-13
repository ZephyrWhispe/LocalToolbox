/* 文件管理页（v5.2 P2）：页内标签「浏览 | 工具」。
   浏览标签 = 三栏固定布局 + 多标签 + 双窗格 + 会话恢复。
   架构：Side（窗格）持有 tabs[]，每 tab 独立目录状态（cwd/sel/排序/历史/过滤/搜索）；
   单栏 = 一个 Side 占满；双窗格 = 两个 Side 左右（可拖分隔）；导航/操作/状态栏
   始终作用于「活动侧」的当前 tab。标签组持久化到 cfg.file_tabs（继续上次浏览）。
   跨窗格：复制/移动到另一侧、拖拽（Shift=移动）、在另一窗格打开。
   低频功能一律收进「更多 ⋯」菜单或右键菜单。 */
(function () {
  "use strict";

  let uid = 1;

  const state = {
    sides: [],          // [{ tabs, active, host, tabbar, thead, tbody, tableBox }]
    activeSide: 0,
    dual: false,
    clip: null, busy: false,
    view: "list",       // list | grid（全局偏好）
    favs: [], places: [],
    treeOpen: false,
    treeExpanded: new Set(),
    treeChildren: {},   // path -> nodes
    prog: null,
  };

  /* ---------------- tab / side 工厂 ---------------- */
  function makeTab(cwd) {
    return {
      id: uid++, cwd: cwd || "", parent: "", pinned: false,
      dirty: true, entries: [],
      sel: new Set(), anchor: -1,
      sortKey: null, sortDir: 1,
      hist: [], histIdx: -1,
      mode: "list", searchResults: [], searchTruncated: false, searchKw: "",
      filter: "",
    };
  }

  function makeSide(cwd) {
    return { tabs: [makeTab(cwd)], active: 0 };
  }

  function side(i) {
    const idx = i != null ? i : state.activeSide;
    const s = state.sides[idx];
    /* v5.4 修复：makeSideDom 的 DOM 引用此前从未绑定到模型侧，renderTabs /
       paintTable 因 s.tabbar / s.tbody 缺失直接 return → 浏览窗格无标签栏、无行。
       这里对齐索引做惰性绑定（覆盖 makeSide / restoreSession / toggleDual 全部创建路径） */
    if (s && !s.tbody && refs.sides && refs.sides[idx]) {
      Object.assign(s, refs.sides[idx]);
    }
    return s;
  }
  function curTab() { const s = side(); return s.tabs[s.active]; }

  function curEntries() {
    const t = curTab();
    return t.mode === "search" ? t.searchResults : t.entries;
  }

  function visibleEntries(tab) {
    let arr = tab.mode === "search" ? tab.searchResults : tab.entries;
    if (tab.mode === "list" && tab.filter) {
      const kw = tab.filter.toLowerCase();
      arr = arr.filter((e) => e.name.toLowerCase().includes(kw));
    }
    return arr;
  }

  function sortedEntries(tab) {
    const arr = visibleEntries(tab).slice();
    if (!tab.sortKey) return arr;
    const dir = tab.sortDir, key = tab.sortKey;
    const val = (e) =>
      key === "name" ? e.name.toLowerCase()
        : key === "type" ? (e.is_dir ? 0 : 1)
          : key === "size" ? e.size
            : e.mtime;
    arr.sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      const va = val(a), vb = val(b);
      if (va < vb) return -dir;
      if (va > vb) return dir;
      return 0;
    });
    return arr;
  }

  /* ---------------- 路径工具 ---------------- */
  function baseName(p) {
    return p.replace(/[\\/]+$/, "").split(/[\\/]/).pop();
  }

  function parentOf(p) {
    const cur = p.replace(/[\\/]+$/, "");
    if (/^\\\\[^\\]/.test(p)) {
      const parts = p.replace(/^\\+/, "").split("\\").filter(Boolean);
      return parts.length > 2
        ? parts.slice(0, -1).reduce((a, s) => a + "\\" + s, "\\\\") : p;
    }
    const parent = cur.replace(/[\\/][^\\]*$/, "");
    if (!parent) return "";
    return /^[A-Za-z]:$/.test(parent) ? parent + "\\" : parent;
  }

  function crumbSegs(p) {
    const segs = [];
    if (/^\\\\/.test(p)) {
      const parts = p.replace(/^\\+/, "").split("\\").filter(Boolean);
      if (parts.length) {
        segs.push({ label: "\\\\" + parts[0], path: "\\\\" + parts[0] });
        let acc = "\\\\" + parts[0];
        for (let i = 1; i < parts.length; i++) {
          acc += "\\" + parts[i];
          segs.push({ label: parts[i], path: acc });
        }
      }
    } else {
      const parts = p.split("\\").filter(Boolean);
      let acc = "";
      for (const part of parts) {
        acc = acc ? acc + "\\" + part : part;
        if (!acc.includes("\\")) acc += "\\";   // "C:" → "C:\"
        segs.push({ label: part, path: acc });
      }
    }
    return segs;
  }

  /* ---------------- 数据加载 ---------------- */
  async function loadTab(tab, path, push = true) {
    const r = await App.tryCall("file_list", path);
    tab.mode = "list";
    tab.filter = "";
    tab.sel.clear();
    tab.anchor = -1;
    if (!r.ok) {
      App.toast(r.err, "error", 6000);
      tab.entries = [];
      tab.dirty = false;
      paintAll();
      return false;
    }
    tab.cwd = r.data.path;
    tab.parent = r.data.parent;
    tab.entries = r.data.entries;
    tab.dirty = false;
    if (push) {
      tab.hist = tab.hist.slice(0, tab.histIdx + 1);
      tab.hist.push(tab.cwd);
      tab.histIdx = tab.hist.length - 1;
    }
    state.treeChildren = {};
    if (state.treeOpen) renderTree();
    paintAll();
    saveSession();
    return true;
  }

  async function load(path, push = true) {
    return loadTab(curTab(), path, push);
  }

  /* ---------------- tab / side 管理 ---------------- */
  async function activateTab(sideIdx, tabIdx) {
    const s = side(sideIdx);
    if (!s || !s.tabs[tabIdx]) return;
    const prev = state.activeSide;
    s.active = tabIdx;
    state.activeSide = sideIdx;
    renderTabs(prev);
    renderTabs(sideIdx);
    syncFilterInput();
    const tab = s.tabs[tabIdx];
    if (tab.dirty) await loadTab(tab, tab.cwd, false);
    else paintAll();
    saveSession();
  }

  /* 过滤框跟随活动 tab 的过滤词 */
  function syncFilterInput() {
    if (refs.filterInput) refs.filterInput.value = curTab().filter || "";
  }

  async function addTab(cwd, activate = true) {
    const s = side();
    const t = makeTab(cwd != null ? cwd : curTab().cwd);
    s.tabs.push(t);
    if (activate) {
      s.active = s.tabs.length - 1;
      await loadTab(t, t.cwd, true);
    }
    renderTabs(state.activeSide);
    saveSession();
    return t;
  }

  async function closeTab(sideIdx, tabIdx) {
    const s = side(sideIdx);
    const t = s.tabs[tabIdx];
    if (!t) return;
    if (t.pinned) { App.toast("标签已固定，请先取消固定", "warn"); return; }
    s.tabs.splice(tabIdx, 1);
    if (!s.tabs.length) {
      /* 关最后一个标签：保留窗格，开新标签回到主目录 */
      const nt = makeTab("");
      s.tabs.push(nt);
      s.active = 0;
      await loadTab(nt, nt.cwd, true);
      renderTabs(sideIdx);
      saveSession();
      return;
    }
    if (s.active >= s.tabs.length) s.active = s.tabs.length - 1;
    await activateTab(sideIdx, s.active);
  }

  function nextTab() {
    const s = side();
    activateTab(state.activeSide, (s.active + 1) % s.tabs.length);
  }

  async function toggleDual() {
    state.dual = !state.dual;
    if (state.dual && state.sides.length < 2) {
      /* 右侧窗格以当前目录开新标签 */
      const s2 = makeSide(curTab().cwd);
      state.sides.push(s2);
      state.activeSide = 1;
      renderTabs(1);
      const t = s2.tabs[0];
      await loadTab(t, t.cwd, true);
    }
    layoutSides();
    paintAll();
    saveSession();
  }

  /* 布局：单栏（隐藏侧 2 与分隔条）/ 双栏 */
  function layoutSides() {
    refs.paneWrap.classList.toggle("dual", state.dual);
    refs.sideHosts.forEach((host, i) => {
      host.style.display = state.dual || i === 0 ? "" : "none";
    });
    refs.divider.style.display = state.dual ? "" : "none";
    refs.toCopy.style.display = state.dual ? "" : "none";
    refs.toMove.style.display = state.dual ? "" : "none";
  }

  /* ---------------- 会话持久化（继续上次浏览） ---------------- */
  let sessionTimer = null;
  function saveSession() {
    clearTimeout(sessionTimer);
    sessionTimer = setTimeout(async () => {
      const data = {
        dual: state.dual,
        activeSide: state.activeSide,
        sides: state.sides.map((s) => ({
          active: s.active,
          tabs: s.tabs.map((t) => ({ cwd: t.cwd, pinned: !!t.pinned })),
        })),
      };
      const r = await App.tryCall("cfg_set", "file_tabs", data);
      if (!r.ok) console.warn("会话保存失败", r.err);
    }, 400);
  }

  async function restoreSession() {
    const r = await App.tryCall("cfg_get");
    const saved = r.ok ? (r.data.file_tabs || null) : null;
    if (saved && Array.isArray(saved.sides) && saved.sides.length
        && saved.sides.some((s) => s.tabs && s.tabs.length)) {
      state.dual = !!(saved.dual && saved.sides.length >= 2);
      state.sides = saved.sides.slice(0, state.dual ? 2 : 1).map((s) => ({
        active: Math.max(0, s.active | 0),
        tabs: (s.tabs && s.tabs.length)
          ? s.tabs.map((t) => {
              const tab = makeTab(t.cwd);
              tab.pinned = !!t.pinned;
              return tab;
            })
          : [makeTab("")],   // 会话损坏/空 tabs → 兜底主目录
      }));
      if (!state.sides[state.activeSide]) state.activeSide = 0;
      state.sides.forEach((s, i) => {
        s.active = Math.min(s.active, s.tabs.length - 1);
        renderTabs(i);
      });
      layoutSides();
      /* 只加载活动侧的当前 tab，其余懒加载（首次激活时） */
      await activateTab(state.activeSide, side().active);
      return;
    }
    /* 无会话 → 默认单栏主目录 */
    await loadTab(side().tabs[0], "", true);
  }

  /* ---------------- 渲染：标签栏 ---------------- */
  function renderTabs(sideIdx) {
    const s = side(sideIdx);
    if (!s || !s.tabbar) return;
    s.tabbar.innerHTML = "";
    s.tabs.forEach((t, i) => {
      const active = sideIdx === state.activeSide && i === s.active;
      const chip = App.h("div", {
        class: "ftab" + (active ? " active" : "") + (t.pinned ? " pinned" : ""),
        title: t.cwd,
        onclick: () => activateTab(sideIdx, i),
        onauxclick: (ev) => { if (ev.button === 1) { ev.preventDefault(); closeTab(sideIdx, i); } },
        oncontextmenu: (ev) => {
          ev.preventDefault();
          App.contextMenu([
            { label: t.pinned ? "取消固定" : "固定标签", icon: "star",
              onclick: () => { t.pinned = !t.pinned; renderTabs(sideIdx); saveSession(); } },
            { label: "复制标签（同目录新开）", icon: "copy",
              onclick: async () => {
                const nt = makeTab(t.cwd);
                s.tabs.splice(i + 1, 0, nt);
                await activateTab(sideIdx, i + 1);
              } },
            "sep",
            { label: "关闭", icon: "x", danger: !t.pinned,
              disabled: t.pinned, onclick: () => closeTab(sideIdx, i) },
            { label: "关闭其他", disabled: s.tabs.length < 2,
              onclick: async () => {
                s.tabs = s.tabs.filter((x, j) => j === i || x.pinned);
                const k = s.tabs.findIndex((x) => x === t);
                s.active = Math.max(0, k);
                await activateTab(sideIdx, s.active);
              } },
          ], ev.clientX, ev.clientY);
        },
      },
        App.h("span", { html: App.icon("folder", 11),
          style: { flex: "none", display: "inline-flex" } }),
        App.h("span", { class: "ftab-title" }, baseName(t.cwd) || t.cwd),
        t.pinned ? App.h("span", { class: "ftab-pin", html: App.icon("star", 10) }) : null,
        App.h("span", {
          class: "ftab-x", title: "关闭（中键同）",
          onclick: (ev) => { ev.stopPropagation(); closeTab(sideIdx, i); },
        }, "×"),
      );
      s.tabbar.appendChild(chip);
    });
    s.tabbar.appendChild(App.h("button", {
      class: "ftab-add", title: "新建标签（Ctrl+T）",
      onclick: () => addTab(),
      html: App.icon("plus", 12),
    }));
  }

  /* ---------------- 渲染：文件表 ---------------- */
  const COLS = [
    { key: "name", label: "名称" },
    { key: "type", label: "类型", width: "56px" },
    { key: "size", label: "大小", width: "78px", mono: true },
    { key: "mtime", label: "修改时间", width: "120px", mono: true },
  ];

  function renderHeader(sideIdx) {
    const s = side(sideIdx);
    const tab = s.tabs[s.active];
    s.thead.innerHTML = "";
    s.thead.style.display = state.view === "grid" ? "none" : "";
    s.thead.appendChild(
      App.h("div", { style: { display: "flex", gap: "10px", alignItems: "center" } },
        COLS.map((c) =>
          App.h("span", {
            class: c.mono ? "mono" : null,
            style: {
              width: c.width || null, flex: c.width ? "none" : "1",
              cursor: "pointer", userSelect: "none",
            },
            onclick: () => {
              state.activeSide = sideIdx;
              if (tab.sortKey === c.key) tab.sortDir = -tab.sortDir;
              else { tab.sortKey = c.key; tab.sortDir = 1; }
              paintTable(sideIdx);
            },
          },
            c.label,
            tab.sortKey === c.key
              ? App.h("span", { class: "muted" }, tab.sortDir > 0 ? " ▲" : " ▼")
              : null,
          ),
        ),
      ),
    );
  }

  function rowEl(sideIdx, tab, arr, e, idx) {
    const selected = tab.sel.has(e.path);
    return App.h("div", {
      class: "list-item click",
      style: selected ? { background: "var(--accent-soft)", borderColor: "var(--border2)" } : null,
      title: e.path, draggable: true,
      onclick: (ev) => selectRow(sideIdx, tab, arr, idx, ev),
      ondblclick: () => activate(e),
      oncontextmenu: (ev) => {
        ev.preventDefault();
        if (!selected) { tab.sel = new Set([e.path]); tab.anchor = idx; paintTable(sideIdx); }
        rowMenu(e, ev.clientX, ev.clientY);
      },
      ondragstart: (ev) => dragStart(ev, sideIdx),
    },
      App.h("span", { class: "li-title", style: { flex: "1", minWidth: "0" } }, e.name),
      App.h("span", { style: { width: "56px", flex: "none", display: "flex" } },
        App.h("span", { class: "tag" + (e.is_dir ? " accent" : "") }, e.is_dir ? "文件夹" : "文件"),
      ),
      App.h("span", {
        class: "mono", style: {
          width: "78px", flex: "none", textAlign: "right",
          fontSize: "11px", color: "var(--muted)",
        },
      }, e.is_dir ? "" : App.fmtBytes(e.size)),
      App.h("span", {
        class: "mono", style: {
          width: "120px", flex: "none", textAlign: "right",
          fontSize: "11px", color: "var(--muted)",
        },
      }, App.fmtDate(e.mtime)),
    );
  }

  function gridEl(sideIdx, tab, arr, e, idx) {
    const selected = tab.sel.has(e.path);
    return App.h("div", {
      class: "g-item" + (selected ? " selected" : ""),
      title: e.path, draggable: true,
      onclick: (ev) => selectRow(sideIdx, tab, arr, idx, ev),
      ondblclick: () => activate(e),
      oncontextmenu: (ev) => {
        ev.preventDefault();
        if (!selected) { tab.sel = new Set([e.path]); tab.anchor = idx; paintTable(sideIdx); }
        rowMenu(e, ev.clientX, ev.clientY);
      },
      ondragstart: (ev) => dragStart(ev, sideIdx),
    },
      App.h("span", { html: App.icon(e.is_dir ? "folder" : "filetext", 26),
        style: { color: e.is_dir ? "var(--accent)" : "var(--muted)" } }),
      App.h("div", { class: "g-name" }, e.name),
      App.h("div", { class: "g-sub" }, e.is_dir ? "文件夹" : App.fmtBytes(e.size)),
    );
  }

  function paintTable(sideIdx) {
    const s = side(sideIdx);
    if (!s || !s.tbody) return;
    const tab = s.tabs[s.active];
    renderHeader(sideIdx);
    s.tbody.innerHTML = "";
    const arr = sortedEntries(tab);
    const isGrid = state.view === "grid";
    if (!arr.length) {
      s.tbody.className = "list";
      s.tbody.appendChild(App.h("div", { class: "empty" }, emptyText(tab)));
      return;
    }
    if (isGrid) {
      s.tbody.className = "file-grid";
      arr.forEach((e, i) => s.tbody.appendChild(gridEl(sideIdx, tab, arr, e, i)));
    } else {
      s.tbody.className = "list";
      arr.forEach((e, i) => s.tbody.appendChild(rowEl(sideIdx, tab, arr, e, i)));
    }
  }

  function emptyText(tab) {
    if (tab.mode === "search") return "无匹配结果";
    return tab.filter ? "无匹配项" : "空目录";
  }

  /* 全量重绘：两侧表 + 全局栏 */
  function paintAll() {
    state.sides.forEach((_, i) => paintTable(i));
    renderCrumb();
    updateActionbar();
    renderStatus();
  }

  /* ---------------- 选择 ---------------- */
  function selectRow(sideIdx, tab, arr, idx, ev) {
    const e = arr[idx];
    if (!e) return;
    state.activeSide = sideIdx;
    if (ev.ctrlKey || ev.metaKey) {
      if (tab.sel.has(e.path)) tab.sel.delete(e.path);
      else tab.sel.add(e.path);
      tab.anchor = idx;
    } else if (ev.shiftKey && tab.anchor >= 0) {
      const a = Math.min(tab.anchor, idx), b = Math.max(tab.anchor, idx);
      tab.sel = new Set(arr.slice(a, b + 1).map((x) => x.path));
    } else {
      tab.sel = new Set([e.path]);
      tab.anchor = idx;
    }
    paintTable(sideIdx);
    updateActionbar();
    renderStatus();
  }

  function selectAll() {
    curTab().sel = new Set(sortedEntries(curTab()).map((x) => x.path));
    paintTable(state.activeSide);
  }

  function activate(e) {
    if (e.is_dir) { load(e.path); return; }
    App.tryCall("file_open", e.path).then((r) => {
      if (!r.ok) App.toast(r.err, "error");
    });
  }

  /* ---------------- 拖拽（内部跨窗格复制 / 移动） ---------------- */
  function dragStart(ev, sideIdx) {
    const s = side(sideIdx);
    const tab = s.tabs[s.active];
    let paths = [...tab.sel];
    if (!paths.length) paths = [ev.currentTarget.title];
    ev.dataTransfer.setData("application/x-lt-file",
      JSON.stringify({ from: sideIdx, paths }));
    ev.dataTransfer.effectAllowed = "copyMove";
  }

  function sideDrop(ev, sideIdx) {
    ev.preventDefault();
    /* 内部拖拽：跨窗格复制 / 移动（Shift=移动） */
    const raw = ev.dataTransfer.getData("application/x-lt-file");
    if (raw) {
      try {
        const d = JSON.parse(raw);
        if (d.from !== sideIdx && (d.paths || []).length) {
          crossCopy(d.paths, sideIdx, ev.shiftKey);
        }
      } catch (e) { /* 忽略坏数据 */ }
      return;
    }
    /* 外部文件拖入 */
    const files = Array.from((ev.dataTransfer && ev.dataTransfer.files) || []);
    const paths = files.map((f) => f.path).filter(Boolean);
    if (!paths.length) { App.toast("无法获取拖入文件的本地路径", "warn"); return; }
    if (state.busy) return;
    state.busy = true;
    const target = side(sideIdx).tabs[side(sideIdx).active];
    App.tryCall("file_drop", paths, target.cwd).then((r) => {
      state.busy = false;
      if (!r.ok) { App.toast(r.err, "error"); return; }
      App.toast(r.data.msg, "ok");
      loadTab(target, target.cwd, false);
    });
  }

  /* ---------------- 动作（作用于活动侧当前 tab） ---------------- */
  async function doCopy() {
    const paths = [...curTab().sel];
    if (!paths.length) { App.toast("请先选择要复制的项目。"); return; }
    const r = await App.tryCall("file_copy", paths);
    if (!r.ok) { App.toast(r.err, "error"); return; }
    state.clip = r.data;
    updateActionbar();
  }

  async function doCut() {
    const paths = [...curTab().sel];
    if (!paths.length) { App.toast("请先选择要剪切的项目。"); return; }
    const r = await App.tryCall("file_cut", paths);
    if (!r.ok) { App.toast(r.err, "error"); return; }
    state.clip = r.data;
    updateActionbar();
  }

  async function doPaste() {
    await pasteInto(curTab().cwd);
  }

  async function pasteInto(dest) {
    if (state.busy) return;
    state.busy = true;
    const r = await App.tryCall("file_paste_async", dest);
    state.busy = false;
    if (!r.ok) { App.toast(r.err, "error"); return; }
    App.toast(`开始${r.data.action === "cut" ? "移动" : "复制"} ${r.data.count} 个项目…`, "ok");
  }

  /* 跨窗格：把 paths 复制/移动到目标侧当前目录 */
  async function crossCopy(paths, toSideIdx, move) {
    if (!paths.length) return;
    const apiName = move ? "file_cut" : "file_copy";
    const r = await App.tryCall(apiName, paths);
    if (!r.ok) { App.toast(r.err, "error"); return; }
    state.clip = r.data;
    updateActionbar();
    await pasteInto(side(toSideIdx).tabs[side(toSideIdx).active].cwd);
  }

  function otherSideIdx() { return state.dual ? 1 - state.activeSide : -1; }

  async function doToOther(move) {
    const to = otherSideIdx();
    if (to < 0) return;
    const paths = [...curTab().sel];
    if (!paths.length) { App.toast("请先选择项目"); return; }
    await crossCopy(paths, to, move);
  }

  async function doRename() {
    const paths = [...curTab().sel];
    if (paths.length !== 1) { App.toast("请选中一个要重命名的项目（F2）"); return; }
    const old = paths[0];
    const name = await App.prompt("重命名", baseName(old), "新名称：");
    if (name === null || !name.trim() || name.trim() === baseName(old)) return;
    if (state.busy) return;
    state.busy = true;
    const r = await App.tryCall("file_rename", old, name.trim());
    state.busy = false;
    if (!r.ok) { App.toast(r.err, "error"); return; }
    await load(curTab().cwd, false);
  }

  async function doNewFile() {
    const name = await App.prompt("新建文件", "", "文件名称（含扩展名）：");
    if (name === null || !name.trim()) return;
    if (state.busy) return;
    state.busy = true;
    const r = await App.tryCall("file_new_file", curTab().cwd, name.trim());
    state.busy = false;
    if (!r.ok) { App.toast(r.err, "error"); return; }
    await load(curTab().cwd, false);
  }

  async function doNewFolder() {
    const name = await App.prompt("新建文件夹", "", "文件夹名称：");
    if (name === null || !name.trim()) return;
    if (state.busy) return;
    state.busy = true;
    const r = await App.tryCall("file_new_folder", curTab().cwd, name.trim());
    state.busy = false;
    if (!r.ok) { App.toast(r.err, "error"); return; }
    App.toast(r.data.msg, "ok");
    load(curTab().cwd, false);
  }

  async function doDelete() {
    const paths = [...curTab().sel];
    if (!paths.length) { App.toast("请先选择要删除的项目。"); return; }
    const names = paths.slice(0, 10).map(baseName).join("\n");
    const more = paths.length > 10 ? `\n... 等共 ${paths.length} 项` : "";
    if (!(await App.confirm("确认删除",
      `确定删除以下项目吗？\n${names}${more}\n\n将尝试移入回收站。`))) return;
    if (state.busy) return;
    state.busy = true;
    const r = await App.tryCall("file_delete", paths);
    state.busy = false;
    if (!r.ok) { App.toast(r.err, "error"); return; }
    App.toast(r.data.msg, "ok");
    load(curTab().cwd, false);
  }

  async function doSearch() {
    const kw = await App.prompt("递归搜索", "", `在 ${curTab().cwd} 及其子目录中搜索：`);
    if (kw === null || !kw.trim()) return;
    const tab = curTab();
    tab.searchKw = kw.trim();
    const r = await App.tryCall("file_search", tab.cwd, kw.trim(), 200);
    if (!r.ok) { App.toast(r.err, "error"); return; }
    tab.mode = "search";
    tab.searchResults = r.data.entries;
    tab.searchTruncated = !!r.data.truncated;
    tab.sel.clear();
    tab.anchor = -1;
    paintAll();
  }

  function exitSearch() {
    const tab = curTab();
    tab.mode = "list";
    paintAll();
  }

  async function doFavAdd() {
    if (!curTab().cwd) return;
    const name = await App.prompt("收藏当前目录", baseName(curTab().cwd), "显示名称：");
    if (name === null) return;
    const r = await App.tryCall("file_fav_add", curTab().cwd, name);
    if (!r.ok) { App.toast(r.err, "error"); return; }
    state.favs = r.data;
    if (state.treeOpen) renderTree();
    App.toast("已收藏", "ok");
  }

  async function doCopyPath() {
    const paths = [...curTab().sel];
    if (!paths.length) { App.toast("请先选择项目"); return; }
    const ok = await App.copyText(paths.join("\r\n"));
    if (ok) App.toast("已复制路径", "ok");
  }

  async function doStat() {
    const paths = [...curTab().sel];
    if (paths.length !== 1) { App.toast("请选中一个项目查看属性"); return; }
    const r = await App.tryCall("file_stat", paths[0]);
    if (!r.ok) { App.toast(r.err, "error"); return; }
    const d = r.data;
    App.modal({
      title: "属性",
      body: App.h("div", null,
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "名称"),
          App.h("span", { class: "prop-val" }, d.name)),
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "类型"),
          App.h("span", { class: "prop-val" }, d.kind)),
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "大小"),
          App.h("span", { class: "prop-val mono" }, App.fmtBytes(d.size))),
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "创建"),
          App.h("span", { class: "prop-val mono" }, d.created)),
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "修改"),
          App.h("span", { class: "prop-val mono" }, d.modified)),
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "访问"),
          App.h("span", { class: "prop-val mono" }, d.accessed)),
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "只读"),
          App.h("span", { class: "prop-val" }, d.readonly ? "是" : "否")),
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "路径"),
          App.h("span", { class: "prop-val mono", style: { fontSize: "11px" } }, d.path)),
        App.h("div", { class: "row", style: { marginTop: "8px" } },
          App.h("button", { class: "btn sm", onclick: () => {
            App.copyText(d.path).then(() => App.toast("已复制路径", "ok"));
          } }, "复制路径"),
        ),
      ),
      okText: "关闭",
    });
  }

  function doTerminal() {
    App.tryCall("file_terminal", curTab().cwd).then((r) => {
      if (!r.ok) App.toast(r.err, "error");
    });
  }

  function doUp() {
    const tab = curTab();
    if (tab.parent) load(tab.parent);
  }

  function goBack() {
    const tab = curTab();
    if (tab.histIdx > 0) {
      tab.histIdx--;
      load(tab.hist[tab.histIdx], false);
    }
  }

  function onFilterInput() {
    clearTimeout(onFilterInput._t);
    onFilterInput._t = setTimeout(() => {
      curTab().filter = (refs.filterInput.value || "").trim().toLowerCase();
      paintTable(state.activeSide);
      renderStatus();
    }, 250);
  }

  /* ---------------- 面包屑 / 状态栏 / 操作栏 ---------------- */
  function renderCrumb() {
    if (!refs.crumb) return;
    const tab = curTab();
    refs.crumb.innerHTML = "";
    if (tab.mode === "search") {
      refs.crumb.appendChild(App.h("span", { class: "hint", style: { padding: "3px 0" } },
        `搜索「${tab.searchKw || ""}」结果`));
      return;
    }
    const segs = crumbSegs(tab.cwd);
    segs.forEach((sg, i) => {
      const isCur = i === segs.length - 1;
      refs.crumb.appendChild(App.h("span", {
        class: "crumb-seg" + (isCur ? " current" : ""),
        title: "跳转到 " + sg.path,
        onclick: () => load(sg.path),
      }, sg.label));
      refs.crumb.appendChild(App.h("span", {
        class: "crumb-arrow", title: "浏览同级目录",
        onclick: (ev) => {
          ev.stopPropagation();
          showSiblings(sg.path, ev.clientX, ev.clientY);
        },
      }, "▾"));
      if (!isCur) {
        refs.crumb.appendChild(App.h("span", { class: "crumb-arrow" }, "›"));
      }
    });
    refs.crumb.appendChild(App.h("span", {
      class: "crumb-seg", title: "直接输入路径",
      style: { color: "var(--faint)" },
      onclick: () => {
        refs.crumb.innerHTML = "";
        refs.crumb.appendChild(refs.pathInput);
        refs.pathInput.value = tab.cwd;
        refs.pathInput.focus();
      },
    }, "⌨"));
  }

  async function showSiblings(segPath, x, y) {
    const parent = parentOf(segPath) || segPath;
    const r = await App.tryCall("file_tree", parent);
    if (!r.ok || !r.data.nodes.length) {
      App.toast(r.err || "没有同级目录", "warn");
      return;
    }
    App.contextMenu(r.data.nodes.map((n) => ({
      label: n.name, icon: "folder", onclick: () => load(n.path),
    })), x, y);
  }

  function renderStatus() {
    if (!refs.statText) return;
    const tab = curTab();
    const arr = tab.mode === "search" ? tab.searchResults : tab.entries;
    const shown = sortedEntries(tab).length;
    const filtered = tab.mode === "list" && tab.filter
      ? `（过滤自 ${arr.length}）` : "";
    const n = tab.sel.size;
    let selSize = 0;
    for (const e of arr) if (tab.sel.has(e.path)) selSize += e.size || 0;
    const mode = tab.mode === "search"
      ? (tab.searchTruncated ? " · 搜索结果（已截断）" : " · 搜索结果") : "";
    const other = otherSideIdx() >= 0
      ? ` · 另一侧：${side(otherSideIdx()).tabs[side(otherSideIdx()).active].cwd}` : "";
    refs.statText.textContent =
      `${shown} 项${filtered} · 已选 ${n}${selSize ? "（" + App.fmtBytes(selSize) + "）" : ""}${mode}${other}`;
  }

  function updateActionbar() {
    const n = curTab().sel.size;
    const one = n === 1;
    refs.btnCopy.disabled = !n;
    refs.btnCut.disabled = !n;
    refs.btnPaste.disabled = !state.clip;
    refs.btnRename.disabled = !one;
    refs.btnDelete.disabled = !n;
    refs.btnPaste.classList.toggle("primary", !!state.clip);
    refs.clipLabel.textContent = !state.clip ? "剪贴板：空"
      : (state.clip.action === "copy"
        ? `已复制 ${state.clip.count} 项（可重复粘贴）`
        : `已剪切 ${state.clip.count} 项，前往目标目录粘贴`);
    refs.clipLabel.style.color = state.clip ? "var(--text)" : "var(--faint)";
  }

  /* ---------------- 树抽屉 ---------------- */
  function toggleTree(force) {
    state.treeOpen = force != null ? force : !state.treeOpen;
    refs.tree.style.display = state.treeOpen ? "" : "none";
    if (state.treeOpen) renderTree();
  }

  function renderTree() {
    refs.tree.innerHTML = "";
    refs.tree.appendChild(App.h("div", { class: "hint", style: { padding: "2px 6px 4px" } }, "收藏"));
    if (!state.favs.length) {
      refs.tree.appendChild(App.h("div", { class: "hint", style: { padding: "2px 6px 6px", fontSize: "11px" } },
        "暂无收藏（更多 ⋯ → 收藏当前目录）"));
    }
    for (const f of state.favs) {
      refs.tree.appendChild(App.h("div", { class: "tree-row", title: f.path,
        onclick: () => load(f.path) },
        App.h("span", { class: "tree-caret", html: App.icon("folder", 12) }),
        App.h("span", { style: { overflow: "hidden", textOverflow: "ellipsis" } },
          f.name || baseName(f.path)),
      ));
    }
    refs.tree.appendChild(App.h("div", { class: "hint", style: { padding: "8px 6px 4px" } }, "位置"));
    for (const p of state.places) {
      refs.tree.appendChild(App.h("div", { class: "tree-row", title: p.path,
        onclick: () => load(p.path) },
        App.h("span", { class: "tree-caret", html: App.icon(p.path.endsWith("\\") ? "server" : "folder", 12) }),
        App.h("span", { style: { overflow: "hidden", textOverflow: "ellipsis" } }, p.name),
      ));
    }
    refs.tree.appendChild(App.h("div", { class: "hint", style: { padding: "8px 6px 4px" } }, "目录"));
    if (curTab().cwd) {
      refs.tree.appendChild(treeNodeEl(
        { name: baseName(curTab().cwd) || curTab().cwd, path: curTab().cwd }, 0));
    }
  }

  function treeNodeEl(node) {
    const expanded = state.treeExpanded.has(node.path);
    const row = App.h("div", { class: "tree-row" + (node.path === curTab().cwd ? " current" : "") },
      App.h("span", {
        class: "tree-caret",
        onclick: (ev) => { ev.stopPropagation(); toggleTreeNode(node.path); },
      }, expanded ? "▾" : (node.has_child === false ? "" : "▸")),
      App.h("span", {
        style: { overflow: "hidden", textOverflow: "ellipsis", flex: "1" },
        onclick: () => { load(node.path); },
      }, node.name),
    );
    const wrap = App.h("div", { class: "tree-node" }, row);
    if (expanded) {
      const box = App.h("div", { class: "tree-children" });
      const children = state.treeChildren[node.path] || [];
      if (!children.length) {
        box.appendChild(App.h("div", { class: "hint", style: { padding: "2px 6px", fontSize: "11px" } },
          "（无子目录）"));
      } else {
        for (const c of children) box.appendChild(treeNodeEl(c));
      }
      wrap.appendChild(box);
    }
    return wrap;
  }

  async function toggleTreeNode(path) {
    if (state.treeExpanded.has(path)) {
      state.treeExpanded.delete(path);
      renderTree();
      return;
    }
    if (!state.treeChildren[path]) {
      const r = await App.tryCall("file_tree", path);
      if (!r.ok) { App.toast(r.err, "error"); return; }
      state.treeChildren[path] = r.data.nodes || [];
    }
    state.treeExpanded.add(path);
    renderTree();
  }

  /* ---------------- 右键菜单 ---------------- */
  function rowMenu(e, x, y) {
    const tab = curTab();
    const n = tab.sel.size;
    const items = [
      { label: "打开", icon: e.is_dir ? "folder" : "filetext", onclick: () => activate(e) },
      "sep",
      { label: "复制", icon: "copy", hint: "Ctrl+C", onclick: doCopy },
      { label: "剪切", icon: "scissors", hint: "Ctrl+X", onclick: doCut },
    ];
    if (state.clip) {
      items.push({ label: "粘贴到当前目录", icon: "clipboard", onclick: doPaste });
    }
    if (state.dual) {
      items.push(
        { label: "复制到另一侧", icon: "copy", onclick: () => doToOther(false) },
        { label: "移动到另一侧", icon: "scissors", onclick: () => doToOther(true) });
      if (e.is_dir) {
        items.push({ label: "在另一窗格打开", icon: "folder",
          onclick: async () => {
            const si = otherSideIdx();
            const s = side(si);
            const t = makeTab(e.path);
            s.tabs.push(t);
            await activateTab(si, s.tabs.length - 1);
          } });
      }
    }
    items.push("sep",
      { label: "重命名", hint: "F2", disabled: n !== 1, onclick: doRename },
      { label: "删除", icon: "trash", hint: "Del", danger: true, onclick: doDelete },
      "sep",
      { label: "复制路径", icon: "copy", onclick: doCopyPath },
      { label: "复制文件名", onclick: async () => {
        const ok = await App.copyText([...tab.sel].map(baseName).join("\r\n"));
        if (ok) App.toast("已复制文件名", "ok");
      } });
    if (e.is_dir) {
      items.push({ label: "收藏此目录", icon: "star",
        onclick: async () => {
          const r = await App.tryCall("file_fav_add", e.path, e.name);
          if (r.ok) { state.favs = r.data; if (state.treeOpen) renderTree(); App.toast("已收藏", "ok"); }
        } });
    }
    items.push("sep",
      { label: "属性", icon: "info", disabled: n !== 1, onclick: doStat });
    App.contextMenu(items, x, y);
  }

  function blankMenu(x, y) {
    const items = [];
    if (state.clip) {
      items.push({ label: "粘贴", icon: "clipboard", hint: "Ctrl+V", onclick: doPaste }, "sep");
    }
    items.push(
      { label: "新建文件夹", icon: "folder", onclick: doNewFolder },
      { label: "新建文件", icon: "filetext", onclick: doNewFile },
      "sep",
      { label: "刷新", icon: "refresh", onclick: () => load(curTab().cwd, false) },
      { label: "全选", hint: "Ctrl+A", onclick: selectAll },
      "sep",
      { label: "收藏当前目录", icon: "star", onclick: doFavAdd },
      { label: "搜索子目录…", icon: "search", onclick: doSearch },
      { label: "在终端打开", icon: "terminal", onclick: doTerminal },
    );
    App.contextMenu(items, x, y);
  }

  function moreMenu(anchorEl) {
    const tab = curTab();
    App.overflowMenu(anchorEl, [
      { label: tab.mode === "search" ? "退出搜索结果" : "搜索子目录…", icon: "search",
        onclick: () => (tab.mode === "search" ? exitSearch() : doSearch()) },
      { label: "收藏当前目录", icon: "star", onclick: doFavAdd },
      { label: "在终端打开", icon: "terminal", onclick: doTerminal },
      "sep",
      { label: "复制路径", icon: "copy", disabled: !tab.sel.size, onclick: doCopyPath },
      { label: "属性", icon: "info", disabled: tab.sel.size !== 1, onclick: doStat },
      "sep",
      { label: "新建文件夹", icon: "folder", onclick: doNewFolder },
      { label: "新建文件", icon: "filetext", onclick: doNewFile },
      "sep",
      { label: (state.dual ? "退出" : "开启") + "双窗格", icon: "columns", hint: "Ctrl+Shift+S",
        onclick: toggleDual },
      { label: (state.treeOpen ? "关闭" : "打开") + "目录树", icon: "tree", hint: "Ctrl+B",
        onclick: () => toggleTree() },
      { label: "视图：" + (state.view === "list" ? "切换到网格" : "切换到列表"),
        icon: state.view === "list" ? "grid" : "list",
        onclick: () => { state.view = state.view === "list" ? "grid" : "list"; paintAll(); } },
    ]);
  }

  /* ---------------- 进度 ---------------- */
  function renderProgress() {
    if (!refs.progBox) return;
    const p = state.prog;
    if (!p || p.stage === "done") {
      refs.progBox.style.display = "none";
      return;
    }
    refs.progBox.style.display = "";
    if (p.stage === "error") {
      refs.progText.textContent = "粘贴失败：" + (p.err || "");
      return;
    }
    const total = p.total || 0;
    const done = p.done || 0;
    const fmtB = (n) => n >= 1073741824 ? (n / 1073741824).toFixed(1) + " GB"
      : n >= 1048576 ? (n / 1048576).toFixed(1) + " MB"
        : n >= 1024 ? (n / 1024).toFixed(0) + " KB" : n + " B";
    const pct = total > 0 ? Math.min(100, Math.round(done * 100 / total)) : 0;
    refs.progText.textContent = `${p.name ? p.name + " · " : ""}` +
      (p.unit === "files" ? `${done}/${total} 个文件`
        : `${fmtB(done)} / ${fmtB(total)}（${pct}%）`);
    refs.progBar.style.width = (total > 0 ? pct : 5) + "%";
  }

  /* ---------------- 工具 pane（v5.2 P3：批量重命名 / 压缩解压 / 哈希 / 回收站） ---------------- */
  function fillFromSel(ta, filterExt) {
    const paths = [...curTab().sel];
    const list = filterExt
      ? paths.filter((p) => p.toLowerCase().endsWith(filterExt)) : paths;
    ta.value = list.join("\r\n");
    if (ta.value) App.toast(`已带入 ${list.length} 项`, "ok");
    else App.toast("浏览页没有选中的项目", "warn");
  }

  function buildToolsPane() {
    const wrap = App.h("div", { class: "file-pane", style: { paddingTop: "8px", gap: "10px" } });

    /* --- 批量重命名 --- */
    const rnPaths = App.h("textarea", {
      class: "input mono", rows: 5,
      placeholder: "每行一个文件/文件夹完整路径…（可从浏览页带入）",
      style: { fontFamily: "Consolas, monospace", fontSize: "11.5px", resize: "vertical" },
    });
    const rnFind = App.h("input", { class: "input", style: { width: "120px" }, placeholder: "查找" });
    const rnReplace = App.h("input", { class: "input", style: { width: "120px" }, placeholder: "替换为" });
    const rnPrefix = App.h("input", { class: "input", style: { width: "120px" }, placeholder: "前缀" });
    const rnNumber = App.h("input", { type: "checkbox" });
    const rnNumStart = App.h("input", {
      class: "input", type: "number", value: "1", min: "0", style: { width: "64px" },
    });
    const rnExtSel = App.h("select", { class: "input", style: { width: "auto" } },
      App.h("option", { value: "" }, "扩展名不变"),
      App.h("option", { value: "lower" }, "扩展名转小写"),
      App.h("option", { value: "upper" }, "扩展名转大写"));
    const rnPreviewBox = App.h("div", {
      style: { maxHeight: "260px", overflowY: "auto", display: "none" },
    });
    const rnApply = App.h("button", { class: "btn primary", disabled: true }, "执行重命名");
    const rnRule = () => ({
      find: rnFind.value, replace: rnReplace.value, prefix: rnPrefix.value,
      number: rnNumber.checked, num_start: parseInt(rnNumStart.value, 10) || 1,
      ext_case: rnExtSel.value,
    });
    rnNumber.addEventListener("change", () => { rnApply.disabled = true; });

    const paneRename = App.h("div", { class: "card" },
      App.h("div", { class: "card-title" }, "批量重命名"),
      App.h("div", { class: "hint" },
        "规则组合：查找替换 / 加前缀 / 重排为 001 序号 / 扩展名大小写。先预览再执行。"),
      rnPaths,
      App.h("div", { class: "row", style: { marginTop: "8px" } },
        App.h("button", { class: "btn sm", onclick: () => fillFromSel(rnPaths) }, "带入选中"),
        App.h("span", { class: "field-label", style: { flex: "none" } }, "查找"), rnFind,
        App.h("span", { class: "field-label", style: { flex: "none" } }, "替换"), rnReplace,
      ),
      App.h("div", { class: "row", style: { marginTop: "8px" } },
        App.h("span", { class: "field-label", style: { flex: "none" } }, "前缀"), rnPrefix,
        App.h("label", { class: "switch", style: { flex: "none" } },
          rnNumber, App.h("span", { class: "track" }), "重排为序号"),
        rnNumStart, rnExtSel,
      ),
      App.h("div", { class: "row", style: { marginTop: "10px" } },
        App.h("button", {
          class: "btn", onclick: async function () {
            const paths = rnPaths.value.split("\n").map((s) => s.trim()).filter(Boolean);
            if (!paths.length) { App.toast("请先填入路径", "warn"); return; }
            const r = await App.tryCall("file_batch_rename", paths, rnRule(), true);
            if (!r.ok) { App.toast(r.err, "error"); return; }
            rnPreviewBox.style.display = "";
            rnPreviewBox.innerHTML = "";
            rnPreviewBox.appendChild(App.h("div", { class: "hint", style: { padding: "2px 2px 6px" } },
              `预览 ${r.data.rows.length} 项（旧名 → 新名）`));
            let bad = 0;
            r.data.rows.forEach((row) => {
              const color = row.conflict === "unchanged" ? "var(--faint)"
                : (row.conflict ? "var(--danger)" : "var(--ok)");
              if (row.conflict && row.conflict !== "unchanged") bad++;
              rnPreviewBox.appendChild(App.h("div", { class: "prop-row" },
                App.h("span", { class: "prop-val mono", style: { fontSize: "11.5px", color } },
                  `${row.old}  →  ${row.conflict === "unchanged" ? "（不变）" : row.new}`),
                row.conflict && row.conflict !== "unchanged"
                  ? App.h("span", { class: "hint", style: { color: "var(--danger)", flex: "none" } },
                      row.conflict) : null,
              ));
            });
            rnApply.disabled = bad > 0;
            if (bad) App.toast(`有 ${bad} 项冲突，请调整规则`, "warn");
          },
        }, "预览"),
        rnApply,
      ),
      rnPreviewBox,
    );
    rnApply.addEventListener("click", async function () {
      if (this._busy) return;
      const paths = rnPaths.value.split("\n").map((s) => s.trim()).filter(Boolean);
      this._busy = true; this.disabled = true;
      try {
        const r = await App.tryCall("file_batch_rename", paths, rnRule(), false);
        if (!r.ok) { App.toast(r.err, "error"); return; }
        App.toast(r.data.msg, "ok", 5000);
        rnPreviewBox.style.display = "none";
        load(curTab().cwd, false);   // 刷新浏览页
      } finally {
        this._busy = false;
      }
    });

    /* --- 压缩 / 解压 --- */
    const zpPaths = App.h("textarea", {
      class: "input mono", rows: 4,
      placeholder: "每行一个要压缩的文件/文件夹…",
      style: { fontFamily: "Consolas, monospace", fontSize: "11.5px", resize: "vertical" },
    });
    const zpName = App.h("input", { class: "input", style: { width: "200px" }, placeholder: "压缩包名（默认同名.zip）" });
    const uzPath = App.h("input", { class: "input grow mono", placeholder: "ZIP 文件完整路径…" });
    const uzDest = App.h("input", { class: "input grow mono", placeholder: "解压到（留空 = 同名子目录）" });

    const paneZip = App.h("div", null,
      App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "压缩为 ZIP"),
        zpPaths,
        App.h("div", { class: "row", style: { marginTop: "8px" } },
          App.h("button", { class: "btn sm", onclick: () => fillFromSel(zpPaths) }, "带入选中"),
          zpName,
          App.h("button", {
            class: "btn primary", onclick: async function () {
              if (this._busy) return;
              const paths = zpPaths.value.split("\n").map((s) => s.trim()).filter(Boolean);
              if (!paths.length) { App.toast("请先填入路径", "warn"); return; }
              this._busy = true; this.disabled = true;
              const old = this.textContent; this.textContent = "压缩中…";
              try {
                const r = await App.tryCall("file_zip", paths, zpName.value.trim());
                if (!r.ok) { App.toast(r.err, "error", 6000); return; }
                App.toast(`已压缩 ${r.data.count} 个文件（${App.fmtBytes(r.data.size)}）`, "ok", 6000);
                load(curTab().cwd, false);
              } finally {
                this._busy = false; this.disabled = false; this.textContent = old;
              }
            },
          }, "压缩"),
        ),
      ),
      App.h("div", { class: "card", style: { marginTop: "10px" } },
        App.h("div", { class: "card-title" }, "解压 ZIP"),
        App.h("div", { class: "row" }, uzPath),
        App.h("div", { class: "row", style: { marginTop: "8px" } },
          uzDest,
          App.h("button", {
            class: "btn primary", onclick: async function () {
              if (this._busy) return;
              const src = uzPath.value.trim();
              if (!src) { App.toast("请先填入 ZIP 路径", "warn"); return; }
              this._busy = true; this.disabled = true;
              const old = this.textContent; this.textContent = "解压中…";
              try {
                const r = await App.tryCall("file_unzip", src, uzDest.value.trim());
                if (!r.ok) { App.toast(r.err, "error", 6000); return; }
                App.toast(`已解压 ${r.data.count} 项到 ${r.data.dest}`, "ok", 6000);
              } finally {
                this._busy = false; this.disabled = false; this.textContent = old;
              }
            },
          }, "解压"),
        ),
        App.h("div", { class: "hint", style: { marginTop: "6px" } },
          "GBK 中文文件名压缩包自动兼容。"),
      ),
    );
    /* 解压路径带入：选中里找 .zip */
    const uzBring = App.h("button", {
      class: "btn sm", onclick: () => {
        const hit = [...curTab().sel].find((p) => p.toLowerCase().endsWith(".zip"));
        if (hit) { uzPath.value = hit; App.toast("已带入", "ok"); }
        else App.toast("浏览页没有选中的 .zip", "warn");
      },
    }, "带入选中");
    paneZip.querySelectorAll(".card")[1]
      .querySelectorAll(".row")[0].appendChild(uzBring);

    /* --- 哈希校验 --- */
    const hsPath = App.h("input", { class: "input grow mono", placeholder: "文件完整路径…" });
    const hsMd5 = App.h("input", { type: "checkbox", checked: true });
    const hsSha1 = App.h("input", { type: "checkbox" });
    const hsSha256 = App.h("input", { type: "checkbox" });
    const hsResult = App.h("div", { style: { display: "none", flexDirection: "column", gap: "6px" } });
    const hsExpect = App.h("input", {
      class: "input mono", placeholder: "粘贴期望的哈希值进行比对…",
      oninput: () => markExpect(),
    });
    let hsHashes = {};
    function markExpect() {
      const v = hsExpect.value.trim().toLowerCase();
      if (!v) return;
      const hit = Object.entries(hsHashes).some(([, h]) => h === v);
      hsExpect.style.borderColor = hit ? "var(--ok)" : "var(--danger)";
    }
    const paneHash = App.h("div", { class: "card" },
      App.h("div", { class: "card-title" }, "哈希校验"),
      App.h("div", { class: "row" }, hsPath,
        App.h("button", { class: "btn sm", onclick: () => {
          const hit = [...curTab().sel].find((p) => !p.toLowerCase().endsWith(".zip"))
            || [...curTab().sel][0];
          if (hit) { hsPath.value = hit; App.toast("已带入", "ok"); }
          else App.toast("浏览页没有选中的项目", "warn");
        } }, "带入选中")),
      App.h("div", { class: "row", style: { marginTop: "8px" } },
        App.h("label", { class: "switch", style: { flex: "none" } },
          hsMd5, App.h("span", { class: "track" }), "MD5"),
        App.h("label", { class: "switch", style: { flex: "none" } },
          hsSha1, App.h("span", { class: "track" }), "SHA1"),
        App.h("label", { class: "switch", style: { flex: "none" } },
          hsSha256, App.h("span", { class: "track" }), "SHA256"),
        App.h("button", {
          class: "btn primary", onclick: async function () {
            if (this._busy) return;
            const algos = [];
            if (hsMd5.checked) algos.push("md5");
            if (hsSha1.checked) algos.push("sha1");
            if (hsSha256.checked) algos.push("sha256");
            if (!algos.length) { App.toast("请至少勾选一种算法", "warn"); return; }
            const p = hsPath.value.trim();
            if (!p) { App.toast("请先填入文件路径", "warn"); return; }
            this._busy = true; this.disabled = true;
            const old = this.textContent; this.textContent = "计算中…";
            try {
              const r = await App.tryCall("file_hash", p, algos);
              if (!r.ok) { App.toast(r.err, "error"); return; }
              hsHashes = r.data.hashes;
              hsResult.style.display = "flex";
              hsResult.innerHTML = "";
              for (const [algo, h] of Object.entries(hsHashes)) {
                hsResult.appendChild(App.h("div", { class: "row" },
                  App.h("span", { class: "tag accent", style: { flex: "none" } }, algo.toUpperCase()),
                  App.h("span", {
                    class: "mono hint", style: {
                      flex: "1", minWidth: "0", wordBreak: "break-all", fontSize: "11px",
                    },
                  }, h),
                  App.h("button", { class: "btn xs", onclick: () => {
                    App.copyText(h).then(() => App.toast("已复制", "ok"));
                  } }, "复制"),
                ));
              }
              hsExpect.value = "";
              hsExpect.style.borderColor = "";
              App.toast(`已计算（${App.fmtBytes(r.data.size)}）`, "ok");
            } finally {
              this._busy = false; this.disabled = false; this.textContent = old;
            }
          },
        }, "计算"),
      ),
      App.h("div", { style: { marginTop: "10px" } }, hsResult),
      App.h("div", { style: { marginTop: "10px" } }, hsExpect),
    );

    /* --- 回收站 --- */
    const binList = App.h("div", {
      style: { maxHeight: "420px", overflowY: "auto", display: "flex", flexDirection: "column", gap: "6px" },
    });
    const binStatus = App.h("span", { class: "hint", style: { marginLeft: "auto" } });
    async function refreshBin() {
      binList.innerHTML = "";
      binStatus.textContent = "读取中…";
      const r = await App.tryCall("file_recycle_list");
      if (!r.ok) {
        binStatus.textContent = "";
        binList.appendChild(App.h("div", { class: "empty" }, r.err));
        return;
      }
      binStatus.textContent = `共 ${r.data.count} 项`
        + (r.data.truncated ? "（仅显示前 100）" : "");
      if (!r.data.items.length) {
        binList.appendChild(App.h("div", { class: "empty" }, "回收站是空的"));
        return;
      }
      for (const it of r.data.items) {
        binList.appendChild(App.h("div", { class: "list-item", title: it.path },
          App.h("span", { class: "li-title", style: { flex: "1", minWidth: "0" } }, it.name || "(无名)"),
          App.h("span", { class: "hint", style: {
            flex: "none", width: "180px", overflow: "hidden",
            textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "11px",
          } }, it.origin || ""),
          App.h("span", { class: "hint mono", style: { flex: "none", width: "110px", fontSize: "11px" } },
            it.deleted || ""),
          App.h("span", { class: "hint mono", style: { flex: "none", width: "64px", textAlign: "right", fontSize: "11px" } },
            it.size != null ? App.fmtBytes(it.size) : ""),
          App.h("span", { style: { flex: "none", display: "flex", gap: "4px" } },
            App.h("button", {
              class: "btn xs", title: "还原到原位置", onclick: async (ev) => {
                ev.stopPropagation();
                const rr = await App.tryCall("file_recycle_restore", it.path);
                if (!rr.ok) { App.toast(rr.err, "error"); return; }
                App.toast("已还原", "ok");
                refreshBin();
              },
            }, "还原"),
            App.h("button", {
              class: "btn xs danger", title: "永久删除", onclick: async (ev) => {
                ev.stopPropagation();
                if (!(await App.confirm("永久删除该项？\n" + (it.name || it.path)))) return;
                const rr = await App.tryCall("file_recycle_delete", it.path);
                if (!rr.ok) { App.toast(rr.err, "error"); return; }
                App.toast("已永久删除", "ok");
                refreshBin();
              },
            }, "删除"),
          ),
        ));
      }
    }
    const paneBin = App.h("div", { class: "card" },
      App.h("div", { class: "card-title" }, "回收站"),
      App.h("div", { class: "row" },
        App.h("button", { class: "btn sm", onclick: refreshBin }, "刷新"),
        App.h("button", {
          class: "btn sm danger", onclick: async function () {
            if (this._busy) return;
            if (!(await App.confirm("确定清空回收站？此操作不可恢复！"))) return;
            this._busy = true; this.disabled = true;
            try {
              const r = await App.tryCall("file_recycle_empty");
              if (!r.ok) { App.toast(r.err, "error"); return; }
              App.toast("回收站已清空", "ok");
              refreshBin();
            } finally {
              this._busy = false; this.disabled = false;
            }
          },
        }, "清空回收站"),
        binStatus,
      ),
      App.h("div", { style: { marginTop: "8px" } }, binList),
    );

    /* 工具页内二级标签 */
    const tnav = App.subnav([
      { label: "批量重命名", el: paneRename },
      { label: "压缩 / 解压", el: paneZip },
      { label: "哈希校验", el: paneHash },
      { label: "回收站", el: paneBin },
    ]);
    wrap.appendChild(tnav);
    tnav.panes.forEach((p) => wrap.appendChild(p));
    return wrap;
  }

  /* ---------------- 页面注册 ---------------- */
  const refs = {};


  function makeSideDom(sideIdx) {
    const host = App.h("div", { class: "side-host" });
    const tabbar = App.h("div", { class: "ftabbar" });
    const thead = App.h("div", {
      class: "hint", style: {
        padding: "2px 12px 8px", borderBottom: "1px solid var(--border)",
        flex: "none",
      },
    });
    const tbody = App.h("div", { class: "list", style: { marginTop: "10px" } });
    const tableBox = App.h("div", { class: "file-table" }, thead, tbody);
    tableBox.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = e.shiftKey ? "move" : "copy";
    });
    tableBox.addEventListener("drop", (e) => sideDrop(e, sideIdx));
    tableBox.addEventListener("contextmenu", (ev) => {
      if (ev.target.closest(".list-item") || ev.target.closest(".g-item")) return;
      ev.preventDefault();
      if (state.activeSide !== sideIdx) {
        state.activeSide = sideIdx;
        renderTabs(0);
        renderTabs(1);
        syncFilterInput();
        renderCrumb();
        updateActionbar();
        renderStatus();
      }
      blankMenu(ev.clientX, ev.clientY);
    });
    tableBox.addEventListener("mousedown", () => {
      if (state.activeSide !== sideIdx) {
        /* 只切换活动侧并更新全局栏；不重绘 tbody——避免行元素被替换导致 click 丢失 */
        state.activeSide = sideIdx;
        renderTabs(0);
        renderTabs(1);
        syncFilterInput();
        renderCrumb();
        updateActionbar();
        renderStatus();
        /* 懒加载：首次激活的会话 tab 在此触发加载 */
        const s = side(sideIdx);
        const t = s.tabs[s.active];
        if (t && t.dirty) loadTab(t, t.cwd, false);
      }
    });
    host.appendChild(tabbar);
    host.appendChild(tableBox);
    return { host, tabbar, thead, tbody, tableBox };
  }

  App.registerPage({
    id: "file",
    title: "文件管理",
    icon: "filetext",
    group: "工具与增效",

    async mount(el) {
      el.innerHTML = "";
      el.classList.add("page-flex", "file-page", "file-subnav-host");
      /* v5.4 修复：重挂载时旧 DOM 引用已随 innerHTML 失效，解绑后由 side() 惰性重绑 */
      state.sides.forEach((s) => {
        delete s.host; delete s.tabbar; delete s.thead;
        delete s.tbody; delete s.tableBox;
      });

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "文件管理"),
        App.h("div", { class: "sub" },
          "多标签 · 双窗格 · 跨窗格拖拽；低频操作收在右键菜单与「更多 ⋯」中"),
      ));

      /* ===== 浏览 pane ===== */
      const browse = App.h("div", { class: "file-pane" });

      /* 导航栏（固定） */
      refs.filterInput = App.h("input", {
        class: "input grow-in",
        placeholder: "过滤当前目录…（Ctrl+F）",
        oninput: onFilterInput,
        onkeydown: (e) => {
          if (e.key === "Escape") { refs.filterInput.value = ""; onFilterInput(); }
        },
      });
      refs.pathInput = App.h("input", {
        class: "input crumb-input",
        placeholder: "输入路径，Enter 跳转",
        onkeydown: (e) => {
          if (e.key === "Enter") {
            const v = refs.pathInput.value.trim();
            if (v) load(v);
          } else if (e.key === "Escape") {
            renderCrumb();
          }
        },
        onblur: () => setTimeout(renderCrumb, 120),
      });
      refs.crumb = App.h("div", { class: "crumb" });

      browse.appendChild(App.h("div", { class: "row file-toolbar" },
        App.h("button", {
          class: "btn", title: "目录树（Ctrl+B）", onclick: () => toggleTree(),
          html: App.icon("tree", 14),
        }),
        App.h("button", {
          class: "btn", title: "后退（Alt+←）", onclick: goBack,
          html: App.icon("arrowleft", 14),
        }),
        App.h("button", {
          class: "btn", title: "上级", onclick: doUp,
          html: App.icon("arrowup", 14),
        }),
        App.h("button", {
          class: "btn", title: "刷新", onclick: () => load(curTab().cwd, false),
          html: App.icon("refresh", 14),
        }),
        refs.crumb,
        refs.filterInput,
        App.h("button", {
          class: "btn", title: "更多操作", onclick: (ev) => moreMenu(ev.currentTarget),
          html: App.icon("moreh", 14),
        }),
      ));

      /* 操作栏（固定，上下文联动） */
      refs.btnCopy = App.h("button", { class: "btn sm", onclick: doCopy }, "复制");
      refs.btnCut = App.h("button", { class: "btn sm", onclick: doCut }, "剪切");
      refs.btnPaste = App.h("button", { class: "btn sm", onclick: doPaste }, "粘贴");
      refs.btnRename = App.h("button", { class: "btn sm", onclick: doRename, title: "F2" }, "重命名");
      refs.btnDelete = App.h("button", { class: "btn sm danger", onclick: doDelete, title: "Delete" }, "删除");
      refs.toCopy = App.h("button", {
        class: "btn sm", title: "复制选中项到另一窗格", onclick: () => doToOther(false),
      }, "→ 另一侧");
      refs.toMove = App.h("button", {
        class: "btn sm", title: "移动选中项到另一窗格", onclick: () => doToOther(true),
      }, "⇒ 另一侧");

      refs.clipLabel = App.h("span", { class: "hint", style: { marginLeft: "auto" } }, "剪贴板：空");

      browse.appendChild(App.h("div", { class: "row file-actionbar" },
        refs.btnCopy, refs.btnCut, refs.btnPaste, refs.btnRename, refs.btnDelete,
        refs.toCopy, refs.toMove,
        refs.clipLabel,
      ));

      /* 主体：树抽屉 + 窗格区（单栏 / 双栏） */
      refs.tree = App.h("div", { class: "tree-drawer", style: { display: "none" } });
      refs.sides = [];
      refs.sideHosts = [];
      for (let i = 0; i < 2; i++) {
        const sd = makeSideDom(i);
        refs.sides.push(sd);
        refs.sideHosts.push(sd.host);
      }
      refs.divider = App.h("div", { class: "pane-divider", title: "拖动调整分栏比例" });
      refs.divider.addEventListener("mousedown", (e) => {
        e.preventDefault();
        const wrap = refs.paneWrap;
        const onMove = (ev) => {
          const r = wrap.getBoundingClientRect();
          const pct = Math.min(80, Math.max(20, (ev.clientX - r.left) / r.width * 100));
          refs.sideHosts[0].style.flexBasis = pct + "%";
          refs.sideHosts[1].style.flexBasis = (100 - pct) + "%";
        };
        const onUp = () => {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
        };
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });
      refs.paneWrap = App.h("div", { class: "pane-wrap" },
        refs.sideHosts[0], refs.divider, refs.sideHosts[1]);
      browse.appendChild(App.h("div", { class: "file-body" }, refs.tree, refs.paneWrap));

      /* 状态栏（固定） */
      refs.statText = App.h("span", { class: "hint" });
      refs.progBox = App.h("div", {
        style: { display: "none", alignItems: "center", gap: "8px", flex: "none", width: "240px" },
      },
        App.h("div", {
          style: {
            flex: "1", height: "6px", borderRadius: "3px", overflow: "hidden",
            background: "var(--card2)",
          },
        }, refs.progBar = App.h("div", {
          style: {
            height: "100%", width: "0%", background: "var(--accent)",
            transition: "width .15s",
          },
        })),
        App.h("span", { class: "hint", style: { maxWidth: "140px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }),
      );
      refs.progText = refs.progBox.children[1];
      browse.appendChild(App.h("div", { class: "file-status" },
        refs.statText,
        App.h("span", { style: { flex: "1" } }),
        refs.progBox,
      ));

      /* 初始布局（单栏；restoreSession 可能切双栏） */
      layoutSides();
      /* 默认单侧窗格数据（restoreSession 有会话时会整体重建） */
      if (!state.sides.length) state.sides = [makeSide("")];

      /* ===== 工具 pane（v5.2 P3：四件套） ===== */
      const toolsPane = buildToolsPane();

      /* 页内一级标签 */
      const nav = App.subnav([
        { label: "浏览", el: browse },
        { label: "工具", el: toolsPane },
      ]);
      el.appendChild(nav);
      nav.panes.forEach((p) => el.appendChild(p));

      /* 键盘快捷键（焦点在页面内时；作用于活动侧） */
      el.addEventListener("keydown", (e) => {
        const inInput = /input|textarea|select/i.test(e.target.tagName || "");
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "s") {
          e.preventDefault(); toggleDual(); return;
        }
        if ((e.ctrlKey || e.metaKey) && !e.shiftKey) {
          const k = e.key.toLowerCase();
          if (k === "t") { e.preventDefault(); addTab(); return; }
          if (k === "w" && !inInput) { e.preventDefault(); closeTab(state.activeSide, side().active); return; }
          if (k === "tab") { e.preventDefault(); nextTab(); return; }
          if (k === "c" && !inInput) { e.preventDefault(); doCopy(); return; }
          if (k === "x" && !inInput) { e.preventDefault(); doCut(); return; }
          if (k === "v" && !inInput) { e.preventDefault(); doPaste(); return; }
          if (k === "a" && !inInput) { e.preventDefault(); selectAll(); return; }
          if (k === "f") { e.preventDefault(); refs.filterInput.focus(); refs.filterInput.select(); return; }
          if (k === "b") { e.preventDefault(); toggleTree(); return; }
        }
        if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); goBack(); return; }
        if (e.key === "F2") { e.preventDefault(); doRename(); return; }
        if (e.key === "Delete" && !inInput) { e.preventDefault(); doDelete(); return; }
        if (e.key === "Enter" && !inInput && curTab().sel.size === 1) {
          e.preventDefault();
          const arr = sortedEntries(curTab());
          const hit = arr.find((x) => curTab().sel.has(x.path));
          if (hit) activate(hit);
          return;
        }
        if (e.key === "Backspace" && !inInput) { e.preventDefault(); doUp(); }
      });
      /* 异步粘贴进度（file_progress 事件） */
      App.on("file_progress", (p) => {
        state.prog = p;
        if (p.stage === "done") {
          App.toast(p.msg || "粘贴完成", "ok");
          if (p.cleared) state.clip = null;
          /* 刷新两侧（跨窗格粘贴目标可能非活动侧） */
          state.sides.forEach((s, i) => {
            const t = s.tabs[s.active];
            if (!t.dirty) loadTab(t, t.cwd, false);
          });
          setTimeout(() => { state.prog = null; renderProgress(); }, 1200);
        }
        renderProgress();
        updateActionbar();
      });

      /* 恢复会话（或默认主目录） */
      await restoreSession();
    },

    show() {
      App.tryCall("file_places").then((r) => { if (r.ok) state.places = r.data; });
      App.tryCall("file_favs").then((r) => {
        if (r.ok) { state.favs = r.data; if (state.treeOpen) renderTree(); }
      });
    },
  });
})();
