"""截图核心：全屏/活动窗口捕获、保存（模板命名+日期子目录+重名序号）、图片剪贴板、历史列表。

依赖 Pillow（ImageGrab）。活动窗口截图经 ctypes 获取前台窗口矩形，随后区域捕获，
无需 pywin32。剪贴板写入使用原生 Win32 CF_DIB，不依赖第三方包。
"""

import base64
import ctypes
import ctypes.wintypes
import io
import os
import re
import struct
from datetime import datetime

from PIL import Image, ImageGrab

from . import logger as applog

log = applog.get_logger("screenshot")

DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "LocalToolbox")


def capture_full_png(all_screens=True):
    """全屏捕获：默认整个虚拟桌面（多显示器合并，与录屏一致）；all_screens=False 仅主屏。"""
    img = ImageGrab.grab(all_screens=all_screens)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _window_rect(hwnd):
    """窗口物理矩形 (x, y, w, h)：优先 DWM 扩展边界（去除 Win10/11 不可见
    阴影边），失败回退 GetWindowRect；进程 DPI Aware 时均为物理像素。"""
    user32 = ctypes.windll.user32
    rect = ctypes.wintypes.RECT()
    try:
        # 9 = DWMWA_EXTENDED_FRAME_BOUNDS（物理像素，不含阴影）
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.wintypes.HWND(hwnd), ctypes.wintypes.DWORD(9),
            ctypes.byref(rect), ctypes.sizeof(rect))
    except Exception:
        hr = -1
    if hr != 0:
        user32.GetWindowRect.argtypes = [
            ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    return rect.left, rect.top, w, h


def _foreground_rect():
    """前台窗口物理矩形；失败返回 None（含最小化窗口）。"""
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
    return _window_rect(user32.GetForegroundWindow())



def window_rect_at(sx, sy, skip=None):
    """屏幕物理坐标 (sx, sy) 处顶层窗口的物理矩形（窗口吸附圈选用）。

    EnumWindows 自 Z 序最顶可见窗口向下查找首个包含该坐标的窗口 →
    DWM 扩展边界。跳过 skip 集合中的句柄（如全屏截图遮罩自身，否则
    顶层遮罩会拦下所有命中把屏幕当成“窗口”）以及桌面背景窗口
    （Progman/WorkerW，空白桌面回退自由圈选）。不可识别返回 None。
    """
    skip = set(skip or ())
    user32 = ctypes.windll.user32
    found = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _enum_cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if hwnd in skip:
            return True
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buf, len(buf))
        cls = buf.value or ""
        if cls == "Progman" or cls.startswith("WorkerW"):
            return True
        r = _window_rect(hwnd)
        if r is None:
            return True
        x, y, w, h = r
        if x <= sx < x + w and y <= sy < y + h:
            found["rect"] = r
            return False
        return True

    user32.EnumWindows(_enum_cb, 0)
    return found.get("rect")


def capture_window_png():
    """捕获当前活动窗口（DWM 物理边界，去阴影），失败返回 None。"""
    rect = _foreground_rect()
    if not rect:
        return None
    x, y, w, h = rect
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def png_data_url(png_bytes):
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def thumbnail_data_url(png_bytes, max_w=160):
    """生成小尺寸缩略图 data URL（剪贴板图片历史缩略图等场景）。"""
    img = Image.open(io.BytesIO(png_bytes))
    img.thumbnail((max_w, max_w))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return png_data_url(buf.getvalue())


def decode_data_url(data_url):
    """把 data URL 解码回 PNG 字节。"""
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    return base64.b64decode(data_url)


def _unique_path(directory, basename):
    """重名自动追加序号：name.png → name_001.png …"""
    path = os.path.join(directory, basename)
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(basename)
    i = 1
    while True:
        cand = os.path.join(directory, "%s_%03d%s" % (stem, i, ext))
        if not os.path.exists(cand):
            return cand
        i += 1


def _safe_label(label):
    """清洗文件名标签：去除 Windows 非法字符并截断。"""
    label = str(label or "截图").strip() or "截图"
    label = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", label)
    return label[:24] or "截图"


