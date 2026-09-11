"""桥接聚合：一个 Bridge 实例同时作为 pywebview 的 js_api。

公开方法命名约定 ``页面_动作``，各页面混入文件：
clipboard_api / shellmenu_api / share_api / web_api / ftp_api /
scan_api / network_api / file_api / km_api / transfer_api / log_api /
tools_api / screenshot_api。
"""

import socket
import threading

from ..core import clash_core as cc
from ..core.config import AppConfig
from ..core.discovery import Discoverer
from .base import BridgeBase
from .batch_api import BatchApi
from .clash_api import ClashApi
from .clipboard_api import ClipboardApi
from .cliphist_api import ClipHistApi
from .clip_pop_api import ClipPopApi
from .combine_api import CombineApi
from .dns_api import DnsApi
from .editor_api import EditorApi
from .file_api import FileApi
from .ftp_api import FtpApi
from .hotkey_api import HotkeyApi
from .km_api import KmApi
from .log_api import LogApi
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
from .video_api import VideoEditApi
from .web_api import WebApi
from .win_api import WindowApi

APP_VERSION = "3.0"


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
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
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
        # v3.2：按配置恢复 OpenList 服务与 rclone 挂载（后台线程，不阻塞启动）
        threading.Thread(target=self._pan_autostart_restore, daemon=True, name="pan-autostart").start()
        # v3.5e：按配置自动开启剪贴板同步（后台线程，防火墙/绑定不阻塞启动）
        threading.Thread(target=self._clip_autostart, daemon=True, name="clip-autostart").start()

    def shutdown(self):
        """应用退出前清理（线程收束，避免子线程/UI 对象在进程退出时被错误线程销毁）。"""
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
        if getattr(self, "_rec", None) and self._rec.running:
            self._rec.stop()
        if getattr(self, "_pin_mgr", None):
            self._pin_mgr.shutdown()
        if getattr(self, "_clip_monitor", None):
            self._clip_monitor.stop()
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
        aliases = self.cfg.get("aliases", {}) or {}
        name = str(name or "").strip()
        if name:
            aliases[str(device_id)] = name
        else:
            aliases.pop(str(device_id), None)
        self.cfg.set("aliases", aliases)
        return {"ok": True, "data": name}

    def cfg_get(self):
        return {"ok": True, "data": dict(self.cfg.data)}

    def cfg_set(self, key, value):
        allowed = {
            "theme", "history_limit", "desensitize", "start_page",
            "save_dir", "screenshot_dir", "auto_accept", "require_pairing",
            "hud_enabled", "clip_encrypt", "hotkey_enabled",
            "hotkey_clip", "hotkey_shot",
            # v3.2：OpenList / Rclone / 自启动
            "openlist_host", "openlist_port", "openlist_fw", "openlist_autostart",
            "rclone_bin", "rclone_autostart", "rclone_user", "rclone_pwd",
            "rclone_mount_type", "rclone_mount_target", "app_autostart",
            "recorder_fps", "recorder_format", "recorder_scope", "recorder_audio",
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
            "batch_output_dir", "video_output_dir",
            # v4.5：工具箱收藏与最近使用（列表键，白名单过滤见下）
            "tool_favs", "tool_recent",
            # v4.7：键鼠共享设置
            "km_allow", "km_lock_input", "km_edge_switch", "km_edge_margin",
            "km_edges",
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
        if key in ("hotkey_clip", "hotkey_shot", "hotkey_pop"):
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
            # 热切换 store 密钥：开启后用 DPAPI 密钥（新的历史即加密，旧明文保留）；
            # 关闭后置 None（历史中的密文条目显示为「已加密」，数据仍在）
            if hasattr(self, "clip") and getattr(self.clip, "store", None) is not None:
                from ..core.clipboard_store import clip_store_key
                from ..core.config import DATA_HOME
                self.clip.store.key = clip_store_key(DATA_HOME) if value else None
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
        self.cfg.set(key, value)
        return {"ok": True, "data": value}