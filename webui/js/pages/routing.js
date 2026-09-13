/* 分流规则（统一规则中心）：V2rayN（xray/sing-box/v2ray）与 Clash（mihomo）共用。
 *
 * 单页面 + 页内页签（App.subnav 互斥区块），归入「代理网络」分组（Clash 之后）：
 *   规则集     —— 精选规则集开关（GitHub 开源数据）+ 三内核就绪态
 *   自定义规则 —— 统一条目编辑（一处编辑双引擎生效）
 *   生效预览   —— 三内核编译产物 + 警告
 *   更新维护   —— 数据源（主源/备源自动回退）、自动更新、进度日志
 * 后端：routing_*（app/bridge/routing_api.py）→ app/core/routing_core.py。
 */
(function () {
  "use strict";

  const GROUP = "代理网络";
  const TYPES = ["DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "GEOSITE",
                 "IP-CIDR", "IP-CIDR6", "GEOIP", "DST-PORT", "PROCESS-NAME"];
  const POLICIES = ["DIRECT", "PROXY", "REJECT"];

  const state = { st: null, entries: [], wired: false };
  let refs = {};
  let log = null;

  /* ---------------- 状态 ---------------- */
  function readyTag(ok, label) {
    return App.statusTag(label + (ok ? " ✓" : " 缺"), ok ? "ok" : "warn", ok ? "check" : "x");
  }

  function engineTag(e) {
    if (!e) return App.statusTag("—", "", "info");
    return e.running ? App.statusTag("运行中", "ok", "check")
                     : App.statusTag("未运行", "", "info");
  }

  function renderStatus() {
    if (!refs.st || !state.st) return;
    const st = state.st;
    refs.engines.replaceChildren(
      App.h("span", { class: "field-label" }, "V2rayN："),
      engineTag(st.engines && st.engines.v2rayn),
      st.engines && st.engines.v2rayn && st.engines.v2rayn.running
        ? App.h("span", { class: "hint" }, "（" + (st.engines.v2rayn.core || "") + "）") : null,
      App.h("span", { class: "field-label", style: { marginLeft: "10px" } }, "Clash："),
      engineTag(st.engines && st.engines.clash),
      App.h("span", { class: "field-label", style: { marginLeft: "10px" } }, "规则库："),
      readyTag(st.geo_ready, "dat"),
    );
    refs.managedHint.textContent = st.managed && st.managed.v2rayn === "expert"
      ? "⚠ V2rayN 高级配置为专家模式（routing 为对象），已接管路由；统一规则不写入 V2rayN 侧"
      : (st.managed && st.managed.v2rayn === "unified"
          ? "统一规则已接管两个引擎的路由；旧入口（高级配置 routing / Clash 自定义规则）的修改会被下次保存覆盖"
          : "");
    if (refs.updState) {
      const u = st.update || {};
      refs.updState.textContent = u.last_ts
        ? "上次更新：" + App.fmtDate(u.last_ts)
        : "尚未更新过规则库";
    }
  }

  async function refreshState() {
    const r = await App.tryCall("routing_get_state");
    if (!r.ok) return;
    state.st = r.data;
    renderStatus();
    renderPresets();
    renderUpdateOpts();
  }

  function wire() {
    if (state.wired) return;
    state.wired = true;
    App.on("routing_log", (m) => { if (log) log(String(m)); });
    App.on("routing_update_progress", (m) => {
      if (!m) return;
      if (m.status === "summary") {
        if (m.ok) App.toast(`规则库更新完成：${m.oks}/${m.total} 个文件就绪`, "ok", 6000);
        else App.toast(`规则库更新完成（部分失败）：${(m.errs || []).slice(0, 2).join("；")}`,
          "warn", 8000);
        refreshState();
        return;
      }
      if (m.status === "running" && log) {
        log(`下载 ${m.name}：${App.fmtBytes(m.done)}/${App.fmtBytes(m.total) || "?"}`);
      }
    });
  }

  /* ---------------- 页签 1：规则集 ---------------- */
  function renderPresets() {
    if (!refs.presetBox || !state.st) return;
    const box = refs.presetBox;
    box.innerHTML = "";
    for (const p of (state.st.presets || [])) {
      const tgl = App.h("input", { type: "checkbox",
        onchange: async (ev) => {
          const r = await App.tryCall("routing_set_preset", p.key, ev.target.checked);
          if (!r.ok) { ev.target.checked = !ev.target.checked; App.toast(r.err, "error", 6000); return; }
          const a = (r.data || {}).applied || {};
          App.toast(`「${p.name}」已${ev.target.checked ? "启用" : "停用"}（` +
            `Clash ${a.mihomo === "hot" ? "已热生效" : "下次启动生效"}；` +
            `V2rayN ${a.v2rayn === "skipped_expert" ? "专家模式跳过" : "下次启动生效"}）`, "ok", 6000);
          await refreshState();
        } });
      tgl.checked = !!p.on;    // checked 是 DOM 属性：setAttribute("checked", false) 会误勾选
      box.appendChild(App.h("div", { class: "list-item", style: { gap: "8px" } },
        App.h("label", { class: "switch" }, tgl, App.h("span", { class: "track" })),
        App.h("span", { class: "li-main" },
          App.h("span", { class: "li-title" }, p.name,
            App.h("span", { class: "tag " + (p.policy === "REJECT" ? "danger" : (p.policy === "DIRECT" ? "" : "accent")),
              style: { marginLeft: "8px" } }, p.policy)),
          App.h("span", { class: "li-sub" }, p.desc)),
        App.h("span", { style: { flex: "none", display: "flex", gap: "4px" } },
          readyTag(p.ready.xray, "Xray"),
          readyTag(p.ready.singbox, "sing-box"),
          readyTag(p.ready.mihomo, "Clash")),
      ));
    }
  }

  /* ---------------- 页签 2：自定义规则 ---------------- */
  function renderEntries() {
    if (!refs.entryBox) return;
    const box = refs.entryBox;
    box.innerHTML = "";
    state.entries.forEach((e, i) => {
      const typeSel = App.h("select", { class: "input", style: { width: "140px", flex: "none" } },
        TYPES.map((t) => App.h("option", { value: t }, t)));
      typeSel.value = e.type;
      typeSel.addEventListener("change", () => { e.type = typeSel.value; });
      const valIn = App.h("input", { class: "input", value: e.value || "",
        placeholder: "值（如 example.com / cn / 443）", style: { flex: "1", minWidth: "140px" } });
      valIn.addEventListener("input", () => { e.value = valIn.value; });
      const polSel = App.h("select", { class: "input", style: { width: "104px", flex: "none" } },
        POLICIES.map((t) => App.h("option", { value: t }, t)));
      polSel.value = e.policy || "PROXY";
      polSel.addEventListener("change", () => { e.policy = polSel.value; });
      const en = App.h("input", { type: "checkbox",
        title: "启用/停用（停用=保留条目不生效）",
        onchange: () => { e.enabled = en.checked; } });
      en.checked = e.enabled !== false;   // DOM 属性赋值，避免 setAttribute 误勾选
      const move = (d) => {
        const j = i + d;
        if (j < 0 || j >= state.entries.length) return;
        [state.entries[i], state.entries[j]] = [state.entries[j], state.entries[i]];
        renderEntries();
      };
      /* v5.3：此前 7 个元素挤在一条 list-item 里（下拉+值输入+下拉+3 个按钮），
         改两行：第一行选择与排序操作，第二行"值"输入独占宽度 */
      box.appendChild(App.h("div", { class: "list-item", style: { gap: "6px", flexWrap: "wrap" } },
        App.h("label", { class: "chk", title: "启用" }, en),
        typeSel, polSel,
        App.h("span", { class: "grow" }),
        App.h("button", { class: "btn sm", title: "上移（优先级更高）", onclick: () => move(-1) }, "↑"),
        App.h("button", { class: "btn sm", title: "下移", onclick: () => move(1) }, "↓"),
        App.h("button", { class: "btn sm danger", title: "删除",
          onclick: () => { state.entries.splice(i, 1); renderEntries(); } }, "删"),
        App.h("div", { style: { width: "100%", display: "flex", alignItems: "center", gap: "6px", marginTop: "2px" } },
          App.h("span", { class: "field-label" }, "值："), valIn),
      ));
    });
    if (!state.entries.length) {
      box.appendChild(App.h("div", { class: "empty" },
        "还没有自定义规则\n可用下方输入快速添加（如 DOMAIN-SUFFIX,example.com,REJECT）"));
    }
  }

  async function saveEntries() {
    const r = await App.tryCall("routing_save_custom", state.entries);
    if (!r.ok) { App.toast(r.err, "error", 8000); return; }
    const a = (r.data || {}).applied || {};
    const warns = (r.data || {}).warnings || [];
    App.toast("分流规则已保存并应用：Clash " + (a.mihomo === "hot" ? "已热生效" : "下次启动生效") +
      "；V2rayN " + (a.v2rayn === "skipped_expert" ? "专家模式，未写入" : "下次启动生效") +
      (warns.length ? "。注意：" + warns[0] : ""), warns.length ? "warn" : "ok", 7000);
    await refreshState();
  }

  function quickAdd() {
    const s = (refs.quickIn.value || "").trim();
    if (!s) { App.toast("请输入规则行，如 DOMAIN-SUFFIX,example.com,REJECT", "warn"); return; }
    const parts = s.split(",").map((x) => x.trim());
    if (parts.length < 2) { App.toast("格式：TYPE,VALUE[,POLICY]", "error", 5000); return; }
    let policy = "PROXY";
    if (parts.length >= 3 && POLICIES.indexOf(parts[parts.length - 1].toUpperCase()) >= 0) {
      policy = parts.pop().toUpperCase();
    }
    state.entries.push({ type: parts[0].toUpperCase(), value: parts.slice(1).join(","),
      policy, enabled: true, remark: "" });
    refs.quickIn.value = "";
    renderEntries();
  }

  /* ---------------- 页签 3：生效预览 ---------------- */
  async function refreshPreview() {
    const r = await App.tryCall("routing_preview");
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    const d = r.data || {};
    refs.preMihomo.textContent = ((d.mihomo || {}).lines || []).join("\n");
    refs.preXray.textContent = JSON.stringify(d.v2rayn || [], null, 2);
    refs.preSing.textContent = JSON.stringify(d.singbox || {}, null, 2);
    refs.preWarns.textContent = (d.warnings || []).join("\n") || "无警告";
  }

  /* ---------------- 页签 4：更新维护 ---------------- */
  function renderUpdateOpts() {
    if (!refs.st || !state.st) return;
    const u = (state.st.update || {});
    if (refs.autoTgl && document.activeElement !== refs.autoTgl) {
      refs.autoTgl.checked = !!u.auto;
    }
    if (refs.repoSel && document.activeElement !== refs.repoSel) {
      refs.repoSel.value = u.dat_repo || "metacubex";
    }
  }

  async function doUpdate() {
    const r = await App.tryCall("routing_check_update");
    if (!r.ok) { App.toast(r.err, "warn", 4000); return; }
    App.toast("规则库更新已开始（主源失败会自动切换备份源）…", "info", 4000);
  }

  /* ---------------- 页面注册 ---------------- */
  App.registerPage({
    id: "routing",
    title: "分流规则",
    icon: "layers",
    group: GROUP,

    async mount(el) {
      wire();
      refs = {};

      refs.engines = App.h("span", { style: { display: "inline-flex", alignItems: "center", gap: "6px", flexWrap: "wrap" } });
      refs.managedHint = App.h("div", { class: "hint", style: { whiteSpace: "pre-line", marginTop: "6px" } });
      refs.updState = App.h("span", { class: "hint" }, "");
      refs.log = App.h("div", { class: "log-box", style: { maxHeight: "150px" } }, "分流规则日志…");
      log = App.makeLog(refs.log);

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "分流规则"),
        App.h("div", { class: "sub" },
          "V2rayN 与 Clash 共用的统一分流规则中心：一处编辑、双引擎自动编译生效；" +
          "精选规则集来自 GitHub 开源数据（MetaCubeX/meta-rules-dat，每日构建），支持自动更新与备份源回退")));

      el.appendChild(App.svcCard("引擎与规则库状态",
        [App.row(refs.engines, App.h("span", { style: { flex: 1 } }), refs.updState),
         refs.managedHint],
        [App.h("button", { class: "btn", onclick: () => refreshState() }, "刷新")]));

      /* 页签 1：规则集 */
      refs.presetBox = App.h("div", { class: "list", style: { minHeight: "120px", maxHeight: "460px", overflowY: "auto" } });
      const panePresets = App.h("div");
      panePresets.appendChild(App.svcCard("精选规则集（启用后自动编译进两个引擎；sing-box 缺失类别会自动下载 .srs）",
        [refs.presetBox]));

      /* 页签 2：自定义规则 */
      refs.entryBox = App.h("div", { class: "list", style: { minHeight: "100px", maxHeight: "380px", overflowY: "auto" } });
      refs.quickIn = App.h("input", { class: "input grow-in", placeholder: "DOMAIN-SUFFIX,example.com,REJECT（一行一条）" });
      refs.quickIn.addEventListener("keydown", (ev) => { if (ev.key === "Enter") quickAdd(); });
      const paneCustom = App.h("div");
      paneCustom.appendChild(App.svcCard("自定义规则（用户顺序即优先级，置顶于精选集之前）",
        [refs.entryBox,
         App.row(refs.quickIn,
           App.h("button", { class: "btn sm", onclick: quickAdd }, "＋ 添加")),
         App.row(App.h("button", { class: "btn primary", onclick: saveEntries }, "保存并应用到两个引擎"),
           App.h("span", { class: "hint" }, "Clash 运行中=热生效；Xray/Sing-Box=下次启动核心生效"))]));

      /* 页签 3：生效预览 */
      refs.preWarns = App.h("div", { class: "hint", style: { whiteSpace: "pre-line" } }, "无警告");
      const mkPre = (h0) => App.h("div", { class: "log-box mono",
        style: { maxHeight: "260px", overflowY: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all" } }, h0);
      refs.preMihomo = mkPre("（点击「刷新预览」编译）");
      refs.preXray = mkPre("（点击「刷新预览」编译）");
      refs.preSing = mkPre("（点击「刷新预览」编译）");
      const panePreview = App.h("div");
      panePreview.appendChild(App.svcCard("mihomo（Clash）rules 行",
        [refs.preMihomo],
        [App.h("button", { class: "btn", onclick: () => refreshPreview() }, "刷新预览")]));
      panePreview.appendChild(App.svcCard("xray / v2ray routing（v2rayN 数组 → advanced.json）",
        [refs.preXray]));
      panePreview.appendChild(App.svcCard("sing-box route.rules（.srs 缺失的类别自动跳过）",
        [refs.preSing]));
      panePreview.appendChild(App.h("div", { class: "hint", style: { marginTop: "6px" } }, "编译警告："));
      panePreview.appendChild(refs.preWarns);

      /* 页签 4：更新维护 */
      refs.autoTgl = App.h("input", { type: "checkbox",
        onchange: async (ev) => {
          const r = await App.tryCall("routing_set_options", ev.target.checked, null, null);
          if (!r.ok) { ev.target.checked = !ev.target.checked; App.toast(r.err, "error", 5000); return; }
          App.toast(ev.target.checked ? "已开启规则库自动更新" : "已关闭规则库自动更新", "ok");
        } });
      refs.repoSel = App.h("select", { class: "input", style: { width: "220px" },
        onchange: async (ev) => {
          const r = await App.tryCall("routing_set_options", null, null, ev.target.value);
          if (!r.ok) { App.toast(r.err, "error", 5000); }
          else App.toast("规则数据源已保存：主源 " + (ev.target.value === "metacubex"
            ? "MetaCubeX/meta-rules-dat" : "Loyalsoldier/v2ray-rules-dat"), "ok", 5000);
          await refreshState();
        } },
        App.h("option", { value: "metacubex" }, "MetaCubeX/meta-rules-dat（每日构建）"),
        App.h("option", { value: "loyalsoldier" }, "Loyalsoldier/v2ray-rules-dat（备用仓库）"));
      const hoursSel = App.h("select", { class: "input", style: { width: "130px" },
        onchange: async (ev) => {
          const r = await App.tryCall("routing_set_options", null, parseInt(ev.target.value, 10), null);
          if (!r.ok) App.toast(r.err, "error", 5000);
        } },
        App.h("option", { value: "24" }, "每 24 小时"),
        App.h("option", { value: "72" }, "每 3 天"),
        App.h("option", { value: "168" }, "每 7 天"),
        App.h("option", { value: "720" }, "每 30 天"));
      const paneUpdate = App.h("div");
      paneUpdate.appendChild(App.svcCard("规则库更新（geosite.dat / geoip.dat / 各类别 .srs）",
        [App.row(App.h("span", { class: "field-label" }, "dat 主源："), refs.repoSel,
           App.h("span", { class: "hint" }, "下载失败自动切换到另一仓库作为备份源")),
         App.row(App.h("span", { class: "field-label" }, "自动更新："),
           App.h("label", { class: "switch" }, refs.autoTgl, App.h("span", { class: "track" })), hoursSel,
           App.h("span", { class: "hint" }, "按间隔检查；.srs 有 7 天缓存，「立即更新」会强制重下")),
         App.row(App.h("button", { class: "btn primary", onclick: doUpdate }, "立即更新"),
           App.h("button", { class: "btn", onclick: async () => {
             const r = await App.tryCall("routing_open_dir");
             if (!r.ok) App.toast(r.err, "error", 4000);
           } }, "打开规则目录"),
           App.h("span", { class: "hint" }, "数据目录 DATA_HOME/rules + proxy/bin")),
         refs.log]));

      const nav = App.subnav([
        { label: "规则集", el: panePresets },
        { label: "自定义规则", el: paneCustom },
        { label: "生效预览", el: panePreview },
        { label: "更新维护", el: paneUpdate },
      ]);
      el.appendChild(nav);
      nav.panes.forEach((p) => el.appendChild(p));

      await refreshState();
      const c = await App.tryCall("routing_get_custom");
      if (c.ok) { state.entries = (c.data || {}).entries || []; renderEntries(); }
    },

    show() { refreshState(); },
  });
})();
