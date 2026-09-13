"""主窗口参数工厂：生产与测试共用同一份窗口形态配置。

为什么单独抽出来：`tests/test_ui_smoke.py` 此前自己拼 `create_window` 参数，
只传了 width/height/js_api —— 也就是说**冒烟测试从来没测过生产窗口的形态**
（frameless / shadow / easy_drag / background_color）。而窗口形态直接决定标题栏
高度、拖动行为与 DPI 换算，是最容易出回归的地方。

约定：所有新增的窗口能力（材质透明、圆角等）都必须经此函数下发，
测试与生产才会拿到同一套形态。
"""

from . import win_shell

MATERIAL_MODES = ("auto", "off", "blur", "mica")
BASE_BG = "#0f1419"   # 不透明状态下的窗口底色（启动瞬间可见，与 --bg 同色）


def material_mode(cfg):
    """读配置并归一化材质模式（非法值回退 auto）。"""
    m = str(cfg.get("ui_material") or "auto").strip().lower()
    return m if m in MATERIAL_MODES else "auto"


def wants_transparent(cfg, cap=None):
    """是否需要"透明窗口"—— 材质生效的前提。

    **只在探测确认支持时才透明**：一旦透明却没有材质（DWM 调用失败、远程会话、
    显卡驱动异常），窗口会直接透出桌面，比不透明难看得多。所以探测不通过一律
    保持不透明，观感与改造前完全一致。
    """
    if material_mode(cfg) == "off":
        return False
    cap = cap if cap is not None else win_shell.probe()
    return bool(cap.get("mica") or cap.get("blur"))


def window_kwargs(cfg, geo=None, overrides=None):
    """返回 `webview.create_window(...)` 的关键字参数。

    **透明决策只在这里做**：生产、UI 冒烟测试、布局审计三处共用，避免"测试窗口
    与生产窗口形态不同"的老问题。透明时顺带把 hidden 置真（避免页面内容就绪前
    就显示一个透明窗口），由调用方在 loaded 后再 show。

    geo: main._restore_geometry(cfg) 的结果（宽/高/坐标）；缺省用默认尺寸。
    overrides: 测试用覆盖（如 {"hidden": False} 让测试窗口可见）。
    """
    geo = geo or {}
    transparent = wants_transparent(cfg)
    kw = {
        "width": geo.get("width", 1320),
        "height": geo.get("height", 860),
        "x": geo.get("x"),
        "y": geo.get("y"),
        "frameless": True,
        "shadow": False,
        "easy_drag": True,
        "hidden": bool(cfg.get("start_minimized", False)) or transparent,
        "on_top": bool(cfg.get("win_on_top", False)),
        "background_color": BASE_BG,
    }
    if transparent:
        kw["transparent"] = True
    if overrides:
        kw.update(overrides)
    return kw
