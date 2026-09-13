# -*- coding: utf-8 -*-
"""平台能力探测单点（v5.4 O12）。

目标软件为 Windows 专用；本模块收敛散布在各处的管理员/外部工具探测调用，
输出统一的能力声明，供降级提示与功能开关判断。不做跨平台移植。
"""

import ctypes
import shutil
import sys

from .runner import run


def is_windows():
    return sys.platform == "win32"


def is_admin():
    """当前进程是否以管理员令牌运行。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def has_winget():
    """winget（应用安装程序）是否可用。"""
    return shutil.which("winget") is not None


def has_wt():
    """Windows Terminal 是否可用。"""
    return shutil.which("wt") is not None


def capabilities():
    """聚合能力声明（前端降级提示用）。"""
    return {
        "windows": is_windows(),
        "admin": is_admin(),
        "winget": has_winget(),
        "wt": has_wt(),
    }
