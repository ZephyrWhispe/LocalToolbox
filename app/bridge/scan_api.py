"""扫描与映射页桥接：局域网共享扫描（后台线程+事件推送）、凭据连接、驱动器映射/断开。"""

import ctypes
import string
import threading

from ..core import drive_mapper, scanner


def available_drive_letters():
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        bitmask = 0
    used = {chr(ord("A") + i) for i in range(26) if bitmask & (1 << i)}
    return [c for c in string.ascii_uppercase if c not in used]


def _extract_host(path):
    """从 UNC 路径取主机（IP 形式整体保留，主机名去掉 DNS 后缀）。"""
    if not path.startswith("\\\\"):
        return ""
    rest = path[2:].split("\\")[0].strip()
    if not rest:
        return ""
    if rest.replace(".", "").isdigit():   # IP：整体作为主机
        return rest
    return rest.split(".")[0]


class ScanApi:
    def _init_scan(self):
        self._scan_running = False
        self._scan_lock = threading.Lock()
        self._cred_cache = {}  # host.lower() -> (user, password)

    # -- API -----------------------------------------------------------
    def scan_start(self):
        with self._scan_lock:
            if self._scan_running:
                return {"ok": False, "err": "扫描正在进行中，请等待完成"}
            self._scan_running = True
        threading.Thread(target=self._scan_worker, daemon=True).start()
        return {"ok": True, "data": True}

    def _scan_worker(self):
        try:
            def progress(cur, total, msg):
                self.emit("scan_progress", {"current": cur, "total": total, "message": msg})

            def host_found(h):
                self.emit("scan_host", h)

            results = scanner.scan_network(progress=progress, host_callback=host_found)
            if not results:
                msg = "未发现任何主机。请确认设备在同一局域网且开启了共享。"
            else:
                msg = "扫描完成，发现 %d 台主机。双击共享可自动填入路径。" % len(results)
            self.emit("scan_done", {"count": len(results), "message": msg})
        except Exception as e:
            self.emit("scan_done", {"count": 0, "message": "扫描出错：" + str(e)})
        finally:
            with self._scan_lock:
                self._scan_running = False

    def scan_connect(self, host, ip, user, password):
        """凭据连接主机（net use IPC$），成功后枚举共享。"""
        try:
            host = str(host or "").strip()
            ip = str(ip or "").strip()
            target = ip or host
            if not target:
                return {"ok": False, "err": "缺少主机地址"}
            user = str(user or "")
            password = str(password or "")
            if not user:
                return {"ok": False, "err": "请输入用户名。"}
            ok, msg = scanner.connect_host(target, user, password)
            if not ok:
                return {"ok": False, "err": msg}
            shares, error = scanner.list_computer_shares(target)
            if ip:
                self._cred_cache[ip.lower()] = (user, password)
            if host:
                short = host.lower().split(".")[0]
                self._cred_cache[short] = (user, password)
                self._cred_cache[host.lower()] = (user, password)
            return {"ok": True, "data": {"shares": shares, "error": error}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def scan_letters(self):
        """当前可用的网络驱动器盘符。"""
        return {"ok": True, "data": available_drive_letters()}

    def scan_map(self, path, letter, user="", password=""):
        try:
            path = str(path or "").strip()
            letter = str(letter or "").replace(":", "").strip().upper()
            if not path:
                return {"ok": False, "err": "请先输入共享路径，或选择一个扫描到的共享。"}
            if not letter:
                return {"ok": False, "err": "没有可用盘符。"}
            user = str(user or "")
            password = str(password or "")
            if not user:
                host = _extract_host(path)
                cached = self._cred_cache.get(host.lower()) if host else None
                if cached:
                    user, password = cached
            ok, msg = drive_mapper.map_drive(letter, path, user=user, password=password)
            if ok:
                if user:
                    host = _extract_host(path)
                    if host:
                        self._cred_cache[host.lower()] = (user, password)
                return {"ok": True, "data": msg}
            return {"ok": False, "err": msg}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def scan_unmap(self, letter):
        try:
            ok, msg = drive_mapper.unmap_drive(str(letter or ""))
            return {"ok": True, "data": msg} if ok else {"ok": False, "err": msg}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def scan_mapped(self):
        try:
            return {"ok": True, "data": drive_mapper.list_mapped_drives()}
        except Exception as e:
            return {"ok": False, "err": str(e)}
