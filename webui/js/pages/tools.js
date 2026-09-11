/* 工具箱（v4.5 重构）：
 *   目录页（搜索 / 收藏 / 最近使用 / 分区网格） + 15 个工具二级页（toolShell 统一壳）。
 * 合并：tool-text（格式化/编码解码/文本对比）、tool-files（哈希/清单校验/目录快照/列表导出）。
 * 新增：tool-uuid（UUID v4/v7）、tool-radix（BigInt 任意进制转换）。
 * 统一结果面板 App.toolOut（ui.js）、Ctrl+K 直达 App.toolLauncher（ui.js）。
 * 后端：tool_*（app/bridge/tools_api.py）与 dns_api / editor_api / proxy_* 调用保持不变。
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

  /* ================= 工具页壳：返回 + 标题 + 星标 + 内容 ================= */
  function toolShell(t, buildBody) {
    const head = App.h("div", { class: "tool-page-head", style: { display: "flex", alignItems: "center", gap: "10px" } },
      App.h("button", { class: "btn sm", onclick: () => App.navigate("tools") }, "← 返回工具箱"),
      App.h("div", { class: "tool-item-icon", html: App.icon(t.icon, 22) }),
      App.h("div", { style: { minWidth: "0" } },
        App.h("h2", { style: { margin: 0, fontSize: "17px" } }, t.title),
        App.h("div", { class: "sub", style: { margin: 0 } }, t.desc)),
      spacer(),
      starBtn(t.id),
    );
    return App.h("div", null, head, App.h("div", { class: "tool-page" }, buildBody()));
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

  /* ================= 14 图片 OCR ================= */
  function buildOcrFile() {
    const out = App.toolOut({ saveName: "OCR 识别" });
    async function run() {
      App.toast("请在对话框中选择图片文件…", "info", 4000);
      const r = await App.tryCall("tool_ocr_file");
      if (!r.ok) { App.toast(r.err, "error", 7000); return; }
      const d = r.data || {};
      if (d.empty || !(d.text || "").trim()) { App.toast("未识别到文字", "info", 4000); return; }
      out.set([{ label: "识别全文（" + (d.path || "图片") + "）", text: d.text,
                 ts: Math.floor(Date.now() / 1000) }]);
      App.ocrResultModal(d);
    }
    return pane(
      App.h("p", { class: "hint", style: { margin: "0 0 6px" } },
        "基于 Windows.Media.Ocr 系统引擎，本地离线识别图片中的文字；结果可复制 / 另存。"),
      actions(App.h("button", { class: "btn sm primary", onclick: run }, "选择图片识别")),
      out.el,
    );
  }

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
    { id: "cliphist", title: "剪贴板历史", desc: "查看和管理剪贴板历史记录", icon: "clipboard", section: "屏幕与图像", kw: "剪贴板 粘贴 历史" },
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
    { id: "tool-ocr", title: "文字识别", desc: "本地 OCR 识别图片文字", icon: "filetext", section: "屏幕与图像", kw: "ocr 文字识别 图片", build: buildOcrFile },
    { id: "tool-palette", title: "调色板", desc: "从图片提取主色调", icon: "palette", section: "屏幕与图像", kw: "调色板 主色 配色", build: buildPalette },
    { id: "tool-port", title: "端口占用", desc: "查询 / 释放端口占用进程", icon: "radar", section: "系统与网络", kw: "端口 占用 进程 杀进程", build: buildPort },
    { id: "tool-dns", title: "DNS 切换", desc: "网卡 DNS 快速切换预设", icon: "server", section: "系统与网络", kw: "dns 网卡 阿里 114", build: buildDns },
    { id: "tool-monitor", title: "显示器信息", desc: "分辨率 / 缩放 / 多屏布局", icon: "monitor", section: "系统与网络", kw: "显示器 分辨率 dpr 缩放", build: buildMonitorInfo },
    { id: "tool-proxy", title: "代理开关", desc: "系统代理一键开 / 关", icon: "globe", section: "系统与网络", kw: "代理 开关 v2rayn 系统代理", build: buildProxyQuickSwitch },
  ];
  const SECTION_ORDER = ["转换与编码", "文件与校验", "生成与安全", "屏幕与图像", "系统与网络"];
  const SECTION_TINT = { "转换与编码": 0, "文件与校验": 1, "生成与安全": 2, "屏幕与图像": 3, "系统与网络": 4 };
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
  function sectionCard(title, tools) {
    return App.h("div", { class: "card" },
      App.h("div", { class: "card-title" }, title),
      App.h("div", { class: "tool-list" }, tools.map(toolRow)));
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
        el.appendChild(toolShell(t, t.build));
      },
      show() { /* 纯本地工具，无需刷新 */ },
    });
  });
})();
