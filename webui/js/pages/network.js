/* 网络发现/文件共享页：功能开关与状态检测（应用设置需管理员权限，会弹 UAC）。 */
(function () {
  "use strict";

  let refs = {};
  let busy = false;

  const stateText = (s) => (s === null || s === undefined ? "未知" : s ? "已开启" : "已关闭");

  /* ---------------- 渲染 ---------------- */
  function setStateHint(msg, kind) {
    refs.stateHint.innerHTML = "";
    if (kind === "error") {
      refs.stateHint.appendChild(App.h("span", { class: "tag danger" }, msg));
    } else {
      refs.stateHint.appendChild(App.h("span", { class: "hint" }, msg));
    }
  }

  function featureRow(label, state, reason) {
    const dotCls = state === null || state === undefined ? "" : state ? "on" : "off";
    const tagCls = state === true ? "ok" : state === null || state === undefined ? "warn" : "";
    return App.h("div", { class: "list-item" },
      App.h("span", { class: "dot " + dotCls }),
      App.h("span", { class: "li-main" },
        App.h("div", { class: "li-title" }, label),
        reason ? App.h("div", { class: "li-sub" }, reason) : null,
      ),
      App.h("span", { class: "tag " + tagCls }, stateText(state)),
    );
  }

  function renderStatus(status) {
    const discovery = status.discovery;
    const sharing = status.sharing;
    const details = status.details || {};

    if (discovery !== null && discovery !== undefined) refs.discoveryTgl.checked = !!discovery;
    if (sharing !== null && sharing !== undefined) refs.sharingTgl.checked = !!sharing;

    let dReason = "";
    if (discovery === false) {
      const reasons = [];
      if (details.is_private === false) reasons.push("当前网络为公用，需设为专用");
      if (details.discovery_rules === false) reasons.push("防火墙规则未启用");
      if (details.fdrespub === false) reasons.push("发现服务未运行");
      dReason = reasons.join("、");
    }
    let sReason = "";
    if (sharing === false) {
      const reasons = [];
      if (details.is_private === false) reasons.push("当前网络为公用，需设为专用");
      if (details.sharing_rules === false) reasons.push("防火墙规则未启用");
      sReason = reasons.join("、");
    }

    refs.statusList.innerHTML = "";
    refs.statusList.appendChild(featureRow("网络发现", discovery, dReason));
    refs.statusList.appendChild(featureRow("文件共享", sharing, sReason));
    const profiles = status.profiles || [];
    refs.statusList.appendChild(App.h("div", { class: "list-item" },
      App.h("span", { class: "dot accent" }),
      App.h("span", { class: "li-main" }, App.h("div", { class: "li-title" }, "当前网络")),
      App.h("span", { class: "tag" }, profiles.length ? profiles.join("、") : "未知"),
    ));

    if (discovery === null && sharing === null) {
      setStateHint("无法检测状态，请确认已以管理员身份运行。", "error");
    } else {
      setStateHint("状态检测完成。");
    }
  }

  function setBusy(b) {
    busy = b;
    refs.discoveryTgl.disabled = b;
    refs.sharingTgl.disabled = b;
    refs.discoveryLabel.style.opacity = b ? "0.5" : "";
    refs.sharingLabel.style.opacity = b ? "0.5" : "";
    if (refs.recheckBtn) refs.recheckBtn.disabled = b;   // 检测期间防重复触发
  }

  /* ---------------- 动作 ---------------- */
  async function refresh() {
    setBusy(true);
    setStateHint("正在检测状态...");
    const r = await App.tryCall("network_status");
    if (!r.ok) {
      setBusy(false);
      setStateHint("操作出错：" + r.err, "error");
      return;
    }
    renderStatus(r.data);
    setBusy(false);
  }

  async function apply(which, checked, checkbox) {
    if (busy) { checkbox.checked = !checked; return; }
    setBusy(true);
    setStateHint("正在应用设置…（普通权限运行时会弹一次 UAC 授权，可能需要几秒）");
    const r = await App.tryCall("network_set", which, checked);
    if (!r.ok) setStateHint("设置失败：" + r.err, "error");
    else setStateHint("设置已应用，正在检测状态...");
    await refresh();
  }

  /* 管理员状态：仅显示状态标签；提权重启按钮统一收在右下角状态栏（v5.4） */
  async function renderAdmin() {
    refs.adminBox.innerHTML = "";
    const r = await App.tryCall("app_info");
    const admin = !!(r.ok && r.data && r.data.admin);
    if (admin) {
      refs.adminBox.appendChild(App.h("span", { class: "tag ok" }, "已以管理员身份运行"));
      refs.adminBox.appendChild(App.h("span", { class: "hint" },
        "开关功能直接执行，不会弹出 UAC 授权框"));
    } else {
      refs.adminBox.appendChild(App.h("span", { class: "tag warn" }, "非管理员权限"));
      refs.adminBox.appendChild(App.h("span", { class: "hint" },
        "每次开关都会弹出 UAC；点右下角「提权重启」可一次性提权"));
    }
  }

  /* ---------------- 页面注册 ---------------- */
  App.registerPage({
    id: "network",
    title: "网络发现/文件共享",
    icon: "wifi",
    group: "互联与协作",

    async mount(el) {
      refs = {};
      refs.discoveryTgl = App.h("input", {
        type: "checkbox",
        onchange: (e) => apply("discovery", e.target.checked, e.target),
      });
      refs.sharingTgl = App.h("input", {
        type: "checkbox",
        onchange: (e) => apply("sharing", e.target.checked, e.target),
      });
      refs.discoveryLabel = App.h("label", { class: "switch" },
        refs.discoveryTgl, App.h("span", { class: "track" }),
        "网络发现（使此电脑可被其他设备发现）");
      refs.sharingLabel = App.h("label", { class: "switch" },
        refs.sharingTgl, App.h("span", { class: "track" }),
        "文件共享（允许其他设备访问本机共享文件夹）");
      refs.statusList = App.h("div", { class: "list" });
      refs.stateHint = App.h("div", { class: "row" }, App.h("span", { class: "hint" }, "正在检测状态..."));

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "网络发现/文件共享"),
        App.h("div", { class: "sub" }, "一键开关网络发现与文件共享，让资源管理器的「网络」能看到其他设备"),
      ));

      /* v5.3：三张卡各自只有一两行控件，单列纵向堆叠在宽屏下会留下大片空白，
         改为自适应两列（说明卡跨整行） */
      el.appendChild(App.h("div", { class: "card-cols" },
        App.h("div", { class: "card" },
          App.h("div", { class: "card-title" }, "网络功能开关"),
          refs.adminBox = App.h("div", { class: "row", style: { marginBottom: "4px" } }),
          App.h("div", { class: "row", style: { flexDirection: "column", alignItems: "flex-start", gap: "12px" } },
            refs.discoveryLabel,
            refs.sharingLabel,
          ),
        ),
        App.h("div", { class: "card" },
          App.h("div", { class: "card-title" }, "当前状态检测"),
          refs.statusList,
          App.h("div", { class: "sep" }),
          App.h("div", { class: "row" },
            refs.stateHint,
            App.h("span", { class: "grow" }),
            refs.recheckBtn = App.h("button", {
              class: "btn sm",
              html: App.icon("refresh", 12) + "<span>重新检测</span>",
              onclick: refresh,
            }),
          ),
        ),
        App.h("div", { class: "card span" },
          App.h("div", { class: "hint" },
            "说明：开启“网络发现”会同时把当前网络设为“专用”、启用功能发现服务并开放防火墙规则，",
            "这样资源管理器的“网络”里才能看到其他设备。以上操作需要管理员权限；",
            "点状态栏右下角「提权重启」以管理员身份运行后，开关将直接执行，不再弹出授权框。",
          ),
        ),
      ));

      await renderAdmin();
      await refresh();
    },

    show() { renderAdmin(); refresh(); },
  });
})();
