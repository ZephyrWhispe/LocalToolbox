import os
import posixpath
from datetime import datetime, timezone
from ftplib import FTP

from . import logger as applog

log = applog.get_logger("ftp_client")


def connect(host, port, username="", password="", timeout=10):
    ftp = FTP()
    ftp.encoding = "utf-8"
    ftp.connect(host, int(port), timeout=timeout)
    ftp.login(username or "anonymous", password)
    return ftp


def disconnect(ftp):
    if ftp is None:
        return
    try:
        ftp.quit()
    except Exception:
        try:
            ftp.close()
        except Exception:
            pass


def list_directory(ftp, path):
    try:
        return list(_mlsd_entries(ftp, path))
    except Exception:
        return list(_plain_entries(ftp, path))


def _mlsd_entries(ftp, path):
    entries = []
    for name, facts in ftp.mlsd(path):
        if name in (".", ".."):
            continue
        is_dir = facts.get("type") == "dir"
        size = 0
        if not is_dir:
            size = _safe_int(facts.get("size"), 0)
        entries.append(
            {
                "name": name,
                "is_dir": is_dir,
                "size": size,
                "mtime": _parse_modify(facts.get("modify")),
            }
        )
    return entries


def _plain_entries(ftp, path):
    lines = []
    target = path or "/"
    if not target.endswith("/"):
        target += "/"
    ftp.retrlines("LIST -a " + target, lines.append)
    entries = []
    for line in lines:
        name = _name_from_list_line(line)
        if not name or name in (".", ".."):
            continue
        is_dir = line.startswith("d") or "<DIR>" in line.upper()
        size = _size_from_list_line(line, is_dir)
        entries.append(
            {"name": name, "is_dir": is_dir, "size": size, "mtime": 0}
        )
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


def _name_from_list_line(line):
    parts = line.split()
    if len(parts) <= 1:
        return ""
    if line.startswith("d") or line.startswith("-"):
        return line.split(None, 8)[-1] if len(line.split(None, 8)) > 1 else ""
    return parts[-1].strip()


def _size_from_list_line(line, is_dir):
    if is_dir:
        return 0
    parts = line.split()
    for token in parts:
        if token.startswith("size="):
            return _safe_int(token.split("=", 1)[1], 0)
    for i, token in enumerate(parts + [""] * 4):
        if i >= 1 and token.isdigit() and _looks_like_size_index(i, parts):
            return int(token)
    return 0


def _looks_like_size_index(i, parts):
    rest = parts[i + 1 :]
    return any(_looks_like_month(t) for t in rest[:3])


def _looks_like_month(token):
    months = {
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec",
    }
    return token.lower() in months


def _safe_int(text, default):
    if text is None:
        return default
    try:
        return int(text)
    except (ValueError, TypeError):
        return default


def _parse_modify(text):
    if not text:
        return 0
    try:
        dt = datetime.strptime(text.split(".")[0], "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0


def size(ftp, remote_path):
    try:
        return ftp.size(remote_path)
    except Exception:
        return None


def download(ftp, remote_path, local_path, progress=None):
    total = size(ftp, remote_path) or 0
    got = [0]

    def handler(data):
        got[0] += len(data)
        if progress:
            progress(got[0], total)

    with open(local_path, "wb") as f:
        ftp.retrbinary("RETR " + remote_path, lambda d: (f.write(d), handler(d)))

    log.info("FTP 下载完成 %s → %s（%d 字节）", remote_path, local_path, got[0])
    return local_path


def upload(ftp, local_path, remote_path, progress=None):
    total = os.path.getsize(local_path)
    sent = [0]

    def callback(data):
        sent[0] += len(data)
        if progress:
            progress(sent[0], total)

    with open(local_path, "rb") as f:
        ftp.storbinary("STOR " + remote_path, f, blocksize=65536, callback=callback)

    log.info("FTP 上传完成 %s → %s（%d 字节）", local_path, remote_path, sent[0])
    return remote_path


def delete(ftp, remote_path, is_dir):
    if is_dir:
        ftp.rmd(remote_path)
    else:
        ftp.delete(remote_path)
    log.info("FTP 删除 %s（%s）", remote_path, "目录" if is_dir else "文件")
    return remote_path


def mkdir(ftp, remote_path):
    ftp.mkd(remote_path)
    log.info("FTP 新建目录 %s", remote_path)
    return remote_path


def rename(ftp, old_path, new_path):
    ftp.rename(old_path, new_path)
    log.info("FTP 重命名 %s → %s", old_path, new_path)
    return new_path


def change_dir(ftp, path):
    ftp.cwd(path)
    return ftp.pwd()