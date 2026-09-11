"""屏幕与显示器助手：虚拟桌面范围、光标所在显示器、DPI、多显示器截图。

截图底层仍用 Pillow ImageGrab（本模块只做几何/坐标换算）：
- ImageGrab.grab(all_screens=True)：合并整个虚拟桌面为一张图（含负坐标显示器，
  图的左上角对应虚拟桌面左上角 minX,minY）；
- ImageGrab.grab(bbox=物理矩形)：按指定显示器/区域截取（Windows 虚拟坐标可含负值）。
"""

import ctypes
from ctypes import wintypes

from PIL import ImageGrab

MDT_EFFECTIVE_DPI = 0
MONITOR_DEFAULTTONEAREST = 2

_user32 = ctypes.windll.user32
_shcore = ctypes.windll.shcore


class _RECT(wintypes.RECT):
    pass


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


def _monitor_dpi(hmon):
    """显示器 DPI（MDT_EFFECTIVE_DPI）。取不到返回 96。"""
    try:
        x = wintypes.UINT()
        y = wintypes.UINT()
        _shcore.GetDpiForMonitor.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                             ctypes.POINTER(wintypes.UINT),
                                             ctypes.POINTER(wintypes.UINT)]
        if _shcore.GetDpiForMonitor(hmon, MDT_EFFECTIVE_DPI,
                                    ctypes.byref(x), ctypes.byref(y)) == 0:
            return int(x.value) or 96
    except Exception:
        pass
    return 96


def _collect():
    """枚举显示器：{hMonitor: MonitorInfo}（rcMonitor 为虚拟桌面坐标，可含负值）。"""
    out = {}

    def cb(hmon, hdc, lprc, lparam):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            out[int(hmon)] = {
                "left": int(r.left), "top": int(r.top),
                "width": int(r.right - r.left), "height": int(r.bottom - r.top),
                "dpi": _monitor_dpi(hmon),
                "primary": bool(info.dwFlags & 1),
                "name": info.szDevice,
            }
        return 1

    MonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_int,
                                         ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.POINTER(_RECT), wintypes.LPARAM)
    _user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p,
                                            MonitorEnumProc, wintypes.LPARAM]
    _user32.EnumDisplayMonitors(None, None, MonitorEnumProc(cb), 0)
    return out


def monitors():
    """显示器列表（虚拟桌面坐标）。"""
    return list(_collect().values())


def virtual_bounds():
    """整个虚拟桌面边界：{left, top, width, height}（left/top 可为负）。"""
    ms = _collect().values()
    if not ms:
        return {"left": 0, "top": 0, "width": 0, "height": 0}
    left = min(m["left"] for m in ms)
    top = min(m["top"] for m in ms)
    right = max(m["left"] + m["width"] for m in ms)
    bottom = max(m["top"] + m["height"] for m in ms)
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def cursor_point():
    p = _POINT()
    if not _user32.GetCursorPos(ctypes.byref(p)):
        return None
    return p.x, p.y


def monitor_at(x=None, y=None):
    """光标所在（或指定点所在）显示器；失败返回主显示器或 None。"""
    if x is None or y is None:
        pt = cursor_point()
        if pt:
            x, y = pt
    coll = _collect()
    if not coll:
        return None
    if x is None or y is None:
        prim = [m for m in coll.values() if m["primary"]]
        return (prim or list(coll.values()))[0]
    hmon = None
    try:
        _user32.MonitorFromPoint.argtypes = [_POINT, wintypes.DWORD]
        _user32.MonitorFromPoint.restype = wintypes.HANDLE
        hmon = _user32.MonitorFromPoint(_POINT(x, y), MONITOR_DEFAULTTONEAREST)
    except Exception:
        hmon = None
    if hmon is not None and int(hmon) in coll:
        return coll[int(hmon)]
    return list(coll.values())[0]


def capture_rect(left, top, width, height):
    """(保留接口) 按物理坐标截取矩形（仅供无 DPI 缩放的 100% 场景使用）。"""
    return ImageGrab.grab(bbox=(int(left), int(top),
                                int(left + width), int(top + height)))


def capture_virtual():
    """截取整个虚拟桌面（多显示器合并为一张图，物理像素）。"""
    return ImageGrab.grab(all_screens=True)


def _phys_scale(merged=None):
    """逻辑虚拟桌面尺寸 → 物理像素换算系数（DPI 非 100% 时 >1）。

    进程非 DPI 感知时 EnumDisplayMonitors 返回逻辑单位，ImageGrab 返回物理像素；
    以整屏合并图的像素数 / 逻辑边界宽推算每轴系数（多显示器混用 DPI 时近似）。
    """
    vb = virtual_bounds()
    if merged is None:
        merged = ImageGrab.grab(all_screens=True)
    mw, mh = merged.size
    sx = mw / vb["width"] if vb["width"] else 1.0
    sy = mh / vb["height"] if vb["height"] else 1.0
    return sx, sy


def monitor_phys_box(mon):
    """显示器在整屏合并图（物理像素）中的裁剪盒 (x, y, w, h)。"""
    vb = virtual_bounds()
    sx, sy = _phys_scale()
    x = int(round((mon["left"] - vb["left"]) * sx))
    y = int(round((mon["top"] - vb["top"]) * sy))
    return (x, y,
            int(round(mon["width"] * sx)),
            int(round(mon["height"] * sy)))


def capture_monitor(mon, merged=None):
    """截取指定显示器物理像素（从整屏合并图裁剪，保证任意 DPI/排列正确）。"""
    if merged is None:
        merged = ImageGrab.grab(all_screens=True)
    x, y, w, h = monitor_phys_box(mon)
    return merged.crop((x, y, x + w, y + h))
