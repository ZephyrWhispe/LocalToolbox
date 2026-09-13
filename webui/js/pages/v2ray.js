/* V2rayN 页（V2rayN 代理）：节点导入 / 三核心托管 / 代理模式 / TUN / 系统代理策略 / 自动重连。
后端：proxy_*（app/bridge/proxy_api.py）→ v2ray_core.py。 */
(function () {
  "use strict";

  const state = {
    st: null,          /* proxy_get_state 结果 data */
    nodes: [],
    busy: false,       /* 启停/切换中，锁操作 */
    burst: false,      /* 批量测速进行中 */
    testing: new Set(),
    filter: "",        /* 节点搜索关键字（备注/地址/协议） */
    sort: "default",   /* default | latency | latency-desc | name */
  };
  let refs = {};
  let log = null;

  const CORE_LABEL = { xray: "Xray", "sing-box": "Sing-Box", v2ray: "V2Ray" };
  const MODE_LABEL = { global: "全局模式", smart: "智能分流", direct: "直连模式" };
  const SYS_PROXY_LABEL = {
    auto: "自动配置（启动写入，停止还原）",
    pac: "PAC 模式（本地脚本分流）",
    none: "不改变（保留其他软件设定）",
    clear: "清除（启动时强制清除）",
  };

  /* ---------------- 状态渲染 ---------------- */
  function tagOf(st) {
    if (!st || !st.running) return App.statusTag("未运行", "warn", "zap");
    if (st.healthy) return App.statusTag(`运行中 · ${st.latency != null ? st.latency + "ms" : "…"}`, "ok", "check");
    return App.statusTag("运行中 · 节点不可达", "warn", "info");
  }

  function renderBin(st) {
    const ok = !!st.core_ok;
    const path = st.core_bin || st.bin_cfg || "";
    refs.binVal.replaceChildren(
      ok
        ? App.statusTag("已配置", "ok", "check")
        : App.statusTag("未配置", "warn", "x"));
    refs.binVal.title = path
      ? path
      : "未找到核心程序：点「自动下载核心」，或从 V2rayN 目录导入时自动识别";
  }

  function coreLabel(k) { return CORE_LABEL[k] || k || "xray"; }

  /* 节点列表：协议徽标 + 延迟色阶 + 搜索/排序（默认按原顺序，与后端索引一致） */
  const PROTO_TAG = {
    anytls: "accent", hy2: "accent", tuic: "accent", wireguard: "accent",
    vless: "ok", vmess: "ok", trojan: "", ss: "", socks: "", http: "",
  };

  function latencyTag(lat) {
    if (lat == null) return App.statusTag("未测", "", "info");
    const kind = lat < 300 ? "ok" : (lat < 800 ? "warn" : "danger");
    return App.statusTag(lat + "ms", kind, "zap");
  }

  /** 过滤 + 排序（仅影响显示，序号始终为后端原始索引） */
  function viewNodes() {
    const q = String(state.filter || "").trim().toLowerCase();
    let rows = state.nodes.map((n, i) => ({ n, i }));
    if (q) {
      rows = rows.filter(({ n }) =>
        String(n.remark || "").toLowerCase().includes(q) ||
        String(n.addr || "").toLowerCase().includes(q) ||
        String(n.type || "").toLowerCase().includes(q));
    }
    const byLatency = (a, b) => {
      const la = a.n.latency, lb = b.n.latency;
      if (la == null && lb == null) return a.i - b.i;
      if (la == null) return 1;
      if (lb == null) return -1;
      return la - lb;
    };
    if (state.sort === "latency") rows.sort(byLatency);
    else if (state.sort === "latency-desc") rows.sort((a, b) => byLatency(b, a));
    else if (state.sort === "name") {
      rows.sort((a, b) => String(a.n.remark || "").localeCompare(String(b.n.remark || ""), "zh"));
    }
    return rows;
  }

  function renderNodes() {
    refs.nodes.innerHTML = "";
    if (!state.nodes.length) {
      refs.nodes.appendChild(App.h("div", { class: "empty" },
        "暂无节点\n可在「节点导入」页输入订阅地址、粘贴分享链接，或导入 V2rayN 目录"));
      return;
    }
    const sel = state.st ? state.st.selected : 0;
    const cur = state.st && state.st.current ? state.st.current.index : -1;
    const rows = viewNodes();
    if (refs.nodeShown) {
      refs.nodeShown.textContent = rows.length === state.nodes.length
        ? `共 ${state.nodes.length} 个`
        : `显示 ${rows.length} / ${state.nodes.length} 个`;
    }
    if (!rows.length) {
      refs.nodes.appendChild(App.h("div", { class: "empty" }, "没有匹配的节点"));
      return;
    }
    rows.forEach(({ n, i }) => {
      const testing = state.testing.has(i);
      const active = i === cur;
      refs.nodes.appendChild(App.h("div", {
        class: "list-item" + (i === sel ? " sel" : ""),
        title: "双击应用该节点",
        ondblclick: () => selectNode(i),
      },
        App.h("span", { class: "tag " + (PROTO_TAG[n.type] || ""), style: { flex: "none", minWidth: "58px", textAlign: "center" } },
          n.type || "?"),
        App.h("span", { class: "li-main" },
          App.h("span", { class: "li-title" },
            App.esc(n.remark || (n.addr + ":" + n.port)),
            i === sel ? App.h("span", { class: "tag accent", style: { marginLeft: "8px" } }, "当前") : null,
            active ? App.h("span", { class: "tag", style: { marginLeft: "6px" } }, "运行中") : null,
          ),
          App.h("span", { class: "li-sub mono" },
            `${n.addr}:${n.port}` +
            (n.tls && n.tls !== "none" ? " · TLS" : "") +
            (n.network && n.network !== "tcp" ? ` · ${n.network}` : "")),
        ),
        App.h("span", { style: { flex: "none" } }, latencyTag(n.latency)),
        App.h("button", {
          class: "btn sm", title: "测试节点连通性",
          onclick: (ev) => { ev.stopPropagation(); testNode(i); },
        }, testing ? "…" : "测试"),
        App.h("button", {
          class: "btn sm", title: "设为当前节点并应用",
          disabled: state.busy,
          onclick: (ev) => { ev.stopPropagation(); selectNode(i); },
        }, "应用"),
        App.h("button", {
          class: "btn sm danger", title: "删除节点",
          onclick: async (ev) => {
            ev.stopPropagation();
            if (!(await App.confirm("删除节点", `确定删除 ${n.remark || n.addr} 吗？`))) return;
            const r = await App.tryCall("proxy_delete", i);
            if (!r.ok) App.toast(r.err, "error", 6000);
            await refreshState();
          },
        }, "删除"),
      ));
    });
  }

  function renderPorts(st) {
    refs.httpPort.value = st ? st.http_port : 10809;
    refs.socksPort.value = st ? st.socks_port : 10808;
  }

  function renderControls(st) {
    refs.status.replaceChildren(tagOf(st));
    refs.startBtn.disabled = state.busy || !!st.running;
    refs.stopBtn.disabled = state.busy || !st.running;
    refs.autoTgl.checked = !!st.auto_switch;
    refs.coreSel.value = st.core_type || "xray";
    refs.modeSel.value = st.mode || "smart";
    refs.tunTgl.checked = !!st.tun;
    refs.tunTgl.disabled = (st.core_type || "xray") !== "sing-box";
    refs.sysProxySel.value = st.sys_proxy_mode || "auto";
    refs.sysProxySel.title = SYS_PROXY_LABEL[refs.sysProxySel.value] || "";
    refs.dlBtn.textContent = `自动下载 ${coreLabel(st.core_type)}`;
    refs.burstBtn.disabled = state.busy || state.burst;
    if (refs.subAuto) {
      refs.subAuto.value = String(Math.round(st.sub_autoupdate_hours || 0));
    }
    if (refs.subInfo) {
      const ts = st.sub_updated_ts || 0;
      refs.subInfo.textContent = st.sub_url
        ? ("订阅：" + st.sub_url.slice(0, 48) + (st.sub_url.length > 48 ? "…" : "") +
           (ts ? " · 上次更新 " + App.fmtDate(ts) : " · 尚未更新"))
        : "未设置订阅地址";
    }
    refs.speedRow.style.display = st.running ? "" : "none";
    if (!st.running) refs.speed.textContent = "↑ 0/s ↓ 0/s";
    const isSing = (st.core_type || "xray") === "sing-box";
    const geoOk = isSing ? st.geo_sing_ok : st.geo_ok;
    refs.geoVal.replaceChildren(
      geoOk
        ? App.statusTag("已就绪", "ok", "check")
        : App.statusTag("缺失", "warn", "x"));
    refs.geoVal.title = isSing
      ? (geoOk ? "规则集（.srs）已就绪" : "未下载规则集：点「下载规则库」获取 .srs（缺失时智能分流退回内置静态规则）")
      : (geoOk ? "geoip.dat / geosite.dat 已就绪" : "未下载规则库：点「下载规则库」获取 geoip.dat / geosite.dat（缺失时智能分流退回内置静态规则）");
  }

  /* ---------------- 操作 ---------------- */
  async function refreshState() {
    const r = await App.tryCall("proxy_get_state");
    if (!r.ok) {
      if (r.err) refs.status.replaceChildren(App.statusTag(r.err, "danger"));
      return;
    }
    state.st = r.data;
    state.nodes = r.data.nodes || [];
    renderControls(r.data);
    refs.nodeCount.textContent = String(state.nodes.length);
    refs.curNode.textContent =
      r.data.current ? `${r.data.current.remark}（${r.data.current.addr}:${r.data.current.port}）` : "—";
    renderBin(r.data);
    renderPorts(r.data);
    // 已保存的订阅地址回填（避免每次重输；不覆盖用户正在输入的内容）
    if (refs.subUrl && !refs.subUrl.value.trim() && r.data.sub_url) {
      refs.subUrl.value = String(r.data.sub_url);
    }
    renderNodes();
  }

  async function doStart() {
    if (state.busy) return;
    state.busy = true;
    refs.startBtn.disabled = true;
    refs.stopBtn.disabled = true;   // 启动进行中禁止同时停止，避免竞态
    try {
      const r = await App.tryCall("proxy_start", refs.httpPort.value, refs.socksPort.value);
      if (!r.ok) App.toast(r.err, "error", 8000);
    } finally {
      state.busy = false;
      refs.stopBtn.disabled = false;
    }
    await refreshState();
  }

  async function doStop() {
    const r = await App.tryCall("proxy_stop");
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refreshState();
  }

  async function testNode(i) {
    state.testing.add(i);
    renderNodes();
    const r = await App.tryCall("proxy_test", i);
    state.testing.delete(i);
    if (!r.ok) App.toast(r.err || "测试失败", "warn", 6000);
    else App.toast(r.data && r.data.ok
      ? `节点[${i}] 可用，延迟 ${r.data.latency}ms` : (`节点[${i}] ${r.err || "不可用"}`),
      r.data && r.data.ok ? "ok" : "warn", 6000);
    renderNodes();
  }

  async function selectNode(i) {
    if (state.busy) return;
    state.busy = true;   // proxy_select 内部连通性探测最长 12s，防连点
    renderNodes();
    try {
      const r = await App.tryCall("proxy_select", i);
      if (!r.ok) App.toast(r.err, "error", 8000);
      else App.toast(`已应用节点[${i}]${state.st && state.st.running ? "，回退自动恢复" : "（未运行，仅记录选择）"}`, "ok");
    } finally {
      state.busy = false;
    }
    await refreshState();
  }

  /* -- 三核心 / 模式 / TUN / 系统代理策略 -- */
  async function doSetCore() {
    const kind = refs.coreSel.value;
    const r = await App.tryCall("proxy_set_core", kind);
    if (!r.ok) { refs.coreSel.value = state.st ? state.st.core_type : "xray"; App.toast(r.err, "error", 8000); }
    await refreshState();
  }

  async function doSetMode() {
    const mode = refs.modeSel.value;
    const r = await App.tryCall("proxy_set_mode", mode);
    if (!r.ok) { refs.modeSel.value = state.st ? state.st.mode : "smart"; App.toast(r.err, "error", 8000); }
    await refreshState();
  }

  async function doSetTun(ev) {
    const on = ev.target.checked;
    if (on && state.st && !state.st.admin) {
      App.toast("TUN 模式需要管理员权限运行本程序", "warn", 6000);
    }
    const r = await App.tryCall("proxy_set_tun", on);
    if (!r.ok) { ev.target.checked = !on; App.toast(r.err, "error", 6000); }
    await refreshState();
  }

  async function doSetSysProxy(ev) {
    const r = await App.tryCall("proxy_set_sysproxy", ev.target.value);
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refreshState();
  }

  async function doBurstTest() {
    if (state.burst || !state.nodes.length) {
      if (!state.nodes.length) App.toast("暂无节点可测试", "warn");
      return;
    }
    state.burst = true;
    refs.burstBtn.textContent = "测速中…";
    refs.burstBtn.disabled = true;
    const r = await App.tryCall("proxy_burst_test");
    state.burst = false;
    refs.burstBtn.textContent = "批量测速";
    refs.burstBtn.disabled = false;
    if (!r.ok) { App.toast(r.err, "error", 8000); return; }
    // 结果直接回写节点列表（延迟徽标 + 自动排序），不再弹窗罗列
    const rows = r.data || [];
    const okCount = rows.filter((x) => x.ok).length;
    const failCount = rows.length - okCount;
    if (okCount) {
      state.sort = "latency";
      if (refs.nodeSort) refs.nodeSort.value = "latency";
      const best = rows.filter((x) => x.ok).sort((a, b) => a.latency - b.latency)[0];
      App.toast(`测速完成：${okCount} 个可用` +
        (failCount ? ` / ${failCount} 个不可用` : "") +
        `；最快 ${best.latency}ms，已按延迟排序`, "ok", 6000);
    } else {
      App.toast(`测速完成：${rows.length} 个节点均不可用`, "warn", 8000);
    }
    await refreshState();
  }

  /* 刷新组件：重新探测核心/规则库（立即），版本检查结果经 proxy_bins 事件推送 */
  async function refreshBins() {
    refs.refreshBtn.disabled = true;
    refs.refreshBtn.textContent = "检查中…";
    try {
      const r = await App.tryCall("proxy_refresh_bins");
      if (!r.ok) { App.toast(r.err, "error", 6000); return; }
      const d = r.data || {};
      App.toast("组件刷新：" +
        (d.found ? `核心已就绪${d.current ? "（" + d.current + "）" : ""}` : "未找到核心") +
        "；" + (d.geo ? "规则库已就绪" : "规则库缺失") +
        "；正在检查是否有新版本…", d.found ? "ok" : "warn", 5000);
      await refreshState();
    } finally {
      refs.refreshBtn.disabled = false;
      refs.refreshBtn.textContent = "检查更新";
    }
  }

  /* 版本检查结果（后台线程推送）：与本地版本对比提示是否有新版 */
  function onBinsInfo(d) {
    if (!d) return;
    if (d.outdated) {
      App.toast(`${coreLabel(d.core)} 有新版本：本地 ${d.current} → 最新 ${d.latest}，` +
        "点「自动下载核心」更新", "info", 8000);
    } else if (d.current && d.latest) {
      App.toast(`${coreLabel(d.core)} 已是最新版本（${d.current}）`, "ok", 4000);
    } else if (d.check_err) {
      App.toast(`${coreLabel(d.core)} 版本检查失败：网络无法访问 GitHub（可稍后重试或使用镜像）`,
        "warn", 7000);
    }
  }

  async function openAdvanced() {
    const r = await App.tryCall("proxy_get_advanced");
    const cur = r.ok && r.data ? r.data : {};
    const text = JSON.stringify(cur, null, 2) || "{}";
    const v = await App.modal({
      title: "高级配置（路由 / DNS）",
      textarea: text,
      okText: "保存",
      body: "仅支持 routing / dns 两个段，均为 JSON。\n" +
        "routing 可填数组（v2rayN 自定义路由规则）或对象（覆盖默认 routing）；dns 为对象。\n" +
        "留空保存将恢复默认配置。修改下次启动生效。" + "\n\n示例：{\"routing\":[{\"outboundTag\":\"block\",\"domain\":[\"geosite:category-ads-all\"]}]}",
    });
    if (v == null) return;
    const s = await App.tryCall("proxy_set_advanced", v.trim());
    if (!s.ok) App.toast(s.err, "error", 8000);
    else App.toast("高级配置已保存，属性在下次启动生效", "ok");
  }

  /* ---------------- 策略组（Clash Verge 风格） ---------------- */
  async function refreshGroups() {
    if (!refs.groups) return;
    const r = await App.tryCall("proxy_groups");
    if (!r.ok) {
      refs.groups.replaceChildren(App.h("div", { class: "empty" }, r.err || "读取策略组失败"));
      return;
    }
    const d = r.data || {};
    refs.groups.replaceChildren();
    if (!(d.groups || []).length) {
      refs.groups.appendChild(App.h("div", { class: "empty" },
        "暂无策略组。点上方「编辑策略组」按备注 / 地址 / 协议关键字分组；启动核心后可按组切换。"));
      return;
    }
    if (!d.clash) {
      refs.groups.appendChild(App.h("div", { class: "hint", style: { marginBottom: "8px" } },
        "代理未运行：当前仅显示分组定义，启动后可实时切换成员。"));
    }
    d.groups.forEach((g) => {
      const members = App.h("div", { class: "list", style: { maxHeight: "220px", overflowY: "auto" } });
      (g.members || []).forEach((m) => {
        const isNow = !!g.now && m.tag === g.now;
        members.appendChild(App.h("div", { class: "list-item" + (isNow ? " sel" : "") },
          App.h("span", { class: "li-main" },
            App.h("span", { class: "li-title" }, App.esc(m.remark || m.tag)),
            App.h("span", { class: "li-sub mono" }, m.tag)),
          App.h("span", { style: { flex: "none" } }, latencyTag(m.latency)),
          App.h("button", {
            class: "btn sm",
            disabled: !d.clash || g.type !== "selector" || isNow,
            title: g.type === "selector" ? "切换到此成员" : "仅 selector 组可手动切换",
            onclick: async () => {
              const s = await App.tryCall("proxy_group_select", g.name, m.tag);
              if (!s.ok) { App.toast(s.err, "error", 6000); return; }
              App.toast(`「${g.name}」已切换为 ${m.remark || m.tag}`, "ok");
              await refreshGroups();
            },
          }, isNow ? "当前" : (g.type === "selector" ? "切换" : "—")),
        ));
      });
      refs.groups.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "card-title" },
          `${g.name}（${g.type === "urltest" ? "自动测速" : "手动选择"}）`),
        App.h("div", { class: "hint" },
          `过滤关键字：${g.filter || "全部"} · 测试间隔 ${g.interval || "5m"}`),
        members));
    });
  }

  async function editGroups() {
    const r = await App.tryCall("proxy_groups");
    const cur = ((r.ok && r.data && r.data.groups) || []).map((g) => ({
      name: g.name, type: g.type, filter: g.filter, interval: g.interval,
    }));
    const v = await App.modal({
      title: "编辑策略组",
      textarea: JSON.stringify(cur, null, 2),
      okText: "保存",
      body: "JSON 数组，每项 {name, type, filter, interval}。\n" +
        "type：selector=运行中手动切换；urltest=自动选延迟最低。\n" +
        "filter：按备注 / 地址 / 协议匹配的关键字（逗号分隔，留空或 * 表示全部节点）。\n" +
        "保存后需重启核心生效。" +
        '\n\n示例：[{"name":"自动选择","type":"urltest","filter":"","interval":"5m"}]',
    });
    if (v == null) return;
    let groups;
    try {
      groups = JSON.parse(v.trim() || "[]");
    } catch (e) {
      App.toast("JSON 解析失败：" + e.message, "error", 6000);
      return;
    }
    if (!Array.isArray(groups)) { App.toast("策略组必须是 JSON 数组", "error"); return; }
    const s = await App.tryCall("proxy_save_groups", groups);
    if (!s.ok) { App.toast(s.err, "error", 6000); return; }
    App.toast(`策略组已保存（${(s.data || []).length} 个）`, "ok");
    await refreshGroups();
  }

  /* ---------------- 连接监控 ---------------- */
  async function refreshConns() {
    if (!refs.conns) return;
    const r = await App.tryCall("proxy_connections");
    if (!r.ok) {
      refs.conns.replaceChildren(App.h("div", { class: "empty" },
        r.err || "读取连接失败（代理未运行）"));
      if (refs.connStat) refs.connStat.textContent = "";
      return;
    }
    const d = r.data || {};
    refs.conns.replaceChildren();
    if (refs.connStat) {
      refs.connStat.textContent =
        `活动 ${(d.connections || []).length} · 上传 ${App.fmtBytes(d.upload_total || 0)}` +
        ` · 下载 ${App.fmtBytes(d.download_total || 0)}`;
    }
    if (!(d.connections || []).length) {
      refs.conns.appendChild(App.h("div", { class: "empty" }, "暂无活动连接"));
      return;
    }
    d.connections.forEach((c) => {
      refs.conns.appendChild(App.h("div", { class: "list-item" },
        App.h("span", { class: "li-main" },
          App.h("span", { class: "li-title" }, App.esc(c.host || c.dest || "(未知目标)")),
          App.h("span", { class: "li-sub mono" },
            `${c.network || "tcp"} · ${(c.chains && c.chains.length) ? c.chains.join(" → ") : "—"}` +
            (c.process ? ` · ${c.process}` : ""))),
        App.h("span", { class: "hint", style: { flex: "none" } },
          `↑${App.fmtBytes(c.upload || 0)} ↓${App.fmtBytes(c.download || 0)}`),
        App.h("button", {
          class: "btn sm danger", title: "关闭此连接",
          onclick: async () => {
            const s = await App.tryCall("proxy_close_connection", c.id);
            if (!s.ok) { App.toast(s.err, "error", 5000); return; }
            await refreshConns();
          },
        }, "关闭"),
      ));
    });
  }

  async function closeAllConns() {
    if (!(await App.confirm("关闭全部连接", "确定关闭所有活动连接吗？"))) return;
    const r = await App.tryCall("proxy_close_all_connections");
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast("已关闭全部连接", "ok");
    await refreshConns();
  }

  async function importSub() {
    const url = refs.subUrl.value.trim();
    if (!url) { App.toast("请输入订阅地址。"); return; }
    const r = await App.tryCall("proxy_import_sub", url);
    if (!r.ok) { App.toast(r.err, "error", 8000); return; }
    App.toast(`订阅导入成功：新增 ${r.data.added}，共 ${r.data.total} 个节点`, "ok");
    refs.subUrl.value = "";
    await refreshState();
  }

  async function importClipboard() {
    const r = await App.tryCall("proxy_import_clipboard");
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast(`剪贴板导入成功：新增 ${r.data.added}，共 ${r.data.total} 个节点`, "ok");
    await refreshState();
  }

  async function pickAndImportDir() {
    const r = await App.tryCall("proxy_pick_dir");
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    if (!r.data) return;
    const s = await App.tryCall("proxy_import_dir", r.data);
    if (!s.ok) App.toast(s.err, "error", 8000);
    else App.toast(`V2rayN 目录导入成功：新增 ${s.data.added}，共 ${s.data.total} 个节点`, "ok");
    const b = await App.tryCall("proxy_set_bin_from_dir", r.data);
    if (b.ok) App.toast("已从该目录识别核心程序", "ok");
    await refreshState();
  }

