"""贴图桥接层。"""

import base64

from .base import BridgeBase


class PinApi(BridgeBase):
    def _init_pin(self):
        from ..core.pin_screen import PinManager
        self._pin_mgr = PinManager()

    def pin_image(self, data_url):
        try:
            raw = _decode_data_url(data_url)
            opacity = float(self.cfg.get("pin_opacity", 1.0))
            pin_id = self._pin_mgr.pin(raw, opacity=opacity)
            return {"ok": True, "data": {"pin_id": pin_id}}
        except Exception as e:
            return {"ok": False, "err": "贴图失败: %s" % e}

    def pin_close(self, pin_id):
        self._pin_mgr.close(int(pin_id))
        return {"ok": True}

    def pin_close_all(self):
        self._pin_mgr.close_all()
        return {"ok": True}

    def pin_set_opacity(self, pin_id, alpha):
        with self._pin_mgr._lock:
            entry = self._pin_mgr._pins.get(int(pin_id))
        if entry:
            entry["state"]["opacity"] = float(alpha)
            entry["top"].attributes("-alpha", float(alpha))
        return {"ok": True}

    def pin_list(self):
        return {"ok": True, "data": self._pin_mgr.list_pins()}

    def pin_shutdown(self):
        self._pin_mgr.shutdown()


def _decode_data_url(data_url):
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    return base64.b64decode(data_url)
