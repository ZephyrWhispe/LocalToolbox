/* 设置页：主题 / 剪贴板偏好 / 本机信息 / 关于。 */
(function () {
  "use strict";

  let refs = {};

  async function save(key, value) {
    const r = await App.tryCall("cfg_set", key, value);
    if (!r.ok) { App.toast(r.err, "error"); return false; }
    if (App.state.cfg) App.state.cfg[key] = r.data;
    return true;
  }

  async function refresh() {
    let info = null;
    try { info = await App.guardedCall("app_info"); }
    catch (e) { info = null; }   // guardedCall 失败已 toast；避免中断 mount/show
    refs.selfBox.innerHTML = "";
    if (!info) {
      refs.selfBox.appendChild(App.h("div", { class: "empty" }, "本机信息读取失败，请重新打开页面"));
      return;
    }
    refs.selfBox.appendChild(
      App.h("div", { class: "list-item" },
        App.h("span", { class: "dot accent" }),
        App.h("span", { class: "li-main" },
          App.h("div", { class: "li-title" }, `${info.device_name}（本机）`),
          App.h("div", { class: "li-sub mono" }, `设备 ID：${info.device_id}`),
          App.h("div", { class: "li-sub mono" }, `局域网 IP：${info.ips.join("  ·  ") || "未获取到"}`),
        ),
      ),
    );
  }

  /* ---------------- 自定义上传目标管理（v5.4 O2，ShareX Custom Uploader 子集） ---------------- */
  function parseKVText(text, sep) {
    /* 每行一条 Key<sep>Value；解析失败行忽略，空行跳过 */
    const out = {};
    String(text || "").split("\n").forEach((line) => {
      const s = line.trim();
      if (!s) return;
      const i = s.indexOf(sep);
      if (i <= 0) return;
      out[s.slice(0, i).trim()] = s.slice(i + sep.length).trim();
    });
    return out;
  }

  function showUploadTargetsModal(onChanged) {
    const listBox = App.h("div", { class: "list", style: { maxHeight: "180px", overflowY: "auto" } });
    function renderList() {
      listBox.innerHTML = "";
      const targets = ((App.state.cfg || {}).upload_targets) || [];
      if (!targets.length) {
        listBox.appendChild(App.h("div", { class: "empty" },
          "还没有上传目标\n点击下方表单新增，截图上传时即可选择"));
        return;
      }
      targets.forEach((t, i) => {
        listBox.appendChild(App.h("div", { class: "list-item" },
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, t.name),
            App.h("div", { class: "li-sub mono" }, t.method + " " + t.url)),
          App.h("button", {
            class: "btn xs danger", onclick: async function () {
              if (this._busy) return;
              if (!(await App.confirm("删除上传目标",
                "将删除目标「" + t.name + "」（不影响已上传的历史链接）。确定？"))) return;
              this._busy = true; this.disabled = true; this.textContent = "删除中…";
              try {
                const r = await App.tryCall("upload_targets_del", i);
                if (!r.ok) { App.toast(r.err, "error"); return; }
                App.state.cfg.upload_targets = r.data.targets || [];
                App.toast("已删除「" + r.data.removed + "」", "ok");
                renderList();
                if (onChanged) onChanged();
              } finally { this._busy = false; this.disabled = false; this.textContent = "删除"; }
            },
          }, "删除"),
        ));
      });
    }
    renderList();

    /* 新增表单（枚举一律下拉，禁止手输） */
    const inName = App.h("input", { class: "input", placeholder: "如：公司图床" });
    const inUrl = App.h("input", { class: "input", placeholder: "https://example.com/api/upload" });
    const selMethod = App.h("select", { class: "input" },
      App.h("option", { value: "POST" }, "POST"),
      App.h("option", { value: "PUT" }, "PUT"),
      App.h("option", { value: "GET" }, "GET"),
    );
    const selBody = App.h("select", { class: "input" },
      App.h("option", { value: "multipart" }, "multipart（表单上传，最常见）"),
      App.h("option", { value: "json" }, "json（{file} 替换为 base64）"),
      App.h("option", { value: "raw" }, "raw（原始图片字节）"),
    );
    const inField = App.h("input", { class: "input", value: "file", placeholder: "file" });
    const inHeaders = App.h("textarea", {
      class: "input", rows: "2",
      placeholder: "每行一条，Key: Value\n如 Authorization: Bearer xxx",
    });
    const inArgs = App.h("textarea", {
      class: "input", rows: "2",
      placeholder: "每行一条，key=value（multipart/json 生效）\n可用变量 {file}（文件路径或base64）、{filename}",
    });
    const inPath = App.h("input", { class: "input", placeholder: "如 data.url（留空则自动尝试常见路径）" });
    const inRegex = App.h("input", { class: "input", placeholder: "如 \"url\":\"(.+?)\"（可选）" });

    const addBtn = App.h("button", {
      class: "btn primary", onclick: async function () {
        if (this._busy) return;
        this._busy = true; this.disabled = true; this.textContent = "保存中…";
        try {
          const r = await App.tryCall("upload_targets_add", {
            name: inName.value, url: inUrl.value, method: selMethod.value,
            body: selBody.value, headers: parseKVText(inHeaders.value, ":"),
            file_field: inField.value, arguments: parseKVText(inArgs.value, "="),
            url_path: inPath.value, url_regex: inRegex.value,
          });
          if (!r.ok) { App.toast(r.err, "error"); return; }
          App.state.cfg.upload_targets = r.data || [];
          App.toast("已添加上传目标", "ok");
          inName.value = ""; inUrl.value = ""; inHeaders.value = ""; inArgs.value = "";
          inPath.value = ""; inRegex.value = "";
          renderList();
          if (onChanged) onChanged();
        } finally { this._busy = false; this.disabled = false; this.textContent = "添加目标"; }
      },
    }, "添加目标");

    App.modal({
      title: "自定义上传目标",
      okText: "关闭",
      body: App.h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } },
        App.h("div", { class: "card-title", style: { fontSize: "13px", margin: "0" } }, "已有目标"),
        listBox,
        App.h("div", { class: "card-title", style: { fontSize: "13px", margin: "4px 0 0" } }, "新增目标"),
        App.h("div", { class: "row", style: { margin: "0", flexWrap: "nowrap" } }, inName, inUrl),
        App.h("div", { class: "row", style: { margin: "0", flexWrap: "nowrap" } }, selMethod, selBody, inField),
        inHeaders, inArgs,
        App.h("div", { class: "row", style: { margin: "0", flexWrap: "nowrap" } }, inPath, inRegex),
        App.h("div", { class: "row", style: { margin: "0", justifyContent: "flex-end" } }, addBtn),
        App.h("div", { class: "hint" },
          "响应链接提取：优先按「URL 提取路径」从 JSON 取值，其次常见路径兜底，再次正则第一个捕获组，最后取纯文本。"),
      ),
    });
  }

  /* ---------------- 快捷键录制控件 ---------------- */
  let armedEditor = null;   // 同一时刻只允许一个编辑器在录制

  function comboKeyOf(e) {
    const k = e.key;
    if (/^[a-zA-Z0-9]$/.test(k)) return k.toUpperCase();
    if (/^F([1-9]|1\d|2[0-4])$/i.test(k)) return k.toUpperCase();
    return null;
  }

  function comboModsOf(e) {
    const out = [];
    if (e.ctrlKey) out.push("Ctrl");
    if (e.altKey) out.push("Alt");
    if (e.shiftKey) out.push("Shift");
    if (e.metaKey) out.push("Win");
    return out;
  }

  /** 热键编辑器：按钮显示当前组合键，点击后按下新组合（ESC 取消）。 */
  function hotkeyEditor(slot, init) {
    const btn = App.h("button", {
      class: "btn sm mono",
      style: { minWidth: "168px", justifyContent: "flex-start", fontFamily: "Consolas, monospace" },
      title: "点击后按下新的快捷键组合（ESC 取消）",
    });
    const ed = {
      btn: btn,
      combo: String(init || "").trim(),
      armed: false,
      paint() {
        btn.classList.toggle("primary", ed.armed);
        btn.textContent = ed.armed ? "请按下组合键…（ESC 取消）" : (ed.combo || "（未设置）");
      },
      disarm() {
        ed.armed = false;
        if (armedEditor === ed) armedEditor = null;
        ed.paint();
      },
      arm() {
        if (armedEditor && armedEditor !== ed) armedEditor.disarm();
        armedEditor = ed;
        ed.armed = true;
        ed.paint();
      },
    };
    btn.addEventListener("click", () => (ed.armed ? ed.disarm() : ed.arm()));

    window.addEventListener("keydown", async (e) => {
      if (!ed.armed) return;
      /* v5.3：若已离开设置页（捕获态未解除），立即解除并放行按键 —— 此前该监听
         会持续 preventDefault，用户切页后整个键盘被静默吞掉（按什么都没反应） */
      if (!App.isPageActive("settings")) { ed.disarm(); return; }
      e.preventDefault();
      if (e.key === "Escape") { ed.disarm(); return; }
      if (["Control", "Alt", "Shift", "Meta", "Win", "OS", "CapsLock"].includes(e.key)) return;
      const keyName = comboKeyOf(e);
      if (!keyName) { App.toast("仅支持字母 / 数字 / F1-F24 作为主键", "warn"); return; }
      const mods = comboModsOf(e);
      if (!mods.length && !/^F\d+$/.test(keyName)) {
        App.toast("字母 / 数字键需搭配 Ctrl / Alt / Shift / Win", "warn");
        return;
      }
      const combo = [...mods, keyName].join("+");
      const prev = ed.combo;
      ed.combo = combo;
      ed.disarm();
      const r = await App.tryCall("hotkey_set", slot, combo);
      if (r.ok) {
        ed.combo = r.data.combo;
        if (App.state.cfg) {
          const map = { clip: "hotkey_clip", shot: "hotkey_shot",
                        full: "hotkey_full", pop: "hotkey_pop",
                        memo: "hotkey_memo", ocr: "hotkey_ocr" };
          App.state.cfg[map[slot] || "hotkey_clip"] = ed.combo;
        }
        if (r.data.stolen) {
          App.toast(`${ed.combo} 被系统占用，已钩子接管：本应用运行期间由本应用响应，退出后系统恢复`, "ok", 7000);
        } else {
          App.toast(`已设置：${ed.combo}`, "ok");
        }
      } else {
        ed.combo = prev;   // 注册失败恢复旧组合
        App.toast(r.err, "error", 6000);
      }
      ed.paint();
    });
    ed.paint();
    return ed;
  }

  App.registerPage({
    id: "settings",
    title: "设置",
    icon: "settings",
    group: "系统",

    async mount(el) {
      refs = {};
      refs.selfBox = App.h("div", { class: "list" });

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "设置"),
        App.h("div", { class: "sub" }, "配置保存在 %APPDATA%/LocalToolbox/config.json"),
      ));

      /* L3 分区容器（subnav 页签互斥显示） */
      const paneGeneral = App.h("div");
      const paneHotkey = App.h("div");
      const paneLog = App.h("div");
      const put = (pane, node) => pane.appendChild(node);

      /* 外观（v3.5d：主题 + 界面缩放 + 强调色；v3.5f：主题跟随系统） */
      const themeSel = App.h("select", { class: "input" },
        App.h("option", { value: "auto" }, "跟随系统"),
        App.h("option", { value: "dark" }, "深色"),
        App.h("option", { value: "light" }, "浅色"),
      );
      const savedTheme = String((App.state.cfg || {}).theme || "dark");
      themeSel.value = ["auto", "light", "dark"].includes(savedTheme) ? savedTheme : "dark";
      themeSel.addEventListener("change", async () => {
        if (await save("theme", themeSel.value)) App.setTheme(themeSel.value);
      });

      const zoomSel = App.h("select", { class: "input" },
        App.h("option", { value: "0.9" }, "90%"),
        App.h("option", { value: "1" }, "100%（默认）"),
        App.h("option", { value: "1.1" }, "110%"),
        App.h("option", { value: "1.25" }, "125%"),
      );
      zoomSel.value = String(Number((App.state.cfg || {}).ui_zoom) || 1);
      if (zoomSel.selectedIndex < 0) zoomSel.value = "1";
      zoomSel.addEventListener("change", async () => {
        if (await save("ui_zoom", Number(zoomSel.value))) {
          App.applyAppearance();
          App.toast("界面缩放已应用", "ok");
        }
      });

      const ACCENTS = [
        ["", "默认蓝"], ["#22b8a6", "青"], ["#51cf66", "绿"],
        ["#845ef7", "紫"], ["#f59f00", "橙"], ["#e64980", "粉"],
      ];
      const accentRow = App.h("div", { class: "row", style: { gap: "8px" } });
      const accentBtns = [];
      const curAccent = String((App.state.cfg || {}).accent_color || "");
      ACCENTS.forEach(([val, label]) => {
        const b = App.h("button", {
          class: "accent-swatch",
          title: label,
          "aria-label": "强调色：" + label,
          style: {
            width: "26px", height: "26px", borderRadius: "50%",
            border: "2px solid var(--border2)", cursor: "pointer", padding: "0",
            background: val || "linear-gradient(135deg, #4c8dff, #7b5cff)",
          },
          onclick: async () => {
            if (!(await save("accent_color", val))) return;
            App.applyAppearance();
            accentBtns.forEach((x) => { x.style.borderColor = "var(--border2)"; });
            b.style.borderColor = "var(--accent)";   // 选中环随新强调色
          },
        });
        if (val === curAccent || (!curAccent && !val)) b.style.borderColor = "var(--accent)";
        accentBtns.push(b);
        accentRow.appendChild(b);
      });

      const reduceMotionTgl = App.h("input", {
        type: "checkbox",
        ...((App.state.cfg || {}).ui_reduce_motion ? { checked: true } : {}),
      });
      reduceMotionTgl.addEventListener("change", async () => {
        if (await save("ui_reduce_motion", reduceMotionTgl.checked)) {
          App.applyAppearance();
        }
      });

      put(paneGeneral, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "外观"),
        App.row(App.h("span", { class: "field-label" }, "主题："), themeSel),
        App.row(App.h("span", { class: "field-label" }, "界面缩放："), zoomSel),
        App.row(App.h("span", { class: "field-label" }, "强调色："), accentRow),
        App.row(
          App.h("label", { class: "switch" }, reduceMotionTgl, App.h("span", { class: "track" }),
            "减少动画（全局禁用过渡与进场动画，低配机更流畅）"),
        ),
      ));

      /* 剪贴板偏好 */
      const deselTgl = App.h("input", {
        type: "checkbox",
        ...(App.state.cfg && App.state.cfg.desensitize ? { checked: true } : {}),
      });
      deselTgl.addEventListener("change", async () => {
        if (await save("desensitize", deselTgl.checked)) {
          App.toast(deselTgl.checked ? "已开启脱敏显示" : "已关闭脱敏显示", "ok");
        }
      });

      const limitInput = App.h("input", {
        class: "input", type: "number", min: 10, max: 1000,
        value: (App.state.cfg && App.state.cfg.history_limit) || 100,
        style: { width: "100px" },
      });
      const limitSave = App.h("button", {
        class: "btn sm",
        onclick: async () => {
          if (await save("history_limit", limitInput.value)) App.toast("历史上限已保存", "ok");
        },
      }, "保存");

      const clipAutoTgl = App.h("input", {
        type: "checkbox",
        ...((App.state.cfg || {}).clip_autostart ? { checked: true } : {}),
      });
      clipAutoTgl.addEventListener("change", async () => {
        if (await save("clip_autostart", clipAutoTgl.checked)) {
          App.toast(clipAutoTgl.checked
            ? "已开启：下次启动自动开始剪贴板同步"
            : "已关闭：下次启动不再自动同步", "ok");
        }
      });

      /* v3.5f：历史保留天数（0=永久，保存立即清理过期条目） */
      const retainSel = App.h("select", { class: "input" },
        App.h("option", { value: "0" }, "永久保留"),
        App.h("option", { value: "7" }, "7 天"),
        App.h("option", { value: "30" }, "30 天"),
        App.h("option", { value: "90" }, "90 天"),
        App.h("option", { value: "365" }, "365 天"),
      );
      retainSel.value = String(Number((App.state.cfg || {}).clip_retain_days) || 0);
      if (retainSel.selectedIndex < 0) retainSel.value = "0";
      retainSel.addEventListener("change", async () => {
        if (!(await save("clip_retain_days", Number(retainSel.value)))) return;
        const d = Number(retainSel.value);
        App.toast(d ? `历史保留 ${d} 天，过期条目已清理` : "历史改为永久保留", "ok");
        if (App.state.page === "clipboard") {
          const p = App.pages.find((x) => x.id === "clipboard");
          if (p && p.show) { try { p.show(); } catch (e) { /* ignore */ } }
        }
      });

      put(paneGeneral, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "剪贴板"),
        App.row(
          App.h("label", { class: "switch" }, deselTgl, App.h("span", { class: "track" }),
            "脱敏显示（手机号 / 身份证 / 银行卡模糊化）"),
        ),
        App.row(
          App.h("span", { class: "field-label" }, "历史上限（条）："), limitInput, limitSave,
        ),
        App.row(
          App.h("span", { class: "field-label" }, "历史保留："), retainSel,
        ),
        App.row(
          App.h("label", { class: "switch" }, clipAutoTgl, App.h("span", { class: "track" }),
            "启动后自动开启剪贴板同步（收发全开，可在剪贴板页随时停止）"),
        ),
        App.h("div", { class: "hint", style: { marginTop: "8px" } },
          "历史本地落盘（SQLite），退出软件后重启仍可查看。"),
      ));

      /* 隐私：历史落盘加密 */
      const encTgl = App.h("input", {
        type: "checkbox",
        ...(App.state.cfg && App.state.cfg.clip_encrypt ? { checked: true } : {}),
      });
      encTgl.addEventListener("change", async () => {
        if (encTgl._busy) return;
        encTgl._busy = true;
        try {
          const r = await App.tryCall("cfg_set", "clip_encrypt", encTgl.checked);
          if (r.ok) {
            const n = Number(r.migrated) || 0;
            App.toast(encTgl.checked
              ? "已开启历史加密（AES-256-GCM）" + (n ? "，存量 " + n + " 条已加密" : "")
              : "已关闭历史加密" + (n ? "，" + n + " 条已解密回明文" : ""), "ok");
          } else {
            App.toast(r.err, "error");
            encTgl.checked = !encTgl.checked;  // 开关回弹：迁移失败配置未变更
          }
        } finally { encTgl._busy = false; }
      });
      /* v5.4 二期：本地 IPC 接口开关（cfg_set 即时启停监听） */
      const ipcTgl = App.h("input", {
        type: "checkbox",
        ...((App.state.cfg || {}).ipc_enabled ? { checked: true } : {}),
      });
      ipcTgl.addEventListener("change", async () => {
        if (ipcTgl._busy) return;
        ipcTgl._busy = true;
        try {
          const r = await App.tryCall("cfg_set", "ipc_enabled", ipcTgl.checked);
          if (r.ok) {
            App.toast(ipcTgl.checked
              ? "IPC 接口已开启（127.0.0.1:17258，只读）" : "IPC 接口已关闭", "ok");
          } else {
            App.toast(r.err, "error", 6000);
            ipcTgl.checked = !ipcTgl.checked;
          }
        } finally { ipcTgl._busy = false; }
      });
      put(paneGeneral, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "隐私"),
        App.row(
          App.h("label", { class: "switch" }, encTgl, App.h("span", { class: "track" }),
            "历史落盘加密（文本 AES-256-GCM，密钥由系统 DPAPI 保护）"),
        ),
        /* v5.4 二期：本地 IPC 只读接口（默认关，仅回环） */
        App.h("div", { class: "sep" }),
        App.row(
          App.h("label", { class: "switch" }, ipcTgl, App.h("span", { class: "track" }),
            "本地 IPC 接口（默认关）"),
        ),
        App.h("div", { class: "hint", style: { marginTop: "4px" } },
          "开启后本机其它程序可经 http://127.0.0.1:17258/api/v1/ 读取已装应用清单" +
          "（只读、仅回环、不暴露局域网）。供自动化脚本对接用，普通使用无需开启。"),
      ));

      /* 文件传输偏好（v3.5d：+ 完成提示音） */
      const hudTgl = App.h("input", {
        type: "checkbox",
        ...(App.state.cfg && App.state.cfg.hud_enabled ? { checked: true } : {}),
      });
      hudTgl.addEventListener("change", async () => {
        const r = await App.tryCall("xfer_set_hud", hudTgl.checked);
        if (r.ok) {
          App.toast(hudTgl.checked ? "已开启进度悬浮窗" : "已关闭进度悬浮窗", "ok");
        } else App.toast(r.err, "error");
      });

      const sndTgl = App.h("input", {
        type: "checkbox",
        ...((App.state.cfg || {}).notify_sound ? { checked: true } : {}),
      });
      sndTgl.addEventListener("change", async () => {
        if (await save("notify_sound", sndTgl.checked)) {
          if (sndTgl.checked) App.beep();   // 开启即试听一声
          App.toast(sndTgl.checked ? "已开启传输完成提示音" : "已关闭传输完成提示音", "ok");
        }
      });

      put(paneGeneral, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "文件传输"),
        App.row(
          App.h("label", { class: "switch" }, hudTgl, App.h("span", { class: "track" }),
            "进度悬浮窗（HUD：传输时桌面右下角显示进度）"),
        ),
        App.row(
          App.h("label", { class: "switch" }, sndTgl, App.h("span", { class: "track" }),
            "完成提示音（收发文件完成时播放短哔声）"),
        ),
      ));

      /* 截图与编辑器（ShareX 功能扩展） */
      const clipMonImgTgl = App.h("input", {
        type: "checkbox",
        ...((App.state.cfg || {}).cliphist_monitor_images !== false ? { checked: true } : {}),
      });
      clipMonImgTgl.addEventListener("change", async () => {
        if (await save("cliphist_monitor_images", clipMonImgTgl.checked)) {
          App.toast(clipMonImgTgl.checked ? "剪贴板历史将同时记录图片" : "剪贴板历史仅记录文本", "ok");
        }
      });

      const clipHistRetainSel = App.h("select", { class: "input" },
        App.h("option", { value: "0" }, "永久保留"),
        App.h("option", { value: "7" }, "7 天"),
        App.h("option", { value: "30" }, "30 天"),
        App.h("option", { value: "90" }, "90 天"),
      );
      clipHistRetainSel.value = String(Number((App.state.cfg || {}).cliphist_retain_days) || 30);
      if (clipHistRetainSel.selectedIndex < 0) clipHistRetainSel.value = "30";
      clipHistRetainSel.addEventListener("change", async () => {
        if (await save("cliphist_retain_days", Number(clipHistRetainSel.value))) {
          const d = Number(clipHistRetainSel.value);
          App.toast(d ? `剪贴板历史保留 ${d} 天` : "剪贴板历史永久保留", "ok");
        }
      });

      const clipPopPasteTgl = App.h("input", {
        type: "checkbox",
        ...(App.state.cfg && App.state.cfg.clip_pop_autopaste ? { checked: true } : {}),
      });
      clipPopPasteTgl.addEventListener("change", async () => {
        if (await save("clip_pop_autopaste", clipPopPasteTgl.checked)) {
          App.toast(clipPopPasteTgl.checked
            ? "弹窗选中后将自动粘贴到原窗口" : "弹窗选中后仅复制到剪贴板", "ok");
        } else if (App.state.cfg) {
          clipPopPasteTgl.checked = !!App.state.cfg.clip_pop_autopaste;
        }
      });

      const pinOpacitySel = App.h("select", { class: "input" },
        App.h("option", { value: "0.5" }, "50%"),
        App.h("option", { value: "0.7" }, "70%"),
        App.h("option", { value: "0.9" }, "90%（默认）"),
        App.h("option", { value: "1" }, "100%"),
      );
      pinOpacitySel.value = String(Number((App.state.cfg || {}).pin_opacity) || 0.9);
      pinOpacitySel.addEventListener("change", async () => {
        if (await save("pin_opacity", Number(pinOpacitySel.value))) {
          App.toast("贴图默认不透明度已保存", "ok");
        }
      });

      /* v5.4 O2：默认图床下拉含自定义上传目标（target:<index>） */
      const uploadSel = App.h("select", { class: "input" });
      function fillUploadSel() {
        uploadSel.innerHTML = "";
        uploadSel.appendChild(App.h("option", { value: "imgur" }, "Imgur（免费公共）"));
        uploadSel.appendChild(App.h("option", { value: "custom" }, "自定义接口（简单）"));
        (((App.state.cfg || {}).upload_targets) || []).forEach((t, i) => {
          uploadSel.appendChild(App.h("option", { value: "target:" + i },
            "目标「" + t.name + "」"));
        });
        const cur = (App.state.cfg || {}).upload_service || "imgur";
        uploadSel.value = cur;
        if (uploadSel.selectedIndex < 0) uploadSel.value = "imgur";
      }
      fillUploadSel();
      uploadSel.addEventListener("change", async () => {
        if (await save("upload_service", uploadSel.value)) {
          App.toast("默认图床已保存", "ok");
        }
      });
      const uploadTargetsBtn = App.h("button", {
        class: "btn sm",
        onclick: () => showUploadTargetsModal(() => { fillUploadSel(); }),
      }, "管理上传目标…");

      put(paneGeneral, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "截图与编辑器"),
        App.row(
          App.h("label", { class: "switch" }, clipMonImgTgl, App.h("span", { class: "track" }),
            "剪贴板历史同时记录图片（复制截图 / 图片时一并保存）"),
        ),
        App.row(
          App.h("span", { class: "field-label" }, "剪贴板历史保留："), clipHistRetainSel,
        ),
        App.row(
          App.h("label", { class: "switch" }, clipPopPasteTgl, App.h("span", { class: "track" }),
            "弹窗选中后自动粘贴到原窗口"),
          App.h("span", { class: "hint" }, "关闭后 Enter 仅复制到剪贴板"),
        ),
        App.row(
          App.h("span", { class: "field-label" }, "贴图默认不透明度："), pinOpacitySel,
        ),
        App.row(
          App.h("span", { class: "field-label" }, "默认图床："), uploadSel,
        ),
        App.row(
          App.h("span", { class: "field-label" }, "自定义上传目标："), uploadTargetsBtn,
        ),
        App.h("div", { class: "hint", style: { marginTop: "8px" } },
          "贴图（Pin to Screen）可将截图钉在桌面最上层；图床上传用于快速分享截图链接。"),
      ));

      /* OCR（v5.1：三引擎 + 截图识字 + 后处理） */
      const ocrEngineSel = App.h("select", { class: "input", style: { width: "auto" } },
        App.h("option", { value: "winrt" }, "Windows 内置（默认，零依赖）"),
        App.h("option", { value: "rapid" }, "RapidOCR 本地内核（需 pip 安装）"),
        App.h("option", { value: "umi" }, "Umi-OCR 内核（HTTP，可下载）"),
      );
      ocrEngineSel.value = ["winrt", "rapid", "umi"].includes(
        (App.state.cfg || {}).ocr_engine) ? App.state.cfg.ocr_engine : "winrt";
      ocrEngineSel.addEventListener("change", () => save("ocr_engine", ocrEngineSel.value));
      const ocrUrlInput = App.h("input", {
        class: "input grow-in",
        placeholder: "http://127.0.0.1:1224",
        value: (App.state.cfg || {}).ocr_umi_url || "http://127.0.0.1:1224",
        onchange: (e) => save("ocr_umi_url", e.target.value.trim()),
      });
      const ocrMergeTgl = App.h("input", {
        type: "checkbox",
        ...((App.state.cfg || {}).ocr_merge_lines !== false ? { checked: true } : {}),
        onchange: (e) => save("ocr_merge_lines", e.target.checked),
      });
      const ocrAutoTgl = App.h("input", {
        type: "checkbox",
        ...((App.state.cfg || {}).ocr_umi_autostart !== false ? { checked: true } : {}),
        onchange: (e) => save("ocr_umi_autostart", e.target.checked),
      });
      const ocrTestBtn = App.h("button", {
        class: "btn sm", onclick: async () => {
          const r = await App.tryCall("tool_ocr_engines");
          if (!r.ok) { App.toast(r.err, "error"); return; }
          const d = r.data;
          if (d.engine === "umi") {
            App.toast(d.umi_ready ? "Umi-OCR 服务连接正常" : "Umi-OCR 服务未运行（识别时会自动拉起）",
                      d.umi_ready ? "ok" : "warn", 6000);
          } else if (d.engine === "rapid") {
            App.toast(d.rapid ? "RapidOCR 可用" : "RapidOCR 未安装", d.rapid ? "ok" : "warn", 6000);
          } else {
            App.toast(d.winrt ? "Windows 内置引擎可用" : "内置引擎不可用（缺语言包）",
                      d.winrt ? "ok" : "warn", 6000);
          }
        },
      }, "测试引擎");
      const ocrDlBtn = App.h("button", {
        class: "btn sm", onclick: async () => {
          App.toast("开始下载 Umi-OCR 内核（约 103MB）…", "ok", 6000);
          await App.tryCall("ocr_umi_download");
        },
      }, "下载 Umi 内核");
      put(paneGeneral, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "OCR 识别"),
        App.row(
          App.h("span", { class: "field-label" }, "识别引擎："), ocrEngineSel, ocrTestBtn,
        ),
        App.row(
          App.h("span", { class: "field-label" }, "Umi 服务地址："), ocrUrlInput,
          ocrDlBtn,
        ),
        App.row(
          App.h("label", { class: "switch" }, ocrMergeTgl, App.h("span", { class: "track" }),
            "文本后处理：中文相邻行智能合并"),
          App.h("label", { class: "switch" }, ocrAutoTgl, App.h("span", { class: "track" }),
            "Umi 服务未运行时自动拉起内核"),
        ),
        App.h("div", { class: "hint", style: { marginTop: "8px" } },
          "内置 Windows 引擎零依赖；RapidOCR 需源码运行并 pip install rapidocr_onnxruntime；",
          App.h("br"),
          "Umi-OCR 内核由 OCR 页「下载内核」按钮按需下载（约 103MB，本机离线识别），不选择 umi 引擎时完全不依赖。"),
      ));

      /* 全局快捷键：总开关 + 自定义组合键（剪贴板呼出 / 区域截图） */
      const hotkeyTgl = App.h("input", {
        type: "checkbox",
        ...(App.state.cfg && App.state.cfg.hotkey_enabled !== false ? { checked: true } : {}),
      });
      hotkeyTgl.addEventListener("change", async () => {
        const r = await App.tryCall("hotkey_set_enabled", hotkeyTgl.checked);
        if (r.ok) {
          App.toast(hotkeyTgl.checked ? "已启用全局快捷键" : "已禁用全部全局快捷键", "ok");
        } else App.toast(r.err, "error");
      });

      const clipEditor = hotkeyEditor("clip",
        App.state.cfg && App.state.cfg.hotkey_clip ? App.state.cfg.hotkey_clip : "Ctrl+Alt+V");
      const shotEditor = hotkeyEditor("shot",
        App.state.cfg && App.state.cfg.hotkey_shot ? App.state.cfg.hotkey_shot : "Ctrl+Alt+A");
      const fullEditor = hotkeyEditor("full",
        App.state.cfg && App.state.cfg.hotkey_full ? App.state.cfg.hotkey_full : "Ctrl+Alt+F");
      const popEditor = hotkeyEditor("pop",
        App.state.cfg && App.state.cfg.hotkey_pop ? App.state.cfg.hotkey_pop : "Win+V");
      const memoEditor = hotkeyEditor("memo",
        App.state.cfg && App.state.cfg.hotkey_memo ? App.state.cfg.hotkey_memo : "Ctrl+Alt+M");
      const ocrEditor = hotkeyEditor("ocr",
        App.state.cfg && App.state.cfg.hotkey_ocr ? App.state.cfg.hotkey_ocr : "Ctrl+Alt+O");

      put(paneHotkey, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "全局快捷键"),
        App.row(
          App.h("label", { class: "switch" }, hotkeyTgl, App.h("span", { class: "track" }),
            "启用全局快捷键（总开关）"),
        ),
        App.h("div", { class: "sep" }),
        App.row(
          App.h("span", { class: "field-label", style: { width: "150px" } }, "剪贴板弹窗："),
          popEditor.btn,
        ),
        App.row(
          App.h("span", { class: "field-label", style: { width: "150px" } }, "备忘录弹窗："),
          memoEditor.btn,
        ),
        App.row(
          App.h("span", { class: "field-label", style: { width: "150px" } }, "截图识字："),
          ocrEditor.btn,
        ),
        App.row(
          App.h("span", { class: "field-label", style: { width: "150px" } }, "剪贴板历史呼出："),
          clipEditor.btn,
        ),
        App.row(
          App.h("span", { class: "field-label", style: { width: "150px" } }, "区域截图："),
          shotEditor.btn,
        ),
        App.row(
          App.h("span", { class: "field-label", style: { width: "150px" } }, "全屏截图："),
          fullEditor.btn,
        ),
        App.h("div", { class: "hint" },
          "剪贴板弹窗热键：任意程序内唤起剪贴板历史弹窗，选中即粘贴回原窗口（默认 Win+V，接管系统剪贴板历史）；", App.h("br"),
          "剪贴板历史热键：任意程序内呼出主窗口并打开剪贴板历史页；区域截图热键：呼出主窗口并直接进入区域圈选；", App.h("br"),
          "全屏截图热键：不呼出主窗口，自动隐藏后直拍整个虚拟桌面并按「截图后自动」任务链处理（默认 Ctrl+Alt+F）。", App.h("br"),
          "点击对应按钮后按下新组合即可修改（ESC 取消）。字母 / 数字键需搭配 Ctrl / Alt / Shift / Win。", App.h("br"),
          "含 Win 的组合若被系统占用（如 Win+V 被「系统剪贴板历史」占用），本应用会自动接管：运行期间该组合由本应用响应，退出后系统恢复原样。"),
      ));

      /* 启动与托盘（v3.5d：自启动 + 启动页 + 最小化启动 + 关闭行为 + 通知开关） */
      const appAutoTgl = App.h("input", {
        type: "checkbox",
        ...(App.state.cfg && App.state.cfg.app_autostart ? { checked: true } : {}),
      });
      appAutoTgl.addEventListener("change", async () => {
        if (await save("app_autostart", appAutoTgl.checked)) {
          App.toast(appAutoTgl.checked ? "已开启开机自启（HKCU Run）" : "已取消开机自启", "ok");
        } else if (App.state.cfg) {
          appAutoTgl.checked = !!App.state.cfg.app_autostart; // 写入失败回滚
        }
      });

      const startSel = App.h("select", { class: "input" });
      App.pages.forEach((p) => startSel.appendChild(App.h("option", { value: p.id }, p.title)));
      startSel.value = (App.state.cfg || {}).start_page || "clipboard";
      if (startSel.selectedIndex < 0) startSel.value = "clipboard";
      startSel.addEventListener("change", async () => {
        if (await save("start_page", startSel.value)) {
          App.toast("启动页已保存，下次启动生效", "ok");
        }
      });

      const minStartTgl = App.h("input", {
        type: "checkbox",
        ...((App.state.cfg || {}).start_minimized ? { checked: true } : {}),
      });
      minStartTgl.addEventListener("change", async () => {
        if (await save("start_minimized", minStartTgl.checked)) {
          App.toast(minStartTgl.checked
            ? "已开启：下次启动直接驻留托盘" : "已关闭：下次启动弹出主窗口", "ok");
        }
      });

      const closeExitTgl = App.h("input", {
        type: "checkbox",
        ...((App.state.cfg || {}).tray_close_exit ? { checked: true } : {}),
      });
      closeExitTgl.addEventListener("change", async () => {
        if (await save("tray_close_exit", closeExitTgl.checked)) {
          App.toast(closeExitTgl.checked
            ? "关闭按钮现在直接退出程序" : "关闭按钮现在隐藏到托盘", "ok");
        }
      });

      const trayNotifyTgl = App.h("input", {
        type: "checkbox",
        ...(App.state.cfg && App.state.cfg.tray_notify !== false ? { checked: true } : {}),
      });
      trayNotifyTgl.addEventListener("change", async () => {
        if (await save("tray_notify", trayNotifyTgl.checked)) {
          App.toast(trayNotifyTgl.checked ? "已开启托盘通知" : "已关闭托盘通知", "ok");
        }
      });

      const onTopTgl = App.h("input", {
        type: "checkbox",
        ...((App.state.cfg || {}).win_on_top ? { checked: true } : {}),
      });
      onTopTgl.addEventListener("change", async () => {
        if (!(await save("win_on_top", onTopTgl.checked))) return;
        const r = await App.tryCall("win_set_on_top", onTopTgl.checked);
        if (!r.ok) App.toast(r.err, "error");
        else App.toast(onTopTgl.checked ? "主窗口已置顶" : "已取消置顶", "ok");
      });

      const rememberTgl = App.h("input", {
        type: "checkbox",
        ...((App.state.cfg || {}).win_remember ? { checked: true } : {}),
      });
      rememberTgl.addEventListener("change", async () => {
        if (await save("win_remember", rememberTgl.checked)) {
          App.toast(rememberTgl.checked
            ? "已开启：下次启动按上次关闭时的窗口大小与位置显示"
            : "已关闭：恢复默认窗口尺寸", "ok");
        }
      });

      put(paneHotkey, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "启动与托盘"),
        App.row(App.h("span", { class: "field-label" }, "启动页："), startSel),
        App.row(
          App.h("label", { class: "switch" }, appAutoTgl, App.h("span", { class: "track" }),
            "开机自动启动本应用（登录 Windows 后后台常驻）"),
        ),
        App.row(
          App.h("label", { class: "switch" }, minStartTgl, App.h("span", { class: "track" }),
            "启动时最小化到托盘（不弹出主窗口，托盘图标照常可用）"),
        ),
        App.h("div", { class: "sep" }),
        App.row(
          App.h("label", { class: "switch" }, closeExitTgl, App.h("span", { class: "track" }),
            "关闭按钮直接退出程序（不勾选时隐藏到托盘后台运行）"),
        ),
        App.row(
          App.h("label", { class: "switch" }, trayNotifyTgl, App.h("span", { class: "track" }),
            "托盘气泡通知（传输完成 / 服务异常提醒等）"),
        ),
        App.row(
          App.h("label", { class: "switch" }, onTopTgl, App.h("span", { class: "track" }),
            "主窗口置顶（始终悬浮在其他窗口之上）"),
        ),
        App.row(
          App.h("label", { class: "switch" }, rememberTgl, App.h("span", { class: "track" }),
            "记住窗口大小与位置（下次启动还原上次布局）"),
        ),
        App.h("div", { class: "hint" },
          "OpenList 服务随应用启动、rclone 恢复挂载的开关在「网盘挂载」页配置。"),
      ));

      /* 自动更新（v3.5c）：定时检查 GitHub 新版本，可选自动下载 */
      const upCfg = App.state.cfg || {};
      const upScope = Array.isArray(upCfg.update_scope) ? upCfg.update_scope : ["openlist", "rclone", "core"];
      const autoChkTgl = App.h("input", {
        type: "checkbox", ...(upCfg.update_auto_check ? { checked: true } : {}),
      });
      const autoDlTgl = App.h("input", {
        type: "checkbox", ...(upCfg.update_auto_download ? { checked: true } : {}),
      });
      const scopeBoxes = {};
      const scopeRow = App.h("div", { class: "row" });
      [["openlist", "OpenList"], ["rclone", "Rclone"], ["core", "代理核心"]].forEach(([k, label]) => {
        scopeBoxes[k] = App.h("input", { type: "checkbox", checked: upScope.includes(k) });
        scopeRow.append(App.h("label", { class: "chk" }, scopeBoxes[k], " " + label));
      });
      async function saveUpdate() {
        const r = await App.tryCall("update_set_settings",
          autoChkTgl.checked, autoDlTgl.checked,
          Object.keys(scopeBoxes).filter((k) => scopeBoxes[k].checked));
        if (!r.ok) { App.toast(r.err, "error"); return; }
        autoDlTgl.disabled = !autoChkTgl.checked;
        Object.values(scopeBoxes).forEach((b) => { b.disabled = !autoChkTgl.checked; });
        App.toast(autoChkTgl.checked ? "自动检查更新已开启（每天一次）" : "已关闭自动检查更新", "ok");
      }
      autoChkTgl.addEventListener("change", saveUpdate);
      autoDlTgl.addEventListener("change", saveUpdate);
      scopeRow.addEventListener("change", saveUpdate);
      autoDlTgl.disabled = !upCfg.update_auto_check;
      Object.values(scopeBoxes).forEach((b) => { b.disabled = !upCfg.update_auto_check; });

      const upResult = App.h("div", { class: "list", style: { marginTop: "8px" } });
      const upMeta = App.h("div", { class: "hint", style: { marginTop: "6px" } });
      const upStatus = App.h("div", { class: "hint", style: { marginTop: "6px", color: "var(--accent)" } });
      function renderUpdateResults(results) {
        upResult.innerHTML = "";
        for (const it of results || []) {
          if (it.error) {
            upResult.appendChild(App.h("div", { class: "list-item" },
              App.h("span", { class: "li-main" },
                App.h("div", { class: "li-title" }, it.kind),
                App.h("div", { class: "li-sub", style: { color: "var(--danger)" } }, it.error))));
            continue;
          }
          upResult.appendChild(App.h("div", { class: "list-item" },
            App.h("span", { class: "li-main" },
              App.h("div", { class: "li-title" }, it.kind),
              App.h("div", { class: "li-sub mono" },
                `本地 ${it.cached || "未下载"} → 最新 ${it.latest}`)),
            it.has_update
              ? App.h("span", { class: "tag warn" }, "有新版本")
              : App.h("span", { class: "tag ok" }, "已是最新"),
          ));
        }
      }
      /* 事件订阅只在 mount 注册一次；自动检查（后台线程）才 toast，手动检查由按钮自己反馈 */
      App.on("update_found", (d) => {
        if (d && d.results) renderUpdateResults(d.results);
        if (!d || !d.auto) return;
        const upd = (d.results || []).filter((r) => r.has_update);
        if (upd.length) {
          App.toast(`发现新版本：${upd.map((r) => r.kind).join("、")}` +
            (d.downloaded ? "（已自动下载，重启服务后生效）" : "，可在设置中手动下载"), "warn", 7000);
        }
      });
      App.on("update_progress", (d) => {
        if (!d) return;
        if (d.status === "running" && d.total > 0) {
          upStatus.textContent = `正在下载 ${d.kind}：${App.fmtBytes(d.done)} / ${App.fmtBytes(d.total)}`;
        } else if (d.status === "done") {
          upStatus.textContent = `${d.kind} 下载完成（重启对应服务后生效）`;
        } else if (d.status === "error") {
          upStatus.textContent = `${d.kind} 下载失败：${d.err || "未知错误"}`;
          App.toast(`${d.kind} 下载失败：${d.err || "未知错误"}`, "error", 8000);
        }
      });
      App.on("update_downloaded", (d) => {
        App.toast("自动下载完成：" + (d.ok || []).join("、") +
          ((d.fail || []).length ? "；失败：" + d.fail.join("、") : ""), "ok", 8000);
      });

      const checkNowBtn = App.h("button", { class: "btn sm primary" }, "立即检查");
      checkNowBtn.onclick = async () => {
        checkNowBtn.disabled = true;
        checkNowBtn.textContent = "检查中…";
        const r = await App.tryCall("update_check_now");
        checkNowBtn.disabled = false;
        checkNowBtn.textContent = "立即检查";
        if (!r.ok) { App.toast(r.err, "error", 6000); return; }
        renderUpdateResults(r.data);
        const upd = (r.data || []).filter((x) => x.has_update);
        App.toast(upd.length ? `发现 ${upd.length} 个组件有新版本` : "全部组件已是最新", "ok");
      };
      const dlBtn = App.h("button", { class: "btn sm", title: "下载检查范围内有新版本的组件（运行中的服务需重启后生效）" }, "下载更新");
      dlBtn.onclick = async () => {
        dlBtn.disabled = true;
        dlBtn.textContent = "下载中…";
        try {
          const r = await App.tryCall("update_check_now");
          if (!r.ok) { App.toast(r.err, "error", 6000); return; }
          const upd = (r.data || []).filter((x) => x.has_update).map((x) => x.kind);
          if (!upd.length) { App.toast("没有需要下载的更新", "info"); return; }
          // 逐个下载有新版本的组件（update_download_one 内置进度与缓存幂等）
          const okList = [], failList = [];
          for (const kind of upd) {
            const dr = await App.tryCall("update_download_one", kind);
            if (dr.ok) okList.push(kind);
            else failList.push(`${kind}（${dr.err || "失败"}）`);
          }
          if (failList.length) {
            App.toast("下载失败：" + failList.join("、"), "error", 8000);
          } else if (okList.length) {
            App.toast("下载完成：" + okList.join("、") + "（重启对应服务后生效）", "ok", 8000);
          }
        } finally {
          dlBtn.disabled = false;
          dlBtn.textContent = "下载更新";
        }
      };
      /* 恢复上次检查时间与结果（重进页面不清空） */
      (async () => {
        const r = await App.tryCall("update_get_settings");
        if (!r.ok) return;
        if (r.data.last_check) {
          upMeta.textContent = "上次检查：" +
            (App.fmtTime ? App.fmtTime(r.data.last_check) : new Date(r.data.last_check * 1000).toLocaleString());
        }
        if (Array.isArray(r.data.last_results) && r.data.last_results.length) {
          renderUpdateResults(r.data.last_results);
        }
      })();

      put(paneHotkey, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "自动更新（内核与组件保持最新）"),
        App.row(
          App.h("label", { class: "switch" }, autoChkTgl, App.h("span", { class: "track" }),
            "自动检查更新（启动 30 秒后首查，之后每天一次）"),
        ),
        App.row(
          App.h("label", { class: "switch" }, autoDlTgl, App.h("span", { class: "track" }),
            "自动下载新版本（下载后需重启对应服务生效，不中断运行）"),
        ),
        App.row(App.h("span", { class: "field-label" }, "检查范围："), scopeRow),
        App.row(
          checkNowBtn, dlBtn,
          App.h("span", { class: "hint" }, "手动下载仅作用于有新版本的组件；OpenList / Rclone / 代理核心"),
        ),
        upStatus,
        upMeta,
        upResult,
      ));

      /* 备份与恢复（v5.0：备忘录/密码库 WebDAV 多目标并行） */
      const bkTargetsBox = App.h("div", { style: { marginTop: "6px" } });
      const bkTargets = () => ((App.state.cfg || {}).backup_targets || []);
      const bkSaveTargets = async (list) => {
        const r = await App.tryCall("cfg_set", "backup_targets", list);
        if (!r.ok) { App.toast(r.err, "error"); return null; }
        return r.data;
      };
      const bkRenderTargets = () => {
        bkTargetsBox.innerHTML = "";
        const list = bkTargets();
        if (!list.length) {
          bkTargetsBox.appendChild(App.h("div", { class: "hint" }, "尚未配置备份目标"));
        }
        list.forEach((t, i) => {
          const tgl = App.h("input", {
            type: "checkbox", ...(t.enabled ? { checked: true } : {}),
            onchange: async (e) => {
              const next = bkTargets().map((x, j) => j === i ? { ...x, enabled: e.target.checked } : x);
              const r = await bkSaveTargets(next);
              if (r) bkRenderTargets();
            },
          });
          bkTargetsBox.appendChild(App.h("div", { class: "row", style: { alignItems: "center" } },
            App.h("label", { class: "switch", style: { flex: "none" } },
              tgl, App.h("span", { class: "track" })),
            App.h("span", { style: { width: "90px", flex: "none", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, t.name || t.type),
            App.h("span", { class: "hint", style: { flex: "1", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } },
              t.type === "openlist" ? `复用网盘账号（${(App.state.cfg || {}).openlist_host || "127.0.0.1"}:${(App.state.cfg || {}).openlist_port || 15244}/dav）` : (t.url || "未填地址")),
            App.h("button", {
              class: "btn sm", onclick: async () => {
                App.toast("正在测试连接…", "ok");
                const r = await App.tryCall("backup_test", i);
                if (!r.ok) App.toast(r.err, "error", 6000);
                else App.toast(`「${r.data.name}」连接成功`, "ok");
              },
            }, "测试"),
            App.h("button", {
              class: "btn sm", onclick: async () => {
                const ok = await App.confirm(`删除备份目标「${t.name}」？`);
                if (!ok) return;
                const r = await bkSaveTargets(bkTargets().filter((_, j) => j !== i));
                if (r) bkRenderTargets();
              },
            }, "×"),
          ));
        });
        bkTargetsBox.appendChild(App.h("div", { class: "row", style: { marginTop: "4px" } },
          App.h("button", {
            class: "btn sm", onclick: async () => {
              const r = await bkSaveTargets([...bkTargets(), {
                type: "openlist", name: "网盘", url: "", user: "", pwd: "",
                dir: "LocalToolboxBackup", enabled: true }]);
              if (r) bkRenderTargets();
            },
          }, "+ 复用网盘账号"),
          App.h("button", {
            class: "btn sm", onclick: async () => {
              const vals = await App.modal({
                title: "添加 WebDAV 目标",
                inputs: [
                  { label: "名称", placeholder: "例如：坚果云" },
                  { label: "WebDAV 地址", placeholder: "https://dav.jianguoyun.com/dav/" },
                  { label: "账号", placeholder: "账号" },
                  { label: "密码 / 应用密码", type: "password" },
                  { label: "备份目录", placeholder: "LocalToolboxBackup" },
                ],
                okText: "添加",
              });
              if (!vals || !vals[1]) return;
              const r = await bkSaveTargets([...bkTargets(), {
                type: "webdav", name: vals[0] || "WebDAV", url: vals[1].trim(),
                user: vals[2] || "", pwd: vals[3] || "",
                dir: vals[4] || "LocalToolboxBackup", enabled: true }]);
              if (r) bkRenderTargets();
            },
          }, "+ 自定义 WebDAV"),
        ));
      };

      const bkKeepInput = App.h("input", {
        class: "input", type: "number", min: "1", max: "50", style: { width: "70px" },
        value: (App.state.cfg || {}).backup_keep != null ? App.state.cfg.backup_keep : 10,
        onchange: (e) => save("backup_keep", parseInt(e.target.value, 10) || 10),
      });
      const bkAutoTgl = App.h("input", {
        type: "checkbox",
        ...((App.state.cfg || {}).backup_autoupload ? { checked: true } : {}),
        onchange: (e) => save("backup_autoupload", e.target.checked),
      });
      const bkHoursInput = App.h("input", {
        class: "input", type: "number", min: "6", max: "720", style: { width: "80px" },
        value: (App.state.cfg || {}).backup_autoupload_hours != null
          ? App.state.cfg.backup_autoupload_hours : 24,
        onchange: (e) => save("backup_autoupload_hours", parseInt(e.target.value, 10) || 24),
      });
      const bkStatus = App.h("div", { class: "hint", style: { marginTop: "6px" } });
      const bkRestoreKind = App.h("select", { class: "input", style: { width: "auto" } },
        App.h("option", { value: "memo" }, "备忘录"),
        App.h("option", { value: "vault" }, "密码库"));
      const bkRestoreTarget = App.h("select", { class: "input", style: { width: "auto" } });
      const bkRestoreFile = App.h("select", { class: "input", style: { width: "auto", maxWidth: "260px" } });
      const bkRestoreMode = App.h("select", { class: "input", style: { width: "auto" } },
        App.h("option", { value: "merge" }, "合并（仅备忘录）"),
        App.h("option", { value: "replace" }, "替换现有内容"));
      const bkRefreshTargets = () => {
        bkRestoreTarget.innerHTML = "";
        bkTargets().forEach((t, i) => {
          bkRestoreTarget.appendChild(App.h("option", { value: String(i) },
            `${t.name || t.type}（${t.type === "openlist" ? "网盘账号" : "WebDAV"}）`));
        });
      };
      const bkListFiles = async () => {
        bkRestoreFile.innerHTML = "";
        const kind = bkRestoreKind.value;
        const r = await App.tryCall("backup_list", kind, parseInt(bkRestoreTarget.value || "0", 10));
        if (!r.ok) {
          bkRestoreFile.appendChild(App.h("option", { value: "" }, r.err || "获取失败"));
          return;
        }
        const files = r.data.files || [];
        if (!files.length) {
          bkRestoreFile.appendChild(App.h("option", { value: "" }, "（该目标暂无备份）"));
          return;
        }
        for (const f of files) bkRestoreFile.appendChild(App.h("option", { value: f.name }, f.name));
      };
      bkRestoreKind.addEventListener("change", bkListFiles);
      bkRestoreTarget.addEventListener("change", bkListFiles);
      bkRefreshTargets();

      put(paneHotkey, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "备份与恢复（备忘录 / 密码库）"),
        bkTargetsBox,
        App.h("div", { class: "sep" }),
        /* v5.3：此前 5 个元素挤一行（保留份数+开关+间隔+单位），拆成语义两行 */
        App.row(
          App.h("span", { class: "field-label" }, "每目标保留份数："), bkKeepInput,
        ),
        App.row(
          App.h("label", { class: "switch" }, bkAutoTgl, App.h("span", { class: "track" }), "自动定期备份"),
          App.h("span", { class: "field-label" }, "间隔："), bkHoursInput,
          App.h("span", { class: "hint" }, "小时"),
        ),
        App.h("div", { class: "row", style: { marginTop: "4px" } },
          App.h("button", {
            class: "btn primary", onclick: async function () {
              /* v5.1c：备份是慢操作，禁用按钮 + 状态联动（backup_progress 事件） */
              if (this._busy) return;
              this._busy = true; this.disabled = true;
              const old = this.textContent; this.textContent = "备份中…";
              bkStatus.textContent = "正在备份到全部启用目标…";
              try {
                const r = await App.tryCall("backup_run", "all");
                if (!r.ok) { bkStatus.textContent = r.err; App.toast(r.err, "error"); }
              } finally {
                this._busy = false; this.disabled = false; this.textContent = old;
              }
            },
          }, "立即备份"),
          App.h("span", { class: "hint" },
            "备忘录导出为明文 JSON；密码库上传加密文件原样（跨机输主口令即可恢复）"),
        ),
        bkStatus,
        App.h("div", { class: "sep" }),
        App.h("div", { class: "card-title", style: { fontSize: "13px" } }, "从备份恢复"),
        App.row(
          bkRestoreKind, bkRestoreTarget,
          App.h("button", { class: "btn sm", onclick: bkListFiles }, "刷新列表"),
        ),
        App.row(
          bkRestoreFile, bkRestoreMode,
          App.h("button", {
            class: "btn", onclick: async function () {
              /* v5.1c：恢复含 WebDAV 下载（可达数秒），防连点重复恢复 */
              if (this._busy) return;
              const name = bkRestoreFile.value;
              if (!name) { App.toast("请先刷新并选择备份文件", "warn"); return; }
              const kind = bkRestoreKind.value;
              const mode = bkRestoreMode.value;
              if (mode === "replace") {
                const what = kind === "memo" ? "备忘录现有全部内容"
                  : "密码库现有全部条目（恢复后需用备份时的主口令重新解锁）";
                const ok = await App.confirm("替换导入确认",
                  `将清空${what}并从备份重建，确定继续？`);
                if (!ok) return;
              }
              this._busy = true; this.disabled = true;
              const old = this.textContent; this.textContent = "恢复中…";
              try {
                const r = await App.tryCall("backup_restore", kind,
                  parseInt(bkRestoreTarget.value || "0", 10), name, mode);
                if (!r.ok) { App.toast(r.err, "error", 6000); return; }
                App.toast(kind === "memo" ? `已恢复 ${r.data.count} 条备忘` : "密码库已恢复，请用备份时的主口令解锁", "ok", 6000);
              } finally {
                this._busy = false; this.disabled = false; this.textContent = old;
              }
            },
          }, "恢复"),
        ),
      ));

      App.on("backup_progress", (d) => {
        const fails = (d.results || []).filter((x) => !x.ok);
        bkStatus.textContent = `「${d.target}」：` + (fails.length
          ? fails.map((f) => `${f.kind} 失败（${f.err}）`).join("，")
          : (d.results || []).map((f) => `${f.kind} ✓ ${f.file}`).join("，"));
      });
      App.on("backup_done", (d) => {
        bkStatus.textContent = `备份完成：成功 ${d.ok_count} 项，失败 ${d.fail_count} 项`;
        if (d.ok) App.toast("云备份完成", "ok");
        else App.toast(`云备份有 ${d.fail_count} 项失败，详见状态栏`, "warn", 6000);
      });

      /* v5.4 O7：整机配置导出/导入 */
      const showCfgImportModal = () => {
        const stratSel = App.h("select", { class: "input" },
          App.h("option", { value: "skip_existing" }, "存量优先（保留本机已改过的设置）"),
          App.h("option", { value: "overwrite" }, "导入优先（导入文件覆盖本机）"),
        );
        App.modal({
          title: "导入整机配置",
          okText: "选择文件并导入",
          body: App.h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } },
            App.row(App.h("span", { class: "field-label" }, "合并策略："), stratSel),
            App.h("div", { class: "hint" },
              "导入前请确认文件由本应用「导出配置」生成（含校验和，损坏文件会被拒绝）。"),
            App.h("div", { class: "hint" },
              "部分设置（端口、自启动等）将在重启应用后完全生效。"),
          ),
        }).then(async (v) => {
          if (!v) return;
          const pick = await App.tryCall("cfg_import_pick");
          if (!pick.ok) {
            if (String(pick.err || "").indexOf("未选择") < 0) App.toast(pick.err, "error");
            return;
          }
          const ok = await App.confirm("导入配置确认",
            "将按所选策略合并导入配置（导入过程自动备份当前配置），确定继续？");
          if (!ok) return;
          const r = await App.tryCall("cfg_import", pick.data.path, stratSel.value);
          if (!r.ok) { App.toast(r.err, "error", 6000); return; }
          const d = r.data;
          let msg = `导入完成：合并 ${d.merged} 项，保留 ${d.skipped} 项`;
          if (d.failed.length) msg += `，失败 ${d.failed.length} 项（${d.failed[0]}…）`;
          App.toast(msg + "。部分设置重启后生效", d.failed.length ? "warn" : "ok", 8000);
        });
      };
      put(paneHotkey, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "整机配置迁移"),
        App.h("div", { class: "row", style: { marginTop: "4px", flexWrap: "wrap" } },
          App.h("button", {
            class: "btn", onclick: async function () {
              if (this._busy) return;
              this._busy = true; this.disabled = true;
              const old = this.textContent; this.textContent = "导出中…";
              try {
                const pick = await App.tryCall("cfg_export_pick");
                if (!pick.ok) {
                  if (String(pick.err || "").indexOf("未选择") < 0) App.toast(pick.err, "error");
                  return;
                }
                const r = await App.tryCall("cfg_export", pick.data.path, true);
                if (!r.ok) { App.toast(r.err, "error"); return; }
                App.toast(`已导出 ${r.data.count} 项配置（敏感项已剔除）：${r.data.path}`, "ok", 8000);
              } finally { this._busy = false; this.disabled = false; this.textContent = old; }
            },
          }, "导出配置…"),
          App.h("button", { class: "btn", onclick: showCfgImportModal }, "导入配置…"),
        ),
        App.h("div", { class: "hint", style: { marginTop: "8px" } },
          "导出文件含版本号与 SHA-256 校验和；WebDAV 密码等敏感项默认剔除。导入逐键合并，可保留本机设置或以导入文件为准。"),
      ));

      /* 日志 */
      const levelSel = App.h("select", { class: "input" },
        App.h("option", { value: "DEBUG" }, "DEBUG"),
        App.h("option", { value: "INFO" }, "INFO"),
        App.h("option", { value: "WARN" }, "WARN"),
        App.h("option", { value: "ERROR" }, "ERROR"),
      );
      const logMeta = App.h("div", { class: "hint", style: { marginTop: "8px" } });
      const openDirBtn = App.h("button", { class: "btn sm" }, "打开日志目录");
      const openAppBtn = App.h("button", { class: "btn sm" }, "查看 app.log");
      const openDataBtn = App.h("button", { class: "btn sm" }, "打开数据目录");
      openDirBtn.onclick = async () => {
        const r = await App.tryCall("log_open_dir");
        if (!r.ok) App.toast(r.err, "error");
      };
      openAppBtn.onclick = async () => {
        const r = await App.tryCall("log_open_app");
        if (!r.ok) App.toast(r.err, "error");
      };
      openDataBtn.onclick = async () => {
        const r = await App.tryCall("log_open_data");
        if (!r.ok) App.toast(r.err, "error");
      };

      async function refreshLogCard() {
        const r = await App.tryCall("log_status");
        if (!r.ok) return;
        if (r.data.level) levelSel.value = r.data.level;
        const fmt = (n) => n >= 1048576 ? (n / 1048576).toFixed(2) + " MiB"
          : n >= 1024 ? (n / 1024).toFixed(1) + " KiB" : n + " B";
        logMeta.textContent =
          `app.log：${fmt(r.data.app_size)}　·　级别：${r.data.level}` +
          (r.data.dropped ? `　·　队列溢出已丢弃 ${r.data.dropped} 条（级别过低时发生）` : "") +
          "。日志存放于 %APPDATA%/LocalToolbox/logs/，按大小自动轮转。";
      }
      levelSel.addEventListener("change", async () => {
        const r = await App.tryCall("log_set_level", levelSel.value);
        if (!r.ok) { App.toast(r.err, "error"); return; }
        App.toast("日志级别已设为 " + r.data, "ok");
        refreshLogCard();
      });

      put(paneLog, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "日志"),
        App.row(
          App.h("span", { class: "field-label" }, "日志级别："), levelSel,
          App.h("span", { style: { flex: 1 } }),
          openDirBtn, openAppBtn, openDataBtn,
        ),
        logMeta,
      ));

      /* 本机信息（v3.5e：+ 自定义设备名） */
      const nameInput = App.h("input", {
        class: "input grow-in", type: "text", maxLength: "32",
        placeholder: "留空使用主机名",
        value: String((App.state.cfg || {}).device_name_custom || ""),
      });
      const nameSave = App.h("button", { class: "btn sm" }, "保存");
      nameSave.onclick = async () => {
        const r = await App.tryCall("cfg_set", "device_name_custom", nameInput.value);
        if (!r.ok) { App.toast(r.err, "error"); return; }
        if (App.state.cfg) App.state.cfg.device_name_custom = r.data;
        App.toast(r.data
          ? "设备名已更新为「" + r.data + "」，广播 3 秒内对其他设备可见（传输握手名重启后完全生效）"
          : "已恢复为主机名", "ok", 6000);
        refresh();
      };

      put(paneLog, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "本机信息"),
        refs.selfBox,
        App.h("div", { class: "sep" }),
        App.row(
          App.h("span", { class: "field-label" }, "自定义设备名："), nameInput, nameSave,
        ),
        App.h("div", { class: "hint", style: { marginTop: "6px" } },
          "显示给局域网内其他设备的名称（最长 32 字符），留空则使用主机名。"),
      ));

      /* 关于 */
      put(paneLog, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" },
          "关于 LocalToolbox",
          App.h("span", { class: "tag accent", style: { marginLeft: "8px" } },
            "v" + ((App.state.info || {}).version || "?")),
        ),
        App.h("div", { class: "hint" },
          "产品定位：局域网设备互联与协作工具箱 —— 两台设备装上 LocalToolbox 即可互相发现、传文件、同步剪贴板、共享键鼠、发布共享服务。", App.h("br"),
          "发现端口：UDP 41890　·　剪贴板同步：TCP 41891　·　键鼠共享：TCP 41892　·　文件传输：TCP 41893", App.h("br"),
          "同步内容在局域网内明文传输，仅建议在可信网络（家庭 / 办公内网）中使用。", App.h("br"),
          "如被安全软件拦截广播或模拟输入，请将 LocalToolbox 加入白名单。",
        ),
      ));

      /* L3 页内分段页签：把长设置页按认知分组拆为互斥区块 */
      const nav = App.subnav([
        { label: "通用偏好", el: paneGeneral },
        { label: "快捷键与启动", el: paneHotkey },
        { label: "日志与关于", el: paneLog },
      ]);
      el.appendChild(nav);
      nav.panes.forEach((p) => el.appendChild(p));

      await refresh();
      await refreshLogCard();
    },

    show() { refresh(); refreshLogCard(); },
  });
})();
