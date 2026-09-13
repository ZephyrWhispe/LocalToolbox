/* 键鼠共享页：被控端（监听/允许被控制）+ 控制端（连接目标/热键控制）+ 日志。 */
(function () {
  "use strict";

  const state = {
    target: { running: false, controlled: false, controller_name: "", port: 41892, allow: true },
    controller: { connected: false, active: false, peer: "" },
    targets: [],
    edges: { left: "", right: "", top: "", bottom: "" },
    options: { edge_switch: true, edge_margin: 4, lock_input: false, allow: true },
    devices: [],
    fw: true, /* 放行防火墙端口（仅前端记忆，同旧页默认勾选） */
  };
  let refs = {};
  let log = null; /* 由 App.makeLog 创建，统一日志实现 */

  const EDGE_LABELS = { right: "右侧", left: "左侧", top: "上方", bottom: "下方" };

  /* ---------------- 渲染 ---------------- */
  function renderTarget() {
    refs.tListen.disabled = state.target.running;
    refs.tStop.disabled = !state.target.running;
    refs.allowTgl.checked = !!state.target.allow;
    refs.lockTgl.checked = !!state.options.lock_input;
    if (state.target.running) {
      refs.tState.textContent = state.target.controlled
        ? `正在被 ${state.target.controller_name} 控制`
        : "监听中，等待控制端...";
    } else {
      refs.tState.textContent = "未监听";
    }
  }

  function renderController() {
    const c = state.controller;
    refs.connect.disabled = c.connected;
    refs.disconnect.disabled = !c.connected;
    refs.control.disabled = !c.connected;
    refs.ipInput.disabled = c.connected;
    refs.portInput.disabled = c.connected;
    refs.devSelect.disabled = c.connected || !state.devices.length;
    refs.control.textContent = c.active ? "释放控制（Ctrl+Alt+K）" : "开始控制（Ctrl+Alt+K）";
    refs.cState.textContent = c.connected
      ? `已连接 ${c.peer}` + (c.active ? "，控制中（Ctrl+Alt+K 释放）" : "，按 Ctrl+Alt+K 开始")
      : "未连接";
  }

  function renderDevices() {
    const prev = refs.devSelect.value;
    refs.devSelect.innerHTML = "";
    if (!state.devices.length) {
      refs.devSelect.appendChild(App.h("option", { value: "" }, "（未发现可被控设备）"));
    } else {
      for (const d of state.devices) {
        refs.devSelect.appendChild(
          App.h("option", { value: `${d.ip}|${d.km_port}` }, `${d.name}  [${d.ip}]`),
        );
      }
    }
    /* 设备列表未变化时保留用户选择 */
    const has = [...refs.devSelect.options].some((o) => o.value === prev);
    if (has) refs.devSelect.value = prev;
  }

  function renderAll() {
    renderTarget();
    renderController();
    renderDevices();
    renderMulti();
  }

  /* T-03/04：多目标列表 + 边缘邻居渲染 */
  function renderMulti() {
    if (!refs.targetBox || !refs.edgeBox) return;
    refs.edgeTgl.checked = !!state.options.edge_switch;
    if (document.activeElement !== refs.marginInput) {
      refs.marginInput.value = state.options.edge_margin;
    }
    refs.targetBox.innerHTML = "";
    if (!state.targets.length) {
      refs.targetBox.appendChild(App.h("div", { class: "hint" }, "暂无已挂载目标（多目标即「一台控制端挂多台被控端，鼠标滑屏幕边缘切换」）。"));
    }
    for (const t of state.targets) {
      refs.targetBox.appendChild(App.h("div", { class: "list-item" },
        App.h("span", {
          class: "dot" + (t.active ? " accent" : ""),
          style: { flex: "none" },
        }),
        App.h("span", { class: "li-main" },
          App.h("div", { class: "li-title" },
            `${t.peer}${t.active ? "（当前）" : ""}　[${t.ip}]`),
          App.h("div", { class: "li-sub mono" },
            `DPI ${t.dpi} · 屏 ${t.screen && t.screen.w}x${t.screen && t.screen.h}`),
        ),
        App.h("span", { class: "li-actions", style: { flex: "none" } },
          App.h("button", {
            class: "btn sm" + (t.active ? " primary" : ""),
            disabled: t.active, onclick: async () => {
              const r = await App.tryCall("km_set_active", t.ip);
              if (!r.ok) App.toast(r.err, "error");
              await refresh();
            },
          }, "切换"),
          App.h("button", {
            class: "btn sm", onclick: async () => {
              const r = await App.tryCall("km_detach", t.ip);
              if (!r.ok) App.toast(r.err, "error");
              await refresh();
            },
          }, "断开"),
        ),
      ));
    }
    /* 边缘邻居配置：每个方向一行下拉 */
    refs.edgeBox.innerHTML = "";
    for (const dir of Object.keys(EDGE_LABELS)) {
      const sel = App.h("select", { class: "input", style: { minWidth: "180px" } });
      sel.appendChild(App.h("option", { value: "" }, "（无，不启用）"));
      for (const d of state.devices) {
        const v = `${d.ip}|${d.km_port || 41892}`;
        const opt = App.h("option", { value: v },
          `${d.name}  [${d.ip}]${state.edges[dir] === d.ip ? "　✓" : ""}`);
        sel.appendChild(opt);
        if (state.edges[dir] === d.ip) sel.value = v;
      }
      if (!sel.value) sel.value = "";
      sel.addEventListener("change", async () => {
        const [ip, port] = sel.value ? sel.value.split("|") : ["", ""];
        const r = await App.tryCall("km_edge_set", dir, ip);
        if (!r.ok) App.toast(r.err, "error");
        await refresh();
      });
      const row = App.h("div", { class: "row" },
        App.h("span", { class: "field-label" },
          `${EDGE_LABELS[dir]}边缘滑出 →`),
        sel,
      );
      refs.edgeBox.appendChild(row);
    }
  }

  function applyState(s) {
    if (s.target) state.target = s.target;
    if (s.controller) state.controller = s.controller;
    if (s.devices) state.devices = s.devices;
    if (s.options) state.options = s.options;
    // 后端快照把多目标列表嵌在 controller.targets（顶层无 targets 键）
    if (s.controller && Array.isArray(s.controller.targets)) {
      state.targets = s.controller.targets;
    }
    if (s.edges) state.edges = s.edges;
    renderAll();
  }

  /* ---------------- 动作 ---------------- */
  async function refresh() {
    const r = await App.tryCall("km_get_state");
    if (r.ok) applyState(r.data);
  }

  async function doStartListen() {
    const r = await App.tryCall("km_start_listen", state.fw);
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refresh();
  }

  async function doStopListen() {
    const r = await App.tryCall("km_stop_listen");
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refresh();
  }

  async function doAllow(e) {
    const r = await App.tryCall("km_set_allow", e.target.checked);
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refresh();
  }

  async function doLockInput(e) {
    const r = await App.tryCall("cfg_set", "km_lock_input", e.target.checked);
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refresh();
  }

  async function doEdgeSwitch(e) {
    const r = await App.tryCall("cfg_set", "km_edge_switch", e.target.checked);
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refresh();
  }

  async function doEdgeMargin(e) {
    const v = parseInt(e.target.value, 10);
    if (!(v >= 1 && v <= 50)) { App.toast("边缘宽度需为 1–50 像素", "warn"); await refresh(); return; }
    const r = await App.tryCall("cfg_set", "km_edge_margin", v);
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refresh();
  }

  function resolveTarget() {
    /* 同旧页：已发现设备优先，其次手动 IP */
    const v = refs.devSelect.value;
    if (v) {
      const i = v.lastIndexOf("|");
      return { ip: v.slice(0, i), port: parseInt(v.slice(i + 1), 10) };
    }
    const ip = refs.ipInput.value.trim();
    if (ip) return { ip, port: parseInt(refs.portInput.value, 10) || 41892 };
    return null;
  }

  async function doConnect() {
    const t = resolveTarget();
    if (!t) { App.toast("请选择设备或填写被控端 IP。", "warn"); return; }
    const r = await App.tryCall("km_connect", t.ip, t.port);
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    await refresh();
  }

  async function doDisconnect() {
    const r = await App.tryCall("km_disconnect");
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refresh();
  }

  async function doToggleControl() {
    const r = await App.tryCall("km_toggle_control");
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refresh();
  }

  /* ---------------- 页面注册 ---------------- */
  App.registerPage({
    id: "km",
    title: "键鼠共享",
    icon: "keyboard",
    group: "互联与协作",

    async mount(el) {
      el.innerHTML = "";
      refs = {};

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "键鼠共享"),
        App.h("div", { class: "sub" },
          "把本机键盘鼠标实时延伸到局域网内的另一台电脑；被控端点「启动监听」，控制端连接后按 Ctrl+Alt+K 开始控制（再按一次释放）。" +
          "注意：控制期间本机键鼠被拦截；无法作用于管理员权限弹窗（UAC）等系统安全界面。"),
      ));

      /* 被控端 */
      refs.allowTgl = App.h("input", { type: "checkbox", onchange: doAllow });
      refs.lockTgl = App.h("input", { type: "checkbox", onchange: doLockInput });
      refs.fwTgl = App.h("input", {
        type: "checkbox",
        onchange: (e) => { state.fw = e.target.checked; },
      });
      refs.tState = App.h("span", { class: "hint ml-auto" }, "未监听");
      const paneServer = App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "被控端（让别的电脑控制本机）"),
        App.h("div", { class: "row" },
          refs.tListen = App.h("button", {
            class: "btn primary", onclick: doStartListen,
            html: App.icon("wifi", 14) + "<span>启动监听</span>",
          }),
          refs.tStop = App.h("button", { class: "btn", onclick: doStopListen }, "停止监听"),
          App.h("label", { class: "switch" },
            refs.allowTgl, App.h("span", { class: "track" }), "允许被控制"),
          App.h("label", { class: "switch" },
            refs.lockTgl, App.h("span", { class: "track" }), "被控时锁定本机键鼠"),
          App.h("label", { class: "switch" },
            refs.fwTgl, App.h("span", { class: "track" }), "放行防火墙端口"),
          refs.tState,
        ),
      );

      /* 控制端 */
      refs.devSelect = App.h("select", { class: "input", style: { minWidth: "220px" } });
      refs.ipInput = App.h("input", { class: "input", placeholder: "192.168.1.23", style: { width: "150px" } });
      refs.portInput = App.h("input", { class: "input", type: "number", min: "1", max: "65535", value: 41892, style: { width: "90px" } });
      refs.cState = App.h("span", { class: "hint ml-auto" }, "未连接");
      const paneClient = App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "控制端（用本机键鼠控制其他电脑）"),
        App.h("div", { class: "row" },
          App.h("span", { class: "hint" }, "被控设备："), refs.devSelect,
          App.h("span", { class: "hint" }, "或手动 IP："), refs.ipInput,
          App.h("span", { class: "hint" }, "端口："), refs.portInput,
          refs.connect = App.h("button", { class: "btn primary", onclick: doConnect }, "连接"),
          refs.disconnect = App.h("button", { class: "btn", onclick: doDisconnect }, "断开"),
        ),
        App.h("div", { class: "row" },
          refs.control = App.h("button", { class: "btn", onclick: doToggleControl }, "开始控制（Ctrl+Alt+K）"),
          refs.cState,
        ),
      );

      /* 多设备与边缘穿越（T-03/04/05，v4.7 设置持久化 + 双向切回） */
      refs.targetBox = App.h("div", { class: "list" });
      refs.edgeBox = App.h("div", {});
      refs.edgeTgl = App.h("input", { type: "checkbox", onchange: doEdgeSwitch });
      refs.marginInput = App.h("input", {
        class: "input", type: "number", min: "1", max: "50",
        style: { width: "80px" }, onchange: doEdgeMargin,
      });
      const paneEdge = App.h("div", { class: "card" },
        App.h("div", { class: "card-title" },
          "多设备与边缘穿越",
          App.h("span", { class: "hint", style: { marginLeft: "8px" } },
            "鼠标滑到本机屏幕边缘切换到该方向设备；控制中把远端光标滑回进入时的边缘即可切回本机")),
        App.h("div", { class: "row" },
          App.h("label", { class: "switch" },
            refs.edgeTgl, App.h("span", { class: "track" }), "启用边缘穿越"),
          App.h("span", { class: "hint" }, "边缘宽度："),
          refs.marginInput,
          App.h("span", { class: "hint" }, "像素"),
        ),
        refs.targetBox,
        App.h("div", { style: { marginTop: "10px" } },
          refs.edgeBox),
      );

      /* v5.2 P4：页内标签「被控端 | 控制端 | 边缘与多设备」 */
      const nav = App.subnav([
        { label: "被控端", el: paneServer },
        { label: "控制端", el: paneClient },
        { label: "边缘与多设备", el: paneEdge },
      ]);
      el.appendChild(nav);
      nav.panes.forEach((p) => el.appendChild(p));

      refs.log = App.h("div", { class: "log-box", style: { maxHeight: "110px" } }, "键鼠共享日志...");
      log = App.makeLog(refs.log);
      el.appendChild(refs.log);

      /* 事件订阅 */
      App.on("km_state", applyState);
      App.on("km_log", log);

      refs.fwTgl.checked = true;
      await refresh();
    },
  });
})();
