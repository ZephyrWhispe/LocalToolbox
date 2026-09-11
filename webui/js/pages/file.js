/* 文件管理页：目录浏览、常用位置、复制/剪切/粘贴、新建/删除/打开、拖放复制。 */
(function () {
  "use strict";

  const state = {
    cwd: "", parent: "", entries: [],
    sel: new Set(), anchor: -1,
    sortKey: null, sortDir: 1,
    clip: null, busy: false,
  };
  let refs = {};

  /* ---------------- 工具 ---------------- */
  function baseName(p) {
    return p.replace(/[\\/]+$/, "").split(/[\\/]/).pop();
  }

  function sortedEntries() {
    const arr = state.entries.slice();
    if (!state.sortKey) return arr;
    const dir = state.sortDir;
    const key = state.sortKey;
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

  function selectedPaths() {
    return [...state.sel];
  }

  /* ---------------- 渲染 ---------------- */
  const COLS = [
    { key: "name", label: "名称" },
    { key: "type", label: "类型", width: "56px" },
    { key: "size", label: "大小", width: "78px", mono: true },
    { key: "mtime", label: "修改时间", width: "120px", mono: true },
  ];

  function renderHeader() {
    refs.thead.innerHTML = "";
    refs.thead.appendChild(
      App.h("div", { style: { display: "flex", gap: "10px", alignItems: "center" } },
        COLS.map((c) =>
          App.h("span", {
            class: c.mono ? "mono" : null,
            style: {
              width: c.width || null, flex: c.width ? "none" : "1",
              cursor: "pointer", userSelect: "none",
            },
            onclick: () => {
              if (state.sortKey === c.key) state.sortDir = -state.sortDir;
              else { state.sortKey = c.key; state.sortDir = 1; }
              renderTable();
            },
          },
            c.label,
            state.sortKey === c.key
              ? App.h("span", { class: "muted" }, state.sortDir > 0 ? " ▲" : " ▼")
              : null,
          ),
        ),
      ),
    );
  }

  function rowEl(arr, e, idx) {
    const selected = state.sel.has(e.path);
    return App.h("div", {
      class: "list-item click",
      style: selected ? { background: "var(--accent-soft)", borderColor: "var(--border2)" } : null,
      title: e.path,
      onclick: (ev) => selectRow(arr, idx, ev),
      ondblclick: () => activate(e),
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

  function renderTable() {
    renderHeader();
    refs.tbody.innerHTML = "";
    const arr = sortedEntries();
    if (!arr.length) {
      refs.tbody.appendChild(App.h("div", { class: "empty" }, "空目录"));
      return;
    }
    arr.forEach((e, i) => refs.tbody.appendChild(rowEl(arr, e, i)));
  }

  function renderClip() {
    if (!state.clip) {
      refs.clipLabel.textContent = "剪贴板：空";
      refs.clipLabel.style.color = "var(--faint)";
    } else if (state.clip.action === "copy") {
      refs.clipLabel.textContent = `剪贴板：已复制 ${state.clip.count} 项（可重复粘贴）`;
      refs.clipLabel.style.color = "var(--text)";
    } else {
      refs.clipLabel.textContent = `剪贴板：已剪切 ${state.clip.count} 项，请前往目标目录粘贴`;
      refs.clipLabel.style.color = "var(--text)";
    }
  }

  /* ---------------- 选择 ---------------- */
  function selectRow(arr, idx, ev) {
    const e = arr[idx];
    if (!e) return;
    if (ev.ctrlKey || ev.metaKey) {
      if (state.sel.has(e.path)) state.sel.delete(e.path);
      else state.sel.add(e.path);
      state.anchor = idx;
    } else if (ev.shiftKey && state.anchor >= 0) {
      const a = Math.min(state.anchor, idx), b = Math.max(state.anchor, idx);
      state.sel = new Set(arr.slice(a, b + 1).map((x) => x.path));
    } else {
      state.sel = new Set([e.path]);
      state.anchor = idx;
    }
    renderTable();
  }

  function activate(e) {
    if (e.is_dir) { load(e.path); return; }
    App.tryCall("file_open", e.path).then((r) => {
      if (!r.ok) App.toast(r.err, "error");
    });
  }

  /* ---------------- 数据加载 ---------------- */
  async function loadPlaces() {
    const r = await App.tryCall("file_places");
    if (!r.ok) return;
    refs.places.innerHTML = "";
    refs.places.appendChild(App.h("option", { value: "" }, "常用位置"));
    for (const p of r.data) {
      refs.places.appendChild(App.h("option", { value: p.path }, p.name));
    }
    refs.places.selectedIndex = 0;
  }

  async function load(path) {
    const r = await App.tryCall("file_list", path);
    if (!r.ok) {
      /* 同旧页 refresh：访问失败时提示并清空列表 */
      App.toast(r.err, "error", 6000);
      state.entries = [];
      state.sel.clear();
      state.anchor = -1;
      renderTable();
      return false;
    }
    state.cwd = r.data.path;
    state.parent = r.data.parent;
    state.entries = r.data.entries;
    state.sel.clear();
    state.anchor = -1;
    refs.path.value = state.cwd;
    renderTable();
    return true;
  }

  /* ---------------- 动作 ---------------- */
  async function doCopy() {
    const paths = selectedPaths();
    if (!paths.length) { App.toast("请先选择要复制的项目。"); return; }
    const r = await App.tryCall("file_copy", paths);
    if (!r.ok) { App.toast(r.err, "error"); return; }
    state.clip = r.data;
    renderClip();
  }

  async function doCut() {
    const paths = selectedPaths();
    if (!paths.length) { App.toast("请先选择要剪切的项目。"); return; }
    const r = await App.tryCall("file_cut", paths);
    if (!r.ok) { App.toast(r.err, "error"); return; }
    state.clip = r.data;
    renderClip();
  }

  async function doPaste() {
    if (state.busy) return;
    state.busy = true;
    const r = await App.tryCall("file_paste", state.cwd);
    state.busy = false;
    if (!r.ok) { App.toast(r.err, "error"); return; }
    if (r.data.cleared) { state.clip = null; renderClip(); }
    App.toast(r.data.msg, "ok");
    load(state.cwd);
  }

  async function doDelete() {
    const paths = selectedPaths();
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
    load(state.cwd);
  }

  async function doNewFolder() {
    const name = await App.prompt("新建文件夹", "", "文件夹名称：");
    if (name === null || !name.trim()) return;
    if (state.busy) return;
    state.busy = true;
    const r = await App.tryCall("file_new_folder", state.cwd, name.trim());
    state.busy = false;
    if (!r.ok) { App.toast(r.err, "error"); return; }
    App.toast(r.data.msg, "ok");
    load(state.cwd);
  }

  function doOpen() {
    /* 同旧页：仅处理第一个选中项 */
    const paths = selectedPaths();
    if (!paths.length) return;
    const e = state.entries.find((x) => x.path === paths[0]);
    if (e) activate(e);
  }

  function doUp() {
    if (state.parent) load(state.parent);
  }

  function doGoto() {
    const path = refs.path.value.trim();
    if (path) load(path);
  }

  function onPlaceChange() {
    const v = refs.places.value;
    if (v) load(v);
    refs.places.selectedIndex = 0;
  }

  function onDrop(ev) {
    ev.preventDefault();
    const files = Array.from((ev.dataTransfer && ev.dataTransfer.files) || []);
    const paths = files.map((f) => f.path).filter(Boolean);
    if (!paths.length) { App.toast("无法获取拖入文件的本地路径", "warn"); return; }
    if (state.busy) return;
    state.busy = true;
    App.tryCall("file_drop", paths, state.cwd).then((r) => {
      state.busy = false;
      if (!r.ok) { App.toast(r.err, "error"); return; }
      App.toast(r.data.msg, "ok");
      load(state.cwd);
    });
  }

  /* ---------------- 页面注册 ---------------- */
  App.registerPage({
    id: "file",
    title: "文件管理",
    icon: "filetext",
    group: "工具与增效",

    async mount(el) {
      el.innerHTML = "";
      refs = {};

      refs.places = App.h("select", { class: "input", style: { maxWidth: "160px" }, onchange: onPlaceChange });
      refs.path = App.h("input", {
        class: "input grow", style: { minWidth: "160px" },
        placeholder: "输入路径，例如 C:\\ 或 \\\\电脑名\\共享名",
        onkeydown: (e) => { if (e.key === "Enter") doGoto(); },
      });

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "文件管理"),
        App.h("div", { class: "sub" }, "浏览本机与局域网共享目录，复制 / 剪切 / 粘贴 / 删除 / 打开"),
      ));

      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "row" },
          App.h("button", { class: "btn", onclick: doUp }, "上级"),
          refs.places,
          refs.path,
          App.h("button", { class: "btn", onclick: doGoto }, "转到"),
          App.h("button", {
            class: "btn", onclick: () => load(state.cwd),
            html: App.icon("refresh", 14) + "<span>刷新</span>",
          }),
        ),
      ));

      refs.thead = App.h("div", {
        class: "hint", style: {
          padding: "2px 12px 8px", borderBottom: "1px solid var(--border)",
        },
      });
      refs.tbody = App.h("div", { class: "list", style: { marginTop: "10px" } });
      refs.table = App.h("div", null, refs.thead, refs.tbody);
      refs.table.addEventListener("dragover", (e) => e.preventDefault());
      refs.table.addEventListener("drop", onDrop);

      refs.clipLabel = App.h("span", { class: "hint", style: { marginLeft: "auto" } }, "剪贴板：空");

      el.appendChild(App.h("div", { class: "card" },
        refs.table,
        App.h("div", { class: "sep" }),
        App.h("div", { class: "row" },
          App.h("button", { class: "btn sm", onclick: doCopy }, "复制"),
          App.h("button", { class: "btn sm", onclick: doCut }, "剪切"),
          App.h("button", { class: "btn sm", onclick: doPaste }, "粘贴"),
          App.h("button", { class: "btn sm", onclick: doNewFolder }, "新建文件夹"),
          App.h("button", { class: "btn sm danger", onclick: doDelete }, "删除"),
          App.h("button", { class: "btn sm primary", onclick: doOpen }, "打开"),
          refs.clipLabel,
        ),
      ));

      renderClip();
      await loadPlaces();
      await load("");
    },

    /* 对应旧页 refresh_places：每次进入页面刷新常用位置（驱动器可能变化） */
    show() { loadPlaces(); },
  });
})();
