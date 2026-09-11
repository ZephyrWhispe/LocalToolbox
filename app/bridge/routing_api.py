"""分流规则页桥接：统一规则中心（V2rayN + Clash 共用）。

js_api 契约：方法名 ``routing_动作``，返回 {ok, data} / {ok, err}；
更新进度经 ``routing_update_progress`` 推送、日志经 ``routing_log`` 推送。
核心逻辑见 app/core/routing_core.py（数据目录 DATA_HOME/rules）。
"""

import os
import threading
import time

from ..core import routing_core as rc


class RoutingApi:
    def _init_routing(self):
        self._routing_stop = threading.Event()
        self._routing_lock = threading.Lock()
        self._routing_busy = False
        # 规则库自动更新（后台守护线程；启动延迟 90s，之后每小时检查一次到期）
        threading.Thread(target=self._routing_autoupdate_loop, daemon=True,
                         name="routing-update").start()

    def _stop_routing(self):
        self._routing_stop.set()

    # -- 状态 ---------------------------------------------------------------
    def routing_get_state(self):
        try:
            data = rc.load_unified()
            presets = []
            for p in rc.PRESETS:
                presets.append({
                    "key": p["key"], "name": p["name"], "desc": p["desc"],
                    "policy": p["policy"],
                    "on": bool((data.get("presets") or {}).get(p["key"])),
                    "ready": rc.preset_ready(p),
                })
            adv = self._v2rayn_advanced()
            routing = adv.get("routing") if isinstance(adv, dict) else None
            managed_v2 = ("expert" if isinstance(routing, dict)
                          else ("unified" if rc.has_unified_file() else "default"))
            return {"ok": True, "data": {
                "presets": presets,
                "geo_ready": rc.geo_dat_ready(),
                "missing_srs": rc._missing_srs(data),
                "engines": {
                    "v2rayn": {"running": bool(getattr(self, "_proxy", None) and
                                               self._proxy.running),
                               "core": str(self._cfg().get("v2ray_core") or "xray")},
                    "clash": {"running": bool(getattr(self, "_clash", None) and
                                              self._clash.running)},
                },
                "managed": {"v2rayn": managed_v2, "clash": rc.has_unified_file()},
                "update": {
                    "auto": bool(self._cfg().get("routing_auto_update")),
                    "hours": int(self._cfg().get("routing_update_hours") or 168),
                    "dat_repo": str(self._cfg().get("routing_dat_repo") or "metacubex"),
                    "last_ts": float(data.get("updated_ts") or 0),
                    "busy": self._routing_busy,
                },
                "dir": rc.RULES_DIR,
            }}
        except Exception as e:
            return {"ok": False, "err": "读取分流规则状态失败：%s" % e}

    def routing_get_custom(self):
        try:
            data = rc.load_unified()
            return {"ok": True, "data": {"entries": data.get("custom") or []}}
        except Exception as e:
            return {"ok": False, "err": "读取自定义规则失败：%s" % e}

    # -- 保存与应用（双写） ---------------------------------------------------
    def routing_save_custom(self, entries=None):
        try:
            clean = [rc.normalize_entry(e) for e in (entries or [])]
        except ValueError as e:
            return {"ok": False, "err": str(e)}
        data = rc.load_unified()
        data["custom"] = clean
        return self._routing_apply(data)

    def routing_set_preset(self, key, on):
        if key not in rc.PRESET_KEYS:
            return {"ok": False, "err": "未知的规则集：%s" % key}
        data = rc.load_unified()
        data["presets"][key] = bool(on)
        return self._routing_apply(data)

    def _routing_apply(self, data):
        """统一保存：写 unified.json + 双引擎编译应用。"""
        try:
            r = rc.apply_unified(self._clash, data, emit=self.emit)
        except Exception as e:
            return {"ok": False, "err": "应用分流规则失败：%s" % e}
        # 刷新 V2rayN 页的高级配置缓存与状态推送
        try:
            if getattr(self, "_proxy", None):
                self._proxy.load_advanced()
                self._proxy_publish()
        except Exception:
            pass
        self._clash_publish()
        if r["warnings"]:
            self.emit("routing_log", "；".join(r["warnings"]))
        return {"ok": True, "data": r}

    # -- 预览 ----------------------------------------------------------------
    def routing_preview(self):
        try:
            return {"ok": True, "data": rc.compile_preview()}
        except Exception as e:
            return {"ok": False, "err": "编译预览失败：%s" % e}

    # -- 更新（GitHub 开源规则数据，主源失败自动切换备份源） ---------------------
    def routing_check_update(self):
        with self._routing_lock:
            if self._routing_busy:
                return {"ok": False, "err": "规则库更新正在进行中"}
            self._routing_busy = True
        threading.Thread(target=self._routing_update_job, args=(True,),
                         daemon=True, name="routing-update-now").start()
        return {"ok": True, "data": {"started": True}}

    def _routing_update_job(self, force):
        try:
            self._routing_run_update(force=force)
        finally:
            self._routing_busy = False

    def _routing_run_update(self, force):
        def cb(m):
            self.emit("routing_update_progress", m)
        try:
            r = rc.update_all(progress_cb=cb, force=force,
                              dat_repo=str(self._cfg().get("routing_dat_repo") or "metacubex"))
        except Exception as e:
            self.emit("routing_update_progress", {"name": "", "status": "summary",
                                                  "ok": False, "errs": [str(e)]})
            self.emit("routing_log", "规则库更新失败：%s" % e)
            return
        ok = sum(1 for x in r["results"] if x["ok"])
        self.emit("routing_update_progress", {"name": "", "status": "summary",
                                              "ok": r["ok"], "oks": ok,
                                              "total": len(r["results"]),
                                              "errs": r["errs"]})
        if r["ok"]:
            self.emit("routing_log", "规则库更新完成：%d 个文件全部就绪" % ok)
        else:
            self.emit("routing_log", "规则库更新完成（%d/%d 成功）：失败项 %s"
                      % (ok, len(r["results"]), "；".join(r["errs"][:2])))

    def _routing_autoupdate_loop(self):
        """启动延迟 90s，之后每小时检查：到期（距上次更新 > 间隔）自动更新。"""
        self._routing_stop.wait(90)
        while not self._routing_stop.is_set():
            try:
                if self._cfg().get("routing_auto_update"):
                    hours = float(self._cfg().get("routing_update_hours") or 168)
                    last = float(rc.load_unified().get("updated_ts") or 0)
                    if (not last) or (time.time() - last) >= hours * 3600:
                        with self._routing_lock:
                            busy = self._routing_busy
                            if not busy:
                                self._routing_busy = True
                        if not busy:
                            try:
                                self._routing_run_update(force=False)
                            finally:
                                self._routing_busy = False
            except Exception as e:
                try:
                    self.emit("routing_log", "规则库自动更新异常：%s" % e)
                except Exception:
                    pass
            self._routing_stop.wait(3600)

    def routing_set_options(self, auto=None, hours=None, dat_repo=None):
        try:
            if auto is not None:
                self._cfg().set("routing_auto_update", bool(auto))
            if hours is not None:
                h = max(6, min(720, int(hours)))
                self._cfg().set("routing_update_hours", h)
                hours = h
            if dat_repo is not None:
                repo = str(dat_repo)
                if repo not in ("metacubex", "loyalsoldier"):
                    return {"ok": False, "err": "未知的规则数据源：%s" % repo}
                self._cfg().set("routing_dat_repo", repo)
        except (TypeError, ValueError):
            return {"ok": False, "err": "更新间隔需为数字（小时）"}
        return {"ok": True, "data": {
            "auto": bool(self._cfg().get("routing_auto_update")),
            "hours": int(self._cfg().get("routing_update_hours") or 168),
            "dat_repo": str(self._cfg().get("routing_dat_repo") or "metacubex"),
        }}

    def routing_open_dir(self):
        try:
            os.makedirs(rc.RULES_DIR, exist_ok=True)
            os.startfile(rc.RULES_DIR)
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": "打开规则目录失败：%s" % e}

    # -- 内部 ---------------------------------------------------------------
    def _cfg(self):
        if not hasattr(self, "cfg") or self.cfg is None:
            from ..core.config import AppConfig
            self.cfg = AppConfig()
        return self.cfg

    def _v2rayn_advanced(self):
        try:
            return self._proxy.load_advanced() or {}
        except Exception:
            try:
                import json
                from ..core import v2ray_core as v2c
                with open(v2c.ADVANCED_FILE, "r", encoding="utf-8") as f:
                    d = json.load(f)
                return d if isinstance(d, dict) else {}
            except Exception:
                return {}
