import json
import subprocess
import sys
import os
import ctypes
import tempfile
import logging
import time
from ctypes import wintypes

from . import logger as applog

log = applog.get_logger("runner")


class CommandResult:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout or ""
        self.stderr = stderr or ""

    @property
    def ok(self):
        return self.returncode == 0

    @property
    def output(self):
        return self.stdout if self.stdout else self.stderr


def _creationflags():
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def run(command, timeout=None):
    if isinstance(command, str):
        shell = True
        cmd = command
    else:
        shell = False
        cmd = command
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=True,
            encoding="gbk",
            errors="replace",
            timeout=timeout,
            creationflags=_creationflags(),
        )
        result = CommandResult(proc.returncode, proc.stdout, proc.stderr)
        _log_command(cmd, result, t0)
        return result
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else ""
        err = e.stderr if isinstance(e.stderr, str) else ""
        result = CommandResult(1, out, err)
        log.warning("命令执行超时：%s", cmd)
        _log_command(cmd, result, t0)
        return result
    except Exception as e:
        result = CommandResult(1, "", str(e))
        log.error("命令执行异常：%s（%s）", cmd, e)
        return result


def _log_command(cmd, result, t0):
    """DEBUG 级别记录命令与耗时（默认 INFO 级别下不落盘，避免刷屏）。"""
    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "命令 %s 结束 code=%d 耗时 %dms",
            cmd if isinstance(cmd, str) else " ".join(cmd),
            result.returncode,
            (time.time() - t0) * 1000,
        )


_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SW_HIDE = 0
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_STILL_ACTIVE = 259


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


def run_elevated(command, timeout=120):
    if isinstance(command, list):
        cmd_str = subprocess.list2cmdline(command)
    else:
        cmd_str = command

    tag = f"{os.getpid()}_{id(command)}"
    out_file = os.path.join(tempfile.gettempdir(), f"smb_out_{tag}.txt")
    bat_file = os.path.join(tempfile.gettempdir(), f"smb_run_{tag}.bat")

    with open(bat_file, "w", encoding="gbk") as f:
        f.write("@echo off\n")
        f.write(f'{cmd_str} > "{out_file}" 2>&1\n')

    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = bat_file
    info.nShow = _SW_HIDE

    success = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info))
    if not success:
        _safe_remove(bat_file)
        _safe_remove(out_file)
        err = ctypes.GetLastError()
        return CommandResult(
            1, "", f"提权执行失败（错误码 {err}），可能拒绝了 UAC 提示。"
        )

    ret_code = 0
    wait_result = 0
    if info.hProcess:
        wait_ms = int(timeout * 1000) if timeout else 0xFFFFFFFF
        wait_result = ctypes.windll.kernel32.WaitForSingleObject(
            info.hProcess, wait_ms
        )
        exit_code = wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(
            info.hProcess, ctypes.byref(exit_code)
        )
        ctypes.windll.kernel32.CloseHandle(info.hProcess)
        ret_code = exit_code.value

    output = ""
    if os.path.exists(out_file):
        try:
            with open(out_file, "r", encoding="gbk", errors="replace") as f:
                output = f.read()
        except Exception:
            pass

    _safe_remove(bat_file)
    _safe_remove(out_file)

    if wait_result == _WAIT_TIMEOUT:
        return CommandResult(1, output, "提权执行超时。")

    return CommandResult(ret_code, output, "")


def run_powershell(script):
    return run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ]
    )


def run_powershell_elevated(script, timeout=120):
    return run_elevated(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        timeout=timeout,
    )


def run_powershell_json(script):
    result = run_powershell(script)
    if not result.ok:
        return None
    text = result.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _safe_remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass