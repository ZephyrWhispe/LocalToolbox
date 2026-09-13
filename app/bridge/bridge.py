"""桥接聚合：一个 Bridge 实例同时作为 pywebview 的 js_api。

公开方法命名约定 ``页面_动作``，各页面混入文件：
clipboard_api / shellmenu_api / share_api / web_api / ftp_api /
scan_api / network_api / file_api / km_api / transfer_api / log_api /
tools_api / screenshot_api。
"""

import os
import socket
import threading

from ..core import clash_core as cc
from ..core.config import AppConfig
from ..core.discovery import Discoverer
from .base import BridgeBase
from .backup_api import BackupApi
from .batch_api import BatchApi
from .clash_api import ClashApi
from .clipboard_api import ClipboardApi
from .cliphist_api import ClipHistApi
from .clip_pop_api import ClipPopApi
from .combine_api import CombineApi
from .dns_api import DnsApi
from .editor_api import EditorApi
from .file_api import FileApi
from .firewall_api import FirewallApi
from .ftp_api import FtpApi
from .hotkey_api import HotkeyApi
from .km_api import KmApi
from .log_api import LogApi
from .memo_api import MemoApi
from .memo_pop_api import MemoPopApi
from .network_api import NetworkApi
from .pan_api import PanApi
from .pin_api import PinApi
from .proxy_api import ProxyApi
from .recorder_api import RecorderApi
from .routing_api import RoutingApi
from .scan_api import ScanApi
from .scroll_api import ScrollApi
from .screenshot_api import ShotApi
from .share_api import ShareApi
from .shellmenu_api import ShellMenuApi
from .split_api import SplitApi
from .tools_api import ToolsApi
from .transfer_api import TransferApi
from .update_api import UpdateApi
from .upload_api import UploadApi
from .optimize_api import OptimizeApi
from .vault_api import VaultApi
from .video_api import VideoEditApi
from .web_api import WebApi
from .cap_api import CapApi
from .win_api import WindowApi

APP_VERSION = "5.5"

# v5.4 O7：整机配置导出脱敏清单（顶层键 / backup_targets 子键）
CFG_SENSITIVE_KEYS = {"rclone_pwd", "upload_custom_key"}
CFG_SENSITIVE_SUBKEYS = {"pwd", "password", "key"}


def _local_ips():
    ips = []
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    # v5.1c：socket 用 with 确保异常路径也释放（app_info 高频调用，泄漏会累积句柄）
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        if ip not in ips:
            ips.insert(0, ip)
    except OSError:
        pass
    return ips


