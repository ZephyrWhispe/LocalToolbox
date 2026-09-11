"""文件传输页桥接：发送/接收状态、传输确认、保存目录与自动接收设置。"""

import os

import webview

from ..core import firewall
from ..core.config import DATA_HOME
from ..core.hud import HudController
from ..core.pairing import Identity, TrustStore
from ..core.transfer import FILE_PORT_DEFAULT, TransferEngine, expand_paths

FW_PREFIX = "XferHelper-"


def _default_save_dir():
    return os.path.join(os.path.expanduser("~"), "Downloads")


class TransferApi:
    def _init_transfer(self):
        # 身份密钥与信任表全应用共享（未来剪贴板加密复用）
        self.identity = Identity()
        self.trust = TrustStore()
        self.xfer = TransferEngine(
            port=FILE_PORT_DEFAULT,
            device_id=self.discovery.device_id,
            device_name=self.discovery.device_name,
            get_save_dir=self._xfer_save_dir,
            get_auto_accept=lambda: bool(self.cfg.get("auto_accept", False)),
            get_require_pairing=lambda: bool(self.cfg.get("require_pairing", False)),
            identity=self.identity,
            trust=self.trust,
            on_event=self._xfer_on_event,
            log=self.emit_log,
            queue_path=os.path.join(DATA_HOME, "transfer_queue.json"),
        )
        self.xfer.start()
        # 进度悬浮窗（HUD）：按配置启停，失败时静默降级
        self.hud = HudController(log=self.emit_log)
        if bool(self.cfg.get("hud_enabled", False)):
            self.hud.start()
        # 发现心跳广播 file_port，其他设备据此显示「文件」标签并启用发送
        self.discovery.advertise["file_port"] = self.xfer.port

    def _stop_transfer(self):
        """退出清理：先收 HUD（tkinter 线程须在进程退出前收束，否则 Tcl 崩溃）。"""
        try:
            self.hud.stop()
        except Exception:
            pass

    def _xfer_on_event(self, name, payload):
        """引擎事件 → UI；进度/完成事件同步刷新悬浮窗。"""
        self.emit(name, payload)
        if name in ("xfer_progress", "xfer_done", "xfer_queue") and self.hud.available:
            self.hud.update(self.xfer.snapshot()["active"])

    def _xfer_save_dir(self):
        d = str(self.cfg.get("save_dir") or "").strip()
        if not d or not os.path.isdir(d):
            d = _default_save_dir()
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        return d

    # -- API -----------------------------------------------------------
    def xfer_get_state(self):
        snap = self.xfer.snapshot()
        snap["save_dir"] = self._xfer_save_dir()
        snap["auto_accept"] = bool(self.cfg.get("auto_accept", False))
        snap["require_pairing"] = bool(self.cfg.get("require_pairing", False))
        snap["device_name"] = self.discovery.device_name
        return {"ok": True, "data": snap}

    def xfer_send(self, ip, port, paths, peer_name=None):
        try:
            paths = [str(p) for p in (paths or []) if p]
            return self.xfer.send_to(str(ip), int(port or 0), paths, peer_name=peer_name)
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def xfer_enqueue(self, ip, port, paths, peer_name=None):
        """把发送任务加入持久化队列（重启续接，失败可手动重试）。"""
        try:
            paths = [str(p) for p in (paths or []) if p]
            return self.xfer.enqueue(str(ip), int(port or 0), paths, peer_name=peer_name)
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def xfer_queue_remove(self, tid):
        try:
            return self.xfer.queue_remove(str(tid))
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def xfer_queue_retry(self, tid):
        try:
            return self.xfer.queue_retry(str(tid))
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def xfer_respond(self, tid, accept):
        try:
            return self.xfer.respond(str(tid), bool(accept))
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def xfer_cancel(self, tid):
        try:
            return self.xfer.cancel(str(tid))
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def xfer_firewall(self):
        """防火墙放行接收端口（触发 UAC 提权）。"""
        try:
            ok, output = firewall.add_ports(
                FW_PREFIX, [(self.xfer.port, "TCP")]
            )
            if not ok:
                return {"ok": False, "err": "防火墙放行失败（需管理员确认）：" + str(output)}
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def xfer_pick_files(self):
        """系统多选文件对话框，返回路径列表。"""
        if self._window is None:
            return {"ok": False, "err": "窗口尚未就绪。"}
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, directory="", allow_multiple=True
            )
        except Exception as e:
            return {"ok": False, "err": str(e)}
        return {"ok": True, "data": list(result or [])}

    def xfer_pick_folder(self):
        if self._window is None:
            return {"ok": False, "err": "窗口尚未就绪。"}
        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory="")
        except Exception as e:
            return {"ok": False, "err": str(e)}
        return {"ok": True, "data": result[0] if result else None}

    def xfer_expand(self, paths):
        """预览：把文件/目录展开为将发送的相对路径列表（含大小）。"""
        try:
            files = expand_paths([str(p) for p in (paths or []) if p])
            return {"ok": True, "data": files}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def xfer_set_save_dir(self, path=None):
        try:
            if path:
                path = str(path)
            else:
                if self._window is None:
                    return {"ok": False, "err": "窗口尚未就绪。"}
                result = self._window.create_file_dialog(
                    webview.FOLDER_DIALOG, directory=""
                )
                dirs = list(result or [])
                if not dirs:
                    return {"ok": False, "err": "未选择目录"}
                path = dirs[0]
            if not os.path.isdir(path):
                return {"ok": False, "err": "目录不存在：%s" % path}
            self.cfg.set("save_dir", path)
            return {"ok": True, "data": path}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def xfer_set_auto_accept(self, value):
        value = bool(value)
        self.cfg.set("auto_accept", value)
        return {"ok": True, "data": value}

    def xfer_set_hud(self, value):
        """开关进度悬浮窗（HUD）。"""
        value = bool(value)
        self.cfg.set("hud_enabled", value)
        if value:
            self.hud.start()
        else:
            self.hud.stop()
        return {"ok": True, "data": value}

    # -- 配对与加密 -----------------------------------------------------
    def xfer_pair(self, ip, port, peer_name=None):
        """发起与指定设备的配对（独立连接）。"""
        try:
            return self.xfer.pair_to(str(ip), int(port or 0), peer_name=peer_name)
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def xfer_pair_submit(self, tid, pin):
        """发起方提交对方屏幕上显示的 6 位配对码。"""
        try:
            return self.xfer.pair_submit(str(tid), pin)
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def xfer_pair_decide(self, tid, allow):
        """接收方允许/拒绝配对请求。"""
        try:
            return self.xfer.pair_decide(str(tid), bool(allow))
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def xfer_untrust(self, device_id):
        """解除配对（删除信任公钥）。"""
        try:
            return self.xfer.untrust(str(device_id))
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def xfer_my_fingerprint(self):
        """本机公钥指纹（供用户核对）。"""
        try:
            return {"ok": True, "data": {"pk": self.identity.public_hex()}}
        except Exception as e:
            return {"ok": False, "err": str(e)}
