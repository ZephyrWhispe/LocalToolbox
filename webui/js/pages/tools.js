/* 工具箱（v4.5 重构）：
 *   目录页（搜索 / 收藏 / 最近使用 / 分区网格） + 工具二级页（toolShell 统一壳）。
 * 合并：tool-text（格式化/编码解码/文本对比）、tool-files（哈希/清单校验/目录快照/列表导出）。
 * 新增：tool-uuid（UUID v4/v7）、tool-radix（BigInt 任意进制转换）。
 * 统一结果面板 App.toolOut（ui.js）、Ctrl+K 直达 App.toolLauncher（ui.js）。
 * 后端：tool_*（app/bridge/tools_api.py）与 dns_api / editor_api / proxy_* 调用保持不变。
 * v5.5：系统类工具（系统优化/硬件信息/显示器信息/无人值守安装）提升为一级页面，
 *   归入侧栏「系统」分组；工具箱原「系统与网络」分区改为「网络工具」。
 */
(function () {
  "use strict";

  /* ================= 通用小组件 ================= */
  function ta(rows, placeholder) {
    return App.h("textarea", {
      class: "input mono", rows: rows || 4,
      placeholder: placeholder || "",
      style: { fontFamily: "Consolas, monospace" },
    });
  }
  function copyBtn(getText) {
    return App.h("button", { class: "btn sm", onclick: () => {
      if (!getText()) return;
      navigator.clipboard.writeText(getText()).then(
        () => App.toast("已复制", "ok"),
        () => App.toast("复制失败", "error"),
      );
    } }, "复制");
  }
  /* 无标题卡片（工具页外壳已带标题，卡片只做视觉分组） */
  function pane(...children) {
    return App.h("div", { class: "card" }, children);
  }
  function actions(...children) {
    return App.h("div", { class: "card-actions" }, children);
  }
  const spacer = () => App.h("span", { class: "grow" });
  function field(label, el) {
    /* field-row：标签与控件同行（flex-wrap 禁止换行），控件 flex 占据剩余宽度 */
    return App.h("div", { class: "row field-row" },
      App.h("span", { class: "field-label" }, label), el);
  }
  const boxSel = (opts) => App.h("select", { class: "input" },
    opts.map((o) => App.h("option", { value: o }, o)));
  function fmtSize(n) {
    if (n >= 1048576) return (n / 1048576).toFixed(1) + " MiB";
    if (n >= 1024) return (n / 1024).toFixed(1) + " KiB";
    return n + " B";
  }
  const STAR_SVG = (on) =>
    `<svg viewBox="0 0 24 24" width="16" height="16" fill="${on ? "currentColor" : "none"}" ` +
    `stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` +
    `<path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>`;

  /* ================= 收藏 / 最近使用（cfg 持久化） ================= */
  const favs = () => ((App.state || {}).cfg || {}).tool_favs || [];
  const recents = () => ((App.state || {}).cfg || {}).tool_recent || [];

  App.toggleToolFav = async function (id) {
    const cur = favs().slice();
    const i = cur.indexOf(id);
    if (i >= 0) cur.splice(i, 1); else cur.unshift(id);
    const r = await App.tryCall("cfg_set", "tool_favs", cur);
    if (r.ok) {
      if ((App.state || {}).cfg) App.state.cfg.tool_favs = r.data || cur;
      App.toast(cur.includes(id) ? "已收藏" : "已取消收藏", "ok", 2000);
    } else {
      App.toast(r.err || "保存失败", "error", 4000);
    }
    return r.ok;
  };
  App.recordToolRecent = async function (id) {
    const cur = recents().filter((x) => x !== id);
    cur.unshift(id);
    const r = await App.tryCall("cfg_set", "tool_recent", cur.slice(0, 20));
    if (r.ok && (App.state || {}).cfg) App.state.cfg.tool_recent = (r.data || cur).slice(0, 20);
  };
  function isFav(id) { return favs().includes(id); }

  function starBtn(id) {
    const btn = App.h("button", {
      class: "tool-item-star" + (isFav(id) ? " on" : ""),
      title: isFav(id) ? "取消收藏" : "收藏工具",
    });
    const paint = () => {
      btn.classList.toggle("on", isFav(id));
      btn.title = isFav(id) ? "取消收藏" : "收藏工具";
      btn.innerHTML = STAR_SVG(isFav(id));
    };
    paint();
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (await App.toggleToolFav(id)) {
        paint();
        if (typeof App._toolDirRerender === "function") App._toolDirRerender();
      }
    });
    return btn;
  }

  /* ================= 工具页壳：返回 + 标题 + 星标 + 内容 =================
     v5.5：topLevel=true 时为一级页面（不显示「返回工具箱」与收藏星标） */
  function toolShell(t, buildBody, topLevel) {
    const head = App.h("div", { class: "tool-page-head", style: { display: "flex", alignItems: "center", gap: "10px" } },
      topLevel ? null : App.h("button", { class: "btn sm", onclick: () => App.navigate("tools") }, "← 返回工具箱"),
      App.h("div", { class: "tool-item-icon", html: App.icon(t.icon, 22) }),
      App.h("div", { style: { minWidth: "0" } },
        App.h("h2", { style: { margin: 0, fontSize: "17px" } }, t.title),
        App.h("div", { class: "sub", style: { margin: 0 } }, t.desc)),
      spacer(),
      topLevel ? null : starBtn(t.id),
    );
    return App.h("div", { class: "tool-shell" }, head,
      App.h("div", { class: "tool-page" }, buildBody()));
  }

  /* ================= 1 时间戳转换 ================= */
  function buildTimestamp() {
    const tsIn = App.h("input", { class: "input mono", placeholder: "输入秒(10位)/毫秒(13位)时间戳" });
    const dateIn = App.h("input", { class: "input mono", placeholder: "YYYY-MM-DD HH:mm:ss（北京时区）" });
    const out = App.toolOut({ saveName: "时间戳转换" });
    function toDate(ts) {
      ts = String(ts || "").trim();
      if (!/^\d+$/.test(ts)) return null;
      const ms = ts.length > 11 ? Number(ts) : Number(ts) * 1000;
      const d = new Date(ms);
      if (isNaN(d.getTime())) return null;
      const cst = new Date(ms + 8 * 3600 * 1000).toISOString().replace("T", " ").slice(0, 19);
      return {
        cst, utc: d.toISOString().replace("T", " ").slice(0, 19),
        sec: Math.floor(ms / 1000), ms,
      };
    }
    function renderDate() {
      const r = toDate(tsIn.value);
      if (!r) { out.clear(); return; }
      out.set([{ label: "转换结果", text: `北京：${r.cst}　UTC：${r.utc}　秒：${r.sec}　毫秒：${r.ms}` }]);
    }
    tsIn.addEventListener("input", renderDate);
    dateIn.addEventListener("change", () => {
      const v = dateIn.value.trim();
      const t = Date.parse(v.replace(" ", "T"));
      if (isNaN(t)) { App.toast("无法解析日期", "error"); return; }
      tsIn.value = String(Math.floor(t / 1000));
      renderDate();
    });
    return pane(
      field("时间戳 →", tsIn),
      field("日期 →", dateIn),
      actions(App.h("button", { class: "btn sm", onclick: () => {
        tsIn.value = String(Math.floor(Date.now() / 1000)); renderDate();
      } }, "填入当前时间戳")),
      out.el,
    );
  }

  /* ================= 2 文本工具箱（格式化 / 编码解码 / 文本对比） ================= */
  function detectFmt(s) {
    const t = s.trim();
    if (!t) return null;
    if (t.startsWith("{") || t.startsWith("[")) return "json";
    if (t.startsWith("<")) return "xml";
    if (/^(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|WITH)\b/i.test(t)) return "sql";
    return null;
  }
  function formatSql(sql) {
    const K = ["SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING", "LIMIT",
      "JOIN", "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "OUTER JOIN", "ON", "SET",
      "VALUES", "INTO", "AND", "OR", "UNION", "CASE", "WHEN", "THEN", "ELSE", "END"];
    const s = sql.replace(/\s+/g, " ").replace(/\s*,\s*/g, ", ");
    const re = new RegExp("\\b(" + K.join("|") + ")\\b", "gi");
    let depth = 0;
    const out = [];
    for (const part of s.split(re)) {
      if (!part) continue;
      const upper = part.toUpperCase();
      if (K.includes(upper) && !["AND", "OR"].includes(upper)) {
        if (depth) out.push("\n" + "  ".repeat(depth));
        out.push(upper);
        if (["JOIN", "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "OUTER JOIN"].includes(upper)) depth++;
      } else {
        out.push(part);
      }
    }
    return out.join("").replace(/\n\s*,/g, ", ");
  }
  function formatXml(xml) {
    try {
      const dom = new DOMParser().parseFromString(xml, "text/xml");
      if (dom.getElementsByTagName("parsererror").length) return "（XML 解析失败）";
      let out = "";
      (function walk(node, depth) {
        const pad = "  ".repeat(depth);
        if (node.nodeType === 3) {
          const t = node.nodeValue && node.nodeValue.trim();
          if (t) out += pad + t + "\n";
          return;
        }
        if (node.nodeType !== 1) return;
        const name = node.nodeName;
        const attrs = Array.from(node.attributes || [])
          .map((a) => ` ${a.name}="${a.value}"`).join("");
        const kids = Array.from(node.childNodes || []);
        const hasEl = kids.some((k) => k.nodeType === 1);
        if (!hasEl) {
          const text = (node.textContent || "").trim();
          out += `${pad}<${name}${attrs}>${text ? " " + text + " " : ""}</${name}>` + "\n";
        } else {
          out += `${pad}<${name}${attrs}>` + "\n";
          kids.forEach((k) => walk(k, depth + 1));
          out += `${pad}</${name}>` + "\n";
        }
      })(dom.documentElement, 0);
      return out.trim();
    } catch (e) { return "（XML 解析失败）"; }
  }

  function paneFormat() {
    const src = ta(6, "粘贴 JSON / XML / SQL…");
    const kindSel = App.h("select", { class: "input" },
      ["auto"].concat(["json", "xml", "sql"]).map((o) =>
        App.h("option", { value: o }, o === "auto" ? "自动检测" : o.toUpperCase())));
    const out = App.toolOut({ saveName: "格式化" });
    function fmt(minify) {
      const raw = src.value;
      const kind = kindSel.value === "auto" ? detectFmt(raw) : kindSel.value;
      if (!kind) { App.toast("无法识别输入格式", "error"); return; }
      try {
        let text;
        if (kind === "json") {
          const obj = JSON.parse(raw);
          text = minify ? JSON.stringify(obj) : JSON.stringify(obj, null, 2);
        } else if (kind === "xml") {
          text = minify ? raw : formatXml(raw);
        } else {
          text = minify ? raw.replace(/\s+/g, " ").trim() : formatSql(raw);
        }
        out.set([{ label: `${kind.toUpperCase()} · ${minify ? "压缩" : "美化"}`, text,
                   ts: Math.floor(Date.now() / 1000) }]);
      } catch (e) {
        out.set([{ label: "格式化失败", text: e.message }]);
      }
    }
    return pane(
      field("格式：", kindSel),
      src,
      actions(
        App.h("button", { class: "btn sm primary", onclick: () => fmt(false) }, "格式化"),
        App.h("button", { class: "btn sm", onclick: () => fmt(true) }, "压缩"),
      ),
      out.el,
    );
  }

  function paneCodec() {
    const kind = boxSel(["base64", "url", "html", "unicode"]);
    const act = App.h("select", { class: "input" },
      App.h("option", { value: "enc" }, "编码"),
      App.h("option", { value: "dec" }, "解码"));
    const src = ta(5, "输入文本…");
    const out = App.toolOut({ saveName: "编解码" });
    const utf8 = new TextEncoder();
    const utf8d = new TextDecoder();
    function run() {
      const v = src.value;
      try {
        let text;
        if (kind.value === "base64") {
          text = act.value === "enc"
            ? btoa(String.fromCharCode(...utf8.encode(v)))
            : utf8d.decode(Uint8Array.from(atob(v.trim()), (c) => c.charCodeAt(0)));
        } else if (kind.value === "url") {
          text = act.value === "enc" ? encodeURIComponent(v) : decodeURIComponent(v.trim());
        } else if (kind.value === "html") {
          text = act.value === "enc" ? v.replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
          }[c])) : v.replace(/&(#x?\d+|amp|lt|gt|quot|apos|nbsp|ensp|emsp);/gi, (m) => {
            const el = document.createElement("textarea");
            el.innerHTML = m;
            return el.value;
          });
        } else {
          text = act.value === "enc"
            ? [...v].map((c) => "\\u" + c.codePointAt(0).toString(16).padStart(4, "0")).join("")
            : v.replace(/\\u([0-9a-fA-F]{2,6})/g, (_, h) => String.fromCodePoint(parseInt(h, 16)));
        }
        out.set([{ label: `${kind.value} · ${act.value === "enc" ? "编码" : "解码"}`, text,
                   ts: Math.floor(Date.now() / 1000) }]);
      } catch (e) {
        out.set([{ label: "失败", text: e.message }]);
      }
    }
    [kind, act].forEach((s) => s.addEventListener("change", run));
    return pane(
      App.row(field("类型：", kind), field("操作：", act)),
      src,
      actions(App.h("button", { class: "btn sm primary", onclick: run }, "转换")),
      out.el,
    );
  }

  /* 逐行 LCS（行数过大时降级为公共前后缀裁剪 + 中段整块标记） */
  function diffLines(aText, bText) {
    const a = aText.split("\n");
    const b = bText.split("\n");
    const ops = [];
    let pre = 0;
    while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
    let suf = 0;
    while (suf < a.length - pre && suf < b.length - pre &&
           a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
    for (let i = 0; i < pre; i++) ops.push({ t: "ctx", s: a[i] });
    const A = a.slice(pre, a.length - suf);
    const B = b.slice(pre, b.length - suf);
    const n = A.length, m = B.length;
    if (n * m > 4000000) {
      A.forEach((s) => ops.push({ t: "del", s }));
      B.forEach((s) => ops.push({ t: "add", s }));
    } else {
      const w = m + 1;
      const dp = new Uint32Array((n + 1) * w);
      for (let i = n - 1; i >= 0; i--) {
        for (let j = m - 1; j >= 0; j--) {
          dp[i * w + j] = A[i] === B[j]
            ? dp[(i + 1) * w + j + 1] + 1
            : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1]);
        }
      }
      let i = 0, j = 0;
      while (i < n && j < m) {
        if (A[i] === B[j]) { ops.push({ t: "ctx", s: A[i] }); i++; j++; }
        else if (dp[i * w + j + 1] >= dp[(i + 1) * w + j]) { ops.push({ t: "add", s: B[j] }); j++; }
        else { ops.push({ t: "del", s: A[i] }); i++; }
      }
      while (i < n) { ops.push({ t: "del", s: A[i] }); i++; }
      while (j < m) { ops.push({ t: "add", s: B[j] }); j++; }
    }
    for (let i = a.length - suf; i < a.length; i++) ops.push({ t: "ctx", s: a[i] });
    return ops;
  }

  function paneDiff() {
    const left = ta(10, "原始文本…");
    const right = ta(10, "修改后的文本…");
    const out = App.toolOut({ saveName: "文本对比" });
    function run() {
      const ops = diffLines(left.value, right.value);
      const add = ops.filter((o) => o.t === "add").length;
      const del = ops.filter((o) => o.t === "del").length;
      const box = App.h("div", { class: "mono", style: { fontSize: "12px" } });
      for (const o of ops) {
        box.appendChild(App.h("div", {
          class: "tool-out-text " + (o.t === "del" ? "diff-del" : (o.t === "add" ? "diff-add" : "diff-ctx")),
          style: { whiteSpace: "pre-wrap", wordBreak: "break-all" },
        }, (o.t === "del" ? "− " : (o.t === "add" ? "+ " : "  ")) + o.s));
      }
      out.set([
        { label: "差异汇总", text: `+${add} 行新增 · −${del} 行删除 · ${ops.length - add - del} 行相同` },
        { label: "逐行对比", el: box },
      ]);
    }
    return pane(
      App.row(
        App.h("div", { style: { flex: 1, minWidth: "0" } }, left),
        App.h("div", { style: { flex: 1, minWidth: "0" } }, right),
      ),
      actions(App.h("button", { class: "btn sm primary", onclick: run }, "对比"),
        App.h("span", { class: "hint" }, "逐行对比；行数过大时自动降级为整块标记")),
      out.el,
    );
  }

  function buildText() {
    const nav = App.subnav([
      { label: "格式化", el: paneFormat() },
      { label: "编码解码", el: paneCodec() },
      { label: "文本对比", el: paneDiff() },
    ]);
    const wrap = App.h("div", null, nav);
    nav.panes.forEach((p) => wrap.appendChild(p));
    return [wrap];
  }

  /* ================= 3 正则测试 ================= */
  function buildRegex() {
    const pat = App.h("input", { class: "input mono", placeholder: "正则表达式，如 1[3-9]\\d{9}" });
    const flag = App.h("input", { class: "input mono", placeholder: "gim",
      style: { width: "90px", flex: "none" } });
    const sample = ta(6, "在此粘贴测试文本…");
    const out = App.toolOut({ saveName: "正则测试" });
    const presets = App.h("select", { class: "input" },
      [["", "常用模板…"], ["1[3-9]\\d{9}", "手机号"], ["\\d{17}[\\dXx]", "身份证"],
        ["(\\d{1,3}\\.){3}\\d{1,3}", "IPv4"], ["https?://\\S+", "URL"],
        ["[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "邮箱"]]
        .map(([v, t]) => App.h("option", { value: v }, t)));
    presets.addEventListener("change", () => {
      if (presets.value) { pat.value = presets.value; presets.value = ""; run(); }
    });
    function run() {
      let re;
      try {
        const flags = flag.value.includes("g") ? flag.value : flag.value + "g";
        re = new RegExp(pat.value, flags);
      } catch (e) {
        out.set([{ label: "正则错误", text: e.message }]);
        return;
      }
      const text = sample.value;
      if (!text) return;
      const matches = [...text.matchAll(re)];
      const summary = `共 ${matches.length} 个匹配` +
        (flag.value.includes("g") ? "" : "（未开启 g 标志，已自动补全以便展示）");
      const body = App.h("div", { style: { whiteSpace: "pre-wrap", wordBreak: "break-all", fontSize: "12px" } });
      let last = 0;
      const frag = document.createDocumentFragment();
      for (const m of matches) {
        if (m.index > last) frag.append(document.createTextNode(text.slice(last, m.index)));
        frag.append(App.h("mark", { style: { background: "var(--accent-soft)", color: "var(--accent)", borderRadius: "3px" } }, m[0]));
        last = m.index + m[0].length;
        if (!m[0].length) break;   // 空匹配防死循环
      }
      frag.append(document.createTextNode(text.slice(last)));
      body.append(...frag);
      const items = [{ label: "结果", text: summary }];
      if (matches[0] && matches[0].length > 1) {
        items.push({ label: "分组明细（首处）", text: matches[0].slice(1)
          .map((g, i) => `分组${i + 1}：${g === undefined ? "未参与" : g}`).join("　·　") });
      }
      items.push({ label: "高亮预览", el: body });
      out.set(items);
    }
    return pane(
      field("表达式：", pat),
      App.row(field("标志：", flag), field("常用模板：", presets)),
      sample,
      actions(App.h("button", { class: "btn sm primary", onclick: run }, "测试")),
      out.el,
    );
  }

  /* ================= 4 进制转换（BigInt） ================= */
  const RADIX_DIGITS = "0123456789abcdef";
  function parseBig(s, base) {
    s = String(s || "").trim().toLowerCase();
    let neg = false;
    if (s.startsWith("-")) { neg = true; s = s.slice(1); }
    else if (s.startsWith("+")) { s = s.slice(1); }
    s = s.replace(/[\s_]/g, "");
    if (!s) throw new Error("空输入");
    const digits = RADIX_DIGITS.slice(0, base);
    let v = 0n;
    const b = BigInt(base);
    for (const ch of s) {
      const d = digits.indexOf(ch);
      if (d < 0) throw new Error(`字符「${ch}」不是 ${base} 进制数字`);
      v = v * b + BigInt(d);
    }
    return neg ? -v : v;
  }
  function buildRadix() {
    const inp = ta(3, "输入整数（可多行，每行一个；支持任意长度大数）");
    const baseSel = boxSel(["10", "2", "8", "16"]);
    const err = App.h("div", { class: "hint", style: { color: "var(--danger)", whiteSpace: "pre-line" } });
    const out = App.toolOut({ saveName: "进制转换" });
    const bIn = App.h("input", { class: "input mono", placeholder: "第二操作数（10 进制整数）" });
    const opOut = App.toolOut({ saveName: "位运算" });
    function rows(v) {
      return [2, 8, 10, 16].map((r) => ({ label: r + " 进制", text: v.toString(r) }));
    }
    function render() {
      err.textContent = "";
      const items = [];
      const lines = inp.value.split("\n").map((s) => s.trim()).filter(Boolean);
      for (let i = 0; i < lines.length; i++) {
        try {
          const v = parseBig(lines[i], parseInt(baseSel.value, 10));
          items.push(...rows(v).map((r) => ({ ...r, label: `第 ${i + 1} 行 · ${r.label}` })));
        } catch (e) {
          err.textContent += `第 ${i + 1} 行：${e.message}\n`;
        }
      }
      out.set(items);
    }
    inp.addEventListener("input", render);
    baseSel.addEventListener("change", render);
    function runOp(op) {
      let a, b;
      try {
        const first = inp.value.split("\n").map((s) => s.trim()).filter(Boolean)[0];
        if (first == null) { App.toast("请先在上方输入一个数值", "warn"); return; }
        a = parseBig(first, parseInt(baseSel.value, 10));
        b = parseBig(bIn.value, 10);
      } catch (e) {
        opOut.set([{ label: "错误", text: e.message }]);
        return;
      }
      let v;
      try {
        if (op === "and") v = a & b;
        else if (op === "or") v = a | b;
        else if (op === "xor") v = a ^ b;
        else if (op === "shl") v = a << b;
        else if (op === "shr") v = a >> b;
      } catch (e) {
        opOut.set([{ label: "错误", text: "运算失败：" + e.message }]);
        return;
      }
      opOut.set(rows(v).map((r) => ({ ...r, label: `${op} · ${r.label}` })));
    }
    const opBtns = [["and", "AND"], ["or", "OR"], ["xor", "XOR"], ["shl", "左移 <<"], ["shr", "右移 >>"]]
      .map(([op, label]) => App.h("button", { class: "btn sm", onclick: () => runOp(op) }, label));
    return pane(
      App.row(field("输入进制：", baseSel)),
      inp,
      err,
      out.el,
      App.h("div", { class: "card-title", style: { marginTop: "12px" } }, "位运算（对第一行数值）"),
      App.row(field("操作数：", bIn), ...opBtns),
      opOut.el,
    );
  }

  /* ================= 5 随机密码 ================= */
  function buildPassword() {
    const len = App.h("input", { class: "input mono", type: "number", value: "16", min: "4", max: "64", style: { width: "90px", flex: "none" } });
    const bands = {
      upper: ["大写 A-Z", true], lower: ["小写 a-z", true],
      digit: ["数字 0-9", true], symbol: ["符号 !@#$", true],
    };
    const cbs = {};
    const rowBox = App.h("div", { class: "row" });
    for (const [k, [label, def]] of Object.entries(bands)) {
      cbs[k] = App.h("input", { type: "checkbox" });
      cbs[k].checked = def;                       // DOM 属性赋值，避免 setAttribute 误勾选
      rowBox.append(App.h("label", { class: "chk" }, cbs[k], " ", label));
    }
    const similar = App.h("input", { type: "checkbox" });
    rowBox.append(App.h("label", { class: "chk" }, similar, " 排除相似字符(O/0·I/l·1)"));
    const out = App.toolOut({ saveName: "随机密码" });
    function gen() {
      const pool = [];
      if (cbs.upper.checked) pool.push("ABCDEFGHIJKLMNOPQRSTUVWXYZ");
      if (cbs.lower.checked) pool.push("abcdefghijklmnopqrstuvwxyz");
      if (cbs.digit.checked) pool.push("0123456789");
      if (cbs.symbol.checked) pool.push("!@#$%^&*()-_=+[]{};:,.?");
      let chars = pool.join("");
      if (similar.checked) chars = chars.replace(/[O0Il1]/g, "");
      if (!chars) { App.toast("请至少选择一种字符类型", "error"); return null; }
      const n = Math.max(4, Math.min(64, parseInt(len.value, 10) || 16));
      const bytes = new Uint32Array(n);
      crypto.getRandomValues(bytes);
      let pwd = "";
      for (const x of bytes) pwd += chars[x % chars.length];
      const entropy = Math.round(n * Math.log2(chars.length));
      const score = entropy >= 80 ? "强" : (entropy >= 40 ? "中" : "弱");
      out.set([{ label: `密码（熵 ${entropy} bit · ${score}）`, text: pwd,
                 ts: Math.floor(Date.now() / 1000) }]);
      return pwd;
    }
    return pane(
      field("长度：", len),
      rowBox,
      actions(App.h("button", { class: "btn sm primary", onclick: () => { gen(); } }, "生成")),
      out.el,
    );
  }

  /* ================= 6 UUID 生成 ================= */
  function uuidV7() {
    const ts = BigInt(Date.now());
    const b = new Uint8Array(16);
    crypto.getRandomValues(b);
    for (let i = 0; i < 6; i++) {
      b[i] = Number((ts >> BigInt(8 * (5 - i))) & 0xffn);
    }
    b[6] = (b[6] & 0x0f) | 0x70;    // version 7
    b[8] = (b[8] & 0x3f) | 0x80;    // variant 10
    const hex = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  function buildUuid() {
    const verSel = App.h("select", { class: "input" },
      App.h("option", { value: "v4" }, "v4（随机）"),
      App.h("option", { value: "v7" }, "v7（时间有序）"));
    const cntIn = App.h("input", { class: "input mono", type: "number", value: "5",
      min: "1", max: "100", style: { width: "90px", flex: "none" } });
    const fmtSel = App.h("select", { class: "input" },
      App.h("option", { value: "lower" }, "小写"),
      App.h("option", { value: "upper" }, "大写"),
      App.h("option", { value: "nohyphen" }, "去连字符"),
      App.h("option", { value: "brace" }, "大括号"),
      App.h("option", { value: "json" }, "JSON 数组"));
    const out = App.toolOut({ saveName: "UUID" });
    function shape(u) {
      if (fmtSel.value === "upper") return u.toUpperCase();
      if (fmtSel.value === "nohyphen") return u.replace(/-/g, "");
      if (fmtSel.value === "brace") return "{" + u + "}";
      return u;
    }
    function gen() {
      const n = Math.max(1, Math.min(100, parseInt(cntIn.value, 10) || 5));
      const list = [];
      for (let i = 0; i < n; i++) {
        list.push(verSel.value === "v7" ? uuidV7() : crypto.randomUUID());
      }
      if (fmtSel.value === "json") {
        out.set([{ label: `JSON 数组 · ${n} 个`, text: JSON.stringify(list, null, 2),
                   ts: Math.floor(Date.now() / 1000) }]);
      } else {
        out.set(list.map((u, i) => ({ label: `#${i + 1}`, text: shape(u),
                                      ts: Math.floor(Date.now() / 1000) })));
      }
    }
    [verSel, fmtSel].forEach((s) => s.addEventListener("change", gen));
    return pane(
      App.row(field("版本：", verSel), field("数量：", cntIn), field("格式：", fmtSel)),
      actions(App.h("button", { class: "btn sm primary", onclick: gen }, "生成"),
        App.h("span", { class: "hint" }, "浏览器安全随机；单条可复制，也可「另存 txt」")),
      out.el,
    );
  }

  /* ================= 7 二维码 ================= */
  function buildQr() {
    const text = ta(4, "输入文本或链接，生成二维码…");
    const sizeSel = boxSel(["180", "280", "420", "640"]);
    sizeSel.value = "280";
    const out = App.toolOut({ saveName: "二维码" });
    async function gen() {
      if (!text.value.trim()) { App.toast("请输入内容", "error"); return; }
      const r = await App.tryCall("tool_qr", text.value.trim(), parseInt(sizeSel.value, 10));
      if (!r.ok) { App.toast(r.err, "error"); return; }
      const img = App.h("img", { src: r.data.img, alt: "二维码",
        style: { maxWidth: "240px", border: "1px solid var(--border)", borderRadius: "6px" } });
      const a = App.h("a", { href: r.data.img, download: "qrcode.png", class: "btn sm" }, "下载 PNG");
      a.onclick = () => { setTimeout(() => { App.toast("已开始下载", "ok"); }, 80); };
      out.set([{ label: "二维码（" + sizeSel.value + "px）",
                 el: App.h("div", { style: { textAlign: "center" } }, img,
                        App.h("div", { style: { marginTop: "6px" } }, a)) }]);
    }
    return pane(
      field("尺寸：", sizeSel),
      text,
      actions(App.h("button", { class: "btn sm primary", onclick: gen }, "生成二维码")),
      out.el,
    );
  }

  /* ================= 8 文件校验中心（哈希 / 清单校验 / 目录快照 / 列表导出） ================= */
  function paneHash() {
    const picked = [];
    const pickInfo = App.h("span", { class: "hint" }, "未选择文件/目录");
    const algoSel = boxSel(["sha256", "sha1", "md5"]);
    const list = App.h("div", { class: "list", style: { maxHeight: "180px", overflow: "auto" } });
    const out = App.toolOut({ saveName: "哈希校验" });
    async function pick(folder) {
      const r = await App.tryCall("tool_hash_pick", !!folder);
      if (!r.ok) return;
      for (const p of (r.data || [])) if (!picked.includes(p)) picked.push(p);
      pickInfo.textContent = `已选择 ${picked.length} 项`;
      renderList();
    }
    function renderList() {
      list.innerHTML = "";
      for (const p of picked) {
        list.appendChild(App.h("div", { class: "list-item" },
          App.h("span", { class: "li-main mono", style: { wordBreak: "break-all" } }, p),
          App.h("button", { class: "btn sm danger", onclick: () => {
            const i = picked.indexOf(p); if (i >= 0) picked.splice(i, 1);
            renderList(); pickInfo.textContent = `已选择 ${picked.length} 项`;
          } }, "移除"),
        ));
      }
      if (!picked.length) list.appendChild(App.h("div", { class: "empty" }, "暂未选择文件/目录"));
    }
    let hashing = false;
    let hashBtn = null;
    async function run() {
      if (hashing || !picked.length) {
        if (!picked.length) App.toast("请先选择文件或目录", "error");
        return;
      }
      hashing = true;
      hashBtn.disabled = true; hashBtn.textContent = "计算中…";
      try {
        App.toast("计算中，文件较多时请稍候…", "info", 4000);
        const r = await App.tryCall("tool_hash", picked, algoSel.value);
        if (!r.ok) { App.toast(r.err, "error"); return; }
        const d = r.data;
        const items = d.results.map((f) => ({
          label: f.path, text: `${f.digest}　·　${fmtSize(f.size)}`,
          ts: Math.floor(Date.now() / 1000),
        }));
        for (const f of (d.fails || [])) {
          items.push({ label: "无法读取", el: App.h("div", { class: "tool-out-text diff-del" },
            f.path + (f.err ? `（${f.err}）` : "")) });
        }
        out.set(items);
      } finally {
        hashing = false;
        hashBtn.disabled = false; hashBtn.textContent = "开始计算";
      }
    }
    renderList();
    return pane(
      field("算法：", algoSel),
      actions(
        App.h("button", { class: "btn sm", onclick: () => pick(false) }, "选择文件"),
        App.h("button", { class: "btn sm", onclick: () => pick(true) }, "选择目录"),
        spacer(),
        pickInfo,
        hashBtn = App.h("button", { class: "btn sm primary", onclick: run }, "开始计算"),
      ),
      list,
      out.el,
    );
  }

  function paneVerify() {
    const picked = App.h("span", { class: "hint" }, "未选择清单");
    const out = App.toolOut({ saveName: "清单校验" });
    async function pick() {
      const r = await App.tryCall("tool_manifest_pick");
      if (!r.ok || !r.data || !r.data.length) return;
      picked.textContent = r.data[0];
      const v = await App.tryCall("tool_verify", r.data[0]);
      if (!v.ok) { App.toast(v.err, "error"); return; }
      const d = v.data;
      const items = [{ label: d.ok ? "全部通过" : "存在问题",
                       text: `${d.ok_count}/${d.total} 项校验成功`,
                       ts: Math.floor(Date.now() / 1000) }];
      for (const f of (d.fails || [])) {
        items.push({ label: f.path || "第 " + f.line + " 行",
                     el: App.h("div", { class: "tool-out-text diff-del" }, f.reason) });
      }
      out.set(items);
    }
    return pane(
      App.h("p", { class: "hint", style: { margin: "0 0 6px" } },
        "选择 .md5 清单文件，逐行核对路径对应文件的哈希；支持标准格式（hash *path 或 hash  path，路径相对清单目录）。"),
      actions(App.h("button", { class: "btn sm primary", onclick: pick }, "选择清单校验"), spacer(), picked),
      out.el,
    );
  }

  function paneSnapshot() {
    const src = App.h("input", { class: "input", placeholder: "源目录（仅复制目录骨架）", readonly: true });
    const dest = App.h("input", { class: "input", placeholder: "目标目录", readonly: true });
    const out = App.toolOut({ saveName: "目录快照" });
    async function pickInput(input) {
      const r = await App.tryCall("tool_hash_pick", true);
      if (r.ok && r.data && r.data.length) input.value = r.data[0];
    }
    async function run() {
      const r = await App.tryCall("tool_snapshot", src.value, dest.value);
      if (r.ok) {
        out.set([{ label: "目录骨架", text: `已创建 ${r.data.created} 个目录`,
                   ts: Math.floor(Date.now() / 1000) }]);
        App.toast(`已创建 ${r.data.created} 个目录骨架`, "ok");
      } else {
        App.toast(r.err, "error", 6000);
      }
    }
    return pane(
      App.row(field("源：", src),
        App.h("button", { class: "btn sm", onclick: () => pickInput(src) }, "选择")),
      App.row(field("目标：", dest),
        App.h("button", { class: "btn sm", onclick: () => pickInput(dest) }, "选择")),
      actions(App.h("button", { class: "btn sm primary", onclick: run }, "创建目录骨架")),
      out.el,
    );
  }

  function paneExport() {
    const src = App.h("input", { class: "input", placeholder: "选择要导出的目录", readonly: true });
    const withHash = App.h("input", { type: "checkbox" });
    const includeSub = App.h("input", { type: "checkbox" });
    includeSub.checked = true;                   // DOM 属性赋值，避免 setAttribute 误勾选
    const algoSel = boxSel(["sha256", "md5"]);
    algoSel.style.display = "none";              // 附加哈希未勾选时隐藏算法选择
    withHash.addEventListener("change", () => {
      algoSel.style.display = withHash.checked ? "" : "none";
    });
    const out = App.toolOut({ saveName: "列表导出" });
    async function run() {
      if (!src.value) {
        const r0 = await App.tryCall("tool_export_pick");
        if (!r0.ok || !r0.data || !r0.data.length) return;
        src.value = r0.data[0];
      }
      const r = await App.tryCall("tool_export", src.value, includeSub.checked, withHash.checked, algoSel.value);
      if (!r.ok) { App.toast(r.err, "error", 6000); return; }
      out.set([{ label: "CSV 已导出", text: `${r.data.path}（${r.data.count} 行）`,
                 ts: Math.floor(Date.now() / 1000) }]);
      App.toast(`已导出 ${r.data.count} 行`, "ok", 4000);
    }
    return pane(
      App.row(field("目录：", src),
        App.h("button", { class: "btn sm", onclick: async () => {
          const r = await App.tryCall("tool_export_pick");
          if (r.ok && r.data && r.data.length) { src.value = r.data[0]; run(); }
        } }, "选择并导出")),
      App.row(
        App.h("label", { class: "chk" }, includeSub, " 包含子目录"),
        App.h("label", { class: "chk" }, withHash, " 附加哈希"),
        algoSel,
      ),
      actions(App.h("button", { class: "btn sm primary", onclick: run }, "导出 CSV")),
      out.el,
    );
  }

  function buildFiles() {
    const nav = App.subnav([
      { label: "哈希计算", el: paneHash() },
      { label: "清单校验", el: paneVerify() },
      { label: "目录快照", el: paneSnapshot() },
      { label: "列表导出", el: paneExport() },
    ]);
    const wrap = App.h("div", null, nav);
    nav.panes.forEach((p) => wrap.appendChild(p));
    return [wrap];
  }

  /* ================= 9 端口占用 ================= */
  function buildPort() {
    const inp = App.h("input", { class: "input mono", placeholder: "端口号，如 8080" });
    const out = App.toolOut({ saveName: "端口占用" });
    async function query() {
      const r = await App.tryCall("tool_port_query", inp.value);
      if (!r.ok) { App.toast(r.err, "error"); return; }
      const list = r.data || [];
      if (!list.length) {
        out.set([{ label: "查询结果", text: "该端口当前无监听进程" }]);
        return;
      }
      out.set(list.map((it) => ({
        label: `${it.name || "未知进程"}（PID ${it.pid}）`,
        el: App.h("div", { class: "row", style: { gap: "8px" } },
          App.h("span", { class: "tool-out-text mono" }, `监听 ${it.port} 端口`),
          App.h("button", { class: "btn sm danger", onclick: async () => {
            if (!(await App.confirm("结束进程", `确定结束进程 ${it.name}（PID ${it.pid}）？`))) return;
            const k = await App.tryCall("tool_port_kill", it.pid);
            if (k.ok) { App.toast(k.data || "已结束", "ok"); query(); }
            else App.toast(k.err, "error", 6000);
          } }, "结束进程"),
        ),
      })));
    }
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") query(); });
    return pane(
      App.row(field("端口：", inp),
        App.h("button", { class: "btn sm primary", onclick: query }, "查询")),
      App.h("p", { class: "hint" }, "基于 PowerShell Get-NetTCPConnection；结束他人进程可能需要管理员权限。"),
      out.el,
    );
  }

  /* ================= 10 代理快速开关 ================= */
  function buildProxyQuickSwitch() {
    const status = App.h("div", { class: "list" }, App.h("div", { class: "empty" }, "查询中…"));
    const stBtn = App.h("button", { class: "btn primary", onclick: doStart }, "启动代理");
    const spBtn = App.h("button", { class: "btn", disabled: true, onclick: doStop }, "停止");
    spBtn.disabled = true;   // setAttribute("disabled", false) 同样会禁用，改用属性赋值
    async function refresh() {
      const r = await App.tryCall("proxy_get_state");
      if (!r.ok) {
        status.replaceChildren(App.h("div", { class: "empty" }, r.err || "查询失败"));
        return;
      }
      const st = r.data;
      status.replaceChildren(
        App.h("div", { class: "list-item",
          style: { padding: "6px 10px", justifyContent: "flex-start", gap: "8px" } },
          st.running ? App.statusTag("运行中", "ok", "check") : App.statusTag("未运行", "warn", "zap"),
          App.h("span", { class: "li-main" },
            App.h("span", { class: "li-title" }, `${st.core_type || "xray"} · ${st.mode || "smart"}` +
              (st.sys_proxy_mode && st.sys_proxy_mode !== "auto" ? ` · 代理:${st.sys_proxy_mode}` : "")),
            App.h("span", { class: "li-sub" },
              st.current ? `${st.current.remark}${st.latency != null ? ` · ${st.latency}ms` : ""}` : "（未选择节点）"),
          )),
      );
      stBtn.disabled = !!st.running;
      spBtn.disabled = !st.running;
    }
    async function doStart() {
      const r = await App.tryCall("proxy_start");
      if (!r.ok) App.toast(r.err, "error", 8000);
      await refresh();
    }
    async function doStop() {
      const r = await App.tryCall("proxy_stop");
      if (!r.ok) App.toast(r.err, "error", 8000);
      await refresh();
    }
    refresh();
    return pane(
      App.h("p", { class: "hint", style: { margin: "0 0 6px" } },
        "核心 / 模式 / 节点在「V2rayN」页配置；此处一键启停。"),
      status,
      actions(stBtn, spacer(), spBtn),
    );
  }

  /* ================= Wi-Fi 密码查看 ================= */
  function buildWifi() {
    const list = App.h("div", { class: "list", style: { maxHeight: "420px", overflowY: "auto" } });
    const status = App.h("span", { class: "hint", style: { marginLeft: "auto" } });
    async function showPwd(name, detail, btn) {
      if (!detail.dataset.loaded) {
        btn.disabled = true;
        const r = await App.tryCall("tool_wifi_password", name);
        btn.disabled = false;
        if (!r.ok) { App.toast(r.err, "error"); return; }
        detail.dataset.loaded = "1";
        const pwd = r.data.has_pwd ? r.data.password : "（开放网络或未保存密码）";
        /* v5.2：复制/隐藏同处密码行，保持水平 */
        detail.replaceChildren(
          App.h("div", { class: "row", style: { margin: "0", flexWrap: "nowrap" } },
            r.data.auth ? App.h("span", { class: "tag accent", style: { flex: "none" } }, r.data.auth) : null,
            App.h("span", {
              class: "mono", style: {
                flex: "1", minWidth: "0", wordBreak: "break-all",
                userSelect: "all",
                color: r.data.has_pwd ? "var(--text)" : "var(--muted)",
              },
            }, pwd),
            r.data.has_pwd ? App.h("button", { class: "btn xs", style: { flex: "none" }, onclick: () => {
              App.copyText(pwd).then(() => App.toast("密码已复制", "ok"));
            } }, "复制") : null,
            App.h("button", { class: "btn xs", style: { flex: "none" }, onclick: () => {
              detail.style.display = "none";
              btn.style.display = "";
            } }, "隐藏"),
          ),
        );
      }
      detail.style.display = "";
      btn.style.display = "none";   /* 展开时右侧按钮让位，避免与「隐藏」错位 */
    }
    async function refresh() {
      list.innerHTML = "";
      status.textContent = "读取中…";
      const r = await App.tryCall("tool_wifi_list");
      if (!r.ok) {
        status.textContent = "";
        list.appendChild(App.h("div", { class: "empty" }, r.err));
        return;
      }
      status.textContent = `已保存 ${r.data.count} 个网络`;
      if (!r.data.names.length) {
        list.appendChild(App.h("div", { class: "empty" },
          "本机没有已保存的 WLAN 网络\n（或没有无线网卡）"));
        return;
      }
      for (const name of r.data.names) {
        const detail = App.h("div", { style: { display: "none", marginTop: "6px" } });
        const btn = App.h("button", {
          class: "btn sm", style: { flex: "none" },
          onclick: () => showPwd(name, detail, btn),
        }, "显示密码");
        list.appendChild(App.h("div", { class: "list-item" },
          App.h("span", { class: "li-main", style: { display: "flex", flexDirection: "column", gap: "4px" } },
            App.h("div", { class: "li-title" }, name),
            detail),
          btn,
        ));
      }
    }
    refresh();
    return pane(
      App.h("p", { class: "hint", style: { margin: "0 0 6px" } },
        "列出本机曾连接并保存的 Wi-Fi 网络；「显示密码」读取本机 netsh 存储的明文密钥（仅本机数据，不联网）。"),
      actions(App.h("button", { class: "btn", onclick: refresh }, "刷新列表"), spacer(), status),
      list,
    );
  }

  /* ================= 硬件信息 ================= */
  function buildHardware() {
    /* 通用 pane 容器：懒加载 + 加载占位 + 失败重试（UI 规范 §8.3/§8.4） */
    function makePane(loader) {
      const box = App.h("div", { style: { minHeight: "140px" } });
      const state2 = { loaded: false, loading: false };
      async function load(force) {
        if (state2.loading || (state2.loaded && !force)) return;
        state2.loading = true;
        box.replaceChildren(App.h("div", { class: "empty" }, "正在读取硬件信息…"));
        const r = await App.tryCall(loader.api);
        state2.loading = false;
        if (!r.ok) {
          box.replaceChildren(
            App.h("div", { class: "empty" }, "读取失败：" + (r.err || "未知错误")),
            App.h("div", { style: { textAlign: "center", marginTop: "8px" } },
              App.h("button", { class: "btn sm", onclick: () => load(true) }, "重试")));
          return;
        }
        state2.loaded = true;
        box.replaceChildren(...loader.render(r.data));
      }
      const el = App.h("div", null, box);
      return { el, load, box };
    }

    function propRow(key, val, mono) {
      if (val == null || val === "") val = "—";
      return App.h("div", { class: "prop-row" },
        App.h("span", { class: "prop-key" }, key),
        App.h("span", { class: "prop-val" + (mono ? " mono" : "") }, String(val)));
    }
    /* 使用率条（file 状态栏同款进度条样式） */
    function usageBar(label, pct, right) {
      const color = pct > 90 ? "var(--danger)" : pct > 75 ? "var(--warn)" : "var(--accent)";
      return App.h("div", { class: "row", style: { margin: "0 0 6px", flexWrap: "nowrap" } },
        App.h("span", { class: "field-label", style: { width: "56px", flex: "none" } }, label),
        App.h("div", {
          style: {
            flex: "1", height: "8px", borderRadius: "4px", overflow: "hidden",
            background: "var(--card2)",
          },
        }, App.h("div", {
          style: {
            height: "100%", width: Math.max(2, Math.min(100, pct || 0)) + "%",
            background: color, transition: "width .3s",
          },
        })),
        App.h("span", { class: "hint mono", style: { width: right || "56px", flex: "none", textAlign: "right" } },
          (pct != null ? pct : "—") + "%"),
      );
    }

    /* --- 概览 --- */
    let liveTimer = null;
    let lastLiveKey = "";
    function paneOverview() {
      const liveBox = App.h("div", { style: { marginBottom: "10px" } });
      const infoBox = App.h("div", null, App.h("div", { class: "empty" }, "正在读取硬件信息…"));
      const p = makePane({
        api: "tool_hw_summary",
        render: (d) => {
          const rows = [
            ["电脑", [d.vendor, d.model].filter(Boolean).join(" ") || d.computer],
            ["主机名", d.computer],
            ["系统", d.sys ? `${d.sys}（Build ${d.build}）` : ""],
            ["开机时长", d.uptime_h != null ? d.uptime_h + " 小时" : ""],
            ["CPU", d.cpu ? `${d.cpu}（${d.cpu_cores}核/${d.cpu_threads}线程 · ${d.cpu_ghz}GHz）` : ""],
            ["内存", d.mem_gb != null ? `${d.mem_gb} GB（${d.mem_bars} 条插槽）` : ""],
            ["显卡", (d.gpus || []).join(" · ")],
            ["磁盘", d.disk_total_tb != null ? `${d.disk_total_tb} TB` : ""],
            ["分区容量", d.ld_total_gb != null
              ? `已用 ${d.ld_used_gb} / ${d.ld_total_gb} GB` : ""],
          ];
          return [App.h("div", { class: "card", style: { padding: "12px 14px" } },
            rows.map(([k, v]) => propRow(k, v))),
          ];
        },
      });
      async function pollLive() {
        clearTimeout(liveTimer);
        liveTimer = setTimeout(async () => {
          /* 页面非活动时不请求（节能），保留轮询节拍 */
          if (document.querySelector(".page.active#page-tool-sysinfo")) {
            const r = await App.tryCall("tool_hw_live");
            if (r.ok) {
              const d = r.data;
              const key = JSON.stringify(d);
              if (key !== lastLiveKey) {   /* 快照比对（UI 规范 §9.1） */
                lastLiveKey = key;
                const items = [
                  usageBar("CPU", d.cpu),
                  usageBar("内存", d.mem_pct,
                    `${d.mem_used_gb || 0} / ${d.mem_total_gb || 0}G`),
                ];
                for (const dk of d.disks || []) {
                  items.push(usageBar("盘" + (dk.drive || "").replace(":", ""), dk.pct));
                }
                liveBox.replaceChildren(
                  App.h("div", { class: "card", style: { padding: "12px 14px", marginBottom: "10px" } },
                    App.h("div", { class: "card-title", style: { fontSize: "13px", marginBottom: "8px" } }, "实时状态"),
                    ...items));
              }
            }
          }
          pollLive();
        }, 10000);
      }
      return {
        el: App.h("div", null, liveBox, infoBox, p.el),
        load: async () => { await p.load(); pollLive(); },
      };
    }

    /* --- 硬件明细 --- */
    function paneDetail() {
      const p = makePane({
        api: "tool_hw_detail",
        render: (d) => {
          const cards = [];
          const cpu = d.cpu || {};
          cards.push(App.h("div", { class: "card", style: { padding: "12px 14px", marginBottom: "10px" } },
            App.h("div", { class: "card-title" }, "CPU"),
            propRow("型号", cpu.name),
            propRow("核心/线程", cpu.cores != null ? `${cpu.cores} 核 / ${cpu.threads} 线程` : ""),
            propRow("基准频率", cpu.base_ghz != null ? cpu.base_ghz + " GHz" : "", true),
            propRow("L2 缓存", cpu.cache_l2 ? (cpu.cache_l2 / 1024).toFixed(1) + " MB" : "", true),
            propRow("L3 缓存", cpu.cache_l3 ? (cpu.cache_l3 / 1024).toFixed(1) + " MB" : "", true),
            propRow("插槽", cpu.socket)));
          const mems = d.mems || [];
          cards.push(App.h("div", { class: "card", style: { padding: "12px 14px", marginBottom: "10px" } },
            App.h("div", { class: "card-title" }, `内存（${mems.length} 条）`),
            ...(mems.length ? mems.map((m, i) => App.h("div", null,
              propRow(`插槽 ${m.slot || i + 1}`,
                `${m.gb} GB ${m.type}${m.speed ? " · " + m.speed + "MHz" : ""}${m.mfr ? " · " + m.mfr : ""}`)))
              : [propRow("内存条", "未读取到（部分台式机/虚拟机不暴露）")])));
          cards.push(App.h("div", { class: "card", style: { padding: "12px 14px", marginBottom: "10px" } },
            App.h("div", { class: "card-title" }, "显卡"),
            ...(d.gpus || []).map((g) => App.h("div", null,
              propRow(g.name, [
                g.ram_gb != null && g.ram_gb > 0 ? `显存约 ${g.ram_gb} GB` : "",
                g.driver ? "驱动 " + g.driver : "",
                g.driver_date || "",
                g.mode || "",
              ].filter(Boolean).join(" · "))),
              ),
            ));
          cards.push(App.h("div", { class: "card", style: { padding: "12px 14px" } },
            App.h("div", { class: "card-title" }, "主板与 BIOS"),
            propRow("主板", [d.board.mfr, d.board.product].filter(Boolean).join(" ")),
            propRow("BIOS", [d.bios.mfr, d.bios.ver].filter(Boolean).join(" "), true),
            propRow("BIOS 日期", d.bios.date, true)));
          return cards;
        },
      });
      return { el: p.el, load: () => p.load() };
    }

    /* --- 存储 --- */
    function paneDisks() {
      const p = makePane({
        api: "tool_hw_disks",
        render: (d) => {
          const cards = [];
          cards.push(App.h("div", { class: "card", style: { padding: "12px 14px", marginBottom: "10px" } },
            App.h("div", { class: "card-title" }, `物理磁盘（${(d.drives || []).length}）`),
            ...(d.drives || []).map((x, i) => App.h("div", null,
              propRow(`磁盘 ${i}`,
                [x.model, x.media, x.size_gb != null ? x.size_gb + " GB" : "", x.iface]
                  .filter(Boolean).join(" · ")),
              x.serial ? propRow("序列号", x.serial, true) : null))));
          cards.push(App.h("div", { class: "card", style: { padding: "12px 14px" } },
            App.h("div", { class: "card-title" }, "分区使用率"),
            ...(d.parts || []).map((pt) => {
              const fmtG = (n) => n != null ? (n / (1 << 30)).toFixed(1) + " GB" : "—";
              return App.h("div", { style: { marginBottom: "6px" } },
                App.h("div", { class: "row", style: { margin: "0 0 3px", flexWrap: "nowrap" } },
                  App.h("span", { class: "li-title", style: { fontWeight: "600" } },
                    pt.drive + (pt.label ? " " + pt.label : "")),
                  App.h("span", { class: "hint", style: { marginLeft: "auto" } },
                    `${pt.fs || "—"} · 已用 ${pt.pct}% · 剩余 ${fmtG(pt.free)} / ${fmtG(pt.total)}`)),
                usageBar("用量", pt.pct));
            })));
          return cards;
        },
      });
      return { el: p.el, load: () => p.load() };
    }

    /* --- 网络 --- */
    function paneNet() {
      const p = makePane({
        api: "tool_hw_network",
        render: (d) => {
          const adapters = d.adapters || [];
          if (!adapters.length) {
            return [App.h("div", { class: "empty" }, "未读取到物理网卡")];
          }
          return [App.h("div", { class: "card", style: { padding: "12px 14px" } },
            App.h("div", { class: "card-title" }, `物理网卡（${adapters.length}）`),
            ...adapters.map((a) => App.h("div", {
              class: "list-item", style: { flexDirection: "column", alignItems: "stretch", gap: "3px" } },
              App.h("div", { class: "row", style: { margin: "0", flexWrap: "nowrap" } },
                a.netok ? App.statusTag("已连接", "ok", "check") : App.statusTag("未连接"),
                App.h("span", { class: "li-title", style: { marginLeft: "6px", fontWeight: "600" } },
                  a.name || "(未命名)"),
                App.h("span", { class: "hint", style: { marginLeft: "auto" } },
                  a.speed ? (a.speed / 1e9).toFixed(1) + " Gbps" : "—")),
              App.h("div", { class: "hint mono", style: { fontSize: "11px", wordBreak: "break-all" } },
                a.desc || ""),
              App.h("div", { class: "hint mono", style: { fontSize: "11px" } },
                ["MAC " + (a.mac || "—"),
                 a.ips.length ? "IP " + a.ips.join(" / ") : "",
                 a.gw.length ? "网关 " + a.gw.join(" / ") : ""]
                  .filter(Boolean).join(" · ")))),
          )];
        },
      });
      return { el: p.el, load: () => p.load() };
    }

    const panes = [paneOverview(), paneDetail(), paneDisks(), paneNet()];
    const nav = App.subnav([
      { label: "概览", el: panes[0].el },
      { label: "硬件明细", el: panes[1].el },
      { label: "存储", el: panes[2].el },
      { label: "网络", el: panes[3].el },
    ], {
      onSwitch: (i) => panes[i].load(),   /* 懒加载：首次激活才查询 */
    });
    const wrap = App.h("div", null,
      App.h("p", { class: "hint", style: { margin: "0 0 6px" } },
        "Windows 原生 CIM/WMI 只读查询，无需管理员权限；温度等不可靠传感器不提供。"),
      nav);
    nav.panes.forEach((p2) => wrap.appendChild(p2));
    panes[0].load();   /* 首签立即加载 */
    return wrap;
  }

  /* ================= 系统优化 ================= */
  function buildOptimize() {
    const sel = new Set();       // 勾选待应用
    const refs2 = {};
    let items = [];              // opt_tweaks 返回

    const applyBtn = App.h("button", {
      class: "btn primary", onclick: async function () {
        if (this._busy) return;
        if (!sel.size) { App.toast("请先勾选优化项", "warn"); return; }
        const high = [...sel].filter((id) =>
          (items.find((x) => x.id === id) || {}).risk === "high");
        if (high.length && !(await App.confirm(
          "包含高风险项", "所选含高风险项：\n" +
          high.map((id) => "· " + (items.find((x) => x.id === id) || {}).name)
            .join("\n") + "\n\n确定继续？"))) return;
        this._busy = true; this.disabled = true;
        const old = this.textContent; this.textContent = "应用中…";
        try {
          const r = await App.tryCall("opt_apply", [...sel]);
          if (!r.ok) { App.toast(r.err, "error"); return; }
          refs2.prog.style.display = "flex";
          refs2.progText.textContent = "应用中…";
        } finally {
          this._busy = false; this.disabled = false; this.textContent = old;
        }
      },
    }, "应用所选");

    const revertBtn = App.h("button", {
      class: "btn", onclick: async function () {
        if (this._busy) return;
        if (!(await App.confirm("还原全部",
          "将按快照还原本工具执行过的全部修改，确定继续？"))) return;
        this._busy = true; this.disabled = true;
        try { await App.tryCall("opt_revert"); }
        finally { this._busy = false; this.disabled = false; }
      },
    }, "还原全部");

    const presetSel = App.h("select", { class: "input", style: { width: "auto", flex: "none" } },
      App.h("option", { value: "" }, "预设…"),
      App.h("option", { value: "lite" }, "轻量（仅低风险）"),
      App.h("option", { value: "recommended" }, "推荐（低+中风险）"),
      App.h("option", { value: "deep" }, "深度（含谨慎项）"));
    presetSel.addEventListener("change", async () => {
      const k = presetSel.value;
      if (!k) return;
      const r = await App.tryCall("opt_preset", k);
      if (!r.ok) { App.toast(r.err, "error"); return; }
      sel.clear();
      r.data.ids.forEach((id) => sel.add(id));
      refresh();
      App.toast(`已按预设勾选 ${r.data.ids.length} 项，请检查后点「应用所选」`, "ok", 5000);
      presetSel.value = "";
    });

    const progBar = App.h("div", {
      style: {
        flex: "1", height: "6px", borderRadius: "3px", overflow: "hidden",
        background: "var(--card2)", display: "none",
      },
    }, App.h("div", {
      style: { height: "100%", width: "0%", background: "var(--accent)", transition: "width .2s" },
    }));
    const progText = App.h("span", { class: "hint", style: { maxWidth: "200px" } });

    async function refresh() {
      refs2.list.innerHTML = "";
      refs2.list.appendChild(App.h("div", { class: "empty" }, "正在读取优化项状态…"));
      const r = await App.tryCall("opt_tweaks");
      refs2.list.innerHTML = "";
      if (!r.ok) {
        refs2.list.appendChild(App.h("div", { class: "empty" }, r.err));
        return;
      }
      items = r.data.items;
      refs2.adminTip.style.display = r.data.admin ? "none" : "";
      const byGroup = {};
      items.forEach((it) => {
        (byGroup[it.group] = byGroup[it.group] || []).push(it);
      });
      for (const [g, name] of Object.entries(r.data.groups)) {
        const list = byGroup[g] || [];
        if (!list.length) continue;
        const body = App.h("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } });
        for (const it of list) {
          const chk = App.h("input", {
            type: "checkbox",
            onchange: (e) => {
              if (e.target.checked) sel.add(it.id); else sel.delete(it.id);
              applyBtn.textContent = `应用所选（${sel.size}）`;
            },
          });
          if (sel.has(it.id)) chk.checked = true;
          body.appendChild(App.h("div", { class: "prop-row" },
            chk,
            App.h("span", { class: "prop-val" },
              App.h("span", { style: { fontWeight: "500" } }, it.name),
              it.risk !== "low" ? App.h("span", {
                class: "tag " + (it.risk === "high" ? "danger" : "warn"),
                style: { marginLeft: "6px" },
                title: "风险等级",
              }, it.risk === "high" ? "高" : "中") : null,
              App.h("div", { class: "hint", style: { fontSize: "11.5px", marginTop: "2px" } }, it.desc),
            ),
            it.on === true ? App.statusTag("已生效", "ok", "check")
              : it.admin && !r.data.admin ? App.statusTag("需管理员", "warn")
              : App.statusTag("未优化"),
            App.h("button", {
              class: "btn xs", style: { flex: "none" }, title: "还原此项",
              disabled: it.on !== true,
              onclick: async function () {
                if (this._busy) return;
                this._busy = true; this.disabled = true;
                try {
                  const rr = await App.tryCall("opt_revert_one", it.id);
                  if (!rr.ok) { App.toast(rr.err, "error"); return; }
                  App.toast("已还原此项", "ok");
                  refresh();
                } finally { this._busy = false; }
              },
            }, "还原"),
          ));
        }
        refs2.list.appendChild(App.sec(`${name}（${list.length}）`, [body], { open: g === "privacy" }));
      }
    }

    function onOptProgress(d) {
      if (d.stage !== "run") return;
      refs2.prog.style.display = "flex";
      progBar.style.width = d.total ? Math.round(d.done * 100 / d.total) + "%" : "5%";
      progText.textContent = `${d.done}/${d.total} · ${d.name || ""}`;
    }
    function onOptDone(d) {
      if (d.tag) return;   // 全量已装应用等带 tag 的事件由各自处理器消化，防串扰
      progBar.style.width = "100%";
      setTimeout(() => { refs2.prog.style.display = "none"; progBar.style.width = "0%"; }, 1200);
      if (!d.ok) { App.toast(d.err || "操作失败", "error", 6000); }
      else {
        const fails = (d.results || []).filter((x) => !x.ok);
        if (fails.length) {
          App.toast(`完成，${fails.length} 项失败：` +
            fails.map((x) => x.err).join("；"), "warn", 8000);
        } else {
          App.toast("操作完成", "ok");
        }
      }
      sel.clear();
      applyBtn.textContent = "应用所选";
      refresh();
    }

    const p1 = App.h("div", null,
      App.h("div", { class: "card", style: { padding: "10px 14px" } },
        App.h("div", { class: "row", style: { flexWrap: "wrap" } },
          presetSel, applyBtn, revertBtn,
          App.h("button", { class: "btn sm", onclick: async function () {
            const r = await App.tryCall("opt_export_cfg", [...sel]);
            if (!r.ok) { App.toast(r.err, "error"); return; }
            App.toast(`配置已导出（${r.data.count} 项）：\n${r.data.path}`, "ok", 8000);
          } }, "导出配置"),
          App.h("button", { class: "btn sm", onclick: async function () {
            const r = await App.tryCall("opt_import_pick");
            if (!r.ok) { if (r.err !== "未选择文件。") App.toast(r.err, "error"); return; }
            sel.clear();
            r.data.ids.forEach((id) => sel.add(id));
            refresh();
            App.toast(`已导入 ${r.data.ids.length} 项勾选，请检查后应用`, "ok", 5000);
          } }, "导入配置"),
          refs2.adminTip = App.h("span", {
            class: "hint", style: { color: "var(--warn)", display: "none" },
          }, "⚠ 未以管理员运行：管理员项将不可应用（可点右下角「提权重启」）"),
        ),
        App.h("div", { class: "row", style: { margin: "8px 0 0" } },
          refs2.prog = App.h("div", {
            style: { display: "none", alignItems: "center", gap: "8px", flex: "1" },
          }, progBar, progText),
        ),
      ),
      refs2.list = App.h("div", { style: { marginTop: "10px" } }),
    );

    /* --- 页签 2：更新策略 --- */
    const updBox = App.h("div", { class: "list" });
    let updMode = null;
    async function refreshUpdate() {
      updBox.innerHTML = "";
      updBox.appendChild(App.h("div", { class: "empty" }, "正在读取…"));
      const r = await App.tryCall("opt_update_get");
      updBox.innerHTML = "";
      if (!r.ok) { updBox.appendChild(App.h("div", { class: "empty" }, r.err)); return; }
      updMode = r.data.mode;
      const modes = [
        ["default", "默认（全量更新）", "正常接收全部 Windows 更新"],
        ["security", "仅安全更新", "推迟功能更新 365 天，只收安全补丁"],
        ["paused", "暂停更新", "完全停止自动更新（有安全风险，自行斟酌）"],
      ];
      for (const [m, title, desc] of modes) {
        const active = updMode === m;
        updBox.appendChild(App.h("div", {
          class: "list-item" + (active ? " selected" : ""), style: { cursor: "pointer" },
          onclick: async function () {
            if (!(await App.confirm("切换更新策略",
              `将切换为「${title}」，需要管理员权限（UAC）。继续？`))) return;
            const rr = await App.tryCall("opt_update_set", m);
            if (!rr.ok) { App.toast(rr.err, "error", 6000); return; }
            App.toast("更新策略已切换", "ok");
            refreshUpdate();
          },
        },
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, title,
              active ? App.h("span", { class: "tag accent", style: { marginLeft: "8px" } }, "当前") : null),
            App.h("div", { class: "li-sub" }, desc)),
        ));
      }
    }
    const p2 = App.h("div", null,
      App.h("div", { class: "card", style: { padding: "10px 14px" } },
        App.h("div", { class: "row" },
          App.h("button", { class: "btn sm", onclick: refreshUpdate }, "刷新"),
          App.h("span", { class: "hint" }, "策略对齐 WinUtil：功能更新可推迟，安全补丁持续接收")),
        App.h("div", { style: { marginTop: "8px" } }, updBox)));

    /* --- 页签 3：垃圾清理 --- */
    const cleanBox = App.h("div", { class: "list" });
    const cleanSel = new Set();
    async function cleanScan() {
      cleanBox.innerHTML = "";
      cleanBox.appendChild(App.h("div", { class: "empty" }, "正在扫描…"));
      const r = await App.tryCall("opt_clean_scan");
      cleanBox.innerHTML = "";
      if (!r.ok) { cleanBox.appendChild(App.h("div", { class: "empty" }, r.err)); return; }
      for (const it of r.data.items) {
        const chk = App.h("input", {
          type: "checkbox", checked: cleanSel.has(it.id),
          onchange: (e) => {
            if (e.target.checked) cleanSel.add(it.id); else cleanSel.delete(it.id);
          },
        });
        cleanBox.appendChild(App.h("div", { class: "list-item" },
          chk,
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, it.name,
              it.admin ? App.h("span", { class: "hint", style: { marginLeft: "6px" } }, "（需管理员）") : null)),
          App.h("span", { class: "hint mono" },
            it.size != null ? App.fmtBytes(it.size) : "—"),
        ));
      }
    }
    const p3 = App.h("div", { class: "card", style: { padding: "10px 14px" } },
      App.h("div", { class: "row" },
        App.h("button", { class: "btn sm", onclick: cleanScan }, "扫描大小"),
        App.h("button", {
          class: "btn primary", onclick: async function () {
            if (this._busy) return;
            if (!cleanSel.size) { App.toast("请先勾选清理项（先扫描）", "warn"); return; }
            if (cleanSel.has("recycle") && !(await App.confirm(
              "包含回收站清理", "回收站可能包含你还需要的数据，确定一并清空？"))) {
              cleanSel.delete("recycle");
            }
            if (!(await App.confirm("开始清理", `将清理 ${cleanSel.size} 项，无法撤销，继续？`))) return;
            this._busy = true; this.disabled = true;
            try { await App.tryCall("opt_clean_run", [...cleanSel]); }
            finally { this._busy = false; this.disabled = false; }
          },
        }, "开始清理"),
        App.h("span", { class: "hint" }, "均为可再生缓存；回收站请先确认")),
      App.h("div", { style: { marginTop: "8px" } }, cleanBox));

    /* --- 页签 4：系统修复 --- */
    const fixes = [
      ["sfc", "系统文件检查（SFC）", "扫描并修复受保护的系统文件。耗时 5-15 分钟。"],
      ["dism", "组件存储修复（DISM）", "修复组件存储，SFC 无法修复时先跑这个。耗时 10-20 分钟。"],
      ["netstack", "网络栈重置", "重置 Winsock 与 TCP/IP，完成后需重启电脑。"],
      ["wu-reset", "Windows 更新组件重置", "停止更新服务并清空更新缓存后重启服务。"],
      ["winget", "WinGet 源修复", "重置并更新 WinGet 包源。"],
    ];
    const fixLog = App.h("div", { class: "log-box", style: { maxHeight: "160px", marginTop: "8px" } }, "修复输出…");
    const logFix = App.makeLog(fixLog);
    const p4 = App.h("div", null,
      ...fixes.map(([id, name, desc]) => App.h("div", { class: "card", style: { padding: "10px 14px", marginBottom: "10px" } },
        App.h("div", { class: "card-title" }, name),
        App.h("div", { class: "hint" }, desc),
        App.h("div", { class: "row", style: { marginTop: "6px" } },
          App.h("button", {
            class: "btn", onclick: async function () {
              if (this._busy) return;
              if (!(await App.confirm("运行修复",
                `「${name}」需要管理员权限（UAC），耗时可能较长。开始？`))) return;
              this._busy = true; this.disabled = true;
              const old = this.textContent; this.textContent = "运行中…";
              try {
                const r = await App.tryCall("opt_fix_run", id);
                if (!r.ok) { App.toast(r.err, "error", 8000); return; }
                logFix(r.data.output || "（无输出）");
                App.toast(`${name} 已完成`, "ok");
              } finally {
                this._busy = false; this.disabled = false; this.textContent = old;
              }
            },
          }, "运行")))),
      fixLog);

    /* opt_progress/opt_done 在 paneWinget 之后统一注册（此处勿重复订阅，
       否则同一事件会触发两次处理器——历史遗留双份 toast 已修） */

    /* --- 页签：应用管理（UWP + 可选功能 + 旧版能力，Winhance 三合一） --- */
    const uwpBox = App.h("div", { class: "list", style: { maxHeight: "300px", overflowY: "auto" } });
    const uwpRemovedBox = App.h("div", { class: "list", style: { marginTop: "6px" } });
    const uwpSearch = App.h("input", {
      class: "input", placeholder: "搜索应用…", style: { width: "180px", flex: "none" },
      oninput: () => renderUwp(),
    });
    const uwpSel = new Set();
    let uwpAdmin = false;
    function renderUwp() {
      uwpBox.innerHTML = "";
      const kw = (uwpSearch.value || "").trim().toLowerCase();
      const list = (buildOptimize._uwpInstalled || []).filter((x) =>
        !kw || x.name.toLowerCase().includes(kw));
      if (!list.length) {
        uwpBox.appendChild(App.h("div", { class: "empty" },
          kw ? "无匹配应用" : "未读取到 UWP 应用"));
        return;
      }
      for (const it of list) {
        const chk = App.h("input", {
          type: "checkbox", disabled: !uwpAdmin,
          onchange: (e) => {
            if (e.target.checked) uwpSel.add(it.name); else uwpSel.delete(it.name);
            uwpRemoveBtn.textContent = `卸载所选（${uwpSel.size}）`;
          },
        });
        if (uwpSel.has(it.name)) chk.checked = true;
        uwpBox.appendChild(App.h("div", { class: "list-item" },
          chk,
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, it.name),
            App.h("div", { class: "li-sub mono", style: { fontSize: "10.5px" } }, it.full || "")),
        ));
      }
    }
    const uwpRemoveBtn = App.h("button", {
      class: "btn sm danger", onclick: async function () {
        if (this._busy) return;
        if (!uwpAdmin) { App.toast("卸载 UWP 应用需要以管理员身份运行", "warn"); return; }
        if (!uwpSel.size) { App.toast("请先勾选要卸载的应用", "warn"); return; }
        if (!(await App.confirm("卸载 UWP 应用",
          `将卸载所选 ${uwpSel.size} 个应用（已记录位置，可恢复）。确定继续？`))) return;
        this._busy = true; this.disabled = true;
        try {
          const r = await App.tryCall("opt_uwp_remove", [...uwpSel]);
          if (!r.ok) { App.toast(r.err, "error"); return; }
          refs2.prog.style.display = "flex";
        } finally { this._busy = false; this.disabled = false; }
      },
    }, "卸载所选");
    async function uwpRefresh() {
      uwpBox.innerHTML = "";
      uwpBox.appendChild(App.h("div", { class: "empty" }, "正在读取 UWP 应用…"));
      uwpRemovedBox.innerHTML = "";
      const r = await App.tryCall("opt_uwp_list");
      if (!r.ok) { uwpBox.innerHTML = ""; uwpBox.appendChild(App.h("div", { class: "empty" }, r.err)); return; }
      buildOptimize._uwpInstalled = r.data.installed;
      uwpAdmin = r.data.admin;
      uwpSel.clear();
      uwpRemoveBtn.textContent = "卸载所选";
      paintUwpAdmin();
      renderUwp();
      uwpRemovedBox.innerHTML = "";
      const removed = r.data.removed || [];
      if (!removed.length) {
        uwpRemovedBox.appendChild(App.h("div", { class: "hint" }, "本工具卸载过的应用会出现在这里，可一键恢复。"));
        return;
      }
      for (const it of removed) {
        uwpRemovedBox.appendChild(App.h("div", { class: "prop-row" },
          App.h("span", { class: "prop-val" }, it.name),
          App.h("button", { class: "btn xs", onclick: async function () {
            if (this._busy) return;
            this._busy = true; this.disabled = true;
            try {
              const rr = await App.tryCall("opt_uwp_restore", it.name);
              if (!rr.ok) { App.toast(rr.err, "error", 8000); return; }
              App.toast("已恢复，刷新列表可见", "ok");
              uwpRefresh();
            } finally { this._busy = false; }
          } }, "恢复")));
      }
    }
    /* 可选功能 / 旧版能力 */
    const featBox = App.h("div", { class: "list", style: { maxHeight: "300px", overflowY: "auto" } });
    async function featRefresh() {
      featBox.innerHTML = "";
      featBox.appendChild(App.h("div", { class: "empty" }, "正在读取可选功能…（需管理员）"));
      const r = await App.tryCall("opt_features_list");
      featBox.innerHTML = "";
      if (!r.ok) { featBox.appendChild(App.h("div", { class: "empty" }, r.err)); return; }
      for (const f of r.data.features) {
        const enabled = f.state === "Enabled";
        featBox.appendChild(App.h("div", { class: "list-item" },
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title mono", style: { fontSize: "12px" } }, f.name)),
          enabled ? App.statusTag("已启用", "ok") : App.statusTag("已禁用"),
          App.h("button", {
            class: "btn xs", style: { flex: "none" },
            onclick: async function () {
              if (this._busy) return;
              if (!(await App.confirm("切换可选功能",
                `${enabled ? "禁用" : "启用"}「${f.name}」？（UAC，部分功能需重启）`))) return;
              this._busy = true; this.disabled = true;
              try {
                const rr = await App.tryCall("opt_feature_set", f.name, !enabled);
                if (!rr.ok) { App.toast(rr.err, "error", 8000); return; }
                App.toast("已切换（如需重启会另行提示）", "ok");
                featRefresh();
              } finally { this._busy = false; }
            },
          }, enabled ? "禁用" : "启用")));
      }
    }
    const capBox = App.h("div", { class: "list", style: { maxHeight: "300px", overflowY: "auto" } });
    async function capRefresh() {
      capBox.innerHTML = "";
      capBox.appendChild(App.h("div", { class: "empty" }, "正在读取旧版能力…（需管理员）"));
      const r = await App.tryCall("opt_caps_list");
      capBox.innerHTML = "";
      if (!r.ok) { capBox.appendChild(App.h("div", { class: "empty" }, r.err)); return; }
      for (const c of r.data.caps) {
        const installed = c.state === "Installed";
        capBox.appendChild(App.h("div", { class: "list-item" },
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title mono", style: { fontSize: "12px" } }, c.name)),
          installed ? App.statusTag("已安装", "ok") : App.statusTag("未安装"),
          App.h("button", {
            class: "btn xs", style: { flex: "none" },
            onclick: async function () {
              if (this._busy) return;
              if (!(await App.confirm("切换旧版能力",
                `${installed ? "移除" : "添加"}「${c.name}」？（UAC，可能联网下载）`))) return;
              this._busy = true; this.disabled = true;
              try {
                const rr = await App.tryCall("opt_cap_set", c.name, !installed);
                if (!rr.ok) { App.toast(rr.err, "error", 8000); return; }
                App.toast("已切换", "ok");
                capRefresh();
              } finally { this._busy = false; }
            },
          }, installed ? "移除" : "添加")));
      }
    }
    /* 三区块纵向布局（UWP / 可选功能 / 旧版能力） */
    /* v5.4：提权重启按钮统一收在右下角状态栏，这里仅保留权限状态提示 */
    const uwpAdminRow = App.h("div", { class: "row", style: { marginBottom: "10px" } });
    function paintUwpAdmin() {
      uwpAdminRow.innerHTML = "";
      if (uwpAdmin) {
        uwpAdminRow.appendChild(App.h("span", { class: "tag ok" }, "已以管理员身份运行"));
        return;
      }
      uwpAdminRow.appendChild(App.h("span", { class: "hint", style: { color: "var(--warn)" } },
        "⚠ 未以管理员运行：卸载 UWP / 切换可选功能与旧版能力需要管理员权限（点右下角「提权重启」一次性提权）"));
    }
    /* 打开页签即显示权限状态（轻量 app_info，无需先点「刷新」；uwpRefresh 后以实际值覆盖） */
    App.tryCall("app_info").then((r) => {
      if (r.ok) { uwpAdmin = !!(r.data && r.data.admin); paintUwpAdmin(); }
    });

    /* --- 全量已装应用（v5.4 架构级复刻一期：注册表 + UWP 双源检测工厂） --- */
    const appsSel = new Set();          // 勾选的 listkey（跨页保持）
    let appsPage = 1, appsTotal = 0, appsKwTimer = null;
    const appsBox = App.h("div", { class: "list", style: { maxHeight: "320px", overflowY: "auto" } },
      App.h("div", { class: "empty" }, "尚未扫描\n点击「扫描本机应用」读取注册表与 UWP 已装应用"));
    const appsSearch = App.h("input", {
      class: "input grow-in", placeholder: "搜索名称 / 发布者…",
      oninput: () => {
        clearTimeout(appsKwTimer);
        appsKwTimer = setTimeout(() => loadAppsPage(1, false), 300);
      },
    });
    const appsSrcSel = App.h("select", {
      class: "input", style: { width: "auto", flex: "none" },
      onchange: () => loadAppsPage(1, false),
    },
      App.h("option", { value: "" }, "全部来源"),
      App.h("option", { value: "registry" }, "注册表程序"),
      App.h("option", { value: "uwp" }, "UWP 应用"));
    const appsUninstallBtn = App.h("button", {
      class: "btn sm danger", onclick: async function () {
        if (this._busy) return;
        if (!appsSel.size) { App.toast("请先勾选要卸载的应用", "warn"); return; }
        const names = [...appsSel].map((k) => appsNameById.get(k)).filter(Boolean);
        const show = names.slice(0, 3).join("、") + (names.length > 3 ? " 等" : "");
        if (!(await App.confirm("批量静默卸载",
          `将静默卸载所选 ${appsSel.size} 个应用：${show}。\n` +
          "仅支持静默卸载的应用可执行；操作会弹出一次 UAC 授权。确定继续？"))) return;
        this._busy = true; this.disabled = true;
        const old = this.textContent; this.textContent = "卸载中…";
        try {
          const r = await App.tryCall("opt_apps_uninstall", [...appsSel]);
          if (!r.ok) { App.toast(r.err, "error", 6000); return; }
          refs2.prog.style.display = "flex";
        } finally { this._busy = false; this.disabled = false; this.textContent = old; }
      },
    }, "卸载所选");
    async function loadAppsPage(page, append) {
      if (!append) { appsPage = 1; }
      else { appsPage = page; }
      if (!append) {
        appsBox.innerHTML = "";
        appsBox.appendChild(App.h("div", { class: "empty" }, "正在读取…"));
      } else {
        const more = appsBox.querySelector(".apps-more");
        if (more) more.remove();
      }
      const r = await App.tryCall("opt_apps_list", appsSrcSel.value,
        appsSearch.value.trim(), appsPage, 100);
      if (!append) appsBox.innerHTML = "";
      if (!r.ok) {
        appsBox.innerHTML = "";
        appsBox.appendChild(App.h("div", { class: "empty" }, r.err));
        return;
      }
      appsTotal = r.data.total;
      for (const it of r.data.items) {
        appsNameById.set(it.listkey, it.name);
        appsBox.appendChild(appsRow(it));
      }
      const shown = (appsPage - 1) * (r.data.page_size || 100) + r.data.items.length;
      if (!r.data.items.length && !append) {
        appsBox.appendChild(App.h("div", { class: "empty" },
          appsSearch.value.trim() ? "无匹配应用" : "未读取到已装应用"));
      }
      if (shown < appsTotal) {
        appsBox.appendChild(App.h("div", { class: "apps-more", style: { padding: "8px", textAlign: "center" } },
          App.h("button", {
            class: "btn sm", onclick: () => loadAppsPage(appsPage + 1, true),
          }, `加载更多（已显示 ${shown} / ${appsTotal}）`)));
      }
      appsUninstallBtn.textContent =
        appsSel.size ? `卸载所选（${appsSel.size}）` : "卸载所选";
    }
    function appsRow(it) {
      const checked = appsSel.has(it.listkey);
      const sizeTxt = it.size_kb > 0 ? App.fmtBytes(it.size_kb * 1024) : "—";
      const srcTag = it.source === "uwp" ? "UWP"
        : (it.scope === "user" ? "用户级" : "系统级");
      return App.h("div", { class: "list-item" },
        App.h("input", {
          type: "checkbox",
          disabled: !it.quiet_possible,
          checked,
          onchange: (e) => {
            if (e.target.checked) appsSel.add(it.listkey);
            else appsSel.delete(it.listkey);
            appsUninstallBtn.textContent =
              appsSel.size ? `卸载所选（${appsSel.size}）` : "卸载所选";
          },
        }),
        App.h("span", { class: "li-main" },
          App.h("div", { class: "li-title" }, it.name),
          App.h("div", { class: "li-sub" },
            [it.version, it.publisher].filter(Boolean).join(" · ") || "—")),
        it.source === "uwp" ? App.h("span", { class: "tag accent" }, "UWP")
          : App.h("span", { class: "tag" }, srcTag),
        it.quiet_possible ? null
          : App.h("span", { class: "tag warn", title: "无静默卸载串，需手动卸载" }, "无静默"),
        App.h("span", { class: "hint mono", style: { width: "72px", textAlign: "right", flex: "none" } }, sizeTxt),
      );
    }
    const appsNameById = new Map();    // listkey → name（confirm 文案用）
    function onAppsDone(d) {
      if (d.tag !== "apps") return;    // 只消化全量已装应用事件（更新事件见 onAppsUpdDone）
      refs2.prog.style.display = "none";
      if (!d.ok) { App.toast(d.err || "操作失败", "error", 6000); return; }
      if (typeof d.count === "number") {         // 扫描完成
        App.toast(`扫描完成：共 ${d.count} 个已装应用`, "ok");
        loadAppsPage(1, false);
        loadAppsRemoved();
        return;
      }
      if (Array.isArray(d.results)) {            // 卸载完成
        const fails = d.results.filter((x) => !x.ok);
        appsSel.clear();
        appsUninstallBtn.textContent = "卸载所选";
        if (fails.length) {
          App.toast(`卸载完成，${fails.length} 项失败：` +
            fails.map((x) => `${x.name}（${x.err}）`).join("；"), "warn", 8000);
        } else {
          App.toast(`已卸载 ${d.results.length} 个应用`, "ok");
        }
        loadAppsPage(1, false);
        loadAppsRemoved();
      }
    }
    /* 可升级应用（winget，v5.4 二期：UniGetUI 更新检测思想的最小子集） */
    const upSel = new Set();
    const upBox = App.h("div", { class: "list", style: { maxHeight: "260px", overflowY: "auto" } },
      App.h("div", { class: "empty" }, "尚未检查\n点击「检查升级」读取 winget 可升级列表"));
    const upUpgradeBtn = App.h("button", {
      class: "btn sm primary", onclick: async function () {
        if (this._busy) return;
        if (!upSel.size) { App.toast("请先勾选要升级的应用", "warn"); return; }
        if (!(await App.confirm("批量静默升级",
          `将通过 winget 静默升级所选 ${upSel.size} 个应用（联网下载，耗时视网速），继续？`))) return;
        this._busy = true; this.disabled = true;
        const old = this.textContent; this.textContent = "升级中…";
        refs2.prog.style.display = "flex";
        try {
          const r = await App.tryCall("opt_apps_upgrade", [...upSel]);
          if (!r.ok) { App.toast(r.err, "error", 6000); return; }
        } finally { this._busy = false; this.disabled = false; this.textContent = old; }
      },
    }, "升级所选");
    function renderUpgrades(rows) {
      upBox.innerHTML = "";
      if (!rows.length) {
        upBox.appendChild(App.h("div", { class: "empty" }, "全部应用已是最新"));
        upUpgradeBtn.disabled = true;
        return;
      }
      upUpgradeBtn.disabled = false;
      for (const it of rows) {
        upBox.appendChild(App.h("div", { class: "list-item" },
          App.h("input", {
            type: "checkbox",
            checked: upSel.has(it.id),
            onchange: (e) => {
              if (e.target.checked) upSel.add(it.id); else upSel.delete(it.id);
              upUpgradeBtn.textContent =
                upSel.size ? `升级所选（${upSel.size}）` : "升级所选";
            },
          }),
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, it.name),
            App.h("div", { class: "li-sub mono" }, it.id)),
          App.h("span", { class: "hint mono" },
            `${it.version} → ${it.available}`),
        ));
      }
      upUpgradeBtn.textContent =
        upSel.size ? `升级所选（${upSel.size}）` : "升级所选";
    }
    function onAppsUpdDone(d) {
      if (d.tag !== "apps_updates") return;
      refs2.prog.style.display = "none";
      if (!d.ok) { App.toast(d.err || "操作失败", "error", 6000); return; }
      if (Array.isArray(d.rows)) {               // 升级检查完成
        upSel.clear();
        renderUpgrades(d.rows);
        App.toast(d.rows.length ? `发现 ${d.rows.length} 个可升级应用` : "全部应用已是最新", "ok");
        return;
      }
      if (Array.isArray(d.results)) {            // 批量升级完成
        const fails = d.results.filter((x) => !x.ok);
        upSel.clear();
        if (fails.length) {
          App.toast(`升级完成，${fails.length} 项失败：` +
            fails.map((x) => `${x.id}（${x.err}）`).join("；"), "warn", 8000);
        } else {
          App.toast(`已升级 ${d.results.length} 个应用`, "ok");
        }
        upCheckBtn._busy = false;   // 允许再次检查
      }
    }
    const upCheckBtn = App.h("button", {
      class: "btn primary", onclick: async function () {
        if (this._busy) return;
        this._busy = true; this.disabled = true;
        const old = this.textContent; this.textContent = "检查中…";
        refs2.prog.style.display = "flex";
        try {
          const r = await App.tryCall("opt_apps_updates");
          if (!r.ok) { App.toast(r.err, "error"); return; }
        } finally {
          this._busy = false; this.disabled = false; this.textContent = old;
        }
      },
    }, "检查升级");
    /* 已卸载记录（快照留痕，一期仅展示） */
    const appsRemovedBox = App.h("div", { class: "list", style: { maxHeight: "160px", overflowY: "auto" } });
    async function loadAppsRemoved() {
      appsRemovedBox.innerHTML = "";
      const r = await App.tryCall("opt_apps_removed_list");
      if (!r.ok) return;
      const removed = r.data || [];
      if (!removed.length) {
        appsRemovedBox.appendChild(App.h("div", { class: "hint" },
          "本工具静默卸载过的应用会记录在这里。"));
        return;
      }
      for (const it of removed) {
        appsRemovedBox.appendChild(App.h("div", { class: "list-item" },
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, it.name || it.listkey),
            App.h("div", { class: "li-sub" },
              it.ts ? new Date(it.ts * 1000).toLocaleString() : "")),
        ));
      }
    }
    const appsScanBtn = App.h("button", {
      class: "btn primary", onclick: async function () {
        if (this._busy) return;
        this._busy = true; this.disabled = true;
        const old = this.textContent; this.textContent = "扫描中…";
        refs2.prog.style.display = "flex";
        try {
          const r = await App.tryCall("opt_apps_scan");
          if (!r.ok) { App.toast(r.err, "error"); return; }
        } finally {
          this._busy = false; this.disabled = false; this.textContent = old;
        }
      },
    }, "扫描本机应用");
    loadAppsRemoved();   // 挂载即加载已卸载记录（折叠区内）
    const paneAppsMgr = App.h("div", null,
      uwpAdminRow,
      /* 全量已装应用（注册表 + UWP 双源检测工厂） */
      App.h("div", { class: "card", style: { padding: "10px 14px" } },
        App.h("div", { class: "card-title" }, "全量已装应用"),
        App.h("div", { class: "row", style: { flexWrap: "wrap" } },
          appsScanBtn,
          App.h("span", { class: "hint" },
            "注册表 + UWP + Scoop/Chocolatey 多源检测（检测到对应 CLI 时自动启用）；" +
            "勾选后可批量静默卸载")),
        App.h("div", { class: "row", style: { marginTop: "8px" } },
          appsSearch, appsSrcSel, appsUninstallBtn),
        App.h("div", { style: { marginTop: "8px" } }, appsBox)),
      /* v5.4 二期：可升级（winget）+ 已卸载记录（默认折叠） */
      App.sec("可升级应用（winget，检查需联网）",
        [App.h("div", { class: "row", style: { marginBottom: "6px" } },
          upCheckBtn, upUpgradeBtn,
          App.h("span", { class: "hint" }, "静默升级，可多选批量执行")),
         upBox], { open: false }),
      App.sec("已卸载记录", [appsRemovedBox], { open: false }),
      App.h("div", { class: "card", style: { padding: "10px 14px" } },
        App.h("div", { class: "card-title" }, "UWP 应用"),
        App.h("div", { class: "row" }, uwpSearch, uwpRemoveBtn,
          App.h("button", { class: "btn sm", onclick: uwpRefresh }, "刷新")),
        App.h("div", { style: { marginTop: "8px" } }, uwpBox),
        App.h("div", { style: { marginTop: "6px" } }, uwpRemovedBox)),
      App.h("div", { class: "card", style: { padding: "10px 14px", marginTop: "10px" } },
        App.h("div", { class: "row" },
          App.h("div", { class: "card-title", style: { margin: "0" } }, "Windows 可选功能"),
          App.h("span", { class: "grow" }),
          App.h("button", { class: "btn sm", onclick: featRefresh }, "刷新")),
        App.h("div", { style: { marginTop: "8px" } }, featBox)),
      App.h("div", { class: "card", style: { padding: "10px 14px", marginTop: "10px" } },
        App.h("div", { class: "row" },
          App.h("div", { class: "card-title", style: { margin: "0" } }, "旧版能力（按需组件）"),
          App.h("span", { class: "grow" }),
          App.h("button", { class: "btn sm", onclick: capRefresh }, "刷新")),
        App.h("div", { style: { marginTop: "8px" } }, capBox)));

    /* --- 页签：软件安装（winget） --- */
    const wingetBox = App.h("div");
    const wingetSel = new Set();
    async function wingetRefresh() {
      wingetBox.innerHTML = "";
      const r = await App.tryCall("opt_winget_catalog");
      if (!r.ok) { wingetBox.appendChild(App.h("div", { class: "empty" }, r.err)); return; }
      if (!r.data.available) {
        wingetBox.appendChild(App.h("div", { class: "empty" },
          "未检测到 winget\n请先安装微软「应用安装程序」（Microsoft Store）"));
        return;
      }
      for (const [cat, apps] of Object.entries(r.data.catalog)) {
        const row = App.h("div", { style: { display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "6px" } });
        for (const app of apps) {
          row.appendChild(App.h("label", { class: "chk" },
            App.h("input", {
              type: "checkbox",
              onchange: (e) => {
                if (e.target.checked) wingetSel.add(app.id); else wingetSel.delete(app.id);
                wingetInstallBtn.textContent = `安装所选（${wingetSel.size}）`;
              },
            }), " " + app.name));
        }
        wingetBox.appendChild(App.h("div", { class: "card-title", style: { fontSize: "13px", margin: "10px 0 0" } }, cat));
        wingetBox.appendChild(row);
      }
    }
    const wingetInstallBtn = App.h("button", {
      class: "btn primary", onclick: async function () {
        if (this._busy) return;
        if (!wingetSel.size) { App.toast("请先勾选要安装的软件", "warn"); return; }
        if (!(await App.confirm("安装软件",
          `将通过 winget 静默安装 ${wingetSel.size} 个软件（联网下载，耗时视网速），继续？`))) return;
        this._busy = true; this.disabled = true;
        try { await App.tryCall("opt_winget_install", [...wingetSel]); }
        finally { this._busy = false; this.disabled = false; }
      },
    }, "安装所选");
    const paneWinget = App.h("div", { class: "card", style: { padding: "10px 14px" } },
      App.h("div", { class: "row" }, wingetInstallBtn,
        App.h("button", { class: "btn sm", onclick: wingetRefresh }, "刷新列表"),
        App.h("span", { class: "hint" }, "通过 winget 官方源静默安装，无捆绑")),
      App.h("div", { style: { marginTop: "6px" } }, wingetBox));

    App.on("opt_progress", onOptProgress);
    App.on("opt_done", onOptDone);
    App.on("opt_done", onAppsDone);       // 全量已装应用：按 tag==="apps" 消化
    App.on("opt_done", onAppsUpdDone);    // 可升级应用：按 tag==="apps_updates" 消化

    const nav = App.subnav([
      { label: "系统优化", el: p1 },
      { label: "应用管理", el: paneAppsMgr },
      { label: "软件安装", el: paneWinget },
      { label: "更新策略", el: p2 },
      { label: "垃圾清理", el: p3 },
      { label: "系统修复", el: p4 },
    ]);
    refresh();
    /* App.subnav 只返回页签条，pane 必须经 nav.panes 手动挂载（此前遗漏导致
       「系统优化」页签下 6 个页签全部无内容） */
    const wrap = App.h("div", null,
      App.h("p", { class: "hint", style: { margin: "0 0 6px" } },
        "优化清单移植自 WinUtil（MIT）并经 Winhance 源码交叉验证；全部项可按快照还原。不做激活/Defender 禁用/强卸 Edge。"),
      nav);
    nav.panes.forEach((p) => wrap.appendChild(p));
    return wrap;
  }

  /* ================= 无人值守安装应答生成器（autounattend.xml） ================= */
  function buildUnattend() {
    /* 微软官方 unattend schema；优化项联动：勾选的 Tweaks 以 FirstLogonCommands
       reg add / sc config 方式写入（Winhance 思想：装完即已优化）。 */
    const f = {};
    f.lang = App.h("select", { class: "input", style: { flex: "1" } },
      ["zh-CN", "en-US", "ja-JP", "ko-KR", "de-DE", "fr-FR"].map((l) => App.h("option", { value: l }, l)));
    f.tz = App.h("select", { class: "input", style: { flex: "1" } },
      ["China Standard Time", "UTC", "W. Europe Standard Time", "Eastern Standard Time",
        "Pacific Standard Time", "Tokyo Standard Time", "Korea Standard Time"]
        .map((t) => App.h("option", { value: t }, t)));
    f.user = App.h("input", { class: "input", value: "user", style: { flex: "1" } });
    f.pwd = App.h("input", { class: "input", type: "password", style: { flex: "1" }, placeholder: "留空 = 无密码" });
    f.autologon = App.h("input", { type: "checkbox" });
    f.computer = App.h("input", { class: "input", placeholder: "留空随机", style: { flex: "1" } });
    f.key = App.h("input", { class: "input mono", placeholder: "产品密钥（留空 = 使用镜像内置）", style: { flex: "1" } });
    f.disk = App.h("input", { type: "checkbox" });
    f.skipprivacy = App.h("input", { type: "checkbox", checked: true });
    f.bypass = App.h("input", { type: "checkbox", checked: true });

    /* 优化项联动：勾选写入 FirstLogonCommands */
    const optBox = App.h("div", { style: { display: "flex", flexDirection: "column", gap: "4px", maxHeight: "260px", overflowY: "auto" } });
    const optSel = new Set();
    let optItems = [];
    async function loadOpts() {
      optBox.innerHTML = "";
      optBox.appendChild(App.h("div", { class: "empty" }, "正在读取优化清单…"));
      const r = await App.tryCall("opt_tweaks");
      optBox.innerHTML = "";
      if (!r.ok) { optBox.appendChild(App.h("div", { class: "empty" }, r.err)); return; }
      optItems = r.data.items.filter((x) => (x.registry || []).length || (x.services || []).length);
      const groups = {};
      optItems.forEach((x) => (groups[x.group_name] = groups[x.group_name] || []).push(x));
      for (const [g, list] of Object.entries(groups)) {
        optBox.appendChild(App.h("div", { class: "hint", style: { marginTop: "6px" } }, g));
        for (const it of list) {
          const chk = App.h("input", {
            type: "checkbox",
            onchange: (e) => {
              if (e.target.checked) optSel.add(it.id); else optSel.delete(it.id);
              optCount.textContent = `将写入 ${optSel.size} 项优化`;
            },
          });
          optBox.appendChild(App.h("label", { class: "chk" }, chk, " " + it.name));
        }
      }
      optCount.textContent = "将写入 0 项优化";
    }
    const optCount = App.h("span", { class: "hint", style: { marginLeft: "auto" } });

    const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

    function buildXml() {
      const lang = f.lang.value, tz = f.tz.value;
      const user = (f.user.value || "user").trim();
      const pwd = f.pwd.value;
      const cmds = [];
      /* 优化项 → FirstLogonCommands（registry 型 reg add；services 型 sc config） */
      for (const id of optSel) {
        const t = optItems.find((x) => x.id === id);
        if (!t) continue;
        for (const r of t.registry || []) {
          const hive = r.path.replace(/^HKLM:/i, "HKLM").replace(/^HKCU:/i, "HKCU").replace(/\\/g, "\\");
          const type = r.kind === "DWord" ? "REG_DWORD"
            : r.kind === "QWord" ? "REG_QWORD" : "REG_SZ";
          cmds.push({ cmd: `reg add "${hive}" /v "${r.name}" /t ${type} /d ${r.value} /f`,
                      desc: "优化: " + t.name });
        }
        for (const s of t.services || []) {
          const map = { Disabled: "disabled", Manual: "demand", Automatic: "auto" };
          cmds.push({ cmd: `sc config "${s.name}" start= ${map[s.startup] || "demand"}`,
                      desc: "服务优化: " + t.name });
        }
      }
      if (f.disk.checked) {
        cmds.push({ cmd: "powercfg -setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
                    desc: "高性能电源计划" });
      }
      let xml = '<?xml version="1.0" encoding="utf-8"?>\n'
        + '<unattend xmlns="urn:schemas-microsoft-com:unattend">\n';
      /* windowsPE：语言 + 分区 + EULA/密钥 */
      xml += '  <settings pass="windowsPE">\n'
        + '    <component name="Microsoft-Windows-International-Core-WinPE" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
        + '      <SetupUILanguage><UILanguage>' + esc(lang) + '</UILanguage></SetupUILanguage>\n'
        + '      <InputLocale>' + esc(lang) + '</InputLocale>\n'
        + '      <SystemLocale>' + esc(lang) + '</SystemLocale>\n'
        + '      <UILanguage>' + esc(lang) + '</UILanguage>\n'
        + '      <UserLocale>' + esc(lang) + '</UserLocale>\n'
        + '    </component>\n'
        + '    <component name="Microsoft-Windows-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
        + '      <UserData><AcceptEula>true</AcceptEula>'
        + (f.key.value.trim() ? '<ProductKey><Key>' + esc(f.key.value.trim()) + '</Key></ProductKey>' : '')
        + '</UserData>\n';
      if (f.disk.checked) {
        xml += '      <DiskConfiguration>\n'
          + '        <Disk wcm:action="add">\n'
          + '          <DiskID>0</DiskID><WillWipeDisk>true</WillWipeDisk>\n'
          + '          <CreatePartitions>\n'
          + '            <CreatePartition wcm:action="add"><Order>1</Order><Type>EFI</Type><Size>300</Size></CreatePartition>\n'
          + '            <CreatePartition wcm:action="add"><Order>2</Order><Type>MSR</Type><Size>16</Size></CreatePartition>\n'
          + '            <CreatePartition wcm:action="add"><Order>3</Order><Type>Primary</Type><Extend>true</Extend></CreatePartition>\n'
          + '          </CreatePartitions>\n'
          + '          <ModifyPartitions>\n'
          + '            <ModifyPartition wcm:action="add"><Order>1</Order><PartitionID>1</PartitionID><Format>FAT32</Format><Label>System</Label></ModifyPartition>\n'
          + '            <ModifyPartition wcm:action="add"><Order>2</Order><PartitionID>3</PartitionID><Format>NTFS</Format><Label>Windows</Label><Letter>C</Letter></ModifyPartition>\n'
          + '          </ModifyPartitions>\n'
          + '        </Disk>\n'
          + '      </DiskConfiguration>\n'
          + '      <ImageInstall><OSImage><InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo></OSImage></ImageInstall>\n';
      }
      xml += '    </component>\n  </settings>\n';
      /* specialize：计算机名 */
      xml += '  <settings pass="specialize">\n'
        + '    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
        + (f.computer.value.trim() ? '      <ComputerName>' + esc(f.computer.value.trim()) + '</ComputerName>\n' : '')
        + '      <TimeZone>' + esc(tz) + '</TimeZone>\n'
        + '    </component>\n  </settings>\n';
      /* oobeSystem：OOBE / 用户 / 自动登录 / FirstLogonCommands */
      xml += '  <settings pass="oobeSystem">\n'
        + '    <component name="Microsoft-Windows-International-Core" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
        + '      <InputLocale>' + esc(lang) + '</InputLocale>\n'
        + '      <SystemLocale>' + esc(lang) + '</SystemLocale>\n'
        + '      <UILanguage>' + esc(lang) + '</UILanguage>\n'
        + '      <UserLocale>' + esc(lang) + '</UserLocale>\n'
        + '    </component>\n'
        + '    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
        + '      <OOBE>\n'
        + '        <HideEULAPage>true</HideEULAPage>\n'
        + '        <HideOEMRegistrationScreen>true</HideOEMRegistrationScreen>\n'
        + '        <HideOnlineAccountScreens>' + (f.bypass.checked ? "true" : "false") + '</HideOnlineAccountScreens>\n'
        + '        <HideWirelessSetupInOOBE>false</HideWirelessSetupInOOBE>\n'
        + '        <ProtectYourPC>' + (f.skipprivacy.checked ? "3" : "1") + '</ProtectYourPC>\n'
        + '      </OOBE>\n'
        + '      <UserAccounts><LocalAccounts><LocalAccount wcm:action="add">\n'
        + '        <Name>' + esc(user) + '</Name>\n'
        + '        <Group>Administrators</Group>\n'
        + '        <Password><Value>' + esc(pwd) + '</Value><PlainText>true</PlainText></Password>\n'
        + '      </LocalAccount></LocalAccounts></UserAccounts>\n'
        + (f.autologon.checked
          ? '      <AutoLogon><Enabled>true</Enabled><LogonCount>1</LogonCount><Username>' + esc(user) + '</Username><Password><Value>' + esc(pwd) + '</Value><PlainText>true</PlainText></Password></AutoLogon>\n'
          : '')
        + '      <TimeZone>' + esc(tz) + '</TimeZone>\n'
        + '      <FirstLogonCommands>\n';
      cmds.forEach((c, i) => {
        xml += '        <SynchronousCommand wcm:action="add">\n'
          + '          <Order>' + (i + 1) + '</Order>\n'
          + '          <CommandLine>' + esc(c.cmd) + '</CommandLine>\n'
          + '          <Description>' + esc(c.desc) + '</Description>\n'
          + '        </SynchronousCommand>\n';
      });
      xml += '      </FirstLogonCommands>\n'
        + '    </component>\n  </settings>\n</unattend>\n';
      return { xml, cmdCount: cmds.length };
    }

    function download() {
      const { xml, cmdCount } = buildXml();
      const a = App.h("a", {
        href: URL.createObjectURL(new Blob([xml], { type: "application/xml" })),
        download: "autounattend.xml",
      });
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 200);
      App.toast(`autounattend.xml 已生成（${cmdCount} 条首次登录命令）`, "ok", 6000);
    }

    loadOpts();
    return App.h("div", null,
      App.h("p", { class: "hint", style: { margin: "0 0 8px" } },
        "生成微软官方 autounattend.xml：放进 Windows 安装 U 盘根目录即可无人值守安装（语言/账户/OOBE 全自动，装完自动应用勾选的优化）。"),
      App.h("div", { class: "card", style: { padding: "10px 14px" } },
        App.h("div", { class: "card-title" }, "区域与账户"),
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "语言"), f.lang),
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "时区"), f.tz),
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "用户名"), f.user),
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "密码"), f.pwd),
        App.h("div", { class: "prop-row" },
          App.h("span", { class: "prop-key" }, "自动登录"),
          App.h("label", { class: "chk" }, f.autologon, " 装机完成自动登录一次")),
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "计算机名"), f.computer),
        App.h("div", { class: "prop-row" }, App.h("span", { class: "prop-key" }, "产品密钥"), f.key),
        App.h("div", { class: "prop-row" },
          App.h("span", { class: "prop-key" }, "OOBE"),
          App.h("label", { class: "chk" }, f.skipprivacy, " 跳过隐私问题（推荐）"),
          App.h("label", { class: "chk" }, f.bypass, " 强制本地账户（屏蔽联网账户页）")),
      ),
      App.h("div", { class: "card", style: { padding: "10px 14px", marginTop: "10px" } },
        App.h("div", { class: "card-title" }, "磁盘分区（危险）"),
        App.h("label", { class: "chk" }, f.disk,
          " 自动清空磁盘 0 并分区（GPT/UEFI 标准三分区）——⚠ 会删除磁盘 0 全部数据！"),
        App.h("div", { class: "hint", style: { marginTop: "4px" } },
          "不勾选则安装时手动分区。")),
      App.h("div", { class: "card", style: { padding: "10px 14px", marginTop: "10px" } },
        App.h("div", { class: "row" },
          App.h("div", { class: "card-title", style: { margin: "0" } }, "预装系统优化"),
          optCount),
        App.h("div", { class: "hint" },
          "勾选的优化将以首次登录命令方式在装完系统后自动应用（注册表与服务型）。"),
        App.h("div", { style: { marginTop: "6px" } }, optBox)),
      App.h("div", { class: "row", style: { marginTop: "10px" } },
        App.h("button", {
          class: "btn primary", onclick: async () => {
            if (!(f.user.value || "").trim()) { App.toast("请填写用户名", "warn"); return; }
            if (f.disk.checked && !(await App.confirm(
              "⚠ 已勾选自动分区", "生成的应答文件会清空磁盘 0 的全部数据！确定继续？"))) return;
            download();
          },
        }, "下载 autounattend.xml"),
        App.h("span", { class: "hint" }, "将文件放到安装 U 盘根目录")));
  }

  /* ================= 11 DNS 切换 ================= */
  function buildDns() {
    const adapterSel = App.h("select", { class: "input" });
    const dns1 = App.h("input", { class: "input mono", placeholder: "首选 DNS，如 223.5.5.5" });
    const dns2 = App.h("input", { class: "input mono", placeholder: "备用 DNS，如 114.114.114.114" });
    const info = App.h("div", { class: "hint" }, "正在加载网卡列表…");
    const presets = App.h("div", { class: "row", style: { flexWrap: "wrap", gap: "6px" } });
    const presetList = [
      ["阿里 DNS", "223.5.5.5", "223.6.6.6"],
      ["114 DNS", "114.114.114.114", "114.114.115.115"],
      ["腾讯 DNS", "119.29.29.29", ""],
      ["Google DNS", "8.8.8.8", "8.8.4.4"],
      ["Cloudflare", "1.1.1.1", "1.0.0.1"],
    ];
    for (const [label, a, b] of presetList) {
      presets.appendChild(App.h("button", { class: "btn sm", onclick: () => {
        dns1.value = a; dns2.value = b;
      } }, label));
    }
    async function loadAdapters() {
      const r = await App.tryCall("dns_list_adapters");
      if (!r.ok) { info.textContent = "加载失败：" + r.err; return; }
      const list = (r.data || []).map((a) => (a && typeof a === "object")
        ? { name: String(a.name || ""), mac: String(a.mac || "") }
        : { name: String(a || ""), mac: "" }).filter((a) => a.name);
      adapterSel.innerHTML = "";
      if (!list.length) {
        adapterSel.appendChild(App.h("option", { value: "" }, "（未检测到网卡）"));
        info.textContent = "未找到可用的网络适配器";
        return;
      }
      for (const a of list) {
        adapterSel.appendChild(App.h("option", { value: a.name },
          a.mac ? a.name + "（" + a.mac + "）" : a.name));
      }
      info.textContent = "共检测到 " + list.length + " 个适配器";
      loadCurrent();
    }
    async function loadCurrent() {
      const name = adapterSel.value;
      if (!name) return;
      const r = await App.tryCall("dns_get", name);
      if (r.ok && r.data) {
        dns1.value = r.data.primary || "";
        dns2.value = r.data.secondary || "";
      }
    }
    adapterSel.addEventListener("change", loadCurrent);
    async function applyDns() {
      const name = adapterSel.value;
      if (!name) { App.toast("请先选择网卡", "error"); return; }
      const a = dns1.value.trim(), b = dns2.value.trim();
      if (!a) { App.toast("请输入首选 DNS", "error"); return; }
      const r = await App.tryCall("dns_set", name, a, b);
      if (!r.ok) { App.toast(r.err, "error", 6000); return; }
      App.toast("DNS 已设置：" + a + (b ? " / " + b : ""), "ok");
    }
    async function resetDns() {
      const name = adapterSel.value;
      if (!name) { App.toast("请先选择网卡", "error"); return; }
      const r = await App.tryCall("dns_reset", name);
      if (!r.ok) { App.toast(r.err, "error", 6000); return; }
      dns1.value = ""; dns2.value = "";
      App.toast("已恢复自动获取 DNS", "ok");
    }
    loadAdapters();
    return pane(
      App.h("p", { class: "hint", style: { margin: "0 0 6px" } },
        "一键切换网卡 DNS（需要管理员权限）"),
      field("网卡：", adapterSel),
      field("首选 DNS：", dns1),
      field("备用 DNS：", dns2),
      App.h("div", { class: "field-label", style: { marginTop: "6px" } }, "常用 DNS 预设："),
      presets,
      actions(
        App.h("button", { class: "btn sm primary", onclick: applyDns }, "应用 DNS"),
        App.h("button", { class: "btn sm", onclick: resetDns }, "恢复自动"),
        App.h("button", { class: "btn sm", onclick: loadAdapters }, "刷新"),
      ),
      info,
    );
  }

  /* ================= 12 显示器信息 ================= */
  function buildMonitorInfo() {
    const out = App.h("div", { class: "list" });
    function refresh() {
      out.innerHTML = "";
      const s = window.screen;
      const dpr = window.devicePixelRatio || 1;
      const items = [
        ["屏幕分辨率", `${s.width} × ${s.height}`],
        ["可用区域", `${s.availWidth} × ${s.availHeight}`],
        ["色深", `${s.colorDepth} bit`],
        ["设备像素比", dpr.toFixed(2) + "x"],
        ["逻辑分辨率（CSS）", `${Math.round(s.width / dpr)} × ${Math.round(s.height / dpr)}`],
        ["物理分辨率（估算）", `${Math.round(s.width * dpr)} × ${Math.round(s.height * dpr)}`],
        ["方向", s.orientation ? s.orientation.type : "未知"],
      ];
      for (const [label, val] of items) {
        out.appendChild(App.h("div", { class: "list-item" },
          App.h("span", { class: "li-main" }, App.h("div", { class: "li-title" }, label)),
          App.h("span", { class: "tag" }, val),
        ));
      }
    }
    refresh();
    return pane(
      out,
      actions(
        App.h("button", { class: "btn sm", onclick: refresh }, "刷新"),
        spacer(),
        App.h("button", { class: "btn sm", onclick: () => {
          const s = window.screen;
          const dpr = window.devicePixelRatio || 1;
          const text = `分辨率: ${s.width}×${s.height}\n可用: ${s.availWidth}×${s.availHeight}\n色深: ${s.colorDepth}bit\nDPR: ${dpr}x`;
          navigator.clipboard.writeText(text).then(
            () => App.toast("已复制", "ok"),
            () => App.toast("复制失败", "error"),
          );
        } }, "复制信息"),
      ),
    );
  }

  /* ================= 13 屏幕取色 ================= */
  function buildColor() {
    const chip = App.h("span", {
      style: {
        display: "inline-block", width: "18px", height: "18px", borderRadius: "4px",
        border: "1px solid var(--border)", verticalAlign: "middle", background: "#888",
      },
    });
    const txt = App.h("span", { class: "mono" }, "未取色");
    const picked = { hex: "" };
    const out = App.toolOut({ saveName: "取色" });
    async function run() {
      App.toast("取色器已打开：移动鼠标取色，左键/空格锁定并复制，ESC 取消", "info", 5000);
      const r = await App.tryCall("tool_pick_color");
      if (!r.ok) { App.toast(r.err, "error", 6000); return; }
      const d = r.data;
      if (!d || !d.hex) { App.toast("已取消取色", "info"); return; }
      picked.hex = d.hex;
      chip.style.background = d.hex;
      txt.textContent = `${d.hex}　·　RGB(${d.rgb[0]}, ${d.rgb[1]}, ${d.rgb[2]})`;
      out.set([{ label: "取色结果",
                 el: App.h("div", { class: "row", style: { gap: "8px" } }, chip, txt) }]);
      App.toast("已取色并复制到剪贴板：" + d.hex, "ok");
    }
    return pane(
      App.h("p", { class: "hint", style: { margin: "0 0 6px" } },
        "全屏放大预览 + 实时通道值；锁定后自动复制 HEX 到剪贴板。"),
      actions(App.h("button", { class: "btn sm primary", onclick: run }, "打开取色器")),
      out.el,
    );
  }

  /* ================= 14 图片 OCR =================
     v5.1：OCR 已独立为一级页面（pages/ocr.js），工具箱不再收录。 */

  /* ================= 15 调色板提取 ================= */
  function buildPalette() {
    const countSel = App.h("select", { class: "input", style: { width: "80px", flex: "none" } },
      ["4", "6", "8", "12", "16", "24"].map((v) =>
        App.h("option", { value: v }, v + " 色")));
    countSel.value = "8";
    const out = App.toolOut({ saveName: "调色板" });
    async function run() {
      App.toast("请在对话框中选择图片…", "info", 3000);
      const r = await App.tryCall("editor_open");
      if (!r.ok) { App.toast(r.err || "未选择文件", "info"); return; }
      const dataUrl = (r.data || {}).data_url;
      if (!dataUrl) return;
      const path = (r.data || {}).path || "";   // v4.5：修复此前引用未定义变量 path 的问题
      const pr = await App.tryCall("editor_extract_palette", dataUrl, parseInt(countSel.value, 10));
      if (!pr.ok) { App.toast(pr.err, "error", 5000); return; }
      const colors = pr.data || [];
      const grid = App.h("div", { style: { display: "flex", flexWrap: "wrap", gap: "8px" } });
      for (const c of colors) {
        const swatch = App.h("div", {
          style: {
            width: "48px", height: "48px", borderRadius: "6px",
            background: c.hex, border: "1px solid var(--border)",
            cursor: "pointer", title: c.hex + " · RGB(" + c.rgb.join(",") + ")\n点击复制",
          },
          onclick: () => {
            navigator.clipboard.writeText(c.hex).then(
              () => App.toast("已复制 " + c.hex, "ok"),
              () => App.toast("复制失败", "error"),
            );
          },
        });
        const label = App.h("div", { class: "mono", style: { fontSize: "11px", textAlign: "center" } }, c.hex);
        grid.appendChild(App.h("div", { style: { display: "flex", flexDirection: "column", gap: "2px" } }, swatch, label));
      }
      out.set([{ label: "调色板 · " + colors.length + " 色（" +
                 (path.split(/[/\\]/).pop() || "图片") + "）", el: grid }]);
    }
    return pane(
      App.h("p", { class: "hint", style: { margin: "0 0 6px" } },
        "从图片中提取主色调色板，点击色块复制 HEX"),
      field("提取数量：", countSel),
      actions(App.h("button", { class: "btn sm primary", onclick: run }, "选择图片提取")),
      out.el,
    );
  }

  /* ================= 目录数据 ================= */
  const IMAGE_TOOLS = [
    { id: "editor", title: "图片编辑", desc: "滤镜、标注、裁剪、文字等图像编辑功能", icon: "image", section: "屏幕与图像", kw: "图片 ps 编辑 滤镜" },
    /* v5.2：剪贴板历史升级为一级页面（互联与协作组），不再从工具箱进入 */
    { id: "combine", title: "图片合并", desc: "垂直/水平/网格拼接多张图片", icon: "copy", section: "屏幕与图像", kw: "拼接 合并 长图" },
    { id: "split", title: "图片分割", desc: "将图片按行列拆分为多个子图", icon: "scissors", section: "屏幕与图像", kw: "切割 拆分 九宫格" },
    { id: "batch", title: "批量处理", desc: "批量调整大小、特效、格式转换", icon: "layers", section: "屏幕与图像", kw: "批量 缩放 转格式" },
    { id: "video", title: "视频编辑", desc: "视频剪辑、转码、提取帧等功能", icon: "video", section: "屏幕与图像", kw: "视频 剪辑 转码 gif" },
  ];
  const BUILT_IN_TOOLS = [
    { id: "tool-text", title: "文本工具箱", desc: "JSON/XML/SQL 格式化 · Base64 等编解码 · 文本对比", icon: "code", section: "转换与编码", kw: "json xml sql base64 unicode 编码 解码 diff 对比 格式化 压缩", build: buildText },
    { id: "tool-timestamp", title: "时间戳转换", desc: "秒 / 毫秒与日期互转", icon: "clock", section: "转换与编码", kw: "时间 日期 unix 时间戳", build: buildTimestamp },
    { id: "tool-radix", title: "进制转换", desc: "2/8/10/16 互转（BigInt 大数）与位运算", icon: "hash", section: "转换与编码", kw: "二进制 十六进制 位运算 大数 bigint", build: buildRadix },
    { id: "tool-files", title: "文件校验中心", desc: "哈希计算 · .md5 清单校验 · 目录快照 · CSV 导出", icon: "folder", section: "文件与校验", kw: "md5 sha256 哈希 校验 清单 快照 导出 csv", build: buildFiles },
    { id: "tool-password", title: "随机密码", desc: "浏览器安全随机生成密码", icon: "key", section: "生成与安全", kw: "密码 随机 安全 熵", build: buildPassword },
    { id: "tool-uuid", title: "UUID 生成", desc: "UUID v4 / v7 批量生成", icon: "zap", section: "生成与安全", kw: "uuid guid 雪花 id 标识", build: buildUuid },
    { id: "tool-qr", title: "二维码", desc: "文本 / 链接生成二维码", icon: "qrcode", section: "生成与安全", kw: "二维码 qr 扫码 链接", build: buildQr },
    { id: "tool-regex", title: "正则测试", desc: "正则匹配与高亮预览", icon: "search", section: "生成与安全", kw: "正则 表达式 regex 匹配", build: buildRegex },
    { id: "tool-color", title: "屏幕取色", desc: "全屏放大镜取色并复制 HEX", icon: "cursor", section: "屏幕与图像", kw: "取色 颜色 hex 放大镜", build: buildColor },
    { id: "tool-palette", title: "调色板", desc: "从图片提取主色调", icon: "palette", section: "屏幕与图像", kw: "调色板 主色 配色", build: buildPalette },
    { id: "tool-port", title: "端口占用", desc: "查询 / 释放端口占用进程", icon: "radar", section: "网络工具", kw: "端口 占用 进程 杀进程", build: buildPort },
    { id: "tool-dns", title: "DNS 切换", desc: "网卡 DNS 快速切换预设", icon: "server", section: "网络工具", kw: "dns 网卡 阿里 114", build: buildDns },
    { id: "tool-wifi", title: "Wi-Fi 密码查看", desc: "查看本机已保存的无线网络密码", icon: "wifi", section: "网络工具", kw: "wifi 无线 密码 wlan 网络 netsh", build: buildWifi },
    { id: "tool-proxy", title: "代理开关", desc: "系统代理一键开 / 关", icon: "globe", section: "网络工具", kw: "代理 开关 v2rayn 系统代理", build: buildProxyQuickSwitch },
  ];
  /* v5.5：系统类工具提升为一级页面（侧栏「系统」分组），不再从工具箱进入 */
  const SYSTEM_TOOLS = [
    { id: "tool-optimize", title: "系统优化", desc: "隐私遥测 / 任务栏 / 服务 / 更新策略 / 垃圾清理 / 系统修复（快照可还原）", icon: "settings", kw: "优化 隐私 遥测 精简 debloat 服务 更新 清理 修复 winutil", build: buildOptimize },
    { id: "tool-sysinfo", title: "硬件信息", desc: "CPU / 内存 / 显卡 / 磁盘 / 网卡 / 主板 全景与实时使用率", icon: "monitor", kw: "硬件 cpu 内存 显卡 磁盘 主板 bios 系统 配置 使用率", build: buildHardware },
    { id: "tool-monitor", title: "显示器信息", desc: "分辨率 / 缩放 / 多屏布局", icon: "monitor", kw: "显示器 分辨率 dpr 缩放", build: buildMonitorInfo },
    { id: "tool-unattend", title: "无人值守安装", desc: "生成 autounattend.xml：装机全自动并预装系统优化", icon: "filetext", kw: "autounattend 无人值守 安装 重装 应答 装机", build: buildUnattend },
  ];
  const SECTION_ORDER = ["转换与编码", "文件与校验", "生成与安全", "屏幕与图像", "网络工具"];
  const SECTION_TINT = { "转换与编码": 0, "文件与校验": 1, "生成与安全": 2, "屏幕与图像": 3, "网络工具": 4 };
  const ALL_TOOLS = BUILT_IN_TOOLS.concat(IMAGE_TOOLS);
  App.TOOL_CATALOG = ALL_TOOLS;   // Ctrl+K 直达数据源

  /* ================= 目录页 ================= */
  function toolRow(t) {
    return App.h("div", {
      class: "tool-item",
      style: { cursor: "pointer" },
      onclick: () => { App.recordToolRecent(t.id); App.navigate(t.id); },
    },
      App.h("div", { class: "tool-item-icon tint-" + (SECTION_TINT[t.section] % 6),
                     html: App.icon(t.icon, 24) }),
      App.h("div", { class: "tool-item-content" },
        App.h("div", { class: "tool-item-title" }, t.title),
        App.h("div", { class: "tool-item-desc" }, t.desc),
      ),
      starBtn(t.id),
    );
  }
  /* v5.4：分类可折叠（默认折叠，会话内记忆展开状态；「清理 ⋯」式收纳减少首屏堆叠） */
  const secOpen = new Set();
  function sectionCard(title, tools) {
    const el = App.sec(`${title}（${tools.length}）`,
      [App.h("div", { class: "tool-list" }, tools.map(toolRow))],
      { open: secOpen.has(title) });
    el.querySelector(".sec-head").addEventListener("click", () => {
      /* App.sec 的内部监听已先切换 collapsed 类，此处读取的是切换后的状态 */
      if (el.classList.contains("collapsed")) secOpen.delete(title);
      else secOpen.add(title);
    });
    return el;
  }
  function chip(label, t, onRemove) {
    const el = App.h("span", { class: "tool-chip", title: t.desc || t.title,
      onclick: () => { App.recordToolRecent(t.id); App.navigate(t.id); } },
      App.h("span", { html: App.icon(t.icon || "toolbox", 13) }),
      label,
      onRemove ? App.h("span", { class: "x", title: "取消收藏",
        onclick: (e) => { e.stopPropagation(); onRemove(); } }, "×") : null);
    return el;
  }

  App.registerPage({
    id: "tools",
    title: "工具箱",
    icon: "toolbox",
    group: "工具与增效",

    mount(el) {
      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "工具箱"),
        App.h("div", { class: "sub" }, "点击工具进入独立页面；星标收藏，Ctrl+K 随时直达"),
      ));

      const searchIn = App.h("input", { class: "input", type: "search",
        placeholder: "搜索工具（标题 / 描述 / 关键字）…" });
      const toolbar = App.h("div", { class: "tool-toolbar" }, searchIn,
        App.h("span", { class: "hint", style: { flex: "none" } }, "Ctrl+K 快速直达"));
      const favBox = App.h("div", { class: "tool-chips" });
      const recentBox = App.h("div", { class: "tool-chips" });
      const grid = App.h("div", { class: "tools-grid one" });

      function chipRow(box, labelText, ids, withRemove) {
        box.innerHTML = "";
        const list = ids.map((id) => ALL_TOOLS.find((t) => t.id === id)).filter(Boolean);
        box.appendChild(App.h("span", { class: "tool-chips-label" }, labelText));
        if (!list.length) {
          box.appendChild(App.h("span", { class: "hint" }, "暂无"));
          return;
        }
        for (const t of list) {
          box.appendChild(chip(t.title, t,
            withRemove ? () => { App.toggleToolFav(t.id).then(render); } : null));
        }
      }

      function render() {
        const q = searchIn.value.trim().toLowerCase();
        chipRow(favBox, "★ 收藏：", favs(), true);
        chipRow(recentBox, "最近使用：", recents().slice(0, 6), false);
        grid.innerHTML = "";
        const shown = q
          ? ALL_TOOLS.filter((t) =>
              (t.title || "").toLowerCase().includes(q) ||
              (t.desc || "").toLowerCase().includes(q) ||
              (t.kw || "").toLowerCase().includes(q))
          : ALL_TOOLS;
        const sections = q ? [] : SECTION_ORDER;
        if (q) {
          grid.appendChild(App.h("div", { class: "card" },
            App.h("div", { class: "card-title" }, `搜索结果（${shown.length}）`),
            App.h("div", { class: "tool-list" },
              shown.length ? shown.map(toolRow)
                : App.h("div", { class: "empty" }, "没有匹配的工具"))));
        } else {
          grid.append(...sections.map((s) =>
            sectionCard(s, ALL_TOOLS.filter((t) => t.section === s))));
        }
      }
      searchIn.addEventListener("input", render);
      App._toolDirRerender = render;   // 星标/收藏在其它入口变更后刷新目录
      el.append(toolbar, favBox, recentBox, grid);
      render();
    },

    show() {
      /* 收藏/最近使用可能在其它入口变更：回到目录页时重渲染 */
      if (typeof App._toolDirRerender === "function") App._toolDirRerender();
    },
  });

  /* 为每个内置工具注册隐藏二级页面（toolShell 统一壳） */
  BUILT_IN_TOOLS.forEach((t) => {
    App.registerPage({
      id: t.id,
      title: t.title,
      group: "工具与增效",
      hidden: true,
      icon: t.icon,
      mount(el) {
        /* v5.3：标记为全高页面，让工具页工作区铺满内容区（最后一张结果/画布
           卡吸收剩余高度），消除此前"顶部一个小卡 + 下方 400~670px 空白" */
        el.classList.add("page-flex");
        el.appendChild(toolShell(t, t.build));
      },
      show() { /* 纯本地工具，无需刷新 */ },
    });
  });

  /* v5.5：系统类工具注册为一级页面（侧栏「系统」分组，与「设置」同组） */
  SYSTEM_TOOLS.forEach((t) => {
    App.registerPage({
      id: t.id,
      title: t.title,
      group: "系统",
      icon: t.icon,
      mount(el) {
        el.classList.add("page-flex");
        el.appendChild(toolShell(t, t.build, true));
      },
      show() { /* 纯本地工具，无需刷新 */ },
    });
  });
})();
