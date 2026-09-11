"""剪贴板历史监控核心：监听系统剪贴板变化并存储历史。"""

import ctypes
import ctypes.wintypes
import io
import os
import sqlite3
import threading
import time

from ctypes import wintypes

from ..core import logger as applog
from ..core.config import DATA_HOME

log = applog.get_logger("clipmon")

user32 = ctypes.WinDLL("user32")   # 私有实例：argtypes 与 hotkey.py 共享单例的声明互相隔离
kernel32 = ctypes.WinDLL("kernel32")   # 同上（screenshot/clipboard_sync 在共享实例上声明 GlobalLock）
kernel32.GetModuleHandleW.restype = ctypes.c_void_p   # 64 位下 HMODULE 为大整数，不声明会溢出
# 项目铁律：Win32 ctypes 必须声明 argtypes/restype —— 否则 64 位句柄按 c_int 截断溢出
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.restype = ctypes.c_ssize_t   # LRESULT（3.12 wintypes 已移除该别名）
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
# 剪贴板监听器注册（HWND 在 64 位为大整数，不声明 argtypes 会截断为 c_int 导致注册失败）
user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
user32.AddClipboardFormatListener.restype = wintypes.BOOL
user32.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
user32.RemoveClipboardFormatListener.restype = wintypes.BOOL
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
# 剪贴板数据读取：GlobalLock 返回 64 位指针，不声明 restype 会截断导致读取失败
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
WM_CLIPBOARDUPDATE = 0x031D
CF_TEXT = 1
CF_UNICODETEXT = 13
CF_DIB = 8
CF_HDROP = 15

HWND_MESSAGE = ctypes.c_void_p(-3)
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
)


class _WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
        ("hIconSm", ctypes.c_void_p),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_void_p),
        ("lParam", ctypes.c_void_p),
        ("time", ctypes.c_ulong),
        ("pt", ctypes.c_long * 2),
    ]

