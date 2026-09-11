"""V2rayN 代理集成分页桥接：节点导入 / 核心托管 / 自动重连 / 系统代理。

js_api 契约：方法名 ``proxy_动作``，返回 {ok, data} / {ok, err}；
状态与日志经 ``proxy_state`` / ``proxy_log`` 事件推送，核心程序下载进度经
``proxy_task`` 推送。核心托管逻辑见 app/core/v2ray_core.py。
"""

import json
import os
import threading
import time

from ..core import bindl, traffic, v2ray_core as v2c
from ..core.config import DATA_HOME
from .web_api import _pick_folder


class ProxyApi:
    """JS 每次调用自带线程；此处全部用普通 def（pywebview 6 不支持 async js_api）。"""

    def _init_proxy(self):
        self._proxy = v2c.V2rayCoreManager(
            log_callback=lambda m: self.emit("proxy_log", str(m))
        )
        self._proxy_lock = threading.Lock()
        self._apply_proxy_settings()
        self._traffic_lock = threading.Lock()
        self._last_traffic_emit = 0.0
        self._traffic = traffic.TrafficMonitor(self._on_traffic, interval=1.5)
        # 流量统计随核心健康状态启停的守门线程：无论手动/监控自动停止都会收敛
        self._gate_stop = threading.Event()
        self._gate = threading.Thread(target=self._traffic_gate, daemon=True)
        self._gate.start()
        # 订阅自动更新（Clash Verge 风格）：按配置周期刷新订阅
        self._sub_stop = threading.Event()
        self._sub_thread = threading.Thread(target=self._sub_autoupdate_loop,
                                            daemon=True, name="sub-autoupdate")
        self._sub_thread.start()

    def _traffic_gate(self):
        was = False
        while not self._gate_stop.wait(1.0):
            try:
                run = bool(self._proxy.running and self._proxy.healthy)
            except Exception:
                run = False
            if run and not was:
                self._traffic.start()
            elif not run and was:
                self._traffic.stop()
            was = run

    def _apply_proxy_settings(self):
        """从配置恢复核心/模式/TUN/系统代理策略（策略对齐 v2rayN 系统代理说明）。"""
        cfg = self._proxy_cfg()
        self._proxy.core_type = cfg.get("v2ray_core", v2c.CORE_XRAY)
        self._proxy.mode = cfg.get("v2ray_mode", v2c.MODE_SMART)
        self._proxy.tun = bool(cfg.get("v2ray_tun", False))
        self._proxy.sys_proxy_mode = cfg.get("v2ray_sysproxy", v2c.SYS_PROXY_AUTO)
        try:
            self._proxy.detect_bin(cfg.get("v2ray_bin", ""))
        except ValueError:
            pass  # 核心未就绪：等用户下载/选择
        self._proxy.load_advanced()

    def _on_traffic(self, up, down):
        if not (self._proxy.running and self._proxy.healthy):
            return
        now = time.time()
        with self._traffic_lock:
            if now - self._last_traffic_emit < 1.0:
                return
            self._last_traffic_emit = now
        try:
            self.emit("proxy_traffic", {"up": float(up), "down": float(down)})
        except Exception:
            pass

    # -- 内部辅助 ---------------------------------------------------------
    def _proxy_cfg(self):
        if not hasattr(self, "cfg"):
            from ..core.config import AppConfig

            self.cfg = AppConfig()
        return self.cfg

    def _proxy_publish(self):
        self.emit("proxy_state", self._proxy.state())

    # -- 状态 -------------------------------------------------------------
    def proxy_get_state(self):
        # 自愈：核心程序下载/手动放置后即时识别（无需重启应用）
        if not (self._proxy.bin_path and os.path.isfile(self._proxy.bin_path)):
            try:
                self._proxy.detect_bin(self._proxy_cfg().get("v2ray_bin", ""))
            except ValueError:
                pass
        st = self._proxy.state()
        data = self._proxy.store.data
        st["sub_url"] = data.get("sub_url") or ""
        st["sub_updated_ts"] = float(data.get("sub_updated_ts") or 0)
        try:
            st["sub_autoupdate_hours"] = float(
                self._proxy_cfg().get("sub_autoupdate_hours", 0) or 0)
        except (TypeError, ValueError):
            st["sub_autoupdate_hours"] = 0
        st["bin_cfg"] = self._proxy_cfg().get("v2ray_bin", "")
        st["nodes"] = self._proxy.store.nodes()
        st["bins"] = {
            "xray": os.path.join(PROXY_BIN_DIR(), "xray.exe"),
            "v2ray": os.path.join(PROXY_BIN_DIR(), "v2ray.exe"),
            "sing-box": os.path.join(PROXY_BIN_DIR(), "sing-box.exe"),
        }
        st["geo_files"] = {
            "geoip": os.path.join(PROXY_BIN_DIR(), "geoip.dat"),
            "geosite": os.path.join(PROXY_BIN_DIR(), "geosite.dat"),
            "geoip_db": os.path.join(PROXY_BIN_DIR(), "geoip.db"),
            "geosite_db": os.path.join(PROXY_BIN_DIR(), "geosite.db"),
            "wintun": os.path.join(PROXY_BIN_DIR(), "wintun.dll"),
        }
        return {"ok": True, "data": st}

    def proxy_set_auto_switch(self, on):
        self._proxy.auto_switch = bool(on)
        self._proxy_publish()
        return {"ok": True, "data": self._proxy.auto_switch}

    # -- 核心 / 模式 / TUN / 系统代理策略（对齐 v2rayN 三核心 + 四系统代理策略） --
    def proxy_set_core(self, kind):
        kind = str(kind or "")
        if kind not in v2c.CORES:
            return {"ok": False, "err": "不支持的核心：%s（可选 xray / sing-box / v2ray）" % kind}
        self._proxy.core_type = kind
        self._proxy_cfg().set("v2ray_core", kind)
        try:
            self._proxy.detect_bin(self._proxy_cfg().get("v2ray_bin", ""))
        except ValueError as e:
            return {"ok": False, "err": "核心已记录，但未找到 %s 程序：%s" % (kind, e)}
        if self._proxy.running and self._proxy.current >= 0:
            # 运行中切换核心：按新核心重启当前节点
            try:
                self._proxy.select(self._proxy.current)
            except Exception as e:
                self.emit("proxy_log", "核心切换后重启失败：%s" % e)
        self.emit("proxy_log", "已切换核心：%s" % kind)
        self._proxy_publish()
        return {"ok": True, "data": kind}

    def proxy_set_mode(self, mode):
        mode = str(mode or "")
        if mode not in (v2c.MODE_GLOBAL, v2c.MODE_SMART, v2c.MODE_DIRECT):
            return {"ok": False, "err": "不支持的代理模式：%s" % mode}
        self._proxy.mode = mode
        self._proxy_cfg().set("v2ray_mode", mode)
        if mode == v2c.MODE_DIRECT:
            # 直连 = 停止核心并还原系统代理
            if self._proxy.running:
                try:
                    self._proxy.stop()
                except Exception:
                    pass
            self.emit("proxy_log", "代理模式已切换为「直连」，核心已停止，系统设置已还原。")
        else:
            if self._proxy.running and self._proxy.current >= 0:
                try:
                    self._proxy.select(self._proxy.current)
                    self.emit("proxy_log", "代理模式已切换为「%s」，已按新分流规则重启。" %
                              ("全局" if mode == v2c.MODE_GLOBAL else "智能分流"))
                except Exception:
                    pass
            else:
                self.emit("proxy_log", "代理模式已切换为「%s」" %
                          ("全局" if mode == v2c.MODE_GLOBAL else "智能分流"))
        self._proxy_publish()
        return {"ok": True, "data": mode}

    def proxy_set_tun(self, on):
        on = bool(on)
        if on and self._proxy.core_type != v2c.CORE_SING:
            return {"ok": False, "err": "TUN 模式仅 sing-box 核心支持"}
        self._proxy.tun = on
        self._proxy_cfg().set("v2ray_tun", on)
        self.emit("proxy_log", "TUN 模式已%s（需管理员权限 + wintun.dll；下次启动生效）" %
                  ("启用" if on else "关闭"))
        self._proxy_publish()
        return {"ok": True, "data": on}

    def proxy_set_sysproxy(self, mode):
        mode = str(mode or "")
        try:
            self._proxy.set_sys_proxy_mode(mode)
        except Exception as e:
            return {"ok": False, "err": str(e)}
        self._proxy_cfg().set("v2ray_sysproxy", mode)
        self.emit("proxy_log", "系统代理策略已切换：%s" % mode)
        self._proxy_publish()
        return {"ok": True, "data": mode}

    def proxy_burst_test(self):
        try:
            results = self._proxy.burst_test()
        except Exception as e:
            return {"ok": False, "err": str(e)}
        self.emit("proxy_log", "批量测速完成，共 %d 个节点；结果已按延迟排序。" % len(results))
        self._proxy_publish()
        return {"ok": True, "data": results}

    def proxy_get_advanced(self):
        data = self._proxy.load_advanced() or {}
        return {"ok": True, "data": data}

    def proxy_set_advanced(self, text):
        text = str(text or "").strip()
        if not text:
            try:
                os.remove(v2c.ADVANCED_FILE)
            except OSError:
                pass
            self._proxy._advanced = None
            self.emit("proxy_log", "高级配置已清空（恢复为默认路由/DNS）")
            self._proxy_publish()
            return {"ok": True, "data": {}}
        try:
            data = json.loads(text)
        except ValueError as e:
            return {"ok": False, "err": "JSON 解析失败：%s" % e}
        if not isinstance(data, dict):
            return {"ok": False, "err": "高级配置必须是 JSON 对象"}
        allowed = {}
        for k in ("routing", "dns"):
            if k in data:
                v = data[k]
                # 仅接受 dict（覆盖段）或 list（v2rayN 自定义路由规则数组）
                if isinstance(v, (dict, list)):
                    allowed[k] = v
                else:
                    return {"ok": False, "err": "「%s」段必须是 JSON 对象或数组" % k}
        if not allowed and data:
            return {"ok": False, "err": "仅支持 routing / dns 两个配置段"}
        os.makedirs(v2c.PROXY_DIR, exist_ok=True)
        with open(v2c.ADVANCED_FILE, "w", encoding="utf-8") as f:
            json.dump(allowed, f, ensure_ascii=False, indent=2)
        self._proxy.load_advanced()
        self.emit("proxy_log", "高级配置已保存（下次启动生效）：routing=%s dns=%s" %
                  ("自定义" if "routing" in allowed else "默认",
                   "自定义" if "dns" in allowed else "默认"))
        self._proxy_publish()
        return {"ok": True, "data": self._proxy._advanced or {}}

    # -- 节点导入 -----------------------------------------------------------
    def proxy_import_sub(self, url):
        try:
            added, total, nodes = v2c.import_sub_url(str(url), self._proxy.store)
        except ValueError as e:
            return {"ok": False, "err": str(e)}
        self._proxy.store.data["sub_updated_ts"] = time.time()
        self._proxy.store.save()
        self._proxy_publish()
        msg = "订阅导入成功：新增 %d 个，共 %d 个节点。"
        self.emit("proxy_log", msg % (added, total))
        return {"ok": True, "data": {"added": added, "total": total}}

    def proxy_import_text(self, text):
        nodes = v2c.parse_sub_text(str(text or ""))
        if not nodes:
            return {"ok": False, "err": "未解析到有效节点（支持 vless/vmess/trojan/ss/socks/http 分享链接）"}
        added, total = self._proxy.store.merge(nodes)
        self._proxy_publish()
        self.emit("proxy_log", "剪贴板/文本导入成功：新增 %d 个，共 %d 个节点。" % (added, total))
        return {"ok": True, "data": {"added": added, "total": total}}

    def proxy_import_dir(self, path):
        try:
            added, total, nodes = v2c.import_v2rayn_dir(str(path), self._proxy.store)
        except ValueError as e:
            return {"ok": False, "err": str(e)}
        self._proxy_publish()
        self.emit("proxy_log", "V2rayN 目录导入成功：新增 %d 个，共 %d 个节点。" % (added, total))
        return {"ok": True, "data": {"added": added, "total": total}}

    def proxy_pick_dir(self):
        try:
            return {"ok": True, "data": _pick_folder(self)}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def proxy_import_clipboard(self):
        import ctypes

        try:
            text = _clipboard_text()
        except Exception as e:
            return {"ok": False, "err": "读取剪贴板失败：%s" % e}
        if not text:
            return {"ok": False, "err": "剪贴板为空"}
        return self.proxy_import_text(text)

    # -- 策略组 / 连接监控（Clash Verge 风格，基于 sing-box Clash API） --------
    def proxy_groups(self):
        try:
            return {"ok": True, "data": self._proxy.groups_state()}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def proxy_save_groups(self, groups):
        """保存策略组定义（成员由关键字过滤决定；改动需重启代理生效）。"""
        try:
            saved = v2c.save_groups(groups or [])
        except Exception as e:
            return {"ok": False, "err": "保存策略组失败：%s" % e}
        self.emit("proxy_log", "策略组已保存（%d 个）：%s" % (
            len(saved), "、".join(g["name"] for g in saved) or "无"))
        if self._proxy.running and self._proxy.core_type == v2c.CORE_SING:
            self.emit("proxy_log", "策略组改动需重启代理后生效（可用「重启」按钮）")
        self._proxy_publish()
        return {"ok": True, "data": saved}

    def proxy_group_select(self, name, member):
        """运行时切换策略组选中项（无需重启核心）。"""
        try:
            self._proxy.group_select(str(name), str(member))
        except Exception as e:
            return {"ok": False, "err": "切换失败：%s" % e}
        self.emit("proxy_log", "策略组「%s」已切换为 %s" % (name, member))
        return {"ok": True, "data": {"now": member}}

    def proxy_connections(self):
        try:
            return {"ok": True, "data": self._proxy.connections()}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def proxy_close_connection(self, conn_id):
        try:
            self._proxy.close_connection(str(conn_id))
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def proxy_close_all_connections(self):
        try:
            self._proxy.close_all_connections()
            self.emit("proxy_log", "已关闭全部活动连接")
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 订阅自动更新 ---------------------------------------------------------
    def _sub_autoupdate_loop(self):
        """按配置周期自动刷新订阅（默认关闭）。"""
        while not self._sub_stop.wait(60):
            try:
                hours = float(self._proxy_cfg().get("sub_autoupdate_hours", 0) or 0)
            except (TypeError, ValueError):
                hours = 0
            if hours <= 0:
                continue
            data = self._proxy.store.data
            url = str(data.get("sub_url") or "").strip()
            if not url:
                continue
            last = float(data.get("sub_updated_ts") or 0)
            if last and (time.time() - last) < hours * 3600:
                continue
            try:
                added, total, _nodes = v2c.import_sub_url(url, self._proxy.store)
                self._proxy.store.data["sub_updated_ts"] = time.time()
                self._proxy.store.save()
                self.emit("proxy_log", "订阅自动更新完成：新增 %d 个，共 %d 个节点" % (added, total))
                self._proxy_publish()
            except Exception as e:
                self.emit("proxy_log", "订阅自动更新失败：%s" % e)
                # 失败也记时间，避免每分钟重试刷屏
                self._proxy.store.data["sub_updated_ts"] = time.time()
                self._proxy.store.save()

    def proxy_set_sub_autoupdate(self, hours=0):
        """设置订阅自动更新周期（小时；0 = 关闭）。"""
        try:
            hours = max(0.0, min(168.0, float(hours or 0)))
        except (TypeError, ValueError):
            return {"ok": False, "err": "周期需为数字（小时）"}
        self._proxy_cfg().set("sub_autoupdate_hours", hours)
        self.emit("proxy_log", "订阅自动更新：%s" % (
            "已关闭" if hours <= 0 else "每 %g 小时检查一次" % hours))
        self._proxy_publish()
        return {"ok": True, "data": hours}

    def proxy_delete(self, index):
        self._proxy.store.delete(int(index))
        self._proxy_publish()
        self.emit("proxy_log", "已删除节点 #%d。" % int(index))
        return {"ok": True, "data": True}

    def proxy_select(self, index):
        try:
            r = self._proxy.select(int(index))
        except ValueError as e:
            return {"ok": False, "err": str(e)}
        self._proxy_publish()
        if not r.get("ok") and r.get("note") != "not-running":
            return {"ok": False, "err": r.get("err", "切换失败")}
        return {"ok": True, "data": r.get("data", True)}

    def proxy_test(self, index):
        try:
            r = self._proxy.test_node(int(index))
        except Exception as e:
            return {"ok": False, "err": str(e)}
        self._proxy_publish()
        return r

    # -- 核心程序定位 / 下载 -------------------------------------------------
    def proxy_set_bin(self, path):
        path = str(path or "").strip()
        if not path or not os.path.isfile(path) or not path.lower().endswith(".exe"):
            return {"ok": False, "err": "请选择有效的 xray.exe / v2ray.exe 路径"}
        self._proxy_cfg().set("v2ray_bin", path)
        self._proxy.bin_path = os.path.abspath(path)
        self.emit("proxy_log", "已设置核心程序：%s" % path)
        self._proxy_publish()
        return {"ok": True, "data": path}

    def proxy_pick_bin(self):
        try:
            path = _pick_bin_file(self)
            if path:
                return self.proxy_set_bin(path)
            return {"ok": True, "data": None}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def proxy_set_bin_from_dir(self, dir_path):
        """从 V2rayN 安装目录自动识别核心（按当前核心类型优先）。"""
        dir_path = str(dir_path or "").strip()
        if not os.path.isdir(dir_path):
            return {"ok": False, "err": "目录不存在：%s" % dir_path}
        pref = [self._proxy.core_type] + [c for c in v2c.CORES if c != self._proxy.core_type]
        for kind in pref:
            name = "sing-box.exe" if kind == v2c.CORE_SING else kind + ".exe"
            p = os.path.join(dir_path, name)
            if os.path.isfile(p):
                return self.proxy_set_bin(p)
        return {"ok": False, "err": "目录中未找到 %s" % " / ".join(
            "sing-box.exe" if k == v2c.CORE_SING else k + ".exe" for k in v2c.CORES)}

    def proxy_download_bin(self, kind="xray"):
        kind = str(kind or "xray")
        if kind not in v2c.CORES:
            return {"ok": False, "err": "核心类型无效（可选 xray / sing-box / v2ray）"}
        try:
            dest = os.path.join(DATA_HOME, kind, "bin")

            def cb(done, total):
                self.emit(
                    "proxy_task",
                    {"id": "dl_core", "kind": "下载", "name": kind, "done": done,
                     "total": total, "status": "running", "err": ""},
                )

            r = bindl.download_binary(kind, dest_dir=dest, progress_cb=cb)
            self.emit("proxy_task", {"id": "dl_core", "kind": "下载", "name": kind,
                                     "done": 0, "total": 0, "status": "done", "err": ""})
            self._proxy.bin_path = r["exe"]
            if not self._proxy_cfg().get("v2ray_bin"):
                self._proxy_cfg().set("v2ray_bin", r["exe"])
            msg = "%s %s 已%s" % (kind, r["version"], "存在（复用缓存）" if r["cached"] else "下载完成")
            self.emit("proxy_log", msg)
            self._proxy_publish()
            return {"ok": True, "data": r}
        except bindl.BindlError as e:
            return {"ok": False, "err": str(e)}
        except Exception as e:
            return {"ok": False, "err": "下载失败：%s" % e}

    def proxy_download_geo(self, core=None):
        """下载当前核心所需的 GeoIP / GeoSite 规则库。

        sing-box 用 .srs 规则集（MetaCubeX/meta-rules-dat，1.12+ 唯一受支持的
        本地格式；旧 geoip.db/geosite.db 会让核心 FATAL）；
        xray / v2ray 用 Loyalsoldier/v2ray-rules-dat 的 geoip.dat/geosite.dat。
        已存在且在缓存期内的文件跳过（bindl 内部处理）。
        """
        core = str(core or "").strip() or (self._proxy.core_type or v2c.CORE_XRAY)
        if core not in v2c.CORES:
            return {"ok": False, "err": "核心类型无效（可选 xray / sing-box / v2ray）"}
        dest = PROXY_BIN_DIR()
        res, errs = [], []

        def _one(kind):
            def cb(done, total, _k=kind):
                self.emit("proxy_task",
                          {"id": "dl_geo", "kind": "下载", "name": _k,
                           "done": done, "total": total, "status": "running", "err": ""})
            fn = bindl.download_ruleset if core == v2c.CORE_SING else bindl.download_binary
            r = fn(kind, dest_dir=dest, progress_cb=cb)
            res.append(r)
            self.emit("proxy_log", "规则库 %s %s 已%s" % (
                kind, r["version"], "存在（复用缓存）" if r["cached"] else "下载完成"))

        if core == v2c.CORE_SING:
            kinds = list(v2c.SING_RULE_SETS_KINDS)
        else:
            kinds = ("geoip", "geosite")
        for kind in kinds:
            try:
                _one(kind)
            except bindl.BindlError as e:
                errs.append(str(e))
                self.emit("proxy_log", "规则库 %s 下载失败：%s" % (kind, e))
        self.emit("proxy_task", {"id": "dl_geo", "kind": "下载", "name": "",
                                 "done": 0, "total": 0, "status": "done", "err": ""})
        self._proxy_publish()
        if errs:
            return {"ok": len(res) > 0, "data": res, "err": "；".join(errs)}
        return {"ok": True, "data": res}

    def proxy_refresh_bins(self):
        """刷新组件：立即重新探测核心（含手动放置位置）并回报本地状态。

        版本检查（访问 GitHub）在后台线程完成，经 ``proxy_bins`` 事件推送——
        在 GitHub 不可达的网络里单次查询可能耗时至几十秒，不应阻塞按钮。
        """
        core = self._proxy.core_type
        cfg = self._proxy_cfg()
        self._proxy.bin_path = ""     # 强制重新探测（含下载目录与手动放置位置）
        found, path, derr = False, "", ""
        try:
            path = self._proxy.detect_bin(cfg.get("v2ray_bin", ""))
            found = True
        except ValueError as e:
            derr = str(e)
        geo = v2c.geo_available(core)
        self.emit("proxy_log", "组件刷新：核心 %s%s | 规则库 %s" % (
            core, ("已就绪" if found else "未找到"), ("已就绪" if geo else "缺失")))
        self._proxy_publish()
        threading.Thread(target=self._proxy_check_version, args=(core,),
                         daemon=True, name="proxy-ver").start()
        return {"ok": True, "data": {
            "core": core, "found": found, "path": path, "err": derr,
            "current": bindl.cached_version(core), "geo": geo, "checking": True,
        }}

    def _proxy_check_version(self, core):
        latest, err = None, ""
        try:
            latest = bindl.latest_version(core)
        except bindl.BindlError as e:
            err = str(e)
        except Exception as e:
            err = str(e)
        cur = bindl.cached_version(core)
        try:
            self.emit("proxy_bins", {
                "core": core, "current": cur, "latest": latest, "check_err": err,
                "outdated": bool(cur and latest and cur != latest),
            })
        except Exception:
            pass

    def proxy_detect_bin(self, dir_path=""):
        """自动探测核心，返回探测结果（不设置配置）。"""
        try:
            path = self._proxy.detect_bin(
                self._proxy_cfg().get("v2ray_bin", ""), str(dir_path or "")
            )
            self._proxy_publish()
            return {"ok": True, "data": path}
        except ValueError as e:
            return {"ok": False, "err": str(e)}

    # -- 启停 -------------------------------------------------------------
    def proxy_start(self, http_port=10809, socks_port=10808, index=None):
        try:
            if not self._proxy.bin_path:
                self._proxy.detect_bin(self._proxy_cfg().get("v2ray_bin", ""))
            self._proxy.start(int(http_port or 10809), int(socks_port or 10808), index)
        except Exception as e:
            return {"ok": False, "err": str(e)}
        self.emit("proxy_log", "代理已启动（HTTP %d / SOCKS5 %d），已自动设置系统代理。" %
                  (self._proxy.http_port, self._proxy.socks_port))
        self._proxy_publish()
        return {"ok": True, "data": self._proxy.state()}

    def proxy_stop(self):
        try:
            was = self._proxy.running
            self._proxy.stop()
        except Exception as e:
            return {"ok": False, "err": str(e)}
        if was:
            self.emit("proxy_log", "代理已停止。")
        self._proxy_publish()
        return {"ok": True, "data": True}

    # -- 数据目录 ----------------------------------------------------------
    def proxy_open_dir(self):
        try:
            os.makedirs(v2c.PROXY_DIR, exist_ok=True)
            os.startfile(v2c.PROXY_DIR)
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": "打开数据目录失败：%s" % e}

    # -- 关闭清理 ------------------------------------------------------------
    def _stop_proxy(self):
        try:
            self._gate_stop.set()
            self._traffic.stop()
        except Exception:
            pass
        try:
            self._proxy.shutdown()
        except Exception:
            pass


def PROXY_BIN_DIR():
    return os.path.join(DATA_HOME, "proxy", "bin")


def _clipboard_text():
    """读取系统剪贴板文本（线程中调用，独立消息循环）。"""
    from ctypes import wintypes

    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard(0)
    try:
        if not user32.IsClipboardFormatAvailable(13):  # CF_UNICODETEXT
            return None
        handle = user32.GetClipboardData(13)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        try:
            if not ptr:
                return None
            try:
                return ctypes.cast(ptr, ctypes.c_wchar_p).value or ""
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            pass
    finally:
        user32.CloseClipboard()


def _pick_bin_file(bridge):
    import webview

    if bridge._window is None:
        raise RuntimeError("窗口尚未就绪，无法打开文件选择框。")
    result = bridge._window.create_file_dialog(
        webview.OPEN_DIALOG, file_types=("可执行程序 (*.exe)",)
    )
    if not result:
        return None
    if isinstance(result, (list, tuple)):
        return result[0] if result else None
    return result