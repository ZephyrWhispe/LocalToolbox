"""备忘录 / 密码库 WebDAV 云备份（多目标并行）。

目标来自 cfg ``backup_targets`` 列表（cap 5）：
- type="openlist"：复用网盘挂载账号——url 取 http://{openlist_host}:{openlist_port}/dav，
  凭据取 rclone_user/rclone_pwd（每次运行时读取，跟随设置热变化）；
- type="webdav"：独立配置（url/user/pwd 自填，坚果云等任意标准服务端）。

文件名带秒级时间戳永不覆盖（memo_20260911_205900.json / vault_....dat），
每目标按 backup_keep 清最旧。restore 由桥接层下载后导入。
"""

import itertools
import os
import time

from .webdav_client import WebDavClient, WebDavError

DEFAULT_DIR = "LocalToolboxBackup"
_PREFIX = {"memo": "memo_", "vault": "vault_"}
_SUFFIX = {"memo": ".json", "vault": ".dat"}
_SEQ = itertools.count()  # 进程内序号：同秒同毫秒也不重名


class BackupTargetError(Exception):
    pass


def normalize_targets(raw):
    """cfg_set 收紧用：规范目标列表（cap 5，字段逐个规范）。"""
    out = []
    for t in list(raw or [])[:5]:
        if not isinstance(t, dict):
            continue
        ttype = str(t.get("type") or "openlist")
        if ttype not in ("openlist", "webdav"):
            ttype = "openlist"
        out.append({
            "type": ttype,
            "name": str(t.get("name") or "").strip()[:32] or
                    ("网盘" if ttype == "openlist" else "WebDAV"),
            "url": str(t.get("url") or "").strip()[:300],
            "user": str(t.get("user") or "").strip()[:200],
            "pwd": str(t.get("pwd") or "")[:300],
            "dir": str(t.get("dir") or "").strip()[:120] or DEFAULT_DIR,
            "enabled": bool(t.get("enabled", True)),
        })
    return out


class CloudBackup:
    def __init__(self, cfg, log=None):
        self.cfg = cfg          # AppConfig（运行时读，跟随设置热变化）
        self.log = log or (lambda m: None)

    # -- 目标 --------------------------------------------------------------
    def targets(self, enabled_only=False):
        out = []
        for t in normalize_targets(self.cfg.get("backup_targets")):
            if enabled_only and not t["enabled"]:
                continue
            out.append(t)
        return out

    def _resolve(self, target):
        """目标 → (WebDavClient, dir)；openlist 型动态取当前服务地址与凭据。"""
        if target.get("type") == "openlist":
            host = str(self.cfg.get("openlist_host", "127.0.0.1") or "127.0.0.1")
            port = int(self.cfg.get("openlist_port", 15244) or 15244)
            url = "http://%s:%d/dav" % (host, port)
            user = str(self.cfg.get("rclone_user", "") or "")
            pwd = str(self.cfg.get("rclone_pwd", "") or "")
        else:
            url = str(target.get("url") or "")
            user = str(target.get("user") or "")
            pwd = str(target.get("pwd") or "")
        if not url:
            raise BackupTargetError("未配置 WebDAV 地址")
        try:
            client = WebDavClient(url, user, pwd, timeout=15)
        except ValueError as e:
            raise BackupTargetError(str(e))
        segs = [s for s in str(target.get("dir") or DEFAULT_DIR)
                .replace("\\", "/").split("/") if s.strip()]
        return client, segs

    # -- 备份 --------------------------------------------------------------
    def backup_one(self, target, kind, local_path):
        """单目标单类备份：mkdir（容错）→ 时间戳上传 → prune。返回远端文件名。"""
        client, segs = self._resolve(target)
        try:
            client.mkdir("/".join(segs))
        except WebDavError:
            pass  # 目录已存在（405/301 等均视为已有）
        stamp = time.strftime("%Y%m%d_%H%M%S") + "_%04d" % (next(_SEQ) % 10000)
        name = "%s%s%s" % (_PREFIX[kind], stamp, _SUFFIX[kind])
        client.upload(local_path, remote_dir="/".join(segs) if segs else None,
                      remote_path="/".join(segs + [name]) if segs else None)
        self._prune(client, segs, kind, int(self.cfg.get("backup_keep", 10) or 10))
        return name

    def _prune(self, client, segs, kind, keep):
        if keep <= 0:
            return
        try:
            info = client.listdir("/".join(segs) if segs else "/")
        except WebDavError:
            return
        files = sorted(
            (e["name"] for e in info.get("entries", [])
             if not e["is_dir"] and e["name"].startswith(_PREFIX[kind])
             and e["name"].endswith(_SUFFIX[kind])),
            reverse=True)  # 时间戳名：字典序即时间序
        for old in files[keep:]:
            try:
                client.delete("/".join(segs + [old]) if segs else old)
            except WebDavError:
                continue

    # -- 列表 / 下载 -------------------------------------------------------
    def list_remote(self, target, kind):
        client, segs = self._resolve(target)
        info = client.listdir("/".join(segs) if segs else "/")
        out = []
        for e in info.get("entries", []):
            if e["is_dir"] or not e["name"].startswith(_PREFIX[kind]) \
                    or not e["name"].endswith(_SUFFIX[kind]):
                continue
            out.append({"name": e["name"], "size": e["size"], "mtime": e["mtime"]})
        out.sort(key=lambda x: x["name"], reverse=True)
        return out

    def download_one(self, target, kind, remote_name, local_dest):
        client, segs = self._resolve(target)
        name = os.path.basename(str(remote_name or ""))
        if not name.startswith(_PREFIX[kind]) or not name.endswith(_SUFFIX[kind]):
            raise BackupTargetError("远端文件名不合法")
        remote = "/".join(segs + [name]) if segs else name
        return client.download(remote, local_dest)
