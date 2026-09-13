/* UI 工具库：DOM 构建、图标、Toast、模态框、脱敏、格式化。 */
(function () {
  "use strict";

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v == null) continue;
        if (k === "class") node.className = v;
        else if (k === "html") node.innerHTML = v;
        else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
        else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
        else if (typeof v === "boolean") node[k] = v;   /* 布尔属性走 property：
          setAttribute("checked", false) 会因属性存在而渲染为勾选（垃圾清理页实测） */
        else node.setAttribute(k, v);
      }
    }
    for (const child of children.flat(Infinity)) {
      if (child == null || child === false) continue;
      node.appendChild(typeof child === "string" || typeof child === "number"
        ? document.createTextNode(String(child)) : child);
    }
    return node;
  }

  const escMap = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => escMap[c]);

  /* ---------------- 图标（feather 风格描边 SVG） ---------------- */
  const ICONS = {
    clipboard: '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
    keyboard: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M6 12h.01M10 12h.01M14 12h.01M18 12h.01M8 16h8"/>',
    monitor: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.59 13.51l6.83 3.98M15.41 6.51l-6.82 3.98"/>',
    globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    server: '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><path d="M6 6h.01M6 18h.01"/>',
    folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    radar: '<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>',
    wifi: '<path d="M5 12.55a11 11 0 0 1 14.08 0M8.53 16.11a6 6 0 0 1 6.95 0M12 20h.01"/>',
    cursor: '<path d="M3 3l7.07 16.97 2.51-7.39 7.39-2.51L3 3z"/>',
    settings: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    sun: '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>',
    moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    edit: '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>',
    copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    trash: '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M10 11v6M14 11v6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    send: '<path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-2z"/>',
    refresh: '<path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
    x: '<path d="M18 6L6 18M6 6l12 12"/>',
    check: '<path d="M20 6L9 17l-5-5"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    toolbox: '<rect x="2" y="7" width="20" height="13" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2M2 13h20M7 13v3M17 13v3"/>',
    hash: '<path d="M4 9h16M4 15h16M10 3L8 21M16 3l-2 18"/>',
    qrcode: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><path d="M14 14h3v3h-3zM21 14h.01M21 21h.01M14 21h.01"/>',
    zap: '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>',
    filetext: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
    camera: '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
    image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>',
    scissors: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.12 15.88M14.47 14.48L20 20M8.12 8.12L12 12"/>',
    layers: '<path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5M2 12l10 5 10-5"/>',
    video: '<rect x="2" y="6" width="14" height="12" rx="2"/><path d="M22 8.5l-6 3.5 6 3.5v-7z"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    code: '<path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/>',
    key: '<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3"/>',
    eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    palette: '<path d="M12 2a10 10 0 0 0 0 20 2 2 0 0 0 2-2 2 2 0 0 1 2-2h1a5 5 0 0 0 5-5c0-5.5-4.5-9-10-9z"/><circle cx="7.5" cy="10.5" r="1"/><circle cx="12" cy="7.5" r="1"/><circle cx="16.5" cy="10.5" r="1"/>',
    star: '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    grid: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
    list: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    terminal: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
    moreh: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    arrowup: '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>',
    arrowleft: '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>',
    tree: '<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
    filter: '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    chevronr: '<polyline points="9 18 15 12 9 6"/>',
    columns: '<rect x="3" y="3" width="8" height="18" rx="1"/><rect x="13" y="3" width="8" height="18" rx="1"/>',
  };

  function icon(name, size) {
    size = size || 16;
    const path = ICONS[name] || ICONS.info;
    return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
  }

  /* ---------------- Toast ---------------- */
  function toast(msg, kind, ms) {
    kind = kind || "info";
    ms = ms || 3600;
    let box = document.getElementById("toasts");
    const t = h("div", { class: "toast " + (kind === "info" ? "" : kind) }, String(msg));
    box.appendChild(t);
    const kill = () => { t.classList.add("out"); setTimeout(() => t.remove(), 260); };
    t.addEventListener("click", kill);
    setTimeout(kill, ms);
  }

  /* ---------------- 模态框 ---------------- */
  /**
   * 通用模态框。
   * @param {object} opts
   * @param {string} [opts.title] 标题
   * @param {string|Node} [opts.body] 正文
   * @param {string} [opts.input] 单个输入框的初始值（resolve 返回输入值）
   * @param {Array<{label?:string, type?:string, placeholder?:string, value?:string}>} [opts.inputs]
   *        多个输入框（resolve 返回字符串数组，顺序与 inputs 一致）
   * @param {string} [opts.textarea] 单文本域初值（resolve 返回字符串），比 input 优先
   * @param {string} [opts.okText] 确定按钮文字
   * @returns {Promise<string|string[]|boolean|null>}
   */
  function modal({ title, body, input, inputs, textarea, okText, cancelText }) {
    return new Promise((resolve) => {
      const root = document.getElementById("modal-root");
      let close = (val) => { root.innerHTML = ""; resolve(val); };
      const content = [];
      if (body) content.push(h("div", { class: "modal-body" }, body));

      let inputEl = null;        /* 单输入：原始 input 元素 */
      let inputsEls = [];        /* 多输入：原始 input 元素列表 */
      let textareaEl = null;     /* 单文本域：原始 textarea 元素 */
      if (Array.isArray(inputs) && inputs.length) {
        inputsEls = inputs.map((f) => {
          const inp = h("input", {
            class: "input modal-input",
            type: f.type || "text",
            ...(f.placeholder ? { placeholder: f.placeholder } : {}),
            ...(f.value ? { value: f.value } : {}),
          });
          return h("div", { class: "modal-field" },
            f.label ? h("div", { class: "field-label" }, f.label) : null,
            inp);
        });
        content.push(...inputsEls);
      } else if (textarea !== undefined) {
        textareaEl = h("textarea", {
          class: "input modal-input mono", rows: 14, wrap: "off",
          style: { resize: "vertical", fontFamily: "Consolas, monospace", tabSize: 2 },
        }, textarea);
        content.push(textareaEl);
      } else if (input !== undefined) {
        inputEl = h("input", { class: "input modal-input", value: input || "" });
        content.push(inputEl);
      }

      const dlg = h("div", { class: "modal" },
        h("h3", null, title),
        ...content,
        h("div", { class: "modal-btns" },
          h("button", { class: "btn", onclick: () => close(null) }, cancelText || "取消"),
          h("button", {
            class: "btn primary",
            onclick: () => {
              if (inputsEls.length) close(inputsEls.map((el) => el.querySelector("input").value));
              else if (textareaEl) close(textareaEl.value);
              else close(inputEl ? inputEl.value : true);
            },
          }, okText || "确定"),
        ),
      );
      const mask = h("div", {
        class: "modal-mask",
        onclick: (e) => { if (e.target === mask) close(null); },
      }, dlg);
      root.appendChild(mask);
      /* v5.1b：键盘支持——Esc 取消、Enter 确认（textarea 内换行除外）、自动聚焦 */
      const onKey = (e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          close(null);
        } else if (e.key === "Enter"
                   && !(e.target instanceof HTMLTextAreaElement)) {
          e.preventDefault();
          const pb = dlg.querySelector(".btn.primary");
          if (pb) pb.click();
        }
      };
      const realClose = close;
      close = (val) => {
        document.removeEventListener("keydown", onKey, true);
        realClose(val);
      };
      document.addEventListener("keydown", onKey, true);
      if (inputEl) { inputEl.focus(); inputEl.select(); }
      else if (inputsEls.length) inputsEls[0].querySelector("input").focus();
      else {
        const pb = dlg.querySelector(".btn.primary");
        if (pb) pb.focus();
      }
    });
  }

  const confirm = (title, body) => modal({ title, body, okText: "确定" });
  const prompt = (title, value, body) => modal({ title, body, input: value, okText: "保存" });

  /* OCR 识别结果弹窗：全文（可复制）+ 逐行列表（点击复制该行） */
  function ocrResultModal(result) {
    const processed = (result && result.text) || "";
    const rawText = (result && result.raw_text != null) ? result.raw_text : null;
    const lines = (result && result.lines) || [];
    let cur = processed;
    if (!String(cur).trim()) { toast("未识别到文字", "info", 4000); return Promise.resolve(null); }
    const copy = (t) => navigator.clipboard.writeText(t)
      .then(() => toast("已复制", "ok"), () => toast("复制失败", "error"));
    const ta = h("textarea", {
      class: "input mono modal-input", rows: 8, readOnly: true,
      style: { resize: "vertical", fontFamily: "Consolas, monospace" },
    }, cur);
    ta.value = cur;   // 显式赋值，兼容 textarea 值同步差异
    /* v5.1：有后处理原文差异时提供「已处理 / 原始」切换 */
    let toggle = null;
    if (rawText != null && rawText !== processed) {
      let showRaw = false;
      toggle = h("button", {
        class: "btn sm", style: { alignSelf: "flex-end" },
        onclick: () => {
          showRaw = !showRaw;
          cur = showRaw ? rawText : processed;
          ta.value = cur;
          toggle.textContent = showRaw ? "查看已处理" : "查看原始文本";
        },
      }, "查看原始文本");
    }
    const list = h("div", { class: "list", style: { maxHeight: "220px", overflow: "auto" } });
    (lines || []).forEach((ln) => {
      const r = ln.rect || {};
      list.appendChild(h("div", {
        class: "list-item",
        style: { cursor: "pointer" },
        title: "点击复制该行",
        onclick: () => copy(ln.text || ""),
      },
        h("span", { class: "li-main" },
          h("div", { class: "li-title", style: { wordBreak: "break-all" } }, (ln.text || " ") || " "),
          h("div", { class: "li-sub" },
            `(${Math.round(r.x || 0)}, ${Math.round(r.y || 0)})  ${Math.round(r.w || 0)} × ${Math.round(r.h || 0)}`),
        ),
        h("span", { class: "hint" }, "复制"),
      ));
    });
    return modal({
      title: "OCR 识别结果",
      body: h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } },
        h("p", { class: "hint", style: { margin: 0 } },
          `共 ${lines.length} 行文字 · 点击条目可复制单行`),
        ta,
        h("div", { style: { display: "flex", gap: "8px", justifyContent: "flex-end" } },
          toggle,
          h("button", {
            class: "btn sm", onclick: () => copy(cur),
          }, "复制全文"),
        ),
        list,
      ),
      okText: "关闭", cancelText: "取消",
    });
  }

  /* ---------------- 格式化 ---------------- */
  const pad = (n) => String(n).padStart(2, "0");
  function fmtTime(ts) {
    const d = new Date(ts * 1000);
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }
  function fmtBytes(n) {
    if (n == null) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n : n.toFixed(1)) + " " + units[i];
  }

  /* ---------------- 通用组件 ---------------- */

  /** 日期时间格式化（YYYY-MM-DD HH:mm，空/非法返回空串）。统一 file/ftp 等页面的时间列。 */
  function fmtDate(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    if (isNaN(d.getTime())) return "";
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  /** 日志框：包装容器元素，返回 log(msg) 函数（自动追加时间戳、限行数、滚动到底）。
   *  统一 clipboard / km / transfer 等页面的日志实现。 */
  function makeLog(el, maxLines) {
    maxLines = maxLines || 200;
    return (msg) => {
      for (const line of String(msg).split("\n")) {
        el.appendChild(h("div", null,
          `[${new Date().toLocaleTimeString("zh-CN", { hour12: false })}] ${line}`));
      }
      while (el.children.length > maxLines) el.firstChild.remove();
      el.scrollTop = el.scrollHeight;
    };
  }

  /** 状态标签：统一「运行中/未运行/已加密」等状态显示。
   *  kind ∈ ok | warn | danger | accent | default（空则不染色）
   *  iconName 的 SVG 必须经 html 属性插入（icon() 返回字符串，直接作子节点会当文本显示） */
  function statusTag(text, kind, iconName) {
    const k = kind && kind !== "default" ? " " + kind : "";
    const attrs = { class: "tag" + k };
    if (iconName) attrs.html = icon(iconName, 11);
    return h("span", attrs, String(text));
  }

  /* ---------------- 管理员权限统一入口（v5.4：状态栏右下角，全应用唯一） ----------------
     背景：此前「以管理员身份重启」散落在网络页、应用管理页各一份，提示文案也不一致。
     现统一收敛为状态栏右下角的全局芯片：非管理员 → 警示标签 + 提权重启按钮；
     已管理员 → 绿色标签。各页面仅保留文字提示，不再自带按钮。 */
  let _adminEl = null;
  let _adminState = null;
  function renderAdminChip() {
    if (!_adminEl) return;
    _adminEl.innerHTML = "";
    if (_adminState === true) {
      _adminEl.appendChild(h("span", {
        class: "tag ok", title: "已以管理员身份运行：提权类操作（共享开关 / 卸载 UWP / 系统级清理等）不再弹 UAC",
      }, "管理员"));
      return;
    }
    _adminEl.appendChild(h("span", {
      class: "tag warn", title: "非管理员运行：网络发现/共享开关、卸载 UWP、垃圾清理系统项、TUN 模式等操作会弹 UAC 授权框",
    }, "非管理员"));
    _adminEl.appendChild(h("button", {
      class: "btn xs admin-relaunch",
      title: "弹出一次 UAC 授权并以管理员身份重启本应用（设置与服务会保留）",
      onclick: async function () {
        if (this._busy) return;
        this._busy = true; this.disabled = true;
        const old = this.textContent; this.textContent = "重启中…";
        try {
          /* v5.4：不再弹确认框——UAC 授权框本身即用户确认 */
          const r = await App.tryCall("network_relaunch_admin");
          if (!r.ok) {
            App.toast(r.err || "提权失败", "error", 6000);
            this._busy = false; this.disabled = false; this.textContent = old;
            return;
          }
          App.toast(r.data || "正在以管理员身份重启…", "info", 5000);
        } catch (e) {
          this._busy = false; this.disabled = false; this.textContent = old;
          App.toast(e.message || String(e), "error", 6000);
        }
      },
    }, "提权重启"));
  }
  /** 刷新右下角管理员芯片（admin 缺省时用 App.state.info 缓存）。 */
  function refreshAdminChip(admin) {
    if (typeof admin === "boolean") _adminState = admin;
    else if (App.state && App.state.info && App.state.info.admin != null) {
      _adminState = !!App.state.info.admin;
    }
    renderAdminChip();
  }
  /** 挂载到状态栏右端（启动时调用一次）。 */
  function initAdminChip() {
    const host = document.getElementById("status-right");
    if (!host || _adminEl) return;
    _adminEl = h("span", { class: "admin-chip" });
    host.insertBefore(_adminEl, host.firstChild);
    refreshAdminChip();
  }

  /** 行容器：统一 .row 布局 */
  function row(...children) {
    return h("div", { class: "row" }, ...children);
  }

  /**
   * 服务卡片：统一「标题 + 内容行 + 动作行 + 日志框」的服务端表单结构。
   * 用于 web / ftp 两个服务页，消除结构高度雷同的重复表单代码。
   * @param {string|Node} title 卡片标题
   * @param {Array<Node>} rows 内容行（每行通常为 App.row(...)）
   * @param {Array<Node>} [actions] 动作行控件（启动/停止等按钮）
   * @param {Node} [log] 日志框元素
   */
  function svcCard(title, rows, actions, log) {
    return h("div", { class: "card" },
      h("div", { class: "card-title" }, title),
      ...rows,
      actions && actions.length ? h("div", { class: "row" }, ...actions) : null,
      log ? h("div", { style: { marginTop: "10px" } }, log) : null,
    );
  }

  /* ---------------- L3 层级组件（v3.5 多级信息架构） ---------------- */

  /**
   * 页内分段页签：把长页面按认知分组拆为互斥区块（L3 导航层）。
   * tabs: [{ label, el }] —— el 为该分区的容器元素；首签默认激活。
   * opts.onSwitch(i)：可选，页签切换回调（i 为目标索引，含首次渲染不触发；
   *   调用方可用于懒加载——首次激活某个 pane 时才拉数据）。
   * 返回 sticky 页签条；点击切换 .active（pane 带进场动效）。
   */
  function subnav(tabs, opts) {
    opts = opts || {};
    const nav = h("div", { class: "subnav", role: "tablist" });
    const panes = [];
    let cur = 0;
    tabs.forEach((t, i) => {
      const tab = h("button", { class: "subnav-tab", role: "tab" }, t.label);
      const pane = h("div", { class: "subnav-pane" }, t.el);
      const activate = () => {
        if (cur === i) return;
        cur = i;
        nav.querySelectorAll(".subnav-tab").forEach((b) => b.classList.remove("active"));
        tab.classList.add("active");
        panes.forEach((p) => p.classList.remove("active"));
        pane.classList.add("active");
        if (opts.onSwitch) opts.onSwitch(i);
      };
      tab.addEventListener("click", activate);
      nav.appendChild(tab);
      panes.push(pane);
      if (i === 0) {
        tab.classList.add("active");
        pane.classList.add("active");
      }
    });
    // 把所有 pane 挂回调用方 DOM 树的占位容器：subnav() 只返回页签条，
    // pane 需要按顺序插到页签条后面 —— 由返回值上的 panes 数组供调用方 append。
    nav.panes = panes;
    return nav;
  }

  /**
   * 可折叠分区（次级内容收纳）：头部点击展开/收起，chevron 旋转 + 高度过渡。
   * 默认展开；open=false 初始收起（低频/高级内容）。
   */
  function sec(title, children, opts) {
    const { open = true } = opts || {};
    const body = h("div", { class: "sec-body" },
      h("div", null, h("div", { class: "sec-body-inner" }, children)));
    const chevron = h("span", { class: "sec-chevron" },
      h("svg", { viewBox: "0 0 24 24", width: "14", height: "14", fill: "none",
        stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round",
        "stroke-linejoin": "round" },
        h("path", { d: "M6 9l6 6 6-6" })));
    const el = h("div", { class: "sec" + (open ? "" : " collapsed") },
      h("div", { class: "sec-head" }, title, chevron), body);
    el.querySelector(".sec-head").addEventListener("click", () => {
      el.classList.toggle("collapsed");
    });
    return el;
  }

  /* ---------------- 弹出菜单（v5.2：右键菜单 / 更多⋯下拉，统一内核） ---------------- */
  let _menuEl = null;
  function _menuCleanup() {
    document.removeEventListener("mousedown", _menuOutside, true);
    document.removeEventListener("keydown", _menuKey, true);
    window.removeEventListener("resize", closeMenu);
    window.removeEventListener("blur", closeMenu);
  }
  function _menuOutside(e) {
    if (_menuEl && !_menuEl.contains(e.target)) closeMenu();
  }
  function _menuKey(e) {
    if (e.key === "Escape") { e.preventDefault(); closeMenu(); }
  }
  function closeMenu() {
    if (!_menuEl) return;
    _menuEl.remove();
    _menuEl = null;
    _menuCleanup();
  }
  function _buildMenu(items) {
    const menu = h("div", { class: "ctx-menu", role: "menu" });
    for (const it of (items || [])) {
      if (it === "sep" || it === "-") {
        menu.appendChild(h("div", { class: "ctx-sep" }));
        continue;
      }
      if (!it || !it.label) continue;
      const btn = h("div", {
        class: "ctx-item" + (it.danger ? " danger" : "") + (it.disabled ? " disabled" : ""),
        role: "menuitem",
      },
        it.icon ? h("span", { class: "ctx-ico", html: icon(it.icon, 13) }) : null,
        h("span", { class: "ctx-label" }, it.label),
        it.hint ? h("span", { class: "ctx-hint" }, it.hint) : null,
      );
      if (!it.disabled && it.onclick) {
        btn.addEventListener("click", () => { closeMenu(); it.onclick(); });
      }
      menu.appendChild(btn);
    }
    return menu;
  }

  /**
   * 定位弹出菜单（右键菜单 / 锚定下拉共用）。
   * items: [{label, icon?, hint?, danger?, disabled?, onclick} | "sep"]
   */
  function contextMenu(items, x, y) {
    closeMenu();
    const menu = _buildMenu(items);
    menu.style.visibility = "hidden";
    document.body.appendChild(menu);
    /* 先渲染再测量，窗口边界裁剪 */
    const r = menu.getBoundingClientRect();
    menu.style.left = Math.max(4, Math.min(x, window.innerWidth - r.width - 6)) + "px";
    menu.style.top = Math.max(4, Math.min(y, window.innerHeight - r.height - 6)) + "px";
    menu.style.visibility = "";
    _menuEl = menu;
    setTimeout(() => {
      document.addEventListener("mousedown", _menuOutside, true);
      document.addEventListener("keydown", _menuKey, true);
      window.addEventListener("resize", closeMenu);
      window.addEventListener("blur", closeMenu);
    }, 0);
  }

  /** 「更多 ⋯」下拉：以按钮为锚点展开（自动上下翻转） */
  function overflowMenu(anchorEl, items) {
    if (!anchorEl) return;
    const r = anchorEl.getBoundingClientRect();
    contextMenu(items, r.left, r.bottom + 4);
  }

  /* ---------------- 脱敏（手机号 / 身份证 / 银行卡） ---------------- */
  const SENSITIVE_RE = /(?<!\d)(\d{17}[\dXx])(?!\d)|(?<!\d)(1[3-9]\d{9})(?!\d)|(?<!\d)(\d{13,19})(?!\d)/g;

  function _maskPart(s) {
    if (s.length <= 7) return "****";
    return s.slice(0, 3) + "****" + s.slice(-4);
  }

  /** 返回 { text: 模糊化文本, hits: ["身份证", ...] } */
  function maskSensitive(text) {
    const hits = [];
    const masked = String(text ?? "").replace(SENSITIVE_RE, (m, id, phone, bank) => {
      if (id) { hits.push("身份证"); return id.slice(0, 4) + "***********" + id.slice(-3); }
      if (phone) { hits.push("手机号"); return _maskPart(phone); }
      hits.push("银行卡");
      return bank.slice(0, 4) + "********" + bank.slice(-4);
    });
    return { text: masked, hits: [...new Set(hits)] };
  }

  /* ---------------- 工具箱：统一结果面板 + Ctrl+K 快速直达 ---------------- */

  /**
   * 工具结果面板：工具栏（复制全部/清空/另存 txt）+ 多条目列表。
   * item = { label, text?, el?, ts?(秒) }；空列表时整体隐藏。
   */
  function toolOut(opts) {
    opts = opts || {};
    const saveName = opts.saveName || "结果";
    let items = [];
    const list = h("div", { class: "tool-out-list" });
    const root = h("div", { class: "card-out tool-out" },
      h("div", { class: "tool-out-bar" },
        h("button", { class: "btn sm", onclick: () => {
          const t = items.map((x) => (x.label ? "[" + x.label + "] " : "") +
            (x.text != null ? x.text : "")).join("\n");
          if (!t) return;
          navigator.clipboard.writeText(t).then(() => toast("已复制全部", "ok"),
            () => toast("复制失败", "error"));
        } }, "复制全部"),
        h("button", { class: "btn sm", onclick: () => api.clear() }, "清空"),
        h("button", { class: "btn sm", onclick: () => {
          if (!items.length) return;
          const body = items.map((x) => (x.label ? "[" + x.label + "] " : "") +
            (x.text != null ? x.text : "")).join("\n");
          const d = new Date();
          const p = (n) => String(n).padStart(2, "0");
          const name = `${saveName}-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}` +
            `-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}.txt`;
          const a = h("a", {
            href: URL.createObjectURL(new Blob([body], { type: "text/plain;charset=utf-8" })),
            download: name,
          });
          document.body.appendChild(a);
          a.click();
          setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 200);
          toast("已另存：" + name, "ok");
        } }, "另存 txt"),
        h("span", { class: "hint" }, opts.placeholder || ""),
      ),
      list,
    );
    function render() {
      list.innerHTML = "";
      for (const it of items) {
        const row = h("div", { class: "tool-out-item" },
          h("div", { class: "tool-out-head" },
            h("span", { class: "tool-out-label" }, it.label || ""),
            it.ts ? h("span", { class: "hint" }, fmtDate(it.ts)) : null,
            h("span", { class: "grow" }),
            it.text != null ? h("button", { class: "btn sm", title: "复制本条", onclick: () => {
              navigator.clipboard.writeText(it.text || "").then(() => toast("已复制", "ok"),
                () => toast("复制失败", "error"));
            } }, "复制") : null,
          ),
          it.el ? it.el
            : h("div", { class: "tool-out-text mono", style: { wordBreak: "break-all" } },
                String(it.text != null ? it.text : "")),
        );
        list.appendChild(row);
      }
      /* v5.3：结果面板不再"无内容就整体隐藏"。工具页（.page-flex）会把结果面板
         拉伸到剩余高度，若此时整块是 display:none，卡片就会变成"大卡装小内容"的
         空洞。改为常驻 + 空态提示，使工具页成为「参数区 + 结果区」的稳定工作区。 */
      if (!items.length) {
        list.appendChild(h("div", { class: "empty", style: { margin: "10px 0" } },
          opts.emptyText || "运行后，结果显示在这里"));
      }
      root.style.display = "";
    }
    const api = {
      el: root,
      set(arr) { items = (arr || []).slice(); render(); },
      append(item) { items.push(item || {}); render(); },
      clear() { items = []; render(); },
    };
    render();
    return api;
  }

  /** Ctrl+K 快速直达浮层：搜索 App.TOOL_CATALOG，↑↓ 选择，Enter 直达。 */
  let launcherOpen = false;
  function toolLauncher() {
    if (launcherOpen) return;
    const catalog = (window.App && App.TOOL_CATALOG) || [];
    launcherOpen = true;
    const mask = h("div", { class: "launcher-mask" });
    const box = h("div", { class: "launcher" });
    const inp = h("input", { class: "input", placeholder: "搜索工具（标题 / 描述 / 关键字）…" });
    const list = h("div", { class: "launcher-list" });
    box.append(inp, list);
    mask.append(box);
    document.body.appendChild(mask);
    const close = () => { mask.remove(); launcherOpen = false; };
    let sel = 0;
    let hits = [];
    const favs = ((window.App && App.state && App.state.cfg) || {}).tool_favs || [];
    const recent = ((window.App && App.state && App.state.cfg) || {}).tool_recent || [];

    function render() {
      const q = inp.value.trim().toLowerCase();
      if (!q) {
        hits = [];
        for (const id of recent) {
          const t = catalog.find((x) => x.id === id);
          if (t) hits.push(t);
        }
        for (const id of favs) {
          const t = catalog.find((x) => x.id === id);
          if (t && !hits.includes(t)) hits.push(t);
        }
        for (const t of catalog) {
          if (!hits.includes(t) && (favs.includes(t.id) || recent.includes(t.id))) hits.push(t);
        }
        for (const t of catalog) if (!hits.includes(t)) hits.push(t);
      } else {
        const pre = catalog.filter((t) => (t.title || "").toLowerCase().startsWith(q));
        const sub = catalog.filter((t) => !pre.includes(t) &&
          ((t.title || "").toLowerCase().includes(q) ||
           (t.desc || "").toLowerCase().includes(q) ||
           (t.kw || "").toLowerCase().includes(q)));
        hits = pre.concat(sub);
      }
      sel = 0;
      list.innerHTML = "";
      if (!hits.length) {
        list.appendChild(h("div", { class: "launcher-empty" }, "没有匹配的工具"));
        return;
      }
      hits.forEach((t, i) => {
        const row = h("div", { class: "launcher-item" + (i === sel ? " active" : "") },
          h("span", { class: "launcher-icon", html: icon(t.icon || "toolbox", 16) }),
          h("span", { class: "launcher-title" }, t.title),
          h("span", { class: "launcher-desc" }, t.desc || ""),
          favs.includes(t.id) ? h("span", { class: "tag accent" }, "收藏") : null,
        );
        row.addEventListener("click", () => pick(t));
        list.appendChild(row);
      });
      const act = list.querySelector(".active");
      if (act) act.scrollIntoView({ block: "nearest" });
    }
    function move(d) {
      if (!hits.length) return;
      sel = (sel + d + hits.length) % hits.length;
      [...list.querySelectorAll(".launcher-item")].forEach((n, i) =>
        n.classList.toggle("active", i === sel));
      const act = list.querySelector(".active");
      if (act) act.scrollIntoView({ block: "nearest" });
    }
    function pick(t) {
      close();
      if (!t) return;
      if (App.recordToolRecent) App.recordToolRecent(t.id);
      App.navigate(t.id);
    }
    inp.addEventListener("input", render);
    inp.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter") { e.preventDefault(); pick(hits[sel]); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
    });
    mask.addEventListener("mousedown", (e) => { if (e.target === mask) close(); });
    render();
    setTimeout(() => inp.focus(), 30);
  }

  /* 导出 */
  window.App = window.App || {};
  Object.assign(App, {
    h, esc, icon, toast, modal, confirm, prompt, ocrResultModal,
    fmtTime, fmtDate, fmtBytes, maskSensitive,
    makeLog, statusTag, row, svcCard, subnav, sec,
    toolOut, toolLauncher,
    contextMenu, overflowMenu, closeMenu,
    initAdminChip, refreshAdminChip,
  });
})();
