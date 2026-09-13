"""密码库导入导出（v5.1）：多来源解析与多格式写出。

支持导入：
- 浏览器 CSV：Chrome/Edge（name,url,username,password）、Firefox
  （url,username,password,httpRealm,...）、Safari（Title,URL,Username,...）
- 密码管理器：Bitwarden JSON/CSV、KeePass CSV（Account/Login Name/...）、
  1Password CSV（Title,Url,...）
- 本应用自有格式：明文 JSON（{entries:[...]}）与加密容器（LTVAULT1，口令解密）
- 未知 CSV：按表头模糊映射（url/uri/website、username/user、password/pass…）

导出：CSV（Chrome 兼容列 + 备注/分组）、明文 JSON、加密容器（独立口令）。
解析永远不抛给前端原始堆栈——错误进 errors 列表逐行报告。
"""

import csv
import io
import json
import os
import re
import time
from urllib.parse import urlsplit

from .vault import MAGIC, _decrypt_container, _encrypt_container

# 表头模糊映射（统一小写去空白比较）
URL_KEYS = {"url", "uri", "login_uri", "web site", "website", "origin_url",
            "webseite", "login_uri_str", "网址"}
USER_KEYS = {"username", "user name", "login", "login_username", "user",
             "login name", "login_name", "用户名", "帐号", "账号"}
PASS_KEYS = {"password", "login_password", "pass", "passwd", "密码"}
TITLE_KEYS = {"title", "name", "account", "标题", "名称"}
NOTE_KEYS = {"note", "notes", "comments", "comment", "备注", "注释"}
GROUP_KEYS = {"group", "folder", "groups", "collection", "分类", "分组"}

_ENCRYPTED_PREFIX = MAGIC + "|"


