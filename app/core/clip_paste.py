"""剪贴板粘贴注入：把剪贴板内容用模拟按键（Ctrl+V）粘贴到之前的前台窗口。

Ditto 式粘贴链路的最后一步：弹窗写回剪贴板 → 隐藏 → 本模块把前台切回
原窗口并注入 Ctrl+V。SendInput 的 INPUT 结构参照 km_share.py 的范式。

注意：对 UAC / 管理员权限窗口注入无效（系统隔离），失败时由调用方降级为
「仅写回剪贴板」。
"""

import ctypes
import os
import time
from ctypes import wintypes

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_MENU = 0x12        # Alt（前台锁解锁技巧用）
VK_V = 0x56

if os.name == "nt":
    user32 = ctypes.windll.user32

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT),
                    ("hi", _HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]

    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
else:
    user32 = None


def _key(vk, up=False):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki = _KEYBDINPUT(
        wVk=vk, wScan=0,
        dwFlags=KEYEVENTF_KEYUP if up else 0,
        time=0, dwExtraInfo=0)
    return inp


def _send_inputs(inputs):
    arr = (INPUT * len(inputs))(*inputs)
    return user32.SendInput(len(arr), arr, ctypes.sizeof(INPUT)) == len(arr)


def send_combo(vk, ctrl=True):
    """注入一次按键（可选 Ctrl 修饰）：Ctrl↓ vk↓ vk↑ Ctrl↑。"""
    if user32 is None:
        return False
    seq = []
    if ctrl:
        seq.append(_key(VK_CONTROL))
    seq.append(_key(vk))
    seq.append(_key(vk, up=True))
    if ctrl:
        seq.append(_key(VK_CONTROL, up=True))
    return _send_inputs(seq)


def _foreground() -> int:
    h = user32.GetForegroundWindow()
    return int(h) if h else 0


def _ensure_foreground(prev_hwnd: int) -> bool:
    """把 prev_hwnd 切回前台；Windows 前台锁失败时用「ALT 解锁」技巧重试一次。"""
    hwnd = wintypes.HWND(prev_hwnd)
    if _foreground() == prev_hwnd:
        return True
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.12)
    if _foreground() == prev_hwnd:
        return True
    # ALT 解锁：系统禁止非前台进程抢焦点，先注入一次 Alt 抬起即可解锁本进程
    send_combo(VK_MENU, ctrl=False)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.12)
    return _foreground() == prev_hwnd


def send_paste(prev_hwnd: int, timeout_checks: int = 2) -> bool:
    """切回 prev_hwnd 并注入 Ctrl+V。切前台失败返回 False（调用方降级仅复制）。"""
    if user32 is None or not prev_hwnd:
        return False
    for _ in range(max(1, timeout_checks)):
        if _ensure_foreground(prev_hwnd):
            time.sleep(0.05)
            return send_combo(VK_V, ctrl=True)
        time.sleep(0.15)
    return False
