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
                        full: "hotkey_full", pop: "hotkey_pop" };
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
        const r = await App.tryCall("cfg_set", "clip_encrypt", encTgl.checked);
        if (r.ok) {
          App.toast(encTgl.checked ? "已开启历史加密（新条目加密落盘）" : "已关闭历史加密", "ok");
        } else App.toast(r.err, "error");
      });
      put(paneGeneral, App.h("div", { class: "card" },
        App.h("div", { class: "card-title" }, "隐私"),
        App.row(
          App.h("label", { class: "switch" }, encTgl, App.h("span", { class: "track" }),
            "历史落盘加密（文本 AES-256-GCM，密钥由系统 DPAPI 保护）"),
        ),
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

      const uploadSel = App.h("select", { class: "input" },
        App.h("option", { value: "imgur" }, "Imgur（免费公共）"),
        App.h("option", { value: "custom" }, "自定义接口"),
      );
      uploadSel.value = (App.state.cfg || {}).upload_service || "imgur";
      uploadSel.addEventListener("change", async () => {
        if (await save("upload_service", uploadSel.value)) {
          App.toast("默认图床已设为 " + uploadSel.value, "ok");
        }
      });

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
        App.h("div", { class: "hint", style: { marginTop: "8px" } },
          "贴图（Pin to Screen）可将截图钉在桌面最上层；图床上传用于快速分享截图链接。"),
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
        class: "input", type: "text", maxLength: "32",
        placeholder: "留空使用主机名",
        value: String((App.state.cfg || {}).device_name_custom || ""),
        style: { width: "220px" },
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
