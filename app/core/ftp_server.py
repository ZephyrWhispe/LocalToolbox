import os
import socket
import threading

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from . import logger as applog
from .firewall import add as _fw_add, remove as _fw_remove

log = applog.get_logger("ftp")

READ_PERMS = "elr"
WRITE_PERMS = "elradfmwMT"
RULE_PREFIX = "FtpHelper-"


def local_ip_addresses():
    try:
        return list(socket.gethostbyname_ex(socket.gethostname())[2])
    except Exception:
        return []


def add_firewall_rule(port):
    return _fw_add(RULE_PREFIX, port)


def remove_firewall_rule(port):
    return _fw_remove(RULE_PREFIX, port)


def _build_handler(authorizer, log_callback):
    class _Handler(FTPHandler):
        def on_login(self, username):
            log.info("FTP 用户登录：%s", username)
            if log_callback:
                log_callback(f"用户登录：{username}")

        def on_login_failed(self, username, password):
            log.warning("FTP 登录失败：%s", username)
            if log_callback:
                log_callback(f"登录失败：{username}")

        def on_logout(self, username):
            log.info("FTP 用户退出：%s", username)
            if log_callback:
                log_callback(f"用户退出：{username}")

        def on_file_received(self, file):
            log.info("FTP 上传完成：%s", file)
            if log_callback:
                log_callback(f"上传完成：{file}")

        def on_file_sent(self, file):
            log.info("FTP 下载完成：%s", file)
            if log_callback:
                log_callback(f"下载完成：{file}")

        def on_incomplete_file_received(self, file):
            log.warning("FTP 上传中断：%s", file)
            if log_callback:
                log_callback(f"上传中断：{file}")

        def on_incomplete_file_sent(self, file):
            log.warning("FTP 下载中断：%s", file)
            if log_callback:
                log_callback(f"下载中断：{file}")

    _Handler.authorizer = authorizer
    _Handler.encoding = "utf-8"
    return _Handler


class FtpServer:
    def __init__(self):
        self._server = None
        self._thread = None
        self.host = ""
        self.port = 0
        self.root_dir = ""
        self.username = ""
        self.allow_write = False
        self.log_callback = None

    @property
    def running(self):
        return bool(self._server and self._thread and self._thread.is_alive())

    def start(
        self,
        host,
        port,
        root_dir,
        username="",
        password="",
        allow_write=True,
        log_callback=None,
    ):
        self.stop()
        root = os.path.abspath(root_dir.strip())
        if not os.path.isdir(root):
            raise ValueError("共享目录不存在，请重新选择。")
        if not (0 < int(port) < 65536):
            raise ValueError("端口号必须在 1-65535 之间。")

        self.log_callback = log_callback
        authorizer = DummyAuthorizer()
        perms = WRITE_PERMS if allow_write else READ_PERMS
        if username.strip():
            self.username = username.strip()
            authorizer.add_user(self.username, password, root, perm=perms)
        else:
            self.username = "anonymous"
            authorizer.add_anonymous(root, perm=perms)

        handler = _build_handler(authorizer, log_callback)
        server = FTPServer((host, port), handler)
        self._server = server
        self.host = host
        self.port = int(port)
        self.root_dir = root
        self.allow_write = bool(allow_write)
        self._thread = threading.Thread(
            target=self._serve, name="ftp-server", daemon=True
        )
        self._thread.start()
        return "FTP 服务器已启动。"

    def _serve(self):
        try:
            self._server.serve_forever(timeout=0.2)
        except Exception:
            pass

    def stop(self):
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server:
            try:
                server.close_all()
            except Exception:
                pass
        if (
            thread
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=3)