/* FTP 页：服务端（把本地文件夹发布为 FTP）+ 客户端（连接远程 FTP 浏览/传输）。 */
(function () {
  "use strict";

  const state = {
    running: false, fwOpen: false, srvPort: 0, srvRoot: "",
    connected: false, busy: false, remote: "/",
    entries: [], selected: new Set(),
    status: "尚未连接",
  };
  let refs = {};
  let log = null; /* 由 App.makeLog 创建，统一日志实现 */

  /* ---------------- 工具 ---------------- */
  function setStatus(text) {
    state.status = text;
    refs.cliStatus.textContent = text;
  }

  function setBusy(busy, statusText) {
    state.busy = busy;
    if (statusText !== undefined) setStatus(statusText);
    renderClient();
  }

  /* ---------------- 渲染 ---------------- */
  function renderServer() {
    refs.srvStart.disabled = state.running;
    refs.srvStop.disabled = !state.running;
    refs.srvFwRm.disabled = !state.fwOpen;
    // replaceChildren 仅接受 Node/string（不能传数组），须展开
    refs.srvState.replaceChildren(
      ...(state.running
        ? [
            App.statusTag("运行中", "ok", "check"),
            App.h("span", { class: "hint mono" }, `端口 ${state.srvPort} · ${state.srvRoot}`),
          ]
        : [App.statusTag("未运行")]),
    );
  }

  function renderClient() {
    const active = state.connected && !state.busy;
    refs.cliConnect.disabled = state.connected || state.busy;
    for (const w of [refs.cliDisconnect, refs.cliUp, refs.cliPath, refs.cliRefresh,
      refs.cliUpload, refs.cliDownload, refs.cliNewdir, refs.cliDelete]) {
      w.disabled = !active;
    }
    refs.cliStatus.textContent = state.status;
  }

  function renderList() {
    refs.cliList.innerHTML = "";
    if (!state.connected) {
      refs.cliList.appendChild(App.h("div", { class: "empty" },
        "尚未连接\n连接远程 FTP 服务器后，这里显示目录内容"));
      return;
    }
    if (!state.entries.length) {
      refs.cliList.appendChild(App.h("div", { class: "empty" }, "此目录为空"));
      return;
    }
    const items = state.entries.slice().sort(
      (a, b) => (b.is_dir - a.is_dir) || a.name.toLowerCase().localeCompare(b.name.toLowerCase()),
    );
    for (const e of items) {
      const selected = state.selected.has(e.name);
      refs.cliList.appendChild(
        App.h("div", {
          class: "list-item click",
          title: "点击选中/取消，双击文件夹进入",
          style: selected ? { background: "var(--accent-soft)", borderColor: "var(--accent)" } : null,
          onclick: () => toggleSelect(e.name),
          ondblclick: () => { if (e.is_dir) go(e.name); },
        },
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title", style: { userSelect: "text" } }, e.name),
          ),
          App.h("span", { class: "tag" + (e.is_dir ? " accent" : ""), style: { flex: "none" } },
            e.is_dir ? "文件夹" : "文件"),
          App.h("span", { class: "mono muted", style: { flex: "none", minWidth: "70px", textAlign: "right" } },
            e.is_dir ? "" : App.fmtBytes(e.size)),
          App.h("span", { class: "mono muted", style: { flex: "none", minWidth: "120px", textAlign: "right" } },
            App.fmtDate(e.mtime)),
        ),
      );
    }
  }

  /* ---------------- 客户端浏览 ---------------- */
  const selectedEntries = () => state.entries.filter((e) => state.selected.has(e.name));

  function toggleSelect(name) {
    if (state.selected.has(name)) state.selected.delete(name);
    else state.selected.add(name);
    renderList();
  }

  function joinRemote(name) {
    const base = state.remote.replace(/\/+$/, "");
    return base ? `${base}/${name}` : `/${name}`;
  }

  function go(name) {
    state.selected = new Set();
    load(joinRemote(name));
  }

  function goUp() {
    const parts = state.remote.replace(/\/+$/, "").split("/").filter(Boolean);
    parts.pop();
    state.selected = new Set();
    load("/" + parts.join("/"));
  }

  function gotoPath() {
    let p = refs.cliPath.value.trim();
    if (!p) return;
    if (!p.startsWith("/")) p = "/" + p;
    state.selected = new Set();
    load(p);
  }

  async function load(path) {
    if (state.busy || !state.connected) return;
    setBusy(true, "正在读取目录...");
    const r = await App.tryCall("ftp_list", path);
    if (!r.ok) {
      setBusy(false, "读取目录失败");
      App.toast(r.err, "error");
      return;
    }
    state.remote = r.data.path;
    state.entries = r.data.entries;
    state.selected = new Set();
    refs.cliPath.value = state.remote;
    setBusy(false, `已连接：${state.remote}（${state.entries.length} 项）`);
    renderList();
  }

  /* ---------------- 客户端动作 ---------------- */
  async function connectClient() {
    const host = refs.cliHost.value.trim();
    if (!host) { App.toast("请输入 FTP 服务器地址。"); return; }
    setBusy(true, "正在连接...");
    const r = await App.tryCall("ftp_connect", host,
      parseInt(refs.cliPort.value, 10) || 21,
      refs.cliUser.value.trim(), refs.cliPwd.value);
    if (!r.ok) {
      state.connected = false;
      state.entries = [];
      state.selected = new Set();
      setBusy(false, "连接失败");
      renderList();
      App.toast(r.err, "error", 6000);
      return;
    }
    state.connected = true;
    state.remote = "/";
    state.entries = [];
    state.selected = new Set();
    refs.cliPath.value = "/";
    renderList();
    setBusy(false, "已连接");   // 先复位 busy，否则 load() 会被重入守卫拦截
    load("/");
  }

  async function disconnectClient() {
    setBusy(true, "正在断开...");
    await App.tryCall("ftp_disconnect");
    state.connected = false;
    state.remote = "/";
    state.entries = [];
    state.selected = new Set();
    setBusy(false, "已断开");
    renderList();
  }

  function afterOp(r, tag) {
    if (!r.ok) {
      setBusy(false, `${tag}失败：${r.err}`);
      App.toast(r.err, "error", 6000);
      return;
    }
    setBusy(false, tag);
    load(state.remote);
  }

  async function uploadFiles() {
    const r = await App.tryCall("ftp_pick_files");
    if (!r.ok) { App.toast(r.err, "error"); return; }
    if (!r.data || !r.data.length) return;
    setBusy(true, "正在上传...");
    const res = await App.tryCall("ftp_upload", r.data);
    afterOp(res, "上传完成");
  }

  async function downloadFiles() {
    const sel = selectedEntries();
    if (!sel.length) { App.toast("请先在列表中选中要下载的文件。"); return; }
    if (sel.some((e) => e.is_dir)) { App.toast("暂不支持从 FTP 下载文件夹，请只选择文件。"); return; }
    const r = await App.tryCall("ftp_pick_folder");
    if (!r.ok) { App.toast(r.err, "error"); return; }
    if (!r.data) return;
    setBusy(true, "正在下载...");
    const res = await App.tryCall("ftp_download",
      sel.map((e) => ({ name: e.name, is_dir: e.is_dir })), r.data);
    afterOp(res, "下载完成");
  }

  async function newFolder() {
    const name = await App.modal({ title: "新建文件夹", body: "文件夹名称：", input: "", okText: "确定" });
    if (name === null || !name.trim()) return;
    setBusy(true);
    const res = await App.tryCall("ftp_mkdir", name.trim());
    afterOp(res, "新建文件夹完成");
  }

  async function deleteSelected() {
    const sel = selectedEntries();
    if (!sel.length) { App.toast("请先选择要删除的项目。"); return; }
    const names = sel.slice(0, 10).map((e) => e.name).join("\n");
    const more = sel.length > 10 ? `\n... 等共 ${sel.length} 项` : "";
    if (!(await App.confirm("确认删除", `确定删除以下项目吗？\n${names}${more}`))) return;
    setBusy(true);
    const res = await App.tryCall("ftp_delete", sel.map((e) => ({ name: e.name, is_dir: e.is_dir })));
    afterOp(res, "删除完成");
  }

  /* ---------------- 服务端动作 ---------------- */
  function applyServer(s) {
    state.running = !!s.running;
    state.fwOpen = !!s.fw_open;
    state.srvPort = s.port || 0;
    state.srvRoot = s.root || "";
    renderServer();
  }

  async function browseDir() {
    const r = await App.tryCall("ftp_pick_folder");
    if (!r.ok) { App.toast(r.err, "error"); return; }
    if (r.data) refs.srvDir.value = r.data;
  }

  async function startServer() {
    const root = refs.srvDir.value.trim();
    if (!root) { App.toast("请先选择要共享的目录。"); return; }
    const r = await App.tryCall("ftp_server_start",
      refs.srvHost.value.trim() || "0.0.0.0",
      parseInt(refs.srvPort.value, 10) || 21,
      root,
      refs.srvUser.value.trim(),
      refs.srvPwd.value,
      refs.srvWrite.checked,
      refs.srvFw.checked,
    );
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    applyServer(r.data);
  }

  async function stopServer() {
    const r = await App.tryCall("ftp_server_stop");
    if (!r.ok) { App.toast(r.err, "error"); return; }
    applyServer(r.data);
  }

  async function removeFw() {
    const r = await App.tryCall("ftp_fw_remove");
    if (!r.ok) { App.toast(r.err, "error"); return; }
    applyServer(r.data);
  }

  /* ---------------- 状态同步 ---------------- */
  async function refreshState() {
    const r = await App.tryCall("ftp_state");
    if (!r.ok) return;
    const srv = r.data.server || {};
    applyServer(srv);
    if (srv.running) {
      if (srv.host) refs.srvHost.value = srv.host;
      if (srv.port) refs.srvPort.value = srv.port;
      if (srv.root) refs.srvDir.value = srv.root;
      if (srv.username && srv.username !== "anonymous") refs.srvUser.value = srv.username;
      refs.srvWrite.checked = !!srv.allow_write;
    }
    const cli = r.data.client || {};
    if (cli.connected) {
      state.connected = true;
      state.remote = cli.remote || "/";
      refs.cliPath.value = state.remote;
      renderClient();
      renderList();
      load(state.remote);
    } else {
      renderClient();
    }
  }

  /* ---------------- 页面注册 ---------------- */
  App.registerPage({
    id: "ftp",
    title: "FTP",
    icon: "server",
    group: "共享服务",

    async mount(el) {
      refs = {};
      /* 服务端控件 */
      refs.srvHost = App.h("input", {
        class: "input", value: "0.0.0.0", style: { width: "120px" },
        title: "0.0.0.0 表示本机所有网卡",
      });
      refs.srvPort = App.h("input", {
        class: "input", type: "number", min: 1, max: 65535, value: 21, style: { width: "80px" },
      });
      refs.srvDir = App.h("input", {
        class: "input grow", placeholder: "选择要发布的本地文件夹", style: { minWidth: "220px" },
      });
      refs.srvUser = App.h("input", {
        class: "input", placeholder: "留空则匿名访问", style: { width: "150px" },
      });
      refs.srvPwd = App.h("input", {
        class: "input", type: "password", placeholder: "密码（可选）", style: { width: "150px" },
      });
      refs.srvWrite = App.h("input", { type: "checkbox", checked: true });
      refs.srvFw = App.h("input", { type: "checkbox", checked: true });
      refs.srvStart = App.h("button", { class: "btn primary", onclick: startServer }, "启动服务器");
      refs.srvStop = App.h("button", { class: "btn", onclick: stopServer }, "停止");
      refs.srvFwRm = App.h("button", { class: "btn", onclick: removeFw }, "移除防火墙放行");
      refs.srvState = App.h("div", { class: "row" });
      refs.srvLog = App.h("div", { class: "log-box", style: { maxHeight: "110px" } },
        "服务器运行日志（登录 / 上传 / 下载事件）...");
      log = App.makeLog(refs.srvLog);

      /* 客户端控件 */
      refs.cliHost = App.h("input", {
        class: "input grow", placeholder: "例如 192.168.1.100 或 ftp.example.com", style: { minWidth: "180px" },
      });
      refs.cliPort = App.h("input", {
        class: "input", type: "number", min: 1, max: 65535, value: 21, style: { width: "80px" },
      });
      refs.cliUser = App.h("input", {
        class: "input", placeholder: "留空为匿名", style: { width: "130px" },
      });
      refs.cliPwd = App.h("input", { class: "input", type: "password", style: { width: "130px" } });
      refs.cliConnect = App.h("button", { class: "btn primary", onclick: connectClient }, "连接");
      refs.cliDisconnect = App.h("button", { class: "btn", onclick: disconnectClient }, "断开");
      refs.cliUp = App.h("button", { class: "btn sm", onclick: goUp }, "上级");
      refs.cliPath = App.h("input", {
        class: "input grow", value: "/",
        onkeydown: (e) => { if (e.key === "Enter") gotoPath(); },
      });
      refs.cliRefresh = App.h("button", {
        class: "btn sm", onclick: () => { state.selected = new Set(); load(state.remote); },
      }, "刷新");
      refs.cliList = App.h("div", { class: "list", style: { maxHeight: "320px", overflowY: "auto" } });
      refs.cliUpload = App.h("button", { class: "btn sm", onclick: uploadFiles }, "上传文件...");
      refs.cliDownload = App.h("button", { class: "btn sm", onclick: downloadFiles }, "下载到...");
      refs.cliNewdir = App.h("button", { class: "btn sm", onclick: newFolder }, "新建文件夹");
      refs.cliDelete = App.h("button", { class: "btn sm danger", onclick: deleteSelected }, "删除");
      refs.cliStatus = App.h("span", { class: "hint" }, "尚未连接");

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "FTP"),
        App.h("div", { class: "sub" }, "把本地文件夹发布为 FTP 服务器；也可作为客户端连接远程 FTP，浏览并传输文件"),
      ));

      el.appendChild(App.svcCard(
        "FTP 服务器（把本地文件夹发布为 FTP，其他设备可用 FTP 客户端访问）",
        [
          App.row(
            App.h("span", { class: "field-label" }, "监听地址："), refs.srvHost,
            App.h("span", { class: "field-label" }, "端口："), refs.srvPort,
            App.h("span", { class: "field-label" }, "共享目录："), refs.srvDir,
            App.h("button", { class: "btn", onclick: browseDir }, "浏览..."),
          ),
          App.row(
            App.h("span", { class: "field-label" }, "用户名："), refs.srvUser,
            App.h("span", { class: "field-label" }, "密码："), refs.srvPwd,
            App.h("label", { class: "switch" }, refs.srvWrite, App.h("span", { class: "track" }),
              "允许写入（未勾选则只读）"),
            App.h("label", { class: "switch" }, refs.srvFw, App.h("span", { class: "track" }),
              "放行防火墙端口"),
          ),
        ],
        [refs.srvStart, refs.srvStop, refs.srvFwRm, refs.srvState],
        refs.srvLog,
      ));

      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "FTP 客户端（连接远程 FTP 服务器，浏览并传输文件）"),
        App.row(
          App.h("span", { class: "field-label" }, "主机："), refs.cliHost,
          App.h("span", { class: "field-label" }, "端口："), refs.cliPort,
          App.h("span", { class: "field-label" }, "用户名："), refs.cliUser,
          App.h("span", { class: "field-label" }, "密码："), refs.cliPwd,
          refs.cliConnect, refs.cliDisconnect,
        ),
        App.row(refs.cliUp, refs.cliPath, refs.cliRefresh),
        refs.cliList,
        App.h("div", { class: "sep" }),
        App.row(
          refs.cliUpload, refs.cliDownload, refs.cliNewdir, refs.cliDelete,
          App.h("span", { class: "ml-auto" }, refs.cliStatus),
        ),
      ));

      /* 事件订阅 */
      App.on("ftp_log", (m) => log(m));
      App.on("ftp_progress", (e) => {
        if (!e) return;
        setStatus(e.total
          ? `${e.op} ${Math.floor((e.done * 100) / e.total)}%（${App.fmtBytes(e.done)} / ${App.fmtBytes(e.total)}）`
          : `${e.op} 已传输 ${App.fmtBytes(e.done)}`);
      });

      renderServer();
      renderClient();
      renderList();
      await refreshState();
    },

    show() { refreshState(); },
  });
})();
