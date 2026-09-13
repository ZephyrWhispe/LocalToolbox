"""防火墙管理桥接层（v5.5）：配置文件开关 / 规则启停删除 / 一键拦截 / 备份恢复。"""

import webview

from .base import BridgeBase


class FirewallApi(BridgeBase):
    def _init_firewall(self):
        pass

    # -- 读取 -----------------------------------------------------------
    def fw_overview(self):
        """全量状态（配置文件 + 规则 + 本工具拦截项 + 备份列表 + 操作记录）。"""
        from ..core import firewall as fw
        try:
            st = fw.enum_state()
            st["blocked"] = fw.blocked_rules(st["rules"])
            st["backups"] = fw.backup_list()
            st["audit"] = fw.audit_list(50)
            return {"ok": True, "data": st}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def fw_pick_program(self):
        """选择 exe 程序文件（一键拦截用）。"""
        if self._window is None:
            return {"ok": False, "err": "窗口尚未就绪。"}
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, directory="")
            return {"ok": True, "data": list(result or [])}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 配置文件 -------------------------------------------------------
    def fw_profile_set(self, profile_id, enabled):
        from ..core import firewall as fw
        return fw.set_profile_enabled(profile_id, bool(enabled))

    def fw_default_set(self, profile_id, direction, action):
        from ..core import firewall as fw
        return fw.set_default_action(profile_id, direction, action)

    def fw_lock_set(self, on):
        """锁定模式：所有配置文件的出站默认动作 Block/Allow（开启前自动备份）。"""
        from ..core import firewall as fw
        if on:
            bk = fw.backup_now()
            if not bk.get("ok"):
                return bk
            data = bk.get("data") or {}
            fw.audit_add("lock_on", "备份：%s" % data.get("name", ""))
        results = []
        for pid in (1, 2, 4):
            r = fw.set_default_action(pid, "out", "block" if on else "allow")
            if not r.get("ok"):
                return {"ok": False, "err": r.get("err")}
            results.append(r)
        if not on:
            fw.audit_add("lock_off", "")
        return {"ok": True, "data": {"on": bool(on),
                                     "backup": (bk.get("data") if on else None)}}

    # -- 规则 -----------------------------------------------------------
    def fw_rule_toggle(self, name, enabled):
        from ..core import firewall as fw
        return fw.toggle_rule(name, enabled)

    def fw_rule_delete(self, name):
        from ..core import firewall as fw
        return fw.delete_rule(name)

    def fw_rule_create(self, spec):
        from ..core import firewall as fw
        return fw.create_rule(spec)

    def fw_block_app(self, program):
        from ..core import firewall as fw
        return fw.block_app(program)

    def fw_unblock_all(self):
        from ..core import firewall as fw
        return fw.unblock_all()

    # -- 备份 / 恢复 ----------------------------------------------------
    def fw_backup_list(self):
        from ..core import firewall as fw
        return {"ok": True, "data": fw.backup_list()}

    def fw_backup_now(self):
        from ..core import firewall as fw
        return fw.backup_now()

    def fw_backup_restore(self, path):
        from ..core import firewall as fw
        return fw.restore_backup(path)
