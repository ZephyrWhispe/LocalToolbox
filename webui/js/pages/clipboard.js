/* 剪贴板同步页：启动/停止、历史（搜索/脱敏/回贴）、手动设备、日志。 */
(function () {
  "use strict";

  const state = {
    running: false, send: true, recv: true,
    history: [], devices: [], search: "",
    deviceName: "", syncPort: 41891, discoveryPort: 41890,
    desensitize: true,
  };
  let refs = {};
  let log = null; /* 由 App.makeLog 创建，统一日志实现 */

  function maskPreview(text) {
    if (!state.desensitize) return { shown: text, hits: [] };
    return App.maskSensitive(text);
  }

  /* ---------------- 渲染 ---------------- */
  function renderControls() {
    refs.start.disabled = state.running;
    refs.stop.disabled = !state.running;
    refs.stateLine.replaceChildren(
      ...(state.running
        ? [
            App.statusTag("同步中", "ok", "check"),
            App.h("span", { class: "hint" },
              `本机 ${App.esc(state.deviceName)} · 接收端口 TCP ${state.syncPort} · 发现端口 UDP ${state.discoveryPort}`),
          ]
        : [
            App.statusTag("未启动"),
            App.h("span", { class: "hint" }, "启动后同一局域网内运行 LocalToolbox 的设备自动互相同步文本"),
          ]),
    );
  }

  function renderHistory() {
    const kw = state.search.trim().toLowerCase();
    const items = state.history.filter(
      (e) => !kw || (e.text || "").toLowerCase().includes(kw),
    );
    refs.histList.innerHTML = "";
    if (!items.length) {
      refs.histList.appendChild(App.h("div", { class: "empty" },
        kw ? "没有匹配的历史条目" : "暂无剪贴板历史\n复制内容、图片或文件后自动出现在这里"));
      return;
    }
    for (const e of items) {
      if (e.kind === "image") {
        refs.histList.appendChild(renderImageEntry(e));
        continue;
      }
      if (e.kind === "file") {
        refs.histList.appendChild(renderFileEntry(e));
        continue;
      }
      const encLost = !!e.enc_lost;   // 加密历史解密失败（密钥变更/失效）
      const rawText = e.text || "";
      const { shown, hits } = maskPreview(e.preview || rawText);
      const origin = e.device === "本机" ? "本机" : e.device;
      const viewFull = () => {
        App.modal({
          title: "历史条目全文",
          body: App.h("div", null,
            App.h("div", { class: "modal-body", style: { maxHeight: "50vh", overflow: "auto", whiteSpace: "pre-wrap", userSelect: "text" } },
              rawText || "（空）"),
            App.h("div", { class: "modal-btns" },
              App.h("button", {
                class: "btn sm primary",
                onclick: async () => {
                  await App.copyText(rawText);
                  App.toast("已复制全文", "ok");
                },
              }, "复制全文"),
            ),
          ),
          okText: "关闭",
        });
      };
      refs.histList.appendChild(
        App.h("div", {
          class: "list-item click",
          title: encLost ? "该条目无法解密（密钥已变更或失效），点击可删除" : "点击回贴到本机剪贴板",
          onclick: async () => {
            if (encLost) return;
            await App.guardedCall("clip_copy", e.hash);
            App.toast("已复制选中历史到剪贴板", "ok");
          },
        },
          App.h("span", { class: "mono", style: { color: "var(--faint)", flex: "none" } }, App.fmtTime(e.ts)),
          App.h("span", { class: "tag" + (e.device === "本机" ? "" : " accent"), style: { flex: "none" } }, origin),
          App.h("span", { class: "li-title", style: { userSelect: "text" } },
            encLost ? "（无法解密：历史加密密钥已变更或失效）" : shown),
          encLost ? App.h("span", { class: "tag danger", style: { flex: "none" } }, "密钥失效") : null,
          hits.length ? App.h("span", { class: "tag danger", style: { flex: "none" } }, hits.join("/")) : null,
          App.h("span", { class: "li-actions", style: { flex: "none" } },
            !encLost && rawText && (rawText.length > 60 || rawText.includes("\n"))
              ? App.h("button", {
                class: "icon-btn", title: "查看全文",
                html: App.icon("filetext", 13),
                onclick: async (ev) => { ev.stopPropagation(); viewFull(); },
              })
              : null,
            App.h("button", {
              class: "icon-btn", title: "删除该条",
              html: App.icon("trash", 13),
              onclick: async (ev) => {
                ev.stopPropagation();
                await App.guardedCall("clip_delete", e.hash);
                state.history = state.history.filter((x) => x.hash !== e.hash);
                renderHistory();
              },
            }),
          ),
        ),
      );
    }
  }

  function renderImageEntry(e) {
    const origin = e.device === "本机" ? "本机" : e.device;
    return App.h("div", {
      class: "list-item click",
      title: "点击回贴图片到本机剪贴板",
      onclick: async () => {
        await App.guardedCall(e.img_path ? "clip_copy_path" : "clip_copy",
          e.img_path || e.hash);
        App.toast("已复制图片到剪贴板", "ok");
      },
    },
      App.h("span", { class: "mono", style: { color: "var(--faint)", flex: "none" } }, App.fmtTime(e.ts)),
      App.h("span", { class: "tag accent", style: { flex: "none" } }, "图片"),
      App.h("span", { class: "li-main", style: { display: "flex", alignItems: "center", gap: "10px" } },
        e.img
          ? App.h("img", { src: e.img, style: { maxHeight: "56px", maxWidth: "160px", borderRadius: "4px", border: "1px solid var(--border)" } })
          : App.h("span", { class: "hint" }, "（图片文件缺失）"),
        App.h("span", { class: "hint" }, origin === "本机" ? "本机" : `来自 ${origin}`),
      ),
      App.h("span", { class: "li-actions", style: { flex: "none" } },
        App.h("button", {
          class: "icon-btn", title: "删除该条",
          html: App.icon("trash", 13),
          onclick: async (ev) => {
            ev.stopPropagation();
            await App.guardedCall("clip_delete", e.hash);
            state.history = state.history.filter((x) => x.hash !== e.hash);
            renderHistory();
          },
        }),
      ),
    );
  }

  function renderDevices() {
    refs.devList.innerHTML = "";
    if (!state.devices.length) {
      refs.devList.appendChild(App.h("div", { class: "empty" }, "暂无设备\n对方也需运行本软件；发现失败可在下方手动添加 IP"));
      return;
    }
    for (const d of state.devices) {
      refs.devList.appendChild(
        App.h("div", { class: "list-item" },
          App.h("span", { class: "dot " + (d.clip_port ? "on" : "off") }),
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, d.name),
            App.h("div", { class: "li-sub mono" }, d.ip + (d.clip_port ? " · TCP " + d.clip_port : " · 未开启剪贴板同步")),
          ),
          App.h("button", {
            class: "icon-btn", title: "复制 IP",
            html: App.icon("copy", 13),
            onclick: async () => { await App.copyText(d.ip); App.toast("已复制", "ok"); },
          }),
        ),
      );
    }
  }

  function renderFileEntry(e) {
    const origin = e.device === "本机" ? "本机" : e.device;
    const size = fmtSize(e.file_size);
    return App.h("div", {
      class: "list-item click",
      title: "点击回贴文件到本机剪贴板",
      onclick: async () => {
        if (e.missing) { App.toast("文件已丢失，无法回贴", "error"); return; }
        const r = await App.tryCall("clip_copy", e.hash);
        if (!r.ok) { App.toast(r.err, "error"); return; }
        App.toast("已复制文件到剪贴板，可直接粘贴到任意位置", "ok");
      },
    },
      App.h("span", { class: "mono", style: { color: "var(--faint)", flex: "none" } }, App.fmtTime(e.ts)),
      App.h("span", { class: "tag accent", style: { flex: "none" } }, "文件"),
      App.h("span", { class: "li-main" },
        App.h("div", { class: "li-title", style: { userSelect: "text" } },
          App.h("span", { html: App.icon("filetext", 14) }),
          " " + App.esc(e.file_name || "未命名")),
        App.h("div", { class: "li-sub mono" },
          (e.missing ? "（文件已丢失）  " : "") + size + " · " + (origin === "本机" ? "本机" : `来自 ${origin}`)),
      ),
      App.h("span", { class: "li-actions", style: { flex: "none" } },
        e.missing ? null : App.h("button", {
          class: "icon-btn", title: "在资源管理器中显示",
          html: App.icon("folder", 13),
          onclick: async (ev) => {
            ev.stopPropagation();
            const r = await App.tryCall("clip_reveal", e.file_path);
            if (!r.ok) App.toast(r.err, "error");
          },
        }),
        App.h("button", {
          class: "icon-btn", title: "删除该条",
          html: App.icon("trash", 13),
          onclick: async (ev) => {
            ev.stopPropagation();
            await App.guardedCall("clip_delete", e.hash);
            state.history = state.history.filter((x) => x.hash !== e.hash);
            renderHistory();
          },
        }),
      ),
    );
  }

  function fmtSize(n) {
    n = Number(n) || 0;
    if (n >= 1 << 30) return (n / (1 << 30)).toFixed(2) + " GB";
    if (n >= 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB";
    if (n >= 1 << 10) return (n / (1 << 10)).toFixed(0) + " KB";
    return n + " B";
  }

  /* ---------------- 动作 ---------------- */
  async function startSync() {
    await App.guardedCall("clip_start", state.send, state.recv, true);
    state.running = true;
    renderControls();
    log("剪贴板同步已启动。");
  }

  async function stopSync() {
    await App.guardedCall("clip_stop");
    state.running = false;
    renderControls();
    log("剪贴板同步已停止。");
  }

  let addingManual = false;
  /* v5.2 P4：手动添加设备改弹窗入口（不再常驻表单） */
  async function addManual() {
    if (addingManual) return;
    const vals = await App.modal({
      title: "手动添加设备",
      body: "对方设备不在发现列表时，可手动输入其 IP 直连同步。",
      inputs: [
        { label: "设备 IP", placeholder: "192.168.1.23" },
        { label: "同步端口", type: "number", value: "41891" },
      ],
      okText: "添加",
    });
    if (!vals) return;
    const ip = (vals[0] || "").trim();
    if (!ip) { App.toast("请输入设备 IP", "error"); return; }
    // IP 逐段校验：每段 0-255 且拒绝前导零
    const segs = ip.split(".");
    const ipOk = segs.length === 4 && segs.every((s) => {
      if (!/^\d{1,3}$/.test(s)) return false;
      if (s.length > 1 && s[0] === "0") return false;
      const n = parseInt(s, 10);
      return n >= 0 && n <= 255;
    });
    if (!ipOk) { App.toast("请输入合法的 IPv4 地址", "error"); return; }
    const port = parseInt(vals[1], 10) || 41891;
    if (port < 1 || port > 65535) { App.toast("端口需在 1-65535 之间", "error"); return; }
    addingManual = true;
    try {
      const r = await App.tryCall("clip_add_manual", ip, port);
      if (!r.ok) { App.toast(r.err, "error"); return; }
      log(`已添加手动设备 ${ip}:${port}`);
      App.toast(`已添加设备 ${ip}:${port}`, "ok");
    } finally {
      addingManual = false;
    }
    refresh();
  }

  async function handleCliFile(path) {
    const r = await App.tryCall("clip_copy_file", path);
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast(`已复制「${path.split("\\").pop()}」的内容到剪贴板` +
      (state.running ? "，并推送到各设备" : "（同步未启动，仅本地）"), "ok", 5000);
  }

  async function refresh() {
    const s = await App.guardedCall("clip_get_state");
    Object.assign(state, {
      running: s.running, send: s.send, recv: s.recv,
      history: s.history, devices: s.devices,
      deviceName: s.device_name, syncPort: s.sync_port,
      discoveryPort: s.discovery_port, desensitize: s.desensitize,
    });
    refs.sendTgl.checked = s.send;
    refs.recvTgl.checked = s.recv;
    renderControls();
    renderHistory();
    renderDevices();
  }

  /* ---------------- 页面注册 ---------------- */
  App.registerPage({
    id: "clipboard",
    title: "剪贴板同步",
    icon: "clipboard",
    group: "互联与协作",

    async mount(el) {
      el.innerHTML = "";
      refs.sendTgl = App.h("input", { type: "checkbox", onchange: (e) => { state.send = e.target.checked; } });
      refs.recvTgl = App.h("input", { type: "checkbox", onchange: (e) => { state.recv = e.target.checked; } });
      refs.stateLine = App.h("div", { class: "row" });
      refs.devList = App.h("div", { class: "list" });
      refs.histList = App.h("div", { class: "list" });
      refs.search = App.h("input", {
        class: "input grow-in", placeholder: "搜索历史…",
        oninput: (e) => { state.search = e.target.value; renderHistory(); },
      });
      refs.log = App.h("div", { class: "log-box", style: { maxHeight: "110px" } }, "同步日志…");
      log = App.makeLog(refs.log);

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "剪贴板同步"),
        App.h("div", { class: "sub" }, "本机 Win+V 式历史 + 局域网设备实时互相同步（文本与图片，单个文件 ≤32MB）"),
      ));

      refs.start = App.h("button", {
        class: "btn primary", onclick: startSync,
        html: App.icon("send", 14) + "<span>启动同步</span>",
      });
      refs.stop = App.h("button", { class: "btn", onclick: stopSync }, "停止");
      /* v5.2 P4：控制与设备合并为一张状态卡；手动添加设备改弹窗入口 */
      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "同步控制"),
        App.row(
          refs.start,
          refs.stop,
          App.h("label", { class: "switch" }, refs.sendTgl, App.h("span", { class: "track" }), "发送到其他设备"),
          App.h("label", { class: "switch" }, refs.recvTgl, App.h("span", { class: "track" }), "从其他设备接收"),
          App.h("span", { class: "hint ml-auto" }, "启动时自动放行防火墙端口"),
        ),
        refs.stateLine,
        App.h("div", { class: "sep" }),
        App.h("div", { class: "card-title", style: { fontSize: "13px" } }, "局域网设备"),
        refs.devList,
        App.row(
          App.h("button", { class: "btn sm", onclick: addManual }, "+ 手动添加设备"),
          App.h("span", { class: "hint" }, "发现失败时可手动输入对方 IP（端口默认 41891）"),
        ),
      ));

      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "card-title" },
          "剪贴板历史（点击条目回贴）",
          /* v5.3：外层容器此前是收缩宽度（内容定宽），导致里面的搜索框
             无法伸展成"整行可用宽度"；给它 flex:1 让输入框有空间可长 */
          App.h("span", { style: { marginLeft: "auto", display: "flex", gap: "6px", flex: "1", minWidth: "0", justifyContent: "flex-end" } },
            refs.search,
            App.h("button", {
              class: "btn sm danger", onclick: async () => {
                if (!(await App.confirm("清空历史", "确定清空全部剪贴板历史？"))) return;
                await App.guardedCall("clip_clear");
                state.history = [];
                renderHistory();
              },
            }, "清空"),
          ),
        ),
        refs.histList,
      ));

      el.appendChild(refs.log);

      /* 事件订阅 */
      App.on("clip_history", (e) => {
        state.history.unshift(e);
        if (state.history.length > 500) state.history.pop();
        if (e.kind !== "image" && state.desensitize && e.text) {
          const { hits } = App.maskSensitive(e.text);
          if (hits.length && !App.state.warnedSensitive) {
            App.state.warnedSensitive = true;
            App.toast(`检测到敏感信息（${hits.join("/")}），历史中已模糊显示`, "warn", 5000);
          }
        }
        renderHistory();
      });
      App.on("clip_log", (m) => log(m));
      App.on("clip_state", (st) => {
        state.running = st.running;
        if (st.send !== undefined) state.send = st.send;
        if (st.recv !== undefined) state.recv = st.recv;
        renderControls();
      });

      await refresh();
    },

    show() { refresh(); },
    async onCli(path) { await handleCliFile(path); },
  });
})();
