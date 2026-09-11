/* Clash（mihomo 内核）页：与「V2rayN」（xray/sing-box）完全独立的一套。
 *
 * 单页面 + 页内页签（v2ray 式管理，App.subnav 互斥区块），归入「网盘与网络」分组
 * （导航项排在 V2rayN 之后——注册顺序由 index.html 脚本加载顺序决定）：
 *   运行控制   —— 启停 / 模式 / 系统代理 / 实时速率 / 策略组 + 服务日志
 *   节点与订阅 —— 订阅导入 / 自动更新 / 节点列表（搜索 / 测速 / 删除）
 *   连接与日志 —— 活动连接实时列表 + 内核日志流
 *   设置与规则 —— 内核与规则库、端口、TUN、日志级别 + 分流规则（折叠分区）
 * 后端：clash_*（app/bridge/clash_api.py）→ app/core/clash_core.py（mihomo）。
 */
(function () {
  "use strict";

  const GROUP = "网盘与网络";
  const state = {
    st: null, nodes: [], groups: null, conns: null, connErr: "",
    busy: false, testing: new Set(), wired: false, filter: "",
    /* 实时速率（/connections 累计值差分）/ 规则页 / 内核日志页 */
    speedPrev: null, downSpeed: null, upSpeed: null,
    rules: null, ruleErr: "", ruleFilter: "",
    kernelLogs: [], logOn: false, logFollow: true,
  };
  let refs = {};
  let log = null;

  /* ---------------- 状态 ---------------- */
  function tagOf(st) {
    if (!st || !st.running) return App.statusTag("未启动", "warn", "zap");
    return st.healthy
      ? App.statusTag("运行中 · 混合端口 " + (st.mixed_port || ""), "ok", "check")
      : App.statusTag("运行中 · 未就绪", "warn", "info");
  }

  function renderStatus(st) {
    if (refs.status) {
      refs.status.replaceChildren(tagOf(st));
      if (refs.startBtn) refs.startBtn.disabled = state.busy || !!st.running;
      if (refs.stopBtn) refs.stopBtn.disabled = state.busy || !st.running;
      if (refs.modeSel) refs.modeSel.value = st.mode || "rule";
      if (refs.nodeCount) refs.nodeCount.textContent = String((st.nodes || []).length);
      if (refs.sysProxyTag) {
        refs.sysProxyTag.replaceChildren(
          st.sys_proxy ? App.statusTag("系统代理已开启", "ok", "check")
                       : App.statusTag("系统代理未开启", "", "info"));
      }
    }
    if (refs.coreHint) {
      const exe = String(st.bin || "").split("\\").pop();
      refs.coreHint.textContent = st.core_ok
        ? ("内核已就绪：" + exe + (st.build ? "　" + st.build : ""))
        : "未找到 mihomo 内核：点右侧「下载 mihomo 内核」";
    }
    if (refs.geoHint) {
      refs.geoHint.textContent = st.geo_ok
        ? "规则库已就绪（geoip.dat / geosite.dat）"
        : "规则库缺失：点「下载规则库」（否则智能分流退回内置规则）";
    }
    if (refs.mixedPort && document.activeElement !== refs.mixedPort) {
      refs.mixedPort.value = st.mixed_port || 7891;
    }
    if (refs.apiPort && document.activeElement !== refs.apiPort) {
      refs.apiPort.value = st.api_port || 9091;
    }
    if (refs.tunTgl) refs.tunTgl.checked = !!st.tun;
    if (refs.tunHint) {
      refs.tunHint.textContent = st.admin ? "（当前具备管理员权限）" : "（需以管理员身份运行本程序）";
    }
    if (refs.archHint) {
      refs.archHint.textContent = st.arch
        ? st.arch + (st.core_asset ? "　→ 已下载：" + st.core_asset.replace(/\.zip$/, "") : "")
        : "";
    }
    if (refs.logLevel && document.activeElement !== refs.logLevel) {
      refs.logLevel.value = st.log_level || "warning";
    }
  }

  async function refreshState() {
    const r = await App.tryCall("clash_get_state");
    if (!r.ok) return;
    state.st = r.data;
    state.nodes = r.data.nodes || [];
    renderStatus(r.data);
    renderNodes();
    renderSub(r.data);
    if (refs.connBox && refs.connBox.offsetParent) await refreshConns();
    await refreshGroups();
  }

  function wire() {
    if (state.wired) return;
    state.wired = true;
    App.on("clash_log", (m) => { if (log) log(String(m)); });
    App.on("clash_state", (m) => {
      state.st = m || state.st;
      if (state.st) {
        state.nodes = state.st.nodes || state.nodes;
        renderStatus(state.st);
        renderSub(state.st);
      }
    });
    App.on("clash_task", (m) => {
      if (m && m.status === "running" && log) {
        log(`下载 ${m.name}：${App.fmtBytes(m.done)}/${App.fmtBytes(m.total) || "?"}`);
      }
    });
    /* 内核日志流推送（订阅后一直收集，页面只负责展示） */
    App.on("clash_kernel_log", (m) => {
      if (!m) return;
      state.kernelLogs.push(m);
      if (state.kernelLogs.length > 500) state.kernelLogs.splice(0, state.kernelLogs.length - 500);
      appendKernelLog(m);
    });
    /* 连接面板轮询：仅当「连接与日志」页签可见且 Clash 运行时
       （页未激活 / 页签隐藏时 offsetParent 为 null，一并跳过） */
    setInterval(() => {
      const st = state.st || {};
      if (!st.running || !refs.connBox || !refs.connBox.offsetParent) return;
      refreshConns();
    }, 2500);
    /* 实时速率：累计字节差分（每 2 秒刷新，仅运行控制页签可见时测量） */
    setInterval(async () => {
      const st = state.st || {};
      if (!refs.speedTag || !refs.speedTag.offsetParent) return;
      if (!st.running) {
        state.speedPrev = null;
        state.downSpeed = state.upSpeed = null;
        renderSpeed();
        return;
      }
      const r = await App.tryCall("clash_connections");
      if (!r.ok) return;
      const now = Date.now();
      const prev = state.speedPrev;
      const cur = { t: now, d: r.data.download_total || 0, u: r.data.upload_total || 0 };
      state.speedPrev = cur;
      if (prev && now > prev.t) {
        const dt = (now - prev.t) / 1000;
        state.downSpeed = Math.max(0, (cur.d - prev.d) / dt);
        state.upSpeed = Math.max(0, (cur.u - prev.u) / dt);
      }
      renderSpeed();
    }, 2000);
  }

  function renderSpeed() {
    if (!refs.speedTag) return;
    const st = state.st || {};
    if (!st.running) { refs.speedTag.textContent = "—"; return; }
    if (state.downSpeed === null) { refs.speedTag.textContent = "测量中…"; return; }
    refs.speedTag.textContent = "↓ " + App.fmtBytes(state.downSpeed) + "/s"
      + "   ↑ " + App.fmtBytes(state.upSpeed) + "/s";
  }

  /* ---------------- 运行控制 ---------------- */
  async function doStart() {
    if (state.busy) return;
    state.busy = true;
    try {
      const r = await App.tryCall("clash_start");
      if (!r.ok) App.toast(r.err, "error", 8000);
      else App.toast("Clash 已启动（系统代理已设置）", "ok", 5000);
    } finally { state.busy = false; }
    await refreshState();
  }

  async function doStop() {
    if (state.busy) return;
    state.busy = true;
    try {
      const r = await App.tryCall("clash_stop");
      if (!r.ok) App.toast(r.err, "error", 6000);
    } finally { state.busy = false; }
    await refreshState();
  }

  async function doSetMode() {
    const r = await App.tryCall("clash_set_mode", refs.modeSel.value);
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refreshState();
  }

  async function toggleSysProxy() {
    const on = !((state.st || {}).sys_proxy);
    const r = await App.tryCall("clash_set_sysproxy", on);
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refreshState();
  }

  async function savePorts() {
    const r = await App.tryCall("clash_set_ports",
      parseInt(refs.mixedPort.value, 10) || 7891,
      parseInt(refs.apiPort.value, 10) || 9091);
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast("端口已保存" + ((state.st || {}).running ? "，重启 Clash 后生效" : ""), "ok", 4000);
  }

  async function toggleTun(ev) {
    const r = await App.tryCall("clash_set_tun", ev.target.checked);
    if (!r.ok) { ev.target.checked = !ev.target.checked; App.toast(r.err, "error", 6000); }
    await refreshState();
  }

  async function downloadCore() {
    const r = await App.tryCall("clash_download_core");
    if (!r.ok) { App.toast(r.err, "error", 8000); return; }
    App.toast(`mihomo ${r.data.version} 就绪`, "ok", 5000);
    await refreshState();
  }

  async function downloadGeo() {
    const r = await App.tryCall("clash_download_geo");
    if (!r.ok) { App.toast(r.err || "规则库下载失败", "error", 8000); return; }
    App.toast("规则库已就绪（geoip.dat / geosite.dat）", "ok", 5000);
    await refreshState();
  }

  /* ---------------- 策略组 ---------------- */
  async function refreshGroups() {
    const r = await App.tryCall("clash_groups");
    state.groups = r.ok ? r.data : null;
    renderGroups();
  }

  function renderGroups() {
    if (!refs.groupsBox) return;
    const box = refs.groupsBox;
    box.innerHTML = "";
    const st = state.st || {};
    if (!st.running) {
      box.appendChild(App.h("div", { class: "empty" },
        "Clash 未运行：启动后这里显示策略组，可运行时切换节点"));
      return;
    }
    const g = state.groups;
    if (!g || !(g.groups || []).length) {
      box.appendChild(App.h("div", { class: "empty" }, "暂无策略组"));
      return;
    }
    for (const grp of g.groups) {
      const isSelect = String(grp.type || "").toLowerCase() === "selector";
      box.appendChild(App.h("div", { class: "card", style: { marginBottom: "8px", padding: "10px 12px" } },
        App.h("div", { class: "row", style: { alignItems: "center", gap: "8px", flexWrap: "wrap" } },
          App.h("b", null, grp.name),
          App.statusTag(isSelect ? "手动选择" : "自动（测速/故障转移）",
            isSelect ? "" : "accent", isSelect ? "info" : "zap"),
          App.h("span", { class: "hint" }, "成员 " + grp.members.length + " 个" +
            (grp.filter ? "（关键字：" + grp.filter + "）" : "")),
          App.h("span", { class: "hint" }, "当前：" + (grp.now || "-")),
          App.h("button", { class: "btn sm", title: "对该组做内核级测速",
            onclick: () => testGroup(grp.name) }, "测速")),
        App.h("div", { class: "row",
          style: { flexWrap: "wrap", gap: "6px", marginTop: "6px", maxHeight: "150px", overflowY: "auto" } },
          grp.members.slice(0, 80).map((m) => App.h("button", {
            class: "btn sm" + (grp.now === m.name ? " primary" : ""),
            title: m.remark || m.name,
            style: { maxWidth: "230px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
            onclick: () => selectMember(grp, m),
          }, (m.remark || m.name) + (m.latency != null ? " · " + m.latency + "ms" : "")))),
        grp.members.length > 80
          ? App.h("div", { class: "hint" }, "（仅显示前 80 个，共 " + grp.members.length + " 个）")
          : null));
    }
    box.appendChild(App.row(
      App.h("button", { class: "btn sm", onclick: editGroups }, "编辑分组"),
      App.h("span", { class: "hint" }, "切换即时生效（内核 API）；成员 / 关键字改动需重启 Clash")));
  }

  async function selectMember(grp, m) {
    if (String(grp.type || "").toLowerCase() !== "selector") {
      App.toast("「" + grp.name + "」是自动组，不能手动指定", "warn", 4000);
      return;
    }
    const r = await App.tryCall("clash_select", grp.name, m.name);
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast(`「${grp.name}」→ ${m.remark || m.name}`, "ok", 3000);
    await refreshGroups();
  }

  async function testGroup(name) {
    App.toast("正在测速…", "info", 2000);
    const r = await App.tryCall("clash_test_group", name);
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    const ok = (r.data || []).filter((x) => x.ok).length;
    App.toast(`「${name}」测速完成：${ok}/${(r.data || []).length} 可用`, "ok", 5000);
    await refreshGroups();
  }

  async function editGroups() {
    const cur = (state.st || {}).groups || [];
    const box = App.h("div", { style: { display: "flex", flexDirection: "column", gap: "6px" } });
    const addRow = (g) => {
      const name = App.h("input", { class: "input", value: g ? g.name : "", placeholder: "组名", style: { width: "120px" } });
      const type = App.h("select", { class: "input", style: { width: "120px" } },
        App.h("option", { value: "select" }, "手动选择"),
        App.h("option", { value: "url-test" }, "自动测速"),
        App.h("option", { value: "fallback" }, "故障转移"),
        App.h("option", { value: "load-balance" }, "负载均衡"));
      type.value = g ? g.type : "select";
      const filter = App.h("input", { class: "input", value: g ? g.filter : "",
        placeholder: "关键字（空=全部，逗号分隔）", style: { flex: "1", minWidth: "130px" } });
      const def = App.h("input", { type: "checkbox" });
      if (g && g.default) def.checked = true;
      const row = App.h("div", { class: "row", style: { alignItems: "center", gap: "6px" } },
        name, type, filter,
        App.h("label", { class: "chk", title: "默认出口" }, def, " 默认"),
        App.h("button", { class: "btn sm danger", onclick: () => row.remove() }, "删除"));
      row._read = () => ({ name: name.value.trim(), type: type.value,
                           filter: filter.value.trim(), default: def.checked });
      box.appendChild(row);
    };
    cur.forEach(addRow);
    if (!cur.length) addRow(null);
    const ok = await App.modal({
      title: "编辑 Clash 策略组",
      body: App.h("div", null,
        App.h("p", { class: "hint" }, "成员由关键字匹配节点备注 / 地址 / 协议（空或 * = 全部）；" +
          "「手动选择」组会自动包含其它已定义的组。"),
        box,
        App.h("button", { class: "btn sm", style: { marginTop: "6px" }, onclick: () => addRow(null) }, "＋ 新增分组")),
      okText: "保存",
    });
    if (ok !== true) return;
    const groups = [...box.children].map((c) => (c._read ? c._read() : null)).filter((g) => g && g.name);
    const r = await App.tryCall("clash_save_groups", groups);
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast("已保存" + ((state.st || {}).running ? "，重启 Clash 后生效" : ""), "ok", 5000);
    await refreshGroups();
  }

  /* ---------------- 节点 ---------------- */
  function renderNodes() {
    if (!refs.nodeBox) return;
    const box = refs.nodeBox;
    box.innerHTML = "";
    if (!state.nodes.length) {
      box.appendChild(App.h("div", { class: "empty" },
        "还没有节点\n在「Clash 订阅」页导入订阅，或从「V2rayN」复制现有节点"));
      return;
    }
    const q = String(state.filter || "").trim().toLowerCase();
    const rows = state.nodes.map((n, i) => ({ n, i }))
      .filter(({ n }) => !q || String(n.remark || "").toLowerCase().includes(q) ||
        String(n.addr || "").toLowerCase().includes(q) ||
        String(n.type || "").toLowerCase().includes(q));
    if (refs.nodeShown) {
      refs.nodeShown.textContent = q ? `显示 ${rows.length} / ${state.nodes.length} 个`
                                     : `共 ${state.nodes.length} 个`;
    }
    if (!rows.length) { box.appendChild(App.h("div", { class: "empty" }, "没有匹配的节点")); return; }
    rows.forEach(({ n, i }) => {
      box.appendChild(App.h("div", { class: "list-item", style: { gap: "8px" } },
        App.h("span", { class: "tag", style: { flex: "none", minWidth: "58px", textAlign: "center" } }, n.type || "?"),
        App.h("span", { class: "li-main" },
          App.h("span", { class: "li-title" }, App.esc(n.remark || (n.addr + ":" + n.port))),
          App.h("span", { class: "li-sub mono" }, `${n.addr}:${n.port}`)),
        App.h("span", { style: { flex: "none" } },
          n.latency != null
            ? App.statusTag(n.latency + "ms", n.latency < 300 ? "ok" : (n.latency < 800 ? "warn" : "danger"), "zap")
            : App.statusTag("未测", "", "info")),
        App.h("button", { class: "btn sm", title: "内核级测速",
          onclick: async (ev) => {
            ev.stopPropagation();
            state.testing.add(i);
            renderNodes();
            const r = await App.tryCall("clash_test_node", i);
            state.testing.delete(i);
            if (r.ok) App.toast(`节点[${i}] 可用，延迟 ${r.data.latency}ms`, "ok", 4000);
            else App.toast(`节点[${i}] ${r.err}`, "warn", 5000);
            await refreshState();
          } }, state.testing.has(i) ? "…" : "测试"),
        App.h("button", { class: "btn sm danger", title: "从 Clash 节点库删除",
          onclick: async (ev) => {
            ev.stopPropagation();
            if (!(await App.confirm("删除节点", `确定删除 ${n.remark || n.addr} 吗？`))) return;
            const r = await App.tryCall("clash_delete_node", i);
            if (!r.ok) { App.toast(r.err, "error", 5000); return; }
            await refreshState();
          } }, "删除")));
    });
  }

  /* ---------------- 连接 ---------------- */
  async function refreshConns() {
    const st = state.st || {};
    if (!st.running) { state.conns = null; state.connErr = ""; renderConns(); return; }
    const r = await App.tryCall("clash_connections");
    if (!r.ok) { state.connErr = r.err; state.conns = null; }
    else { state.connErr = ""; state.conns = r.data; }
    renderConns();
  }

  function renderConns() {
    if (!refs.connBox) return;
    const box = refs.connBox;
    box.innerHTML = "";
    const st = state.st || {};
    if (refs.connStats) {
      refs.connStats.textContent = state.conns
        ? `连接 ${state.conns.connections.length} · 累计 ↓${App.fmtBytes(state.conns.download_total)} ` +
          `↑${App.fmtBytes(state.conns.upload_total)}` +
          (state.conns.memory ? ` · 内存 ${App.fmtBytes(state.conns.memory)}` : "")
        : "";
    }
    if (!st.running) { box.appendChild(App.h("div", { class: "empty" }, "Clash 未运行")); return; }
    if (state.connErr) { box.appendChild(App.h("div", { class: "empty" }, state.connErr)); return; }
    if (!state.conns) { box.appendChild(App.h("div", { class: "empty" }, "加载中…")); return; }
    if (!state.conns.connections.length) { box.appendChild(App.h("div", { class: "empty" }, "暂无活动连接")); return; }
    for (const c of state.conns.connections.slice(0, 150)) {
      box.appendChild(App.h("div", { class: "list-item", style: { gap: "8px" } },
        App.h("span", { class: "li-main" },
          App.h("span", { class: "li-title mono", style: { wordBreak: "break-all" } }, c.host || c.dest || "?"),
          App.h("span", { class: "li-sub" },
            [c.network, c.rule && ("规则 " + c.rule + (c.payload ? ":" + c.payload : "")),
             (c.chains || []).join(" → "), c.process].filter(Boolean).join(" · "))),
        App.h("span", { class: "li-sub mono", style: { flex: "none" } },
          `↑${App.fmtBytes(c.upload)} ↓${App.fmtBytes(c.download)}`),
        App.h("button", { class: "btn sm", onclick: async () => {
          const r = await App.tryCall("clash_close_connection", c.id);
          if (!r.ok) { App.toast(r.err, "error", 5000); return; }
          await refreshConns();
        } }, "关闭")));
    }
  }

  /* ---------------- 订阅 ---------------- */
  async function importSub() {
    const url = refs.subUrl.value.trim();
    if (!url) { App.toast("请输入订阅地址", "warn"); return; }
    const r = await App.tryCall("clash_import_sub", url);
    if (!r.ok) { App.toast(r.err, "error", 8000); return; }
    App.toast(`导入成功：新增 ${r.data.added}，共 ${r.data.total} 个节点`, "ok", 5000);
    await refreshState();
  }

  async function updateSubNow() {
    const url = ((state.st || {}).sub_url || "").trim();
    if (!url) { App.toast("还没有订阅地址", "warn", 4000); return; }
    const r = await App.tryCall("clash_import_sub", url);
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast(`订阅已更新：新增 ${r.data.added}，共 ${r.data.total} 个节点`, "ok", 5000);
    await refreshState();
  }

  async function setSubAuto() {
    const hours = parseFloat(refs.subAuto.value) || 0;
    const r = await App.tryCall("clash_set_sub_autoupdate", hours);
    if (!r.ok) { App.toast(r.err, "error", 5000); return; }
    App.toast(hours > 0 ? `订阅将每 ${hours} 小时自动更新` : "已关闭自动更新", "ok", 4000);
    await refreshState();
  }

  async function copyFromV2ray() {
    const r = await App.tryCall("clash_import_from_v2ray");
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast(`已从「V2rayN」复制：新增 ${r.data.added}，共 ${r.data.total} 个节点`, "ok", 5000);
    await refreshState();
  }

  async function pasteLinks() {
    const text = await App.prompt("粘贴分享链接", "",
      "支持 vless:// vmess:// trojan:// ss:// hysteria2:// tuic:// anytls:// 等，可多行");
    if (!text) return;
    const r = await App.tryCall("clash_import_text", text);
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast(`导入成功：新增 ${r.data.added}，共 ${r.data.total} 个节点`, "ok", 5000);
    await refreshState();
  }

  /** 订阅流量 / 到期（subscription-userinfo 响应头，Clash 客户端同款信息） */
  function fmtUserinfo(u) {
    u = u || {};
    const used = (u.upload || 0) + (u.download || 0);
    if (!u.total && !u.expire && !used) return "订阅未提供流量信息（部分机场不返回该响应头）";
    const parts = ["已用 " + App.fmtBytes(used)];
    if (u.total) {
      parts.push("共 " + App.fmtBytes(u.total) +
        "（剩余 " + App.fmtBytes(Math.max(0, u.total - used)) + "）");
    } else {
      parts.push("不限量");
    }
    if (u.expire) parts.push("到期 " + App.fmtDate(u.expire));
    return "订阅流量：" + parts.join(" · ");
  }

  function renderSub(st) {
    if (refs.subTraffic) {
      refs.subTraffic.textContent = fmtUserinfo(st && st.sub_userinfo);
    }
    if (refs.subInfo) {
      const ts = (st && st.sub_updated_ts) || 0;
      refs.subInfo.textContent = st && st.sub_url
        ? ("当前订阅：" + st.sub_url.slice(0, 56) + (st.sub_url.length > 56 ? "…" : "") +
           (ts ? " · 上次更新 " + App.fmtDate(ts) : " · 尚未更新"))
        : "未设置订阅地址";
    }
    if (refs.subAuto) refs.subAuto.value = String(Math.round((st && st.sub_hours) || 0));
  }

  /* ---------------- 规则 ---------------- */
  async function refreshRules() {
    const r = await App.tryCall("clash_rules");
    state.rules = r.ok ? r.data : null;
    state.ruleErr = r.ok ? "" : r.err;
    renderRules();
  }

  function renderRules() {
    const st = state.st || {};
    /* 自定义规则编辑区：一行一条 */
    if (refs.ruleEdit && document.activeElement !== refs.ruleEdit) {
      refs.ruleEdit.value = ((state.rules || {}).custom || []).join("\n");
    }
    if (refs.ruleStats) {
      const d = state.rules || {};
      const total = (d.rules || []).length;
      refs.ruleStats.textContent = state.ruleErr
        ? state.ruleErr
        : (st.running
            ? `当前生效 ${total} 条 · 自定义 ${(d.custom || []).length} 条`
            : `自定义 ${(d.custom || []).length} 条（Clash 未运行，启动后可查看生效规则）`);
    }
    if (!refs.ruleBox) return;
    const box = refs.ruleBox;
    box.innerHTML = "";
    const all = (state.rules || {}).rules || [];
    const kw = state.ruleFilter.trim().toLowerCase();
    const shown = kw
      ? all.filter((r) => `${r.type},${r.payload},${r.proxy}`.toLowerCase().includes(kw))
      : all;
    if (!shown.length) {
      box.appendChild(App.h("div", { class: "empty" },
        all.length ? "没有匹配的规则" : (st.running ? "暂无规则（内核未下发）" : "Clash 未运行：启动后显示生效规则")));
      return;
    }
    for (const r of shown.slice(0, 300)) {
      box.appendChild(App.h("div", { class: "list-item", style: { gap: "8px" } },
        App.h("span", { class: "li-main" },
          App.h("span", { class: "li-title mono", style: { wordBreak: "break-all" } },
            `${r.type},${r.payload}`),
          App.h("span", { class: "li-sub" },
            (r.custom ? "自定义 · " : "") + "→ " + (r.proxy || "?"))),
        App.h("button", { class: "btn sm", title: "把这条规则复制到自定义规则（置顶生效）",
          onclick: () => {
            const line = `${r.type},${r.payload},${r.proxy}`;
            const cur = refs.ruleEdit.value.trim();
            if (cur.split("\n").includes(line)) { App.toast("已在自定义规则中", "warn", 3000); return; }
            refs.ruleEdit.value = (cur ? line + "\n" + cur : line);
          } }, "复制")));
    }
    if (shown.length > 300) {
      box.appendChild(App.h("div", { class: "hint" },
        `已显示前 300 条（共 ${shown.length} 条，用搜索缩小范围）`));
    }
  }

  async function saveRules() {
    const lines = refs.ruleEdit.value.split("\n").map((s) => s.trim()).filter(Boolean);
    for (const l of lines) {
      if (l.split(",").length < 2) {
        App.toast("规则格式：类型,值,策略（如 DOMAIN-SUFFIX,example.com,DIRECT）", "error", 7000);
        return;
      }
    }
    const r = await App.tryCall("clash_save_rules", lines);
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast(`已保存 ${r.data.length} 条自定义规则` +
      ((state.st || {}).running ? "，已热重载" : ""), "ok", 5000);
    await refreshRules();
  }

  function insertRuleTpl(tpl) {
    if (!refs.ruleEdit) return;
    const cur = refs.ruleEdit.value.trim();
    refs.ruleEdit.value = cur ? tpl + "\n" + cur : tpl;
    refs.ruleEdit.focus();
  }

  /* ---------------- 内核日志 ---------------- */
  function appendKernelLog(m) {
    if (!refs.klogBox) return;
    if (refs.klogLevel && refs.klogLevel.value !== "all"
        && String(m.level || "info") !== refs.klogLevel.value) return;
    const line = App.h("div", { class: "log-line mono", style: { wordBreak: "break-all" } },
      App.h("span", { class: "hint" }, new Date((m.ts || 0) * 1000).toLocaleTimeString() + " "),
      App.h("span", { class: "tag " + (m.level === "error" ? "danger" : (m.level === "warning" ? "warn" : "")),
        style: { marginRight: "6px" } }, String(m.level || "info").toUpperCase()),
      String(m.text || ""));
    refs.klogBox.appendChild(line);
    while (refs.klogBox.childElementCount > 500) refs.klogBox.removeChild(refs.klogBox.firstChild);
    if (state.logFollow) refs.klogBox.scrollTop = refs.klogBox.scrollHeight;
  }

  async function toggleKernelLog(on) {
    if (on === undefined) on = !state.logOn;
    if (!on) {
      state.logOn = false;
      await App.tryCall("clash_log_stop");
      if (refs.klogBtn) refs.klogBtn.textContent = "开始接收";
      return;
    }
    const lv = refs.klogLevel ? refs.klogLevel.value : "info";
    const r = await App.tryCall("clash_log_start", lv === "all" ? "info" : lv);
    if (!r.ok) { App.toast(r.err, "error", 6000); state.logOn = false; return; }
    state.logOn = true;
    if (refs.klogBtn) refs.klogBtn.textContent = "停止接收";
    App.toast(`已订阅内核日志（${r.data}）`, "ok", 4000);
  }

  /* ---------------- 页面注册（单页 + 页签，v2ray 式管理） ---------------- */
  App.registerPage({
    id: "clash",
    title: "Clash",
    icon: "shield",
    group: GROUP,

    async mount(el) {
      wire();
      refs = {};

      /* 页签 1：运行控制（主任务：启停 / 模式 / 系统代理 / 策略组） */
      refs.status = App.h("span", null);
      refs.startBtn = App.h("button", { class: "btn primary", onclick: doStart }, "启动 Clash");
      refs.stopBtn = App.h("button", { class: "btn", disabled: true, onclick: doStop }, "停止");
      refs.modeSel = App.h("select", { class: "input", onchange: doSetMode, style: { width: "104px" } },
        App.h("option", { value: "rule" }, "规则"),
        App.h("option", { value: "global" }, "全局"),
        App.h("option", { value: "direct" }, "直连"));
      refs.sysProxyTag = App.h("span", null);
      refs.speedTag = App.h("span", { class: "mono", style: { color: "var(--accent)" } }, "—");
      refs.groupsBox = App.h("div", { style: { minHeight: "120px" } });
      refs.log = App.h("div", { class: "log-box", style: { maxHeight: "120px" } });
      log = App.makeLog(refs.log);

      const paneRun = App.h("div");
      paneRun.appendChild(App.svcCard("运行控制",
        [App.row(refs.status, App.h("span", { style: { flex: 1 } }), refs.startBtn, refs.stopBtn,
           App.h("span", { class: "field-label" }, "模式："), refs.modeSel,
           App.h("button", { class: "btn sm", onclick: toggleSysProxy }, "系统代理开/关"),
           refs.sysProxyTag),
         App.row(App.h("span", { class: "field-label" }, "实时速率："), refs.speedTag,
           App.h("span", { class: "hint" }, "来自内核连接累计值（每 2 秒刷新，仅本页签在前台时测量）"))],
        [App.h("button", { class: "btn", onclick: () => refreshState() }, "刷新")],
        refs.log));
      paneRun.appendChild(App.svcCard("策略组（手动选择 / 自动测速 / 故障转移 / 负载均衡）",
        [refs.groupsBox]));

      /* 页签 2：节点与订阅 */
      refs.nodeCount = App.h("b", null, "0");
      refs.nodeShown = App.h("span", { class: "hint" }, "");
      refs.nodeBox = App.h("div", { class: "list", style: { minHeight: "160px", maxHeight: "360px", overflowY: "auto" } });
      const nodeSearch = App.h("input", { class: "input", type: "search",
        placeholder: "搜索节点（备注 / 地址 / 协议）", style: { flex: "1", minWidth: "160px" } });
      nodeSearch.addEventListener("input", () => { state.filter = nodeSearch.value; renderNodes(); });
      refs.subUrl = App.h("input", { class: "input", placeholder: "订阅地址（http/https）", style: { flex: "1", minWidth: "220px" } });
      refs.subAuto = App.h("select", { class: "input", onchange: setSubAuto, style: { width: "130px" } },
        App.h("option", { value: "0" }, "自动更新关闭"),
        App.h("option", { value: "6" }, "每 6 小时"),
        App.h("option", { value: "12" }, "每 12 小时"),
        App.h("option", { value: "24" }, "每天"),
        App.h("option", { value: "72" }, "每 3 天"));
      refs.subInfo = App.h("span", { class: "hint" }, "");
      refs.subTraffic = App.h("span", { class: "hint" }, "");

      const paneNodes = App.h("div");
      paneNodes.appendChild(App.svcCard("节点（Clash 独立节点库）",
        [App.row(App.h("span", { class: "field-label" }, "节点数："), refs.nodeCount,
           App.h("span", { style: { flex: 1 } }), nodeSearch, refs.nodeShown),
         refs.nodeBox],
        [App.h("button", { class: "btn", onclick: async () => {
          App.toast("正在批量测速…", "info", 2500);
          const r = await App.tryCall("clash_burst_test");
          if (!r.ok) { App.toast(r.err, "error", 6000); return; }
          const ok = (r.data || []).filter((x) => x.ok).length;
          App.toast(`批量测速完成：${ok}/${(r.data || []).length} 可用`, "ok", 5000);
          await refreshState();
        } }, "批量测速")]));
      paneNodes.appendChild(App.svcCard("订阅导入与自动更新",
        [App.row(refs.subUrl,
           App.h("button", { class: "btn primary", onclick: importSub }, "导入订阅"),
           App.h("button", { class: "btn sm", onclick: updateSubNow }, "立即更新"),
           refs.subAuto),
         App.row(refs.subInfo),
         App.row(refs.subTraffic),
         App.row(
           App.h("button", { class: "btn", onclick: copyFromV2ray,
             title: "把「V2rayN」的节点复制到 Clash 节点库（之后各自独立）" }, "从「V2rayN」复制节点"),
           App.h("button", { class: "btn", onclick: pasteLinks }, "粘贴分享链接…"))],
        [App.h("button", { class: "btn", onclick: () => refreshState() }, "刷新")]));

      /* 页签 3：连接与日志（监控类，页签不可见时轮询自动暂停） */
      refs.connStats = App.h("span", { class: "hint" }, "");
      refs.connBox = App.h("div", { class: "list", style: { minHeight: "200px", maxHeight: "380px", overflowY: "auto" } });
      refs.klogLevel = App.h("select", { class: "input", style: { width: "130px" } },
        App.h("option", { value: "debug" }, "debug"),
        App.h("option", { value: "info" }, "info"),
        App.h("option", { value: "warning" }, "warning"),
        App.h("option", { value: "error" }, "error"),
        App.h("option", { value: "all" }, "全部（展示过滤）"));
      refs.klogLevel.value = ((state.st || {}).log_level) || "info";
      refs.klogBtn = App.h("button", { class: "btn primary",
        onclick: () => toggleKernelLog() }, state.logOn ? "停止接收" : "开始接收");
      refs.klogFollow = App.h("input", { type: "checkbox", checked: state.logFollow,
        onchange: (e) => { state.logFollow = e.target.checked; } });
      refs.klogBox = App.h("div", { class: "log-box mono",
        style: { minHeight: "200px", maxHeight: "380px", overflowY: "auto" } });
      for (const m of state.kernelLogs) appendKernelLog(m);

      const paneMon = App.h("div");
      paneMon.appendChild(App.svcCard("活动连接（每 2.5 秒刷新）",
        [App.row(refs.connStats, App.h("span", { style: { flex: 1 } }),
           App.h("button", { class: "btn sm", onclick: () => refreshConns() }, "刷新"),
           App.h("button", { class: "btn sm danger", onclick: async () => {
             const r = await App.tryCall("clash_close_all_connections");
             if (!r.ok) { App.toast(r.err, "error", 5000); return; }
             await refreshConns();
           } }, "全部关闭")),
         refs.connBox]));
      paneMon.appendChild(App.svcCard("内核日志流（mihomo /logs 接口，推送式）",
        [App.row(App.h("span", { class: "field-label" }, "级别（服务端）："), refs.klogLevel,
           refs.klogBtn,
           App.h("button", { class: "btn sm", onclick: () => {
             refs.klogBox.innerHTML = "";
             state.kernelLogs = [];
           } }, "清空"),
           App.h("label", { class: "switch" }, refs.klogFollow, App.h("span", { class: "track" }), "自动滚动"),
           App.h("span", { class: "hint" }, "服务端级别越低日志越多；「全部」只做前端展示过滤")),
         refs.klogBox]));

      /* 页签 4：设置与规则（低频配置；分流规则收进折叠分区） */
      refs.coreHint = App.h("span", { class: "hint" }, "");
      refs.geoHint = App.h("span", { class: "hint" }, "");
      refs.archHint = App.h("span", { class: "hint" }, "");
      refs.mixedPort = App.h("input", { class: "input", type: "number", min: "1", max: "65535", style: { width: "92px" } });
      refs.apiPort = App.h("input", { class: "input", type: "number", min: "1", max: "65535", style: { width: "92px" } });
      refs.tunTgl = App.h("input", { type: "checkbox", onchange: toggleTun });
      refs.tunHint = App.h("span", { class: "hint" }, "");
      refs.logLevel = App.h("select", { class: "input", style: { width: "130px" },
        onchange: async () => {
          const r = await App.tryCall("clash_set_log_level", refs.logLevel.value);
          if (!r.ok) { App.toast(r.err, "error", 5000); return; }
          App.toast("内核日志级别：" + r.data, "ok", 4000);
        } },
        App.h("option", { value: "debug" }, "debug（最详细）"),
        App.h("option", { value: "info" }, "info"),
        App.h("option", { value: "warning" }, "warning"),
        App.h("option", { value: "error" }, "error（仅错误）"));
      refs.ruleStats = App.h("span", { class: "hint" }, "");
      refs.ruleEdit = App.h("textarea", { class: "input mono", rows: "7",
        placeholder: "一行一条，例如：\nDOMAIN-SUFFIX,example.com,DIRECT\nIP-CIDR,1.1.1.1/32,REJECT\nGEOIP,CN,DIRECT",
        style: { width: "100%", resize: "vertical" } });
      refs.ruleBox = App.h("div", { class: "list", style: { minHeight: "160px", maxHeight: "360px", overflowY: "auto" } });
      const ruleSearch = App.h("input", { class: "input", type: "search",
        placeholder: "搜索生效规则（类型 / 值 / 策略）", style: { flex: "1", minWidth: "180px" } });
      ruleSearch.addEventListener("input", () => { state.ruleFilter = ruleSearch.value; renderRules(); });

      const paneSet = App.h("div");
      paneSet.appendChild(App.svcCard("内核与规则库",
        [App.row(App.h("span", { class: "field-label" }, "本机架构："), refs.archHint,
           App.h("span", { class: "hint" },
             "下载内核时按此自动选择对应构建（x86-64 v3/v2/v1、ARM64、32 位）")),
         App.row(App.h("span", { class: "field-label" }, "内核："), refs.coreHint,
           App.h("button", { class: "btn", onclick: downloadCore }, "下载 mihomo 内核")),
         App.row(App.h("span", { class: "field-label" }, "规则库："), refs.geoHint,
           App.h("button", { class: "btn", onclick: downloadGeo }, "下载规则库")),
         App.row(App.h("button", { class: "btn sm", onclick: async () => {
           const r = await App.tryCall("clash_open_dir");
           if (!r.ok) App.toast(r.err, "error", 4000);
         } }, "打开数据目录"),
           App.h("span", { class: "hint" }, "配置 / 节点 / 分组 / 缓存都在该目录"))]));
      paneSet.appendChild(App.svcCard("端口与 TUN",
        [App.row(App.h("span", { class: "field-label" }, "混合端口（HTTP+SOCKS）："), refs.mixedPort,
           App.h("span", { class: "field-label" }, "API 端口："), refs.apiPort,
           App.h("button", { class: "btn sm", onclick: savePorts }, "保存"),
           App.h("span", { class: "hint" }, "默认 7891 / 9091（避开 Clash Verge 与本应用其它引擎）")),
         App.row(App.h("label", { class: "switch", title: "需管理员权限 + wintun.dll" },
             refs.tunTgl, App.h("span", { class: "track" }), "TUN 模式（虚拟网卡）"), refs.tunHint)]));
      paneSet.appendChild(App.svcCard("日志级别",
        [App.row(App.h("span", { class: "field-label" }, "内核日志级别："), refs.logLevel,
           App.h("span", { class: "hint" }, "影响内核性能，日常建议 warning；排查问题再调 debug"))]));
      paneSet.appendChild(App.sec("分流规则（自定义置顶生效，可覆盖内置规则）",
        [App.svcCard("自定义规则（置顶生效；保存后自动热重载）",
           [App.row(App.h("button", { class: "btn sm", onclick: () => insertRuleTpl("DOMAIN-SUFFIX,,DIRECT") }, "域名直连"),
              App.h("button", { class: "btn sm", onclick: () => insertRuleTpl("DOMAIN-SUFFIX,,REJECT") }, "域名拦截"),
              App.h("button", { class: "btn sm", onclick: () => insertRuleTpl("DOMAIN-KEYWORD,,PROXY") }, "关键字走代理"),
              App.h("button", { class: "btn sm", onclick: () => insertRuleTpl("IP-CIDR,,DIRECT") }, "网段直连"),
              App.h("span", { class: "hint" }, "点击插入模板后补全内容；策略可填 PROXY / DIRECT / REJECT 或策略组名")),
            App.row(refs.ruleEdit),
            App.row(App.h("button", { class: "btn primary", onclick: saveRules }, "保存并热重载"),
              App.h("button", { class: "btn", onclick: () => { refs.ruleEdit.value = ""; saveRules(); } }, "清空自定义规则"),
              App.h("button", { class: "btn sm", onclick: () => refreshRules() }, "刷新"),
              refs.ruleStats)]),
         App.svcCard("当前生效规则（内核下发顺序）",
           [App.row(ruleSearch, App.h("span", { class: "hint" }, "命中顺序自上而下，第一条匹配即生效")),
            refs.ruleBox])],
        { open: false }));

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "Clash"),
        App.h("div", { class: "sub" },
          "mihomo 内核（Clash.Meta），与「V2rayN」相互独立：策略组运行时切换、内核级测速、连接监控；" +
          "内核与规则库在「设置与规则」页签下载")));

      const nav = App.subnav([
        { label: "运行控制", el: paneRun },
        { label: "节点与订阅", el: paneNodes },
        { label: "连接与日志", el: paneMon },
        { label: "设置与规则", el: paneSet },
      ]);
      el.appendChild(nav);
      nav.panes.forEach((p) => el.appendChild(p));

      await refreshState();
      await refreshRules();
      const t = await App.tryCall("clash_log_tail", 40);
      if (t.ok) (t.data || []).forEach((line) => log(line));
    },

    show() { refreshState(); },
  });
})();
