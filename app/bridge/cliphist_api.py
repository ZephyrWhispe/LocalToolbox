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

    def cliphist_list(self, limit=50, offset=0, query=""):
        if not self._clip_monitor:
            return {"ok": True, "data": []}
        entries = self._clip_monitor.list_entries(int(limit), int(offset), str(query))
        return {"ok": True, "data": entries}

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
