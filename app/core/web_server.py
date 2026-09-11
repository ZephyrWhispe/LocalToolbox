import logging
import os
import threading

from cheroot import wsgi
from wsgidav.dc.simple_dc import SimpleDomainController
from wsgidav.fs_dav_provider import FilesystemProvider
from wsgidav.wsgidav_app import WsgiDAVApp

from .firewall import add as _fw_add, remove as _fw_remove
from .ftp_server import local_ip_addresses
from .runner import run_powershell_elevated

RULE_PREFIX = "WebHelper-"

_LOGGER_NAME = "wsgidav"


def add_firewall_rule(port):
    return _fw_add(RULE_PREFIX, port)


def remove_firewall_rule(port):
    return _fw_remove(RULE_PREFIX, port)


def set_webclient_basic_auth(enabled=True):
    value = 2 if enabled else 1
    script = (
        "$p='HKLM:\\SYSTEM\\CurrentControlSet\\Services\\WebClient\\Parameters'; "
        f"New-Item -Path $p -Force | Out-Null; "
        f"Set-ItemProperty -Path $p -Name BasicAuthLevel -Value {value} -Type DWord; "
        "'OK'"
    )
    # 脚本是 PowerShell（New-Item/Set-ItemProperty/| Out-Null）→ 用 PowerShell 提权
    result = run_powershell_elevated(script)
    return result.ok, result.output


def webclient_basic_auth_level():
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Services\WebClient\Parameters",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "BasicAuthLevel")
            return int(value)
    except Exception:
        return None


class _CallbackLogHandler(logging.Handler):
    def __init__(self, callback):
        super().__init__()
        self._cb = callback
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        if self._cb:
            try:
                self._cb(self.format(record))
            except Exception:
                pass


def _attach_logger(callback):
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    for h in list(logger.handlers):
        if isinstance(h, _CallbackLogHandler):
            logger.removeHandler(h)
    if callback:
        handler = _CallbackLogHandler(callback)
        logger.addHandler(handler)


class WebServer:
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
        _attach_logger(log_callback)

        role = "editor" if allow_write else "reader"
        if username.strip():
            self.username = username.strip()
            user_mapping = {
                "*": {
                    self.username: {"password": password, "roles": [role]},
                }
            }
        else:
            # realm 条目为 True 时 SimpleDomainController 放行匿名访问
            self.username = "anonymous"
            user_mapping = {"*": True}

        provider = FilesystemProvider(root, readonly=not allow_write, fs_opts={})

        config = {
            "host": host,
            "port": int(port),
            "provider_mapping": {"/": provider},
            "http_authenticator": {
                "domain_controller": SimpleDomainController,
            },
            "simple_dc": {
                "user_mapping": user_mapping,
            },
            # 禁用 wsgidav 自带日志初始化，否则会移除我们的回调 handler 并重设级别
            "logging": {"enable": False},
            "dir_browser": {"enable": True},
            "lock_storage": True,
            "property_manager": True,
            "verbose": 1,
        }
        app = WsgiDAVApp(config)
        server = wsgi.Server((host, int(port)), app, numthreads=8)
        try:
            server.prepare()
        except Exception:
            try:
                server.stop()
            except Exception:
                pass
            raise

        self._server = server
        self.host = host
        self.port = int(port)
        self.root_dir = root
        self.allow_write = bool(allow_write)
        self._thread = threading.Thread(
            target=self._serve, name="web-server", daemon=True
        )
        self._thread.start()
        return "Web 服务器已启动。"

    def _serve(self):
        try:
            self._server.serve()
        except Exception:
            pass

    def stop(self):
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server:
            try:
                server.stop()
            except Exception:
                pass
        if (
            thread
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=5)

    def urls(self):
        results = []
        port = self.port
        for ip in [ip for ip in local_ip_addresses() if ip]:
            results.append(f"http://{ip}:{port}")
        return results