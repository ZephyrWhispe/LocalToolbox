/* 右键菜单页：资源管理器右键集成（安装/卸载/状态）。 */
(function () {
  "use strict";

  let refs = {};

  function render(verbs) {
    refs.statusList.innerHTML = "";
    if (!verbs || !verbs.length) {
      refs.statusList.appendChild(App.h("div", { class: "empty" },
        verbs === null ? "状态读取失败，请重试" : "未获取到状态"));
      return;
    }
    for (const v of verbs) {
      refs.statusList.appendChild(
        App.h("div", { class: "list-item" },
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, v.text),
          ),
          v.installed
            ? App.h("span", { class: "tag ok" }, "已安装")
            : App.h("span", { class: "tag" }, "未安装"),
        ),
      );
    }
  }

  async function refresh() {
    let verbs = null;
    try { verbs = await App.guardedCall("shell_status"); }
    catch (e) { verbs = null; }   // guardedCall 失败已 toast，此处渲染错误空态
    render(verbs);
  }

  App.registerPage({
    id: "shellmenu",
    title: "右键菜单",
    icon: "cursor",
    group: "工具与增效",

    async mount(el) {
      refs = {};
      refs.statusList = App.h("div", { class: "list" });

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "右键菜单集成"),
        App.h("div", { class: "sub" }, "在资源管理器中右键即可快速调用 LocalToolbox"),
      ));

      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "hint" },
          "写入当前用户注册表（HKCU），无需管理员权限，安装后立即生效。", App.h("br"),
          "• 右键文件夹 →「用 LocalToolbox 共享(SMB)」/「发布(WebDAV)」→ 自动打开对应页面并预填路径", App.h("br"),
          "• 右键任意文件 →「复制到局域网剪贴板同步」→ 文本内容（≤2MB）复制进剪贴板，同步开启时自动发往各设备", App.h("br"),
          "Windows 11 请使用「显示更多选项」（或按住 Shift 右键）看到这些菜单项。",
        ),
      ));

      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "安装状态"),
        refs.statusList,
        App.h("div", { class: "sep" }),
        App.h("div", { class: "row" },
          refs.installBtn = App.h("button", {
            class: "btn primary",
            onclick: async () => {
              if (refs.busy) return;
              refs.busy = true;
              refs.installBtn.disabled = refs.removeBtn.disabled = true;  // 写注册表防连点
              try {
                const r = await App.tryCall("shell_install");
                if (!r.ok) { App.toast(r.err, "error", 6000); }
                else { App.toast(`已安装 ${r.data} 个右键菜单项，立即生效`, "ok"); }
              } finally {
                refs.busy = false;
                refs.installBtn.disabled = refs.removeBtn.disabled = false;
              }
              refresh();
            },
          }, "安装右键菜单"),
          refs.removeBtn = App.h("button", {
            class: "btn danger",
            onclick: async () => {
              if (refs.busy) return;
              if (!(await App.confirm("卸载右键菜单", "确定卸载全部右键菜单项？"))) return;
              refs.busy = true;
              refs.installBtn.disabled = refs.removeBtn.disabled = true;
              try {
                const r = await App.tryCall("shell_remove");
                if (!r.ok) { App.toast(r.err, "error", 6000); }
                else { App.toast("右键菜单已卸载", "ok"); }
              } finally {
                refs.busy = false;
                refs.installBtn.disabled = refs.removeBtn.disabled = false;
              }
              refresh();
            },
          }, "卸载右键菜单"),
        ),
      ));

      await refresh();
    },

    show() { refresh(); },
  });
})();
