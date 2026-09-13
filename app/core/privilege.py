import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SW_SHOW = 5


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", wintypes.INT),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", wintypes.LPVOID),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def is_admin():
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_target(extra_args=()):
    """计算提权重启的 (lpFile, lpParameters)。

    源码运行场景优先选 pythonw.exe（GUI 解释器，无控制台窗口）——此前直接用
    python.exe，ShellExecuteExW 拉起后必带一个命令行窗口闪现；
    打包（frozen）场景 exe 本身无控制台，维持原路径。
    """
    args = list(extra_args or [])
    if getattr(sys, "frozen", False):
        file = os.path.abspath(sys.executable)
        params = subprocess.list2cmdline(args)
        return file, params
    file = os.path.abspath(sys.executable)
    script = os.path.abspath(sys.argv[0] or "main.py")
    pyw = os.path.join(os.path.dirname(file), "pythonw.exe")
    if os.path.isfile(pyw):
        file = pyw
    params = '"%s" %s' % (script, subprocess.list2cmdline(args))
    return file, params


def relaunch_elevated(extra_args=()):
    """以管理员身份重新启动本应用（ShellExecuteExW runas）。

    extra_args: 传给新实例的附加命令行参数（如 ["--relaunch-admin"]）。
    返回 (True, None)：已发起提权启动，调用方应在 ~1.5s 后退出旧实例
    （旧实例退出后新实例以重试模式获取单实例锁继续启动）；
    返回 (False, 原因)：用户取消 UAC 或启动失败，旧实例应保持运行。
    """
    if sys.platform != "win32":
        return False, "仅支持 Windows 提权重启"
    try:
        file, params = _relaunch_target(extra_args)

        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
        shell32.ShellExecuteExW.restype = wintypes.BOOL
        info = _SHELLEXECUTEINFOW()
        info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
        info.fMask = _SEE_MASK_NOCLOSEPROCESS
        info.lpVerb = "runas"
        info.lpFile = file
        info.lpParameters = params
        info.nShow = _SW_SHOW

        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            err = ctypes.GetLastError()
            if err == 1223:  # ERROR_CANCELLED：用户拒绝了 UAC
                return False, "已取消提权"
            return False, "提权重启失败（错误码 %s）" % err
        if info.hProcess:
            ctypes.windll.kernel32.CloseHandle(info.hProcess)
        # 短暂等待 UAC 授权后的新实例启动，再让旧实例退出
        time.sleep(1.2)
        return True, None
    except Exception as e:
        return False, "提权重启失败：%s" % e
