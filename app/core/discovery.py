"""局域网设备发现：UDP 广播心跳，供剪贴板同步 / 键鼠共享共用。"""

import json
import socket
import threading
import time
import uuid

APP_TAG = "localtoolbox"
PROTO_VERSION = 1
DEFAULT_DISCOVERY_PORT = 41890

from . import logger as applog

log = applog.get_logger("discovery")


def machine_guid():
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value)
    except Exception:
        return "pid-" + uuid.uuid4().hex[:16]


def local_hostname():
    try:
        return socket.gethostname() or "未知设备"
    except Exception:
        return "未知设备"


class Discoverer:
    """周期广播自身信息（含各功能模块注入的 advertise 字段），收集同类设备。

    devices() 返回 [{id, name, ip, last_seen, ...advertise 字段}]。
    """

    def __init__(
        self,
        app_tag=APP_TAG,
        proto=PROTO_VERSION,
        port=DEFAULT_DISCOVERY_PORT,
        interval=3.0,
        timeout=10.0,
        device_id=None,
        name=None,
        log=None,
        on_devices=None,
    ):
        self.app_tag = app_tag
        self.proto = proto
        self.port = int(port)
        self.interval = interval
        self.timeout = timeout
        self.device_id = device_id or machine_guid()
        self.device_name = name or local_hostname()
        self.log = log
        self.on_devices = on_devices
        self.advertise = {}
        self._devices = {}
        self._lock = threading.Lock()
        self._sock_send = None
        self._sock_recv = None
        self._threads = []
        self._running = False

    @property
    def running(self):
        return self._running

    def start(self):
        if self._running:
            return
        try:
            self._sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock_send.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock_recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self._sock_recv.bind(("", self.port))
            except OSError as e:
                log.warning("发现服务监听 %d 失败：%s（自动发现不可用）", self.port, e)
                self._emit_log(f"发现服务监听 {self.port} 失败：{e}（自动发现不可用）")
                self._sock_recv.close()
                self._sock_recv = None
        except OSError as e:
            log.error("发现服务启动失败：%s", e)
            self._emit_log(f"发现服务启动失败：{e}")
            self._close_sockets()
            return
        self._running = True
        log.info("设备发现服务已启动 port=%d", self.port)
        self._threads = [
            threading.Thread(target=self._broadcast_loop, name="disc-bc", daemon=True),
            threading.Thread(target=self._listen_loop, name="disc-ls", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self):
        self._running = False
        self._close_sockets()
        for t in self._threads:
            t.join(timeout=2)
        self._threads = []
        with self._lock:
            self._devices = {}
        log.info("设备发现服务已停止")

    def _close_sockets(self):
        for attr in ("_sock_send", "_sock_recv"):
            sock = getattr(self, attr)
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
                setattr(self, attr, None)

    def _beacon(self):
        with self._lock:
            extra = dict(self.advertise)
        return json.dumps(
            {
                "app": self.app_tag,
                "proto": self.proto,
                "id": self.device_id,
                "name": self.device_name,
                **extra,
            }
        ).encode("utf-8")

    def _broadcast_loop(self):
        while self._running:
            sock = self._sock_send
            if sock:
                try:
                    sock.sendto(self._beacon(), ("<broadcast>", self.port))
                except OSError:
                    pass
            time.sleep(self.interval)

    def _listen_loop(self):
        sock = self._sock_recv
        if not sock:
            return
        sock.settimeout(1.0)
        while self._running:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                self._prune()
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if msg.get("app") != self.app_tag or msg.get("proto") != self.proto:
                continue
            peer_id = msg.get("id")
            if not peer_id or peer_id == self.device_id:
                continue
            changed = False
            with self._lock:
                prev = self._devices.get(peer_id)
                entry = {
                    "id": peer_id,
                    "name": msg.get("name", "未知设备"),
                    "ip": addr[0],
                    "last_seen": time.time(),
                }
                entry.update(
                    {k: v for k, v in msg.items() if k not in ("app", "proto", "id")}
                )
                if prev != entry:
                    changed = True
                self._devices[peer_id] = entry
            self._prune()
            if changed:
                self._notify()

    def _prune(self):
        now = time.time()
        with self._lock:
            stale = [k for k, v in self._devices.items() if now - v["last_seen"] > self.timeout]
            for k in stale:
                del self._devices[k]
        if stale:
            self._notify()

    def devices(self):
        with self._lock:
            return [dict(v) for v in self._devices.values()]

    # 回调在使用者线程（监听线程）触发，使用者需自行做线程切换
    def _notify(self):
        if self.on_devices:
            try:
                self.on_devices(self.devices())
            except Exception:
                pass