# -- 嗅探 -------------------------------------------------------------------
def sniff(path):
    """探测文件格式；返回格式标签（未知也返回可读标签，不抛异常）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return "unreadable"
    if head.startswith(_ENCRYPTED_PREFIX.encode("ascii")):
        return "encrypted"
    try:
        text = head.decode("utf-8-sig", errors="replace")
    except Exception:
        return "unknown"
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(_read_text(path))
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return "bitwarden_json"
            if isinstance(data, dict) and isinstance(data.get("entries"), list):
                return "local_json"
            return "unknown_json"
        except Exception:
            return "unknown_json"
    # CSV：看首行表头
    try:
        reader = csv.reader(io.StringIO(_read_text(path)))
        headers = [h.strip().lower() for h in next(reader, [])]
    except Exception:
        return "unknown"
    if not headers:
        return "unknown"
    hs = set(headers)
    if "login_uri" in hs and "login_password" in hs:
        return "csv_bitwarden"
    if "url" in hs and "httprealm" in hs:
        return "csv_firefox"
    if "login name" in hs and "password" in hs:
        return "csv_keepass"
    if "url" in hs and "password" in hs and ("username" in hs or "title" in hs
                                             or "name" in hs):
        return "csv_generic"
    if "password" in hs or "login_password" in hs:
        return "csv_generic"
    return "unknown"


def _read_text(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def _pick(row_map, keys):
    for k in keys:
        v = row_map.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _host_of(url):
    try:
        host = urlsplit(url if "://" in url else "https://" + url).hostname
        return host or ""
    except ValueError:
        return ""


def _norm(entry, errors, row_no, source_fmt):
    """单条规范化+校验；无效条目进 errors 返回 None。"""
    title = str(entry.get("title") or "").strip()[:120]
    username = str(entry.get("username") or "").strip()[:200]
    password = str(entry.get("password") or "")
    url = str(entry.get("url") or "").strip()[:500]
    note = str(entry.get("note") or "").strip()[:4000]
    group = str(entry.get("group") or "").strip()[:32]
    if not password and not username:
        errors.append({"row": row_no, "reason": "用户名与密码均为空"})
        return None
    if not title and url:
        title = _host_of(url) or url
    if not title:
        errors.append({"row": row_no, "reason": "缺少标题（name/title）且无 URL"})
        return None
    return {"title": title, "username": username, "password": password,
            "url": url, "note": note, "group": group, "source": source_fmt}


# -- CSV 解析 ---------------------------------------------------------------
def _parse_csv(text, fmt, errors):
    entries = []
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        errors.append({"row": 0, "reason": "文件为空"})
        return entries
    headers = [h.strip().lower() for h in rows[0]]
    # 表头模糊映射：按优先级找到对应列下标
    def col(keys):
        for k in keys:
            if k in headers:
                return headers.index(k)
        return -1
    i_title, i_url = col(TITLE_KEYS), col(URL_KEYS)
    i_user, i_pass = col(USER_KEYS), col(PASS_KEYS)
    i_note, i_group = col(NOTE_KEYS), col(GROUP_KEYS)
    if i_pass < 0 and i_user < 0:
        errors.append({"row": 1, "reason":
                       "表头缺少 username/password 列（识别到：%s）" % ", ".join(headers[:8])})
        return entries
    for no, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        get = lambda i: (row[i] if 0 <= i < len(row) else "")
        e = _norm({
            "title": get(i_title), "url": get(i_url),
            "username": get(i_user), "password": get(i_pass),
            "note": get(i_note), "group": get(i_group),
        }, errors, no, fmt)
        if e:
            entries.append(e)
    return entries


# -- JSON 解析 --------------------------------------------------------------
def _parse_bitwarden_json(data, errors):
    """Bitwarden 明文 JSON：folders 映射 + items[].login。"""
    folders = {f.get("id"): f.get("name") or ""
               for f in (data.get("folders") or []) if isinstance(f, dict)}
    entries = []
    for no, it in enumerate(data.get("items") or [], start=1):
        try:
            login = it.get("login") or {}
            uris = login.get("uris") or []
            url = ""
            if uris and isinstance(uris[0], dict):
                url = str(uris[0].get("uri") or "")
            e = _norm({
                "title": it.get("name"), "username": login.get("username"),
                "password": login.get("password"), "url": url,
                "note": it.get("notes"), "group": folders.get(it.get("folderId")),
            }, errors, no, "bitwarden_json")
            if e:
                entries.append(e)
        except Exception as ex:
            errors.append({"row": no, "reason": "条目解析失败：%s" % ex})
    return entries


def _parse_local_json(data, errors):
    entries = []
    for no, it in enumerate(data.get("entries") or [], start=1):
        e = _norm(it, errors, no, "local_json")
        if e:
            entries.append(e)
    return entries


# -- 对外入口 ----------------------------------------------------------------
def parse_file(path):
    """解析导入文件 → {format, entries, errors, total_rows}。

    任何解析失败都以 errors 呈现，不抛异常（加密容器口令错误同样进报告）。
    """
    fmt = sniff(path)
    errors = []
    if fmt == "unreadable":
        return {"format": fmt, "entries": [], "errors":
                [{"row": 0, "reason": "无法读取文件"}], "total_rows": 0}
    if fmt == "encrypted":
        # 口令解密在桥接层调用 parse_encrypted（需要用户输入口令）
        return {"format": fmt, "entries": [], "errors": [], "total_rows": 0,
                "need_password": True}
    try:
        text = _read_text(path)
    except OSError as e:
        return {"format": fmt, "entries": [], "errors":
                [{"row": 0, "reason": "无法读取文件：%s" % e}], "total_rows": 0}
    entries = []
    if fmt == "bitwarden_json":
        try:
            data = json.loads(text)
        except ValueError:
            return {"format": fmt, "entries": [], "errors":
                    [{"row": 0, "reason": "JSON 解析失败"}], "total_rows": 0}
        if data.get("encrypted"):
            return {"format": fmt, "entries": [], "errors":
                    [{"row": 0, "reason":
                      "该文件为 Bitwarden 加密导出，请改用明文 JSON 导出"}],
                    "total_rows": 0}
        entries = _parse_bitwarden_json(data, errors)
    elif fmt == "local_json":
        try:
            data = json.loads(text)
        except ValueError:
            return {"format": fmt, "entries": [], "errors":
                    [{"row": 0, "reason": "JSON 解析失败"}], "total_rows": 0}
        entries = _parse_local_json(data, errors)
    elif fmt.startswith("csv"):
        entries = _parse_csv(text, fmt, errors)
    else:
        errors.append({"row": 0, "reason":
                       "无法识别的文件格式：请使用浏览器/密码管理器导出的 CSV，"
                       "或 Bitwarden JSON / 本应用加密容器"})
    return {"format": fmt, "entries": entries, "errors": errors,
            "total_rows": len(entries) + len(errors)}


def parse_encrypted_line(line, password):
    """解密加密容器 → {format:"encrypted", entries, errors}。"""
    try:
        payload = _decrypt_container(line, str(password or ""))
    except Exception as e:
        return {"format": "encrypted", "entries": [],
                "errors": [{"row": 0, "reason": str(e)}], "total_rows": 0}
    entries = []
    errors = []
    for no, it in enumerate(payload.get("entries") or [], start=1):
        e = _norm(it, errors, no, "encrypted")
        if e:
            entries.append(e)
    return {"format": "encrypted", "entries": entries, "errors": errors,
            "total_rows": len(entries) + len(errors)}


# -- 导出 --------------------------------------------------------------------
def write_csv(entries, path):
    """Chrome 兼容列 + 备注/分组：name,url,username,password,note,group。"""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(["name", "url", "username", "password", "note", "group"])
        for e in entries:
            w.writerow([e.get("title") or "", e.get("url") or "",
                        e.get("username") or "", e.get("password") or "",
                        e.get("note") or "", e.get("group") or ""])
    return path


def write_json(entries, path):
    payload = {
        "application": "LocalToolbox",
        "version": 1,
        "exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "entries": [{
            "title": e.get("title") or "", "username": e.get("username") or "",
            "password": e.get("password") or "", "url": e.get("url") or "",
            "note": e.get("note") or "", "group": e.get("group") or "",
        } for e in entries],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def write_encrypted(entries, path, password):
    """自有加密容器（独立口令，与主库口令无关）。"""
    payload = {"version": 1, "kind": "export",
               "exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "entries": [{
                   "title": e.get("title") or "", "username": e.get("username") or "",
                   "password": e.get("password") or "", "url": e.get("url") or "",
                   "note": e.get("note") or "", "group": e.get("group") or "",
               } for e in entries]}
    line = _encrypt_container(payload, str(password or ""))
    with open(path, "w", encoding="utf-8") as f:
        f.write(line)
    return path


def sanitize_filename(stem):
    return re.sub(r'[\\/:*?"<>|]+', "_", str(stem or "vault")).strip() or "vault"
