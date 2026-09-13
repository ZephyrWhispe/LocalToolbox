/* 网盘挂载页：OpenList 服务托管 + WebDAV 文件浏览/上传/下载 + 盘符映射。 */
(function () {
  "use strict";

  const state = {
    cwd: "/",
    entries: [],
    sel: null,          /* 选中的文件条目 */
    running: false,
    mapped: [],         /* [{letter, target}] */
    tasks: new Map(),
    rclone: null,       /* v3.2：pan_rclone_state 结果 */
  };
  let refs = {};
  let log = null;

  /* ---------------- 工具 ---------------- */
  function joinPath(dir, name) {
    const d = String(dir || "/").replace(/\/+$/, "");
    const n = String(name || "").replace(/^\/+/, "");
    if (!n) return d || "/";
    return (d ? d + "/" : "/") + n;
  }

  function parentOf(p) {
    const s = String(p || "/").replace(/\/+$/, "").split("/");
    s.pop();
    return s.length ? s.join("/") || "/" : "/";
  }

  function fmtMtime(t) {
    return t ? String(t) : "";
  }

  function entryPath(name) {
    return joinPath(state.cwd, name);
  }

  /* ---------------- 渲染 ---------------- */
  function renderBreadcrumb() {
    refs.crumb.innerHTML = "";
    const parts = String(state.cwd || "/").split("/").filter(Boolean);
    let acc = "";
    refs.crumb.appendChild(App.h("button", {
      class: "linkbtn", onclick: () => openDir("/"),
    }, "根目录"));
    parts.forEach((p, i) => {
      acc = acc ? acc + "/" + p : "/" + p;
      refs.crumb.appendChild(App.h("span", { style: { color: "var(--faint)" } }, " / "));
      refs.crumb.appendChild(App.h("button", {
        class: "linkbtn" + (i === parts.length - 1 ? " cur" : ""),
        onclick: () => openDir(acc),
      }, App.esc(p)));
    });
    if (state.space && (state.space.used != null || state.space.avail != null)) {
      const used = state.space.used == null ? "?" : App.fmtBytes(state.space.used);
      const avail = state.space.avail == null ? "?" : App.fmtBytes(state.space.avail);
      refs.crumb.appendChild(App.h("span", { class: "hint", style: { marginLeft: "10px" } },
        `空间 已用${used} / 可用${avail}`));
    }
  }

  function renderEntries() {
    refs.tree.innerHTML = "";
    if (!state.running) {
      refs.tree.appendChild(App.h("div", { class: "empty" },
        "OpenList 未运行\n请先在上方卡片启动服务"));
      return;
    }
    if (state.cwd !== "/") {
      refs.tree.appendChild(App.h("div", {
        class: "list-item click", ondblclick: () => openDir(parentOf(state.cwd)),
        onclick: () => openDir(parentOf(state.cwd)),
      },
        App.h("span", { style: { color: "var(--faint)", flex: "none" } }, App.icon("folder", 14)),
        App.h("span", { class: "li-main" }, App.h("span", { class: "li-title" }, "..")),
      ));
    }
    if (!state.entries.length) {
      refs.tree.appendChild(App.h("div", { class: "empty" }, "（空目录）"));
      return;
    }
    for (const e of state.entries) {
      refs.tree.appendChild(App.h("div", {
        class: "list-item click" + (state.sel === e.path ? " sel" : ""),
        title: e.is_dir ? "双击进入目录" : "点击选中，可下载",
        onclick: () => { state.sel = e.is_dir ? null : e.path; renderEntries(); },
        ondblclick: () => { if (e.is_dir) openDir(e.path); },
      },
        App.h("span", { style: { color: "var(--faint)", flex: "none" } },
          App.icon(e.is_dir ? "folder" : "filetext", 14)),
        App.h("span", { class: "li-main" },
          App.h("span", { class: "li-title" }, App.esc(e.name)),
          App.h("span", { class: "li-sub" },
            e.is_dir ? "目录" : App.fmtBytes(e.size) + " · " + fmtMtime(e.mtime))),
        App.h("button", {
          class: "btn sm", style: { marginLeft: "8px", alignSelf: "center" },
          onclick: (ev) => {
            ev.stopPropagation();
            if (e.is_dir) openDir(e.path);
            else downloadEntry(e);
          },
        }, e.is_dir ? "打开" : "下载"),
      ));
    }
  }

  function renderMapped(list) {
    refs.mapped.innerHTML = "";
    if (!list.length) {
      refs.mapped.appendChild(App.h("div", { class: "empty" }, "尚未映射盘符"));
      return;
    }
    for (const m of list) {
      refs.mapped.appendChild(App.h("div", { class: "list-item" },
        App.h("span", { class: "li-main mono" },
          App.h("span", { class: "li-title" }, m.letter + ":  " + App.esc(m.target))),
        App.h("button", {
          class: "btn sm",
          onclick: async () => {
            const r = await App.tryCall("pan_unmap_drive", m.letter);
            if (!r.ok) App.toast(r.err, "error", 6000);
            refreshState();
          },
        }, "断开"),
      ));
    }
  }

  function renderTasks() {
    refs.tasks.innerHTML = "";
    if (!state.tasks.size) return;
    const STATUS_CN = {
      queued: "排队中", running: "进行中", done: "已完成",
      cancelled: "已取消", error: "失败",
    };
    for (const t of state.tasks.values()) {
      const pct = t.total > 0 ? Math.min(100, (t.done / t.total) * 100) : 0;
      const statusCn = STATUS_CN[t.status] || t.status;
      const line = `${t.kind} ${t.name}：${statusCn}` +
        (t.status === "running" && t.total > 0
          ? ` ${App.fmtBytes(t.done)}/${App.fmtBytes(t.total)}` : "") +
        (t.speed ? `（${App.fmtBytes(t.speed)}/s）` : "") +
        (t.err ? ` — ${t.err}` : "");
      const bar = App.h("div", {
        class: "taskbar",
        style: {
          width: (pct ? pct : 0) + "%",
          background: t.status === "error" ? "var(--danger)" : "var(--accent)",
        },
      });
      const rowEl = App.h("div", { class: "task-row" },
        App.h("span", { class: "li-main" }, line),
        t.status === "running"
          ? App.h("button", {
            class: "btn sm", onclick: () => tryCall2("pan_cancel", t.id),
          }, "取消") : null,
      );
      refs.tasks.appendChild(App.h("div", { class: "task-wrap" }, bar, rowEl));
    }
  }

  /* ---------------- 操作 ---------------- */
  async function openDir(path) {
    state.cwd = path;
    state.sel = null;
    await refreshBrowse();
  }

  async function refreshBrowse() {
    const r = await App.tryCall("pan_browse", state.cwd);
    if (!r.ok) {
      refs.tree.innerHTML = "";
      refs.tree.appendChild(App.h("div", { class: "empty" }, r.err));
      return;
    }
    state.entries = r.data.entries || [];
    state.space = { used: r.data.used, avail: r.data.avail };
    renderBreadcrumb();
    renderEntries();
  }

  async function downloadEntry(e) {
    const dir = (refs.saveDir.value || "").trim() ||
      (await App.tryCall("cfg_get")).data?.save_dir || "";
    const r = await App.tryCall("pan_download", e.path, dir);
    if (!r.ok) App.toast(r.err, "error", 6000);
    else log(`开始下载 ${e.name}`);
  }

  async function tryCall2(name, ...args) {
    const r = await App.tryCall(name, ...args);
    if (!r.ok) App.toast(r.err, "error", 6000);
    return r;
  }

  async function refreshState() {
    const r = await tryCall2("pan_get_state");
    if (!r.ok) return;
    state.running = r.data.running;
    refs.start.disabled = state.running;
    refs.stop.disabled = !state.running;
    const binPath = r.data.bin || (r.data.bin_builtin ? "（内置 runtime）" : (r.data.bin_cfg || ""));
    const binReady = !!(r.data.bin || r.data.bin_builtin);
    refs.binVal.replaceChildren(
      binReady
        ? App.statusTag("已配置", "ok", "check")
        : App.statusTag("未配置", "warn", "x"));
    refs.binVal.title = binPath || "未设置程序：点「自动下载」或「选择...」手动指定 openlist.exe";
    refs.stateTag.replaceChildren(
      state.running
        ? App.statusTag(`运行中（端口 ${r.data.port || "?"}·健康${r.data.healthy ? "✓" : "✗"}）`,
          r.data.healthy ? "ok" : "warn", "check")
        : App.statusTag("未运行"));
    renderMapped(r.data.mapped || []);
    state.tasks = new Map((r.data.tasks || []).map((t) => [t.id, t]));
    renderTasks();
  }

  let panBusy = false;   // 服务/下载类长耗时操作统一防重入
  function setPanBusy(b) {
    panBusy = b;
    for (const k of ["start", "stop", "dlBin", "restart", "mapBtn"]) {
      if (refs[k]) refs[k].disabled = b;
    }
    // 解锁后的原始 disabled 语义（运行中才可停止等）由随后的 refreshState 恢复
  }

  async function startServer() {
    if (panBusy) return;
    setPanBusy(true);
    try {
      const r = await tryCall2("pan_start",
        refs.host.value.trim() || "127.0.0.1",
        parseInt(refs.port.value, 10) || 15244,
        refs.fwTgl.checked);
      if (!r.ok) App.toast(r.err, "error", 6000);
    } finally {
      setPanBusy(false);
    }
    await refreshState();
    await refreshBrowse();
  }

  async function stopServer() {
    if (panBusy) return;
    setPanBusy(true);
    try {
      const r = await tryCall2("pan_stop");
      if (!r.ok) App.toast(r.err, "error", 6000);
    } finally {
      setPanBusy(false);
    }
    await refreshState();
    await refreshBrowse();
  }

  async function downloadBin() {
    if (panBusy) return;
    setPanBusy(true);
    try {
      const r = await tryCall2("pan_download_bin");
      if (!r.ok) { App.toast(r.err, "error", 8000); return; }
      App.toast(`OpenList ${r.data.version} 就绪`, "ok");
    } finally {
      setPanBusy(false);
    }
    await refreshState();
  }

  /* 刷新组件：重新探测 OpenList / rclone（立即），版本检查经 pan_bins 事件推送 */
  async function refreshBins() {
    const r = await tryCall2("pan_refresh_bins");
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    const d = r.data || {};
    App.toast("组件刷新：OpenList " + ((d.openlist || {}).found ? "已就绪" : "未找到") +
      "；rclone " + ((d.rclone || {}).found ? "已就绪" : "未找到") +
      "；正在检查是否有新版本…", "ok", 5000);
    await refreshState();
  }

  /* 版本检查结果（后台并行推送）：有新版提示点「自动下载」更新 */
  function onBinsInfo(payload) {
    if (!payload) return;
    const parts = [];
    for (const [name, label] of [["openlist", "OpenList"], ["rclone", "rclone"]]) {
      const it = payload[name] || {};
      if (it.outdated) parts.push(`${label} 有新版本：本地 ${it.current} → 最新 ${it.latest}，点「自动下载」更新`);
      else if (it.current && it.latest) parts.push(`${label} 已是最新（${it.current}）`);
      else if (it.check_err) parts.push(`${label} 版本检查失败（网络无法访问 GitHub）`);
    }
    if (parts.length) {
      App.toast(parts.join("；"), parts.some((x) => x.indexOf("有新版本") >= 0) ? "info" : "ok", 8000);
    }
  }

  async function pickAndImport() {
    const r = await tryCall2("pan_pick_import");
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    if (!r.data) return;
    const s = await tryCall2("pan_import_config", r.data);
    if (!s.ok) App.toast(s.err, "error", 6000);
    else App.toast("配置已导入，重启服务生效");
  }

  async function mapDrive() {
    if (panBusy) return;
    setPanBusy(true);
    try {
      const letter = refs.driveLetter.value.trim();
      const r = await tryCall2("pan_map_drive", letter, "", "", false);
      if (!r.ok) App.toast(r.err, "error", 6000);
      else App.toast(r.data);
      refs.driveLetter.value = "";
    } finally {
      setPanBusy(false);
    }
    await refreshState();
  }

  function openWeb() {
    App.tryCall("pan_open_web").then((r) => {
      if (!r.ok || !r.data) { App.toast((r && r.err) || "OpenList 未运行", "warn"); return; }
      /* WebView2 会在宿主侧拦截 window.open（点了没反应）→ 交后端用默认浏览器打开 */
      App.tryCall("open_external", r.data).then((r2) => {
        if (!r2.ok) App.toast(r2.err, "error", 5000);
      });
    });
  }

  /* ---------------- v3.2 服务监控 / Rclone / 更新 ---------------- */
  function fmtUptime(sec) {
    if (sec == null) return "—";
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? h + " 时 " : "") + (h || m ? m + " 分 " : "") + s + " 秒";
  }

  function renderMetrics(m) {
    if (!refs.metrics) return;
    refs.metrics.textContent = m
      ? `已运行 ${fmtUptime(m.uptime)} · CPU ${m.cpu == null ? "—" : m.cpu.toFixed(1) + "%"} · ` +
        `内存 ${m.mem_mb == null ? "—" : m.mem_mb.toFixed(0) + " MB"} · WebDAV 目录 ${m.drives ?? "—"}`
      : "";
  }

  async function restartServer() {
    if (panBusy) return;
    setPanBusy(true);
    try {
      const r = await tryCall2("pan_restart", refs.fwTgl.checked);
      if (!r.ok) App.toast(r.err, "error", 8000);
    } finally {
      setPanBusy(false);
    }
    await refreshState();
    await refreshBrowse();
  }

  async function refreshLog() {
    const r = await App.tryCall("pan_get_log", 100);
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    if (!r.data.content && refs && refs.log) {
      log("（数据目录暂无 .log 日志文件）");
      return;
    }
    const lines = String(r.data.content || "").split("\n").filter((x) => x.trim());
    log(`— 加载日志 ${r.data.file || "?"}（末尾 ${lines.length} 行）—`);
    for (const l of lines.slice(-40)) log(l.trim());
  }

  function renderRcloneMounts(list) {
    refs.rcMounts.innerHTML = "";
    if (!list || !list.length) {
      refs.rcMounts.appendChild(App.h("div", { class: "empty" }, "暂无挂载"));
      return;
    }
    for (const m of list) {
      refs.rcMounts.appendChild(App.h("div", { class: "list-item" },
        App.h("span", { class: "li-main mono" },
          App.h("span", { class: "li-title" }, App.esc(m.target)),
          App.h("span", { class: "li-sub" },
            `PID ${m.pid} · ${new Date(m.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })}`)),
        App.h("button", {
          class: "btn sm", onclick: async () => {
            const r = await App.tryCall("pan_rclone_umount", m.target);
            if (!r.ok) App.toast(r.err, "error", 6000);
            await refreshRclone();
          },
        }, "卸载"),
      ));
    }
  }

  async function refreshRclone() {
    const r = await App.tryCall("pan_rclone_state");
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    state.rclone = r.data;
    refs.rcWinfsp.replaceChildren(
      App.statusTag(r.data.winfsp ? "已安装" : "未安装", r.data.winfsp ? "ok" : "warn",
        r.data.winfsp ? "check" : "x"));
    refs.rcWinfsp.title = r.data.winfsp
      ? "WinFsp 驱动已安装（rclone 挂载必需）"
      : "WinFsp 驱动未安装：点「安装 WinFsp」（需管理员授权）后自动检测";
    refs.rcBin.replaceChildren(
      App.statusTag(r.data.bin_ready ? "已就绪" : "未就绪", r.data.bin_ready ? "ok" : "warn",
        r.data.bin_ready ? "check" : "x"));
    refs.rcBin.title = r.data.bin_ready
      ? (r.data.bin_cfg || r.data.bin_path || "已就绪（自动探测）")
      : "未找到 rclone.exe：点「自动下载」，或手动放置到应用数据目录";
    renderRcloneMounts(r.data.mounts || []);
    if (!refs.rcType.value || !["letter", "folder"].includes(refs.rcType.value)) {
      refs.rcType.value = r.data.mount_type || "letter";
    }
    refs.rcTarget.value = refs.rcTarget.value || r.data.mount_target || "";
    // 已保存的 WebDAV 用户名回填（密码后端不返回，属预期）
    if (!refs.rcUser.value.trim() && r.data.user) {
      refs.rcUser.value = String(r.data.user);
    }
  }

  async function mountRclone() {
    const type = refs.rcType.value;
    const targetObj = refs.rcTarget.value.trim();
    await App.tryCall("pan_rclone_set_cfg",
      type, targetObj, refs.rcUser.value.trim(), refs.rcPwd.value);
    const r = await App.tryCall("pan_rclone_mount", type, targetObj);
    if (!r.ok) App.toast(r.err, "error", 8000);
    else { log(`挂载成功：${r.data.target}`); App.toast(`已挂载 ${r.data.target}`, "ok"); }
    await refreshRclone();
  }

  async function installWinfsp() {
    const r = await App.tryCall("pan_winfsp_install");
    if (!r.ok) { App.toast(r.err, "error", 8000); return; }
    App.toast(r.data.msg || "WinFsp 安装处理完成", "ok");
    if (!r.data.installed) {
      App.toast("请确认 UAC 弹窗；安装完成后再次点「检测」或重新挂载", "warn", 8000);
    }
    await refreshRclone();
  }

  async function downloadRcloneBin() {
    const r = await tryCall2("pan_download_rclone");
    if (!r.ok) { App.toast(r.err, "error", 8000); return; }
    App.toast(`rclone ${r.data.version} 就绪`, "ok");
    await refreshRclone();
  }

  async function checkUpdates() {
    refs.updateBox.innerHTML = "";
    refs.updateBox.appendChild(App.h("div", { class: "hint" }, "正在查询 GitHub 最新版本…"));
    const r = await App.tryCall("pan_check_update");
    if (!r.ok) {
      refs.updateBox.replaceChildren(App.h("div", { class: "empty" }, r.err));
      return;
    }
    renderUpdates(r.data || {});
  }

  function renderUpdates(data) {
    refs.updateBox.innerHTML = "";
    const names = { openlist: "OpenList 服务", rclone: "rclone 挂载工具" };
    for (const kind of ["openlist", "rclone"]) {
      const info = data[kind] || {};
      const sub = info.error ? info.error
        : `最新 ${info.version}${info.cached ? ` · 本地已缓存 ${info.cached}` : " · 本地未下载"}`;
      const btn = App.h("button", {
        class: "btn sm",
        onclick: async () => {
          const r = await tryCall2(kind === "openlist" ? "pan_download_bin" : "pan_download_rclone");
          if (!r.ok) { App.toast(r.err, "error", 8000); return; }
          App.toast(`${names[kind]} ${r.data.version} 就绪`, "ok");
          await checkUpdates();
          await refreshState();
          await refreshRclone();
        },
      }, "下载 / 更新");
      refs.updateBox.appendChild(App.h("div", { class: "list-item" },
        App.h("span", { class: "li-main" },
          App.h("span", { class: "li-title" }, names[kind]),
          App.h("span", { class: "li-sub" }, sub)),
        btn,
      ));
    }
  }

  /* ---------------- 文件工具行 ---------------- */
  async function uploadToCwd() {
    const r = await tryCall2("pan_pick_local_file");
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    if (!r.data) return;
    const up = await tryCall2("pan_upload", r.data, state.cwd);
    if (!up.ok) App.toast(up.err, "error", 6000);
    else log(`上传任务已提交：${r.data.split(/[\\/]/).pop()} → ${state.cwd}`);
  }

  async function newFolder() {
    const name = await App.prompt("新建文件夹", "", "输入文件夹名称");
    if (!name || !String(name).trim()) return;
    const r = await tryCall2("pan_mkdir", joinPath(state.cwd, String(name).trim()));
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refreshBrowse();
  }

  async function renameEntry() {
    if (!state.sel) { App.toast("请先在列表中选择一个文件。"); return; }
    const src = state.sel;
    const oldName = src.split("/").pop();
    const name = await App.prompt("重命名", oldName);
    if (!name || !String(name).trim() || String(name).trim() === oldName) return;
    const dst = joinPath(parentOf(src), String(name).trim());
    const r = await tryCall2("pan_rename", src, dst);
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refreshBrowse();
  }

  async function deleteEntry() {
    if (!state.sel) { App.toast("请先在列表中选择一个文件。"); return; }
    const name = state.sel.split("/").pop();
    if (!(await App.confirm("删除", `确定删除 ${name} 吗？此操作不可恢复。`))) return;
    const r = await tryCall2("pan_delete", state.sel);
    if (!r.ok) App.toast(r.err, "error", 6000);
    state.sel = null;
    await refreshBrowse();
  }

  function paintAutoStart(on) {
    if (refs.autoStartBtn) {
      refs.autoStartBtn.textContent = `开机自启（当前${on ? "开" : "关"}）`;
    }
  }

  /* ---------------- 页面注册 ---------------- */
  App.registerPage({
    id: "pan",
    title: "网盘挂载",
    icon: "folder",
    group: "网盘挂载",

    async mount(el) {
      refs = {};

      refs.binVal = App.h("span", { class: "mono", style: { color: "var(--muted)" } }, "…");
      refs.host = App.h("input", { class: "input", value: "127.0.0.1", style: { width: "120px" } });
      refs.port = App.h("input", {
        class: "input", type: "number", min: 1, max: 65535, value: 15244,
        style: { width: "90px" },
      });
      refs.fwTgl = App.h("input", { type: "checkbox", checked: true });
      refs.start = App.h("button", { class: "btn primary", onclick: startServer }, "启动 OpenList");
      refs.stop = App.h("button", { class: "btn", disabled: true, onclick: stopServer }, "停止");
      refs.stateTag = App.h("span", null);
      refs.log = App.h("div", { class: "log-box", style: { maxHeight: "90px" } },
        "OpenList 服务日志…");
      log = App.makeLog(refs.log);
      refs.metrics = App.h("span", { class: "hint mono" });
      const autoTgl = App.h("input", {
        type: "checkbox",
        ...(App.state.cfg && App.state.cfg.openlist_autostart ? { checked: true } : {}),
      });
      autoTgl.addEventListener("change", async () => {
        const r = await App.tryCall("pan_set_autostart", "openlist", autoTgl.checked);
        if (!r.ok) App.toast(r.err, "error");
        else App.toast(autoTgl.checked ? "OpenList 将随本应用自动启动" : "已关闭随应用启动", "ok");
      });

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "网盘挂载"),
        App.h("div", { class: "sub" },
          "内置 OpenList 聚合 40+ 网盘（阿里云盘 / 百度网盘 / 夸克 / OneDrive / Google Drive 等），统一入口浏览与传输，可一键映射为本地盘符")));

      /* L3 分区容器（subnav 页签互斥显示） */
      const paneSvc = App.h("div");
      const paneBrowse = App.h("div");
      const paneMount = App.h("div");
      const paneUpdate = App.h("div");

      /* 卡片 1：OpenList 服务（v3.2：实时监控 / 重启 / 日志 / 自启动）
         v5.2 P4：维护类操作收进「维护 ⋯」菜单，常驻仅 启动/停止/管理界面 */
      paneSvc.appendChild(App.svcCard(
        "OpenList 服务（聚合 40+ 网盘驱动，统一 WebDAV 接口）",
        [
          App.row(
            App.h("span", { class: "field-label" }, "程序路径："), refs.binVal,
          ),
          App.row(
            App.h("span", { class: "field-label" }, "监听："), refs.host,
            App.h("span", { class: "field-label" }, "端口："), refs.port,
          ),
          App.row(App.h("span", { class: "field-label" }, "运行状态："), refs.metrics),
          App.row(
            App.h("label", { class: "switch" }, autoTgl, App.h("span", { class: "track" }),
              "OpenList 随本应用启动"),
            App.h("label", { class: "switch" }, refs.fwTgl, App.h("span", { class: "track" }),
              "放行防火墙端口"),
            App.h("button", { class: "btn", onclick: openWeb }, "打开管理界面"),
          ),
          App.h("div", { class: "hint", style: { whiteSpace: "pre-line", marginTop: "8px" } },
            "说明：OpenList 是开源自托管网关，支持阿里云盘 / 百度网盘 / 夸克 / OneDrive 等 40+ 网盘。\n" +
            "首次使用：启动服务后点「打开管理界面」添加网盘账号（或在数据目录放置 openlist.json 后点「导入配置」）。\n" +
            "驱动授权通过管理界面完成；服务异常退出时系统托盘会收到通知。"),
        ],
        [refs.start, refs.stop,
          App.h("button", {
            class: "btn", title: "程序维护",
            onclick: (ev) => App.overflowMenu(ev.currentTarget, [
              { label: "重启服务", icon: "refresh", onclick: restartServer },
              { label: "刷新日志", icon: "list", onclick: refreshLog },
              "sep",
              { label: "导入配置…", icon: "folder", hint: "openlist.json",
                onclick: pickAndImport },
              "sep",
              { label: "选择程序…", icon: "filetext",
                onclick: async () => {
                  const p = await App.tryCall("pan_pick_bin");
                  if (p.ok && p.data) await tryCall2("pan_set_bin", p.data);
                  await refreshState();
                } },
              { label: "自动下载内核", icon: "arrowup",
                hint: "约 30MB",
                onclick: downloadBin },
              { label: "检查更新", icon: "search",
                hint: "重新探测程序并检查新版本",
                onclick: refreshBins },
            ]) }, "维护 ⋯"),
          refs.stateTag],
        refs.log,
      ));

      /* 卡片 2：盘符映射（归属「本地挂载」分区） */
      const mappedHead = App.h("div", { id: "pan-mapped", style: { maxHeight: "140px", overflowY: "auto" } });
      refs.mapped = mappedHead;
      refs.driveLetter = App.h("input", {
        class: "input", placeholder: "盘符（如 E）", maxlength: 1,
        style: { width: "110px" },
      });
      paneMount.appendChild(App.svcCard(
        "映射盘符（把已挂载网盘映射为本地磁盘）",
        [
          App.row(
            App.h("span", { class: "field-label" }, "盘符："), refs.driveLetter,
            App.h("button", { class: "btn primary", onclick: mapDrive }, "映射"),
            App.h("div", { class: "hint" },
              "映射对象为 OpenList 的 WebDAV 根目录，映射后可在资源管理器中直接使用。若系统提示需要 WebClient 服务，请以管理员运行：net start webclient"),
          ),
          mappedHead,
        ],
        [],
      ));

      /* 卡片 3（v3.2）：Rclone 本地挂载 */
      refs.rcWinfsp = App.h("span", null);
      refs.rcBin = App.h("span", { class: "mono", style: { color: "var(--muted)" } }, "探测中…");
      refs.rcType = App.h("select", { class: "input" },
        App.h("option", { value: "letter" }, "盘符"),
        App.h("option", { value: "folder" }, "文件夹"),
      );
      refs.rcTarget = App.h("input", {
        class: "input grow-in", placeholder: "盘符字母（如 V）或文件夹路径",
      });
      refs.rcUser = App.h("input", {
        class: "input", placeholder: "账号（留空 = 匿名）", style: { width: "120px" },
      });
      refs.rcPwd = App.h("input", {
        class: "input", type: "password", placeholder: "密码", style: { width: "120px" },
      });
      refs.rcMounts = App.h("div", { style: { maxHeight: "140px", overflowY: "auto" } }, null);
      paneMount.appendChild(App.svcCard(
        "Rclone 本地挂载（把 OpenList 网盘挂载为盘符 / 文件夹）",
        [
          /* v5.3：这两行此前分别塞了 9 个和 8 个元素（状态/按钮/参数全挤一行），
             换行后每个控件都很窄。按语义拆开：依赖状态行 / 挂载参数行 / 账号行 */
          App.row(
            App.h("span", { class: "field-label" }, "WinFsp 驱动："), refs.rcWinfsp,
            App.h("button", { class: "btn", onclick: async () => refreshRclone() }, "检测"),
            App.h("button", { class: "btn", onclick: installWinfsp }, "安装 WinFsp"),
          ),
          App.row(
            App.h("span", { class: "field-label" }, "rclone："), refs.rcBin,
            App.h("button", { class: "btn", onclick: downloadRcloneBin }, "自动下载"),
            App.h("button", {
              class: "btn", onclick: refreshBins,
              title: "重新探测 OpenList / rclone 并检查是否有新版本，不下载",
            }, "检查更新"),
          ),
          App.row(
            App.h("span", { class: "field-label" }, "挂载目标："), refs.rcType, refs.rcTarget,
          ),
          App.row(
            App.h("span", { class: "field-label" }, "账号："), refs.rcUser,
            App.h("span", { class: "field-label" }, "密码："), refs.rcPwd,
            App.h("button", { class: "btn primary", onclick: mountRclone }, "挂载"),
          ),
          App.h("div", { class: "hint", style: { marginTop: "4px" } },
            "盘符需未被占用（A~Z）；文件夹需填绝对路径。挂载依赖 WinFsp 驱动与 rclone.exe，缺失时点上方按钮自动安装。"),
          refs.rcMounts,
        ],
        [],
      ));

      /* 卡片 4（v3.2）：更新管理 */
      refs.updateBox = App.h("div", null, null);
      paneUpdate.appendChild(App.svcCard(
        "更新管理（GitHub 最新版本检查与下载）",
        [
          App.row(
            App.h("button", { class: "btn", onclick: checkUpdates }, "检查更新"),
            refs.autoStartBtn = App.h("button", {
              class: "btn", onclick: async () => {
                const cfg = (await App.tryCall("cfg_get")).data || {};
                const want = !cfg.app_autostart;
                const s = await App.tryCall("pan_set_autostart", "app", want);
                if (!s.ok) { App.toast(s.err, "error"); return; }
                App.toast(want ? "本应用已写入开机自启（HKCU Run，下次开机自动运行）" : "已取消本应用开机自启", "ok");
                paintAutoStart(want);
              },
            }, "开机自启（当前关）"),
          ),
          App.h("div", { class: "hint" },
            "查询结果仅供参考；「下载 / 更新」会拉取对应组件最新版本并复用本地缓存。"),
          refs.updateBox,
        ],
        [],
      ));

      /* 卡片 5：文件浏览 */
      /* v5.4 修复：面包屑自带边框（视觉上是一个"框"），此前在 .row 里按内容
         自适应宽度，与卡片两侧不对齐；改为撑满整行（margin 交给 .row 管理） */
      refs.crumb = App.h("div", { class: "breadcrumb",
        style: { flex: "1", minWidth: "0", marginBottom: "0" } }, null);
      refs.saveDir = App.h("input", {
        class: "input grow-in", placeholder: "下载保存目录（留空 = 设置页保存目录）",
      });
      const saveDirBrowse = App.h("button", {
        class: "btn", onclick: async () => {
          const r = await App.tryCall("pan_pick_local_folder");
          if (r.ok && r.data) refs.saveDir.value = r.data;
        },
      }, "浏览...");
      refs.tree = App.h("div", { class: "list", style: { minHeight: "160px", maxHeight: "320px", overflowY: "auto" } });
      refs.tasks = App.h("div", { class: "tasklist" }, null);
      paneBrowse.appendChild(App.svcCard(
        "文件浏览 / 上传 / 下载（WebDAV）",
        [
          App.row(refs.crumb),
          App.row(
            App.h("span", { class: "field-label" }, "保存目录："), refs.saveDir, saveDirBrowse,
          ),
          refs.tree,
          refs.tasks,
        ],
        [
          App.h("button", { class: "btn", onclick: uploadToCwd }, "上传到当前目录"),
          App.h("button", { class: "btn", onclick: newFolder }, "新建文件夹"),
          App.h("button", { class: "btn", onclick: renameEntry }, "重命名"),
          App.h("button", { class: "btn", onclick: deleteEntry }, "删除"),
          App.h("button", {
            class: "btn", onclick: () => {
              state.sel = null; refreshBrowse();
            },
          }, "刷新"),
        ],
      ));

      /* L3 页内分段页签：按使用频率与认知分组（服务→浏览→挂载→维护） */
      const nav = App.subnav([
        { label: "网盘服务", el: paneSvc },
        { label: "浏览与传输", el: paneBrowse },
        { label: "本地挂载", el: paneMount },
        { label: "更新维护", el: paneUpdate },
      ]);
      el.appendChild(nav);
      nav.panes.forEach((p) => el.appendChild(p));

      App.on("pan_log", (m) => log(String(m)));
      App.on("pan_bins", (d) => onBinsInfo(d));
      App.on("pan_metrics", (m) => renderMetrics(m || null));
      App.on("pan_task", (m) => {
        const t = { ...(m || {}) };
        if (t.id === "dl_bin" || t.id === "dl_rclone") {
          if (t.status === "running") {
            log(`下载中：${t.name} ${App.fmtBytes(t.done)}/${App.fmtBytes(t.total) || "?"}`);
          } else if (t.status === "done") {
            log(`${t.name} 下载完成（${t.id === "dl_bin" ? "OpenList" : "rclone"}）`);
          }
          return;
        }
        if (t.status === "done" || t.status === "error" || t.status === "cancelled") {
          state.tasks.delete(t.id);
          if (t.status === "done") log(`${t.kind}完成：${t.name}`);
        } else {
          state.tasks.set(t.id, t);
        }
        renderTasks();
      });

      paintAutoStart(!!((App.state.cfg || {}).app_autostart));
      await refreshState();
      await refreshRclone();
      await refreshBrowse();
    },

    show() {
      refreshState();
      refreshRclone();
      refreshBrowse();
    },
  });
})();