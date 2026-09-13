"""图片上传核心：Imgur 匿名上传 + 自定义服务器 + 自定义上传目标（v5.4 O2）。

自定义目标 schema（ShareX Custom Uploader 的 Python 最小子集）：
  { name, url, method: POST|PUT|GET, body: multipart|json|raw,
    headers: {k: v}, file_field: str, arguments: {k: v},
    url_path: "data.url"（响应 JSON 点路径）, url_regex: str（正则捕获组） }
变量：{file} = 临时图片文件绝对路径；{filename} = 随机文件名（含扩展名）。
"""

import base64
import io
import json
import os
import re
import sqlite3
import tempfile
import time
import urllib.request
import urllib.error
import uuid

from ..core.config import DATA_HOME

_TARGET_METHODS = ("POST", "PUT", "GET")
_TARGET_BODIES = ("multipart", "json", "raw")
_TARGET_CAP = 10


def validate_target(t):
    """校验并规范化一个上传目标配置；返回新 dict，不合法抛 ValueError（中文）。"""
    if not isinstance(t, dict):
        raise ValueError("上传目标配置格式不正确")
    name = str(t.get("name") or "").strip()
    url = str(t.get("url") or "").strip()
    if not name:
        raise ValueError("目标名称不能为空")
    if len(name) > 30:
        name = name[:30]
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("请求地址必须以 http:// 或 https:// 开头")
    method = str(t.get("method") or "POST").upper()
    if method not in _TARGET_METHODS:
        raise ValueError("请求方式仅支持 GET / POST / PUT")
    body = str(t.get("body") or "multipart").lower()
    if body not in _TARGET_BODIES:
        raise ValueError("请求体仅支持 multipart / json / raw")
    headers = t.get("headers") if isinstance(t.get("headers"), dict) else {}
    arguments = t.get("arguments") if isinstance(t.get("arguments"), dict) else {}
    return {
        "name": name, "url": url, "method": method, "body": body,
        "headers": {str(k)[:60]: str(v)[:300] for k, v in headers.items()},
        "file_field": str(t.get("file_field") or "file").strip() or "file",
        "arguments": {str(k)[:60]: str(v)[:5000] for k, v in arguments.items()},
        "url_path": str(t.get("url_path") or "").strip(),
        "url_regex": str(t.get("url_regex") or "").strip(),
    }


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

    # -- 自定义上传目标（v5.4 O2，ShareX Custom Uploader 子集） ------------
    def upload_to_target(self, png_bytes, target):
        """按目标配置上传；返回 {"ok": True, "data": {"url": ...}} 或 {"ok": False, "err": ...}。"""
        try:
            t = validate_target(target)
        except ValueError as e:
            return {"ok": False, "err": str(e)}

        filename = "LocalToolbox_%s.png" % time.strftime("%Y%m%d_%H%M%S")
        tmp_path = ""
        try:
            # {file} 变量：写入临时文件（multipart 文件本体不走此路径，直接用内存字节）
            with tempfile.NamedTemporaryFile(
                    prefix="lt_upload_", suffix=".png", delete=False) as tf:
                tf.write(png_bytes)
                tmp_path = tf.name

            headers = {str(k): str(v) for k, v in t["headers"].items()}
            url = t["url"].replace("{file}", tmp_path).replace("{filename}", filename)
            method, data, content_type = t["method"], None, None

            if t["body"] == "multipart":
                boundary = uuid.uuid4().hex
                content_type = "multipart/form-data; boundary=" + boundary
                parts = []
                for k, v in t["arguments"].items():
                    v = v.replace("{file}", tmp_path).replace("{filename}", filename)
                    parts.append(("--%s\r\nContent-Disposition: form-data; "
                                  "name=\"%s\"\r\n\r\n%s\r\n"
                                  % (boundary, k, v)).encode("utf-8"))
                parts.append(
                    ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; "
                     "filename=\"%s\"\r\nContent-Type: image/png\r\n\r\n"
                     % (boundary, t["file_field"], filename)).encode("utf-8"))
                parts.append(png_bytes)
                parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
                data = b"".join(parts)
            elif t["body"] == "json":
                # {file} 在 JSON 体内替换为图片 base64（内容过大时不适合放路径）
                def _sub(v):
                    if "{file}" in v:
                        return v.replace("{file}",
                                         base64.b64encode(png_bytes).decode("ascii"))
                    return v.replace("{filename}", filename)
                payload = {k: _sub(v) for k, v in t["arguments"].items()}
                payload[t["file_field"]] = payload.pop(
                    t["file_field"], base64.b64encode(png_bytes).decode("ascii"))
                payload["filename"] = payload.get("filename", filename)
                data = json.dumps(payload).encode("utf-8")
                content_type = "application/json"
            else:  # raw
                data = png_bytes
                content_type = "image/png"

            headers.setdefault("Content-Type", content_type or "application/octet-stream")
            req = urllib.request.Request(url, data=data, headers=headers,
                                         method=method)
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            link = _extract_url(text, t["url_path"], t["url_regex"])
            if link:
                self._save_history("target:%s" % t["name"], link, "", len(png_bytes))
                return {"ok": True, "data": {"url": link}}
            return {"ok": False,
                    "err": "上传完成但未能从响应提取链接（请检查 URL 提取路径/正则）"}
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            return {"ok": False, "err": "上传失败(HTTP %d): %s" % (e.code, body)}
        except Exception as e:
            return {"ok": False, "err": "上传失败: %s" % e}
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

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


def _extract_url(text, url_path="", url_regex=""):
    """从响应文本提取链接：JSON 点路径 → 常见兜底路径 → 正则捕获组 → 纯文本。"""
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    if obj is not None:
        candidates = [url_path, "url", "link", "data.url", "data.link"]
        for path in candidates:
            if not path:
                continue
            cur = obj
            ok = True
            for part in str(path).split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    ok = False
                    break
            if ok and isinstance(cur, str) and cur.strip():
                return cur.strip()
    if url_regex:
        try:
            m = re.search(url_regex, text)
            if m:
                return (m.group(1) if m.groups() else m.group(0)).strip()
        except re.error:
            pass
    t = text.strip()
    if t.startswith(("http://", "https://")) and len(t) < 2000:
        return t.split()[0]
    return ""
