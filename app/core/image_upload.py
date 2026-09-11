"""图片上传核心：Imgur 匿名上传 + 自定义服务器。"""

import io
import json
import os
import sqlite3
import time
import urllib.request
import urllib.error

from ..core.config import DATA_HOME


class ImageUploader:
    def upload_imgur(self, png_bytes):
        client_id = "546c25a59c58ad7"
        req = urllib.request.Request(
            "https://api.imgur.com/3/image",
            data=png_bytes,
            headers={
                "Authorization": "Client-ID %s" % client_id,
                "Content-Type": "image/png",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            link = data.get("data", {}).get("link", "")
            delete_hash = data.get("data", {}).get("deletehash", "")
            if link:
                self._save_history("imgur", link, delete_hash, len(png_bytes))
                return {"ok": True, "data": {"url": link,
                                              "delete_hash": delete_hash}}
            return {"ok": False, "err": "上传成功但未返回链接"}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            return {"ok": False, "err": "Imgur 上传失败(%d): %s" % (e.code, body[:200])}
        except Exception as e:
            return {"ok": False, "err": "上传失败: %s" % e}

    def upload_custom(self, png_bytes, url, key=""):
        headers = {"Content-Type": "image/png"}
        if key:
            headers["Authorization"] = "Bearer %s" % key
        req = urllib.request.Request(url, data=png_bytes,
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            link = data.get("url") or data.get("link") or data.get("data", {}).get("url", "")
            if link:
                self._save_history("custom", link, "", len(png_bytes))
                return {"ok": True, "data": {"url": link}}
            return {"ok": False, "err": "上传成功但未返回链接"}
        except Exception as e:
            return {"ok": False, "err": "上传失败: %s" % e}

    def _db_path(self):
        return os.path.join(DATA_HOME, "upload_history.db")

    def _save_history(self, provider, url, delete_hash, size):
        conn = sqlite3.connect(self._db_path())
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS uploads "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "provider TEXT, url TEXT, delete_hash TEXT, "
                "size INTEGER, ts REAL)")
            conn.execute(
                "INSERT INTO uploads (provider, url, delete_hash, size, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (provider, url, delete_hash, size, time.time()))
            conn.commit()
        finally:
            conn.close()

    def list_history(self, limit=50, offset=0):
        conn = sqlite3.connect(self._db_path())
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS uploads "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "provider TEXT, url TEXT, delete_hash TEXT, "
                "size INTEGER, ts REAL)")
            cur = conn.execute(
                "SELECT id, provider, url, size, ts FROM uploads "
                "ORDER BY ts DESC LIMIT ? OFFSET ?",
                (limit, offset))
            rows = cur.fetchall()
            return [{"id": r[0], "provider": r[1], "url": r[2],
                     "size": r[3], "ts": r[4]} for r in rows]
        finally:
            conn.close()

    def delete_history(self, entry_id):
        conn = sqlite3.connect(self._db_path())
        try:
            conn.execute("DELETE FROM uploads WHERE id=?", (entry_id,))
            conn.commit()
        finally:
            conn.close()
