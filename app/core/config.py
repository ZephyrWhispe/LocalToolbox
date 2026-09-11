"""应用配置持久化：%APPDATA%/LocalToolbox/config.json。"""

import json
import os
import sys
import threading

# 数据根目录：%APPDATA%/LocalToolbox（config.json 与 logs/ 均在此）
DATA_HOME = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "LocalToolbox"
)

DEFAULTS = {
    "theme": "dark",  # dark | light
    "history_limit": 100,  # 剪贴板历史上限
    "desensitize": True,  # 历史列表模糊显示敏感信息
    "aliases": {},  # 设备ID -> 备注名
    "start_page": "clipboard",
    "save_dir": "",  # 文件传输接收目录（空 = 系统下载目录）
    "screenshot_dir": "",  # 截图保存目录（空 = 图片/LocalToolbox）
    "shot_after": {"save": True, "copy": True, "edit": True,
                   "copy_path": False, "reveal": False},
    # 截图后自动任务链（ShareX After Capture Tasks）：保存文件/复制剪贴板/
    # 进编辑器/复制文件路径/定位到文件（后两项 v3.5b）
    "auto_accept": False,  # 文件传输自动接收
    "require_pairing": False,  # 未配对设备强制走配对加密（关闭则允许明文+逐次确认）
    "hud_enabled": False,  # 文件传输进度悬浮窗（HUD）
    "hotkey_enabled": True,  # 全局热键总开关（剪贴板呼出 + 截图，可自定义组合）
    "hotkey_clip": "Ctrl+Alt+V",  # 剪贴板历史呼出热键（含 Win 的组合被占用时自动接管）
    "hotkey_pop": "Win+V",  # 剪贴板弹窗热键（接管系统 Win+V，类 Ditto；v4.6）
    "clip_pop_autopaste": True,  # 弹窗选中后自动模拟 Ctrl+V 粘贴到之前的前台窗口（v4.6）
    "hotkey_shot": "Ctrl+Alt+A",  # 区域截图热键
    "hotkey_full": "Ctrl+Alt+F",  # 全屏截图热键（隐藏窗口后直拍虚拟桌面，v3.5）
    "update_auto_check": False,  # 自动检查更新（启动 30s 后首查，之后每天一次，v3.5c）
    "update_auto_download": False,  # 检查到新版本时自动下载二进制（不热替换运行中服务，v3.5c）
    "update_scope": ["openlist", "rclone", "core"],  # 检查范围：openlist/rclone/core(当前代理核心)，v3.5c
    "update_interval_hours": 24,  # 自动检查间隔（小时），v3.5c
    "clip_encrypt": False,  # 剪贴板历史落盘加密（T-02，密钥 DPAPI 保护）
    "openlist_bin": "",  # OpenList 程序路径（空 = 自动探测数据目录/运行时目录）
    "openlist_host": "127.0.0.1",  # OpenList 监听地址（v3.2）
    "openlist_port": 15244,  # OpenList 监听端口（v3.2）
    "openlist_fw": True,  # OpenList 启动时放行防火墙端口（v3.2）
    "openlist_autostart": False,  # OpenList 随本应用启动（v3.2）
    "rclone_bin": "",  # rclone.exe 路径（空 = 自动探测数据目录/运行时目录，v3.2）
    "rclone_autostart": False,  # 启动后恢复上次挂载（v3.2）
    "rclone_user": "",  # OpenList WebDAV 账号（空 = 匿名，v3.2）
    "rclone_pwd": "",  # OpenList WebDAV 密码（v3.2）
    "rclone_mount_type": "letter",  # rclone 挂载目标类型：letter / folder（v3.2）
    "rclone_mount_target": "V",  # rclone 挂载目标：盘符字母或文件夹路径（v3.2）
    "app_autostart": False,  # 本应用开机自启（HKCU Run 键，v3.2）
    "tray_close_exit": False,  # 关闭按钮行为：False=隐藏到托盘（默认），True=直接退出程序（v3.5c）
    "start_minimized": False,  # 启动时最小化到托盘（不弹出主窗口，v3.5d）
    "tray_notify": True,  # 托盘气泡通知总开关（传输完成/服务异常等，v3.5d）
    "notify_sound": False,  # 文件传输完成提示音（前端 Web Audio 短哔声，v3.5d）
    "ui_zoom": 1.0,  # 界面缩放（0.85–1.4，CSS zoom，v3.5d）
    "accent_color": "",  # 强调色（空=主题默认蓝；#RRGGBB hex，v3.5d）
    "device_name_custom": "",  # 自定义设备名（空 = 主机名，广播与连接握手显示，v3.5e）
    "clip_autostart": False,  # 启动后自动开启剪贴板同步（收发全开，v3.5e）
    "win_on_top": False,  # 主窗口置顶（v3.5e）
    "ui_reduce_motion": False,  # 减少动画（全局禁用过渡/动画，v3.5e）
    "clip_retain_days": 0,  # 剪贴板历史保留天数（0=永久，插入时按 ts 清理，v3.5f）
    "win_remember": False,  # 记住主窗口大小与位置（v3.5f）
    "win_geometry": {},  # 窗口几何 {x,y,width,height}（后端关闭时写入，v3.5f）
    "v2ray_bin": "",  # xray/v2ray/sing-box 核心程序路径（空 = 自动探测数据目录/运行目录）
    "v2ray_core": "xray",  # 当前核心：xray / sing-box / v2ray
    "v2ray_mode": "smart",  # 代理模式：global（全局）/ smart（智能分流）/ direct（直连）
    "v2ray_tun": False,  # TUN 模式（仅 sing-box，需管理员 + wintun.dll）
    "v2ray_sysproxy": "auto",  # 系统代理策略：auto / pac / none / clear
    "recorder_fps": 5,  # 录制默认帧率：GIF 2/5/10、视频 10/15/30（v3.3）
    "recorder_format": "video",  # 录制格式：gif / video（v3.3 ShareX 式升级）
    "recorder_scope": "full",  # 录制范围：full / region（v3.3）
    "recorder_audio": True,  # 视频录制同时录系统声音（v3.3，WASAPI 回环）
    # -- ShareX 功能扩展 --------------------------------------------------
    "editor_last_dir": "",  # 图片编辑器上次打开目录
    "pin_opacity": 0.9,  # 贴图默认不透明度（0.1–1.0）
    "pin_click_through": False,  # 贴图默认允许鼠标穿透
    "cliphist_enabled": True,  # 剪贴板历史监控随启动开启（Win+V 弹窗与历史页依赖，v4.6）
    "cliphist_limit": 500,  # 剪贴板历史上限条数
    "cliphist_monitor_images": True,  # 剪贴板历史同时记录图片
    "cliphist_retain_days": 30,  # 剪贴板历史保留天数（0=永久）
    "upload_service": "imgur",  # 默认图床：imgur / custom
    "upload_custom_url": "",  # 自定义图床上传接口
    "upload_custom_key": "",  # 自定义图床 API Key
    "batch_output_dir": "",  # 批量处理输出目录（空 = 源文件同目录）
    "video_output_dir": "",  # 视频编辑输出目录
    # -- 工具箱重构（v4.5） ------------------------------------------------
    "tool_favs": [],  # 工具收藏（tool-* id 列表，cap 20）
    "tool_recent": [],  # 最近使用工具（tool-* id 列表，cap 20）
    # -- 统一分流规则（v4.4） ----------------------------------------------
    "routing_auto_update": False,  # 规则库自动更新（geosite/geoip/.srs）
    "routing_update_hours": 168,  # 自动更新间隔（小时，6–720）
    "routing_dat_repo": "metacubex",  # dat 主源仓库：metacubex / loyalsoldier（失败自动换另一仓库）
    # -- 键鼠共享（v4.7） --------------------------------------------------
    "km_allow": True,  # 允许被控制（被控端总开关，持久化）
    "km_lock_input": False,  # 被控时屏蔽本机物理键鼠（对方注入操作不受影响）
    "km_edge_switch": True,  # 边缘穿越总开关（滑出屏幕边缘切换目标 / 切回本机）
    "km_edge_margin": 4,  # 边缘判定宽度（物理像素，1–50）
    "km_edges": {},  # 边缘邻居表 {left/right/top/bottom: 被控端 ip}
}


def set_app_autostart(enabled):
    """写 / 删 HKCU 启动项（Run 键），实现本应用开机自启。

    enabled=True 写入当前可执行文件路径（PyInstaller 打包后为 exe 绝对路径）；
    False 删除启动项（不存在则忽略）。写入失败抛 OSError（含中文原因）。
    """
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    value_name = "LocalToolbox"
    if enabled:
        if getattr(sys, "frozen", False):
            exe = os.path.abspath(sys.executable)
        else:
            exe = os.path.abspath(sys.argv[0])
        if not os.path.isfile(exe):
            raise OSError("可执行文件不存在，无法写入自启动：%s" % exe)
        cmd = '"%s"' % exe
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, cmd)
    else:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, value_name)
        except FileNotFoundError:
            pass  # 本来就没有启动项


class AppConfig:
    def __init__(self, path=None):
        if path is None:
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
            path = os.path.join(base, "LocalToolbox", "config.json")
        self.path = path
        self._lock = threading.Lock()
        self.data = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(loaded, dict):
            merged = dict(DEFAULTS)
            merged.update(loaded)
            self.data = merged

    def save(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            except OSError:
                pass

    def get(self, key, default=None):
        if key in self.data:
            return self.data[key]
        return DEFAULTS.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()
