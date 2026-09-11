"""剪贴板共享：本机 Win+V 式历史 + 局域网设备实时同步（文本 + 图片）。

- 发现：Discoverer（UDP 广播），beacon 中带 clip_port；
- 同步：TCP 直连推送（每条消息一行 JSON）；
- 回声抑制：网络来的内容写入本机后记录哈希，本机剪贴板监听跳过该哈希；
- 图片：读取剪贴板 CF_DIB → PNG，落盘到 %APPDATA%/LocalToolbox/clipboard_images，
  经 base64 随同一协议推送；接收端写回本机剪贴板并落盘。
"""

import base64
import ctypes
import hashlib
import io
import json
import os
import socket
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from . import logger as applog
from . import screenshot
from .config import DATA_HOME
from .discovery import Discoverer, local_hostname, machine_guid

log = applog.get_logger("clipboard")

try:
    import pyperclip
except ImportError:
    pyperclip = None

SYNC_PORT_DEFAULT = 41891
MAX_CLIP_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024  # 剪贴板文件同步上限（超出提示用文件传输页）
# 单行消息缓冲上限：按文件字节 → base64 膨胀 4/3 估
MAX_LINE_BYTES = MAX_FILE_BYTES * 4 // 3 + 65536
DEDUP_WINDOW = 60.0
HISTORY_LIMIT = 100
POLL_INTERVAL = 0.4
CLIP_IMG_DIR = os.path.join(DATA_HOME, "clipboard_images")
CLIP_FILE_DIR = os.path.join(DATA_HOME, "clipboard_files")
MAX_IMAGE_PIXELS = 50_000_000  # 图片像素上限（≈8000×6250），防解压炸弹


def _safe_file_name(name):
    """清洗文件名：去掉目录部分与非法字符，防止路径穿越。"""
    name = str(name or "").strip()
    if not name:
        return "file"
    name = name.replace("\\", "/").rsplit("/", 1)[-1] or "file"
    for ch in ('<>:"|?*'):
        name = name.replace(ch, "_")
    return name[:200]


def hash_text(text):
    return hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def hash_bytes(data):
    return hashlib.sha1(data).hexdigest()[:16]


