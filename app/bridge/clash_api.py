"""Clash 页桥接（mihomo 内核）：与「V2rayN」（xray/sing-box）完全独立的一套。

js_api 契约：方法名 ``clash_动作``，返回 {ok, data} / {ok, err}；
日志经 ``clash_log`` 推送、状态经 ``clash_state`` 推送、测速/任务经 ``clash_task``。
核心逻辑见 app/core/clash_core.py（独立数据目录 DATA_HOME/clash）。
"""

import json
import os
import threading
import time

from ..core import bindl
from ..core import clash_core as cc
from ..core.config import DATA_HOME


class ClashApi:
    def _init_clash(self):
        self._clash = cc.ClashCoreManager(
            log_callback=lambda m: self.emit("clash_log", str(m)))
        self._clash_lock = threading.Lock()
        self._apply_clash_settings()
        self._clash_log_stop = None
        self._clash_sub_stop = threading.Event()
        threading.Thread(target=self._clash_sub_loop, daemon=True,
                         name="clash-sub").start()

    def _apply_clash_settings(self):
        cfg = self._clash_cfg()
        try:
            self._clash.mixed_port = int(cfg.get("clash_mixed_port", cc.DEFAULT_MIXED_PORT))
        except (TypeError, ValueError):
            self._clash.mixed_port = cc.DEFAULT_MIXED_PORT
        try:
            self._clash.api_port = int(cfg.get("clash_api_port", cc.DEFAULT_API_PORT))
        except (TypeError, ValueError):
            self._clash.api_port = cc.DEFAULT_API_PORT
        mode = str(cfg.get("clash_mode", cc.DEFAULT_MODE))
        self._clash.mode = mode if mode in ("rule", "global", "direct") else cc.DEFAULT_MODE
        self._clash.tun = bool(cfg.get("clash_tun", False))
        level = str(cfg.get("clash_log_level", "warning") or "warning").lower()
        self._clash.log_level = level if level in cc.LOG_LEVELS else "warning"
        try:
            self._clash.bin_path = cc.detect_bin(cfg.get("clash_bin", ""))
        except ValueError:
            self._clash.bin_path = ""

    def _clash_cfg(self):
        if not hasattr(self, "cfg"):
            from ..core.config import AppConfig
            self.cfg = AppConfig()
        return self.cfg

    def _clash_publish(self):
        self.emit("clash_state", self._clash_state())

    def _clash_state(self):
        st = self._clash.state()
        data = self._clash.store.data
        st["nodes"] = self._clash.store.nodes()
        st["groups"] = cc.load_groups()
        st["sub_url"] = data.get("sub_url") or ""
        st["sub_updated_ts"] = float(data.get("sub_updated_ts") or 0)
        st["sub_userinfo"] = data.get("sub_userinfo") or {}
        st["sub_interval_hours"] = float(data.get("sub_interval_hours") or 0)
        st["log_level"] = self._clash.log_level
        st["arch"] = bindl.arch_label()
        info = bindl.cached_info("mihomo")
        st["core_asset"] = info.get("asset") or ""
        st["core_version"] = info.get("version") or ""
        try:
            st["custom_rules"] = len(cc.load_custom_rules())
        except Exception:
            st["custom_rules"] = 0
        try:
            st["sub_hours"] = float(self._clash_cfg().get("clash_sub_autoupdate_hours", 0) or 0)
        except (TypeError, ValueError):
            st["sub_hours"] = 0
        return st

    # -- 状态 -------------------------------------------------------------
    def clash_get_state(self):
        if not (self._clash.bin_path and os.path.isfile(self._clash.bin_path)):
            try:
                self._clash.bin_path = cc.detect_bin(self._clash_cfg().get("clash_bin", ""))
            except ValueError:
                pass
        return {"ok": True, "data": self._clash_state()}

    # -- 启停 / 设置 ------------------------------------------------------
    def clash_start(self, mode=None, tun=None):
        try:
            with self._clash_lock:
                self._clash.start(
                    mixed_port=self._clash.mixed_port,
                    api_port=self._clash.api_port,
                    mode=mode or self._clash.mode,
                    tun=self._clash.tun if tun is None else bool(tun),
                    sys_proxy=True)
        except Exception as e:
            return {"ok": False, "err": str(e)}
        self.emit("clash_log", "Clash 已启动（混合端口 %d，系统代理已设置）"
                  % self._clash.mixed_port)
        self._clash_publish()
        return {"ok": True, "data": self._clash_state()}

    def clash_stop(self):
        try:
            with self._clash_lock:
                was = self._clash.running
                self._clash.stop()
        except Exception as e:
            return {"ok": False, "err": str(e)}
        if was:
            self.emit("clash_log", "Clash 已停止，系统代理已还原。")
        self._clash_publish()
        return {"ok": True, "data": True}

    def clash_set_mode(self, mode):
        mode = str(mode or "")
        if mode not in ("rule", "global", "direct"):
            return {"ok": False, "err": "模式无效（rule / global / direct）"}
        self._clash.mode = mode
        self._clash_cfg().set("clash_mode", mode)
        try:
            if self._clash.running:
                self._clash.api("PATCH", "/configs", {"mode": mode})
        except Exception as e:
            self.emit("clash_log", "模式切换失败（将在下次启动生效）：%s" % e)
        self.emit("clash_log", "Clash 模式：%s" % {"rule": "规则", "global": "全局", "direct": "直连"}[mode])
        self._clash_publish()
        return {"ok": True, "data": mode}

    def clash_set_sysproxy(self, on):
        try:
            self._clash.set_sys_proxy(bool(on))
        except Exception as e:
            return {"ok": False, "err": "设置系统代理失败：%s" % e}
        self.emit("clash_log", "系统代理已%s" % ("开启" if on else "关闭"))
        self._clash_publish()
        return {"ok": True, "data": bool(on)}

    def clash_set_ports(self, mixed_port=None, api_port=None):
        try:
            if mixed_port is not None:
                mixed_port = max(1, min(65535, int(mixed_port)))
                self._clash.mixed_port = mixed_port
                self._clash_cfg().set("clash_mixed_port", mixed_port)
            if api_port is not None:
                api_port = max(1, min(65535, int(api_port)))
                self._clash.api_port = api_port
                self._clash_cfg().set("clash_api_port", api_port)
        except (TypeError, ValueError):
            return {"ok": False, "err": "端口需为数字"}
        self._clash_publish()
        return {"ok": True, "data": {"mixed_port": self._clash.mixed_port,
                                     "api_port": self._clash.api_port}}

    def clash_set_tun(self, on):
        on = bool(on)
        self._clash.tun = on
        self._clash_cfg().set("clash_tun", on)
        self.emit("clash_log", "TUN 已%s（需管理员权限 + wintun.dll；下次启动生效）"
                  % ("开启" if on else "关闭"))
        self._clash_publish()
        return {"ok": True, "data": on}

    # -- 内核 / 规则库下载 -------------------------------------------------
    def clash_download_core(self):
        try:
            r = bindl.download_binary("mihomo", dest_dir=cc.MIHOMO_DIR,
                                      progress_cb=self._clash_dl_cb("mihomo"))
            self._clash.bin_path = r["exe"]
            cfg = self._clash_cfg()
            if not cfg.get("clash_bin"):
                cfg.set("clash_bin", r["exe"])
            self.emit("clash_log", "mihomo %s %s" % (
                r["version"], "已存在（复用缓存）" if r["cached"] else "下载完成"))
            self._clash_publish()
            return {"ok": True, "data": r}
        except bindl.BindlError as e:
            return {"ok": False, "err": str(e)}
        except Exception as e:
            return {"ok": False, "err": "下载失败：%s" % e}

    def clash_download_geo(self):
        res, errs = [], []
        for kind in ("clash-geoip", "clash-geosite", "clash-mmdb"):
            try:
                r = bindl.download_binary(kind, dest_dir=cc.MIHOMO_DIR,
                                          progress_cb=self._clash_dl_cb(kind))
                res.append(r)
                self.emit("clash_log", "规则库 %s %s" % (
                    kind, "已存在（复用缓存）" if r["cached"] else "下载完成"))
            except Exception as e:
                errs.append(str(e)[:120])
        self._clash.ensure_geo()
        self._clash_publish()
        if errs:
            return {"ok": bool(res), "data": res, "err": "；".join(errs)}
        return {"ok": True, "data": res}

    def _clash_dl_cb(self, name):
        def cb(done, total):
            self.emit("clash_task", {"name": name, "done": done, "total": total,
                                     "status": "running"})
        return cb

    def clash_open_dir(self):
        try:
            os.makedirs(self._clash.data_dir, exist_ok=True)
            os.startfile(self._clash.data_dir)
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": "打开数据目录失败：%s" % e}

    def clash_log_tail(self, lines=60):
        return {"ok": True, "data": self._clash.log_tail(lines)}

    # -- 规则 --------------------------------------------------------------
    def clash_rules(self):
        try:
            data = self._clash.rules()
        except Exception as e:
            return {"ok": False, "err": "读取规则失败：%s" % e}
        data["path"] = cc.RULES_FILE
        return {"ok": True, "data": data}

    def clash_save_rules(self, rules=None):
        try:
            saved = cc.save_custom_rules(rules or [])
        except OSError as e:
            return {"ok": False, "err": "保存规则失败：%s" % e}
        self.emit("clash_log", "自定义规则已保存 %d 条（重启 Clash 后生效）" % len(saved))
        if self._clash.running:
            try:
                self._clash.reload()
                self.emit("clash_log", "配置已热重载，规则立即生效")
            except Exception as e:
                self.emit("clash_log", "规则已保存，热重载失败（重启 Clash 生效）：%s" % e)
        self._clash_publish()
        return {"ok": True, "data": saved}

    # -- 日志流 ------------------------------------------------------------
    def clash_log_start(self, level="info"):
        self.clash_log_stop()
        try:
            stop = self._clash.start_log_stream(
                level, self._clash_log_line,
                lambda err: self.emit("clash_log", "日志流已断开：%s" % err))
        except ValueError as e:
            return {"ok": False, "err": str(e)}
        self._clash_log_stop = stop
        self._clash_log_level = str(level or "info")
        return {"ok": True, "data": self._clash_log_level}

    def clash_log_stop(self):
        stop = getattr(self, "_clash_log_stop", None)
        if stop:
            try:
                stop()
            except Exception:
                pass
        self._clash_log_stop = None
        return {"ok": True, "data": True}

    def _clash_log_line(self, kind, payload):
        self.emit("clash_kernel_log", {"level": str(kind or "info").lower(),
                                       "text": str(payload or ""),
                                       "ts": time.time()})

    def clash_set_log_level(self, level="warning"):
        try:
            lv = self._clash.set_log_level(level)
        except ValueError as e:
            return {"ok": False, "err": str(e)}
        self._clash_cfg().set("clash_log_level", lv)
        self.emit("clash_log", "内核日志级别：%s" % lv)
        self._clash_publish()
        return {"ok": True, "data": lv}

    # -- 节点 / 订阅 -------------------------------------------------------
    def clash_import_sub(self, url):
        try:
            from ..core.v2ray_core import import_sub_url
            added, total, _nodes = import_sub_url(str(url), self._clash.store)
        except ValueError as e:
            return {"ok": False, "err": str(e)}
        self._clash.store.data["sub_updated_ts"] = time.time()
        self._clash.store.save()
        self.emit("clash_log", "Clash 订阅导入：新增 %d，共 %d 个节点" % (added, total))
        self._apply_sub_interval()
        self._clash_publish()
        return {"ok": True, "data": {"added": added, "total": total,
                                     "userinfo": self._clash.store.data.get("sub_userinfo") or {}}}

    def _apply_sub_interval(self):
        """订阅提供方建议了更新周期（profile-update-interval）且用户还没设置时采用它。

        Clash 客户端普遍这么处理：机场在订阅头里给出建议刷新周期。
        """
        if not hasattr(self, "_clash"):
            return 0
        try:
            suggest = float(self._clash.store.data.get("sub_interval_hours") or 0)
        except (TypeError, ValueError):
            suggest = 0
        if suggest <= 0:
            return 0
        try:
            cur = float(self._clash_cfg().get("clash_sub_autoupdate_hours", 0) or 0)
        except (TypeError, ValueError):
            cur = 0
        if cur > 0:
            return 0
        try:
            self._clash_cfg().set("clash_sub_autoupdate_hours", suggest)
        except Exception:
            return 0
        self.emit("clash_log", "订阅建议每 %g 小时更新一次，已按此开启自动更新" % suggest)
        return suggest

    def clash_import_text(self, text):
        from ..core.v2ray_core import parse_sub_text
        nodes = parse_sub_text(str(text or ""))
        if not nodes:
            return {"ok": False, "err": "没有解析到节点（支持 vless/vmess/trojan/ss/hy2/tuic/anytls 等分享链接）"}
        added, total = self._clash.store.merge(nodes)
        self._clash_publish()
        return {"ok": True, "data": {"added": added, "total": total}}

    def clash_import_from_v2ray(self):
        """从「V2rayN」的节点库复制一份到 Clash 节点库（独立存储）。"""
        try:
            from ..core.v2ray_core import NODES_FILE, NodeStore
            src = NodeStore(NODES_FILE).nodes()
            if not src:
                return {"ok": False, "err": "「V2rayN」节点库为空"}
            added, total = self._clash.store.merge(
                src, sub_url=NodeStore(NODES_FILE).data.get("sub_url", ""))
            self.emit("clash_log", "已从 V2rayN 复制节点：新增 %d，共 %d 个" % (added, total))
            self._clash_publish()
            return {"ok": True, "data": {"added": added, "total": total}}
        except Exception as e:
            return {"ok": False, "err": "复制节点失败：%s" % e}

    def clash_delete_node(self, index):
        if self._clash.store.delete(int(index)):
            self._clash_publish()
            return {"ok": True, "data": True}
        return {"ok": False, "err": "节点索引无效"}

    def clash_set_sub_autoupdate(self, hours=0):
        try:
            hours = max(0.0, min(168.0, float(hours or 0)))
        except (TypeError, ValueError):
            return {"ok": False, "err": "周期需为数字（小时）"}
        self._clash_cfg().set("clash_sub_autoupdate_hours", hours)
        self.emit("clash_log", "Clash 订阅自动更新：%s" % (
            "已关闭" if hours <= 0 else "每 %g 小时" % hours))
        self._clash_publish()
        return {"ok": True, "data": hours}

    def _clash_sub_loop(self):
        while not self._clash_sub_stop.wait(60):
            try:
                hours = float(self._clash_cfg().get("clash_sub_autoupdate_hours", 0) or 0)
            except (TypeError, ValueError):
                hours = 0
            if hours <= 0:
                continue
            data = self._clash.store.data
            url = str(data.get("sub_url") or "").strip()
            if not url:
                continue
            last = float(data.get("sub_updated_ts") or 0)
            if last and (time.time() - last) < hours * 3600:
                continue
            try:
                from ..core.v2ray_core import import_sub_url
                added, total, _n = import_sub_url(url, self._clash.store)
                self._clash.store.data["sub_updated_ts"] = time.time()
                self._clash.store.save()
                self.emit("clash_log", "Clash 订阅自动更新：新增 %d，共 %d 个节点" % (added, total))
                self._clash_publish()
            except Exception as e:
                self.emit("clash_log", "Clash 订阅自动更新失败：%s" % e)
                self._clash.store.data["sub_updated_ts"] = time.time()
                self._clash.store.save()

    # -- 策略组 / 连接 / 测速 ----------------------------------------------
    def clash_groups(self):
        if not self._clash.running:
            return {"ok": True, "data": {"groups": [], "custom": bool(cc.load_groups()),
                                         "running": False,
                                         "node_count": len(self._clash.store.nodes())}}
        try:
            data = self._clash.proxies()
        except Exception as e:
            return {"ok": False, "err": "读取策略组失败：%s" % e}
        data["running"] = True
        return {"ok": True, "data": data}

    def clash_save_groups(self, groups):
        try:
            saved = cc.save_groups(groups or [])
        except Exception as e:
            return {"ok": False, "err": "保存策略组失败：%s" % e}
        self.emit("clash_log", "Clash 策略组已保存（%d 个）：%s" % (
            len(saved), "、".join(g["name"] for g in saved) or "无"))
        if self._clash.running:
            self.emit("clash_log", "分组改动需重启 Clash 才能生效")
        self._clash_publish()
        return {"ok": True, "data": saved}

    def clash_select(self, group, name):
        try:
            self._clash.select(group, name)
        except Exception as e:
            return {"ok": False, "err": "切换失败：%s" % e}
        self.emit("clash_log", "「%s」已切换为 %s" % (group, name))
        return {"ok": True, "data": {"now": name}}

    def clash_test_node(self, index):
        try:
            lat = self._clash.test_node(int(index))
        except Exception as e:
            return {"ok": False, "err": str(e)}
        self._clash_publish()
        return {"ok": True, "data": {"latency": lat}}

    def clash_test_group(self, group):
        try:
            return {"ok": True, "data": self._clash.test_group(str(group))}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def clash_burst_test(self):
        try:
            results = self._clash.burst_test()
        except Exception as e:
            return {"ok": False, "err": str(e)}
        self.emit("clash_log", "Clash 批量测速完成，共 %d 个节点" % len(results))
        self._clash_publish()
        return {"ok": True, "data": results}

    def clash_connections(self):
        if not self._clash.running:
            return {"ok": True, "data": {"connections": [], "upload_total": 0,
                                         "download_total": 0, "memory": 0}}
        try:
            return {"ok": True, "data": self._clash.connections()}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def clash_close_connection(self, conn_id):
        try:
            self._clash.close_connection(conn_id)
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def clash_close_all_connections(self):
        try:
            self._clash.close_all_connections()
            self.emit("clash_log", "已关闭全部 Clash 连接")
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 清理 -------------------------------------------------------------
    def _stop_clash(self):
        try:
            self._clash_sub_stop.set()
            self.clash_log_stop()
            self._clash.stop()
        except Exception:
            pass
