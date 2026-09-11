"""滚动截图核心：自动/手动滚动 + 模板匹配拼接。"""

import ctypes
import ctypes.wintypes
import io
import threading
import time

from PIL import Image

user32 = ctypes.windll.user32

WM_VSCROLL = 0x0115
SB_LINEDOWN = 1
SB_PAGEDOWN = 3
GWL_HWNDPARENT = -8


def find_scrollable_window():
    """返回当前鼠标下方的窗口句柄（用于用户选择目标窗口）。"""
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return user32.WindowFromPoint(pt)


def scroll_and_capture(hwnd, step=50, max_scrolls=40, delay=0.15):
    """自动滚动截图：循环发送 WM_VSCROLL 并截取窗口图像，返回列表。"""
    frames = []
    for _ in range(max_scrolls):
        img = _capture_window(hwnd)
        if img is None:
            break
        frames.append(img)
        user32.SendMessageW(hwnd, WM_VSCROLL, SB_LINEDOWN, 0)
        time.sleep(delay)
    return frames


def _capture_window(hwnd):
    """截取指定窗口客户区图像，返回 PIL Image 或 None。
    
    尝试多种捕获方法以兼容不同应用类型：
    1. PrintWindow（支持 DirectX/Electron/UWP）
    2. GDI BitBlt（传统方法，快速但兼容性差）
    """
    try:
        rect = ctypes.wintypes.RECT()
        if user32.GetClientRect(hwnd, ctypes.byref(rect)) == 0:
            return None
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return None
        
        # 方法1：尝试 PrintWindow（PW_RENDERFULLCONTENT = 0x00000002）
        # 这个方法能捕获 DirectX、GPU 加速内容
        try:
            img = _capture_via_printwindow(hwnd, w, h)
            if img is not None:
                return img
        except Exception:
            pass
        
        # 方法2：回退到 GDI BitBlt
        return _capture_via_gdi(hwnd, w, h)
    except Exception:
        return None


def _capture_via_printwindow(hwnd, w, h):
    """使用 PrintWindow API 捕获窗口（支持 DirectX/GPU 加速内容）。"""
    PW_RENDERFULLCONTENT = 0x00000002
    
    hdc_screen = user32.GetDC(0)
    if not hdc_screen:
        return None
    
    mem_dc = ctypes.windll.gdi32.CreateCompatibleDC(hdc_screen)
    bitmap = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    old_bitmap = ctypes.windll.gdi32.SelectObject(mem_dc, bitmap)
    
    # 调用 PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
    result = ctypes.windll.user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
    
    ctypes.windll.gdi32.SelectObject(mem_dc, old_bitmap)
    ctypes.windll.gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(0, hdc_screen)
    
    if result == 0:
        return None
    
    # 将位图转换为 PIL Image
    bmp_info = ctypes.wintypes.BITMAPINFO()
    bmp_info.bmiHeader.biSize = ctypes.sizeof(bmp_info.bmiHeader)
    bmp_info.bmiHeader.biWidth = w
    bmp_info.bmiHeader.biHeight = -h  # 负数表示从上到下
    bmp_info.bmiHeader.biPlanes = 1
    bmp_info.bmiHeader.biBitCount = 32
    bmp_info.bmiHeader.biCompression = 0
    
    buf = ctypes.create_string_buffer(w * h * 4)
    hdc_temp = user32.GetDC(0)
    ctypes.windll.gdi32.GetDIBits(
        hdc_temp, bitmap,
        0, h, buf, ctypes.byref(bmp_info), 0)
    user32.ReleaseDC(0, hdc_temp)
    ctypes.windll.gdi32.DeleteObject(bitmap)
    
    from PIL import Image
    img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
    return img


def _capture_via_gdi(hwnd, w, h):
    """使用 GDI BitBlt 捕获窗口（传统方法，速度快但兼容性差）。"""
    hdc = user32.GetDC(hwnd)
    if not hdc:
        return None
    mem_dc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
    bitmap = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = ctypes.windll.gdi32.SelectObject(mem_dc, bitmap)
    ctypes.windll.gdi32.BitBlt(mem_dc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)
    ctypes.windll.gdi32.SelectObject(mem_dc, old)
    ctypes.windll.gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, hdc)

    from PIL import Image
    bmp_info = ctypes.wintypes.BITMAPINFO()
    bmp_info.bmiHeader.biSize = ctypes.sizeof(bmp_info.bmiHeader)
    bmp_info.bmiHeader.biWidth = w
    bmp_info.bmiHeader.biHeight = -h
    bmp_info.bmiHeader.biPlanes = 1
    bmp_info.bmiHeader.biBitCount = 32
    bmp_info.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    ctypes.windll.gdi32.GetDIBits(
        hdc if hdc else user32.GetDC(0), bitmap,
        0, h, buf, ctypes.byref(bmp_info), 0)
    ctypes.windll.gdi32.DeleteObject(bitmap)

    img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
    return img


def stitch_vertical(frames, overlap=60):
    """使用模板匹配在重叠区域对齐并拼接图像列表。"""
    if not frames:
        return None
    if len(frames) == 1:
        return frames[0]
    try:
        import cv2
        import numpy as np
    except ImportError:
        return _stitch_simple(frames)

    result = frames[0]
    for i in range(1, len(frames)):
        prev_arr = np.array(result.convert("RGB"))
        curr_arr = np.array(frames[i].convert("RGB"))
        strip_h = min(overlap, prev_arr.shape[0] // 4, curr_arr.shape[0] // 4)
        if strip_h < 10:
            result = _concat_images(result, frames[i])
            continue
        prev_strip = prev_arr[-strip_h:, :, :]
        curr_strip = curr_arr[:strip_h, :, :]
        if curr_strip.shape[0] < prev_strip.shape[0]:
            pad = prev_strip.shape[0] - curr_strip.shape[0]
            curr_strip = np.pad(curr_strip, ((0, pad), (0, 0), (0, 0)),
                                mode="constant")
        prev_gray = cv2.cvtColor(prev_strip, cv2.COLOR_RGB2GRAY)
        curr_gray = cv2.cvtColor(curr_strip, cv2.COLOR_RGB2GRAY)
        res = cv2.matchTemplate(curr_gray, prev_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val > 0.7:
            offset = max_loc[1]
        else:
            offset = strip_h
        crop_top = offset
        if crop_top >= frames[i].height:
            continue
        cropped = frames[i].crop((0, crop_top, frames[i].width, frames[i].height))
        result = _concat_images(result, cropped)
    return result


def _stitch_simple(frames):
    """无 cv2 时简单垂直拼接（无重叠检测）。"""
    if not frames:
        return None
    total_h = sum(f.height for f in frames)
    max_w = max(f.width for f in frames)
    result = Image.new("RGBA", (max_w, total_h), (0, 0, 0, 0))
    y = 0
    for f in frames:
        result.paste(f, (0, y))
        y += f.height
    return result


def _concat_images(top, bottom):
    w = max(top.width, bottom.width)
    h = top.height + bottom.height
    result = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    result.paste(top, (0, 0))
    result.paste(bottom, (0, top.height))
    return result


def image_to_png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
