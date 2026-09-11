/* 共享文件夹（SMB）页：创建共享 / 共享列表 / 取消共享。 */
(function () {
  "use strict";

  const state = { shares: [], selected: "" };
  let refs = {};

  /* 等价旧页 prefill：填路径，共享名为空时取文件夹名 */
  function prefill(path) {
    if (!path) return;
    refs.path.value = path;
    if (!refs.name.value.trim()) {
      const base = String(path).replace(/[\\/]+$/, "").split(/[\\/]/).pop();
      refs.name.value = base || "share";
    }
  }

  function renderList() {
    refs.list.innerHTML = "";
    if (!state.shares.length) {
      refs.list.appendChild(App.h("div", { class: "empty" }, "暂无共享"));
      return;
    }
    for (const s of state.shares) {
      const sel = state.selected === s.name;
      refs.list.appendChild(
        App.h("div", {
          class: "list-item click",
          style: sel ? { background: "var(--hover)", borderColor: "var(--border2)" } : null,
          title: "点击选中该共享",
          onclick: () => { state.selected = s.name; renderList(); },
        },
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, s.name),
            App.h("div", { class: "li-sub mono" }, s.path),
            s.description ? App.h("div", { class: "li-sub" }, s.description) : null,
          ),
          App.h("button", {
            class: "icon-btn", title: "取消选中共享",
            html: App.icon("trash", 13),
            onclick: (ev) => { ev.stopPropagation(); delShare(s.name); },
          }),
        ),
      );
    }
  }

  async function refresh() {
    if (refs.refreshBtn) refs.refreshBtn.disabled = true;  // PowerShell 枚举需 1-3s，先禁用防重入
    try {
      const r = await App.tryCall("share_list");
      if (!r.ok) { App.toast(r.err, "error", 6000); return; }
      state.shares = r.data;
      if (!state.shares.some((s) => s.name === state.selected)) state.selected = "";
      refs.delBtn.disabled = !state.selected;
      renderList();
    } finally {
      if (refs.refreshBtn) refs.refreshBtn.disabled = false;
    }
  }

  let creating = false;
  async function createShare() {
    if (creating) return;   // net share 走 UAC 提权，防连点重复触发
    creating = true;
    try {
      const name = refs.name.value.trim();
      const path = refs.path.value.trim();
      const r = await App.tryCall("share_create", name, path);
      if (!r.ok) App.toast(r.err, "error", 6000);
      else App.toast(r.data, "ok");
      await refresh();
    } finally {
      creating = false;
    }
  }

  async function delShare(name) {
    if (!name) { App.toast("请先选择要取消的共享。"); return; }
    if (!(await App.confirm("确认", `确定取消共享「${name}」吗？`))) return;
    const r = await App.tryCall("share_delete", name);
    if (!r.ok) App.toast(r.err, "error", 6000);
    else App.toast(r.data, "ok");
    await refresh();
  }

  App.registerPage({
    id: "share",
    title: "共享文件夹",
    icon: "share",
    group: "共享服务",

    async mount(el) {
      refs = {};
      refs.path = App.h("input", {
        class: "input", placeholder: "请选择要共享的本地文件夹",
        style: { flex: "1", minWidth: "220px" },
      });
      refs.name = App.h("input", {
        class: "input", placeholder: "例如：MyShare",
        style: { flex: "1", minWidth: "180px" },
      });
      refs.list = App.h("div", { class: "list" });
      refs.delBtn = App.h("button", {
        class: "btn danger", disabled: true,
        onclick: () => delShare(state.selected),
      }, "取消选中共享");

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "共享文件夹"),
        App.h("div", { class: "sub" }, "把本机文件夹发布为 Windows 共享（SMB），局域网其他设备可在「网络」中访问"),
      ));

      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "创建共享"),
        App.row(
          App.h("span", { class: "field-label" }, "文件夹："),
          refs.path,
          App.h("button", {
            class: "btn",
            onclick: async () => {
              const r = await App.tryCall("share_pick_folder");
              if (!r.ok) { App.toast(r.err, "error", 6000); return; }
              if (r.data) prefill(r.data);
            },
          }, "浏览..."),
        ),
        App.row(
          App.h("span", { class: "field-label" }, "共享名："),
          refs.name,
          App.h("button", { class: "btn primary", onclick: createShare }, "创建共享"),
        ),
        App.h("div", { class: "hint", style: { marginTop: "8px" } },
          "创建 / 取消共享需要管理员授权（系统将弹出 UAC 确认框）。创建成功后，其他设备可在「网络」页或文件管理器地址栏输入 \\\\本机IP\\共享名 访问。"),
      ));

      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "card-title" },
          "共享列表",
          refs.refreshBtn = App.h("button", {
            class: "btn sm ml-auto",
            html: App.icon("refresh", 13) + "<span>刷新共享列表</span>",
            onclick: refresh,
          }),
        ),
        refs.list,
        App.h("div", { class: "sep" }),
        App.row(refs.delBtn),
      ));

      await refresh();
    },

    show() { refresh(); },
    async onCli(path) { prefill(path); },
  });
})();