def _dib_to_png(dib):
    """CF_DIB 内存 → PNG 字节。支持 24/32 位位图（上/下颠倒均可），失败返回 None。"""
    try:
        if len(dib) < 40:
            return None
        w, h = struct.unpack("<ii", dib[4:12])
        bits = struct.unpack("<H", dib[14:16])[0]
        topdown = h < 0
        h = abs(h)
        if w <= 0 or h <= 0 or bits not in (24, 32) or w * h > MAX_IMAGE_PIXELS:
            return None
        bpp = bits // 8
        stride = ((w * bpp + 3) // 4) * 4
        raw = dib[40:40 + stride * h]
        mode = "BGRX" if bpp == 4 else "BGR"
        # orientation：-1 表示数据 bottom-up（翻转），0 表示 top-down（不翻转）
        img = Image.frombytes(
            "RGB", (w, h), raw, "raw", mode, stride, -1 if topdown else 0
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _png_pixel_count(png):
    """解析 PNG IHDR 头返回像素总数；非法/非 PNG 返回 None。"""
    try:
        if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w, h = struct.unpack(">II", png[16:24])
        return w * h
    except struct.error:
        return None


def _clipboard_image_png():
    """读取系统剪贴板中的图片（CF_DIB），返回 PNG 字节；无图片/失败返回 None。"""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return None
    if not user32.OpenClipboard(None):
        return None
    try:
        hid = user32.GetClipboardData(8)  # CF_DIB=8
        if not hid:
            return None
        size = kernel32.GlobalSize(hid)
        ptr = kernel32.GlobalLock(hid)
        if not ptr:
            return None
        try:
            data = ctypes.string_at(ptr, int(size))
        finally:
            kernel32.GlobalUnlock(hid)
    finally:
        user32.CloseClipboard()
    return _dib_to_png(data or b"")


def _default_img_reader():
    return _clipboard_image_png()


def _default_img_writer(png_bytes):
    try:
        return screenshot.copy_png_to_clipboard(png_bytes)
    except Exception:
        return False


def _clipboard_file_paths():
    """读取系统剪贴板中的文件列表（CF_HDROP=15）。非 Windows/无文件返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetClipboardData.restype = ctypes.c_void_p
        user32.GetClipboardData.argtypes = [ctypes.c_uint]
        kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    except Exception:
        return None
    if not user32.OpenClipboard(None):
        return None
    try:
        hid = user32.GetClipboardData(15)  # CF_HDROP
        if not hid:
            return None
        size = kernel32.GlobalSize(hid)
        ptr = kernel32.GlobalLock(hid)
        if not ptr:
            return None
        try:
            data = ctypes.string_at(ptr, int(size))
        finally:
            kernel32.GlobalUnlock(hid)
        if not data or len(data) < 20:
            return None
        off = struct.unpack("<I", data[0:4])[0]
        f_wide = struct.unpack("<i", data[16:20])[0]
        body = data[off:]
        if f_wide:
            parts = body.decode("utf-16-le", "ignore").split("\x00")
        else:
            parts = body.decode("gbk", "ignore").split("\x00")
        paths = [p for p in parts if p]
        return paths or None
    finally:
        user32.CloseClipboard()


def _set_clipboard_files(paths):
    """把文件路径列表写入系统剪贴板（CF_HDROP=15）。返回是否成功。"""
    if sys.platform != "win32":
        return False
    paths = [str(p) for p in (paths or []) if p]
    if not paths:
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return False
    HGLOBAL = ctypes.c_void_p
    kernel32.GlobalAlloc.restype = HGLOBAL
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [HGLOBAL]
    kernel32.GlobalFree.restype = HGLOBAL
    kernel32.GlobalFree.argtypes = [HGLOBAL]
    user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
    user32.EmptyClipboard.restype = ctypes.wintypes.BOOL
    user32.SetClipboardData.restype = HGLOBAL
    user32.SetClipboardData.argtypes = [ctypes.c_uint, HGLOBAL]
    user32.CloseClipboard.restype = ctypes.wintypes.BOOL

    buf = (
        struct.pack("<iiii", 20, 0, 0, 1)  # DROPFILES：偏移20，无坐标，fWide=1
        + b"".join(p.encode("utf-16le") + b"\x00" for p in paths)
        + b"\x00\x00"
    )
    h_mem = kernel32.GlobalAlloc(0x0042, len(buf))  # GMEM_MOVEABLE|GMEM_ZEROINIT
    if not h_mem:
        return False
    p = kernel32.GlobalLock(h_mem)
    if not p:
        return False
    ctypes.memmove(p, buf, len(buf))
    kernel32.GlobalUnlock(h_mem)
    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(h_mem)
        return False
    try:
        user32.EmptyClipboard()
        ok = bool(user32.SetClipboardData(15, h_mem))
        if not ok:
            kernel32.GlobalFree(h_mem)
        return ok
    except Exception:
        return False
    finally:
        user32.CloseClipboard()


def _default_file_reader():
    return _clipboard_file_paths()


def _default_file_writer(paths):
    return _set_clipboard_files(paths)


def _default_reader():
    if not pyperclip:
        return None
    try:
        return pyperclip.paste()
    except Exception:
        return None


def _default_writer(text):
    if pyperclip:
        try:
            pyperclip.copy(text)
        except Exception:
            pass


def _clipboard_sequence():
    try:
        import ctypes

        return ctypes.windll.user32.GetClipboardSequenceNumber()
    except Exception:
        return None


class ClipboardSync:
    def __init__(
        self,
        sync_port=SYNC_PORT_DEFAULT,
        device_id=None,
        name=None,
        discovery=None,
        reader=None,
        writer=None,
        img_reader=None,
        img_writer=None,
        file_reader=None,
        file_writer=None,
        history_limit=HISTORY_LIMIT,
        log=None,
        store=None,
    ):
        self.sync_port = int(sync_port)
        self.device_id = device_id or machine_guid()
        self.device_name = name or local_hostname()
        self._reader = reader or _default_reader
        self._writer = writer or _default_writer
        self._img_reader = img_reader or _default_img_reader
        self._img_writer = img_writer or _default_img_writer
        self._file_reader = file_reader or _default_file_reader
        self._file_writer = file_writer or _default_file_writer
        self.history_limit = history_limit
        self.log = log
        self.store = store  # ClipboardStore（可选，None = 不落盘）
        if store is not None:
            try:
                self.history = store.load(history_limit)
            except Exception:
                self.history = []
        else:
            self.history = []  # 最新在前：{ts, device, kind, text?, img_path?, hash, remote}
        self.send_enabled = True
        self.recv_enabled = True
        self.on_history = None  # callable(entry)
        self.on_devices = None  # callable(devices)

        self._discovery_owned = discovery is None
        self.discovery = discovery or Discoverer(
            device_id=self.device_id, name=self.device_name, log=log
        )
        self._manual_peers = []  # [(ip, port)] 手动添加的设备
        self._post_pool = None  # 多设备异步推送线程池（惰性创建）
        self._server_sock = None
        self._threads = []
        self._running = False
        self._suppress = {}  # 网络写入本机剪贴板的哈希 -> ts，监听时跳过（TTL 自动清理）
        self._sent = {}  # hash -> ts，60 秒内不重复推送
        self._recv = {}  # hash -> ts，60 秒内不重复接收
        self._last_seq = None

    @property
    def running(self):
        return self._running

    def start(self, send_enabled=True, recv_enabled=True):
        self.stop()
        self.send_enabled = bool(send_enabled)
        self.recv_enabled = bool(recv_enabled)
        self._running = True
        self.discovery.on_devices = self.on_devices
        self.discovery.start()

        server = threading.Thread(target=self._server_loop, name="clip-srv", daemon=True)
        monitor = threading.Thread(target=self._monitor_loop, name="clip-mon", daemon=True)
        self._threads = [server, monitor]
        server.start()
        monitor.start()
        log.info("剪贴板同步启动 send=%s recv=%s", self.send_enabled, self.recv_enabled)

    def stop(self):
        self._running = False
        sock = self._server_sock
        self._server_sock = None
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        if self._post_pool is not None:
            self._post_pool.shutdown(wait=False, cancel_futures=True)
            self._post_pool = None
        if self._discovery_owned:
            self.discovery.stop()
        else:
            self.discovery.advertise["clip_port"] = None
            self.discovery.on_devices = None
        for t in self._threads:
            t.join(timeout=3)
        self._threads = []
        self._suppress.clear()
        self._last_seq = None
        log.info("剪贴板同步已停止")

    def devices(self):
        found = self.discovery.devices()
        known = {(d["ip"], d.get("clip_port")) for d in found if d.get("clip_port")}
        for ip, port in list(self._manual_peers):
            if (ip, port) not in known:
                found.append(
                    {"id": f"manual-{ip}-{port}", "name": ip, "ip": ip, "clip_port": port}
                )
        return found

    def add_manual_peer(self, ip, port):
        item = (str(ip).strip(), int(port))
        if item not in self._manual_peers:  # 去重后再触发刷新
            self._manual_peers.append(item)
            if self.on_devices:
                self.on_devices(self.devices())

    def _suppress_has(self, h):
        """哈希是否在抑制窗口内（过期条目顺带清理）。"""
        ts = self._suppress.get(h)
        if ts is None:
            return False
        if time.time() - ts < DEDUP_WINDOW:
            return True
        self._suppress.pop(h, None)
        return False

    def _suppress_add(self, h):
        self._suppress[h] = time.time()

    def copy_to_clipboard(self, text):
        """手动回贴：本机动作，不回推网络。"""
        h = hash_text(text)
        self._suppress_add(h)
        self._writer(text)

    def _save_image(self, png_bytes):
        try:
            return screenshot.save_png(png_bytes, CLIP_IMG_DIR, label="剪贴板")
        except Exception:
            return None

    # ------------------------------------------------------------------
    def _monitor_loop(self):
        if sys.platform != "win32" or _clipboard_sequence() is None:
            self._emit_log("本机剪贴板监听不可用（非 Windows 或无法读取）。")
            return
        self._last_seq = _clipboard_sequence()
        while self._running:
            time.sleep(POLL_INTERVAL)
            seq = _clipboard_sequence()
            if seq is None or seq == self._last_seq:
                continue
            self._last_seq = seq
            text = self._reader()
            if isinstance(text, str) and text:
                self._on_clip_change(text)
                continue
            # 文本为空 → 尝试图片（CF_DIB）
            png = self._img_reader()
            if png:
                self._on_clip_image(png)
                continue
            # 图片也没有 → 尝试文件列表（CF_HDROP）
            paths = self._file_reader()
            if paths:
                self._on_clip_files(paths)

    def _on_clip_image(self, png_bytes):
        """本机剪贴板出现图片：落盘 + 历史 + 推送。"""
        h = hash_bytes(png_bytes)
        if self._suppress_has(h):
            return
        img_path = self._save_image(png_bytes)
        self._add_history(h=h, device="本机", remote=False, kind="image", img_path=img_path)
        if self.send_enabled:
            self._push_image(png_bytes, h)

    def _on_clip_change(self, text):
        """本机剪贴板出现文本（由监听线程或测试调用）。"""
        h = hash_text(text)
        if self._suppress_has(h):
            return
        self._add_history(h=h, text=text, device="本机", remote=False)
        if self.send_enabled:
            self._push(text, h)

    def _on_clip_files(self, paths):
        """本机剪贴板出现文件列表：逐个读取推送（跳过文件夹/目录）。"""
        if not paths:
            return
        for p in paths:
            try:
                p = str(p)
                if not os.path.isfile(p):
                    self._emit_log(f"「{os.path.basename(p)}」是文件夹，剪贴板同步仅支持文件。")
                    continue
                size = os.path.getsize(p)
                if size > MAX_FILE_BYTES:
                    self._emit_log(
                        f"「{os.path.basename(p)}」超过 32MB，请使用文件传输页发送。")
                    continue
                with open(p, "rb") as f:
                    data = f.read(size)
            except (OSError, ValueError):
                continue
            h = hash_bytes(data)
            if self._suppress_has(h):
                return
            name = os.path.basename(p.replace("\\", "/"))
            self._add_history(h=h, device="本机", remote=False, kind="file",
                              file_name=name, file_size=size)
            if self.send_enabled:
                self._push_file(data, name, size, h)

    def _push_file(self, data, name, size, h):
        now = time.time()
        if now - self._sent.get(h, 0) < DEDUP_WINDOW:
            return
        self._sent[h] = now
        msg = json.dumps(
            {
                "type": "clip",
                "id": self.device_id,
                "name": self.device_name,
                "hash": h,
                "kind": "file",
                "fname": name,
                "size": size,
                "payload": base64.b64encode(data).decode("ascii"),
                "ts": time.time(),
            }
        ).encode("utf-8")
        if len(msg) > MAX_LINE_BYTES:
            log.warning("剪贴板文件消息过大，仅本地历史")
            return
        targets = [
            (d["ip"], int(d["clip_port"]))
            for d in self.devices()
            if d.get("clip_port")
        ]
        for ip, port in targets:
            self._post_async(ip, port, msg)
        if targets:
            log.info("剪贴板文件推送至 %d 台设备", len(targets))

    def _add_history(self, h, device, remote, text=None, img_path=None, kind="text",
                     file_path=None, file_name=None, file_size=None):
        entry = {
            "ts": time.time(),
            "device": device,
            "hash": h,
            "remote": remote,
            "kind": kind,
            "text": text,
            "img_path": img_path,
            "file_path": file_path,
            "file_name": file_name,
            "file_size": file_size,
        }
        self.history.insert(0, entry)
        del self.history[self.history_limit :]
        if self.store is not None:
            try:
                self.store.insert(entry)
            except Exception:
                pass
        if self.on_history:
            try:
                self.on_history(entry)
            except Exception:
                pass

    def _remove_img_file(self, entry):
        """删除图片条目对应的落盘文件（仅限 clipboard_images 目录内，防误删）。"""
        self.__remove_clip_file_of(entry, CLIP_IMG_DIR, "img_path")

    def _remove_clip_file(self, entry):
        """删除文件条目对应的落盘文件（仅限 clipboard_files 目录内，防误删）。"""
        self.__remove_clip_file_of(entry, CLIP_FILE_DIR, "file_path")

    def __remove_clip_file_of(self, entry, base_dir, key):
        p = entry.get(key) or ""
        if not p:
            return
        try:
            base = os.path.abspath(base_dir)
            if not os.path.abspath(p).startswith(base + os.sep):
                return  # 防御：目录穿越/外部路径不删
            os.remove(p)
        except (OSError, TypeError):
            pass

    def delete_entry(self, h):
        """删除一条历史（内存 + SQLite + 磁盘文件/图片同步）。"""
        h = str(h)
        for e in list(self.history):
            if e["hash"] != h:
                continue
            if e.get("kind") == "image":
                self._remove_img_file(e)
            elif e.get("kind") == "file":
                self._remove_clip_file(e)
            break
        self.history = [e for e in self.history if e["hash"] != h]
        if self.store is not None:
            try:
                self.store.delete(h)
            except Exception:
                pass

    def clear_history(self):
        for e in list(self.history):
            if e.get("kind") == "image":
                self._remove_img_file(e)
            elif e.get("kind") == "file":
                self._remove_clip_file(e)
        self.history.clear()
        if self.store is not None:
            try:
                self.store.clear()
            except Exception:
                pass

    def copy_image_to_clipboard(self, png_bytes):
        """手动回贴图片（本机动作，不回推网络；网络接收也经此入口写剪贴板）。"""
        h = hash_bytes(png_bytes)
        self._suppress_add(h)
        try:
            return self._img_writer(png_bytes)
        except Exception:
            return False

    def copy_file_to_clipboard(self, paths):
        """把文件路径列表回写本机剪贴板（本机动作，不回推网络）。"""
        paths = [str(p) for p in (paths or []) if os.path.isfile(str(p))]
        if not paths:
            return False
        try:
            with open(paths[0], "rb") as f:
                h = hash_bytes(f.read(MAX_FILE_BYTES + 1))
        except OSError:
            h = hash_bytes(str(paths[0]))
        self._suppress_add(h)
        try:
            return self._file_writer(paths)
        except Exception:
            return False

    def _save_file(self, data, h, name):
        """文件字节落盘到 clipboard_files（hash_安全文件名）。"""
        try:
            os.makedirs(CLIP_FILE_DIR, exist_ok=True)
            full = os.path.join(CLIP_FILE_DIR, "%s_%s" % (h, _safe_file_name(name)))
            if not os.path.exists(full):
                with open(full, "wb") as f:
                    f.write(data)
            return full
        except OSError:
            return None

    def broadcast_text(self, text, device="本机"):
        """供 UI/测试直接推送一段文本（等价于剪贴板变化）。"""
        h = hash_text(text)
        self._add_history(h=h, text=text, device=device, remote=device != "本机")
        if self.send_enabled:
            self._push(text, h)

    def _push(self, text, h):
        now = time.time()
        if now - self._sent.get(h, 0) < DEDUP_WINDOW:
            return
        if len(text.encode("utf-8", "surrogatepass")) > MAX_CLIP_BYTES:
            log.warning("剪贴板内容超过 2MB，跳过发送")
            self._emit_log("内容超过 2MB，已跳过发送。")
            return
        self._sent[h] = now
        msg = json.dumps(
            {
                "type": "clip",
                "id": self.device_id,
                "name": self.device_name,
                "hash": h,
                "text": text,
                "ts": time.time(),
            }
        ).encode("utf-8")
        targets = [
            (d["ip"], int(d["clip_port"]))
            for d in self.devices()
            if d.get("clip_port")
        ]
        for ip, port in targets:
            self._post(ip, port, msg)
        if targets:
            log.info("剪贴板文本推送至 %d 台设备", len(targets))

    def _push_image(self, png_bytes, h):
        now = time.time()
        if now - self._sent.get(h, 0) < DEDUP_WINDOW:
            return
        msg = json.dumps(
            {
                "type": "clip",
                "id": self.device_id,
                "name": self.device_name,
                "hash": h,
                "kind": "image",
                "img": base64.b64encode(png_bytes).decode("ascii"),
                "ts": time.time(),
            }
        ).encode("utf-8")
        # 与接收端缓冲上限对齐（base64 膨胀后），超限仅本地历史
        if len(msg) > MAX_LINE_BYTES:
            log.warning("剪贴板图片消息过大，仅本地历史")
            self._emit_log("图片过大，未推送到局域网设备。")
            return
        self._sent[h] = now
        targets = [
            (d["ip"], int(d["clip_port"]))
            for d in self.devices()
            if d.get("clip_port")
        ]
        for ip, port in targets:
            self._post_async(ip, port, msg)
        if targets:
            log.info("剪贴板图片推送至 %d 台设备", len(targets))

    def _post_async(self, ip, port, payload):
        """异步推送：线程池执行 _post，避免串行阻塞剪贴板监听线程。"""
        if self._post_pool is None:
            self._post_pool = ThreadPoolExecutor(max_workers=4)
        self._post_pool.submit(self._post, ip, port, payload)

    def _post(self, ip, port, payload):
        try:
            with socket.create_connection((ip, port), timeout=2) as sock:
                sock.sendall(payload + b"\n")
        except OSError as e:
            log.warning("剪贴板发送 %s:%s 失败：%s", ip, port, e)
            self._emit_log(f"发送到 {ip}:{port} 失败：{e}")

    def _server_loop(self):
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("", self.sync_port))
            server.listen(4)
            server.settimeout(1.0)
        except OSError as e:
            self._emit_log(f"同步监听 {self.sync_port} 失败：{e}（只能发送，不能接收）")
            return
        self._server_sock = server
        self.discovery.advertise["clip_port"] = self.sync_port
        self._emit_log(f"剪贴板同步已就绪（接收端口 {self.sync_port}）。")
        while self._running:
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(
                target=self._client_loop, args=(conn, addr), daemon=True
            )
            t.start()

    def _client_loop(self, conn, addr):
        peer_name = addr[0]
        buf = b""
        conn.settimeout(2.0)
        try:
            while self._running:
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                buf += data
                if len(buf) > MAX_LINE_BYTES:
                    break
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line:
                        peer_name = self._handle_line(line, peer_name) or peer_name
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handle_line(self, line, peer_name):
        try:
            msg = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return peer_name
        if msg.get("type") != "clip" or not self.recv_enabled:
            return peer_name
        peer_name = str(msg.get("name") or peer_name)
        if msg.get("kind") == "image":
            return self._handle_image(msg, peer_name)
        if msg.get("kind") == "file":
            return self._handle_file(msg, peer_name)
        text = msg.get("text")
        if not isinstance(text, str) or not text:
            return peer_name
        h = hash_text(text)  # 忽略远端 hash，本地重算，避免错 hash 引发回声
        if time.time() - self._recv.get(h, 0) < DEDUP_WINDOW:
            return peer_name
        self._recv[h] = time.time()
        self._suppress_add(h)
        self._writer(text)
        self._add_history(h=h, text=text, device=peer_name, remote=True)
        self._emit_log(f"已接收来自 {peer_name} 的剪贴板内容。")
        return peer_name

    def _handle_image(self, msg, peer_name):
        try:
            png = base64.b64decode(msg.get("img") or "")
        except Exception:
            return peer_name
        if not png:
            return peer_name
        # 防解压炸弹：IHDR 像素数超限直接丢弃
        pixels = _png_pixel_count(png)
        if pixels is None or pixels > MAX_IMAGE_PIXELS:
            log.warning("丢弃来自 %s 的超大图片（像素 %s）", peer_name, pixels)
            return peer_name
        h = hash_bytes(png)  # 本地重算，不信任远端 hash
        if time.time() - self._recv.get(h, 0) < DEDUP_WINDOW:
            return peer_name
        self._recv[h] = time.time()
        self._suppress_add(h)
        self.copy_image_to_clipboard(png)
        img_path = self._save_image(png)
        self._add_history(h=h, device=peer_name, remote=True, kind="image", img_path=img_path)
        self._emit_log(f"已接收来自 {peer_name} 的剪贴板图片。")
        return peer_name

    def _handle_file(self, msg, peer_name):
        """接收文件：解码 → 大小校验（防爆带宽/磁盘）→ 落盘 → 写回剪贴板 → 历史。"""
        try:
            data = base64.b64decode(msg.get("payload") or "")
        except Exception:
            return peer_name
        if not data:
            return peer_name
        try:
            size = int(msg.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if len(data) > MAX_FILE_BYTES or size > MAX_FILE_BYTES:
            log.warning("丢弃来自 %s 的超大文件（%d 字节）", peer_name, len(data))
            return peer_name
        name = _safe_file_name(msg.get("fname") or "file")
        h = hash_bytes(data)  # 本地重算，不信任远端 hash
        if time.time() - self._recv.get(h, 0) < DEDUP_WINDOW:
            return peer_name
        self._recv[h] = time.time()
        file_path = self._save_file(data, h, name)
        if not file_path:
            return peer_name
        self._suppress_add(h)
        self.copy_file_to_clipboard([file_path])
        if len(data) != size:
            log.warning("来自 %s 的文件大小不符：声明 %d 实际 %d", peer_name, size, len(data))
        self._add_history(
            h=h, device=peer_name, remote=True, kind="file",
            file_path=file_path, file_name=name, file_size=len(data),
        )
        self._emit_log(f"已接收来自 {peer_name} 的剪贴板文件「{name}」。")
        return peer_name

    def _emit_log(self, msg):
        if self.log:
            try:
                self.log(msg)
            except Exception:
                pass
