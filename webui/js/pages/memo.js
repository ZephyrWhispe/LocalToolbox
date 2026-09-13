/* 备忘录页：分组收纳 + markdown 编辑/预览 + 搜索/置顶。
   快速捕捉走 Ctrl+Alt+M 弹窗（memo_pop），本页负责完整管理。 */
(function () {
  "use strict";

  const state = {
    groups: [],
    memos: [],
    query: "",
    curGroup: "",      // ""=全部, 0=未分组, >0=分组 id
    cur: null,         // 当前编辑的备忘全文（null=列表模式）
    curId: 0,
    preview: false,
  };
  let refs = {};
  let keyHandler = null;

  function esc(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function mdToHtml(md) {
    try {
      if (window.marked && window.DOMPurify) {
        return window.DOMPurify.sanitize(window.marked.parse(String(md || "")));
      }
    } catch (e) { /* 渲染失败回退纯文本 */ }
    return null;
  }

  /* ---------------- 数据 ---------------- */
  async function refresh(keepEditor) {
    const r = await App.tryCall("memo_list", state.query,
      state.curGroup === "" ? null : state.curGroup);
    if (!r.ok) { App.toast(r.err, "error"); return; }
    state.groups = r.data.groups || [];
    state.memos = r.data.memos || [];
    renderGroups();
    if (!keepEditor) renderBody();
  }

  /* ---------------- 分组侧栏 ---------------- */
  function renderGroups() {
    if (!refs.groups) return;
    refs.groups.innerHTML = "";
    const rowStyle = (active) => ({
      padding: "7px 10px", borderRadius: "8px",
      background: active ? "var(--accent-soft)" : "transparent",
    });
    refs.groups.appendChild(App.h("div", {
      class: "list-item click", style: rowStyle(state.curGroup === ""),
      onclick: () => { state.curGroup = ""; refresh(); },
    },
      App.h("span", { class: "li-main", style: { display: "flex", justifyContent: "space-between" } },
        App.h("span", null, "全部"),
        App.h("span", { class: "hint" }, String(state.memos.length)),
      )));
    for (const g of state.groups) {
      const active = state.curGroup !== "" && String(state.curGroup) === String(g.id);
      const row = App.h("div", {
        class: "list-item click", style: rowStyle(active),
        onclick: () => { state.curGroup = g.id; refresh(); },
      },
        App.h("span", { class: "li-main", style: { display: "flex", justifyContent: "space-between" } },
          App.h("span", g.id ? null : { class: "hint" }, g.name || "未分组"),
          App.h("span", { class: "hint" }, String(g.count || 0)),
        ));
      if (g.id) {
        row.appendChild(App.h("span", { class: "li-actions", style: { flex: "none", display: "flex", gap: "4px" } },
          App.h("button", {
            class: "btn sm", title: "重命名分组", onclick: (e) => {
              e.stopPropagation();
              renameGroup(g);
            },
          }, "✎"),
          App.h("button", {
            class: "btn sm", title: "删除分组（备忘移入未分组）", onclick: async (e) => {
              e.stopPropagation();
              const ok = await App.confirm(`删除分组「${g.name}」？组内备忘将移入「未分组」。`);
              if (!ok) return;
              const r = await App.tryCall("memo_group_delete", g.id);
              if (!r.ok) App.toast(r.err, "error");
              if (String(state.curGroup) === String(g.id)) state.curGroup = "";
              await refresh();
            },
          }, "×"),
        ));
      }
      refs.groups.appendChild(row);
    }
    // 新建分组
    const input = App.h("input", {
      class: "input", placeholder: "新分组名",
      style: { flex: "1", minWidth: "0", height: "30px" },
      onkeydown: (e) => {
        if (e.key === "Enter") addGroup(input);
      },
    });
    refs.groups.appendChild(App.h("div", { class: "row", style: { marginTop: "8px" } },
      input,
      App.h("button", { class: "btn sm", onclick: () => addGroup(input) }, "+"),
    ));
  }

  async function addGroup(input) {
    const name = input.value.trim();
    if (!name) return;
    const r = await App.tryCall("memo_group_add", name);
    if (!r.ok) { App.toast(r.err, "error"); return; }
    input.value = "";
    await refresh();
  }

  async function renameGroup(g) {
    const name = await App.prompt(`重命名分组「${g.name}」`, g.name);
    if (!name || name.trim() === g.name) return;
    const r = await App.tryCall("memo_group_rename", g.id, name.trim());
    if (!r.ok) { App.toast(r.err, "error"); return; }
    await refresh();
  }

  /* ---------------- 列表 / 编辑器 ---------------- */
  function renderBody() {
    if (!refs.body) return;
    refs.body.innerHTML = "";
    if (state.cur) { renderEditor(); return; }
    // 工具栏 + 列表
    const search = App.h("input", {
      class: "input", placeholder: "搜索备忘（标题与内容）…",
      value: state.query, style: { flex: "1", minWidth: "0" },
      oninput: (e) => {
        clearTimeout(renderBody._t);
        renderBody._t = setTimeout(() => {
          state.query = e.target.value.trim();
          refresh();
        }, 250);
      },
    });
    refs.search = search;
    refs.body.appendChild(App.h("div", { class: "row" },
      search,
      App.h("button", {
        class: "btn primary", onclick: () => {
          state.cur = { id: 0, title: "", content: "", group_id: state.curGroup || null, pinned: false };
          state.curId = 0;
          state.preview = false;
          renderBody();
        },
      }, "+ 新建备忘"),
    ));
    const list = App.h("div", { class: "list", style: { flex: "1", overflowY: "auto", minHeight: "0", marginTop: "10px" } });
    if (!state.memos.length) {
      list.appendChild(App.h("div", { class: "empty" },
        state.query ? "无匹配结果" : "暂无备忘\n点击「+ 新建备忘」或按 Ctrl+Alt+M 快速记录"));
    }
    for (const m of state.memos) {
      list.appendChild(App.h("div", {
        class: "card", style: { padding: "10px 14px", marginBottom: "8px", cursor: "pointer" },
        onclick: async () => {
          const r = await App.tryCall("memo_get", m.id);
          if (!r.ok) { App.toast(r.err, "error"); return; }
          state.cur = r.data;
          state.curId = m.id;
          state.preview = false;
          renderBody();
        },
      },
        App.h("div", { style: { display: "flex", alignItems: "center", gap: "8px" } },
          m.pinned ? App.h("span", { style: { color: "var(--warn)" } }, "★") : null,
          App.h("span", { style: { fontWeight: "600", flex: "1", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } },
            m.title || "(无标题)"),
          m.group_name ? App.h("span", { class: "hint" }, "#" + m.group_name) : null,
          App.h("span", { class: "hint" }, App.fmtDate(m.updated)),
        ),
        m.preview ? App.h("div", { class: "hint", style: { marginTop: "4px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } },
          m.preview) : null,
      ));
    }
    refs.body.appendChild(list);
  }

  function renderEditor() {
    const m = state.cur;
    const title = App.h("input", {
      class: "input", value: m.title || "", placeholder: "标题（空则取首行）",
      style: { flex: "1", minWidth: "0" },
    });
    const groupSel = App.h("select", { class: "input", style: { width: "auto" } });
    groupSel.appendChild(App.h("option", { value: "" }, "未分组"));
    for (const g of state.groups) {
      if (!g.id) continue;
      const o = App.h("option", { value: String(g.id) }, g.name);
      groupSel.appendChild(o);
      if (String(m.group_id || "") === String(g.id)) groupSel.value = String(g.id);
    }
    const content = App.h("textarea", {
      class: "input", placeholder: "markdown 内容…",
      style: {
        flex: "1", minHeight: "0", resize: "none", lineHeight: "1.6",
        fontFamily: "Consolas, monospace", fontSize: "13px",
        display: state.preview ? "none" : "block",
      },
    });
    content.value = m.content || "";
    const previewBox = App.h("div", {
      class: "md-body", style: {
        flex: "1", minHeight: "0", overflow: "auto", padding: "10px 12px",
        display: state.preview ? "block" : "none",
        background: "var(--bg2)", borderRadius: "8px", border: "1px solid var(--border)",
      },
    });

    const renderPreview = () => {
      const html = mdToHtml(content.value);
      if (html === null) {
        previewBox.textContent = content.value;
        previewBox.style.whiteSpace = "pre-wrap";
      } else {
        previewBox.innerHTML = html;
        previewBox.style.whiteSpace = "";
      }
    };
    if (state.preview) renderPreview();

    const pvBtn = App.h("button", {
      class: "btn" + (state.preview ? " primary" : ""),
      onclick: () => {
        state.preview = !state.preview;
        content.style.display = state.preview ? "none" : "block";
        previewBox.style.display = state.preview ? "block" : "none";
        pvBtn.classList.toggle("primary", state.preview);
        if (state.preview) renderPreview();
      },
    }, state.preview ? "编辑" : "预览");

    const save = async () => {
      /* v5.1c：防连点——新建路径双击会创建两条重复备忘（Ctrl+S 同走此处） */
      if (save._busy) return;
      save._busy = true;
      try {
        const gid = groupSel.value ? parseInt(groupSel.value, 10) : null;
        const r = await App.tryCall("memo_save", state.curId || 0,
          title.value.trim(), content.value, gid, !!m.pinned);
        if (!r.ok) { App.toast(r.err, "error"); return; }
        App.toast(state.curId ? "已保存" : "已创建");
        state.cur = null;
        state.curId = 0;
        await refresh();
      } finally {
        save._busy = false;
      }
    };
    const del = async () => {
      if (!state.curId) { closeEditor(); return; }
      const ok = await App.confirm("删除这条备忘？");
      if (!ok) return;
      const r = await App.tryCall("memo_delete", state.curId);
      if (!r.ok) { App.toast(r.err, "error"); return; }
      closeEditor();
      await refresh();
    };
    const closeEditor = () => { state.cur = null; state.curId = 0; renderBody(); };
    const togglePin = async () => {
      m.pinned = !m.pinned;
      if (state.curId) {
        const r = await App.tryCall("memo_pin", state.curId, m.pinned);
        if (!r.ok) { App.toast(r.err, "error"); return; }
      }
      renderEditor();
    };

    refs.body.appendChild(App.h("div", { style: { display: "flex", flexDirection: "column", flex: "1", minHeight: "0" } },
      App.h("div", { class: "row" },
        App.h("button", { class: "btn", onclick: closeEditor }, "← 返回"),
        title,
        App.h("button", {
          class: "btn sm" + (m.pinned ? " primary" : ""),
          onclick: togglePin, title: "置顶",
        }, m.pinned ? "★ 已置顶" : "☆ 置顶"),
        groupSel,
      ),
      App.h("div", {
        style: {
          display: "flex", flexDirection: "column", flex: "1", minHeight: "0",
          marginTop: "10px", gap: "10px",
        },
      },
        content, previewBox,
        App.h("div", { class: "row" },
          App.h("button", { class: "btn primary", onclick: save }, "保存（Ctrl+S）"),
          pvBtn,
          App.h("span", { style: { flex: "1" } }),
          App.h("button", { class: "btn danger", onclick: del }, "删除"),
        ),
      ),
    ));

    // Ctrl+S 保存（编辑器内有效）
    const keyS = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        save();
      }
    };
    content.addEventListener("keydown", keyS);
    title.addEventListener("keydown", keyS);
  }

  /* ---------------- 页面注册 ---------------- */
  App.registerPage({
    id: "memo",
    title: "备忘录",
    icon: "edit",
    group: "笔记与安全",

    async mount(el) {
      el.innerHTML = "";
      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "备忘录"),
        App.h("div", { class: "sub" },
          "markdown 笔记 + 分组收纳；Ctrl+Alt+M 全局呼出快速捕捉弹窗，内容经 WebDAV 云备份（设置 → 备份与恢复）"),
      ));

      /* v5.1b：页面显隐由 .page/.page.active 类控制，全高布局用 page-flex
         类（禁止在 .page 元素上设内联 display，会覆盖显隐切换） */
      el.classList.add("page-flex");
      const layout = App.h("div", {
        style: {
          display: "flex", gap: "12px", flex: "1", minHeight: "0",
          alignItems: "stretch",
        },
      },
      );
      const side = App.h("div", {
        class: "card", style: {
          width: "220px", flex: "none", padding: "10px",
          display: "flex", flexDirection: "column", minHeight: "0",
        },
      },
        App.h("div", { class: "card-title", style: { marginBottom: "6px" } }, "分组"),
        App.h("div", { style: { flex: "1", overflowY: "auto", minHeight: "0" } }),
      );
      refs.groups = side.children[1];
      refs.body = App.h("div", {
        style: { flex: "1", minWidth: "0", display: "flex", flexDirection: "column", minHeight: "0" },
      });
      layout.appendChild(side);
      layout.appendChild(refs.body);
      el.appendChild(layout);

      keyHandler = (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n" && !state.cur) {
          e.preventDefault();
          state.cur = { id: 0, title: "", content: "", group_id: state.curGroup || null, pinned: false };
          state.curId = 0;
          state.preview = false;
          renderBody();
        }
      };
      el.onkeydown = keyHandler;

      await refresh();
    },
  });

  /* 弹窗「打开完整备忘录」入口：带搜索词跳转 */
  App.setMemoSearch = function (q) {
    setTimeout(() => {
      state.query = String(q || "");
      state.curGroup = "";
      if (refs.search) refs.search.value = state.query;
      if (state.cur) { state.cur = null; state.curId = 0; }
      refresh();
    }, 80);
  };
})();