class Bridge(
    ClashApi,
    ClipboardApi,
    ShellMenuApi,
    ShareApi,
    WebApi,
    FtpApi,
    ScanApi,
    NetworkApi,
    FileApi,
    KmApi,
    TransferApi,
    LogApi,
    ToolsApi,
    ShotApi,
    HotkeyApi,
    PanApi,
    ProxyApi,
    RecorderApi,
    WindowApi,
    UpdateApi,
    RoutingApi,
    EditorApi,
    PinApi,
    ClipHistApi,
    ClipPopApi,
    CombineApi,
    SplitApi,
    BatchApi,
    ScrollApi,
    UploadApi,
    VideoEditApi,
    DnsApi,
    MemoApi,
    VaultApi,
    MemoPopApi,
    BackupApi,
    CapApi,
    FirewallApi,
    OptimizeApi,
):
    def __init__(self):
        # 通过第一个混入类的 MRO 调用 BridgeBase.__init__
        super().__init__()
        self.cfg = AppConfig()
        # v3.5e：自定义设备名（空 = 主机名）
        self._custom_name = str(self.cfg.get("device_name_custom") or "").strip() or None
        self.discovery = Discoverer(name=self._custom_name, log=self.emit_log)
        self.discovery.start()

        self._init_clash()
        self._init_clipboard()
        self._init_shellmenu()
        self._init_share()
        self._init_web()
        self._init_ftp()
        self._init_scan()
        self._init_network()
        self._init_file()
        self._init_km()
        self._init_transfer()
        self._init_tools()
        self._init_shot()
        self._init_hotkey()
        self._init_pan()
        self._init_proxy()
        self._init_rec()
        self._init_win()
        self._init_cap()
        self._init_update()
        self._init_routing()
        self._init_editor()
        self._init_pin()
        self._init_cliphist()
        self._init_clip_pop()
        self._init_combine()
        self._init_split()
        self._init_batch()
        self._init_scroll()
        self._init_upload()
        self._init_video()
        self._init_dns()
        self._init_memo()
        self._init_optimize()
        self._init_vault()
        self._init_backup()
        self._init_memo_pop()
        self._init_firewall()
        # v5.4 二期：本地 IPC 只读接口（默认关；开启时随应用启动，仅回环）
        from ..core import ipc_server
        if self.cfg.get("ipc_enabled", False):
            try:
                ipc_server.start(self.cfg.get("ipc_port", 17258))
            except OSError as e:
                self.emit_log("IPC 接口启动失败：%s" % e)
        # v3.2：按配置恢复 OpenList 服务与 rclone 挂载（后台线程，不阻塞启动）
        threading.Thread(target=self._pan_autostart_restore, daemon=True, name="pan-autostart").start()
        # v3.5e：按配置自动开启剪贴板同步（后台线程，防火墙/绑定不阻塞启动）
        threading.Thread(target=self._clip_autostart, daemon=True, name="clip-autostart").start()
        # v5.1b：按配置自启被控端监听（km_allow 开启时）
        threading.Thread(target=self._km_autostart, daemon=True, name="km-autostart").start()

    def shutdown(self):
        """应用退出前清理（线程收束，避免子线程/UI 对象在进程退出时被错误线程销毁）。"""
        # v5.4 二期：本地 IPC 接口收束
        from ..core import ipc_server
        ipc_server.stop()
        self.hotkey.stop()
        self._stop_pan()
        self._stop_proxy()
        self._stop_transfer()
        self._stop_update()
        self._stop_routing()
        # v4.6：剪贴板弹窗常驻窗口销毁（watchdog 随窗口消亡）
        try:
            if getattr(self, "_pop", None) and self._pop.get("win"):
                self._pop["win"].destroy()
        except Exception:
            pass
        # v5.0：备忘录弹窗常驻窗口销毁
        try:
            if getattr(self, "_memo_pop", None) and self._memo_pop.get("win"):
                self._memo_pop["win"].destroy()
        except Exception:
            pass
        # v5.1：Umi-OCR 内核服务收束
        try:
            from ..core import ocr_umi
            ocr_umi.stop_server()
        except Exception:
            pass
        if getattr(self, "_rec", None) and self._rec.running:
            self._rec.stop()
        if getattr(self, "_pin_mgr", None):
            self._pin_mgr.shutdown()
        if getattr(self, "_clip_monitor", None):
            self._clip_monitor.stop()
        # v5.1b：被控端监听 socket 与 KM 钩子线程收束
        try:
            self._km_target.stop()
        except Exception:
            pass
        self._stop_clash()
        self.discovery.stop()

    # -- v3.5e 剪贴板同步自启 -------------------------------------------------
    def _clip_autostart(self):
        """配置开启时随应用自动启动剪贴板同步（收发全开）。"""
        if not self.cfg.get("clip_autostart", False):
            return
        r = self.clip_start(True, True, True)
        if r.get("ok"):
            self.emit_log("剪贴板同步已随应用自动开启。")
        else:
            self.emit_log("剪贴板同步自动开启失败：%s" % r.get("err", ""))

    # -- v3.2 自启动恢复 -----------------------------------------------------
    def _pan_autostart_restore(self):
        """按配置恢复：OpenList 服务随应用启动；rclone 自动恢复上次挂载。

        后台线程执行（OpenList start 最长轮询 30s），失败仅记日志不打断启动。
        """
        import re as _re

        try:
            if self.cfg.get("openlist_autostart"):
                try:
                    self._pan.detect_bin(self.cfg.get("openlist_bin", ""))
                    if not self._pan.running:
                        self._pan.start(
                            self.cfg.get("openlist_host", "") or "127.0.0.1",
                            int(self.cfg.get("openlist_port", 15244) or 15244),
                            bool(self.cfg.get("openlist_fw", True)),
                        )
                        self.emit("pan_log", "OpenList 已随应用自动启动。")
                except Exception as e:
                    self.emit("pan_log", "OpenList 自动启动失败：%s" % e)
            if self.cfg.get("rclone_autostart"):
                try:
                    mgr = self._rclone_manager()
                    if not mgr.list_mounted():
                        target = str(self.cfg.get("rclone_mount_target", "") or "").strip()
                        mtype = str(self.cfg.get("rclone_mount_type", "letter")).lower()
                        if not target:
                            return
                        args = dict(
                            user=self.cfg.get("rclone_user", ""),
                            pwd=self.cfg.get("rclone_pwd", ""),
                        )
                        if mtype == "letter" and _re.fullmatch(r"[A-Z]", target):
                            mgr.mount(target_type="letter", letter=target, folder="", **args)
                        else:
                            mgr.mount(target_type="folder", letter="", folder=target, **args)
                        self.emit("pan_log", "已恢复 Rclone 挂载：%s" % target)
                except Exception as e:
                    self.emit("pan_log", "Rclone 恢复挂载失败：%s" % e)
        except Exception:
            pass

    # -- 通用 API -------------------------------------------------------
    def app_info(self):
        try:
            from ..core.privilege import is_admin as _is_admin
        except Exception:
            _is_admin = lambda: False
        return {
            "ok": True,
            "data": {
                "version": APP_VERSION,
                "device_id": self.discovery.device_id,
                "device_name": self.discovery.device_name,
                "ips": _local_ips(),
                "discovery_port": self.discovery.port,
                "admin": _is_admin(),
            },
        }

    def devices_list(self):
        devices = self.discovery.devices()
        aliases = self.cfg.get("aliases", {}) or {}
        for d in devices:
            d["alias"] = aliases.get(d["id"], "")
            d["paired"] = bool(getattr(self, "trust", None) and self.trust.get(d["id"]))
        devices.sort(key=lambda d: d.get("name", ""))
        return {"ok": True, "data": devices}

    def device_alias(self, device_id, name):
        name = str(name or "").strip()
        with self.cfg._lock:  # v5.1c：读-改-写原子化，并发设置不丢别名
            aliases = dict(self.cfg.get("aliases", {}) or {})
            if name:
                aliases[str(device_id)] = name
            else:
                aliases.pop(str(device_id), None)
            self.cfg.data["aliases"] = aliases
        self.cfg.save()
        return {"ok": True, "data": name}

    def cfg_get(self):
        return {"ok": True, "data": dict(self.cfg.data)}

    def cfg_set(self, key, value):
        allowed = {
            "theme", "history_limit", "desensitize", "start_page",
            "save_dir", "screenshot_dir", "auto_accept", "require_pairing",
            "hud_enabled", "clip_encrypt", "hotkey_enabled",
            "hotkey_clip", "hotkey_shot", "hotkey_full",
            # v3.2：OpenList / Rclone / 自启动
            "openlist_host", "openlist_port", "openlist_fw", "openlist_autostart",
            "rclone_bin", "rclone_autostart", "rclone_user", "rclone_pwd",
            "rclone_mount_type", "rclone_mount_target", "app_autostart",
            "recorder_fps", "recorder_format", "recorder_scope", "recorder_audio",
            "pickcolor_zoom",
            "sub_autoupdate_hours",
            "clash_bin", "clash_mixed_port", "clash_api_port", "clash_mode",
            "clash_tun", "clash_sub_autoupdate_hours", "clash_log_level",
            # v4.6：剪贴板弹窗
            "hotkey_pop", "clip_pop_autopaste",
            # v3.5d：更多自定义选项
            "tray_close_exit", "start_minimized", "tray_notify",
            "notify_sound", "ui_zoom", "accent_color",
            # v3.5e：更多自定义选项
            "device_name_custom", "clip_autostart", "win_on_top", "ui_reduce_motion",
            # v3.5f：更多自定义选项（win_geometry 仅后端写，不暴露前端）
            "clip_retain_days", "win_remember",
            # ShareX 功能扩展
            "editor_last_dir", "pin_opacity", "pin_click_through",
            "cliphist_monitor_images", "cliphist_retain_days",
            "upload_service", "upload_custom_url", "upload_custom_key",
            "upload_targets", "clip_cmds", "ipc_enabled", "ipc_port",
            "batch_output_dir", "video_output_dir",
            # v4.5：工具箱收藏与最近使用（列表键，白名单过滤见下）
            "tool_favs", "tool_recent",
            # v4.7：键鼠共享设置
            "km_allow", "km_lock_input", "km_edge_switch", "km_edge_margin",
            "km_edges",
            # v5.0：备忘录 / 密码库 / 备份 / 文件收藏
            "hotkey_memo", "memo_pop_group_last", "vault_autolock_min",
            "vault_clip_clear_sec", "backup_targets", "backup_keep",
            "backup_autoupload", "backup_autoupload_hours", "file_favs",
            # v5.1：OCR
            "hotkey_ocr", "ocr_engine", "ocr_merge_lines",
            "ocr_umi_url", "ocr_umi_path", "ocr_umi_autostart",
            # v5.2：文件管理标签会话（「继续上次浏览」）
            "file_tabs",
        }
        if key not in allowed:
            return {"ok": False, "err": "不支持的配置项：%s" % key}
        if key == "history_limit":
            try:
                value = max(10, min(1000, int(value)))
            except (TypeError, ValueError):
                return {"ok": False, "err": "历史上限需为数字"}
            self.clip.history_limit = value
            del self.clip.history[value:]
            st = getattr(self.clip, "store", None)
            if st is not None:
                st.limit = value  # 运行时修剪按新上限（insert 时 prune）
        if key == "desensitize":
            value = bool(value)
        if key == "start_page":
            value = str(value)
        if key == "theme":
            value = str(value) if str(value) in ("auto", "light", "dark") else "dark"
        if key in ("hotkey_clip", "hotkey_shot", "hotkey_pop", "hotkey_memo",
                   "hotkey_ocr", "hotkey_full"):
            from ..core.hotkey import parse_combo as _parse_combo
            try:
                value = _parse_combo(str(value))[2]  # 存规范显示名
            except ValueError as e:
                return {"ok": False, "err": "快捷键格式错误：%s" % e}
        if key == "clip_pop_autopaste":
            value = bool(value)
        if key == "auto_accept":
            value = bool(value)
        if key == "hud_enabled":
            value = bool(value)
        if key == "clip_encrypt":
            value = bool(value)
            # v5.4 O9：开关切换即全量迁移（开启：存量明文加密；关闭：密文解密回明文）
            # 迁移前自动备份库文件（store.migrate 内实现），失败抛错不落配置
            st = getattr(self.clip, "store", None)
            if st is not None:
                from ..core.clipboard_store import clip_store_key
                from ..core.config import DATA_HOME
                if value:
                    k = clip_store_key(DATA_HOME)
                    if k is None:
                        return {"ok": False,
                                "err": "加密密钥初始化失败（DPAPI 不可用），未开启加密"}
                    st.key = k
                try:
                    self._clip_migrated = st.migrate(encrypt=value)
                except RuntimeError as e:
                    return {"ok": False, "err": str(e)}
                if not value:
                    st.key = None  # 迁移回明文后再摘除密钥
        if key in ("ipc_enabled", "ipc_port"):
            # v5.4 二期：IPC 开关/端口变更即时生效（停止旧监听后按新配置重启）
            from ..core import ipc_server
            ipc_server.stop()
            enabled = bool(value) if key == "ipc_enabled" \
                else bool(self.cfg.get("ipc_enabled", False))
            port = value if key == "ipc_port" \
                else self.cfg.get("ipc_port", 17258)
            if enabled:
                try:
                    ipc_server.start(port)
                except (OSError, ValueError) as e:
                    return {"ok": False, "err": "IPC 接口启动失败：%s" % e}
        if key == "save_dir":
            value = str(value)
        if key == "screenshot_dir":
            value = str(value)
        # v3.2 键：类型收紧 + 特殊副作用 -------------------------------------
        if key == "openlist_host":
            value = str(value)
        if key == "openlist_port":
            try:
                value = max(1, min(65535, int(value)))
            except (TypeError, ValueError):
                return {"ok": False, "err": "端口需为数字"}
        if key in ("openlist_fw", "openlist_autostart", "rclone_autostart", "app_autostart"):
            value = bool(value)
        if key == "rclone_bin":
            value = str(value)
        if key in ("rclone_user", "rclone_pwd"):
            value = str(value)
        if key == "rclone_mount_type":
            value = "letter" if str(value) == "letter" else "folder"
        if key == "rclone_mount_target":
            value = str(value).strip()
        if key == "recorder_fps":
            try:
                value = int(value)
            except (TypeError, ValueError):
                return {"ok": False, "err": "fps 需为数字"}
            if value not in (2, 5, 10, 15, 30):
                return {"ok": False, "err": "fps 仅支持 2/5/10（GIF）或 10/15/30（视频）"}
        if key == "recorder_format":
            value = "gif" if str(value) == "gif" else "video"
        if key == "recorder_scope":
            value = "region" if str(value) == "region" else "full"
        if key == "recorder_audio":
            value = bool(value)
        if key == "app_autostart":
            from ..core.config import set_app_autostart

            try:
                set_app_autostart(bool(value))
            except OSError as e:
                return {"ok": False, "err": "开机自启设置失败：%s" % e}
        # v3.5d/e/f：新增自定义选项的类型收紧 --------------------------------
        if key in ("tray_close_exit", "start_minimized", "tray_notify", "notify_sound",
                   "clip_autostart", "win_on_top", "ui_reduce_motion", "win_remember"):
            value = bool(value)
        if key == "clip_retain_days":
            try:
                value = max(0, min(365, int(value)))
            except (TypeError, ValueError):
                return {"ok": False, "err": "保留天数需为数字"}
            # 立即生效：更新 store 天数并清理过期记录 + 过滤内存历史
            st = getattr(getattr(self, "clip", None), "store", None)
            if st is not None and hasattr(st, "set_days"):
                try:
                    st.set_days(value)
                except Exception:
                    pass
            clip = getattr(self, "clip", None)
            if clip is not None and value > 0 and hasattr(clip, "history"):
                import time as _time

                cutoff = _time.time() - value * 86400
                clip.history[:] = [e for e in clip.history
                                   if float(e.get("ts") or 0) >= cutoff]
        if key == "ui_zoom":
            try:
                value = max(0.85, min(1.4, float(value)))
            except (TypeError, ValueError):
                return {"ok": False, "err": "缩放需为数字"}
        if key == "accent_color":
            import re as _re

            value = str(value or "").strip()
            if value and not _re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                return {"ok": False, "err": "强调色格式应为 #RRGGBB"}
        if key in ("clash_mixed_port", "clash_api_port"):
            try:
                value = max(1, min(65535, int(value)))
            except (TypeError, ValueError):
                return {"ok": False, "err": "端口需为数字"}
        if key == "clash_mode":
            value = str(value) if str(value) in ("rule", "global", "direct") else "rule"
        if key == "clash_tun":
            value = bool(value)
        if key == "clash_bin":
            value = str(value)
        if key == "clash_sub_autoupdate_hours":
            try:
                value = max(0.0, min(168.0, float(value)))
            except (TypeError, ValueError):
                return {"ok": False, "err": "周期需为数字（小时）"}
        if key == "clash_log_level":
            value = str(value).lower() if str(value).lower() in cc.LOG_LEVELS else "warning"
        if key in ("tool_favs", "tool_recent"):
            # v4.5：工具收藏 / 最近使用 —— 字符串数组，只收已知工具 id，去重保序，cap 20
            known = {
                "tool-timestamp", "tool-regex", "tool-password", "tool-qr",
                "tool-port", "tool-dns", "tool-monitor", "tool-proxy",
                "tool-color", "tool-ocr", "tool-palette",
                "tool-text", "tool-files", "tool-uuid", "tool-radix",
                "editor", "cliphist", "combine", "split", "batch", "video",
            }
            if not isinstance(value, (list, tuple)):
                return {"ok": False, "err": "该配置项需为列表"}
            seen, out = set(), []
            for v in value:
                v = str(v or "").strip()
                if v in known and v not in seen:
                    seen.add(v)
                    out.append(v)
            value = out[:20]
        if key == "device_name_custom":
            # v3.5e：自定义设备名 —— 立即更新广播名（3s 内对其他设备可见）；
            # 文件传输 / 键鼠共享的握手名重启后完全生效
            from ..core.discovery import local_hostname

            value = str(value or "").strip()[:32]
            fallback = local_hostname()
            if hasattr(self, "discovery"):
                self.discovery.device_name = value or fallback
            if hasattr(self, "clip"):
                self.clip.device_name = value or fallback
        # ShareX 功能扩展：类型收紧 -------------------------------------------
        if key in ("pin_click_through", "cliphist_monitor_images"):
            value = bool(value)
        if key == "pin_opacity":
            try:
                value = max(0.1, min(1.0, float(value)))
            except (TypeError, ValueError):
                return {"ok": False, "err": "不透明度需为数字"}
        if key == "cliphist_retain_days":
            try:
                value = max(0, min(3650, int(value)))
            except (TypeError, ValueError):
                return {"ok": False, "err": "保留天数需为数字"}
        if key in ("editor_last_dir", "upload_custom_url", "upload_custom_key",
                    "batch_output_dir", "video_output_dir"):
            value = str(value)
        if key == "upload_service":
            value = str(value) if str(value) in ("imgur", "custom") else "imgur"
        # v4.7 键鼠共享：类型收紧 + 即时生效副作用 ---------------------------
        if key in ("km_allow", "km_lock_input", "km_edge_switch"):
            value = bool(value)
        if key == "km_edge_margin":
            try:
                value = max(1, min(50, int(value)))
            except (TypeError, ValueError):
                return {"ok": False, "err": "边缘宽度需为数字（像素）"}
            c = getattr(self, "_km_controller", None)
            if c is not None:
                c.edge_margin = value
        if key == "km_edges":
            if not isinstance(value, dict):
                return {"ok": False, "err": "边缘邻居表格式错误"}
            value = {d: str(value.get(d) or "").strip()
                     for d in ("left", "right", "top", "bottom")}
            e = getattr(self, "_km_edges", None)
            if e is not None:
                e.update(value)
        if key == "km_allow":
            t = getattr(self, "_km_target", None)
            if t is not None:
                t.enabled = value
                if not value:
                    t.controlled = False
        if key == "km_lock_input":
            t = getattr(self, "_km_target", None)
            if t is not None and hasattr(t, "set_lock_input"):
                t.set_lock_input(value)
        # v5.0 备忘录 / 密码库 / 备份 / 文件收藏 -----------------------------
        if key == "memo_pop_group_last":
            try:
                value = max(0, int(value))
            except (TypeError, ValueError):
                value = 0
        if key == "vault_autolock_min":
            try:
                value = max(0, min(1440, int(value)))
            except (TypeError, ValueError):
                return {"ok": False, "err": "自动锁定需为数字（分钟，0=不锁）"}
        if key == "vault_clip_clear_sec":
            try:
                value = max(0, min(600, int(value)))
            except (TypeError, ValueError):
                return {"ok": False, "err": "清除秒数需为数字（0=不清除）"}
        if key == "backup_keep":
            try:
                value = max(1, min(50, int(value)))
            except (TypeError, ValueError):
                return {"ok": False, "err": "保留份数需为数字（1–50）"}
        if key == "backup_autoupload":
            value = bool(value)
        if key == "backup_autoupload_hours":
            try:
                value = max(6, min(720, int(value)))
            except (TypeError, ValueError):
                return {"ok": False, "err": "间隔需为数字（小时，6–720）"}
        if key == "backup_targets":
            from ..core.cloud_backup import normalize_targets
            if not isinstance(value, (list, tuple)):
                return {"ok": False, "err": "备份目标需为列表"}
            value = normalize_targets(value)
        if key == "file_favs":
            if not isinstance(value, (list, tuple)):
                return {"ok": False, "err": "收藏路径需为列表"}
            favs, seen = [], set()
            for f in value:
                if isinstance(f, dict) and f.get("path"):
                    p = str(f["path"]).strip()[:500]
                    if p and p not in seen:
                        seen.add(p)
                        favs.append({"name": str(f.get("name") or "").strip()[:32]
                                     or os.path.basename(p.rstrip("\\/")) or p,
                                     "path": p})
            value = favs[:20]
        # v5.1 OCR -----------------------------------------------------------
        if key == "ocr_engine":
            value = str(value) if str(value) in ("winrt", "rapid", "umi") else "winrt"
        if key in ("ocr_merge_lines", "ocr_umi_autostart"):
            value = bool(value)
        if key in ("ocr_umi_url", "ocr_umi_path"):
            value = str(value or "").strip()
        self.cfg.set(key, value)
        if key == "clip_encrypt":
            # v5.4 O9：附带全量迁移条数（前端 toast 提示）
            return {"ok": True, "data": value,
                    "migrated": getattr(self, "_clip_migrated", 0)}
        return {"ok": True, "data": value}

    # -- 整机配置导出/导入（v5.4 O7，Clash Verge 配置域 / v2rayN 发布完整性思想） --
    def cfg_export_pick(self):
        """选择导出文件保存路径。"""
        from tkinter import filedialog
        import time as _t
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile="LocalToolbox_config_%s.json" % _t.strftime("%Y%m%d_%H%M%S"),
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return {"ok": False, "err": "未选择保存路径"}
        return {"ok": True, "data": {"path": path}}

    def cfg_export(self, path=None, sanitize=True):
        """导出全部配置域为单文件 JSON：版本号 + 脱敏选项 + SHA-256 校验和。"""
        import hashlib
        import json
        import time as _t

        from ..core.config import DATA_HOME
        data = dict(self.cfg.data)
        if sanitize:
            for k in CFG_SENSITIVE_KEYS:
                data.pop(k, None)
            if isinstance(data.get("backup_targets"), list):
                data["backup_targets"] = [
                    {kk: ("" if str(kk) in CFG_SENSITIVE_SUBKEYS else vv)
                     for kk, vv in t.items()} if isinstance(t, dict) else t
                    for t in data["backup_targets"]]
        payload = {
            "app": "LocalToolbox",
            "version": APP_VERSION,
            "exported_ts": _t.time(),
            "sanitize": bool(sanitize),
            "data": data,
        }
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        payload["checksum"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if not path:
            path = os.path.join(
                DATA_HOME, "exports", "config_%s.json" % _t.strftime("%Y%m%d_%H%M%S"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except OSError as e:
            return {"ok": False, "err": "写入导出文件失败：%s" % e}
        return {"ok": True, "data": {"path": path, "count": len(data),
                                     "sanitize": bool(sanitize)}}

    def cfg_import_pick(self):
        """选择要导入的配置文件。"""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return {"ok": False, "err": "未选择文件"}
        return {"ok": True, "data": {"path": path}}

    def cfg_import(self, path, strategy="skip_existing"):
        """导入整机配置：逐键经 cfg_set 合并（复用全部校验与热生效副作用）。

        strategy：skip_existing=存量优先（已有非默认值保留）/ overwrite=导入优先。
        导出文件的 SHA-256 校验和先验后并；未知键跳过。导入过程每键独立落盘，
        中途失败不产生半更新配置文件（cfg.set 为原子写 + .bak）。
        """
        import hashlib
        import json

        from ..core.config import DEFAULTS
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError) as e:
            return {"ok": False, "err": "读取配置文件失败：%s" % e}
        if (not isinstance(payload, dict) or payload.get("app") != "LocalToolbox"
                or not isinstance(payload.get("data"), dict)):
            return {"ok": False, "err": "文件格式不正确（不是本应用导出的配置文件）"}
        body = json.dumps({k: v for k, v in payload.items() if k != "checksum"},
                          ensure_ascii=False, sort_keys=True, indent=2)
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != payload.get("checksum"):
            return {"ok": False, "err": "校验和不匹配：文件已损坏或被修改"}
        if strategy not in ("skip_existing", "overwrite"):
            return {"ok": False, "err": "合并策略无效"}
        merged, skipped, failed = 0, 0, []
        for k, v in payload["data"].items():
            if k not in DEFAULTS or k in ("win_geometry",):
                skipped += 1
                continue
            if strategy == "skip_existing":
                cur = self.cfg.data.get(k)
                if cur not in (None, "") and cur != DEFAULTS.get(k):
                    skipped += 1
                    continue
            # 显式走类方法（兼容最小桩测试环境：实例上无 cfg_set 绑定）
            r = Bridge.cfg_set(self, k, v)
            if r.get("ok"):
                merged += 1
            else:
                failed.append("%s（%s）" % (k, r.get("err") or ""))
        return {"ok": True, "data": {
            "merged": merged, "skipped": skipped, "failed": failed,
            "version": str(payload.get("version") or ""),
            "sanitize": bool(payload.get("sanitize")),
        }}

    # -- 服务总览（v5.4 O11：聚合各服务现有状态 API，单次查询） ----------------
    def svc_overview(self):
        """聚合服务状态：同步 / OpenList / WebDAV 挂载 / FTP / HTTP 共享 / 双代理。

        各服务沿用现有 *_state/get_state API；单项异常不影响其余项（置 None）。
        """
        def pick(fn, keys):
            try:
                r = fn()
                if isinstance(r, dict) and r.get("ok"):
                    d = r.get("data") or {}
                    out = {k: d.get(k) for k in keys}
                    out["ready"] = True
                    return out
            except Exception:
                pass
            return {"ready": False}

        mounts = []
        try:
            r = self.pan_rclone_state()
            if isinstance(r, dict) and r.get("ok"):
                mounts = r["data"].get("mounts") or []
        except Exception:
            pass
        return {"ok": True, "data": {
            "clip": pick(self.clip_get_state, ("running", "send", "recv")),
            "openlist": pick(self.pan_get_state, ("running", "port")),
            "webdav": {"ready": True, "mounts": mounts},
            "ftp": pick(lambda: self.ftp_state(), ("server",)),
            "http": pick(self.web_get_state, ("running", "port", "urls")),
            "clash": pick(self.clash_get_state, ("running", "sys_proxy", "mode", "mixed_port")),
            "v2ray": pick(self.proxy_get_state, ("running", "proc_alive")),
        }}