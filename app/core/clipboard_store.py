"""剪贴板历史 SQLite 落盘：重启后历史不丢失。

设计：ClipboardSync 的 history 内存列表仍为唯一实时源（最新在前），
本模块只做持久化镜像 —— insert/delete/clear 时同步写库，启动时 load 回填。
多线程安全：单连接 + check_same_thread=False + 内部锁。

可选加密（T-02 / v2.9）：构造时传入 key（32B），text 字段以
AES-256-GCM（iv12 + 密文）落盘，前缀 enc:v1:；密钥由 clip_store_key()
DPAPI 保护，仅本机当前用户可解。
"""

import base64
import os
import sqlite3
import sys
import threading
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import DATA_HOME

_ENC_PREFIX = "enc:v1:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clip_history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL,
    device  TEXT,
    hash    TEXT UNIQUE,
    kind    TEXT,
    text    TEXT,
    img_path TEXT,
    remote  INTEGER
)
"""

# v2.8 新增：文件同步的历史列（旧库 ALTER 补列，见 __init__）
_FILE_COLUMNS = (
    ("file_path TEXT", "file_path"),
    ("file_name TEXT", "file_name"),
    ("file_size INTEGER", "file_size"),
)

_INSERT = """INSERT OR REPLACE INTO clip_history
    (ts, device, hash, kind, text, img_path, remote, file_path, file_name, file_size)
    VALUES (?,?,?,?,?,?,?,?,?,?)"""

_SELECT = ("SELECT ts, device, hash, kind, text, img_path, remote,"
           " file_path, file_name, file_size"
           " FROM clip_history ORDER BY ts DESC, id DESC LIMIT ?")


def _encrypt_text(text, key):
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, text.encode("utf-8"), None)
    return _ENC_PREFIX + base64.b64encode(iv + ct).decode("ascii")


def _decrypt_text(s, key):
    try:
        assert s.startswith(_ENC_PREFIX)
        raw = base64.b64decode(s[len(_ENC_PREFIX):])
        return AESGCM(key).decrypt(raw[:12], raw[12:], None).decode("utf-8")
    except Exception:
        return None  # 密钥缺失/变更或数据损坏


def clip_store_key(data_home=DATA_HOME):
    """返回 32B 历史加密密钥；非 Windows 或失败返回 None（加密自动禁用）。"""
    if sys.platform != "win32":
        return None
    path = os.path.join(data_home, "clip_enc.key")
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                blob = base64.b64decode(f.read())
            key = _dpapi(blob, protect=False)
            return key if key and len(key) == 32 else None
        key = os.urandom(32)
        blob = _dpapi(key, protect=True)
        if not blob:
            return None
        os.makedirs(data_home, exist_ok=True)
        with open(path, "wb") as f:
            f.write(base64.b64encode(blob))
        return key
    except OSError:
        return None


def _dpapi(data, protect=True):
    """CryptProtectData / CryptUnprotectData（仅本机当前用户可解）。"""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(wintypes.BYTE))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    inb = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(wintypes.BYTE)))
    outb = DATA_BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # 第 6 参：0=默认提示 / CRYPTPROTECT_UI_FORBIDDEN=不弹窗
    flags = 0 if protect else 1
    if not fn(ctypes.byref(inb), None, None, None, None, flags, ctypes.byref(outb)):
        return None
    try:
        return ctypes.string_at(outb.pbData, outb.cbData) or None
    finally:
        kernel32.LocalFree(outb.pbData)


class ClipboardStore:
    def __init__(self, path, limit=100, key=None, days=0):
        self.path = path
        self.limit = int(limit)
        self.days = max(0, int(days))  # v3.5f：历史保留天数（0=永久）
        self.key = key if (key and len(key) == 32) else None
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=5)
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._ensure_file_columns()
            self._conn.commit()

    def _ensure_file_columns(self):
        for ddl, col in _FILE_COLUMNS:
            try:
                self._conn.execute("ALTER TABLE clip_history ADD COLUMN " + ddl)
            except sqlite3.Error:
                pass  # 列已存在

    def insert(self, entry):
        with self._lock:
            try:
                raw_text = entry.get("text")
                if self.key and isinstance(raw_text, str) and entry.get("kind", "text") == "text":
                    raw_text = _encrypt_text(raw_text, self.key)
                self._conn.execute(
                    _INSERT,
                    (
                        entry["ts"],
                        entry["device"],
                        entry["hash"],
                        entry.get("kind", "text"),
                        raw_text,
                        entry.get("img_path"),
                        1 if entry.get("remote") else 0,
                        entry.get("file_path"),
                        entry.get("file_name"),
                        entry.get("file_size"),
                    ),
                )
                self._conn.commit()
                self._prune()
            except sqlite3.Error:
                pass

    def delete(self, h):
        with self._lock:
            try:
                self._conn.execute(
                    "DELETE FROM clip_history WHERE hash=?", (str(h),))
                self._conn.commit()
            except sqlite3.Error:
                pass

    def clear(self):
        with self._lock:
            try:
                self._conn.execute("DELETE FROM clip_history")
                self._conn.commit()
            except sqlite3.Error:
                pass

    def load(self, limit=None):
        with self._lock:
            try:
                rows = self._conn.execute(
                    _SELECT, (int(limit or self.limit),)).fetchall()
            except sqlite3.Error:
                return []
        out = []
        for ts, device, h, kind, text, img_path, remote, file_path, file_name, file_size in rows:
            entry = {
                "ts": ts, "device": device, "hash": h,
                "kind": kind or "text", "img_path": img_path, "remote": bool(remote),
                "file_path": file_path, "file_name": file_name,
                "file_size": file_size,
            }
            if (kind or "text") == "text":
                if isinstance(text, str) and text.startswith(_ENC_PREFIX):
                    if self.key:
                        entry["text"] = _decrypt_text(text, self.key) or ""
                        entry["enc_lost"] = entry["text"] == ""
                    else:
                        entry["text"] = ""
                        entry["enc_lost"] = True
                else:
                    entry["text"] = text or ""
            out.append(entry)
        return out

    def count(self):
        with self._lock:
            try:
                return self._conn.execute(
                    "SELECT COUNT(*) FROM clip_history").fetchone()[0]
            except sqlite3.Error:
                return 0

    def close(self):
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def set_days(self, days):
        """更新保留天数并立即清理过期记录（v3.5f，0=永久）。"""
        self.days = max(0, int(days))
        if self.days <= 0:
            return 0
        with self._lock:
            try:
                cutoff = time.time() - self.days * 86400
                cur = self._conn.execute(
                    "DELETE FROM clip_history WHERE ts < ?", (cutoff,))
                self._conn.commit()
                return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            except sqlite3.Error:
                return 0

    def _prune(self):
        # 超出上限时删除最旧记录（按 ts 倒序保留 limit 条）；v3.5f：同时按天数清理
        if self.days > 0:
            try:
                self._conn.execute(
                    "DELETE FROM clip_history WHERE ts < ?",
                    (time.time() - self.days * 86400,))
            except sqlite3.Error:
                pass
        self._conn.execute(
            "DELETE FROM clip_history WHERE id NOT IN"
            " (SELECT id FROM clip_history ORDER BY ts DESC, id DESC LIMIT ?)",
            (self.limit,),
        )