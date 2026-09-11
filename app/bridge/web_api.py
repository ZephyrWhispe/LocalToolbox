"""Web（WebDAV）页桥接：服务器启停 / 防火墙放行 / WebClient 认证修复。"""

from ..core import web_server


def _pick_folder(bridge):
    """打开系统目录选择对话框，取消返回 None（对应旧页 QFileDialog）。"""
    import webview

    if bridge._window is None:
        raise RuntimeError("窗口尚未就绪，无法打开目录选择框。")
    result = bridge._window.create_file_dialog(webview.FOLDER_DIALOG)
    if not result:
        return None
    if isinstance(result, (list, tuple)):
        return result[0] if result else None
    return result


class WebApi:
    def _init_web(self):
        self._web_srv = web_server.WebServer()
        self._web_fw_open = False

    # -- API -----------------------------------------------------------
    def web_get_state(self):
        srv = self._web_srv
        running = srv.running
        try:
            urls = srv.urls() if running else []
        except Exception:
            urls = []
        try:
            auth_level = web_server.webclient_basic_auth_level()
        except Exception:
            auth_level = None
        return {
            "ok": True,
            "data": {
                "running": running,
                "port": srv.port,
                "urls": urls,
                "fw_open": self._web_fw_open,
                "auth_level": auth_level,
            },
        }

    def web_start(self, host, port, root, username="", password="",
                  allow_write=True, fw=True):
        srv = self._web_srv
        if srv.running:
            return {"ok": True, "data": True}
        try:
            srv.start(
                str(host or "0.0.0.0"),
                int(port),
                str(root or ""),
                str(username or ""),
                str(password or ""),
                bool(allow_write),
                log_callback=lambda m: self.emit("web_log", str(m)),
            )
        except Exception as e:
            return {"ok": False, "err": str(e)}
        if fw:
            ok, msg = web_server.add_firewall_rule(srv.port)
            if ok:
                self._web_fw_open = True
                self.emit("web_log", "已放行防火墙入站端口 %d" % srv.port)
            else:
                self.emit(
                    "web_log",
                    "防火墙放行失败：" + ((msg or "").strip() or "未授权或命令出错"),
                )
        self.emit(
            "web_log",
            "Web 服务器已启动，目录：%s%s"
            % (srv.root_dir, "（可读写）" if srv.allow_write else "（只读）"),
        )
        urls = srv.urls()
        if urls:
            self.emit("web_log", "局域网访问地址：")
            for u in urls:
                self.emit("web_log", "  " + u)
        else:
            self.emit("web_log", "未能获取本机 IP，请检查网络。")
        return {"ok": True, "data": True}

    def web_stop(self):
        try:
            self._web_srv.stop()
            self.emit("web_log", "Web 服务器已停止。")
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def web_remove_fw(self):
        port = self._web_srv.port
        try:
            ok, msg = web_server.remove_firewall_rule(port)
        except Exception as e:
            return {"ok": False, "err": "移除失败：" + str(e)}
        if ok:
            self._web_fw_open = False
            self.emit("web_log", "已移除防火墙放行规则。")
            return {"ok": True, "data": True}
        return {"ok": False, "err": "移除失败：" + (msg or "")}

    def web_fix_auth(self):
        try:
            ok, msg = web_server.set_webclient_basic_auth(True)
        except Exception as e:
            return {"ok": False, "err": "修复失败：" + str(e)}
        if ok:
            self.emit("web_log", "已修复：BasicAuthLevel 已设为 2，可注销或重连后生效。")
            return {"ok": True, "data": True}
        return {"ok": False, "err": "修复失败：" + ((msg or "").strip() or "未授权")}

    def web_pick_folder(self):
        try:
            return {"ok": True, "data": _pick_folder(self)}
        except Exception as e:
            return {"ok": False, "err": str(e)}
