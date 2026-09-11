"""LocalToolbox —— pywebview 入口（Web UI + Python 核心）。"""

import ctypes
import os
import sys

import webview

from app.bridge.bridge import Bridge
from app.core import logger as applog
from app.core.config import DATA_HOME
from app.core.single_instance import acquire_or_exit, start_show_watcher
from app.core.tray import TrayController

APP_TITLE = "LocalToolbox"


def _restore_geometry(cfg):
    """v3.5f：win_remember 开启且几何有效 → 返回 create_window 覆盖参数。

    校验坐标落在虚拟桌面范围内（多显示器拔掉后不至于开到屏幕外）；
    无效则只还原尺寸，位置交给系统居中。
    """
    if not cfg.get("win_remember", False):
        return {}
    geo = cfg.get("win_geometry") or {}
    try:
        w, h = int(geo["width"]), int(geo["height"])
        if not (400 <= w <= 20000 and 300 <= h <= 20000):
            return {}
        out = {"width": w, "height": h}
        x, y = int(geo.get("x") or 0), int(geo.get("y") or 0)
        u32 = ctypes.windll.user32
        vx, vy = u32.GetSystemMetrics(76), u32.GetSystemMetrics(77)
        cx, cy = u32.GetSystemMetrics(78), u32.GetSystemMetrics(79)
        if vx - 100 <= x <= vx + cx and vy - 100 <= y <= vy + cy:
            out["x"], out["y"] = x, y
        return out
    except (KeyError, TypeError, ValueError):
        return {}


def enable_dpi_awareness():
    """进程级 Per-Monitor DPI Aware（截图/窗口识别坐标系正确的前提）。

    声明后 GetWindowRect / DwmGetWindowAttribute / GetCursorPos /
    EnumDisplayMonitors 均返回物理像素，与 ImageGrab 截图坐标系一致
    （DPI≠100% 时活动窗口截图、遮罩窗口吸附才不会错位）。须在创建任何
    窗口前调用；失败静默降级（系统不支持时坐标换算走 screen.py 兜底）。
    """
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE（Win 8.1+）
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # Vista 兜底
    except Exception:
        pass


def install_fatal_hook():
    """未捕获异常 → FATAL 日志（含堆栈），避免进程无声消亡。"""

    def _hook(exc_type, exc, tb):
        applog.get_logger("__main__").fatal(
            "未捕获异常 %s: %s", exc_type.__name__, exc,
            exc_info=(exc_type, exc, tb),
        )

    sys.excepthook = _hook


def parse_cli(argv):
    """解析右键菜单传入的参数：--share/--webdav/--clip <路径>。"""
    action = path = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--share", "--webdav", "--clip") and i + 1 < len(argv):
            action = arg[2:]
            path = argv[i + 1]
            i += 2
        else:
            i += 1
    return action, path


def ui_index():
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "webui", "index.html")


def _ocr_probe():
    """OCR 链自检（v4.2）：winrt 依赖 → 引擎语言包逐级探测。

    返回 True（全链可用）/ "no-engine"（依赖在但系统无 OCR 引擎语言包）/
    False（winrt 依赖缺失，功能会自动禁用）。
    """
    from app.core import ocr as _ocr
    if not _ocr.is_available():
        return False
    try:
        from winrt.windows.media.ocr import OcrEngine
        return True if OcrEngine.try_create_from_user_profile_languages() else "no-engine"
    except Exception:
        return False


def _clash_config_probe():
    """Clash 配置生成链路（PyYAML + build_config + 自定义规则置顶）。"""
    from app.core import clash_core as cc
    cfg, info = cc.build_config(
        [{"type": "trojan", "addr": "1.2.3.4", "port": 443, "id": "p",
          "sni": "s.example.com", "remark": "selftest"}], [],
        data_dir="", custom_rules=["DOMAIN,selftest.example,DIRECT"])
    assert cfg["proxies"] and cfg["rules"][0] == "DOMAIN,selftest.example,DIRECT"
    assert cfg["proxy-groups"] and cfg["external-controller"]
    return "%d 条规则 / %s" % (len(cfg["rules"]), cfg["proxy-groups"][0]["name"])


