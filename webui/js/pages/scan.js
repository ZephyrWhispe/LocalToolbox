/* 扫描与映射页：局域网共享扫描（实时结果）、凭据连接、映射/断开网络驱动器。 */
(function () {
  "use strict";

  const state = {
    scanning: false,
    results: [], // [{host, ip, shares, error, emptyKind}]
  };
  let refs = {};

  /* ---------------- 凭据对话框（用户名 + 密码，复用 App.modal 多输入） ---------------- */
  function askCredentials(title, body, okText) {
    return App.modal({
      title,
      body,
      inputs: [
        { label: "用户名", placeholder: "例如：administrator" },
        { label: "密码", type: "password", placeholder: "密码" },
      ],
      okText,
    });
  }

  /* ---------------- 渲染 ---------------- */
  function setStatus(text, busy) {
    refs.status.textContent = text;
    refs.spin.style.display = busy ? "" : "none";
  }

  function renderResults() {
    refs.tree.innerHTML = "";
    if (!state.results.length) {
      refs.tree.appendChild(App.h("div", { class: "empty" },
        state.scanning ? "正在扫描，发现的主机会实时出现在这里…" : "尚未扫描\n点击「扫描局域网共享」开始"));
      return;
    }
    for (const h of state.results) {
      // IP 形式的主机不能按 "." 拆分（会把 192.168.1.x 拆成 "192" 导致映射失败）
      const isIp = /^\d{1,3}(\.\d{1,3}){3}$/.test(String(h.host || ""));
      const hostClean = isIp ? h.host : String(h.host || "").split(".")[0];
      refs.tree.appendChild(App.h("div", { class: "list-item" },
        App.h("span", { class: "dot accent" }),
        App.h("span", { class: "li-main" },
          App.h("div", { class: "li-title" }, h.host || ""),
          h.ip ? App.h("div", { class: "li-sub mono" }, h.ip) : null,
        ),
        h.error === "denied" ? App.h("button", {
          class: "btn sm", onclick: () => connectHost(h),
        }, "输入凭据连接") : null,
      ));
      if (h.shares && h.shares.length) {
        for (const share of h.shares) {
          refs.tree.appendChild(App.h("div", {
            class: "list-item click",
            title: "双击填入映射路径",
            style: { paddingLeft: "30px" },
            ondblclick: () => { refs.path.value = "\\\\" + hostClean + "\\" + share; },
          },
            App.h("span", { style: { color: "var(--faint)", flex: "none" }, html: App.icon("folder", 14) }),
            App.h("span", { class: "li-main" }, App.h("span", { class: "li-title mono" }, share)),
          ));
        }
      } else {
        let text;
        if (h.error === "denied") text = "（需要凭据，双击本主机输入用户名密码）";
        else if (h.error === "empty" || h.emptyKind === "empty") text = "（该主机无共享文件夹）";
        else if (h.emptyKind) text = "（共享枚举失败，主机可能拒绝访问）";
        else text = "（无法访问该主机）";
        const denied = h.error === "denied";
        refs.tree.appendChild(App.h("div", {
          class: "list-item" + (denied ? " click" : ""),
          title: denied ? "点击输入用户名密码" : null,
          style: { paddingLeft: "30px", color: "var(--muted)" },
          ...(denied ? { onclick: () => connectHost(h) } : {}),
        }, text));
      }
    }
  }

  /* ---------------- 扫描 ---------------- */
  async function startScan() {
    const r = await App.tryCall("scan_start");
    if (!r.ok) { App.toast(r.err, "warn"); return; }
    state.results = [];
    state.scanning = true;
    refs.scanBtn.disabled = true;
    renderResults();
    setStatus("正在扫描...", true);
  }

  /* ---------------- 凭据连接主机 ---------------- */
  async function connectHost(h) {
    const ip = String(h.ip || "").trim() || h.host;
    const cred = await askCredentials(
      "输入网络凭据",
      "连接主机：" + h.host + "（" + ip + "）\n请输入该主机上可用的 Windows 用户名和密码：",
      "连接",
    );
    if (!cred) return;
    const [user, pwd] = cred;
    if (!user.trim()) { App.toast("请输入用户名。", "warn"); return; }
    setStatus("正在连接 " + h.host + " ...", true);
    const r = await App.tryCall("scan_connect", h.host, ip, user.trim(), pwd);
    if (!r.ok) {
      App.toast(r.err, "error", 6000);
      setStatus("连接失败，请检查用户名密码或网络设置。");
      return;
    }
    h.shares = r.data.shares;
    h.emptyKind = r.data.error; // 连接成功后的临时状态：empty / 其他
    h.error = null;
    renderResults();
    setStatus("已连接，共享列表已更新。双击共享可填入映射路径。");
  }

  /* ---------------- 盘符与已映射驱动器 ---------------- */
  async function refreshLetters() {
    const r = await App.tryCall("scan_letters");
    const letters = r.ok ? r.data : [];
    refs.letter.innerHTML = "";
    if (!letters.length) {
      refs.letter.appendChild(App.h("option", { value: "" }, "（无可用盘符）"));
    } else {
      for (const c of letters) refs.letter.appendChild(App.h("option", { value: c }, c + ":"));
    }
  }

  async function refreshMapped() {
    const r = await App.tryCall("scan_mapped");
    const drives = r.ok ? r.data : [];
    refs.mapped.innerHTML = "";
    if (!drives.length) {
      refs.mapped.appendChild(App.h("div", { class: "empty" }, "暂无已映射的网络驱动器"));
      return;
    }
    for (const d of drives) {
      refs.mapped.appendChild(App.h("div", { class: "list-item" },
        App.h("span", { class: "tag accent mono" }, d.letter + ":"),
        App.h("span", { class: "li-main" }, App.h("span", { class: "li-title mono" }, d.remote)),
        App.h("button", {
          class: "btn sm danger",
          onclick: async () => {
            const r2 = await App.tryCall("scan_unmap", d.letter);
            App.toast(r2.ok ? r2.data : r2.err, r2.ok ? "ok" : "error", 6000);
            refreshLetters();
            refreshMapped();
          },
        }, "断开"),
      ));
    }
  }

  /* ---------------- 映射 ---------------- */
  let mapping = false;   // 映射可能弹凭据框，防连点
  async function mapDrive() {
    if (mapping) return;
    const path = refs.path.value.trim();
    if (!path) { App.toast("请先输入共享路径，或选择一个扫描到的共享。", "warn"); return; }
    const letter = refs.letter.value;
    if (!letter) { App.toast("没有可用盘符。", "warn"); return; }
    mapping = true;
    refs.mapBtn.disabled = true;
    try {
      let r = await App.tryCall("scan_map", path, letter);
      if (!r.ok) {
        const firstErr = r.err;
        const cred = await askCredentials(
          "映射需要凭据",
          "映射 " + path + " 失败：\n" + firstErr + "\n\n请输入该共享的访问凭据：",
          "映射",
        );
        if (!cred) {
          App.toast(firstErr, "error", 6000);
        } else {
          const [user, pwd] = cred;
          if (!user.trim()) {
            App.toast("请输入用户名。", "warn");
          } else {
            r = await App.tryCall("scan_map", path, letter, user.trim(), pwd);
            App.toast(r.ok ? r.data : r.err, r.ok ? "ok" : "error", 6000);
          }
        }
      } else {
        App.toast(r.data, "ok", 5000);
      }
    } finally {
      mapping = false;
      refs.mapBtn.disabled = false;
    }
    refreshLetters();
    refreshMapped();
  }

  /* ---------------- 网卡信息 ---------------- */
  async function refreshAdapters() {
    refs.adapters.innerHTML = "";
    const r = await App.tryCall("scan_adapters");
    if (!r.ok) {
      refs.adapters.appendChild(App.h("div", { class: "empty" }, r.err || "读取网卡信息失败"));
      return;
    }
    const list = r.data || [];
    if (!list.length) {
      refs.adapters.appendChild(App.h("div", { class: "empty" }, "未检测到网卡"));
      return;
    }
    for (const a of list) {
      const up = String(a.status || "").toLowerCase() === "up";
      refs.adapters.appendChild(App.h("div", { class: "list-item" },
        App.h("span", { class: "dot" + (up ? " on" : "") }),
        App.h("span", { class: "li-main" },
          App.h("div", { class: "li-title" }, a.name || a.description || "(未命名网卡)"),
          App.h("div", { class: "li-sub mono" },
            (a.ip ? a.ip + (a.prefix ? "/" + a.prefix : "") : "无 IPv4") +
            (a.gateway ? " · 网关 " + a.gateway : "") +
            (a.dns ? " · DNS " + a.dns : "") +
            (a.mac ? " · " + a.mac : ""))),
        App.h("span", { class: "tag " + (up ? "ok" : "") }, a.status || "未知"),
      ));
    }
  }

  /* ---------------- 端口扫描 ---------------- */
  async function scanPorts() {
    const host = refs.portHost.value.trim();
    if (!host) { App.toast("请填写目标主机或 IP。", "warn"); return; }
    refs.portBtn.disabled = true;
    refs.portResult.innerHTML = "";
    refs.portResult.appendChild(App.h("div", { class: "empty" }, "正在扫描端口…"));
    const r = await App.tryCall("scan_ports", host, refs.portSpec.value.trim());
    refs.portBtn.disabled = false;
    refs.portResult.innerHTML = "";
    if (!r.ok) {
      refs.portResult.appendChild(App.h("div", { class: "empty" }, r.err || "扫描失败"));
      return;
    }
    const ports = r.data || [];
    if (!ports.length) {
      refs.portResult.appendChild(App.h("div", { class: "empty" }, "未发现开放端口"));
      return;
    }
    refs.portResult.appendChild(App.h("div", { class: "row", style: { flexWrap: "wrap", gap: "6px" } },
      ...ports.map((p) => App.h("span", { class: "tag ok mono" }, String(p)))));
  }

  /* ---------------- 页面注册 ---------------- */
  App.registerPage({
    id: "scan",
    title: "扫描与映射",
    icon: "radar",
    group: "共享服务",

    async mount(el) {
      refs = {};
      refs.status = App.h("span", { class: "hint" }, "尚未扫描");
      refs.spin = App.h("span", { class: "spin", style: { display: "none" } });
      refs.scanBtn = App.h("button", {
        class: "btn primary",
        onclick: startScan,
        html: App.icon("search", 14) + "<span>扫描局域网共享</span>",
      });
      refs.tree = App.h("div", { class: "list", style: { maxHeight: "300px", overflowY: "auto" } });
      refs.path = App.h("input", {
        class: "input grow",
        placeholder: "例如：\\\\电脑名\\共享名 或选中下方共享后自动填写",
      });
      refs.letter = App.h("select", { class: "input", style: { width: "90px" } });
      refs.mapBtn = App.h("button", { class: "btn primary", onclick: mapDrive }, "映射");
      refs.mapped = App.h("div", { class: "list", style: { maxHeight: "220px", overflowY: "auto" } });
      refs.portHost = App.h("input", {
        class: "input grow", placeholder: "目标主机或 IP，例如 192.168.1.10",
      });
      refs.portSpec = App.h("input", {
        class: "input mono", style: { width: "230px" },
        placeholder: "端口，如 80,443,8000-8010（留空=常见端口）",
      });
      refs.portBtn = App.h("button", { class: "btn primary", onclick: scanPorts }, "扫描端口");
      refs.portResult = App.h("div", { class: "list", style: { minHeight: "40px" } });
      refs.adapters = App.h("div", { class: "list", style: { maxHeight: "260px", overflowY: "auto" } });

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "扫描与映射"),
        App.h("div", { class: "sub" }, "扫描局域网内的 SMB 共享，并把共享文件夹映射为本机网络驱动器"),
      ));

      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "局域网扫描"),
        App.h("div", { class: "row" }, refs.scanBtn, App.h("span", { class: "grow" }), refs.spin, refs.status),
        refs.tree,
      ));

      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "端口扫描"),
        App.h("div", { class: "row" }, refs.portHost, refs.portSpec, refs.portBtn),
        App.h("div", { class: "hint" }, "探测目标主机开放的 TCP 端口；支持逗号分隔与区间（如 80,443,8000-8010），留空则探测常见端口。"),
        refs.portResult,
      ));

      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "网卡信息"),
        App.h("div", { class: "row" },
          App.h("span", { class: "hint" }, "本机网络适配器：IPv4 / 网关 / DNS / MAC"),
          App.h("span", { class: "grow" }),
          App.h("button", {
            class: "btn sm",
            html: App.icon("refresh", 12) + "<span>刷新</span>",
            onclick: refreshAdapters,
          }),
        ),
        refs.adapters,
      ));

      el.appendChild(App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "映射为网络驱动器"),
        App.h("div", { class: "row" },
          refs.path,
          App.h("span", { class: "hint" }, "盘符"),
          refs.letter,
          refs.mapBtn,
        ),
        App.h("div", { class: "hint" }, "提示：在扫描结果中双击一个共享，可自动填入上方路径。"),
        App.h("div", { class: "sep" }),
        App.h("div", { class: "row" },
          App.h("span", { class: "hint" }, "已映射的网络驱动器："),
          App.h("span", { class: "grow" }),
          App.h("button", {
            class: "btn sm",
            html: App.icon("refresh", 12) + "<span>刷新</span>",
            onclick: () => { refreshLetters(); refreshMapped(); },
          }),
        ),
        refs.mapped,
      ));

      /* 事件订阅 */
      App.on("scan_progress", (p) => {
        const msg = p.total > 0 ? p.message + "（" + p.current + "/" + p.total + "）" : p.message;
        setStatus(msg, true);
      });
      App.on("scan_host", (h) => {
        state.results.push(h);
        renderResults();
      });
      App.on("scan_done", (d) => {
        state.scanning = false;
        refs.scanBtn.disabled = false;
        setStatus(d.message);
        refreshLetters();
      });

      await refreshLetters();
      await refreshMapped();
      await refreshAdapters();
    },

    show() { refreshLetters(); refreshMapped(); refreshAdapters(); },
  });
})();
