/* 应用壳：导航 / 路由 / 设备面板 / 状态栏 / 主题 / 品牌。 */
(function () {
  "use strict";

  const GROUP_ORDER = ["互联与协作", "共享服务", "网盘与网络", "工具与增效", "系统"];
  const PAGE_FOR_CLI = { share: "share", webdav: "web", clip: "clipboard" };
  const BRAND = "LocalToolbox";

  /* ---------------- 主题（v3.5f：支持 auto 跟随系统深浅色） ---------------- */
  App.setTheme = function (t) {
    const mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    const resolved = t === "auto" ? (mq && mq.matches ? "dark" : "light") : t;
    const light = resolved === "light";
    document.body.classList.toggle("light", light);
    document.body.classList.toggle("dark", !light);
    const btn = document.getElementById("theme-btn");
    if (btn) btn.innerHTML = App.icon(light ? "moon" : "sun");
    App.applyAppearance();
  };

  /* ---------------- 外观自定义（v3.5d：界面缩放 + 强调色） ---------------- */
  App.applyAppearance = function () {
    const cfg = App.state.cfg || {};
    const zoom = Number(cfg.ui_zoom) || 1.0;
    document.body.style.zoom = zoom === 1.0 ? "" : String(zoom);
    document.body.classList.toggle("reduce-motion", !!cfg.ui_reduce_motion);  // v3.5e
    const root = document.documentElement;
    const accent = String(cfg.accent_color || "").trim();
    if (/^#[0-9a-fA-F]{6}$/.test(accent)) {
      const alpha = document.body.classList.contains("light") ? 0.12 : 0.16;
      const r = parseInt(accent.slice(1, 3), 16);
      const g = parseInt(accent.slice(3, 5), 16);
      const b = parseInt(accent.slice(5, 7), 16);
      root.style.setProperty("--accent", accent);
      root.style.setProperty("--accent-soft", `rgba(${r}, ${g}, ${b}, ${alpha})`);
    } else {
      root.style.removeProperty("--accent");
      root.style.removeProperty("--accent-soft");
    }
  };

  /* ---------------- 提示音（v3.5d：传输完成短哔声，notify_sound 开启时） ---------------- */
  App.beep = function () {
    if (!(App.state.cfg || {}).notify_sound) return;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = App._beepCtx && App._beepCtx.state !== "closed"
        ? App._beepCtx : (App._beepCtx = new Ctx());
      if (ctx.state === "suspended") { ctx.resume().catch(() => {}); }
      const t = ctx.currentTime;
      const gain = ctx.createGain();
      gain.connect(ctx.destination);
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.12, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.28);
      const osc = ctx.createOscillator();
      osc.connect(gain);
      osc.type = "sine";
      osc.frequency.setValueAtTime(880, t);
      osc.frequency.setValueAtTime(1320, t + 0.1);
      osc.start(t);
      osc.stop(t + 0.3);
    } catch (e) { /* 音频不可用时静默 */ }
  };

  App.copyText = async function (text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (e2) { /* ignore */ }
      ta.remove();
      return ok;
    }
  };

  /* ---------------- 导航与路由 ---------------- */
  function buildNav() {
    const nav = document.getElementById("nav");
    nav.innerHTML = "";
    const groups = new Map();
    for (const p of App.pages) {
      if (p.hidden) continue;   // 二级页面：只从工具箱等入口进入，不占侧栏
      const g = p.group || "功能";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(p);
    }
    const ordered = [
      ...GROUP_ORDER.filter((g) => groups.has(g)),
      ...[...groups.keys()].filter((g) => !GROUP_ORDER.includes(g)),
    ];
    for (const g of ordered) {
      // 分组可折叠（L1 层级）：caption 点击收起/展开该组，箭头旋转过渡
      const groupEl = App.h("div", { class: "nav-group" });
      const caption = App.h("div", { class: "nav-caption" }, g,
        App.h("span", { class: "nav-fold" },
          App.h("svg", { viewBox: "0 0 24 24", width: "12", height: "12",
            fill: "none", stroke: "currentColor", "stroke-width": "2",
            "stroke-linecap": "round", "stroke-linejoin": "round" },
            App.h("path", { d: "M6 9l6 6 6-6" }))));
      const items = App.h("div", { class: "nav-items" },
        App.h("div", null,
          groups.get(g).map((p) =>
            App.h("div", {
              class: "nav-item",
              "data-page": p.id,
              onclick: () => App.navigate(p.id),
              html: App.icon(p.icon || "info") + `<span>${App.esc(p.title)}</span>`,
            }))));
      caption.addEventListener("click", () => groupEl.classList.toggle("collapsed"));
      groupEl.appendChild(caption);
      groupEl.appendChild(items);
      nav.appendChild(groupEl);
    }
  }

  function pageEl(p) {
    const el = App.h("div", { class: "page page-enter", id: "page-" + p.id });
    el.__mounted = false;
    return el;
  }

  /** 导航 L1 定位：当前页面所在分组 caption 点亮 */
  function markCurrentGroup(id) {
    document.querySelectorAll(".nav-group").forEach((g) => {
      g.classList.toggle("current", !!g.querySelector(`.nav-item[data-page="${id}"]`));
    });
  }

  App.navigate = async function (id) {
    const p = App.pages.find((x) => x.id === id);
    if (!p) return false;
    closeDrawers();
    const content = document.getElementById("content");
    let el = content.querySelector("#page-" + id);
    if (!el) {
      el = pageEl(p);
      content.appendChild(el);
    }
    for (const child of content.children) child.classList.remove("active");
    el.classList.add("active");
    if (!el.__mounted) {
      try {
        if (p.backTo) {
          el.appendChild(App.h("div", { class: "tool-page-head" },
            App.h("button", {
              class: "btn sm",
              onclick: () => App.navigate(p.backTo),
            }, "← 返回工具箱")));
        }
        await p.mount(el);
        el.__mounted = true;
      } catch (e) {
        App.toast("页面加载失败：" + e.message, "error");
        console.error(e);
      }
    } else if (p.show) {
      try { p.show(); } catch (e) { console.error(e); }
    }
    App.state.page = id;
    document.querySelectorAll(".nav-item").forEach((n) =>
      n.classList.toggle("active", n.dataset.page === id),
    );
    markCurrentGroup(id);
    return true;
  };

  /* 全局快捷键动作（后端热键线程经 evaluate_js 调用）：
     shot → 打开截图页并直接进入区域截图；full → 直接捕获虚拟桌面（窗口已被
     后端隐藏，截图完成恢复）；其余（clip 等）→ 打开对应页面 */
  App.hotkeyAction = async function (name) {
    try {
      if (name === "shot") {
        const ok = await App.navigate("screenshot");
        if (!ok || !App.shotRegionCapture) return;
        setTimeout(() => { try { App.shotRegionCapture(); } catch (e) { console.error(e); } }, 200);
      } else if (name === "full") {
        const r = await App.tryCall("shot_capture", "full", 0);
        App.tryCall("shot_restore_window");   // 截图已完成，恢复主窗口
        if (!r.ok) { App.toast(r.err, "error", 6000); return; }
        const d = r.data;
        const bits = [];
        if (d.saved) bits.push("已保存");
        if (d.copied) bits.push("已复制");
        if (d.edit === false) {
          App.toast("全屏截图完成" + (bits.length ? "（" + bits.join("，") + "）" : ""), "ok", 5000);
          if (d.saved) App.toast("已保存：\n" + d.saved, "ok", 6000);
          return;
        }
        const ok = await App.navigate("screenshot");
        if (ok && App.shotLoadPick) {
          setTimeout(() => { try { App.shotLoadPick(d); } catch (e) { console.error(e); } }, 150);
        }
      } else {
        await App.navigate(name || "clipboard");
      }
    } catch (e) { console.error("[hotkey]", e); }
  };

  /* 全屏遮罩弹窗（ShareX 式区域截图）事件：选区图 → 截图页编辑器。
     edit=false（任务链未勾选"载入编辑器"）时不跳转，仅提示保存/复制结果 */
  App.on("shot_pick", async (d) => {
    try {
      if (d && d.edit === false) {
        const bits = [];
        if (d.saved) bits.push("已保存：" + d.saved);
        if (d.copied) bits.push("已复制到剪贴板");
        App.toast("截图完成" + (bits.length ? "（" + bits.join("，") + "）" : ""), "ok", 6000);
        return;
      }
      const ok = await App.navigate("screenshot");
      if (!ok || !App.shotLoadPick) return;
      setTimeout(() => { try { App.shotLoadPick(d); } catch (e) { console.error(e); } }, 150);
    } catch (e) { console.error("[shot_pick]", e); }
  });
  /* 延迟/普通截图完成（shot_capture delayed 线程推送）：进编辑器 */
  App.on("shot_capture_done", async (d) => {
    try {
      if (!d) return;
      if (d.err) { App.toast(d.err, "error", 6000); return; }
      const ok = await App.navigate("screenshot");
      if (!ok || !App.shotLoadPick) return;
      setTimeout(() => { try { App.shotLoadPick(d); } catch (e) { console.error(e); } }, 150);
    } catch (e) { console.error("[shot_capture_done]", e); }
  });
  App.on("shot_pick_cancel", () => App.toast("已取消截图", "info"));

  /* ---------------- 自绘窗口控制（无边框主窗口） ---------------- */
  function initWinControls() {
    const call = (m, ...a) => App.tryCall(m, ...a);
    document.getElementById("win-min").addEventListener("click", () => call("win_minimize"));
    document.getElementById("win-max").addEventListener("click", () => call("win_toggle_max"));
    document.getElementById("win-close").addEventListener("click", () => call("win_hide_to_tray"));
    const titlebar = document.getElementById("titlebar");
    const isControl = (t) => t.closest && t.closest("button, input, select, a, .logo");
    titlebar.addEventListener("dblclick", (e) => {
      if (isControl(e.target)) return;
      call("win_toggle_max");
    });
    /* 窗口拖动：由 pywebview easy_drag 统一负责（按住任意非交互区域拖动整窗，
       标题栏自然包含在内）。旧实现调 win_begin_drag 走 WM_NCLBUTTONDOWN，
       在 WebView2 上因鼠标捕获不在本进程而拖不动，已停用；画布等交互元素的
       保护见 js/window_drag.js（拦截 mousedown 冒泡到 window）。 */
  }

  /* ---------------- 响应式抽屉 ---------------- */
  function closeDrawers() {
    document.getElementById("app").classList.remove("nav-open", "devices-open");
  }

  function initDrawers() {
    const app = document.getElementById("app");
    const navBtn = document.getElementById("nav-toggle");
    const devBtn = document.getElementById("devices-toggle");
    navBtn.addEventListener("click", () => {
      app.classList.toggle("nav-open");
      app.classList.remove("devices-open");
    });
    devBtn.addEventListener("click", () => {
      app.classList.toggle("devices-open");
      app.classList.remove("nav-open");
    });
    /* 点击内容区可关闭抽屉 */
    document.getElementById("content").addEventListener("click", () => closeDrawers(), true);
  }

  /* ---------------- 设备面板 ---------------- */
  function devCard(d) {
    const alias = d.alias || "";
    const tags = [];
    if (d.clip_port) tags.push(App.h("span", { class: "tag accent" }, "剪贴板"));
    if (d.file_port) tags.push(App.h("span", { class: "tag ok" }, "文件"));
    if (d.paired) tags.push(App.h("span", { class: "tag ok" }, "已配对"));
    const card = App.h("div", { class: "dev-card" },
      App.h("div", { class: "top" },
        App.h("span", { class: "dot on" }),
        App.h("span", { class: "name", title: alias ? d.name : "" },
          alias ? `${alias}（${d.name}）` : d.name),
        App.h("span", { class: "ops" },
          d.file_port ? App.h("button", {
            class: "icon-btn", title: "发送文件",
            html: App.icon("send", 13),
            onclick: (e) => {
              e.stopPropagation();
              App.xferSendToDevice(d);
            },
          }) : null,
          d.file_port ? App.h("button", {
            class: "icon-btn", title: d.paired ? "已配对（传输自动加密）" : "配对加密",
            style: d.paired ? { color: "var(--ok)" } : null,
            html: App.icon("shield", 13),
            onclick: (e) => {
              e.stopPropagation();
              App.xferPairToDevice(d);
            },
          }) : null,
          App.h("button", {
            class: "icon-btn", title: "备注别名",
            html: App.icon("edit", 13),
            onclick: async (e) => {
              e.stopPropagation();
              const name = await App.prompt("设备备注", alias || "", `为 ${d.name}（${d.ip}）设置备注名，留空清除。`);
              if (name === null) return;
              await App.guardedCall("device_alias", d.id, name);
              renderDevices(App.state.devices);
            },
          }),
          App.h("button", {
            class: "icon-btn", title: "复制 IP",
            html: App.icon("copy", 13),
            onclick: async (e) => {
              e.stopPropagation();
              await App.copyText(d.ip);
              App.toast("已复制 " + d.ip, "ok");
            },
          }),
        ),
      ),
      App.h("div", { class: "ip" }, d.ip),
      tags.length ? App.h("div", { class: "tags" }, tags) : null,
    );
    /* 拖拽发送：文件拖到设备卡片即触发 P0-3 文件传输引擎 */
    card.addEventListener("dragover", (e) => { e.preventDefault(); card.classList.add("dragover"); });
    card.addEventListener("dragleave", () => card.classList.remove("dragover"));
    card.addEventListener("drop", (e) => {
      e.preventDefault();
      card.classList.remove("dragover");
      const files = e.dataTransfer.files ? Array.from(e.dataTransfer.files) : [];
      const paths = files.map((f) => f.pywebviewFullPath).filter(Boolean);
      if (paths.length) {
        App.xferSendToDevice(d, paths);
        return;
      }
      if (files.length > 0) {
        App.toast("无法获取拖入文件的本地路径\n请改用卡片上的发送按钮选择文件", "warn", 5000);
        return;
      }
      const text = e.dataTransfer.getData("text/plain");
      if (text) App.toast(`文本已拖到「${alias || d.name}」\n请在「剪贴板同步」页发送`, "warn", 5000);
    });
    return card;
  }

  function selfCard(info) {
    return App.h("div", { class: "dev-card dev-self" },
      App.h("div", { class: "top" },
        App.h("span", { class: "dot accent" }),
        App.h("span", { class: "name" }, `${info.device_name}（本机）`),
      ),
      App.h("div", { class: "ip" }, info.ips.join("  ·  ") || "未获取到局域网 IP"),
    );
  }

  function renderDevices(devices) {
    App.state.devices = devices || [];
    const list = document.getElementById("dev-list");
    list.innerHTML = "";
    document.getElementById("dev-count").textContent = String(App.state.devices.length);
    if (App.state.info) list.appendChild(selfCard(App.state.info));
    if (!App.state.devices.length) {
      list.appendChild(App.h("div", { class: "empty" }, "正在发现设备…\n对方也需运行本软件"));
      return;
    }
    for (const d of App.state.devices) list.appendChild(devCard(d));
  }

  async function pollDevices() {
    const r = await App.tryCall("devices_list");
    if (r.ok) renderDevices(r.data);
    setTimeout(pollDevices, 3000);
  }

  /* ---------------- 状态栏 ---------------- */
  function status(msg) {
    document.getElementById("status-msg").textContent = msg;
  }

  /* ---------------- 启动 ---------------- */
  async function boot() {
    const cfgR = await App.tryCall("cfg_get");
    App.state.cfg = cfgR.ok ? cfgR.data : {};
    App.setTheme(App.state.cfg.theme || "dark");
    initDrawers();
    initWinControls();
    document.getElementById("theme-btn").addEventListener("click", async () => {
      const next = document.body.classList.contains("light") ? "dark" : "light";
      App.setTheme(next);
      App.tryCall("cfg_set", "theme", next);
    });
    /* v3.5f：主题为 auto 时跟随系统深浅色实时切换 */
    if (window.matchMedia) {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const onScheme = () => {
        if ((App.state.cfg || {}).theme === "auto") App.setTheme("auto");
      };
      if (mq.addEventListener) mq.addEventListener("change", onScheme);
      else if (mq.addListener) mq.addListener(onScheme);
    }

    const infoR = await App.tryCall("app_info");
    if (infoR.ok) {
      App.state.info = infoR.data;
      document.getElementById("app-ver").textContent = "v" + infoR.data.version;
      document.getElementById("status-right").textContent =
        `本机 ${infoR.data.device_name} · ${infoR.data.ips[0] || "无局域网 IP"}`;
    }

    buildNav();
    renderDevices([]);
    pollDevices();
    /* v4.5：Ctrl+K 快速直达工具（捕获段，输入框聚焦内同样触发） */
    window.addEventListener("keydown", (e) => {
      if (e.ctrlKey && !e.altKey && !e.shiftKey &&
          String(e.key || "").toLowerCase() === "k") {
        e.preventDefault();
        e.stopPropagation();
        if (App.toolLauncher) App.toolLauncher();
      }
    }, true);
    App.on("app_log", (m) => status(m));
    App.on("xfer_pair_done", () => pollDevices());
    App.on("xfer_done", () => App.beep());  // v3.5d：传输完成提示音（notify_sound 开启时）
    App.on("cli_action", async ({ action, path }) => {
      const target = PAGE_FOR_CLI[action];
      if (!target) return;
      await App.navigate(target);
      const p = App.pages.find((x) => x.id === target);
      if (p && p.onCli) { try { await p.onCli(path); } catch (e) { App.toast(e.message, "error"); } }
    });

    const startId = App.state.cfg.start_page;
    if (!(await App.navigate(startId)) && App.pages.length) await App.navigate(App.pages[0].id);
    status("就绪");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