def _arch_pick_probe():
    """架构自适应选包（本机架构 → mihomo 构建变体）。"""
    from app.core import bindl
    got = bindl._pick_asset_by_arch(
        ["mihomo-windows-amd64-v1-go120-v1.19.30.zip",
         "mihomo-windows-amd64-v3-go125-v1.19.30.zip"], "mihomo")
    info = bindl.cached_info("mihomo")
    return "%s → %s（已装 %s）" % (bindl.arch_label(), got,
                                  info.get("asset") or info.get("version") or "无")


def _webui_clash_probe():
    """前端资源：Clash 单页 + 分流规则页 + 剪贴板弹窗均已随包。"""
    base = sys._MEIPASS if getattr(sys, "frozen", False) else os.path.dirname(
        os.path.abspath(__file__))
    path = os.path.join(base, "webui", "js", "pages", "clash.js")
    if not os.path.isfile(path):
        return "缺少 webui/js/pages/clash.js"
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read()
    ok_files = all(os.path.isfile(os.path.join(base, *p)) for p in (
        ("webui", "js", "pages", "routing.js"),
        ("webui", "clip_pop.html"),
        ("webui", "js", "clip_pop.js"),
        ("webui", "js", "pages", "tools.js"),
    ))
    return ("clash.js %.1f KB / 单页 id=clash；routing/clip_pop%s" %
            (len(txt) / 1024.0, " 已随包" if ok_files else " 缺失！"))


