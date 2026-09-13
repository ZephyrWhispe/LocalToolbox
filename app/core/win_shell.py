"""窗口外壳能力：材质（Win10 模糊 / Win11 Mica）、圆角、系统暗色、窗口句柄。

平台策略（按"Win10 为主、Win11 适配"）：
- **Windows 10**：`DWMWA_SYSTEMBACKDROP_TYPE`(38) 不存在（Win11 22621+ 才有），
  走 `SetWindowCompositionAttribute` + `ACCENT_ENABLE_BLURBEHIND`（Win7+ 均支持，
  Win10 表现稳定；实测 Acrylic(4) 在 Win10 拖动窗口时明显掉帧，故默认不用）。
- **Windows 11**：走 `DWMWA_SYSTEMBACKDROP_TYPE` = Mica(2) / Acrylic(3)，
  圆角交给系统默认（`DWMWCP_DEFAULT` 在 Win11 已是圆角，无需强制）。

**失败一律降级为不透明**，并通过返回值报告可显示的原因（前端设置页会展示
"当前材质：Mica / 模糊 / 无（原因）"）。所有调用幂等、可重复执行。

设计约束：本模块不导入 webview，只依赖 ctypes + 系统信息，便于在测试中直接调用。
"""

import ctypes
import sys
from ctypes import wintypes

MATERIAL_AUTO, MATERIAL_OFF, MATERIAL_BLUR, MATERIAL_MICA = "auto", "off", "blur", "mica"

# DWM 属性
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_NONE, DWMSBT_MAINWINDOW, DWMSBT_TRANSIENTWINDOW = 1, 2, 3
DWMWCP_ROUND = 2

# SetWindowCompositionAttribute（未公开 API，Win7+ 可用）
WCA_ACCENT_POLICY = 19
ACCENT_DISABLED, ACCENT_ENABLE_BLURBEHIND, ACCENT_ENABLE_ACRYLICBLURBEHIND = 0, 3, 4

SM_REMOTESESSION = 0x1000

# 深浅色底色的 ABGR 值（注意 GradientColor 是 0xAABBGGRR，不是 ARGB）
# alpha 取 0xF0（94% 不透明）：即使 DWM 模糊未生效，画面上也只是"接近不透明"，
# 不会出现透出桌面的破相 —— 这是本模块的兜底安全线。
_TINT_DARK = 0xF0_19_14_0F      # #0f1419
_TINT_LIGHT = 0xF0_F9_F5_F2     # #f2f5f9

_state = {"applied": "none", "mode": "auto", "reason": "尚未应用", "hwnd": 0}


class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class _WCADATA(ctypes.Structure):
    _fields_ = [("Attrib", ctypes.c_int), ("pvData", ctypes.c_void_p),
                ("cbData", ctypes.c_size_t)]


def hwnd_of(window):
    """取窗口 HWND。取值范式与 app/bridge/win_api.py:80-82 保持一致。"""
    try:
        native = getattr(window, "native", None)
        h = getattr(native, "Handle", None)
        if not h:
            return 0
        return h if isinstance(h, int) else int(h.ToInt64())
    except Exception:
        return 0


def is_remote_session():
    """远程桌面会话：模糊无意义且会掉帧，直接不启用。"""
    try:
        return bool(ctypes.windll.user32.GetSystemMetrics(SM_REMOTESESSION))
    except Exception:
        return False


def build_no():
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        return 0


def _dwm_set(hwnd, attr, value):
    """DwmSetWindowAttribute，返回 HRESULT 是否为 0（0 = 成功）。"""
    try:
        fn = ctypes.windll.dwmapi.DwmSetWindowAttribute
        fn.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        fn.restype = ctypes.c_long
        v = ctypes.c_int(value)
        return fn(wintypes.HWND(hwnd), attr, ctypes.byref(v), ctypes.sizeof(v)) == 0
    except Exception:
        return False


