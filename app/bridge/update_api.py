"""自动更新桥接：设置读写 + 手动立即检查 + 生命周期接线。

设置（cfg）：
- update_auto_check：总开关（开启后后台线程每天一次自动检查）
- update_auto_download：检查到新版本时自动下载（不热替换运行中服务）
- update_scope：检查范围 ["openlist", "rclone", "core"]（core=当前代理核心）
- update_interval_hours：检查间隔（小时）

事件：update_found（检查完成）/ update_progress（下载进度）/ update_downloaded。
"""

from ..core import auto_update
from ..core import bindl
from ..core import logger as applog

log = applog.get_logger("update")


class UpdateApi:
    def _init_update(self):
        self._updater = auto_update.AutoUpdateChecker(
            get_cfg=lambda: self.cfg,
            emit=lambda name, data: self.emit(name, data),
            log=self.emit_log,
        )
        self._updater.start()   # 内部读 cfg：未开启则不跑线程

    def _stop_update(self):
        try:
            self._updater.stop()
        except Exception:
            pass

    # -- API -----------------------------------------------------------
    def update_get_settings(self):
        """读取自动更新设置与最近一次检查结果。"""
        try:
            st = self._updater._settings()
            return {"ok": True, "data": {
                "auto_check": st["auto_check"],
                "auto_download": st["auto_download"],
                "scope": st["scope"],
                "interval_hours": st["interval_hours"],
                "last_check": self._updater.last_check,
                "last_results": self._updater.last_results,
            }}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def update_set_settings(self, auto_check=None, auto_download=None,
                            scope=None, interval_hours=None):
        """写设置并热重启检查线程（auto_check 开→start，关→stop）。"""
        try:
            if auto_check is not None:
                self.cfg.set("update_auto_check", bool(auto_check))
            if auto_download is not None:
                self.cfg.set("update_auto_download", bool(auto_download))
            if scope is not None:
                scope = [str(s) for s in scope
                         if str(s) in ("openlist", "rclone", "core")]
                self.cfg.set("update_scope", scope or ["openlist", "rclone", "core"])
            if interval_hours is not None:
                self.cfg.set("update_interval_hours",
                             max(1, min(168, int(interval_hours))))
            if self.cfg.get("update_auto_check"):
                self._updater.restart()
            else:
                self._stop_update()
            return self.update_get_settings()
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def update_check_now(self):
        """手动立即检查（同步；每个组件一次 GitHub 查询，可能数秒）。"""
        try:
            results = self._updater.check_once(auto=False)
            return {"ok": True, "data": results}
        except Exception as e:
            return {"ok": False, "err": "检查更新失败：%s" % e}

    def update_download_one(self, kind):
        """手动下载单个组件新版本（进度经 update_progress 推送）。

        kind: openlist / rclone / xray / sing-box / v2ray；
        下载到默认目录（DATA_HOME/<kind>/bin），同版本幂等复用缓存；
        不热替换运行中的服务（cfg bin 路径为空时回填）。
        """
        kind = str(kind or "")
        if kind not in ("openlist", "rclone", "xray", "sing-box", "v2ray"):
            return {"ok": False, "err": "不支持的组件类型：%s" % kind}
        try:
            def cb(done, total):
                self.emit("update_progress", {
                    "kind": kind, "done": done, "total": total,
                    "status": "running",
                })
            r = bindl.download_binary(kind, progress_cb=cb)
            self.emit("update_progress", {
                "kind": kind, "done": 0, "total": 0, "status": "done",
            })
            # cfg bin 路径为空时回填（运行中服务不中断，重启后生效）
            cfg_key = {"openlist": "openlist_bin", "rclone": "rclone_bin",
                       "xray": "v2ray_bin", "sing-box": "v2ray_bin",
                       "v2ray": "v2ray_bin"}.get(kind)
            if cfg_key and not str(self.cfg.get(cfg_key) or "").strip():
                self.cfg.set(cfg_key, r["exe"])
            return {"ok": True, "data": r}
        except Exception as e:
            self.emit("update_progress", {
                "kind": kind, "done": 0, "total": 0, "status": "error",
                "err": str(e),
            })
            return {"ok": False, "err": str(e)}
