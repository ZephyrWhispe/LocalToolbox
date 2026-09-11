"""FTP 页桥接：服务端（pyftpdlib 发布本地目录）+ 客户端（ftplib 浏览/传输）。

移植自 app/ui/ftp_tab.py，行为保持一致：
- 服务端：启动/停止、匿名或用户名口令、只读/可写、防火墙放行与移除；
- 客户端：连接/断开、目录浏览、上传/下载/新建文件夹/删除；
- 上传/下载通过 ftp_progress 事件推送进度，服务端日志通过 ftp_log 推送；
- 同一时刻仅允许一个客户端操作（与旧页 _busy 语义一致），忙时直接返回错误。
"""

import os
import posixpath
import threading
import time

import webview

from ..core import ftp_client, ftp_server

PROGRESS_INTERVAL = 0.15  # 进度事件推送节流（秒）


class FtpApi:
    def _init_ftp(self):
        self._srv = ftp_server.FtpServer()
        self._ftp = None  # ftplib.FTP 连接，仅本模块内使用
        self._ftp_remote = "/"
        self._ftp_lock = threading.Lock()
        self._fw_open = False

    # -- 内部 ------------------------------------------------------------
    def _srv_log(self, msg):
        self.emit("ftp_log", str(msg))

    def _srv_state(self):
        s = self._srv
        return {
            "running": s.running,
            "host": s.host,
            "port": s.port,
            "root": s.root_dir,
            "username": s.username,
            "allow_write": s.allow_write,
            "fw_open": self._fw_open,
        }

    def _progress_cb(self, op):
        last = [0.0]

        def cb(done, total):
            now = time.monotonic()
            if (total > 0 and done >= total) or (now - last[0] >= PROGRESS_INTERVAL):
                last[0] = now
                self.emit("ftp_progress", {"op": op, "done": done, "total": total})

        return cb

    def _pick_entries(self, entries):
        items = []
        for e in entries or []:
            if not isinstance(e, dict):
                continue
            name = str(e.get("name", "")).strip()
            if name:
                items.append({"name": name, "is_dir": bool(e.get("is_dir"))})
        return items

    # -- 状态 ------------------------------------------------------------
    def ftp_state(self):
        return {
            "ok": True,
            "data": {
                "server": self._srv_state(),
                "client": {
                    "connected": self._ftp is not None,
                    "remote": self._ftp_remote,
                },
            },
        }

    # -- 服务端 ----------------------------------------------------------
    def ftp_server_start(
        self, host, port, root, username="", password="", allow_write=True, add_fw=True
    ):
        root = str(root or "").strip()
        if not root:
            return {"ok": False, "err": "请先选择要共享的目录。"}
        try:
            port = int(port)
        except (TypeError, ValueError):
            return {"ok": False, "err": "端口号必须在 1-65535 之间。"}
        try:
            self._srv.start(
                str(host or "0.0.0.0").strip() or "0.0.0.0",
                port,
                root,
                str(username or ""),
                str(password or ""),
                bool(allow_write),
                log_callback=self._srv_log,
            )
        except Exception as e:
            return {"ok": False, "err": str(e)}
        if bool(add_fw):
            ok, msg = ftp_server.add_firewall_rule(port)
            if ok:
                self._fw_open = True
                self._srv_log("已放行防火墙入站端口 %d" % port)
            else:
                self._srv_log("防火墙放行失败：" + ((msg or "").strip() or "未授权或命令出错"))
        self._srv_log("FTP 服务已启动，目录：%s" % self._srv.root_dir)
        ips = [ip for ip in ftp_server.local_ip_addresses() if ip]
        if ips:
            pieces = ["ftp://%s:%d" % (ip, self._srv.port) for ip in ips]
            self._srv_log("局域网访问地址：\n" + "\n".join("  " + p for p in pieces))
        return {"ok": True, "data": self._srv_state()}

    def ftp_server_stop(self):
        try:
            self._srv.stop()
        except Exception as e:
            return {"ok": False, "err": str(e)}
        self._srv_log("FTP 服务已停止。")
        return {"ok": True, "data": self._srv_state()}

    def ftp_fw_remove(self):
        ok, msg = ftp_server.remove_firewall_rule(self._srv.port)
        if ok:
            self._fw_open = False
            self._srv_log("已移除防火墙放行规则。")
            return {"ok": True, "data": self._srv_state()}
        return {"ok": False, "err": "移除失败：" + (msg or "")}

    # -- 客户端 ----------------------------------------------------------
    def ftp_connect(self, host, port, username="", password=""):
        host = str(host or "").strip()
        if not host:
            return {"ok": False, "err": "请输入 FTP 服务器地址。"}
        if self._ftp is not None:
            return {"ok": False, "err": "已连接，请先断开。"}
        if not self._ftp_lock.acquire(blocking=False):
            return {"ok": False, "err": "有操作正在进行，请稍候。"}
        try:
            ftp = ftp_client.connect(
                host, int(port), str(username or "").strip(), str(password or "")
            )
        except Exception as e:
            return {"ok": False, "err": str(e)}
        finally:
            self._ftp_lock.release()
        self._ftp = ftp
        self._ftp_remote = "/"
        return {"ok": True, "data": True}

    def ftp_disconnect(self):
        if self._ftp is None:
            return {"ok": True, "data": True}
        if not self._ftp_lock.acquire(blocking=False):
            return {"ok": False, "err": "有操作正在进行，请稍候。"}
        try:
            ftp_client.disconnect(self._ftp)
        finally:
            self._ftp_lock.release()
        self._ftp = None
        self._ftp_remote = "/"
        return {"ok": True, "data": True}

    def ftp_list(self, path):
        if self._ftp is None:
            return {"ok": False, "err": "尚未连接。"}
        if not self._ftp_lock.acquire(blocking=False):
            return {"ok": False, "err": "有操作正在进行，请稍候。"}
        try:
            entries = ftp_client.list_directory(self._ftp, str(path or "/"))
        except Exception as e:
            return {"ok": False, "err": str(e)}
        finally:
            self._ftp_lock.release()
        self._ftp_remote = str(path or "/")
        return {"ok": True, "data": {"path": self._ftp_remote, "entries": entries}}

    def ftp_upload(self, paths):
        if self._ftp is None:
            return {"ok": False, "err": "尚未连接。"}
        files = [str(p) for p in (paths or []) if str(p).strip()]
        if not files:
            return {"ok": False, "err": "请先选择要上传的文件。"}
        if not self._ftp_lock.acquire(blocking=False):
            return {"ok": False, "err": "有操作正在进行，请稍候。"}
        try:
            remote_dir = self._ftp_remote
            progress = self._progress_cb("上传")
            for lp in files:
                rp = posixpath.join(remote_dir.rstrip("/") or "/", os.path.basename(lp))
                ftp_client.upload(self._ftp, lp, rp, progress)
        except Exception as e:
            return {"ok": False, "err": str(e)}
        finally:
            self._ftp_lock.release()
        return {"ok": True, "data": {"count": len(files)}}

    def ftp_download(self, entries, local_dir):
        if self._ftp is None:
            return {"ok": False, "err": "尚未连接。"}
        items = self._pick_entries(entries)
        if not items:
            return {"ok": False, "err": "请先在列表中选中要下载的文件。"}
        if any(e["is_dir"] for e in items):
            return {"ok": False, "err": "暂不支持从 FTP 下载文件夹，请只选择文件。"}
        folder = str(local_dir or "").strip()
        if not folder:
            return {"ok": False, "err": "请先选择下载保存目录。"}
        if not self._ftp_lock.acquire(blocking=False):
            return {"ok": False, "err": "有操作正在进行，请稍候。"}
        try:
            remote_dir = self._ftp_remote
            progress = self._progress_cb("下载")
            for e in items:
                rp = posixpath.join(remote_dir.rstrip("/") or "/", e["name"])
                lp = os.path.join(folder, e["name"])
                ftp_client.download(self._ftp, rp, lp, progress)
        except Exception as e:
            return {"ok": False, "err": str(e)}
        finally:
            self._ftp_lock.release()
        return {"ok": True, "data": {"count": len(items)}}

    def ftp_mkdir(self, name):
        if self._ftp is None:
            return {"ok": False, "err": "尚未连接。"}
        name = str(name or "").strip()
        if not name:
            return {"ok": False, "err": "文件夹名称不能为空。"}
        rp = posixpath.join(self._ftp_remote.rstrip("/") or "/", name)
        if not self._ftp_lock.acquire(blocking=False):
            return {"ok": False, "err": "有操作正在进行，请稍候。"}
        try:
            ftp_client.mkdir(self._ftp, rp)
        except Exception as e:
            return {"ok": False, "err": str(e)}
        finally:
            self._ftp_lock.release()
        return {"ok": True, "data": {"path": rp}}

    def ftp_delete(self, entries):
        if self._ftp is None:
            return {"ok": False, "err": "尚未连接。"}
        items = self._pick_entries(entries)
        if not items:
            return {"ok": False, "err": "请先选择要删除的项目。"}
        if not self._ftp_lock.acquire(blocking=False):
            return {"ok": False, "err": "有操作正在进行，请稍候。"}
        try:
            remote_dir = self._ftp_remote
            for e in items:
                rp = posixpath.join(remote_dir.rstrip("/") or "/", e["name"])
                ftp_client.delete(self._ftp, rp, e["is_dir"])
        except Exception as e:
            return {"ok": False, "err": str(e)}
        finally:
            self._ftp_lock.release()
        return {"ok": True, "data": {"count": len(items)}}

    # -- 原生文件/目录选择对话框 ------------------------------------------
    def ftp_pick_folder(self):
        if self._window is None:
            return {"ok": False, "err": "窗口尚未就绪。"}
        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory="")
        except Exception as e:
            return {"ok": False, "err": str(e)}
        return {"ok": True, "data": result[0] if result else None}

    def ftp_pick_files(self):
        if self._window is None:
            return {"ok": False, "err": "窗口尚未就绪。"}
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, directory="", allow_multiple=True
            )
        except Exception as e:
            return {"ok": False, "err": str(e)}
        return {"ok": True, "data": list(result) if result else []}