/* 复刻 Clash Verge：策略组 / 连接监控 / 订阅自动更新（插入 v2ray.js 用）。 */

  /* ---------------- 订阅自动更新 ----------------------------------------- */
  async function setSubAuto(ev) {
    const hours = parseFloat(ev.target.value) || 0;
    const r = await App.tryCall("proxy_set_sub_autoupdate", hours);
    if (!r.ok) { App.toast(r.err, "error", 5000); return; }
    App.toast(hours > 0 ? `订阅将每 ${hours} 小时自动更新` : "已关闭订阅自动更新", "ok", 4000);
    await refreshState();
  }

  async function updateSubNow() {
    const url = ((state.st || {}).sub_url || "").trim();
    if (!url) { App.toast("还没有订阅地址：先在「节点导入」填入并导入一次", "warn", 5000); return; }
    const r = await App.tryCall("proxy_import_sub", url);
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast(`订阅已更新：新增 ${r.data.added}，共 ${r.data.total} 个节点`, "ok", 5000);
    await refreshState();
  }

  async function downloadBin() {
    const kind = (state.st && state.st.core_type) || "xray";
    const r = await App.tryCall("proxy_download_bin", kind);
    if (!r.ok) { App.toast(r.err, "error", 8000); return; }
    App.toast(`${coreLabel(kind)} ${r.data.version} 就绪`, "ok");
    await refreshState();
  }

  async function downloadGeo() {
    const kind = (state.st && state.st.core_type) || "xray";
    const r = await App.tryCall("proxy_download_geo", kind);
    if (!r.ok) { App.toast(r.err, "error", 8000); return; }
    const ok = (r.data || []).length;
    // 部分文件失败时后端仍返回 ok:true，err 在顶层
    if (r.err) {
      App.toast(`规则库部分失败（${ok} 个成功）：${r.err}`, "warn", 8000);
    } else {
      App.toast(`规则库下载完成：${ok} 个文件`, "ok");
    }
    await refreshState();
  }

  async function pickBin() {
    const r = await App.tryCall("proxy_pick_bin");
    if (r.ok && r.data) App.toast("核心程序已设置", "ok");
    else if (!r.ok) App.toast(r.err, "error", 6000);
    await refreshState();
  }

  async function openDataDir() {
    const r = await App.tryCall("proxy_open_dir");
    if (!r.ok) App.toast(r.err, "error", 6000);
  }

  async function toggleAuto(ev) {
    const r = await App.tryCall("proxy_set_auto_switch", ev.target.checked);
    if (!r.ok) { ev.target.checked = !ev.target.checked; App.toast(r.err, "error", 6000); }
  }

  /* ---------------- 页面注册 ---------------- */
  App.registerPage({
    id: "v2ray",
    title: "V2rayN",
    icon: "zap",
    group: "代理网络",

    async mount(el) {
      refs = {};

      refs.binVal = App.h("span", { class: "mono", style: { color: "var(--muted)" } }, "…");
      refs.httpPort = App.h("input", {
        class: "input", type: "number", min: 1, max: 65535, value: 10809,
        style: { width: "90px" },
      });
      refs.socksPort = App.h("input", {
        class: "input", type: "number", min: 1, max: 65535, value: 10808,
        style: { width: "90px" },
      });
      refs.status = App.h("span", null);
      refs.startBtn = App.h("button", { class: "btn primary", onclick: doStart }, "启动代理");
      refs.stopBtn = App.h("button", { class: "btn", disabled: true, onclick: doStop }, "停止");

      /* 三核心 / 代理模式 / TUN / 系统代理策略 / 测速 / 高级配置 */
      refs.coreSel = App.h("select", { class: "input", onchange: doSetCore }, [
        App.h("option", { value: "xray" }, "Xray"),
        App.h("option", { value: "sing-box" }, "Sing-Box"),
        App.h("option", { value: "v2ray" }, "V2Ray"),
      ]);
      refs.modeSel = App.h("select", { class: "input", onchange: doSetMode }, [
        App.h("option", { value: "global" }, "全局模式"),
        App.h("option", { value: "smart" }, "智能分流"),
        App.h("option", { value: "direct" }, "直连模式"),
      ]);
      refs.tunTgl = App.h("input", { type: "checkbox", onchange: doSetTun });
      refs.sysProxySel = App.h("select", { class: "input", onchange: doSetSysProxy }, [
        App.h("option", { value: "auto" }, "自动配置"),
        App.h("option", { value: "pac" }, "PAC 模式"),
        App.h("option", { value: "none" }, "不改变"),
        App.h("option", { value: "clear" }, "清除"),
      ]);
      refs.burstBtn = App.h("button", { class: "btn", onclick: doBurstTest }, "批量测速");
      refs.advancedBtn = App.h("button", { class: "btn", onclick: openAdvanced }, "高级配置");
      refs.autoTgl = App.h("input", { type: "checkbox", checked: true });

      refs.speed = App.h("span", { class: "mono", style: { color: "var(--muted)" } }, "↑ 0/s ↓ 0/s");
      refs.speedRow = App.h("div", null,
        App.h("span", { class: "field-label" }, "流量："), refs.speed,
        App.h("span", { class: "hint", style: { marginLeft: "8px" } }, "系统级采样（三核心通用）"),
      );

      refs.nodeCount = App.h("b", null, "0");
      refs.curNode = App.h("span", { class: "mono", style: { color: "var(--muted)" } }, "—");
      refs.log = App.h("div", { class: "log-box", style: { maxHeight: "90px" } },
        "代理服务日志…");
      log = App.makeLog(refs.log);

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "V2rayN"),
        App.h("div", { class: "sub" },
          "导入 V2rayN 节点（订阅 / 配置目录 / 分享链接），应用内自跑代理核心，节点失效自动切换重连并管理系统代理")));

      /* L3 分区容器（subnav 页签互斥显示）：运行控制 / 节点导入 / 节点列表 */
      const paneRun = App.h("div");
      const paneImport = App.h("div");
      const paneNodes = App.h("div");
      const paneGroups = App.h("div");
      const paneConns = App.h("div");

      /* 页签 1：核心与运行控制（主任务：启动 / 停止 / 模式） */
      refs.dlBtn = App.h("button", { class: "btn", onclick: downloadBin }, "自动下载核心");
      refs.geoBtn = App.h("button", { class: "btn", onclick: downloadGeo }, "下载规则库");
      refs.refreshBtn = App.h("button", {
        class: "btn", onclick: refreshBins,
        title: "重新探测核心程序（含手动放置）并检查是否有新版本，不下载",
      }, "检查更新");
      refs.geoVal = App.h("span", { class: "hint", style: { color: "var(--muted)" } }, "");
      paneRun.appendChild(App.svcCard(
        "核心与运行控制",
        [
          App.row(
            App.h("span", { class: "field-label" }, "核心程序："), refs.binVal,
            /* v5.2 P4：核心维护类（选择/下载/更新/数据目录）收进「内核维护 ⋯」 */
            App.h("button", {
              class: "btn", title: "核心程序与规则库维护",
              onclick: (ev) => App.overflowMenu(ev.currentTarget, [
                { label: "自动下载核心", icon: "arrowup",
                  hint: "从 GitHub 下载",
                  onclick: () => downloadBin() },
                { label: "下载规则库", icon: "arrowup",
                  hint: "geoip / geosite / .srs",
                  onclick: () => downloadGeo() },
                { label: "检查更新", icon: "search",
                  hint: "重新探测并检查新版本",
                  onclick: () => refreshBins() },
                "sep",
                { label: "选择核心程序…", icon: "filetext", onclick: pickBin },
                { label: "打开数据目录", icon: "folder", onclick: openDataDir },
              ]) }, "内核维护 ⋯"),
          ),
          App.row(
            App.h("span", { class: "field-label" }, "规则库："), refs.geoVal,
            App.h("span", { class: "hint" }, "智能分流需规则库：Xray 用 geoip.dat/geosite.dat，Sing-Box 用 .srs 规则集（geoip-cn / geosite-cn 等）"),
          ),
          App.row(
            App.h("span", { class: "field-label" }, "核心类型："), refs.coreSel,
            App.h("span", { class: "field-label" }, "代理模式："), refs.modeSel,
            App.h("label", { class: "switch", title: "TUN 仅 Sing-Box 支持，且需管理员权限 + wintun.dll" },
              refs.tunTgl, App.h("span", { class: "track" }), "TUN 模式"),
          ),
          App.row(
            App.h("span", { class: "field-label" }, "HTTP 端口："), refs.httpPort,
            App.h("span", { class: "field-label" }, "SOCKS5 端口："), refs.socksPort,
            App.h("span", { class: "hint" }, "本机代理端口（不可与外网节点端口重复）"),
          ),
          App.row(
            App.h("label", { class: "switch" }, refs.autoTgl, App.h("span", { class: "track" }),
              "失败自动切换节点"),
            App.h("span", { class: "field-label" }, "系统代理策略："), refs.sysProxySel,
            App.h("span", { class: "hint" },
              "对齐 v2rayN：自动配置 / PAC / 不改变 / 清除"),
          ),
          refs.speedRow,
          App.h("div", { class: "hint", style: { whiteSpace: "pre-line", marginTop: "8px" } },
            "自跑 Xray / Sing-Box / V2Ray 核心，启动后本机 HTTP/SOCKS5 端口即为代理入口；系统代理随策略写入并在停止时还原。\n" +
            "全局=全部流量走代理；智能分流=国内+局域网直连，其余走代理；直连=停止核心。\n" +
            "节点不可用时按「失败自动切换」依次尝试，全部失败则停止并还原系统设置。"),
        ],
        [refs.startBtn, refs.stopBtn, refs.status],
        refs.log,
      ));

      /* 页签 2：节点导入 */
      refs.subUrl = App.h("input", {
        class: "input grow-in", placeholder: "订阅地址 http(s):// ...（base64 或分享链接列表）",
      });
      paneImport.appendChild(App.svcCard(
        "节点导入（订阅 / 剪贴板分享链接 / V2rayN 目录）",
        [
          App.row(refs.subUrl,
            App.h("button", { class: "btn primary", onclick: importSub }, "导入订阅"),
          ),
          App.row(
            App.h("button", { class: "btn", onclick: importClipboard }, "从剪贴板导入"),
            App.h("button", { class: "btn", onclick: pickAndImportDir }, "从 V2rayN 目录导入..."),
            App.h("button", {
              class: "btn", onclick: async () => {
                const text = await App.prompt("粘贴分享链接", "", "支持 vless:// vmess:// trojan:// ss:// hy2:// tuic:// wireguard:// anytls:// 等，可多行");
                if (!text) return;
                const r = await App.tryCall("proxy_import_text", text);
                if (!r.ok) App.toast(r.err, "error", 6000);
                else App.toast(`导入成功：新增 ${r.data.added}，共 ${r.data.total} 个节点`, "ok");
                await refreshState();
              },
            }, "手动粘贴..."),
          ),
          App.row(
            App.h("span", { class: "field-label" }, "自动更新："),
            refs.subAuto = App.h("select", { class: "input", style: { width: "150px" } },
              App.h("option", { value: "0" }, "关闭"),
              App.h("option", { value: "6" }, "每 6 小时"),
              App.h("option", { value: "12" }, "每 12 小时"),
              App.h("option", { value: "24" }, "每天"),
              App.h("option", { value: "72" }, "每 3 天")),
            App.h("button", { class: "btn sm", onclick: updateSubNow }, "立即更新订阅"),
            refs.subInfo = App.h("span", { class: "hint" }, ""),
          ),
          App.h("div", { class: "hint" },
            "节点保存在应用数据目录 proxy/nodes.json，不写入 V2rayN。" +
            "开启自动更新后按周期重拉订阅地址（失败只记日志，不影响使用）。"),
        ],
        [],
      ));
      refs.subAuto.addEventListener("change", setSubAuto);

      /* 页签 3：节点列表（搜索 / 排序 / 批量操作归属此页签） */
      const searchIn = App.h("input", {
        class: "input", type: "search", placeholder: "搜索备注 / 地址 / 协议…",
        style: { flex: "1", minWidth: "160px" },
      });
      searchIn.addEventListener("input", () => {
        state.filter = searchIn.value;
        renderNodes();
      });
      const sortSel = App.h("select", { class: "input", style: { width: "150px" } },
        App.h("option", { value: "default" }, "默认顺序"),
        App.h("option", { value: "latency" }, "延迟从低到高"),
        App.h("option", { value: "latency-desc" }, "延迟从高到低"),
        App.h("option", { value: "name" }, "按名称"),
      );
      sortSel.addEventListener("change", () => {
        state.sort = sortSel.value;
        renderNodes();
      });
      refs.nodeSearch = searchIn;
      refs.nodeSort = sortSel;
      refs.nodeShown = App.h("span", { class: "hint" }, "");
      refs.nodes = App.h("div", { class: "list", style: { minHeight: "180px", maxHeight: "420px", overflowY: "auto" } });
      paneNodes.appendChild(App.svcCard("节点列表（测试 / 应用 / 删除）",
        [
          App.row(
            App.h("span", { class: "field-label" }, "节点数："), App.h("span", null, refs.nodeCount),
            App.h("span", { class: "field-label" }, "当前节点："), refs.curNode,
          ),
          App.row(searchIn, sortSel, refs.nodeShown),
          refs.nodes,
          App.row(
            App.h("span", { class: "field-label" }, "批量操作："), refs.burstBtn,
            refs.advancedBtn,
            App.h("span", { class: "hint" }, "双击行=应用该节点；批量测速=全部节点测延迟并自动按延迟排序；高级配置=路由/DNS 自定义"),
          ),
        ],
        [App.h("button", { class: "btn", onclick: () => refreshState() }, "刷新")],
      ));

      /* 页签 4：策略组（Clash Verge 风格分组 + 运行中切换） */
      refs.groups = App.h("div", { style: { display: "flex", flexDirection: "column", gap: "10px" } });
      paneGroups.appendChild(App.svcCard(
        "策略组（Clash Verge 风格）",
        [
          App.h("div", { class: "hint" },
            "按关键字把节点归入策略组：selector 组可在运行中手动切换成员，urltest 组自动选择延迟最低者。" +
            "策略组定义改动需重启核心生效。"),
          refs.groups,
        ],
        [
          App.h("button", { class: "btn", onclick: editGroups }, "编辑策略组"),
          App.h("button", { class: "btn", onclick: () => refreshGroups() }, "刷新"),
        ],
      ));

      /* 页签 5：连接监控（活动连接 + 实时流量 + 单条/全部关闭） */
      refs.connStat = App.h("span", { class: "hint", style: { color: "var(--muted)" } }, "");
      refs.conns = App.h("div", { class: "list", style: { minHeight: "180px", maxHeight: "420px", overflowY: "auto" } });
      paneConns.appendChild(App.svcCard(
        "连接监控",
        [
          App.row(App.h("span", { class: "field-label" }, "统计："), refs.connStat),
          refs.conns,
        ],
        [
          App.h("button", { class: "btn", onclick: () => refreshConns() }, "刷新"),
          App.h("button", { class: "btn danger", onclick: closeAllConns }, "全部关闭"),
        ],
      ));

      const nav = App.subnav([
        { label: "运行控制", el: paneRun },
        { label: "节点导入", el: paneImport },
        { label: "节点列表", el: paneNodes },
        { label: "策略组", el: paneGroups },
        { label: "连接监控", el: paneConns },
      ], {
        onSwitch: (i) => {
          if (i === 3) refreshGroups();
          else if (i === 4) refreshConns();
        },
      });
      el.appendChild(nav);
      nav.panes.forEach((p) => el.appendChild(p));

      App.on("proxy_log", (m) => log(String(m)));
      App.on("proxy_bins", (d) => onBinsInfo(d));
      App.on("proxy_state", (m) => {
        state.st = Object.assign(state.st || {}, m || {});
        renderControls(state.st);   // 自动切换等状态变化同步刷新状态签/按钮
        renderNodes();
      });
      App.on("proxy_traffic", (m) => {
        if (!m) return;
        refs.speed.textContent = `↑ ${App.fmtBytes(m.up)}/s ↓ ${App.fmtBytes(m.down)}/s`;
      });
      App.on("proxy_task", (m) => {
        if (m && m.status === "running") {
          log(`下载 ${m.name}：${App.fmtBytes(m.done)}/${App.fmtBytes(m.total) || "?"}`);
        } else if (m && m.status === "done") {
          log(`核心程序下载完成：${m.name}`);
        }
      });

      await refreshState();
    },

    show() {
      refreshState();
    },
  });
})();