def selftest_if_requested():
    """开发自检：LOCALTOOLBOX_SELFTEST=1 时验证打包资源后退出（写 %TEMP% 结果）。"""
    import json
    import tempfile

    if not os.environ.get("LOCALTOOLBOX_SELFTEST"):
        return False
    out = {}

    def chk(name, fn):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = "ERR: %s" % e

    chk("cv2", lambda: __import__("cv2").__version__)
    chk("numpy", lambda: __import__("numpy").__version__)
    chk("comtypes", lambda: __import__("comtypes").__version__)
    chk("pycaw", lambda: bool(__import__("pycaw.api.audioclient",
                                        fromlist=["IAudioClient"]).IAudioClient))
    chk("ffmpeg", lambda: os.path.isfile(
        __import__("imageio_ffmpeg").get_ffmpeg_exe()))
    chk("screen_monitors", lambda: len(__import__(
        "app.core.screen", fromlist=["monitors"]).monitors()) > 0)
    chk("ocr", _ocr_probe)
    # Clash（mihomo）：PyYAML、配置生成、架构选包、桥接方法、前端资源
    chk("yaml", lambda: __import__("yaml").__version__)
    chk("clash_config", _clash_config_probe)
    chk("arch_pick", _arch_pick_probe)
    chk("clash_bridge", lambda: "OK" if all(
        hasattr(__import__("app.bridge.clash_api", fromlist=["ClashApi"]).ClashApi, n)
        for n in ("clash_rules", "clash_save_rules", "clash_log_start",
                  "clash_log_stop", "clash_set_log_level", "_apply_sub_interval"))
        else "缺少 clash_* 方法")
    chk("webui_clash", _webui_clash_probe)
    path = os.path.join(tempfile.gettempdir(), "localtoolbox_selftest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return True


def main():
    # DPI 感知须最先声明（任何窗口/坐标调用之前）
    enable_dpi_awareness()
    if selftest_if_requested():
        os._exit(0)
    # 单实例：已有实例运行则提示并退出。
    # 提权重启（--relaunch-admin）的子进程等待旧实例释放锁（最长 15s）。
    relaunch = "--relaunch-admin" in sys.argv[1:]
    acquire_or_exit(retry_seconds=15 if relaunch else 0)

    # 日志初始化（尽早：后续 get_logger 才有后端可写）
    applog.init_logging(DATA_HOME, level="INFO")
    install_fatal_hook()
    try:
        from app.core.discovery import local_hostname

        applog.set_user(local_hostname())
    except Exception:
        applog.set_user(APP_TITLE)
    log = applog.get_logger("main")
    log.info("应用启动 %s", APP_TITLE)

    action, path = parse_cli(sys.argv[1:])
    bridge = Bridge()
    # 无边框 + 无 DWM 玻璃边：避免浅色系统主题给深色应用套白色标题栏/边框
    # v3.5d：配置「启动时最小化到托盘」→ hidden 创建（托盘图标照常可用）
    # v3.5e：配置「主窗口置顶」→ on_top 创建；v3.5f：记住窗口大小与位置
    #
    # easy_drag=True（保持默认，显式写出以明示约定）：无边框窗口的拖动由它
    # 提供——按住任意区域移动窗口。画布绘制、输入框选择文本等交互元素会被
    # webui/js/window_drag.js 在 document 冒泡阶段拦截，easy_drag 收不到
    # mousedown，因此不会劫持绘制/选择；标题栏、卡片空白、页面背景照旧拖动
    # 整窗。（自绘标题栏原走的 win_begin_drag/WM_NCLBUTTONDOWN 在 WebView2
    # 上因鼠标捕获不生效而拖不动窗口，已弃用。）
    geo_kw = _restore_geometry(bridge.cfg)
    window = webview.create_window(
        APP_TITLE,
        ui_index(),
        js_api=bridge,
        width=geo_kw.get("width", 1320),
        height=geo_kw.get("height", 860),
        x=geo_kw.get("x"),
        y=geo_kw.get("y"),
        frameless=True,
        shadow=False,
        easy_drag=True,
        hidden=bool(bridge.cfg.get("start_minimized", False)),
        on_top=bool(bridge.cfg.get("win_on_top", False)),
        background_color="#0f1419",
    )
    bridge.attach(window)
    # v4.6：第二实例启动时通过命名事件唤起本实例主窗口（单实例锁拒启场景）
    start_show_watcher(lambda: (window.show(), window.restore()))
    if action and path:
        bridge.set_pending_action(action, path)

    # 托盘常驻：关闭窗口时隐藏到托盘，托盘菜单「退出程序」才真正退出
    tray = TrayController(
        on_show=lambda: window.show(),
        on_quit=lambda: window.destroy(),
        title=APP_TITLE,
    )
    # v3.2：OpenList 异常退出 → 托盘通知；v3.5d：托盘通知总开关（关闭后静默）
    def _tray_notify(title, msg):
        if bridge.cfg.get("tray_notify", True):
            tray.notify(msg, title)

    bridge.set_tray_notify(_tray_notify)
    # v3.5c：关闭按钮「退出程序」选项 → 走托盘统一退出链路（quitting 置位）
    bridge.set_tray_quit(tray._quit)
    tray.start()

    def on_closing():
        # v3.5f：记住窗口几何（隐藏/退出前窗口仍在，坐标为当前值）
        if bridge.cfg.get("win_remember", False):
            try:
                bridge.cfg.set("win_geometry", {
                    "x": window.x, "y": window.y,
                    "width": window.width, "height": window.height,
                })
            except Exception:
                pass
        if tray.quitting:
            log.info("托盘触发退出，进程退出")
            return True  # 托盘触发的退出：放行关闭
        # v3.5d：配置「关闭按钮直接退出」→ 放行关闭（webview.start 返回后走统一收尾）
        if bridge.cfg.get("tray_close_exit", False):
            log.info("配置关闭按钮直接退出，进程退出")
            return True
        window.hide()
        _tray_notify(APP_TITLE, "程序仍在后台运行。\n右键托盘图标可选择「退出程序」。")
        return False  # 阻止关闭，最小化到托盘

    window.events.closing += on_closing

    webview.start()
    bridge.shutdown()
    tray.stop()
    applog.get_logger("main").info("应用退出")
    applog.stop_logging()


if __name__ == "__main__":
    main()
