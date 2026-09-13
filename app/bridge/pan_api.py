"""网盘挂载（OpenList + WebDAV）页桥接：服务托管 / 浏览上传下载 / 盘符映射。

js_api 契约：方法名 ``pan_动作``，返回 {ok, data} / {ok, err}；
后台任务进度经 ``pan_task`` 事件推送，服务日志经 ``pan_log`` 推送。
"""

import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..core import bindl, openlist as openlist_mod, winfsp

# 窗口化进程里跑 net use 等控制台命令会闪黑窗，必须禁用
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
from ..core.config import DATA_HOME, AppConfig
from ..core.webdav_client import WebDavClient, WebDavError
from .web_api import _pick_folder

_THROTTLE = 0.2  # 进度事件节流


def _pick_file(bridge):
    """系统文件选择对话框，取消返回 None。"""
    import webview

    if bridge._window is None:
        raise RuntimeError("窗口尚未就绪，无法打开文件选择框。")
    result = bridge._window.create_file_dialog(webview.OPEN_DIALOG)
    if not result:
        return None
    if isinstance(result, (list, tuple)):
        return result[0] if result else None
    return result


class _TaskCancelled(Exception):
    pass


class PanApi:
    def _init_pan(self):
        self._pan = openlist_mod.OpenListManager(
            log_callback=lambda m: self.emit("pan_log", str(m))
        )
        self._pan_bin = ""
        self._pan_tasks = {}
        self._pan_seq = 0
        self._pan_lock = threading.Lock()
        self._pan_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pan")
        self._tray_notify = None  # v3.2：托盘通知钩子（main.py 注入）
        # v3.2：实时监控事件（服务状态/运行时长/性能指标），3s 一次
        self._pan_metrics_stop = threading.Event()
        self._pan_metrics_thread = threading.Thread(
            target=self._pan_metrics_loop, daemon=True, name="pan-metrics"
        )
        self._pan_metrics_thread.start()

    def set_tray_notify(self, cb):
        """注入托盘通知回调（title, msg），OpenList 异常退出时弹通知。"""
        self._tray_notify = cb
        if hasattr(self, "_pan"):
            self._pan.exit_callback = self._on_pan_exit

    def set_tray_quit(self, cb):
        """注入托盘退出回调（v3.5c：关闭按钮"退出程序"选项走统一退出链路）。"""
        self._tray_quit = cb

    def _on_pan_exit(self):
        try:
            if callable(self._tray_notify):
                self._tray_notify(
                    "OpenList 服务停止",
                    "OpenList 进程已退出（异常或手动关闭）。可在「网盘挂载」页查看日志并重启。",
                )
        except Exception:
            pass

    def _pan_metrics_loop(self):
        while not self._pan_metrics_stop.wait(3.0):
            try:
                st = self._pan.state()
            except Exception:
                continue
            if not st.get("running"):
                continue
            try:
                self.emit(
                    "pan_metrics",
                    {k: st.get(k) for k in (
                        "running", "healthy", "status", "uptime", "cpu", "mem_mb",
                        "port", "drives", "space",
                    )},
                )
            except Exception:
                pass

    # -- 内部辅助 ---------------------------------------------------------
    def _pan_cfg(self):
        if not hasattr(self, "cfg"):
            self.cfg = AppConfig()
        return self.cfg

    def _pan_dav(self, timeout=10):
        srv = self._pan
        if not srv.running:
            raise WebDavError(0, "OpenList 未运行，请先启动服务")
        return WebDavClient(
            "http://%s:%d/dav" % (srv.host, srv.port),
            timeout=timeout,
        )

    def _pan_new_task(self, kind, name):
        with self._pan_lock:
            self._pan_seq += 1
            tid = "pan%d" % self._pan_seq
            t = {
                "id": tid,
                "kind": kind,
                "name": name,
                "done": 0,
                "total": 0,
                "speed": None,
                "status": "queued",
                "err": "",
                "cancel": False,
                "_last_emit": 0.0,
                "_start": None,
            }
            self._pan_tasks[tid] = t
            return tid, t

    def _pan_emit_task(self, task_id):
        t = self._pan_tasks.get(task_id)
        if not t:
            return
        payload = {k: t[k] for k in ("id", "kind", "name", "done", "total", "speed", "status", "err")}
        self.emit("pan_task", payload)

    def _pan_worker(self, task_id, fn):
        t = self._pan_tasks[task_id]
        t["status"] = "running"
        t["_start"] = time.time()

        def progress(done, total):
            now = time.time()
            t["done"], t["total"] = done, total
            if t.get("cancel"):
                raise _TaskCancelled()
            if now - t["_last_emit"] >= _THROTTLE:
                t["_last_emit"] = now
                elapsed = now - (t["_start"] or now)
                t["speed"] = (done / elapsed) if elapsed > 0 else 0
                self._pan_emit_task(task_id)

        try:
            fn(progress)
            t["done"], t["total"] = t["total"] or t["done"], t["total"] or t["done"]
            t["status"] = "done"
        except _TaskCancelled:
            t["status"] = "cancelled"
            self.emit("pan_log", "已取消%s任务：%s" % (t["kind"], t["name"]))
        except WebDavError as e:
            t["status"] = "error"
            t["err"] = str(e)
            self.emit("pan_log", "%s失败：%s" % (t["kind"], e))
        except Exception as e:
            t["status"] = "error"
            t["err"] = str(e)
            self.emit("pan_log", "%s异常：%s" % (t["kind"], e))
        finally:
            self._pan_task_cleanup(task_id)

    def _pan_task_cleanup(self, task_id):
        self._pan_emit_task(task_id)
        t = self._pan_tasks.get(task_id)
        if t and t["status"] in ("done", "error", "cancelled"):
            # 保留完成的任务片刻，便于前端展示，之后清理
            def _drop():
                with self._pan_lock:
                    self._pan_tasks.pop(task_id, None)

            threading.Timer(5.0, _drop).start()

    # -- 服务控制 -----------------------------------------------------------
    def pan_get_state(self):
        srv = self._pan
        # 自愈：cfg 或默认目录已有程序时即时识别（下载/手动放置后无需重启应用）
        if not (srv.bin_path and os.path.isfile(srv.bin_path)):
            try:
                srv.detect_bin(self._pan_cfg().get("openlist_bin", ""))
            except ValueError:
                pass
        st = self._pan.state()
        st["bin_cfg"] = self._pan_cfg().get("openlist_bin", "")
        st["bin_builtin"] = os.path.isfile(openlist_mod.runtime_bin_candidate())
        st["mapped"] = self.pan_mapped().get("data", [])
        st["tasks"] = [
            {k: t[k] for k in ("id", "kind", "name", "done", "total", "speed", "status", "err")}
            for t in self._pan_tasks.values()
        ]
        return {"ok": True, "data": st}

    def pan_start(self, host, port, fw=True):
        srv = self._pan
        try:
            port = int(port)
            self._pan.detect_bin(self._pan_cfg().get("openlist_bin", ""))
            srv.start(str(host or "127.0.0.1"), port, bool(fw))
        except Exception as e:
            return {"ok": False, "err": str(e)}
        self.emit("pan_log", "OpenList 服务已启动。")
        return {"ok": True, "data": srv.state()}

    def pan_stop(self):
        try:
            self._pan.stop()
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def pan_set_bin(self, path):
        path = str(path or "").strip()
        if not path or not os.path.isfile(path) or not path.lower().endswith(".exe"):
            return {"ok": False, "err": "请选择有效的 openlist.exe 路径"}
        self._pan_cfg().set("openlist_bin", path)
        self.emit("pan_log", "已设置 OpenList 程序：%s" % path)
        return {"ok": True, "data": path}

    def pan_pick_bin(self):
        try:
            return {"ok": True, "data": _pick_file(self)}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def pan_download_bin(self):
        try:
            dest = os.path.join(DATA_HOME, "openlist", "bin")

            def cb(done, total):
                self.emit(
                    "pan_task",
                    {"id": "dl_bin", "kind": "下载", "name": "OpenList", "done": done,
                     "total": total, "speed": None, "status": "running", "err": ""},
                )

            r = bindl.download_binary("openlist", dest_dir=dest, progress_cb=cb)
            self.emit("pan_task", {"id": "dl_bin", "kind": "下载", "name": "OpenList",
                                   "done": 0, "total": 0, "speed": None,
                                   "status": "done", "err": ""})
            self._pan.bin_path = r["exe"]
            if not self._pan_cfg().get("openlist_bin"):
                self._pan_cfg().set("openlist_bin", r["exe"])
            msg = "OpenList %s 已%s" % (r["version"], "存在（复用缓存）" if r["cached"] else "下载完成")
            self.emit("pan_log", msg)
            return {"ok": True, "data": r}
        except bindl.BindlError as e:
            return {"ok": False, "err": str(e)}
        except Exception as e:
            return {"ok": False, "err": "下载失败：%s" % e}

    def pan_import_config(self, path):
        try:
            target = self._pan.import_config(str(path))
            return {"ok": True, "data": target}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def pan_pick_import(self):
        try:
            return {"ok": True, "data": _pick_file(self)}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def pan_open_web(self):
        """打开 OpenList 管理界面；服务未运行时给出明确提示，避免打开死链。"""
        try:
            if not getattr(self._pan, "running", False):
                return {"ok": False, "err": "OpenList 服务未运行：请先启动服务再打开管理界面。"}
            return {"ok": True, "data": self._pan.open_web()}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- v3.2 服务增强：重启 / 日志 / 实时监控 ---------------------------------
    def pan_restart(self, fw=True):
        """重启 OpenList 服务（沿用当前配置的 host/port/fw）。"""
        srv = self._pan
        cfg = self._pan_cfg()
        try:
            srv.detect_bin(cfg.get("openlist_bin", ""))
            st = srv.restart(
                host=cfg.get("openlist_host", "") or srv.host,
                port=int(cfg.get("openlist_port", 0) or srv.port or 15244),
                fw=None if fw is None else bool(fw),
            )
        except Exception as e:
            return {"ok": False, "err": str(e)}
        self.emit("pan_log", "OpenList 服务已重启。")
        return {"ok": True, "data": st}

    def pan_get_log(self, lines=200):
        """读取 OpenList 最新日志文件末尾 lines 行。"""
        try:
            name, content = self._pan.tail_log(int(lines))
        except Exception as e:
            return {"ok": False, "err": str(e)}
        return {"ok": True, "data": {"file": name, "content": content}}

    # -- v3.2 更新管理 --------------------------------------------------------
    def pan_check_update(self, kinds=None):
        """查询指定组件的最新版本（不下载）：openlist / rclone。"""
        kinds = kinds or ("openlist", "rclone")
        out = {}
        for kind in kinds:
            if kind not in ("openlist", "rclone"):
                continue
            try:
                info = bindl.latest(kind)
                info["cached"] = bindl.cached_version(kind)
                out[kind] = info
            except bindl.BindlError as e:
                out[kind] = {"error": str(e)}
            except Exception as e:
                out[kind] = {"error": "查询失败：%s" % e}
        return {"ok": True, "data": out}

    def pan_refresh_bins(self):
        """刷新组件：立即重新探测 OpenList / rclone（含手动放置位置）。

        版本检查在后台线程并行完成，经 ``pan_bins`` 事件推送（GitHub 不可达
        时单次查询可能几十秒，不能阻塞按钮）。
        """
        out = {}
        self._pan.bin_path = ""
        try:
            self._pan.detect_bin(self._pan_cfg().get("openlist_bin", ""))
            out["openlist"] = {"found": True, "path": self._pan.bin_path,
                               "current": bindl.cached_version("openlist")}
        except ValueError as e:
            out["openlist"] = {"found": False, "path": "", "err": str(e)}
        mgr = self._rclone_manager()
        mgr.bin_path = ""
        try:
            mgr.detect_bin(str(self._pan_cfg().get("rclone_bin", "") or "").strip())
            out["rclone"] = {"found": True, "path": mgr.bin_path,
                             "current": bindl.cached_version("rclone")}
        except Exception as e:
            out["rclone"] = {"found": False, "path": "", "err": str(e)}
        self.emit("pan_log", "组件刷新：OpenList %s | rclone %s" % (
            "已就绪" if out["openlist"]["found"] else "未找到",
            "已就绪" if out["rclone"]["found"] else "未找到"))
        threading.Thread(target=self._pan_check_versions, daemon=True,
                         name="pan-ver").start()
        return {"ok": True, "data": out}

    def _pan_check_versions(self):
        """并行查询 OpenList / rclone 最新版本并推送（网络失败只回报原因）。"""
        import concurrent.futures as _cf

        def one(kind):
            latest, err = None, ""
            try:
                latest = bindl.latest_version(kind)
            except Exception as e:
                err = str(e)
            return kind, {"latest": latest, "check_err": err,
                          "current": bindl.cached_version(kind),
                          "outdated": bool(latest and bindl.cached_version(kind)
                                           and latest != bindl.cached_version(kind))}

        payload = {}
        try:
            with _cf.ThreadPoolExecutor(max_workers=2) as ex:
                for kind, info in ex.map(one, ("openlist", "rclone")):
                    payload[kind] = info
        except Exception:
            pass
        try:
            self.emit("pan_bins", payload)
        except Exception:
            pass

    def pan_download_rclone(self):
        try:
            dest = os.path.join(DATA_HOME, "rclone", "bin")

            def cb(done, total):
                self.emit(
                    "pan_task",
                    {"id": "dl_rclone", "kind": "下载", "name": "rclone", "done": done,
                     "total": total, "speed": None, "status": "running", "err": ""},
                )

            r = bindl.download_binary("rclone", dest_dir=dest, progress_cb=cb)
            self.emit("pan_task", {"id": "dl_rclone", "kind": "下载", "name": "rclone",
                                   "done": 0, "total": 0, "speed": None,
                                   "status": "done", "err": ""})
            if not self._pan_cfg().get("rclone_bin"):
                self._pan_cfg().set("rclone_bin", r["exe"])
            # 立即让管理器识别新程序，状态行无需重启即可就绪
            try:
                self._rclone_manager().detect_bin(r["exe"])
            except ValueError:
                pass
            msg = "rclone %s 已%s" % (r["version"], "存在（复用缓存）" if r["cached"] else "下载完成")
            self.emit("pan_log", msg)
            return {"ok": True, "data": r}
        except bindl.BindlError as e:
            return {"ok": False, "err": str(e)}
        except Exception as e:
            return {"ok": False, "err": "下载失败：%s" % e}

    # -- v3.2 WinFsp 驱动 -----------------------------------------------------
    def pan_winfsp_status(self):
        try:
            installed = winfsp.is_winfsp_installed()
        except Exception as e:
            return {"ok": False, "err": "检测 WinFsp 失败：%s" % e}
        return {"ok": True, "data": {"installed": installed}}

    def pan_winfsp_install(self):
        """下载 WinFsp msi 并安装：管理员静默安装，非管理员触发 UAC 提权。"""
        try:
            if winfsp.is_winfsp_installed():
                return {"ok": True, "data": {"installed": True, "msg": "已安装，无需重复安装"}}
            dest = os.path.join(DATA_HOME, "winfsp")
            r = bindl.download_binary("winfsp", dest_dir=dest)
            res = winfsp.install_winfsp(
                r["exe"], log_callback=lambda m: self.emit("pan_log", str(m))
            )
            res["installed"] = winfsp.is_winfsp_installed()
            return {"ok": True, "data": res}
        except bindl.BindlError as e:
            return {"ok": False, "err": str(e)}
        except ValueError as e:
            return {"ok": False, "err": str(e)}
        except Exception as e:
            return {"ok": False, "err": "安装失败：%s" % e}

    # -- v3.2 自启动 -----------------------------------------------------------
    def pan_set_autostart(self, kind, enabled):
        """配置自启动开关：openlist（服务随应用启动）/ rclone（恢复挂载）/ app（开机自启）。"""
        kind = str(kind or "")
        enabled = bool(enabled)
        if kind == "openlist":
            self._pan_cfg().set("openlist_autostart", enabled)
        elif kind == "rclone":
            self._pan_cfg().set("rclone_autostart", enabled)
        elif kind == "app":
            from ..core.config import set_app_autostart

            try:
                set_app_autostart(enabled)
            except Exception as e:
                return {"ok": False, "err": "写入开机自启失败：%s" % e}
            self._pan_cfg().set("app_autostart", enabled)
        else:
            return {"ok": False, "err": "未知的自启动类型：%s" % kind}
        self.emit("pan_log", "已设置自启动 %s=%s" % (kind, "开" if enabled else "关"))
        return {"ok": True, "data": enabled}

    # -- v3.2 Rclone 本地挂载 ---------------------------------------------------
    def _rclone_manager(self):
        if not hasattr(self, "_rclone"):
            from ..core.rclone_mount import RcloneMountManager

            self._rclone = RcloneMountManager(
                openlist=self._pan,
                log_callback=lambda m: self.emit("pan_log", str(m)),
            )
            try:
                self._rclone.detect_bin(self._pan_cfg().get("rclone_bin", ""))
            except ValueError:
                pass  # rclone 未就绪：等用户下载/选择
        return self._rclone

    def pan_rclone_state(self):
        cfg = self._pan_cfg()
        mgr = self._rclone_manager()
        # 自愈：下载/手动放置后无需重启即可识别（含默认目录与 runtime 探测）
        cfg_bin = str(cfg.get("rclone_bin", "") or "").strip()
        if not (mgr.bin_path and os.path.isfile(mgr.bin_path)):
            try:
                mgr.detect_bin(cfg_bin)
            except ValueError:
                pass
        return {
            "ok": True,
            "data": {
                "bin_cfg": cfg_bin,
                "bin_path": mgr.bin_path or "",
                "bin_ready": bool(mgr.bin_path and os.path.isfile(mgr.bin_path)),
                "mounts": mgr.list_mounted() if mgr.bin_path else [],
                "mount_type": cfg.get("rclone_mount_type", "letter"),
                "mount_target": cfg.get("rclone_mount_target", "V"),
                "user": cfg.get("rclone_user", ""),
                "winfsp": winfsp.is_winfsp_installed(),
                "openlist_running": bool(self._pan.running),
            },
        }

    def pan_rclone_set_cfg(self, mount_type="", mount_target="", user="", pwd=""):
        """持久化挂载参数（挂载目标/账号密码），下次挂载与自动恢复使用。"""
        cfg = self._pan_cfg()
        if str(mount_type or "") in ("letter", "folder"):
            cfg.set("rclone_mount_type", str(mount_type))
        if str(mount_target or "").strip():
            cfg.set("rclone_mount_target", str(mount_target).strip())
        if str(user or "").strip():
            cfg.set("rclone_user", str(user).strip())
        cfg.set("rclone_pwd", str(pwd or ""))
        self.emit("pan_log", "已保存 Rclone 挂载参数。")
        return {"ok": True, "data": cfg.get("rclone_mount_target", "")}

    def pan_rclone_mount(self, mount_type="", mount_target="", winfsp_ok=None):
        """按（当前/传入）参数执行 rclone 挂载。失败返回中文原因。"""
        cfg = self._pan_cfg()
        mtype = str(mount_type or cfg.get("rclone_mount_type", "letter")).lower()
        target = str(mount_target or cfg.get("rclone_mount_target", "")).strip()
        if mtype not in ("letter", "folder"):
            return {"ok": False, "err": "目标类型无效（可选 letter / folder）"}
        if mtype == "letter" and not re.fullmatch(r"[A-Z]", target):
            return {"ok": False, "err": "请填写单个盘符字母（A~Z）"}
        if mtype == "folder" and not target:
            return {"ok": False, "err": "请填写挂载文件夹绝对路径"}
        try:
            r = self._rclone_manager().mount(
                target_type=mtype,
                letter=target if mtype == "letter" else "",
                folder=target if mtype == "folder" else "",
                user=cfg.get("rclone_user", ""),
                pwd=cfg.get("rclone_pwd", ""),
                winfsp_ok=winfsp_ok,
            )
        except ValueError as e:
            return {"ok": False, "err": str(e)}
        except Exception as e:
            return {"ok": False, "err": "挂载失败：%s" % e}
        return {"ok": True, "data": r}

    def pan_rclone_umount(self, target):
        target = str(target or "").strip()
        if not target:
            return {"ok": False, "err": "参数缺失：挂载目标"}
        try:
            ok = self._rclone_manager().umount(target)
        except Exception as e:
            return {"ok": False, "err": "卸载失败：%s" % e}
        return {"ok": True, "data": ok}

    # -- 文件操作 -----------------------------------------------------------
    def pan_browse(self, path="/"):
        try:
            info = self._pan_dav(6).listdir(str(path or "/"))
            return {"ok": True, "data": info}
        except WebDavError as e:
            return {"ok": False, "err": str(e)}

    def pan_mkdir(self, path):
        try:
            self._pan_dav().mkdir(str(path))
            return {"ok": True, "data": True}
        except WebDavError as e:
            return {"ok": False, "err": str(e)}

    def pan_delete(self, path):
        try:
            self._pan_dav().delete(str(path))
            return {"ok": True, "data": True}
        except WebDavError as e:
            return {"ok": False, "err": str(e)}

    def pan_rename(self, src, dst):
        try:
            self._pan_dav().rename(str(src), str(dst))
            return {"ok": True, "data": True}
        except WebDavError as e:
            return {"ok": False, "err": str(e)}

    # -- 上传 / 下载任务 -------------------------------------------------------
    def pan_upload(self, local_path, remote_dir="/"):
        if not os.path.isfile(str(local_path)):
            return {"ok": False, "err": "本地文件不存在"}
        name = os.path.basename(str(local_path))
        tid, t = self._pan_new_task("上传", name)
        dav = self._pan_dav(30)

        def run(progress):
            dav.upload(local_path, remote_dir=remote_dir, progress_cb=progress)

        self._pan_pool.submit(self._pan_worker_wrap, tid, run)
        return {"ok": True, "data": tid}

    def pan_download(self, remote_path, save_dir=""):
        save_dir = str(save_dir or "").strip() or self._pan_cfg().get("save_dir", "") or os.path.expanduser("~")
        name = str(remote_path).rstrip("/").replace("\\", "/").rsplit("/", 1)[-1]
        if not name:
            return {"ok": False, "err": "请先选择要下载的文件"}
        if not os.path.isdir(save_dir):
            return {"ok": False, "err": "保存目录不存在：%s" % save_dir}
        local = os.path.join(save_dir, name)
        tid, t = self._pan_new_task("下载", name)
        dav = self._pan_dav(30)

        def run(progress):
            dav.download(remote_path, local, progress_cb=progress)

        self._pan_pool.submit(self._pan_worker_wrap, tid, run)
        return {"ok": True, "data": tid}

    def _pan_worker_wrap(self, tid, fn):
        try:
            self._pan_worker(tid, fn)
        finally:
            pass

    def pan_tasks(self):
        return {
            "ok": True,
            "data": [
                {k: t[k] for k in ("id", "kind", "name", "done", "total", "speed", "status", "err")}
                for t in self._pan_tasks.values()
            ],
        }

    def pan_cancel(self, task_id):
        t = self._pan_tasks.get(str(task_id))
        if not t:
            return {"ok": False, "err": "任务不存在"}
        t["cancel"] = True
        return {"ok": True, "data": True}

    def pan_pick_local_file(self):
        try:
            return {"ok": True, "data": _pick_file(self)}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def pan_pick_local_folder(self):
        try:
            return {"ok": True, "data": _pick_folder(self)}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 盘符映射 ----------------------------------------------------------------
    def pan_map_drive(self, letter, user="", pwd="", persistent=False):
        letter = str(letter or "").strip().upper()
        if not re.fullmatch(r"[C-Z]", letter):
            return {"ok": False, "err": "盘符需为 C~Z 的单个字母"}
        srv = self._pan
        if not srv.running:
            return {"ok": False, "err": "OpenList 未运行，无法映射"}
        # 未显式传入账号时回退到已保存的 WebDAV 凭据（与 Rclone 挂载共用）
        user = str(user or "").strip()
        pwd = str(pwd or "")
        if not user:
            cfg = self._pan_cfg()
            user = str(cfg.get("rclone_user", "") or "").strip()
            pwd = str(cfg.get("rclone_pwd", "") or "")
        url = "http://127.0.0.1:%d/dav" % srv.port
        cmd = ["net", "use", "%s:" % letter, url]
        if user:
            cmd.append("/user:" + user)
            if pwd:
                cmd.append(str(pwd))
        if persistent:
            cmd.append("/persistent:yes")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               errors="replace", creationflags=_NOWIN)
        except OSError as e:
            return {"ok": False, "err": "执行 net use 失败：%s" % e}
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0:
            msg = out.splitlines()[-1].strip() if out else "未知错误"
            return {"ok": False, "err": "映射失败：%s" % msg[:200]}
        self.emit("pan_log", "已映射 %s: → %s" % (letter, url))
        return {"ok": True, "data": out.splitlines()[-1].strip() if out else "ok"}

    def pan_unmap_drive(self, letter):
        letter = str(letter or "").strip().upper()
        if not re.fullmatch(r"[A-Z]", letter):
            return {"ok": False, "err": "盘符无效"}
        try:
            r = subprocess.run(
                ["net", "use", "%s:" % letter, "/delete", "/yes"],
                capture_output=True, text=True, errors="replace",
                creationflags=_NOWIN,
            )
        except OSError as e:
            return {"ok": False, "err": "执行 net use 失败：%s" % e}
        if r.returncode != 0:
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            msg = out.splitlines()[-1].strip() if out else ""
            if not msg or "not" in msg.lower() or "不存在" in msg or "没有" in msg:
                return {"ok": True, "data": "尚未映射"}
            return {"ok": False, "err": "断开失败：%s" % msg[:200]}
        return {"ok": True, "data": True}

    def pan_mapped(self):
        """解析 net use 输出的已映射盘符（尽力而为，跨本地化尽力匹配）。"""
        try:
            r = subprocess.run(["net", "use"], capture_output=True, text=True,
                               errors="replace", creationflags=_NOWIN)
        except OSError:
            return {"ok": True, "data": []}
        out = (r.stdout or "") + (r.stderr or "")
        mapped = []
        for line in out.splitlines():
            m = re.match(r"^[ \t]*([A-Z]):[ \t]+(\S+)", line)
            if m and ("http" in m.group(2).lower() or m.group(2).startswith("\\\\")):
                mapped.append({"letter": m.group(1), "target": m.group(2)})
        return {"ok": True, "data": mapped}

    # -- 关闭清理 ------------------------------------------------------------
    def _stop_pan(self):
        try:
            self._pan_metrics_stop.set()
            for tid in list(self._pan_tasks.keys()):
                t = self._pan_tasks.get(tid)
                if t:
                    t["cancel"] = True
            self._pan_pool.shutdown(wait=False)
            if hasattr(self, "_rclone"):
                self._rclone.stop()
            self._pan.stop()
        except Exception:
            pass