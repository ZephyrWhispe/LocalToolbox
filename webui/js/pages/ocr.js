/* OCR 识别页（v5.1，从工具箱独立）：
   三输入（选图/粘贴/拖放）+ 批量队列 + 记录栏 + 引擎状态 + 截图识字链路。 */
(function () {
  "use strict";

  const state = {
    records: [],        // {ts, source, text, raw_text, lines, engine, ms}
    running: false,
    queueTotal: 0, queueDone: 0,
    engines: null,      // tool_ocr_engines 快照
  };
  let refs = {};

  function fmtTime(ts) {
    const d = new Date(ts);
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
  }

  /* ---------------- 引擎状态卡 ---------------- */
  async function refreshEngines() {
    const r = await App.tryCall("tool_ocr_engines");
    if (!r.ok) return;
    state.engines = r.data;
    renderEngines();
  }

  function renderEngines() {
    if (!refs.engineBox || !state.engines) return;
    const e = state.engines;
    for (const o of refs.engineSel.options) {
      const eng = o.value;
      o.textContent = o.textContent.replace(/（[^）]*）?$/, "") +
        (eng === "winrt" ? (e.winrt ? "" : "（不可用）")
          : eng === "rapid" ? (e.rapid ? "" : "（未安装）")
            : (e.umi_ready ? "（运行中）" : "（未运行）"));
    }
    if ([...refs.engineSel.options].some((o) => o.value === e.engine)) {
      refs.engineSel.value = e.engine;
    }
    refs.umiRow.style.display = e.engine === "umi" ? "" : "none";
    refs.engineHint.textContent = e.engine === "umi"
      ? (e.umi_ready ? "Umi-OCR 服务运行中：" + e.umi_url
         : (e.umi_path ? "内核已就绪，识别时自动拉起服务"
            : "未安装内核：点「下载内核」自动下载解压，或手动指定 Umi-OCR.exe"))
      : (e.engine === "rapid" && !e.rapid
         ? "RapidOCR 未安装：源码运行请 pip install rapidocr_onnxruntime；打包版请用 winrt 或 umi 引擎"
         : "当前引擎就绪。");
  }

  /* ---------------- 记录栏 ---------------- */
  function renderRecords() {
    if (!refs.records) return;
    refs.records.innerHTML = "";
    if (!state.records.length) {
      refs.records.appendChild(App.h("div", { class: "empty" },
        "暂无识别记录\n选图 / 粘贴 / 拖放图片，或按 Ctrl+Alt+O 圈选屏幕"));
      return;
    }
    state.records.forEach((rec, i) => {
      /* v5.2 P4：行内只留「查看」，复制/删除走右键菜单 */
      const recMenu = (x, y) => App.contextMenu([
        { label: "查看全文", icon: "eye", onclick: () => App.ocrResultModal(rec) },
        { label: "复制全文", icon: "copy",
          onclick: async () => { await App.copyText(rec.text); App.toast("已复制", "ok"); } },
        "sep",
        { label: "删除该记录", icon: "trash", danger: true,
          onclick: () => { state.records.splice(i, 1); renderRecords(); } },
      ], x, y);
      refs.records.appendChild(App.h("div", {
        class: "card", style: { padding: "9px 13px", marginBottom: "8px" },
        oncontextmenu: (ev) => { ev.preventDefault(); recMenu(ev.clientX, ev.clientY); },
      },
        App.h("div", { style: { display: "flex", alignItems: "center", gap: "8px" } },
          App.h("span", { style: { fontWeight: "600", flex: "1", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } },
            rec.source || "图片"),
          App.h("span", { class: "hint" }, `${rec.engine || ""} · ${fmtTime(rec.ts)} · ${rec.lines}行 · ${rec.ms}ms`),
          App.h("button", {
            class: "btn sm", onclick: () => App.ocrResultModal(rec),
          }, "查看"),
        ),
        App.h("div", { class: "hint", style: { marginTop: "4px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } },
          (rec.text || "(无文字)").slice(0, 160)),
      ));
    });
  }

  function addRecord(rec) {
    state.records.unshift(rec);
    renderRecords();
  }

  /* ---------------- 识别动作 ---------------- */
  /* v5.1b：统一 busy 控制——识别/下载期间禁用全部入口按钮 */
  function setBusy(busy) {
    state.running = busy;
    for (const b of refs.actionBtns || []) b.disabled = busy;
  }

  async function recognizePath(path) {
    const t0 = performance.now();
    const r = await App.tryCall("tool_ocr_file", path);
    const ms = Math.round(performance.now() - t0);
    if (!r.ok) {
      addRecord({ ts: Date.now(), source: path, ms, text: "识别失败：" + r.err, raw_text: "", lines: 0, engine: state.engines ? state.engines.engine : "", err: true });
      return;
    }
    if (r.data.empty) {
      addRecord({ ts: Date.now(), source: path, ms, text: "(未识别到文字)", raw_text: "", lines: 0, engine: r.data.engine || "" });
      return;
    }
    addRecord({ ts: Date.now(), source: path, ms, ...r.data });
  }

  async function runQueue(paths) {
    if (state.running) { App.toast("识别进行中，请稍候", "warn"); return; }
    setBusy(true);
    state.queueTotal = paths.length;
    state.queueDone = 0;
    renderProgress();
    for (const p of paths) {
      state.queueDone += 1;
      renderProgress(p);
      await recognizePath(p);
    }
    setBusy(false);
    refs.prog.textContent = `完成 ${paths.length} 张`;
    setTimeout(() => { refs.prog.textContent = ""; }, 2500);
  }

  function renderProgress(cur) {
    if (!refs.prog) return;
    if (state.running) {
      refs.prog.textContent = `识别中 ${state.queueDone}/${state.queueTotal}` +
        (cur ? " · " + (cur.split(/[\\/]/).pop() || "") : "…");
    }
  }

  async function doPick() {
    const r = await App.tryCall("tool_ocr_file");
    if (!r.ok) { if (r.err !== "未选择图片文件。") App.toast(r.err, "error"); return; }
    await runQueue(r.data.paths || []);
  }

  async function doClipboard() {
    if (state.running) { App.toast("识别进行中，请稍候", "warn"); return; }
    setBusy(true);
    try {
      const r = await App.tryCall("tool_ocr_clipboard");
      if (!r.ok) { App.toast(r.err, "error", 5000); return; }
      if (r.data.empty) { App.toast("未识别到文字", "warn"); return; }
      addRecord({ ts: Date.now(), source: "剪贴板", ms: 0, ...r.data });
      App.toast("识别完成", "ok");
    } finally {
      setBusy(false);
    }
  }

  function doCopyAll() {
    const texts = state.records.filter((r) => !r.err).map((r) => r.text);
    if (!texts.length) { App.toast("没有可复制的记录", "warn"); return; }
    App.copyText(texts.join("\n\n")).then(() => App.toast("已合并复制全部记录", "ok"));
  }

  function doExport(ext) {
    if (!state.records.length) { App.toast("没有记录可导出", "warn"); return; }
    let body;
    if (ext === "md") {
      body = state.records.map((r, i) =>
        `## ${i + 1}. ${r.source || "图片"}\n\n${r.text}\n`).join("\n");
    } else {
      body = state.records.map((r) =>
        `===== ${r.source || "图片"} =====\n${r.text}`).join("\n\n");
    }
    const blob = new Blob([body], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `OCR识别_${Date.now()}.${ext}`;
    a.click();
    URL.revokeObjectURL(a.href);
    App.toast(`已导出 ${state.records.length} 条记录（${ext.toUpperCase()}）`, "ok");
  }

  /* ---------------- 截图识字链路 ---------------- */
  async function ocrRegionCapture() {
    const r = await App.tryCall("shot_region_popup", "ocr", "all");
    if (!r.ok) App.toast(r.err, "error", 6000);
  }

  /* ---------------- 页面注册 ---------------- */
  App.registerPage({
    id: "ocr",
    title: "OCR 识别",
    icon: "eye",
    group: "工具与增效",

    async mount(el) {
      el.innerHTML = "";
      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "OCR 识别"),
        App.h("div", { class: "sub" },
          "选图 / 粘贴 / 拖放批量识别，Ctrl+Alt+O 圈选屏幕识字（识别后自动复制全文）"),
      ));

      /* 引擎状态卡 */
      refs.engineSel = App.h("select", { class: "input", style: { width: "auto" } },
        App.h("option", { value: "winrt" }, "Windows 内置引擎"),
        App.h("option", { value: "rapid" }, "RapidOCR 本地内核"),
        App.h("option", { value: "umi" }, "Umi-OCR 内核"));
      refs.engineSel.addEventListener("change", (e) =>
        App.tryCall("cfg_set", "ocr_engine", e.target.value).then(renderEngines));
      refs.engineHint = App.h("span", { class: "hint", style: { flex: "1", minWidth: "0" } });
      refs.umiRow = App.h("div", { class: "row", style: { marginTop: "8px", display: "none" } },
        /* v5.2 P4：内核维护按钮收进折叠区（低频），由 renderEngines 控制显隐 */
        App.h("div", { ref: true, style: { display: "flex", flexDirection: "column", gap: "8px", width: "100%" } },
          App.h("div", { class: "row" },
            App.h("button", {
              class: "btn sm", onclick: async () => {
                if (state.dlRunning) { App.toast("内核正在下载中", "warn"); return; }
                state.dlRunning = true;
                App.toast("开始下载 Umi-OCR 内核（约 103MB）…", "ok", 6000);
                await App.tryCall("ocr_umi_download");
              },
            }, "下载内核"),
            App.h("button", {
              class: "btn sm", onclick: async () => {
                App.toast("正在启动内核服务…", "ok");
                const r = await App.tryCall("ocr_umi_start");
                if (!r.ok) App.toast(r.err, "error", 6000);
                else App.toast("内核服务已就绪", "ok");
              },
            }, "启动内核"),
            App.h("span", { class: "hint" },
              "Umi-OCR 由本页按需下载运行（RapidOCR 内核），识别全程本机离线；也可自行安装 Umi-OCR 后在本机 1224 端口提供服务"),
          ),
        ),
      );
      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "识别引擎"),
        App.h("div", { class: "row" },
          App.h("span", { class: "field-label", style: { flex: "none" } }, "引擎："),
          refs.engineSel, refs.engineHint,
        ),
        refs.umiRow,
      ));

      /* 输入 + 队列 */
      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "row" },
          App.h("button", { class: "btn primary", onclick: doPick }, "选择图片（可多选）"),
          App.h("button", { class: "btn", onclick: doClipboard }, "粘贴图片识别（Ctrl+V）"),
          App.h("button", { class: "btn", onclick: ocrRegionCapture }, "圈选屏幕识字"),
          App.h("span", { class: "hint", style: { flex: "1", minWidth: "0", textAlign: "right" } },
            refs.prog = App.h("span", null, "")),
        ),
        App.h("div", {
          class: "hint", style: { marginTop: "8px" },
          ondragover: (e) => e.preventDefault(),
          ondrop: (e) => {
            e.preventDefault();
            const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
            const paths = files.map((f) => f.path).filter(Boolean);
            if (!paths.length) { App.toast("无法获取拖入文件路径", "warn"); return; }
            runQueue(paths);
          },
        }, "提示：把图片文件直接拖到这里也能批量识别；识别引擎与后处理在下方/设置中调整。"),
      ));

      /* 记录栏 */
      refs.records = App.h("div", { style: { marginTop: "2px" } });
      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "row" },
          App.h("div", { class: "card-title", style: { margin: "0" } }, "识别记录"),
          App.h("span", { style: { flex: "1" } }),
          App.h("button", { class: "btn sm", onclick: doCopyAll }, "合并复制全部"),
          App.h("button", {
            class: "btn sm", title: "导出识别结果",
            onclick: (e) => App.overflowMenu(e.currentTarget, [
              { label: "导出为 TXT", icon: "filetext", onclick: () => doExport("txt") },
              { label: "导出为 Markdown", icon: "filetext", onclick: () => doExport("md") },
            ]),
          }, "导出 ▾"),
          App.h("button", {
            class: "btn sm danger", onclick: () => {
              state.records = [];
              renderRecords();
            },
          }, "清空"),
        ),
        refs.records,
      ));

      /* Ctrl+V 粘贴图片识别 */
      el.addEventListener("paste", (e) => {
        const items = (e.clipboardData && e.clipboardData.items) || [];
        const hasImg = [...items].some((it) => it.type && it.type.startsWith("image/"));
        if (hasImg) {
          e.preventDefault();
          doClipboard();
        }
      });
      el.tabIndex = -1;

      /* 事件：截图识字链路 + 内核下载进度 + 引擎状态推送 */
      App.on("shot_ocr_start", () => {
        App.toast("正在识别…", "ok", 8000);
        state.running = true;
      });
      App.on("shot_ocr_done", async (d) => {
        state.running = false;
        if (!d || !d.ok) {
          App.toast((d && d.err) || "识别失败", "error", 8000);
          return;
        }
        if (!d.text || !d.text.trim()) { App.toast("未识别到文字", "warn"); return; }
        await App.copyText(d.text);
        addRecord({ ts: Date.now(), source: "屏幕圈选", ms: 0, ...d });
        App.ocrResultModal(d);
        App.toast("识别结果已复制到剪贴板", "ok");
      });
      App.on("shot_region_cancel", () => { state.running = false; });
      App.on("ocr_download_progress", (d) => {
        if (d.stage === "download") {
          const mb = (n) => (n / 1048576).toFixed(1);
          App.toast(`内核下载中 ${mb(d.done)}/${mb(d.total)} MB`, "ok", 1500);
        } else if (d.stage === "done") {
          App.toast("内核下载完成", "ok");
          refreshEngines();
        } else if (d.stage === "error") {
          App.toast("内核下载失败：" + d.err, "error", 8000);
        }
      });
      App.on("ocr_state", (d) => { state.engines = d; renderEngines(); });

      await refreshEngines();
      renderRecords();
    },

    show() { refreshEngines(); },
  });
})();
