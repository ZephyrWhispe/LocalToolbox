"""WinFsp 驱动检测与安装（rclone 挂载依赖）。

- is_winfsp_installed()：注册表或安装目录探测；
- install_winfsp(msi_path)：管理员直接静默安装；非管理员经 ShellExecuteW("runas")
  弹 UAC 提权（异步，仅触达用户确认；安装结果由调用方稍后重新检测确认）。
"""

import os
import subprocess

# 窗口化进程里跑控制台程序会弹黑窗，必须禁用
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

WINPFSP_DLL = r"C:\Program Files (x86)\WinFsp\bin\winfsp-x64.dll"
WINPFSP_REG = r"SOFTWARE\WOW6432Node\WinFsp"
WINPFSP_URL = "https://winfsp.dev/rel/"


def is_winfsp_installed():
    """WinFsp 是否已安装（注册表键或安装目录 dll 任一路径命中即视为已装）。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WINPFSP_REG) as key:
            winreg.QueryValueEx(key, "InstallDir")
        return True
    except OSError:
        pass
    return os.path.isfile(WINPFSP_DLL)


def _is_admin():
    """当前进程是否以管理员身份运行。"""
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def install_winfsp(msi_path, log_callback=None):
    """安装 WinFsp msi 安装包。

    - 管理员：同步执行 msiexec /quiet /norestart，退出码 0/3010 视为成功；
    - 非管理员：ShellExecuteW("runas") 触发 UAC 提权执行（msiexec 需自带
      /quiet 参数），无法同步获知结果，返回"已触发"状态。

    返回 {"ok": True, "msg": 说明}；失败抛 ValueError（含中文原因）。
    """
    log = log_callback or (lambda m: None)
    if not os.path.isfile(str(msi_path)):
        raise ValueError("安装包不存在：%s" % msi_path)
    msi_path = str(msi_path)
    if _is_admin():
        try:
            r = subprocess.run(
                ["msiexec", "/i", msi_path, "/quiet", "/norestart"],
                capture_output=True, text=True, errors="replace",
                creationflags=_NOWIN,
            )
        except OSError as e:
            raise ValueError("无法执行 msiexec：%s" % e)
        if r.returncode not in (0, 3010):
            msg = ((r.stdout or "") + (r.stderr or "")).strip()[:200]
            log("WinFsp 安装失败（退出码 %s）：%s" % (r.returncode, msg or "未知错误"))
            raise ValueError(
                "WinFsp 安装失败（退出码 %s）。请到 %s 手动下载安装后重试。"
                % (r.returncode, WINPFSP_URL)
            )
        log("WinFsp 静默安装完成。")
        return {"ok": True, "msg": "WinFsp 安装完成"}
    # 非管理员：触发 UAC 提权（参数整体交给 runas 执行）
    try:
        import ctypes

        params = '/i "%s" /quiet /norestart' % msi_path
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "msiexec", params, None, 1
        )
    except Exception as e:
        raise ValueError("触发提权安装失败：%s" % e)
    if int(rc) <= 32:
        raise ValueError(
            "提权安装未执行（错误码 %s）。请确认 UAC 提示后重试，或到 %s 手动安装。"
            % (rc, WINPFSP_URL)
        )
    log("已触发 WinFsp 提权安装，请确认 UAC 提示；完成后重新检测。")
    return {"ok": True, "msg": "已触发安装，请确认 UAC 弹窗"}