def save_png(png_bytes, shot_dir=None, label="截图"):
    """保存截图：``{日期}_{时间}_{类型}_{序号}.png``，按 YYYY/MM/DD 建子目录。返回路径。"""
    shot_dir = str(shot_dir or "").strip() or DEFAULT_DIR
    now = datetime.now()
    sub = os.path.join(shot_dir,
                       "%04d" % now.year, "%02d" % now.month, "%02d" % now.day)
    os.makedirs(sub, exist_ok=True)
    name = "%s_%s_%s.png" % (
        now.strftime("%Y-%m-%d"), now.strftime("%H-%M-%S"), _safe_label(label))
    path = _unique_path(sub, name)
    with open(path, "wb") as f:
        f.write(png_bytes)
    log.info("截图已保存：%s", path)
    return path


def recording_path(shot_dir=None, ext="gif"):
    """录制成品路径：日期子目录 + ``{日期}_{时间}_rec.{ext}`` + 重名序号。"""
    shot_dir = str(shot_dir or "").strip() or DEFAULT_DIR
    now = datetime.now()
    sub = os.path.join(shot_dir,
                       "%04d" % now.year, "%02d" % now.month, "%02d" % now.day)
    os.makedirs(sub, exist_ok=True)
    prefix = "%s_%s_rec" % (now.strftime("%Y-%m-%d"), now.strftime("%H-%M-%S"))
    return _unique_path(sub, prefix + "." + ext)


def save_frames(shot_dir, frames, fps):
    """保存录制帧序列：>=2 帧且首帧 PNG 在 1.4MB 内 → GIF，否则退化单张 PNG。

    沿用 save_png 的日期子目录/重名策略，文件名 ``{日期}_{时间}_rec.{gif|png}``。
    frames 为 PIL Image 列表；fps 决定 GIF duration（最小 50ms/帧）。返回保存路径。
    """
    shot_dir = str(shot_dir or "").strip() or DEFAULT_DIR
    now = datetime.now()
    sub = os.path.join(shot_dir,
                       "%04d" % now.year, "%02d" % now.month, "%02d" % now.day)
    os.makedirs(sub, exist_ok=True)
    prefix = "%s_%s_rec" % (now.strftime("%Y-%m-%d"), now.strftime("%H-%M-%S"))

    buf = io.BytesIO()
    frames[0].save(buf, format="PNG")
    first_size = len(buf.getvalue())
    if len(frames) < 2 or first_size > int(1.4 * 1024 * 1024):
        path = _unique_path(sub, prefix + ".png")
        with open(path, "wb") as f:
            f.write(buf.getvalue())
        log.info("录制已存为单帧 PNG：%s", path)
        return path

    path = _unique_path(sub, prefix + ".gif")
    ms = max(50, int(1000.0 / fps))
    # disposal=1（不处置）：各帧为不透明全幅画面，逐帧完全覆盖，无需逐帧处置模式
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=ms,
        loop=0,
        optimize=True,
        disposal=1,
    )
    log.info("录制已存为 GIF：%s（%d 帧，%d ms/帧）", path, len(frames), ms)
    return path


def list_history(shot_dir=None, thumb=True):
    """递归列出截图历史，按修改时间倒序：[{path, name, dir, time, size, thumb?}]。

    thumb=True 时附 96px 缩略图 data URL（历史列表目视识别用；读取失败省略字段）。
    """
    shot_dir = str(shot_dir or "").strip() or DEFAULT_DIR
    entries = []
    if not os.path.isdir(shot_dir):
        return entries
    for root, dirs, files in os.walk(shot_dir):
        dirs[:] = sorted(dirs)
        for name in sorted(files):
            if not name.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp")):
                continue
            full = os.path.join(root, name)
            try:
                st = os.stat(full)
                entry = {
                    "path": full,
                    "name": name,
                    "dir": root,
                    "time": st.st_mtime,
                    "size": st.st_size,
                }
                if thumb:
                    try:
                        with open(full, "rb") as f:
                            entry["thumb"] = thumbnail_data_url(f.read(), 96)
                    except Exception:
                        pass
                entries.append(entry)
            except OSError:
                continue
    entries.sort(key=lambda e: e["time"], reverse=True)
    return entries


# -- 剪贴板（图片） ---------------------------------------------------


