/* 防火墙页（v5.5）：配置文件开关 / 规则管理（搜索·筛选·分页·右键菜单） /
 * 一键拦截应用 / 锁定模式 / 备份恢复 / 操作记录。
 * 后端：fw_*（app/bridge/firewall_api.py → app/core/firewall.py，
 * PowerShell + INetFwPolicy2 COM，写操作需管理员：右下角「提权重启」）。
 */
(function () {
  "use strict";

  const PROF_LABEL = { domain: "域网络", private: "专用网络", public: "公用网络" };
  const DIR_LABEL = { in: "入站", out: "出站" };
  const ACT_LABEL = { allow: "允许", block: "阻止" };
  const PAGE_SIZE = 50;

  App.registerPage({
    id: "firewall",
    title: "防火墙",
    icon: "shield",
    group: "系统",

    mount(el) {
      const refs = {};
      const state = { admin: false, profiles: [], rules: [], blocked: [],
                      backups: [], audit: [] };
      let filtered = [];
      let page = 1;
      const filters = { q: "", dir: "", act: "", enabled: "" };

      /* ---------------- 配置文件卡片 ---------------- */
      function renderProfiles() {
        refs.profBox.replaceChildren();
        for (const p of state.profiles) {
          const btn = App.h("button", {
            class: "btn sm", style: { flex: "none" }, disabled: !state.admin,
            title: state.admin ? "" : "需管理员权限（点右下角「提权重启」）",
            onclick: async function () {
              if (this._busy) return;
              this._busy = true; this.disabled = true;
              const old = this.textContent;
              this.textContent = p.enabled ? "关闭中…" : "开启中…";
              try {
                const r = await App.tryCall("fw_profile_set", p.id, !p.enabled);
                if (!r.ok) { App.toast(r.err, "error", 6000); return; }
                App.toast(`已${p.enabled ? "关闭" : "启用"}${PROF_LABEL[p.name]}防火墙`, "ok");
                refresh();
              } finally {
                this._busy = false; this.disabled = false; this.textContent = old;
              }
            },
          }, p.enabled ? "关闭" : "启用");
          refs.profBox.appendChild(App.h("div", { class: "list-item" },
            App.h("span", { class: "li-main" },
              App.h("div", { class: "li-title" }, PROF_LABEL[p.name]),
              App.h("div", { class: "li-sub" },
                `默认策略：入站 ${ACT_LABEL[p.inbound]} · 出站 ${ACT_LABEL[p.outbound]}`)),
            p.enabled ? App.statusTag("启用", "ok", "check") : App.statusTag("已关闭", "warn"),
            btn,
          ));
        }
        if (!state.profiles.length) {
          refs.profBox.appendChild(App.h("div", { class: "empty" },
            "未读取到防火墙配置文件\n请确认系统 Windows Defender 防火墙服务（mpssvc）正在运行"));
        }
      }

      function renderLockdown() {
        const outBlock = state.profiles.every((p) => p.outbound === "block");
        const btn = refs.lockBtn;
        btn.textContent = outBlock ? "解除锁定" : "开启锁定";
        refs.lockStatus.replaceChildren(
          outBlock ? App.statusTag("已锁定（出站默认拒绝）", "danger", "shield")
                   : App.statusTag("未锁定", "ok"));
      }

      /* ---------------- 拦截卡片 ---------------- */
      function renderBlocked() {
        refs.blockBox.replaceChildren();
        if (!state.blocked.length) {
          refs.blockBox.appendChild(App.h("div", { class: "empty" },
            "暂无本工具创建的拦截规则\n点「选择程序」一键禁止某个应用联网（入站+出站）"));
          return;
        }
        for (const r of state.blocked) {
          refs.blockBox.appendChild(App.h("div", { class: "list-item" },
            App.h("span", { class: "li-main", style: { minWidth: "0" } },
              App.h("div", { class: "li-title mono", style: { wordBreak: "break-all" } },
                r.app || r.name),
              App.h("div", { class: "li-sub" }, DIR_LABEL[r.dir] || r.dir)),
            App.h("button", {
              class: "btn sm", style: { flex: "none" },
              onclick: async function () {
                if (this._busy) return;
                if (!(await App.confirm("解除拦截",
                  `将删除拦截规则「${r.name}」，恢复该程序联网。继续？`))) return;
                this._busy = true; this.disabled = true;
                try {
                  const d = await App.tryCall("fw_rule_delete", r.name);
                  if (!d.ok) { App.toast(d.err, "error", 6000); return; }
                  App.toast("已解除拦截", "ok");
                  refresh();
                } finally { this._busy = false; this.disabled = false; }
              },
            }, "解除"),
          ));
        }
      }

      /* ---------------- 规则列表 ---------------- */
      function applyFilter() {
        const q = filters.q.trim().toLowerCase();
        filtered = state.rules.filter((r) => {
          if (filters.dir && r.dir !== filters.dir) return false;
          if (filters.act && r.action !== filters.act) return false;
          if (filters.enabled === "on" && !r.enabled) return false;
          if (filters.enabled === "off" && r.enabled) return false;
          if (q) {
            const hay = (r.name + " " + r.app + " " + r.lports + " " + r.rports)
              .toLowerCase();
            if (!hay.includes(q)) return false;
          }
          return true;
        });
        page = 1;
      }

      function ruleRow(r) {
        const toggleBtn = App.h("button", {
          class: "btn xs", style: { flex: "none" }, disabled: !state.admin,
          title: state.admin ? "" : "需管理员权限（点右下角「提权重启」）",
          onclick: async function () {
            if (this._busy) return;
            this._busy = true; this.disabled = true; this.textContent = "…";
            try {
              const d = await App.tryCall("fw_rule_toggle", r.name, !r.enabled);
              if (!d.ok) { App.toast(d.err, "error", 6000); return; }
              refresh();
            } finally { this._busy = false; this.disabled = false; }
          },
        }, r.enabled ? "停用" : "启用");
        const row = App.h("div", {
          class: "list-item",
          oncontextmenu: (e) => {
            e.preventDefault();
            App.contextMenu([
              { label: r.enabled ? "停用规则" : "启用规则", icon: "zap",
                disabled: !state.admin,
                onclick: () => {
                  App.tryCall("fw_rule_toggle", r.name, !r.enabled).then((d) => {
                    if (!d.ok) { App.toast(d.err, "error", 6000); return; }
                    refresh();
                  });
                } },
              "sep",
              { label: "删除规则", icon: "trash", danger: true, disabled: !state.admin,
                onclick: async () => {
                  if (!(await App.confirm("删除防火墙规则",
                    `将永久删除规则「${r.name}」，删除后需重新创建。继续？`))) return;
                  const d = await App.tryCall("fw_rule_delete", r.name);
                  if (!d.ok) { App.toast(d.err, "error", 6000); return; }
                  App.toast("规则已删除", "ok");
                  refresh();
                } },
            ], e.clientX, e.clientY);
          },
        },
          App.h("span", { class: "li-main", style: { minWidth: "0" } },
            App.h("div", { class: "li-title", style: { wordBreak: "break-all" } }, r.name),
            App.h("div", { class: "li-sub mono", style: { fontSize: "10.5px",
              wordBreak: "break-all" } },
              [r.app || "任意程序",
                r.lports ? "本地端口 " + r.lports : "",
                r.rports ? "远程端口 " + r.rports : ""]
                .filter(Boolean).join(" · "))),
          App.h("span", { class: "tag", style: { flex: "none" } }, DIR_LABEL[r.dir]),
          r.action === "block"
            ? App.h("span", { class: "tag danger", style: { flex: "none" } }, "阻止")
            : App.h("span", { class: "tag accent", style: { flex: "none" } }, "允许"),
          r.enabled ? App.statusTag("启用", "ok") : App.statusTag("已停用"),
          toggleBtn,
        );
        return row;
      }

      function renderRules() {
        refs.ruleBox.replaceChildren();
        if (!filtered.length) {
          refs.ruleBox.appendChild(App.h("div", { class: "empty" },
            (state.rules.length ? "没有匹配当前筛选条件的规则"
              : "未读取到防火墙规则") + "\n调整搜索关键字或筛选条件后重试"));
          const total = App.h("span", { class: "hint" },
            `共 ${filtered.length} 条`);
          refs.ruleFoot.replaceChildren(total);
          return;
        }
        const start = (page - 1) * PAGE_SIZE;
        const slice = filtered.slice(start, start + PAGE_SIZE);
        for (const r of slice) refs.ruleBox.appendChild(ruleRow(r));
        const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
        refs.ruleFoot.replaceChildren(
          App.h("button", {
            class: "btn sm", disabled: page <= 1,
            onclick: () => { page--; renderRules(); },
          }, "上一页"),
          App.h("span", { class: "hint" },
            `${start + 1}-${start + slice.length} / 共 ${filtered.length} 条 · 第 ${page}/${pages} 页`),
          App.h("button", {
            class: "btn sm", disabled: page >= pages,
            onclick: () => { page++; renderRules(); },
          }, "下一页"),
        );
      }

      /* ---------------- 主刷新 ---------------- */
      let refreshing = false;
      async function refresh() {
        if (refreshing) return;
        refreshing = true;
        refs.status.textContent = "读取中…";
        const r = await App.tryCall("fw_overview");
        refreshing = false;
        if (!r.ok) {
          refs.status.textContent = "";
          refs.profBox.replaceChildren(App.h("div", { class: "empty" },
            "读取失败：" + (r.err || "未知错误") + "\n可点「刷新」重试"));
          return;
        }
        Object.assign(state, {
          admin: !!r.data.admin,
          profiles: r.data.profiles || [],
          rules: r.data.rules || [],
          blocked: r.data.blocked || [],
          backups: r.data.backups || [],
          audit: r.data.audit || [],
        });
        refs.adminTip.style.display = state.admin ? "none" : "";
        refs.status.textContent =
          `共 ${state.rules.length} 条规则 · ${state.blocked.length} 条拦截`;
        renderProfiles();
        renderLockdown();
        renderBlocked();
        applyFilter();
        renderRules();
      }

      /* ---------------- 弹窗：新建高级规则 ---------------- */
      function openCreateModal() {
        const f = {};
        f.name = App.h("input", { class: "input", placeholder: "规则名（自动加 LocalToolbox· 前缀）" });
        f.program = App.h("input", { class: "input mono", placeholder: "留空 = 任意程序" });
        f.dir = App.h("select", { class: "input" },
          App.h("option", { value: "out" }, "出站"),
          App.h("option", { value: "in" }, "入站"));
        f.act = App.h("select", { class: "input" },
          App.h("option", { value: "block" }, "阻止"),
          App.h("option", { value: "allow" }, "允许"));
        f.proto = App.h("select", { class: "input" },
          ["Any", "TCP", "UDP", "ICMPv4", "ICMPv6"].map((v) =>
            App.h("option", { value: v }, v === "Any" ? "任意协议" : v)));
        f.lp = App.h("input", { class: "input mono", placeholder: "如 80,443 或 5000-6000（可留空）" });
        f.rp = App.h("input", { class: "input mono", placeholder: "可留空" });
        f.ra = App.h("input", { class: "input mono", placeholder: "IP / CIDR，逗号分隔（可留空）" });
        const pfs = {};
        const profRow = App.h("div", { class: "row" },
          ...["domain", "private", "public"].map((k) => {
            pfs[k] = App.h("input", { type: "checkbox", checked: true });
            return App.h("label", { class: "chk" }, pfs[k], " " + PROF_LABEL[k]);
          }));
        const form = App.h("div", null,
          App.h("div", { class: "field-label" }, "规则名"), f.name,
          App.h("div", { class: "field-label", style: { marginTop: "8px" } }, "程序路径"),
          f.program,
          App.h("div", { class: "row", style: { marginTop: "8px" } },
            App.h("div", { style: { flex: "1" } },
              App.h("div", { class: "field-label" }, "方向"), f.dir),
            App.h("div", { style: { flex: "1" } },
              App.h("div", { class: "field-label" }, "动作"), f.act),
            App.h("div", { style: { flex: "1" } },
              App.h("div", { class: "field-label" }, "协议"), f.proto)),
          App.h("div", { class: "field-label", style: { marginTop: "8px" } }, "本地端口"), f.lp,
          App.h("div", { class: "field-label", style: { marginTop: "8px" } }, "远程端口"), f.rp,
          App.h("div", { class: "field-label", style: { marginTop: "8px" } }, "远程地址"), f.ra,
          App.h("div", { class: "field-label", style: { marginTop: "8px" } }, "应用到的网络"), profRow,
          App.h("p", { class: "hint", style: { margin: "8px 0 0" } },
            "仅 TCP/UDP 可指定端口；端口填错会在保存时给出中文提示。"),
        );
        App.modal({ title: "新建防火墙规则", body: form, okText: "创建" })
          .then(async (ok) => {
            if (!ok) return;
            if (!(f.name.value || "").trim()) {
              App.toast("请填写规则名", "warn"); return;
            }
            const r = await App.tryCall("fw_rule_create", {
              name: f.name.value.trim(),
              program: f.program.value.trim(),
              direction: f.dir.value,
              action: f.act.value,
              proto: f.proto.value,
              lports: f.lp.value.trim(),
              rports: f.rp.value.trim(),
              raddrs: f.ra.value.trim(),
              profiles: Object.entries(pfs).filter(([, el]) => el.checked)
                .map(([k]) => k),
            });
            if (!r.ok) { App.toast(r.err, "error", 8000); return; }
            App.toast(`规则已创建：${r.data.name}`, "ok", 6000);
            refresh();
          });
      }

      /* ---------------- 弹窗：恢复备份 ---------------- */
      function openRestoreModal() {
        if (!state.backups.length) {
          App.toast("暂无备份，请先「立即备份」", "warn"); return;
        }
        const sel = App.h("select", { class: "input" },
          state.backups.map((b) => App.h("option", { value: b.path },
            `${b.name}（${App.fmtBytes(b.size)}）`)));
        const form = App.h("div", null,
          App.h("div", { class: "field-label" }, "选择备份"), sel,
          App.h("p", { class: "hint", style: { margin: "8px 0 0", color: "var(--danger)" } },
            "⚠ 恢复会用备份内容覆盖当前全部防火墙规则！"),
        );
        App.modal({ title: "从备份恢复防火墙", body: form, okText: "恢复" })
          .then(async (ok) => {
            if (!ok) return;
            const path = sel.value;
            if (!(await App.confirm("确认恢复",
              `将用「${sel.selectedOptions[0].textContent}」覆盖当前防火墙规则，无法撤销。确定继续？`))) {
              return;
            }
            const r = await App.tryCall("fw_backup_restore", path);
            if (!r.ok) { App.toast(r.err, "error", 8000); return; }
            App.toast("防火墙已恢复到所选备份", "ok");
            refresh();
          });
      }

      /* ---------------- 弹窗：操作记录 ---------------- */
      function openAuditModal() {
        const rows = state.audit.length
          ? state.audit.map((a) => App.h("div", { class: "list-item" },
              App.h("span", { class: "li-main" },
                App.h("div", { class: "li-title" }, a.detail || a.action),
                App.h("div", { class: "li-sub" }, a.action)),
              App.h("span", { class: "hint mono" }, App.fmtDate(a.ts))))
          : [App.h("div", { class: "empty" }, "暂无操作记录\n启停规则 / 拦截 / 备份等操作会记录在这里")];
        App.modal({ title: "防火墙操作记录（最近 50 条）", body: App.h("div", { class: "list" }, rows) });
      }

      /* ---------------- 布局 ---------------- */
      refs.adminTip = App.h("span", {
        class: "hint", style: { color: "var(--warn)", display: "none" },
      }, "⚠ 未以管理员运行：写入类操作不可用（可点右下角「提权重启」一次性提权）");
      refs.status = App.h("span", { class: "hint" });
      refs.profBox = App.h("div", { class: "list" });

      const lockBtn = App.h("button", { class: "btn sm danger", onclick: async function () {
        if (this._busy) return;
        if (!state.admin) { App.toast("开启锁定模式需要管理员权限（点右下角「提权重启」）", "warn"); return; }
        const outBlock = state.profiles.every((p) => p.outbound === "block");
        if (outBlock) {
          if (!(await App.confirm("解除锁定",
            "将把所有配置文件的出站默认策略恢复为「允许」。继续？"))) return;
        } else {
          if (!(await App.confirm("⚠ 开启锁定模式",
            "将把所有配置文件的出站默认策略改为「阻止」：\n未获放行的程序将无法访问网络！\n" +
            "开启前会自动备份防火墙（.wfw）。确定继续？"))) return;
        }
        this._busy = true; this.disabled = true; this.textContent = "切换中…";
        try {
          const r = await App.tryCall("fw_lock_set", !outBlock);
          if (!r.ok) { App.toast(r.err, "error", 8000); return; }
          App.toast(outBlock ? "已解除锁定" : "已开启锁定模式", "ok");
          refresh();
        } finally { this._busy = false; this.disabled = false; }
      } }, "开启锁定");
      refs.lockBtn = lockBtn;
      refs.lockStatus = App.h("span");

      const pickBtn = App.h("button", {
        class: "btn primary", onclick: async function () {
          if (this._busy) return;
          this._busy = true; this.disabled = true; this.textContent = "选择中…";
          try {
            const r = await App.tryCall("fw_pick_program");
            if (!r.ok) { App.toast(r.err, "error"); return; }
            const path = (r.data || [])[0];
            if (!path) return;
            const d = await App.tryCall("fw_block_app", path);
            if (!d.ok) { App.toast(d.err, "error", 8000); return; }
            if (d.data && d.data.already) App.toast("该程序已在拦截列表中", "info", 4000);
            else App.toast("已禁止该程序联网（入站+出站）", "ok");
            refresh();
          } finally { this._busy = false; this.disabled = false; this.textContent = "选择程序拦截"; }
        },
      }, "选择程序拦截");
      const unblockAllBtn = App.h("button", {
        class: "btn danger", onclick: async function () {
          if (this._busy) return;
          if (!state.blocked.length) { App.toast("当前没有本工具创建的拦截规则", "info"); return; }
          if (!(await App.confirm("全部解除拦截",
            `将移除本工具创建的全部 ${state.blocked.length} 条拦截规则（移除前自动备份）。继续？`))) {
            return;
          }
          this._busy = true; this.disabled = true; this.textContent = "移除中…";
          try {
            const r = await App.tryCall("fw_unblock_all");
            if (!r.ok) { App.toast(r.err, "error", 8000); return; }
            App.toast(`已移除 ${r.data.removed} 条拦截规则`, "ok");
            refresh();
          } finally { this._busy = false; this.disabled = false; this.textContent = "全部解除"; }
        },
      }, "全部解除");

      const searchIn = App.h("input", { class: "input", type: "search",
        placeholder: "搜索规则名 / 程序 / 端口…",
        oninput: () => { filters.q = searchIn.value; applyFilter(); renderRules(); } });
      const dirSel = App.h("select", { class: "input", style: { width: "auto", flex: "none" },
        onchange: () => { filters.dir = dirSel.value; applyFilter(); renderRules(); } },
        App.h("option", { value: "" }, "方向全部"),
        App.h("option", { value: "in" }, "入站"),
        App.h("option", { value: "out" }, "出站"));
      const actSel = App.h("select", { class: "input", style: { width: "auto", flex: "none" },
        onchange: () => { filters.act = actSel.value; applyFilter(); renderRules(); } },
        App.h("option", { value: "" }, "动作全部"),
        App.h("option", { value: "allow" }, "允许"),
        App.h("option", { value: "block" }, "阻止"));
      const enSel = App.h("select", { class: "input", style: { width: "auto", flex: "none" },
        onchange: () => { filters.enabled = enSel.value; applyFilter(); renderRules(); } },
        App.h("option", { value: "" }, "状态全部"),
        App.h("option", { value: "on" }, "启用"),
        App.h("option", { value: "off" }, "已停用"));
      const moreBtn = App.h("button", { class: "btn sm", onclick: (e) => {
        App.overflowMenu(moreBtn, [
          { label: "刷新", icon: "zap", onclick: refresh },
          { label: "新建高级规则", icon: "filetext", disabled: !state.admin,
            onclick: openCreateModal },
          "sep",
          { label: "立即备份", icon: "copy", disabled: !state.admin, onclick: async () => {
            const r = await App.tryCall("fw_backup_now");
            if (!r.ok) { App.toast(r.err, "error", 8000); return; }
            App.toast(`已备份：${r.data.name}`, "ok", 6000);
            refresh();
          } },
          { label: "从备份恢复…", icon: "clock", disabled: !state.admin,
            onclick: openRestoreModal },
          { label: "操作记录…", icon: "list", onclick: openAuditModal },
        ]);
      } }, "更多 ⋯");

      refs.ruleBox = App.h("div", { class: "list" });
      refs.ruleFoot = App.h("div", { class: "row",
        style: { marginTop: "8px", justifyContent: "center", gap: "10px" } });

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "防火墙"),
        App.h("div", { class: "sub" },
          "Windows Defender 防火墙管理：配置文件开关、规则启停删除、一键拦截应用联网"),
      ));
      el.appendChild(App.h("div", { class: "row", style: { marginBottom: "10px" } },
        refs.adminTip, App.h("span", { class: "grow" }), refs.status));

      el.appendChild(App.h("div", { class: "card", style: { padding: "10px 14px" } },
        App.h("div", { class: "card-title" }, "网络配置文件"),
        refs.profBox,
        App.h("div", { class: "list-item", style: { marginTop: "4px" } },
          App.h("span", { class: "li-main" },
            App.h("div", { class: "li-title" }, "锁定模式（出站默认拒绝）"),
            App.h("div", { class: "li-sub" },
              "未获放行的程序将无法主动访问网络，仅建议高级用户使用")),
          refs.lockStatus, lockBtn),
      ));

      el.appendChild(App.h("div", { class: "card",
        style: { padding: "10px 14px", marginTop: "10px" } },
        App.h("div", { class: "row", style: { flexWrap: "wrap" } },
          App.h("div", { class: "card-title", style: { margin: "0" } }, "一键拦截应用联网"),
          App.h("span", { class: "grow" }), pickBtn, unblockAllBtn),
        App.h("div", { style: { marginTop: "8px" } }, refs.blockBox = App.h("div", { class: "list" })),
      ));

      el.appendChild(App.h("div", { class: "card",
        style: { padding: "10px 14px", marginTop: "10px" } },
        App.h("div", { class: "row", style: { flexWrap: "wrap" } },
          searchIn, dirSel, actSel, enSel, moreBtn),
        App.h("div", { style: { marginTop: "8px" } }, refs.ruleBox),
        refs.ruleFoot,
      ));

      renderProfiles();
      renderBlocked();
      refresh();
    },
  });
})();
