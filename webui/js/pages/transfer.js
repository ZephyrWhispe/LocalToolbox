/* 文件传输页：服务状态、待确认、进行中、历史、日志；并给设备卡片提供发送入口。 */
(function () {
  "use strict";

  const state = {
    running: false, port: 41893, saveDir: "", autoAccept: false,
    requirePairing: false,
    active: {}, offers: {}, pairs: {}, history: [], trusted: [], queue: [],
  };
  let refs = {};
  let mounted = false;
  let logFn = null; /* 由 App.makeLog 创建，统一日志实现 */

  /* ---------------- 渲染 ---------------- */
  function renderStatus() {
    /* v5.4 修复：此前把元素数组整个传给 replaceChildren（期望展开），实际被
       String() 成 "[object HTMLSpanElement]" 文本节点显示在服务状态行 */
    refs.stateLine.replaceChildren(...(
      state.running
        ? [
            App.statusTag("接收就绪", "ok", "check"),
            App.h("span", { class: "hint" }, `本机接收端口 TCP ${state.port}`),
          ]
        : [
            App.statusTag("未就绪"),
            App.h("span", { class: "hint" }, "传输服务未启动，无法接收文件"),
          ]),
    );
    refs.saveDirText.textContent = state.saveDir || "（默认：系统下载目录）";
    refs.autoTgl.checked = state.autoAccept;
    refs.pairTgl.checked = state.requirePairing;
  }

  function renderPairs() {
    if (!refs.pairList) return;
    refs.pairList.innerHTML = "";
    const pairs = Object.values(state.pairs);
    if (!pairs.length) {
      refs.pairBox.style.display = "none";
      return;
    }
    refs.pairBox.style.display = "";
    for (const p of pairs) {
      const body = p.sas
        ? [
            App.h("div", { class: "li-title" }, `${p.peer} 请求与你配对`),
            App.h("div", { class: "li-sub" }, "请把下面的 6 位配对码告诉对方，并核对对方输入："),
            App.h("div", { style: { fontSize: "26px", letterSpacing: "6px", fontWeight: "700", color: "var(--accent)", margin: "6px 0", fontFamily: "Consolas, monospace" } }, p.sas),
          ]
        : [
            App.h("div", { class: "li-title" }, `正在与 ${p.peer} 配对…`),
            App.h("div", { class: "li-sub" }, "请在对方电脑屏幕上查看 6 位配对码"),
          ];
      refs.pairList.appendChild(
        App.h("div", { class: "list-item", style: { alignItems: "flex-start" } },
          App.h("span", { class: "tag accent", style: { flex: "none", marginTop: "3px" } }, "配对"),
          App.h("span", { class: "li-main" }, ...body),
          p.sas
            ? App.h("span", { class: "li-actions", style: { flex: "none", display: "flex", gap: "6px" } },
                App.h("button", {
                  class: "btn sm primary",
                  onclick: async () => { delete state.pairs[p.tid]; renderPairs(); await App.tryCall("xfer_pair_decide", p.tid, true); },
                }, "允许配对"),
                App.h("button", {
                  class: "btn sm danger",
                  onclick: async () => { delete state.pairs[p.tid]; renderPairs(); await App.tryCall("xfer_pair_decide", p.tid, false); },
                }, "拒绝"),
              )
            : App.h("span", { class: "li-actions", style: { flex: "none" } },
                App.h("button", {
                  class: "btn sm",
                  onclick: async () => {
                    delete state.pairs[p.tid]; renderPairs();
                    await App.tryCall("xfer_pair_submit", p.tid, "");
                  },
                }, "取消"),
              ),
        ),
      );
    }
  }

  function renderTrusted() {
    if (!refs.trustList) return;
    refs.trustList.innerHTML = "";
    App.state.trusted = {};
    if (!state.trusted.length) {
      refs.trustList.appendChild(App.h("div", { class: "empty" },
        "暂无已配对设备\n点设备卡片上的盾牌图标发起配对；配对后传输自动加密"));
      return;
    }
    for (const t of state.trusted) {
      App.state.trusted[t.id] = t;
      refs.trustList.appendChild(
        App.h("div", { class: "list-item" },
          App.h("span", { class: "tag ok", style: { flex: "none" } }, "已配对"),
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, t.name || t.id),
            App.h("div", { class: "li-sub mono" }, "公钥指纹：" + t.pk.slice(0, 16) + "…" + t.pk.slice(-8)),
          ),
          App.h("button", {
            class: "btn sm danger",
            onclick: async () => {
              if (!(await App.confirm("解除配对", `确定解除与「${t.name || t.id}」的配对？解除后需重新配对才能加密传输。`))) return;
              const r = await App.tryCall("xfer_untrust", t.id);
              if (r.ok) { App.toast("已解除配对", "ok"); refresh(); }
            },
          }, "解除"),
        ),
      );
    }
  }

  function renderQueue() {
    if (!refs.queueList) return; // 页面未挂载（事件先到）时跳过渲染
    refs.queueList.innerHTML = "";
    const items = state.queue.filter((q) => q.status !== "running");
    if (!items.length) {
      refs.queueBox.style.display = "none";
      return;
    }
    refs.queueBox.style.display = "";
    const failed = items.filter((q) => q.status === "failed");
    if (failed.length > 1) {
      refs.queueList.appendChild(
        App.h("div", { class: "list-item", style: { alignItems: "center" } },
          App.h("span", { class: "hint", style: { flex: 1 } },
            `${failed.length} 个任务发送失败（对方离线、目录不存在或连接中断）`),
          App.h("button", {
            class: "btn sm", style: { flex: "none" },
            onclick: async () => {
              for (const q of failed.slice()) {
                await App.tryCall("xfer_queue_retry", q.tid);
              }
              App.toast("已全部重新加入发送队列", "ok");
            },
          }, "全部重试"),
        ),
      );
    }
    for (const q of items) {
      refs.queueList.appendChild(
        App.h("div", { class: "list-item", style: { alignItems: "center" } },
          App.h("span", { class: "tag" + (q.status === "failed" ? " danger" : ""), style: { flex: "none" } },
            q.status === "failed" ? "失败" : "待发送"),
          q.status === "failed"
            ? App.h("span", { class: "li-main",
                style: { userSelect: "text", color: "var(--danger)" } }, `${q.peer} · ${q.err || "未知错误"}`)
            : App.h("span", { class: "li-main" },
                App.h("div", { class: "li-title" }, `${q.peer} · ${q.paths.length} 个路径`),
                App.h("div", { class: "li-sub" }, q.paths.length > 1
                  ? `${q.paths.length} 个文件/目录（${q.paths.slice(0, 2).join("、")}…）`
                  : q.paths[0])),
          q.status === "failed"
            ? App.h("button", {
                class: "btn sm", style: { flex: "none" },
                onclick: async () => {
                  const r = await App.tryCall("xfer_queue_retry", q.tid);
                  if (r.ok) App.toast("已重新加入发送队列", "ok");
                  else App.toast(r.err, "error");
                },
              }, "重试")
            : null,
          App.h("button", {
            class: "icon-btn", title: "移出队列",
            html: App.icon("x", 13),
            onclick: async () => { await App.tryCall("xfer_queue_remove", q.tid); },
          }),
        ),
      );
    }
  }

  function renderOffers() {
    if (!refs.offerList) return; // 页面未挂载（事件先到）时跳过渲染
    refs.offerList.innerHTML = "";
    const offers = Object.values(state.offers);
    if (!offers.length) {
      refs.offerBox.style.display = "none";
      return;
    }
    refs.offerBox.style.display = "";
    for (const o of offers) {
      refs.offerList.appendChild(
        App.h("div", { class: "list-item" },
          App.h("span", { class: "dot on" }),
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, `${o.peer} 想发送 ${o.files.length} 个文件给你`),
            App.h("div", { class: "li-sub mono" },
              `${App.fmtBytes(o.total)} · ${o.files.slice(0, 3).map((f) => f.path).join("、")}` +
              (o.files.length > 3 ? ` 等 ${o.files.length} 个` : "")),
          ),
          App.h("span", { class: "li-actions", style: { flex: "none" } },
            App.h("button", {
              class: "btn sm primary",
              onclick: async () => {
                delete state.offers[o.tid]; renderOffers();
                const r = await App.tryCall("xfer_respond", o.tid, true);
                if (!r.ok) App.toast(r.err || "接收失败", "error", 6000);
              },
            }, "接收"),
            App.h("button", {
              class: "btn sm danger",
              onclick: async () => {
                delete state.offers[o.tid]; renderOffers();
                const r = await App.tryCall("xfer_respond", o.tid, false);
                if (!r.ok) App.toast(r.err || "操作失败", "error", 6000);
                else App.toast("已拒绝对方发来的文件", "info");
              },
            }, "拒绝"),
          ),
        ),
      );
    }
  }

  function renderActive() {
    if (!refs.activeList) return; // 页面未挂载（事件先到）时跳过渲染
    refs.activeList.innerHTML = "";
    const items = Object.values(state.active);
    if (!items.length) {
      refs.activeList.appendChild(App.h("div", { class: "empty" }, "暂无进行中的传输\n把文件拖到右侧设备卡片，或点设备卡片上的发送按钮"));
      return;
    }
    for (const t of items) {
      const pct = t.total > 0 ? Math.min(100, Math.round((t.done / t.total) * 100)) : 0;
      const bar = App.h("div", { class: "progress" }, App.h("i", { style: { width: pct + "%" } }));
      refs.activeList.appendChild(
        App.h("div", { class: "list-item", style: { alignItems: "center" } },
          App.h("span", { class: "tag" + (t.dir === "send" ? "" : " accent"), style: { flex: "none" } },
            t.dir === "send" ? "发送" : "接收"),
          t.encrypted ? App.h("span", { class: "tag ok", style: { flex: "none" } }, "已加密") : null,
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, `${t.peer} · ${t.current || "…"}`),
            App.h("div", { class: "li-sub" },
              `${App.fmtBytes(t.done)} / ${App.fmtBytes(t.total)}（${pct}%）` +
              (t.speed > 0 ? ` · ${App.fmtBytes(t.speed)}/s` : "") + ` · ${t.status}`),
            bar,
          ),
          App.h("button", {
            class: "icon-btn", title: "取消该传输",
            html: App.icon("x", 13),
            onclick: async () => { await App.tryCall("xfer_cancel", t.tid); },
          }),
        ),
      );
    }
  }

  function renderHistory() {
    if (!refs.histList) return; // 页面未挂载（事件先到）时跳过渲染
    refs.histList.innerHTML = "";
    if (!state.history.length) {
      refs.histList.appendChild(App.h("div", { class: "empty" }, "暂无传输记录"));
      return;
    }
    for (const e of state.history) {
      // 部分文件失败：后端 ok=False 且 err=None（transfer.py），用 results.fail 还原明细
      const failList = (e.results && e.results.fail) || [];
      const title = e.ok
        ? `${e.peer} · ${e.files} 个文件 · ${App.fmtBytes(e.done)}`
        : (e.err || (failList.length
          ? `${e.peer} · ${failList.length}/${e.files} 个文件失败`
          : `${e.peer} · 失败：未知错误`));
      refs.histList.appendChild(
        App.h("div", { class: "list-item" },
          App.h("span", { class: "mono", style: { color: "var(--faint)", flex: "none" } }, App.fmtTime(e.ts)),
          App.h("span", { class: "tag" + (e.dir === "send" ? "" : " accent"), style: { flex: "none" } },
            e.dir === "send" ? "发送" : "接收"),
          e.encrypted ? App.h("span", { class: "tag ok", style: { flex: "none" } }, "加密") : null,
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, title),
            App.h("div", { class: "li-sub" },
              (e.dir === "send" ? "发送到 " : "接收自 ") + e.peer +
              (!e.ok && failList.length
                ? ` · 失败文件：${failList.slice(0, 2).map((f) => f.path).join("、")}` +
                  (failList.length > 2 ? ` 等 ${failList.length} 个` : "") : "")),
          ),
          App.h("span", {
            class: "tag " + (e.ok ? "ok" : "danger"), style: { flex: "none" },
          }, e.ok ? "完成" : (failList.length ? "部分失败" : "失败")),
        ),
      );
    }
  }

  function log(msg) {
    if (logFn) logFn(msg);
  }

  /* ---------------- 发送入口（供设备卡片调用） ---------------- */
  App.xferSendToDevice = async function (device, paths) {
    const name = device.alias || device.name;
    if (!device.file_port) {
      App.toast(`「${name}」未开启文件接收服务`, "error");
      return false;
    }
    if (!paths || !paths.length) {
      const r = await App.tryCall("xfer_pick_files");
      if (!r.ok || !r.data || !r.data.length) return false;
      paths = r.data;
    }
    const r = await App.tryCall("xfer_send", device.ip, device.file_port, paths, name);
    if (!r.ok) { App.toast(r.err, "error", 5000); return false; }
    App.toast(`开始向「${name}」发送 ${r.data.files} 个文件（${App.fmtBytes(r.data.total)}）`, "ok", 5000);
    return true;
  };

  /* 设备卡片「配对」按钮入口 */
  App.xferPairToDevice = async function (device) {
    const name = device.alias || device.name;
    if (!device.file_port) {
      App.toast(`「${name}」未开启文件传输服务，无法配对`, "error");
      return false;
    }
    if (App.state.trusted && App.state.trusted[device.id]) {
      App.toast(`已与「${name}」配对，传输自动加密`, "ok");
      return true;
    }
    const r = await App.tryCall("xfer_pair", device.ip, device.file_port, name);
    if (!r.ok) { App.toast(r.err, "error", 5000); return false; }
    App.toast(`配对请求已发送给「${name}」，请在本机输入对方屏幕上的配对码`, "info", 6000);
    return true;
  };

  /* ---------------- 配对事件（页面未挂载时也收集） ---------------- */
  App.on("xfer_pair_show", (p) => {
    state.pairs[p.tid] = { tid: p.tid, peer: p.peer, sas: p.sas };
    renderPairs();
    App.toast(`「${p.peer}」请求配对：配对码 ${p.sas}，请在「文件传输」页确认`, "warn", 10000);
  });
  App.on("xfer_pair_input", async (p) => {
    state.pairs[p.tid] = { tid: p.tid, peer: p.peer, sas: null };
    renderPairs();
    const pin = await App.modal({
      title: "配对验证",
      body: App.h("div", null,
        App.h("p", { style: { margin: "0 0 8px" } }, `请查看「${p.peer}」电脑屏幕上显示的 6 位配对码，并输入到下方：`),
        App.h("p", { class: "hint" }, "两边配对码一致才能完成配对，可防止中间人窃听。")),
      input: "",
      okText: "确认配对",
    });
    delete state.pairs[p.tid];
    renderPairs();
    if (pin === null) { await App.tryCall("xfer_pair_submit", p.tid, ""); return; }
    const r = await App.tryCall("xfer_pair_submit", p.tid, pin.trim());
    if (!r.ok) App.toast(r.err, "error");
  });
  App.on("xfer_pair_done", (p) => {
    delete state.pairs[p.tid];
    renderPairs();
    if (p.ok) {
      App.toast(`与「${p.peer}」配对成功，后续传输将自动加密`, "ok", 6000);
    } else {
      App.toast(`配对失败：${p.err || "未知原因"}`, "error", 6000);
    }
    refresh();
  });

  /* ---------------- 事件（页面未挂载时也收集） ---------------- */
  App.on("xfer_offer", (o) => {
    state.offers[o.tid] = o;
    renderOffers();
    App.toast(`${o.peer} 想发送 ${o.files.length} 个文件（${App.fmtBytes(o.total)}），请在「文件传输」页确认`, "warn", 8000);
  });
  App.on("xfer_progress", (p) => {
    state.active[p.tid] = p;
    renderActive();
  });
  App.on("xfer_done", (e) => {
    delete state.active[e.tid];
    state.history.unshift(e);
    state.history = state.history.slice(0, 50);
    renderActive();
    renderHistory();
    if (e.ok) {
      App.toast(`${e.dir === "send" ? "发送" : "接收"}完成：${e.peer} · ${e.files} 个文件`, "ok", 5000);
    } else {
      App.toast(`${e.dir === "send" ? "发送" : "接收"}失败：${e.err || "已取消"}`, "error", 6000);
    }
  });
  App.on("xfer_log", (m) => log(m));
  App.on("xfer_queue", (q) => {
    state.queue = q || [];
    renderQueue();
  });

  /* ---------------- 页面注册 ---------------- */
  App.registerPage({
    id: "transfer",
    title: "文件传输",
    icon: "send",
    group: "互联与协作",

    async mount(el) {
      mounted = true;
      refs = {};
      refs.autoTgl = App.h("input", { type: "checkbox", onchange: async (e) => {
        const r = await App.tryCall("xfer_set_auto_accept", e.target.checked);
        if (r.ok) { state.autoAccept = e.target.checked; App.toast(state.autoAccept ? "已开启自动接收" : "已关闭自动接收", "ok"); }
      } });
      refs.pairTgl = App.h("input", { type: "checkbox", onchange: async (e) => {
        const r = await App.tryCall("cfg_set", "require_pairing", e.target.checked);
        if (r.ok) { state.requirePairing = e.target.checked; App.toast(state.requirePairing ? "已开启强制加密：未配对设备需先配对" : "已关闭强制加密（允许明文接收确认）", "ok", 5000); }
      } });
      refs.stateLine = App.h("div", { class: "row" });
      refs.saveDirText = App.h("span", { class: "hint", style: { userSelect: "text" } });
      refs.queueBox = App.h("div", { class: "card", style: { display: "none" } },
        App.h("div", { class: "card-title" }, "发送队列（持久化，重启续接）"),
        (refs.queueList = App.h("div", { class: "list" })));
      refs.pairBox = App.h("div", { class: "card", style: { display: "none" } },
        App.h("div", { class: "card-title" }, "配对请求"),
        (refs.pairList = App.h("div", { class: "list" })));
      refs.offerBox = App.h("div", { class: "card", style: { display: "none" } },
        App.h("div", { class: "card-title" }, "待确认的传输请求"),
        (refs.offerList = App.h("div", { class: "list" })));
      refs.activeList = App.h("div", { class: "list" });
      refs.histList = App.h("div", { class: "list" });
      refs.trustList = App.h("div", { class: "list" });
      refs.log = App.h("div", { class: "log-box", style: { maxHeight: "110px" } }, "传输日志…");
      logFn = App.makeLog(refs.log);

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "文件传输"),
        App.h("div", { class: "sub" }, "局域网设备间点对点直传：分块 + 断点续传 + SHA-256 校验"),
      ));

      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "服务状态"),
        App.h("div", { class: "row" },
          refs.fwBtn = App.h("button", {
            class: "btn",
            onclick: async (ev) => {
              const btn = ev.currentTarget;
              btn.disabled = true;   // 走 UAC 提权，防连点重复触发
              try {
                const r = await App.tryCall("xfer_firewall");
                if (r.ok) App.toast("已放行防火墙端口", "ok");
                else App.toast(r.err || "放行失败", "error", 6000);
              } finally {
                btn.disabled = false;
              }
            },
          }, "防火墙放行"),
          App.h("span", { class: "hint" }, "收不到文件时先放行防火墙（TCP " + state.port + "）"),
        ),
        refs.stateLine,
        App.h("div", { class: "row" },
          App.h("span", { class: "field-label" }, "接收目录："), refs.saveDirText,
          App.h("button", {
            class: "btn sm",
            onclick: async () => {
              const r = await App.tryCall("xfer_set_save_dir");
              if (r.ok) { state.saveDir = r.data; renderStatus(); App.toast("接收目录已保存", "ok"); }
              else if (r.err !== "未选择目录") App.toast(r.err, "error");
            },
          }, "更改…"),
        ),
        App.h("div", { class: "row" },
          App.h("label", { class: "switch" }, refs.autoTgl, App.h("span", { class: "track" }),
            "自动接收（不再逐次确认，仅建议可信内网开启）"),
        ),
        App.h("div", { class: "row" },
          App.h("label", { class: "switch" }, refs.pairTgl, App.h("span", { class: "track" }),
            "强制加密（未配对设备必须先配对，拒绝明文传输）"),
        ),
      ));

      /* v5.2 P4：页内标签「传输 | 历史与信任」；手动发送收进折叠区 */
      const paneActive = App.h("div", { style: { display: "flex", flexDirection: "column", gap: "12px" } },
        refs.pairBox,       // 配对请求（事件弹出）
        refs.offerBox,      // 待确认传输（事件弹出）
        refs.queueBox,      // 发送队列（事件弹出）
        App.h("div", { class: "card" },
          App.h("div", { class: "card-title" }, "进行中的传输"),
          refs.activeList,
        ),
        App.sec("手动发送（IP 直连，跨网段 / 多网卡场景）", [
          App.h("p", { class: "hint", style: { margin: "0 0 8px" } },
            "对方设备不在发现列表时可手动输入 IP 直连发送。"),
          App.h("div", { class: "row" },
            App.h("span", { class: "field-label" }, "目标 IP："),
            (refs.ipInput = App.h("input", { class: "input mono", placeholder: "192.168.1.23", style: { width: "150px" } })),
            App.h("span", { class: "field-label" }, "端口："),
            (refs.portInput = App.h("input", { class: "input mono", value: "41893", style: { width: "90px" } })),
          ),
          App.h("div", { class: "row", style: { marginTop: "8px" } },
            App.h("button", {
              class: "btn",
              onclick: async () => {
                const ip = (refs.ipInput.value || "").trim();
                const port = parseInt(refs.portInput.value, 10);
                // IP 逐段校验：每段 0-255 且拒绝前导零
                const segs = ip.split(".");
                const ipOk = segs.length === 4 && segs.every((s) => {
                  if (!/^\d{1,3}$/.test(s)) return false;
                  if (s.length > 1 && s[0] === "0") return false;
                  const n = parseInt(s, 10);
                  return n >= 0 && n <= 255;
                });
                if (!ipOk) {
                  App.toast("请输入合法的 IPv4 地址", "error"); return;
                }
                if (!port || port < 1 || port > 65535) {
                  App.toast("端口需在 1-65535 之间", "error"); return;
                }
                const r = await App.tryCall("xfer_pick_files");
                if (!r.ok || !r.data || !r.data.length) return;
                const s = await App.tryCall("xfer_send", ip, port, r.data, ip);
                if (!s.ok) { App.toast(s.err, "error", 5000); return; }
                App.toast(`开始向 ${ip} 发送 ${s.data.files} 个文件（${App.fmtBytes(s.data.total)}）`, "ok", 5000);
              },
            }, "选择文件并发送"),
            App.h("button", {
              class: "btn",
              onclick: async () => {
                const ip = (refs.ipInput.value || "").trim();
                const port = parseInt(refs.portInput.value, 10);
                const segs = ip.split(".");
                const ipOk = segs.length === 4 && segs.every((s) => {
                  if (!/^\d{1,3}$/.test(s)) return false;
                  if (s.length > 1 && s[0] === "0") return false;
                  const n = parseInt(s, 10);
                  return n >= 0 && n <= 255;
                });
                if (!ipOk) {
                  App.toast("请输入合法的 IPv4 地址", "error"); return;
                }
                if (!port || port < 1 || port > 65535) {
                  App.toast("端口需在 1-65535 之间", "error"); return;
                }
                const r = await App.tryCall("xfer_pick_files");
                if (!r.ok || !r.data || !r.data.length) return;
                const s = await App.tryCall("xfer_enqueue", ip, port, r.data, ip);
                if (!s.ok) { App.toast(s.err, "error", 5000); return; }
                App.toast(`已加入发送队列，将发送到 ${ip}`, "ok", 5000);
              },
            }, "选择文件并加入队列"),
          ),
        ], { open: false }),
        refs.log,
      );
      const paneTrust = App.h("div", { style: { display: "flex", flexDirection: "column", gap: "12px" } },
        App.h("div", { class: "card" },
          App.h("div", { class: "card-title" }, "已配对设备（传输自动加密）"),
          refs.trustList,
        ),
        App.h("div", { class: "card" },
          App.h("div", { class: "card-title" }, "传输历史（最近 50 条）"),
          refs.histList,
        ),
      );
      const tnav = App.subnav([
        { label: "传输", el: paneActive },
        { label: "历史与信任", el: paneTrust },
      ]);
      el.appendChild(tnav);
      tnav.panes.forEach((p) => el.appendChild(p));

      await refresh();
    },

    show() { refresh(); },
  });

  async function refresh() {
    if (!mounted) return;
    const r = await App.tryCall("xfer_get_state");
    if (!r.ok) return;
    const s = r.data;
    Object.assign(state, {
      running: s.running, port: s.port, saveDir: s.save_dir, autoAccept: s.auto_accept,
      requirePairing: !!s.require_pairing,
      history: s.history || [], trusted: s.trusted || [], queue: s.queue || [],
    });
    for (const t of s.active || []) state.active[t.tid] = t;
    renderStatus();
    renderPairs();
    renderTrusted();
    renderOffers();
    renderQueue();
    renderActive();
    renderHistory();
  }
})();
