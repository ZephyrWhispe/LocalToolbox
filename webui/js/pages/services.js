/* 服务总览页（v5.4 O11）：聚合 同步 / OpenList / WebDAV 挂载 / FTP / HTTP 共享 /
   双代理 的运行状态与快捷启停。数据源为各服务现有状态 API（svc_overview 聚合）。
   设计原则：单主任务状态卡列表；5s 轮询 + 快照比对（无变化不重绘）。 */
(function () {
  "use strict";

  const h = App.h;

  App.registerPage({
    id: "services",
    title: "服务总览",
    group: "共享服务",
    icon: "globe",

    mount: mount,
    show: () => refresh(),
  });

  let refs = {};
  let pageCtx = null;
  let lastKey = "";

  function statusOf(svc) {
    if (!svc || !svc.ready) return { text: "未知", kind: "info" };
    if (svc.running) return { text: "运行中", kind: "ok" };
    return { text: "未运行", kind: "info" };
  }

  async function refresh() {
    const r = await App.tryCall("svc_overview");
    if (!r.ok) {
      if (refs.box) {
        refs.box.innerHTML = "";
        refs.box.appendChild(h("div", { class: "empty" }, r.err || "状态读取失败"));
      }
      return;
    }
    const key = JSON.stringify(r.data);
    if (key === lastKey) return;   /* 快照比对：无变化不重绘 */
    lastKey = key;
    render(r.data);
  }

  function card(title, svc, detail, actions, goto) {
    const st = statusOf(svc);
    const acts = h("div", { class: "row", style: { margin: "0", flexWrap: "wrap" } });
    (actions || []).forEach((a) => acts.appendChild(a));
    if (goto) {
      acts.appendChild(h("button", {
        class: "btn xs", onclick: () => App.navigate(goto),
      }, "前往配置"));
    }
    return h("div", { class: "card", style: { padding: "10px 14px" } },
      h("div", { class: "row", style: { margin: "0", flexWrap: "nowrap", alignItems: "center" } },
        h("span", { class: "li-title", style: { flex: "1" } }, title),
        App.statusTag(st.text, st.kind),
      ),
      h("div", { class: "hint", style: { margin: "4px 0" } }, detail),
      acts.childNodes.length ? acts : null,
    );
  }

  function actBtn(label, busyLabel, fn, primary) {
    return h("button", {
      class: "btn xs" + (primary ? " primary" : ""),
      onclick: async function () {
        if (this._busy) return;
        this._busy = true; this.disabled = true;
        const old = this.textContent; this.textContent = busyLabel || label + "…";
        try { await fn(); } finally { this._busy = false; this.disabled = false; this.textContent = old; }
      },
    }, label);
  }

  function render(d) {
    refs.box.innerHTML = "";
    const cfg = App.state.cfg || {};

    /* 剪贴板同步 */
    const clip = d.clip || {};
    refs.box.appendChild(card("剪贴板同步", clip,
      clip.running ? `收发${clip.recv ? "开" : "关"} · 跨设备互传剪贴板内容`
        : "跨设备互传剪贴板内容（含文本 / 图片 / 文件）",
      [
        clip.running
          ? actBtn("停止同步", "停止中…", async () => {
              const r = await App.tryCall("clip_stop");
              if (!r.ok) App.toast(r.err, "error");
              lastKey = ""; await refresh();
            })
          : actBtn("开启同步", "开启中…", async () => {
              const r = await App.tryCall("clip_start", true, true, true);
              if (!r.ok) App.toast(r.err, "error");
              lastKey = ""; await refresh();
            }, true),
      ], "clipboard"));

    /* OpenList */
    const ol = d.openlist || {};
    refs.box.appendChild(card("OpenList 网盘服务", ol,
      ol.running ? `已启动（端口 ${ol.port}）` : "网页盘（聚合网盘 / WebDAV 服务端）",
      [
        ol.running
          ? actBtn("停止服务", "停止中…", async () => {
              const r = await App.tryCall("pan_stop");
              if (!r.ok) App.toast(r.err, "error");
              lastKey = ""; await refresh();
            })
          : actBtn("启动服务", "启动中…", async () => {
              const r = await App.tryCall("pan_start",
                cfg.openlist_host || "127.0.0.1",
                cfg.openlist_port || 15244, true);
              if (!r.ok) App.toast(r.err, "error", 6000);
              lastKey = ""; await refresh();
            }, true),
      ], "pan"));

    /* WebDAV 挂载（rclone） */
    const wd = d.webdav || {};
    const mounts = wd.mounts || [];
    refs.box.appendChild(card("WebDAV 挂载", { ready: true, running: mounts.length > 0 },
      mounts.length
        ? `已挂载 ${mounts.length} 个：${mounts.map((m) => m.target || m.letter || m.dir || "?").join("、")}`
        : "把网盘挂载为本机盘符 / 目录（依赖 OpenList 与 rclone）",
      mounts.length
        ? [actBtn("全部卸载", "卸载中…", async () => {
            for (const m of mounts) {
              const t = m.target || m.letter || m.dir;
              if (t) await App.tryCall("pan_rclone_umount", t);
            }
            App.toast("已卸载", "ok");
            lastKey = ""; await refresh();
          })]
        : null, "pan"));

    /* FTP 服务端 */
    const ftp = (d.ftp || {}).server || {};
    refs.box.appendChild(card("FTP 服务端", ftp,
      ftp.running ? `运行中（${ftp.host}:${ftp.port}，根目录 ${ftp.root || "-"}）`
        : "把本机目录发布为 FTP 服务（需先在 FTP服务页配置根目录与账号）",
      null, "ftp"));

    /* HTTP 共享 */
    const http = d.http || {};
    refs.box.appendChild(card("HTTP 共享", http,
      http.running ? `运行中（端口 ${http.port}）` : "把本机目录发布为 HTTP 下载页（需先选择共享目录）",
      null, "web"));

    /* Clash 代理 */
    const clash = d.clash || {};
    refs.box.appendChild(card("代理（Clash）", clash,
      clash.running ? `运行中（${clash.mode || "rule"} 模式，系统代理${clash.sys_proxy ? "开" : "关"}）`
        : "mihomo 内核代理与系统代理",
      [
        clash.running
          ? actBtn("停止", "停止中…", async () => {
              const r = await App.tryCall("clash_stop");
              if (!r.ok) App.toast(r.err, "error");
              lastKey = ""; await refresh();
            })
          : actBtn("启动", "启动中…", async () => {
              const r = await App.tryCall("clash_start");
              if (!r.ok) App.toast(r.err, "error", 6000);
              lastKey = ""; await refresh();
            }, true),
      ], "clash"));

    /* V2rayN 代理 */
    const v2 = d.v2ray || {};
    refs.box.appendChild(card("代理（V2rayN）", v2,
      v2.running ? "运行中（xray / sing-box / v2ray 核心）" : "多核心代理与智能分流",
      [
        v2.running
          ? actBtn("停止", "停止中…", async () => {
              const r = await App.tryCall("proxy_stop");
              if (!r.ok) App.toast(r.err, "error");
              lastKey = ""; await refresh();
            })
          : actBtn("启动", "启动中…", async () => {
              const r = await App.tryCall("proxy_start");
              if (!r.ok) App.toast(r.err, "error", 6000);
              lastKey = ""; await refresh();
            }, true),
      ], "v2ray"));
  }

  function mount(el, ctx) {
    el.innerHTML = "";
    refs = {};
    pageCtx = ctx;

    el.appendChild(h("div", { class: "page-head" },
      h("h2", null, "服务总览"),
      h("div", { class: "sub" },
        "本机对外服务的运行状态与快捷启停；详细参数请进入对应页面配置"),
    ));

    refs.box = h("div", {
      class: "svc-overview",
      style: { display: "flex", flexDirection: "column", gap: "10px", overflowY: "auto" },
    });
    refs.box.appendChild(h("div", { class: "empty" }, "正在读取服务状态…"));
    el.appendChild(refs.box);

    refresh().then(() => {
      /* 5s 轮询 + 快照比对（refresh 内实现），离开页面由 ctx.every 自动跳过 */
      ctx.every(5000, () => { refresh(); });
    });
  }
})();
