"""DNS 修改器桥接层。"""

from .base import BridgeBase


class DnsApi(BridgeBase):
    def _init_dns(self):
        pass

    def dns_list_adapters(self):
        try:
            from ..core.dns_changer import list_adapters
            return {"ok": True, "data": list_adapters()}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def dns_get(self, adapter_name):
        try:
            from ..core.dns_changer import get_dns
            return {"ok": True, "data": get_dns(str(adapter_name))}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def dns_set(self, adapter_name, primary, secondary=""):
        try:
            from ..core.dns_changer import set_dns
            return set_dns(str(adapter_name), str(primary), str(secondary))
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def dns_reset(self, adapter_name):
        try:
            from ..core.dns_changer import reset_dns
            return reset_dns(str(adapter_name))
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def dns_is_admin(self):
        try:
            from ..core.dns_changer import is_admin
            return {"ok": True, "data": {"admin": is_admin()}}
        except Exception:
            return {"ok": True, "data": {"admin": False}}