def _png_to_dib(png_bytes):
    """PNG → Win32 DIB（BITMAPINFOHEADER + BGRA 像素，自下而上）。"""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    w, h = img.size
    flipped = img.transpose(Image.FLIP_TOP_BOTTOM)
    raw = flipped.tobytes("raw", "BGRA")
    header = struct.pack("<LiiHHIIiiII",
                         40, w, h, 1, 32, 0, len(raw), 0, 0, 0, 0)
    return header + raw


def _dib_to_png(dib):
    """Win32 DIB（CF_DIB，32bpp BGRA）→ PNG 字节；解析失败抛 ValueError。

    从剪贴板读图路径：biHeight<0 表示自上而下（部分应用如此写入）。
    """
    if len(dib) < 40:
        raise ValueError("DIB 数据过短")
    w, h, _planes, bpp = struct.unpack("<iiHH", dib[4:16])
    if w <= 0 or w > 65535 or abs(h) > 65535 or bpp not in (32,):
        raise ValueError("不支持的 DIB 格式：%dx%d %dbpp" % (w, abs(h), bpp))
    top_down = h < 0
    h = abs(h)
    offset = 40
    need = w * h * 4
    raw = dib[offset:offset + need]
    if len(raw) < need:
        raise ValueError("DIB 像素数据不足")
    stride = w * 4
    rows = [raw[y * stride:(y + 1) * stride] for y in range(h)]
    if not top_down:
        rows.reverse()          # 自下而上存储 → 翻转
    img = Image.frombuffer("RGBA", (w, h), b"".join(rows), "raw", "BGRA", 0, 1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def read_clipboard_png():
    """读取系统剪贴板图片（CF_DIB）→ PNG 字节；无图/失败返回 None。"""
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    HGLOBAL = ctypes.c_void_p
    user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
    user32.OpenClipboard.restype = ctypes.wintypes.BOOL
    user32.CloseClipboard.restype = ctypes.wintypes.BOOL
    user32.GetClipboardData.restype = HGLOBAL
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalSize.argtypes = [HGLOBAL]

    if not user32.OpenClipboard(None):
        return None
    try:
        h = user32.GetClipboardData(8)  # CF_DIB
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        if not p:
            return None
        try:
            size = kernel32.GlobalSize(h)
            dib = ctypes.string_at(p, size)
        finally:
            kernel32.GlobalUnlock(h)
        try:
            return _dib_to_png(dib)
        except (ValueError, struct.error):
            log.warning("剪贴板 DIB 解析失败")
            return None
    finally:
        user32.CloseClipboard()


def copy_png_to_clipboard(png_bytes):
    """把 PNG 图片写入系统剪贴板（Win32 CF_DIB=8）。返回是否成功。"""
    try:
        dib = _png_to_dib(png_bytes)
    except Exception as e:
        log.warning("生成 DIB 失败：%s", e)
        return False
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    GMEM_MOVEABLE = 0x0002
    HGLOBAL = ctypes.c_void_p
    kernel32.GlobalAlloc.restype = HGLOBAL
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [HGLOBAL]
    kernel32.GlobalFree.restype = HGLOBAL
    kernel32.GlobalFree.argtypes = [HGLOBAL]
    user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
    user32.EmptyClipboard.restype = ctypes.wintypes.BOOL
    user32.SetClipboardData.restype = HGLOBAL
    user32.SetClipboardData.argtypes = [ctypes.c_uint, HGLOBAL]
    user32.CloseClipboard.restype = ctypes.wintypes.BOOL

    h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
    if not h_mem:
        return False
    try:
        p = kernel32.GlobalLock(h_mem)
        if not p:
            raise ctypes.WinError()
        try:
            ctypes.memmove(p, dib, len(dib))
        finally:
            kernel32.GlobalUnlock(h_mem)
        if not user32.OpenClipboard(None):
            raise ctypes.WinError()
        try:
            user32.EmptyClipboard()
            ok = bool(user32.SetClipboardData(8, h_mem))  # CF_DIB
        finally:
            user32.CloseClipboard()
        if ok:
            return True  # 成功后句柄归系统管理
        raise ctypes.WinError()  # SetClipboardData 失败：句柄未被接管
    except Exception as e:
        kernel32.GlobalFree(h_mem)
        log.warning("写入剪贴板失败：%s", e)
        return False