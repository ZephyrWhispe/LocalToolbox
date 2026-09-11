/* Web（WebDAV）页：HTTP 浏览 + WebDAV 挂载服务器、防火墙放行、认证修复。 */
(function () {
  "use strict";

  const state = { running: false, fwOpen: false, port: 0 };
  let refs = {};
  let log = null; /* 由 App.makeLog 创建，统一日志实现 */

  function authText(level) {
    if (level === null || level === undefined) return "Windows WebDAV 认证状态：未知";
    if (level >= 2) return `Windows WebDAV 认证状态：已允许 http 账号认证（当前值 ${level}）`;
    return `Windows WebDAV 认证状态：未开放（当前值 ${level}，需修复才能用账号登录）`;
  }

  function renderState(urls) {
    refs.start.disabled = state.running;
    refs.stop.disabled = !state.running;
    refs.fwRm.disabled = !state.fwOpen;
    refs.state.replaceChildren(
      state.running
        ? App.statusTag(`运行中（端口 ${state.port}）`, "ok", "check")
        : App.statusTag("未运行"),
    );
    refs.url.textContent = state.running ? (urls || []).slice(0, 3).join("  ") : "";
  }

  async function refresh() {
    const r = await App.tryCall("web_get_state");
    if (!r.ok) return;
    state.running = r.data.running;
    state.fwOpen = r.data.fw_open;
    state.port = r.data.port;
    renderState(r.data.urls);
    refs.authLabel.textContent = authText(r.data.auth_level);
  }

  async function startServer() {
    if (state.running) return;
    const root = refs.dir.value.trim();
    if (!root) { App.toast("请先选择要共享的目录。"); return; }
    const r = await App.tryCall(
      "web_start",
      refs.host.value.trim() || "0.0.0.0",
      parseInt(refs.port.value, 10) || 80,
      root,
      refs.user.value.trim(),
      refs.pwd.value,
      refs.writeTgl.checked,
      refs.fwTgl.checked,
    );
    if (!r.ok) App.toast("启动失败：" + r.err, "error", 6000);
    await refresh();
  }

  async function stopServer() {
    const r = await App.tryCall("web_stop");
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refresh();
  }

  async function removeFw() {
    const r = await App.tryCall("web_remove_fw");
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    await refresh();
  }

  async function fixAuth() {
    if (!(await App.confirm(
      "修复 Windows WebDAV 认证",
      "将把注册表「WebClient\\Parameters\\BasicAuthLevel」设为 2，" +
      "使 Windows 允许通过 http 发送账号密码到 WebDAV 服务器。\n\n" +
      "该操作需要管理员权限，且会弹出 UAC 提示，是否继续？",
    ))) return;
    const r = await App.tryCall("web_fix_auth");
    if (!r.ok) App.toast(r.err, "error", 6000);
    await refresh();
  }

  App.registerPage({
    id: "web",
    title: "Web 服务器",
    icon: "globe",
    group: "共享服务",

    async mount(el) {
      refs = {};

      refs.host = App.h("input", {
        class: "input", value: "0.0.0.0",
        title: "0.0.0.0 表示本机所有网卡", style: { width: "150px" },
      });
      refs.port = App.h("input", {
        class: "input", type: "number", min: 1, max: 65535, value: 80,
        style: { width: "90px" },
      });
      refs.dir = App.h("input", {
        class: "input", placeholder: "选择要发布的本地文件夹",
        style: { flex: "1", minWidth: "200px" },
      });
      refs.user = App.h("input", {
        class: "input", placeholder: "留空则匿名访问", style: { width: "150px" },
      });
      refs.pwd = App.h("input", {
        class: "input", type: "password", placeholder: "密码（可选）", style: { width: "150px" },
      });
      refs.writeTgl = App.h("input", { type: "checkbox", checked: true });
      refs.fwTgl = App.h("input", { type: "checkbox", checked: true });
      refs.start = App.h("button", { class: "btn primary", onclick: startServer }, "启动服务器");
      refs.stop = App.h("button", { class: "btn", disabled: true, onclick: stopServer }, "停止");
      refs.fwRm = App.h("button", { class: "btn", disabled: true, onclick: removeFw }, "移除防火墙放行");
      refs.state = App.h("span", null);
      refs.url = App.h("span", { class: "mono", style: { color: "var(--accent)", userSelect: "text" } });
      refs.authLabel = App.h("span", { class: "hint" });
      refs.log = App.h("div", {
        class: "log-box", style: { maxHeight: "100px" },
      }, "服务器运行日志（访问 / WebDAV 请求 / 错误）...");
      log = App.makeLog(refs.log);

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "Web 服务器"),
        App.h("div", { class: "sub" },
          "把本地文件夹发布为 Web 站点（浏览器访问）并支持 WebDAV 协议挂载（Windows 资源管理器 / rclone / 手机文件管理器）"),
      ));

      const browseBtn = App.h("button", {
        class: "btn",
        onclick: async () => {
          const r = await App.tryCall("web_pick_folder");
          if (!r.ok) { App.toast(r.err, "error", 6000); return; }
          if (r.data) refs.dir.value = r.data;
        },
      }, "浏览...");

      el.appendChild(App.svcCard(
        "Web 服务器（HTTP 浏览 + WebDAV 挂载，把本地文件夹发布为 Web 站点）",
        [
          App.row(
            App.h("span", { class: "field-label" }, "监听地址："), refs.host,
            App.h("span", { class: "field-label" }, "端口："), refs.port,
            App.h("span", { class: "field-label" }, "共享目录："), refs.dir,
            browseBtn,
          ),
          App.row(
            App.h("span", { class: "field-label" }, "用户名："), refs.user,
            App.h("span", { class: "field-label" }, "密码："), refs.pwd,
            App.h("label", { class: "switch" }, refs.writeTgl, App.h("span", { class: "track" }),
              "允许写入（未勾选则只读）"),
            App.h("label", { class: "switch" }, refs.fwTgl, App.h("span", { class: "track" }),
              "放行防火墙端口"),
          ),
          App.h("div", { class: "hint", style: { whiteSpace: "pre-line", marginTop: "10px" } },
            "浏览器直接访问上方地址可浏览/下载文件；Windows 资源管理器可挂载 WebDAV：\n" +
            "     命令行执行  net use X: http://本机IP:端口  （或浏览器打开后复制地址）\n" +
            "提示：用「用户名+密码」访问时，Windows 默认禁止通过 http 发送账号，可点下方按钮修复（需管理员权限）。"),
          App.row(
            refs.authLabel,
            App.h("span", { class: "grow" }),
            App.h("button", { class: "btn", onclick: fixAuth }, "修复 Windows WebDAV http 账号认证"),
          ),
        ],
        [refs.start, refs.stop, refs.fwRm, refs.state, refs.url],
        refs.log,
      ));

      /* 事件订阅：服务器运行日志 */
      App.on("web_log", (m) => log(String(m)));
      await refresh();
    },

    show() { refresh(); },
    async onCli(path) { if (path) refs.dir.value = path; },
  });
})();