def _accent(hwnd, state, tint=0):
    """SetWindowCompositionAttribute + ACCENT_POLICY（Win10 模糊通道）。"""
    try:
        fn = ctypes.windll.user32.SetWindowCompositionAttribute
        fn.argtypes = [wintypes.HWND, ctypes.POINTER(_WCADATA)]
        fn.restype = wintypes.BOOL
        policy = _ACCENT_POLICY(state, 2, tint, 0)
        data = _WCADATA(WCA_ACCENT_POLICY, ctypes.cast(
            ctypes.pointer(policy), ctypes.c_void_p), ctypes.sizeof(policy))
        return bool(fn(wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception:
        return False


def probe(hwnd=0):
    """探测本机可用的材质能力。

    hwnd=0（窗口尚未创建）时只按系统版本与远程会话判断；
    传入真实 HWND 时额外用 DwmSetWindowAttribute 的返回码验证 attr 38 是否真的
    被系统接受 —— 这比版本号判断可靠（覆盖 Win11 早期 build 与精简系统）。
    """
    build = build_no()
    cap = {"build": build, "mica": False, "blur": False,
           "round_corner": False, "remote": is_remote_session(), "reason": ""}
    if cap["remote"]:
        cap["reason"] = "远程桌面会话"
        return cap
    if build >= 10240:
        cap["blur"] = True                      # Win10/11 的 BlurBehind 均可用
    if build >= 22621:
        cap["round_corner"] = True
        if hwnd:
            cap["mica"] = _dwm_set(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_NONE)
        else:
            cap["mica"] = True                  # 版本满足；真机由 apply() 复核
    return cap


def apply(hwnd, mode="auto", dark=True):
    """幂等应用材质，返回 {"applied": "mica|blur|none", "mode": ..., "reason": ...}。"""
    if not hwnd:
        out = {"applied": "none", "mode": mode, "reason": "窗口尚未就绪"}
        _state.update(out)
        return out

    cap = probe(hwnd)
    want = str(mode or "auto").lower()
    reason = ""
    if want == MATERIAL_AUTO:
        want = MATERIAL_MICA if cap["mica"] else (MATERIAL_BLUR if cap["blur"] else MATERIAL_OFF)
    if want == MATERIAL_MICA and not cap["mica"]:
        want, reason = (MATERIAL_BLUR if cap["blur"] else MATERIAL_OFF), "系统不支持 Mica"
    if want == MATERIAL_BLUR and not cap["blur"]:
        want, reason = MATERIAL_OFF, "系统不支持模糊"

    if want == MATERIAL_MICA:
        ok = _dwm_set(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_MAINWINDOW)
        _dwm_set(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)
        _dwm_set(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
        out = {"applied": "mica" if ok else "none", "mode": mode,
               "reason": reason or ("" if ok else "DWM Mica 调用失败")}
    elif want == MATERIAL_BLUR:
        ok = _accent(hwnd, ACCENT_ENABLE_BLURBEHIND,
                     _TINT_DARK if dark else _TINT_LIGHT)
        out = {"applied": "blur" if ok else "none", "mode": mode,
               "reason": reason or ("" if ok else "模糊调用失败（系统可能不支持）")}
    else:
        clear(hwnd)
        out = {"applied": "none", "mode": mode, "reason": reason or "已关闭材质"}

    out["build"] = cap["build"]
    _state.update(out)
    _state["hwnd"] = hwnd
    return out


def clear(hwnd):
    """关闭材质：撤销模糊并把窗口底色还原为不透明。"""
    if not hwnd:
        return False
    _accent(hwnd, ACCENT_DISABLED, 0)
    _dwm_set(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_NONE)
    _state.update({"applied": "none", "reason": "已关闭材质"})
    return True


def state():
    """最近一次应用结果（供 cap_get / 设置页显示"当前材质 + 原因"）。"""
    return dict(_state)
