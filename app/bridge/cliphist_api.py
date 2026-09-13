"""剪贴板历史桥接层。"""

import base64

from .base import BridgeBase


class ClipHistApi(BridgeBase):
    def _init_cliphist(self):
        self._clip_monitor = None
        if self.cfg.get("cliphist_enabled", False):
            self._start_clip_monitor()

    def _start_clip_monitor(self):
        if self._clip_monitor and self._clip_monitor.running:
            return
        from ..core.clip_monitor import ClipboardMonitor
        limit = int(self.cfg.get("cliphist_limit", 500))
        self._clip_monitor = ClipboardMonitor(
            limit=limit,
            monitor_images=self.cfg.get("cliphist_monitor_images", True))
        self._clip_monitor.start(on_change=lambda: self.emit("cliphist_changed"))

    def cliphist_start(self):
        self._start_clip_monitor()
        self.cfg.set("cliphist_enabled", True)
        return {"ok": True}

    def cliphist_stop(self):
        if self._clip_monitor:
            self._clip_monitor.stop()
        self.cfg.set("cliphist_enabled", False)
        return {"ok": True}

    def cliphist_get_state(self):
        running = bool(self._clip_monitor and self._clip_monitor.running)
        return {"ok": True, "data": {"running": running}}

    def cliphist_list(self, limit=50, offset=0, query="", kind="", group_id=None):
        if not self._clip_monitor:
            return {"ok": True, "data": []}
        gid = None
        try:
            if group_id not in (None, "", "all"):
                gid = int(group_id)
        except (TypeError, ValueError):
            gid = None
        entries = self._clip_monitor.list_entries(
            int(limit), int(offset), str(query or ""), str(kind or ""), gid)
        return {"ok": True, "data": entries}

    def cliphist_groups(self):
        if not self._clip_monitor:
            return {"ok": True, "data": []}
        try:
            return {"ok": True, "data": self._clip_monitor.groups()}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def cliphist_group_add(self, name):
        if not self._clip_monitor:
            return {"ok": False, "err": "监控未启动"}
        try:
            gid = self._clip_monitor.group_add(name)
            return {"ok": True, "data": {"id": gid, "groups": self._clip_monitor.groups()}}
        except ValueError as e:
            return {"ok": False, "err": str(e)}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def cliphist_group_delete(self, group_id):
        if not self._clip_monitor:
            return {"ok": False, "err": "监控未启动"}
        try:
            self._clip_monitor.group_delete(int(group_id or 0))
            return {"ok": True, "data": self._clip_monitor.groups()}
        except (TypeError, ValueError) as e:
            return {"ok": False, "err": "分组 id 不合法"}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def cliphist_group_set(self, ids, group_id):
        """把所选条目移入分组（group_id=0 移出）。"""
        if not self._clip_monitor:
            return {"ok": False, "err": "监控未启动"}
        try:
            n = self._clip_monitor.group_set(ids, group_id)
            return {"ok": True, "data": {"count": n}}
        except (TypeError, ValueError) as e:
            return {"ok": False, "err": "参数不合法"}

    # -- 自定义命令（v5.4 O4，CopyQ 命令模板的受限子集：≤5 条，{text} 变量） --
    def _clip_cmds(self):
        cmds = self.cfg.get("clip_cmds")
        return cmds if isinstance(cmds, list) else []

    def clip_cmds_get(self):
        return {"ok": True, "data": self._clip_cmds()}

    def clip_cmds_set(self, cmds):
        """整表保存命令模板（≤5 条，每条 {name, cmd}，cmd 可含 {text} 变量）。"""
        if not isinstance(cmds, list):
            return {"ok": False, "err": "命令模板格式不正确"}
        if len(cmds) > 5:
            return {"ok": False, "err": "自定义命令最多 5 条"}
        cleaned = []
        for i, c in enumerate(cmds):
            if not isinstance(c, dict):
                return {"ok": False, "err": "第 %d 条命令格式不正确" % (i + 1)}
            name = str(c.get("name") or "").strip()
            cmd = str(c.get("cmd") or "").strip()
            if not name or not cmd:
                return {"ok": False, "err": "第 %d 条命令缺少名称或命令体" % (i + 1)}
            cleaned.append({"name": name[:30], "cmd": cmd[:500]})
        self.cfg.set("clip_cmds", cleaned)
        return {"ok": True, "data": cleaned}

    def clip_exec_cmd(self, index, entry_id):
        """对文本条目执行第 index 条命令模板：{text} 替换为条目文本（≤2000 字符）。

        注意：命令为用户自行定义，执行风险由用户自担；这里仅做拼接与启动。
        """
        cmds = self._clip_cmds()
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return {"ok": False, "err": "命令序号不合法"}
        if idx < 0 or idx >= len(cmds):
            return {"ok": False, "err": "命令不存在，请刷新列表"}
        if not self._clip_monitor:
            return {"ok": False, "err": "监控未启动"}
        try:
            entry = self._clip_monitor.get_entry(int(entry_id))
        except (TypeError, ValueError):
            entry = None
        if not entry:
            return {"ok": False, "err": "条目不存在，可能已被删除"}
        if entry.get("kind") != "text" or not entry.get("text"):
            return {"ok": False, "err": "自定义命令仅支持文本条目"}
        text = str(entry["text"])[:2000]
        cmd = str(cmds[idx]["cmd"]).replace("{text}", text)
        try:
            import subprocess
            subprocess.Popen(
                cmd, shell=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except Exception as e:
            return {"ok": False, "err": "命令启动失败：%s" % e}
        return {"ok": True, "data": {"name": cmds[idx]["name"]}}

    def cliphist_merge_copy(self, ids):
        """v5.2：按传入顺序合并文本条目写入剪贴板（\\n 连接）；图片条目跳过。"""
        if not self._clip_monitor:
            return {"ok": False, "err": "监控未启动"}
        texts = []
        for i in (ids or []):
            try:
                e = self._clip_monitor.get_entry(int(i))
            except (TypeError, ValueError):
                e = None
            if e and e["kind"] == "text" and e["text"]:
                texts.append(e["text"])
        if not texts:
            return {"ok": False, "err": "没有可合并的文本条目"}
        try:
            import pyperclip
            pyperclip.copy("\n".join(texts))
        except Exception as e:
            return {"ok": False, "err": "写入剪贴板失败：%s" % e}
        return {"ok": True, "data": {"count": len(texts)}}

    def cliphist_dedup(self):
        """合并相同内容的文本条目（每组保留一条：置顶优先，其次最新）。"""
        if not self._clip_monitor:
            return {"ok": False, "err": "监控未启动"}
        try:
            n = self._clip_monitor.dedup()
            return {"ok": True, "data": {"removed": n}}
        except Exception as e:
            return {"ok": False, "err": "合并失败：%s" % e}

    def cliphist_delete(self, entry_id):
        if not self._clip_monitor:
            return {"ok": False, "err": "监控未启动"}
        self._clip_monitor.delete_entry(int(entry_id))
        return {"ok": True}

    def cliphist_pin(self, entry_id, pinned=True):
        if not self._clip_monitor:
            return {"ok": False, "err": "监控未启动"}
        self._clip_monitor.pin_entry(int(entry_id), bool(pinned))
        return {"ok": True}

    def cliphist_clear(self):
        if not self._clip_monitor:
            return {"ok": False, "err": "监控未启动"}
        self._clip_monitor.clear_all()
        return {"ok": True}

    def cliphist_paste(self, entry_id):
        if not self._clip_monitor:
            return {"ok": False, "err": "监控未启动"}
        ok = self._clip_monitor.paste_to_clipboard(int(entry_id))
        if ok:
            return {"ok": True}
        return {"ok": False, "err": "写入剪贴板失败"}

    def cliphist_get_image(self, entry_id):
        if not self._clip_monitor:
            return {"ok": False, "err": "监控未启动"}
        png = self._clip_monitor.get_image_data(int(entry_id))
        if png:
            data_url = "data:image/png;base64," + base64.b64encode(png).decode()
            return {"ok": True, "data": {"data_url": data_url}}
        return {"ok": False, "err": "图片不存在"}
