"""Windows 资源管理器右键菜单集成（HKCU 二级子菜单，无需管理员权限）。

结构（Explorer 经典 subcommands 级联菜单）：
  Software\\Classes\\{Directory|*}\\shell\\LocalToolbox      默认值 = 菜单文字 + Icon
    \\subcommands\\shell\\<verb>                                   默认值 = 子项文字
      \\command                                                    → 启动命令

安装时自动清理直接挂在 shell 下的平铺动词（与子命令同名），
避免两代菜单并存。
"""

import os
import sys
import winreg

VERBS = [
    # (键名, 菜单文字, CLI 参数, 应用于 "Directory"/"*")
    ("LocalToolboxSMB", "用 LocalToolbox 共享(SMB)", "--share", "Directory"),
    ("LocalToolboxWebDAV", "用 LocalToolbox 发布(WebDAV)", "--webdav", "Directory"),
    ("LocalToolboxClip", "复制到局域网剪贴板同步", "--clip", "*"),
]

_MENU_KEY = "LocalToolbox"
_MENU_TEXT = "LocalToolbox"

_CLASSES_ROOT = r"Software\Classes"


def launcher_prefix():
    """打包后是 exe 本身；脚本运行用 pythonw + main.py。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    main_py = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "main.py",
    )
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return f'"{pythonw}" "{main_py}"'


def _command_for(arg):
    return f'{launcher_prefix()} {arg} "%1"'


def _roots():
    return {r for _k, _t, _a, r in VERBS}


def _verbs_for(root):
    return [v for v in VERBS if v[3] == root]


def _menu_path(root):
    return rf"{_CLASSES_ROOT}\{root}\shell\{_MENU_KEY}"


def _sub_path(root, key):
    return rf"{_menu_path(root)}\subcommands\shell\{key}"


def _sub_cmd_path(root, key):
    return _sub_path(root, key) + r"\command"


def _legacy_verb_path(root, key):
    return rf"{_CLASSES_ROOT}\{root}\shell\{key}"


def _delete_key(hive, path, errors):
    """递归删除一个注册表键及其子树（从最深子键开始）。"""
    try:
        with winreg.OpenKey(hive, path):
            pass
    except FileNotFoundError:
        return
    except OSError as e:
        errors.append(f"{path}: {e}")
        return
    try:
        for sub in _subkeys(hive, path):
            _delete_key(hive, path + "\\" + sub, errors)
    except OSError as e:
        errors.append(f"{path}: {e}")
    try:
        winreg.DeleteKey(hive, path)
    except FileNotFoundError:
        pass
    except OSError as e:
        errors.append(f"{path}: {e}")


def _subkeys(hive, path):
    """返回 path 的直接子键名列表。"""
    out = []
    try:
        with winreg.OpenKey(hive, path) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


def _has(hive, path):
    try:
        with winreg.OpenKey(hive, path):
            return True
    except OSError:
        return False


def install(hive=winreg.HKEY_CURRENT_USER):
    """安装二级子菜单。返回 (成功数, 失败原因列表)。"""
    ok, errors = 0, []
    # 1) 清理旧版平铺动词（升级迁移，避免菜单堆叠）
    for key, _t, _a, root in VERBS:
        _delete_key(hive, _legacy_verb_path(root, key), errors)
    # 2) 按 root（Directory / *）各建一个子菜单
    for root in _roots():
        try:
            with winreg.CreateKey(hive, _menu_path(root)) as base:
                winreg.SetValueEx(base, None, 0, winreg.REG_SZ, _MENU_TEXT)
                winreg.SetValueEx(base, "Icon", 0, winreg.REG_SZ, sys.executable)
        except OSError as e:
            errors.append(f"{_menu_path(root)}: {e}")
            continue
        for key, text, arg, r in _verbs_for(root):
            try:
                with winreg.CreateKey(
                    hive, _sub_cmd_path(root, key)
                ) as cmd:
                    winreg.SetValueEx(cmd, None, 0, winreg.REG_SZ, _command_for(arg))
                with winreg.CreateKey(hive, _sub_path(root, key)) as sub:
                    winreg.SetValueEx(sub, None, 0, winreg.REG_SZ, text)
                ok += 1
            except OSError as e:
                errors.append(f"{key}: {e}")
    return ok, errors


def remove(hive=winreg.HKEY_CURRENT_USER):
    """卸载子菜单（含清理旧版平铺动词）。返回 (成功数, 失败原因列表)。"""
    errors = []
    for root in _roots():
        _delete_key(hive, _menu_path(root), errors)
    for key, _t, _a, root in VERBS:
        _delete_key(hive, _legacy_verb_path(root, key), errors)
    # errors 里 FileNotFoundError 已被忽略；其余按失败计
    return len(VERBS), errors


def _verb_installed(hive, key):
    for _k, _t, _a, root in VERBS:
        if key == _k and _has(hive, _sub_cmd_path(root, key)):
            return True
    return False


def status(hive=winreg.HKEY_CURRENT_USER):
    """返回 {键名: bool}。"""
    return {key: _verb_installed(hive, key) for key, _t, _a, _r in VERBS}


def installed_keys(hive=winreg.HKEY_CURRENT_USER):
    """返回已安装的键名集合。"""
    return {k for k, v in status(hive).items() if v}