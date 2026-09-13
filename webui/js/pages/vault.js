/* 密码库页：主口令加密库（setup/解锁/条目管理/复制密码/生成器）。
   未解锁时条目一律不可见；复制密码按设置定时清除剪贴板。 */
(function () {
  "use strict";

  const state = {
    status: { exists: false, unlocked: false, count: 0 },
    entries: [],
    groups: [],
    query: "",
    curGroup: "",
    editing: null,   // 编辑中的条目（含明文）或 null
    genLen: 16,
    genSym: true,
  };
  let refs = {};
  let keyHandler = null;

  /* ---------------- 流程屏 ---------------- */
  function renderSetup() {
    refs.body.innerHTML = "";
    const p1 = App.h("input", { class: "input", type: "password", placeholder: "设置主口令", style: { width: "100%" } });
    const p2 = App.h("input", { class: "input", type: "password", placeholder: "再次输入主口令", style: { width: "100%" } });
    refs.body.appendChild(App.h("div", { class: "card", style: { maxWidth: "480px", margin: "40px auto", padding: "20px" } },
      App.h("div", { class: "card-title" }, "创建密码库"),
      App.h("div", { class: "hint", style: { marginBottom: "12px" } },
        "整个密码库用主口令派生密钥（PBKDF2 + AES-256-GCM）加密。", App.h("br"),
        App.h("span", { style: { color: "var(--warn)" } },
          "主口令遗忘将无法解锁（无任何后门）；建议配置 WebDAV 备份，历史备份即恢复途径。")),
      p1, App.h("div", { style: { height: "8px" } }), p2,
      App.h("div", { class: "row", style: { marginTop: "14px" } },
        App.h("button", {
          class: "btn primary", onclick: async function () {
            /* v5.1c：PBKDF2 派生耗时，busy 防连点（连按 Enter 会多次派生） */
            if (this._busy) return;
            if (!p1.value || p1.value !== p2.value) { App.toast("两次输入不一致或为空", "warn"); return; }
            this._busy = true; this.disabled = true;
            const old = this.textContent; this.textContent = "创建中…";
            try {
              const r = await App.tryCall("vault_setup", p1.value);
              if (!r.ok) { App.toast(r.err, "error"); return; }
              p1.value = p2.value = "";
              await refresh();
            } finally {
              this._busy = false; this.disabled = false; this.textContent = old;
            }
          },
        }, "创建密码库"),
      ),
    ));
  }

  function renderLocked() {
    refs.body.innerHTML = "";
    const pwd = App.h("input", {
      class: "input", type: "password", placeholder: "主口令",
      style: { maxWidth: "320px" },
      onkeydown: (e) => { if (e.key === "Enter") doUnlock(); },
    });
    const doUnlock = async function () {
      /* v5.1c：解锁派生耗时长，busy 防连点（按钮与 Enter 共用） */
      if (doUnlock._busy) return;
      doUnlock._busy = true;
      unlockBtn.disabled = true;
      const old = unlockBtn.textContent; unlockBtn.textContent = "解锁中…";
      try {
        const r = await App.tryCall("vault_unlock", pwd.value);
        if (!r.ok) { App.toast(r.err, "error"); return; }
        pwd.value = "";
        await refresh();
      } finally {
        doUnlock._busy = false;
        unlockBtn.disabled = false; unlockBtn.textContent = old;
      }
    };
    const unlockBtn = App.h("button", { class: "btn primary", onclick: doUnlock }, "解锁");
    refs.body.appendChild(App.h("div", { class: "card", style: { maxWidth: "480px", margin: "40px auto", padding: "20px" } },
      App.h("div", { class: "card-title" }, "密码库已锁定"),
      App.h("div", { class: "hint", style: { marginBottom: "12px" } },
        "输入主口令解锁。口令遗忘无法恢复，可用设置页「备份与恢复」从 WebDAV 历史备份找回。"),
      pwd,
      App.h("div", { class: "row", style: { marginTop: "14px" } },
        App.h("button", { class: "btn primary", onclick: doUnlock }, "解锁"),
      ),
    ));
  }

  /* ---------------- 导入 / 导出（v5.1，v5.1b 交互改进） ---------------- */
  function reportText(title, r) {
    let t = `${title}：格式 ${r.format}，导入 ${r.imported} 条，重复跳过 ${r.skipped_dup} 条，无效跳过 ${r.skipped_invalid} 条`;
    const errs = (r.errors || []).slice(0, 5).map((e) => `#${e.row} ${e.reason}`);
    if (errs.length) t += "；错误示例：" + errs.join("；");
    return t;
  }

  async function doImport(path, password) {
    /* v5.1c：导入执行期间禁止重入（merge 靠去重兜底，replace 会跑两遍） */
    if (doImport._busy) { App.toast("导入正在进行中", "warn"); return; }
    doImport._busy = true;
    try {
      await doImportInner(path, password);
    } finally {
      doImport._busy = false;
    }
  }

  async function doImportInner(path, password) {
    let pv;
    if (!path) {
      pv = await App.tryCall("vault_import_preview");
      if (!pv.ok) {
        if (pv.err !== "未选择文件。") App.toast(pv.err, "error", 6000);
        return;
      }
    } else {
      pv = await App.tryCall("vault_import_preview", path);
      if (!pv.ok) { App.toast(pv.err, "error", 6000); return; }
    }
    const d = pv.data;
    let pwd = password;
    if (d.need_password && !pwd) {
      const vals = await App.modal({
        title: "导入加密容器",
        inputs: [{ label: "容器口令", type: "password" }],
        okText: "解锁",
      });
      if (!vals) return;
      pwd = vals[0];
    }
    /* 导入方式由导入卡的下拉框选择（v5.1b：替代手输 replace） */
    const mode = refs.importModeSel && refs.importModeSel.value === "replace"
      ? "replace" : "merge";
    if (mode === "replace") {
      const ok = await App.confirm("替换导入确认",
        `将从备份导入 ${d.count} 条，密码库现有全部条目将被清空！确定继续？`);
      if (!ok) return;
    }
    const r = await App.tryCall("vault_import_exec", d.path, mode, pwd);
    if (!r.ok) { App.toast(r.err, "error", 8000); return; }
    App.modal({ title: "导入报告", body: reportText("导入完成", r.data), okText: "完成" });
    await refreshList();
  }

  async function exportFlow(fmt) {
    let password = null;
    if (fmt === "enc") {
      const vals = await App.modal({
        title: "加密导出（独立口令）",
        body: "生成独立口令加密的容器文件（与主库口令无关），可在任何一台机器导入恢复。",
        inputs: [
          { label: "导出口令", type: "password" },
          { label: "再次输入口令", type: "password" },
        ],
        okText: "下一步",
      });
      if (!vals) return;
      if (!vals[0] || vals[0] !== vals[1]) { App.toast("两次口令不一致或为空", "warn"); return; }
      password = vals[0];
    } else {
      const ok = await App.confirm("明文导出提醒",
        `将以未加密的 ${fmt.toUpperCase()} 文件保存全部密码（含明文密码），请务必妥善保管并及时删除。继续导出？`);
      if (!ok) return;
    }
    const r = await App.tryCall("vault_export", fmt, password);
    if (!r.ok) {
      if (r.err !== "已取消导出。") App.toast(r.err, "error", 8000);
      return;
    }
    const d = r.data;
    App.modal({
      title: "导出报告",
      body: `导出完成：${d.count} 条 · 格式 ${d.format.toUpperCase()}`
        + ` · ${d.encrypted ? "加密容器" : "明文文件"} · ${(d.size / 1024).toFixed(1)} KB`
        + ` · ${d.time}\n文件：${d.path}` + (d.encrypted ? "" : "\n注意：明文文件请妥善保管并及时删除。"),
      okText: "完成",
    });
  }

  /* ---------------- 安全设置（自动锁定 / 清剪贴板） ---------------- */
  async function showVaultSecurity() {
    const cfg = App.state.cfg || {};
    const autolock = App.h("input", {
      class: "input", type: "number", min: "0", max: "1440",
      value: String(cfg.vault_autolock_min != null ? cfg.vault_autolock_min : 15),
      style: { width: "90px" },
    });
    const clearSec = App.h("input", {
      class: "input", type: "number", min: "0", max: "600",
      value: String(cfg.vault_clip_clear_sec != null ? cfg.vault_clip_clear_sec : 30),
      style: { width: "90px" },
    });
    const ok = await App.modal({
      title: "密码库安全设置",
      body: App.h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } },
        App.h("div", { class: "row" },
          App.h("span", { class: "field-label" }, "空闲自动锁定（分钟，0 = 不锁）："), autolock),
        App.h("div", { class: "row" },
          App.h("span", { class: "field-label" }, "复制密码后清剪贴板（秒，0 = 不清）："), clearSec),
        App.h("div", { class: "hint" },
          "自动锁定由后端定时器执行；清剪贴板仅在剪贴板内容仍为该密码时清除。")),
      okText: "保存",
    });
    if (!ok) return;
    const r1 = await App.tryCall("cfg_set", "vault_autolock_min", parseInt(autolock.value, 10) || 0);
    if (!r1.ok) { App.toast(r1.err, "error"); return; }
    const r2 = await App.tryCall("cfg_set", "vault_clip_clear_sec", parseInt(clearSec.value, 10) || 0);
    if (!r2.ok) { App.toast(r2.err, "error"); return; }
    App.toast("安全设置已保存", "ok");
  }

  /* ---------------- 列表 ---------------- */
  function renderList() {
    refs.body.innerHTML = "";
    const search = App.h("input", {
      class: "input", placeholder: "搜索标题 / 用户名 / URL…",
      value: state.query, style: { flex: "1", minWidth: "0" },
      oninput: (e) => {
        clearTimeout(renderList._t);
        renderList._t = setTimeout(() => {
          state.query = e.target.value.trim();
          refreshList();
        }, 250);
      },
    });
    const groupSel = App.h("select", { class: "input", style: { width: "auto" }, onchange: (e) => { state.curGroup = e.target.value; refreshList(); } },
      App.h("option", { value: "" }, "全部分组"),
    );
    for (const g of state.groups) {
      const o = App.h("option", { value: g }, g);
      groupSel.appendChild(o);
      if (state.curGroup === g) groupSel.value = g;
    }
    /* v5.2 P4：工具行精简——改口令/锁定进「更多 ⋯」 */
    refs.body.appendChild(App.h("div", { class: "row" },
      search, groupSel,
      App.h("button", {
        class: "btn primary", onclick: () => {
          state.editing = { id: 0, title: "", username: "", password: "", url: "", note: "", group: state.curGroup || "" };
          renderEditor();
        },
      }, "+ 新增条目"),
      App.h("button", {
        class: "btn", title: "锁定与口令管理",
        onclick: (ev) => App.overflowMenu(ev.currentTarget, [
          { label: "安全设置（自动锁定 / 清剪贴板）", icon: "shield", onclick: showVaultSecurity },
          { label: "更改主口令（全库重加密）", icon: "key", onclick: showChangePwd },
          { label: "锁定密码库", icon: "shield",
            onclick: async () => {
              await App.tryCall("vault_lock");
              state.editing = null;
              await refresh();
            } },
        ]),
      }, "更多 ⋯"),
    ));

    /* 导入 / 导出（v5.2 P4：低频操作收进折叠区） */
    refs.importModeSel = App.h("select", { class: "input", style: { width: "auto" } },
      App.h("option", { value: "merge" }, "合并导入（跳过重复）"),
      App.h("option", { value: "replace" }, "替换导入（清空现有）"));
    refs.body.appendChild(App.sec(
      "导入 / 导出（Chrome · Edge · Firefox · Safari · Bitwarden · KeePass 的 CSV / JSON）",
      [App.h("div", { class: "row" },
        refs.importModeSel,
        App.h("button", { class: "btn sm", onclick: () => doImport() }, "导入密码文件"),
        App.h("button", { class: "btn sm", onclick: () => exportFlow("csv") }, "导出 CSV"),
        App.h("button", { class: "btn sm", onclick: () => exportFlow("json") }, "导出 JSON"),
        App.h("button", { class: "btn sm", onclick: () => exportFlow("enc") }, "加密导出"),
      )], { open: false }));

    /* 条目动作（v5.2 P4：行内只留「复制密码」，其余走右键菜单） */
    async function copyUser(e) {
      const r = await App.tryCall("vault_copy_username", e.id);
      if (r.ok) App.toast("用户名已复制");
    }
    async function copyPwd(e) {
      const r = await App.tryCall("vault_copy_password", e.id);
      if (!r.ok) { App.toast(r.err, "error"); return; }
      App.toast(r.data.clear_sec > 0
        ? `密码已复制，${r.data.clear_sec}s 后自动清除剪贴板`
        : "密码已复制", "ok", 4000);
    }
    async function editEntry(e) {
      const r = await App.tryCall("vault_entry", e.id);
      if (!r.ok) { App.toast(r.err, "error"); return; }
      state.editing = r.data;
      renderEditor();
    }
    async function delEntry(e) {
      const ok = await App.confirm(`删除条目「${e.title}」？`);
      if (!ok) return;
      const r = await App.tryCall("vault_delete", e.id);
      if (!r.ok) { App.toast(r.err, "error"); return; }
      await refreshList();
    }
    function entryMenu(e2, x, y) {
      App.contextMenu([
        { label: "复制密码", icon: "key", disabled: !e2.has_pwd, onclick: () => copyPwd(e2) },
        { label: "复制用户名", icon: "copy", disabled: !e2.username, onclick: () => copyUser(e2) },
        "sep",
        { label: "编辑", icon: "edit", onclick: () => editEntry(e2) },
        { label: "删除", icon: "trash", danger: true, onclick: () => delEntry(e2) },
      ], x, y);
    }

    const list = App.h("div", { class: "list", style: { flex: "1", overflowY: "auto", minHeight: "0", marginTop: "10px" } });
    if (!state.entries.length) {
      list.appendChild(App.h("div", { class: "empty" },
        state.query ? "无匹配条目" : "暂无密码条目\n点击「+ 新增条目」开始"));
    }
    for (const e of state.entries) {
      list.appendChild(App.h("div", { class: "card", style: { padding: "10px 14px", marginBottom: "8px" },
        oncontextmenu: (ev) => { ev.preventDefault(); entryMenu(e, ev.clientX, ev.clientY); } },
        App.h("div", { style: { display: "flex", alignItems: "center", gap: "8px" } },
          App.h("div", { style: { flex: "1", minWidth: "0" } },
            App.h("div", { style: { fontWeight: "600", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } },
              e.title || "(无标题)",
              e.group ? App.h("span", { class: "hint", style: { marginLeft: "8px", fontWeight: "400" } }, "#" + e.group) : null),
            App.h("div", { class: "hint mono", style: { marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } },
              [e.username, e.url].filter(Boolean).join(" · ") || "—")),
          e.has_pwd ? App.h("button", {
            class: "btn sm", onclick: () => copyPwd(e),
          }, "复制密码") : null,
        ),
      ));
    }
    refs.body.appendChild(list);
  }

  async function refreshList() {
    const r = await App.tryCall("vault_entries", state.query);
    if (!r.ok) { App.toast(r.err, "error"); state.status.unlocked = false; render(); return; }
    state.entries = r.data.entries || [];
    state.groups = r.data.groups || [];
    renderList();
  }

  /* ---------------- 编辑器 ---------------- */
  function renderEditor() {
    refs.body.innerHTML = "";
    const e = state.editing;
    const mk = (label, ph, type) => App.h("input", {
      class: "input", type: type || "text", placeholder: ph || label,
      value: e[labelKey(label)] || "",
    });
    const labelKey = (label) => ({ "标题": "title", "用户名": "username", "密码": "password", "URL": "url", "分组": "group", "备注": "note" }[label] || label);
    const fTitle = mk("标题");
    const fUser = mk("用户名");
    const fPwdWrap = App.h("div", { class: "row", style: { margin: "0" } });
    const fPwd = App.h("input", { class: "input", type: "password", placeholder: "密码", value: e.password || "", style: { flex: "1", minWidth: "0", fontFamily: "Consolas, monospace" } });
    /* v5.1b：显/隐切换（默认隐藏防旁人瞥屏） */
    const fPwdEye = App.h("button", {
      class: "btn sm", title: "显示 / 隐藏密码",
      onclick: () => {
        const hidden = fPwd.type === "password";
        fPwd.type = hidden ? "text" : "password";
        fPwdEye.textContent = hidden ? "隐藏" : "显示";
      },
    }, "显示");
    const genLen = App.h("input", { class: "input", type: "number", min: "8", max: "64", value: state.genLen, style: { width: "70px" } });
    const genBtn = App.h("button", {
      class: "btn sm", onclick: async () => {
        state.genLen = parseInt(genLen.value, 10) || 16;
        const r = await App.tryCall("vault_generate", state.genLen, state.genSym);
        if (r.ok) fPwd.value = r.data.password;
      },
    }, "生成");
    fPwdWrap.appendChild(fPwd);
    fPwdWrap.appendChild(fPwdEye);
    fPwdWrap.appendChild(genLen);
    fPwdWrap.appendChild(genBtn);
    const fUrl = mk("URL");
    const fGroup = App.h("input", { class: "input", placeholder: "分组（如 工作 / 邮箱，可任意填写）", value: e.group || "" });
    const groupDatalist = App.h("datalist", { id: "vault-groups" });
    for (const g of state.groups) groupDatalist.appendChild(App.h("option", { value: g }));
    fGroup.setAttribute("list", "vault-groups");
    const fNote = App.h("textarea", {
      class: "input", placeholder: "备注", rows: 4,
      style: { resize: "vertical", fontFamily: "inherit" },
    });
    fNote.value = e.note || "";

    const save = async () => {
      /* v5.1c：防连点——新增条目双击保存会创建两条重复条目 */
      if (save._busy) return;
      save._busy = true;
      try {
        const r = await App.tryCall("vault_save", e.id || 0,
          fTitle.value.trim(), fUser.value.trim(), fPwd.value,
          fUrl.value.trim(), fNote.value, fGroup.value.trim());
        if (!r.ok) { App.toast(r.err, "error"); return; }
        App.toast(e.id ? "已保存" : "已新增");
        state.editing = null;
        await refreshList();
      } finally {
        save._busy = false;
      }
    };

    refs.body.appendChild(App.h("div", { style: { display: "flex", flexDirection: "column", flex: "1", minHeight: "0", overflowY: "auto" } },
      App.h("div", { class: "row" },
        App.h("button", {
          class: "btn", onclick: () => { state.editing = null; renderList(); },
        }, "← 返回"),
        App.h("div", { class: "card-title", style: { margin: "0" } }, e.id ? "编辑条目" : "新增条目"),
      ),
      App.h("div", { class: "card", style: { marginTop: "10px", padding: "14px", maxWidth: "640px" } },
        App.h("div", { class: "field-label" }, "标题"), fTitle,
        App.h("div", { class: "field-label", style: { marginTop: "10px" } }, "用户名"), fUser,
        App.h("div", { class: "field-label", style: { marginTop: "10px" } }, "密码"), fPwdWrap,
        App.h("div", { class: "field-label", style: { marginTop: "10px" } }, "URL"), fUrl,
        App.h("div", { class: "field-label", style: { marginTop: "10px" } }, "分组"), fGroup, groupDatalist,
        App.h("div", { class: "field-label", style: { marginTop: "10px" } }, "备注"), fNote,
        App.h("div", { class: "row", style: { marginTop: "14px" } },
          App.h("button", { class: "btn primary", onclick: save }, "保存（Ctrl+S）"),
        ),
      ),
    ));
  }

  async function showChangePwd() {
    const vals = await App.modal({
      title: "更改主口令（全库重加密）",
      inputs: [
        { label: "当前主口令", type: "password" },
        { label: "新主口令", type: "password" },
      ],
      okText: "更改",
    });
    if (!vals || !vals[1]) return;
    const r = await App.tryCall("vault_change_password", vals[0], vals[1]);
    if (!r.ok) { App.toast(r.err, "error"); return; }
    App.toast("主口令已更改");
  }

  /* ---------------- 主渲染 ---------------- */
  function render() {
    if (!state.status.exists) { renderSetup(); return; }
    if (!state.status.unlocked) { renderLocked(); return; }
    if (state.editing) { renderEditor(); return; }
    renderList();
  }

  async function refresh() {
    const r = await App.tryCall("vault_status");
    if (r.ok) state.status = r.data;
    render();
    if (state.status.unlocked && !state.editing) await refreshList();
  }

  App.registerPage({
    id: "vault",
    title: "密码库",
    icon: "key",
    group: "笔记与安全",

    async mount(el) {
      el.innerHTML = "";
      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "密码库"),
        App.h("div", { class: "sub" },
          "主口令加密（PBKDF2 + AES-256-GCM），WebDAV 云备份跨机恢复（设置 → 备份与恢复）；口令遗忘不可解锁"),
      ));
      /* v5.1b：页面显隐由 .page/.page.active 类控制，全高布局用 page-flex
         类（禁止在 .page 元素上设内联 display，会覆盖显隐切换） */
      el.classList.add("page-flex");
      refs.body = App.h("div", {
        style: { display: "flex", flexDirection: "column", flex: "1", minHeight: "0" },
      });
      el.appendChild(refs.body);

      keyHandler = (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && state.editing) {
          e.preventDefault();
          const btn = refs.body.querySelector(".btn.primary");
          if (btn) btn.click();
        }
      };
      el.addEventListener("keydown", keyHandler);

      await refresh();
    },
  });
})();