user32.GetMessageW.argtypes = [ctypes.POINTER(_MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]


class ClipboardMonitor:
    def __init__(self, limit=500, monitor_images=True):
        self._limit = limit
        self._monitor_images = monitor_images
        self._running = False
        self._thread = None
        self._on_change = None
        self._db_path = os.path.join(DATA_HOME, "clip_history.db")
        self._img_dir = os.path.join(DATA_HOME, "clip_images")
        os.makedirs(self._img_dir, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS clip_history "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "kind TEXT, text TEXT, img_path TEXT, ts REAL, pinned INTEGER DEFAULT 0)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_clip_ts ON clip_history(ts DESC)")
            conn.commit()
        finally:
            conn.close()

    @property
    def running(self):
        return self._running

    def start(self, on_change=None):
        if self._running:
            return
        self._on_change = on_change
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="clip-monitor")
        self._thread.start()

    def stop(self):
        self._running = False
        hwnd = getattr(self, "_clip_hwnd", None)
        if hwnd:
            try:
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _run(self):
        cls_name = "LocalToolboxClipMon_%d" % os.getpid()

        def wnd_proc(hwnd, msg, wp, lp):
            # 回调参数可能为 None（NULL 消息参数），argtypes 需要整数
            hwnd, msg, wp, lp = hwnd or 0, msg or 0, wp or 0, lp or 0
            if msg == WM_CLIPBOARDUPDATE:
                time.sleep(0.05)
                seq = user32.GetClipboardSequenceNumber()
                if seq != self._last_seq:
                    self._last_seq = seq
                    self._capture_clipboard()
                return 0
            return user32.DefWindowProcW(hwnd, msg, wp, lp)

        self._wndproc = WNDPROC(wnd_proc)
        hInst = kernel32.GetModuleHandleW(None)

        wc = _WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(_WNDCLASSEX)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hInst
        wc.lpszClassName = cls_name
        user32.RegisterClassExW(ctypes.byref(wc))

        self._clip_hwnd = user32.CreateWindowExW(
            0, cls_name, "ClipMon", 0, 0, 0, 0, 0,
            HWND_MESSAGE, None, hInst, None,
        )
        user32.AddClipboardFormatListener(self._clip_hwnd)

        msg = _MSG()
        self._last_seq = user32.GetClipboardSequenceNumber()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == -1 or ret == 0:
                break
            if ret > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        try:
            user32.RemoveClipboardFormatListener(self._clip_hwnd)
        except Exception:
            pass
        try:
            user32.DestroyWindow(self._clip_hwnd)
        except Exception:
            pass

    def _capture_clipboard(self):
        """捕获当前剪贴板内容入库。回调线程内的异常会被系统静默吞掉，
        必须自己兜底记录日志——否则捕获失败完全无迹可寻。"""
        try:
            self._capture_inner()
        except Exception as e:
            try:
                log.error("剪贴板捕获失败：%s", e)
            except Exception:
                pass

    def _capture_inner(self):
        kind = None
        text = ""
        img_path = ""

        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            kind = "text"
            if user32.OpenClipboard(0):
                try:
                    handle = user32.GetClipboardData(CF_UNICODETEXT)
                    if handle:
                        ptr = kernel32.GlobalLock(handle)
                        if ptr:
                            text = ctypes.wstring_at(ptr)
                            kernel32.GlobalUnlock(handle)
                finally:
                    user32.CloseClipboard()
        elif user32.IsClipboardFormatAvailable(CF_DIB):
            if self._monitor_images:
                kind = "image"
                try:
                    from .screenshot import read_clipboard_png
                    png = read_clipboard_png()
                    if png:
                        fname = "clip_%d.png" % int(time.time() * 1000)
                        img_path = os.path.join(self._img_dir, fname)
                        with open(img_path, "wb") as f:
                            f.write(png)
                except Exception:
                    kind = None

        if kind and text or img_path:
            self._save_entry(kind, text, img_path)
            if self._on_change:
                self._on_change()

    def _save_entry(self, kind, text, img_path):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT INTO clip_history (kind, text, img_path, ts) VALUES (?, ?, ?, ?)",
                (kind, text, img_path, time.time()))
            self._prune(conn)
            conn.commit()
        finally:
            conn.close()

    def _prune(self, conn):
        conn.execute(
            "DELETE FROM clip_history WHERE pinned=0 AND id NOT IN "
            "(SELECT id FROM clip_history ORDER BY ts DESC LIMIT ?)",
            (self._limit,))

    def list_entries(self, limit=50, offset=0, query=""):
        conn = sqlite3.connect(self._db_path)
        try:
            if query:
                sql = ("SELECT id, kind, text, img_path, ts, pinned FROM clip_history "
                       "WHERE text LIKE ? ORDER BY ts DESC LIMIT ? OFFSET ?")
                params = ("%%%s%%" % query, limit, offset)
            else:
                sql = ("SELECT id, kind, text, img_path, ts, pinned FROM clip_history "
                       "ORDER BY ts DESC LIMIT ? OFFSET ?")
                params = (limit, offset)
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
            return [{"id": r[0], "kind": r[1], "text": r[2] or "",
                     "img_path": r[3] or "", "ts": r[4],
                     "pinned": bool(r[5])} for r in rows]
        finally:
            conn.close()

    def delete_entry(self, entry_id):
        conn = sqlite3.connect(self._db_path)
        try:
            cur = conn.execute(
                "SELECT img_path FROM clip_history WHERE id=?", (entry_id,))
            row = cur.fetchone()
            if row and row[0] and os.path.isfile(row[0]):
                try:
                    os.remove(row[0])
                except OSError:
                    pass
            conn.execute("DELETE FROM clip_history WHERE id=?", (entry_id,))
            conn.commit()
        finally:
            conn.close()

    def pin_entry(self, entry_id, pinned=True):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("UPDATE clip_history SET pinned=? WHERE id=?",
                         (int(pinned), entry_id))
            conn.commit()
        finally:
            conn.close()

    def clear_all(self):
        conn = sqlite3.connect(self._db_path)
        try:
            cur = conn.execute("SELECT img_path FROM clip_history WHERE img_path != ''")
            for row in cur.fetchall():
                if row[0] and os.path.isfile(row[0]):
                    try:
                        os.remove(row[0])
                    except OSError:
                        pass
            conn.execute("DELETE FROM clip_history")
            conn.commit()
        finally:
            conn.close()

    def get_image_data(self, entry_id):
        conn = sqlite3.connect(self._db_path)
        try:
            cur = conn.execute(
                "SELECT img_path FROM clip_history WHERE id=?", (entry_id,))
            row = cur.fetchone()
            if row and row[0] and os.path.isfile(row[0]):
                with open(row[0], "rb") as f:
                    return f.read()
            return None
        finally:
            conn.close()

    def get_entry(self, entry_id):
        """按 id 取单条历史（弹窗预览 / 粘贴编排用）；不存在返回 None。"""
        conn = sqlite3.connect(self._db_path)
        try:
            cur = conn.execute(
                "SELECT id, kind, text, img_path, ts, pinned FROM clip_history "
                "WHERE id=?", (entry_id,))
            r = cur.fetchone()
            if not r:
                return None
            return {"id": r[0], "kind": r[1], "text": r[2] or "",
                    "img_path": r[3] or "", "ts": r[4], "pinned": bool(r[5])}
        finally:
            conn.close()

    def paste_to_clipboard(self, entry_id):
        conn = sqlite3.connect(self._db_path)
        try:
            cur = conn.execute(
                "SELECT kind, text, img_path FROM clip_history WHERE id=?",
                (entry_id,))
            row = cur.fetchone()
            if not row:
                return False
            kind, text, img_path = row
            if kind == "text" and text:
                import pyperclip
                pyperclip.copy(text)
                return True
            elif kind == "image" and img_path and os.path.isfile(img_path):
                with open(img_path, "rb") as f:
                    png = f.read()
                from .screenshot import copy_png_to_clipboard
                copy_png_to_clipboard(png)
                return True
            return False
        finally:
            conn.close()
