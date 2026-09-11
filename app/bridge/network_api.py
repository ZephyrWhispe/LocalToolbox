"""网络发现/文件共享页桥接：状态检测与功能开关（netsh 防火墙 + PowerShell，需管理员权限）。"""

import os
import threading

from ..core import network_toggle
from ..core.privilege import is_admin, relaunch_elevated


class NetworkApi:
    def _init_network(self):
        pass

    # -- API -----------------------------------------------------------
    def network_status(self):
        try:
            return {"ok": True, "data": network_toggle.get_all_status()}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def network_set(self, which, enable):
        """which: "discovery" | "sharing"；enable: bool。
        以管理员身份运行时直接执行（无弹窗）；否则内部提权（可能弹 UAC）。"""
        try:
            enable = bool(enable)
            if which == "discovery":
                result = network_toggle.set_network_discovery(enable)
            elif which == "sharing":
                result = network_toggle.set_file_sharing(enable)
            else:
                return {"ok": False, "err": "未知的开关：%s" % which}
            if result.ok:
                return {"ok": True, "data": (result.output or "").strip() or "设置已应用"}
            msg = (result.output or "").strip() or "权限不足或无规则匹配"
            return {"ok": False, "err": msg}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def network_relaunch_admin(self):
        """以管理员身份重启应用（弹出一次 UAC）；成功后旧实例自动退出。"""
        try:
            if is_admin():
                return {"ok": True, "data": "已具备管理员权限"}
            ok, err = relaunch_elevated(["--relaunch-admin"])
            if not ok:
                return {"ok": False, "err": err}
            # UAC 授权后新实例即将启动；短暂延迟后退出旧实例释放单实例锁
            def _exit_old():
                import time as _time
                _time.sleep(0.8)
                os._exit(0)

            threading.Thread(target=_exit_old, daemon=True).start()
            return {"ok": True, "data": "已请求以管理员身份重启，请稍候…"}
        except Exception as e:
            return {"ok": False, "err": str(e)}
