"""Windows 单实例锁：命名 Mutex（CreateMutexW）。

第二个实例启动时检测到同名 Mutex 已存在，先通过命名事件唤醒已有实例的
主窗口（否则用户面对"启动即消失"且托盘图标可能被 Win11 收进溢出区找不到），
再弹窗提示后退出。
--relaunch-admin 场景（以管理员身份重启）会传入 retry_seconds：
旧实例提权启动新实例后即将退出，新实例短暂等待锁释放再继续，避免竞态。
"""

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Global\\LocalToolbox-SingleInstance-v1"
SHOW_EVENT_NAME = "Global\\LocalToolbox-Show-v1"
EVENT_MODIFY_STATE = 0x0002
INFINITE = 0xFFFFFFFF

# 私有 WinDLL 实例：argtypes 不与共享 windll 单例互相污染（项目铁律）；
# use_last_error=True 保证 GetLastError 读到的是本次调用的错误码
_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
if _k32 is not None:
    _k32.CreateMutexW.restype = wintypes.HANDLE
    _k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.OpenEventW.restype = wintypes.HANDLE
    _k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.SetEvent.argtypes = [wintypes.HANDLE]
    _k32.SetEvent.restype = wintypes.BOOL
    _k32.CreateEventW.restype = wintypes.HANDLE
    _k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL,
                                  wintypes.LPCWSTR]
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD


def _message_box(text, title):
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)  # MB_ICONINFORMATION
    except Exception:
        pass


def acquire_or_exit(retry_seconds=0):
    """获取单实例锁；已有实例运行则唤醒其主窗口、提示并退出本进程。
    返回 Mutex 句柄（勿关闭）。

    retry_seconds > 0：遇到已存在锁时按 250ms 间隔重试（供提权重启的子进程
    等待旧实例退出），超时仍未获得才提示退出。
    """
    try:
        deadline = time.time() + max(0, float(retry_seconds))
        while True:
            handle = _k32.CreateMutexW(None, False, MUTEX_NAME)
            if ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
                return handle
            if handle:
                _k32.CloseHandle(handle)  # 释放对已有锁的引用，下次再试
            if time.time() >= deadline:
                break
            time.sleep(0.25)
        # 已有实例：先通过命名事件唤起其主窗口，再提示退出
        notify_existing()
        _message_box(
            "LocalToolbox 已在运行中，已唤起主窗口。\n"
            "若未看到窗口，请检查屏幕右下角系统托盘"
            "（Win11 可能收纳在「^」溢出区内）。",
            "LocalToolbox",
        )
        raise SystemExit(0)
    except SystemExit:
        raise
    except Exception:
        # 非 Windows 或 API 异常时不阻塞启动
        return None


def notify_existing():
    """触发已有实例的唤起事件（主实例 start_show_watcher 在等待它）。"""
    if _k32 is None:
        return
    try:
        h = _k32.OpenEventW(EVENT_MODIFY_STATE, False, SHOW_EVENT_NAME)
        if h:
            _k32.SetEvent(h)
            _k32.CloseHandle(h)
    except Exception:
        pass


def start_show_watcher(on_show):
    """主实例：后台线程等待唤起事件（第二实例启动时触发 on_show）。"""
    if _k32 is None:
        return
    try:
        handle = _k32.CreateEventW(None, False, False, SHOW_EVENT_NAME)
        if not handle:
            return

        def run():
            while True:
                if _k32.WaitForSingleObject(handle, INFINITE) != 0:
                    continue
                try:
                    on_show()
                except Exception:
                    pass

        threading.Thread(target=run, daemon=True, name="show-watcher").start()
    except Exception:
        pass
