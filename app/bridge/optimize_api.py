"""系统优化桥接（v5.4）：Tweaks 应用/还原、更新策略、垃圾清理、系统修复。

批量操作异步执行（进度经 opt_progress 事件推送）；单项目标拆分见 core/optimizer.py。
"""

import threading

from .base import BridgeBase
from ..core import appscan, optimizer


class OptimizeApi(BridgeBase):
    def _init_optimize(self):
        self._opt_running = False
        self._opt_lock = threading.Lock()

    # -- 内部 --------------------------------------------------------------
    def _opt_busy_start(self):
        with self._opt_lock:
            if self._opt_running:
                return False
            self._opt_running = True
            return True

    def _opt_busy_end(self):
        with self._opt_lock:
            self._opt_running = False

    def _opt_progress_cb(self, done, total, name):
        # v5.4 修复：原名 _progress_cb 与 FtpApi._progress_cb(self, op) 在
        # Bridge 多继承 MRO 中冲突（FtpApi 先于 OptimizeApi 胜出），导致
        # opt_uwp_remove / winget_install / clean_run / apps_scan 全部在
        # worker 内 TypeError。改为模块专属前缀名。
        self.emit("opt_progress", {"stage": "run", "done": done,
                                   "total": total, "name": name})

    # -- Tweaks 清单与状态 --------------------------------------------------
    def opt_tweaks(self):
        try:
            st = optimizer.statuses()
            items = [{
                "id": t["id"], "name": t["name"], "desc": t["desc"],
                "group": t["group"], "group_name": optimizer.GROUP_NAMES[t["group"]],
                "risk": t["risk"],
                "on": st.get(t["id"], {}).get("on"),
                "admin": st.get(t["id"], {}).get("admin"),
                # v5.4 三期：暴露 registry/services 细节供 autounattend 生成
                "registry": [{"path": r["path"], "name": r["name"],
                              "value": r["value"], "kind": r["kind"]}
                             for r in t.get("registry", [])],
                "services": [{"name": s[0], "startup": s[1]}
                             for s in t.get("services", [])],
            } for t in optimizer.TWEAKS]
            return {"ok": True, "data": {
                "items": items, "groups": optimizer.GROUP_NAMES,
                "admin": optimizer.is_admin(),
                "snapshot_count": len(optimizer._snapshot_load().get("entries", [])),
            }}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def opt_preset(self, kind):
        try:
            ids = optimizer.PRESETS.get(str(kind or ""))
            if ids is None:
                return {"ok": False, "err": "预设须为 lite/recommended/deep"}
            return {"ok": True, "data": {"ids": ids}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 应用 / 还原 --------------------------------------------------------
    def opt_apply(self, ids):
        ids = [str(i) for i in (ids or [])]
        if not ids:
            return {"ok": False, "err": "请先勾选优化项"}
        if not self._opt_busy_start():
            return {"ok": False, "err": "优化操作正在进行中，请稍候"}
        high = [T["name"] for T in optimizer.TWEAK_MAP.values()
                if T["id"] in ids and T["risk"] == "high"]

        def worker():
            try:
                def cb(done, total, name):
                    self._opt_progress_cb(done, total, name)
                ok, results, err = optimizer.apply_tweaks(ids, cb)
                if not ok:
                    self.emit("opt_done", {"ok": False, "err": err})
                    return
                fails = [r for r in results if not r["ok"]]
                self.emit_log("系统优化：应用 %d 项，成功 %d，失败 %d"
                              % (len(results), len(results) - len(fails),
                                 len(fails)))
                self.emit("opt_done", {"ok": not fails, "results": results,
                                       "err": err})
            except Exception as e:
                self.emit("opt_done", {"ok": False, "err": str(e)})
            finally:
                self._opt_busy_end()

        if high:
            return {"ok": False, "err": "包含高风险项：" + "、".join(high[:3])
                    + "（请在勾选时单独确认）"}
        threading.Thread(target=worker, daemon=True,
                         name="opt-apply").start()
        return {"ok": True, "data": {"async": True, "count": len(ids)}}

    def opt_revert(self, ids=None):
        if not self._opt_busy_start():
            return {"ok": False, "err": "优化操作正在进行中，请稍候"}
        ids = [str(i) for i in (ids or [])] or None

        def worker():
            try:
                def cb(done, total, name):
                    self._opt_progress_cb(done, total, name)
                ok, results, err = optimizer.revert_tweaks(ids, cb)
                self.emit("opt_done", {"ok": ok and not any(
                    r.get("err") for r in results), "results": results,
                    "err": err})
            except Exception as e:
                self.emit("opt_done", {"ok": False, "err": str(e)})
            finally:
                self._opt_busy_end()

        threading.Thread(target=worker, daemon=True,
                         name="opt-revert").start()
        return {"ok": True, "data": {"async": True}}

    def opt_revert_one(self, tid):
        """单项目标还原（同步，单项通常很快）。"""
        try:
            ok, results, err = optimizer.revert_tweaks([str(tid)])
            if not ok:
                return {"ok": False, "err": err}
            r = results[0] if results else {"ok": False, "err": "无可还原记录"}
            return {"ok": r["ok"], "data": r} if r["ok"] \
                else {"ok": False, "err": r.get("err") or "还原失败"}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 配置导入导出 --------------------------------------------------------
    def opt_export_cfg(self, ids):
        try:
            data = optimizer.export_cfg(ids or [])
            path = optimizer.SNAPSHOT_PATH.replace(
                "tweak_snapshot.json", "optimize_config.json")
            import json
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            return {"ok": True, "data": {"path": path, "count": len(data["ids"])}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def opt_import_pick(self):
        """弹出文件选择框导入优化配置。"""
        import webview
        result = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=("优化配置 (*.json)", "所有文件 (*.*)"))
        if not result:
            return {"ok": False, "err": "未选择文件。"}
        return self.opt_import_cfg(result[0])

    def opt_import_cfg(self, path):
        try:
            import json
            with open(str(path or ""), "r", encoding="utf-8") as f:
                data = json.load(f)
            ids = optimizer.import_cfg(data)
            return {"ok": True, "data": {"ids": ids}}
        except FileNotFoundError:
            return {"ok": False, "err": "未选择文件。"}
        except (ValueError, OSError) as e:
            return {"ok": False, "err": "导入失败：%s" % e}

    # -- 更新策略 ------------------------------------------------------------
    def opt_update_get(self):
        try:
            return {"ok": True, "data": {"mode": optimizer.update_policy_get()}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def opt_update_set(self, mode):
        try:
            optimizer.update_policy_set(str(mode or ""))
            return {"ok": True, "data": {"mode": optimizer.update_policy_get()}}
        except (ValueError, OSError) as e:
            return {"ok": False, "err": str(e)}

    # -- 垃圾清理 ------------------------------------------------------------
    def opt_clean_scan(self):
        try:
            return {"ok": True, "data": {"items": optimizer.clean_scan()}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def opt_clean_run(self, ids):
        ids = [str(i) for i in (ids or [])]
        if not ids:
            return {"ok": False, "err": "请先勾选清理项"}
        # v5.4 修复：回收站此前被无条件拒绝，而前端确认弹窗后会把 recycle
        # 一并提交（clean_run 本身支持 Clear-RecycleBin）→ 必现"报异常"。
        # 二次确认已由前端承担，这里不再拦截。
        if not self._opt_busy_start():
            return {"ok": False, "err": "优化操作正在进行中，请稍候"}

        def worker():
            try:
                def cb(done, total, name):
                    self._opt_progress_cb(done, total, name)
                ok, err = optimizer.clean_run(ids, cb)
                self.emit("opt_done", {"ok": ok, "err": err})
            except Exception as e:
                self.emit("opt_done", {"ok": False, "err": str(e)})
            finally:
                self._opt_busy_end()

        threading.Thread(target=worker, daemon=True,
                         name="opt-clean").start()
        return {"ok": True, "data": {"async": True}}

    # -- 系统修复 ------------------------------------------------------------
    def opt_fix_run(self, kind):
        try:
            ok, out = optimizer.fix_run(str(kind or ""))
            return {"ok": ok, "data": {"output": out}}
        except (ValueError, OSError) as e:
            return {"ok": False, "err": str(e)}

    # -- 应用管理（v5.4 二期：UWP + 可选功能 + 旧版能力） -------------------
    def opt_uwp_list(self):
        try:
            return {"ok": True, "data": {
                "installed": optimizer.uwp_list(),
                "removed": optimizer.uwp_removed_list(),
                "admin": optimizer.is_admin(),
            }}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def opt_uwp_remove(self, names):
        if not self._opt_busy_start():
            return {"ok": False, "err": "优化操作正在进行中，请稍候"}

        def worker():
            try:
                def cb(done, total, name):
                    self._opt_progress_cb(done, total, name)
                results = optimizer.uwp_remove(names, cb)
                self.emit("opt_done", {"ok": all(x["ok"] for x in results),
                                       "results": results})
            except (ValueError, OSError) as e:
                self.emit("opt_done", {"ok": False, "err": str(e)})
            except Exception as e:
                self.emit("opt_done", {"ok": False, "err": str(e)})
            finally:
                self._opt_busy_end()

        threading.Thread(target=worker, daemon=True,
                         name="opt-uwp").start()
        return {"ok": True, "data": {"async": True}}

    def opt_uwp_restore(self, name):
        try:
            optimizer.uwp_restore(name)
            return {"ok": True, "data": {"name": name}}
        except (ValueError, OSError) as e:
            return {"ok": False, "err": str(e)}

    def opt_features_list(self):
        try:
            return {"ok": True, "data": {"features": optimizer.opt_features_list()}}
        except (OSError, ValueError) as e:
            return {"ok": False, "err": str(e)}

    def opt_feature_set(self, name, enable):
        try:
            optimizer.opt_feature_set(str(name or ""), bool(enable))
            return {"ok": True}
        except (OSError, ValueError) as e:
            return {"ok": False, "err": str(e)}

    def opt_caps_list(self):
        try:
            return {"ok": True, "data": {"caps": optimizer.opt_caps_list()}}
        except (OSError, ValueError) as e:
            return {"ok": False, "err": str(e)}

    def opt_cap_set(self, name, add):
        try:
            optimizer.opt_cap_set(str(name or ""), bool(add))
            return {"ok": True}
        except (OSError, ValueError) as e:
            return {"ok": False, "err": str(e)}

    # -- winget 软件安装 ------------------------------------------------------
    def opt_winget_catalog(self):
        try:
            return {"ok": True, "data": {
                "catalog": optimizer.WINGET_APPS,
                "available": optimizer.winget_available(),
            }}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def opt_winget_install(self, app_ids):
        if not self._opt_busy_start():
            return {"ok": False, "err": "优化操作正在进行中，请稍候"}

        def worker():
            try:
                def cb(done, total, name):
                    self._opt_progress_cb(done, total, name)
                results = optimizer.winget_install(app_ids, cb)
                self.emit("opt_done", {"ok": all(x["ok"] for x in results),
                                       "results": results})
            except (ValueError, OSError) as e:
                self.emit("opt_done", {"ok": False, "err": str(e)})
            except Exception as e:
                self.emit("opt_done", {"ok": False, "err": str(e)})
            finally:
                self._opt_busy_end()

        threading.Thread(target=worker, daemon=True,
                         name="opt-winget").start()
        return {"ok": True, "data": {"async": True}}

    # -- 全量已装应用中心（v5.4 架构级复刻一期，appscan） --------------------
    def opt_apps_scan(self):
        """多源扫描本机已装应用（注册表 + UWP），结果入 appscan 缓存。"""
        if not self._opt_busy_start():
            return {"ok": False, "err": "优化操作正在进行中，请稍候"}

        def worker():
            try:
                def cb(done, total, name):
                    self._opt_progress_cb(done, total, name)
                entries = appscan.scan_apps(cb)
                self.emit("opt_done", {"ok": True, "tag": "apps",
                                       "count": len(entries)})
            except Exception as e:
                self.emit("opt_done", {"ok": False, "tag": "apps",
                                       "err": str(e)})
            finally:
                self._opt_busy_end()

        threading.Thread(target=worker, daemon=True,
                         name="opt-apps-scan").start()
        return {"ok": True, "data": {"async": True}}

    def opt_apps_list(self, src="", kw="", page=1, page_size=100):
        """分页读取扫描缓存（纯内存，不触发重扫；无缓存则提示先扫描）。"""
        entries = appscan.cache_get()
        if entries is None:
            return {"ok": False, "err": "请先点击「扫描」读取本机应用列表"}
        src = str(src or "").strip()
        kw = str(kw or "").strip().lower()
        items = [e for e in entries
                 if (not src or e.source == src)
                 and (not kw or kw in e.name.lower()
                      or kw in (e.publisher or "").lower())]
        try:
            page = max(1, int(page))
            page_size = max(10, min(200, int(page_size)))
        except (TypeError, ValueError):
            page, page_size = 1, 100
        total = len(items)
        start = (page - 1) * page_size
        return {"ok": True, "data": {
            "items": [e.to_dict() for e in items[start:start + page_size]],
            "total": total, "page": page,
            "page_size": page_size,
        }}

    def opt_apps_uninstall(self, keys):
        keys = [str(k) for k in (keys or []) if k]
        if not keys:
            return {"ok": False, "err": "请先勾选要卸载的应用"}
        if not self._opt_busy_start():
            return {"ok": False, "err": "优化操作正在进行中，请稍候"}

        def worker():
            try:
                def cb(done, total, name):
                    self._opt_progress_cb(done, total, name)
                results = appscan.uninstall_apps(keys, cb)
                self.emit("opt_done", {"ok": all(x["ok"] for x in results),
                                       "tag": "apps", "results": results})
            except (ValueError, OSError) as e:
                self.emit("opt_done", {"ok": False, "tag": "apps",
                                       "err": str(e)})
            except Exception as e:
                self.emit("opt_done", {"ok": False, "tag": "apps",
                                       "err": str(e)})
            finally:
                self._opt_busy_end()

        threading.Thread(target=worker, daemon=True,
                         name="opt-apps-un").start()
        return {"ok": True, "data": {"async": True}}

    def opt_apps_removed_list(self):
        """本工具静默卸载过的应用（快照留痕展示）。"""
        try:
            return {"ok": True, "data": appscan.removed_list()}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def opt_apps_updates(self):
        """winget 可升级应用列表（异步；解析 winget upgrade 表格输出）。"""
        if not self._opt_busy_start():
            return {"ok": False, "err": "优化操作正在进行中，请稍候"}

        def worker():
            try:
                rows = optimizer.winget_upgrade_list()
                self.emit("opt_done", {"ok": True, "tag": "apps_updates",
                                       "rows": rows})
            except Exception as e:
                self.emit("opt_done", {"ok": False, "tag": "apps_updates",
                                       "err": str(e)})
            finally:
                self._opt_busy_end()

        threading.Thread(target=worker, daemon=True,
                         name="opt-apps-upd").start()
        return {"ok": True, "data": {"async": True}}

    def opt_apps_upgrade(self, ids):
        """批量 winget 静默升级（逐个执行，进度经 opt_progress 推送）。"""
        ids = [str(i) for i in (ids or []) if i]
        if not ids:
            return {"ok": False, "err": "请先勾选要升级的应用"}
        if not self._opt_busy_start():
            return {"ok": False, "err": "优化操作正在进行中，请稍候"}

        def worker():
            try:
                def cb(done, total, name):
                    self._opt_progress_cb(done, total, name)
                results = optimizer.winget_upgrade(ids, cb)
                self.emit("opt_done", {"ok": all(x["ok"] for x in results),
                                       "tag": "apps_updates",
                                       "results": results})
            except (ValueError, OSError) as e:
                self.emit("opt_done", {"ok": False, "tag": "apps_updates",
                                       "err": str(e)})
            except Exception as e:
                self.emit("opt_done", {"ok": False, "tag": "apps_updates",
                                       "err": str(e)})
            finally:
                self._opt_busy_end()

        threading.Thread(target=worker, daemon=True,
                         name="opt-apps-upgrade").start()
        return {"ok": True, "data": {"async": True}}
