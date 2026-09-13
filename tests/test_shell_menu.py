"""右键菜单集成测试：真实 HKCU 写入子菜单结构，验证后即清理。

覆盖：子菜单安装（含旧版平铺动词迁移清理）、命令行内容、通配文件根、
卸载幂等（反复删不报错）。
"""

import winreg

from app.core import shell_menu


def _read_default(hive, path):
    with winreg.OpenKey(hive, path) as key:
        val, _ = winreg.QueryValueEx(key, None)
    return val


def test_roundtrip():
    # 先制造"旧版残留"：写入平铺动词，验证 install 会清理
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Classes\Directory\shell\LocalToolboxSMB",
    ):
        pass

    ok, errors = shell_menu.install()
    assert ok == len(shell_menu.VERBS), (ok, errors)
    assert not errors, errors

    st = shell_menu.status()
    assert all(st.values()), st

    # 旧版平铺键已被清理（不再与子菜单并存）
    try:
        winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Classes\Directory\shell\LocalToolboxSMB",
        )
        assert False, "旧版平铺动词未被清理"
    except OSError:
        pass

    # 父菜单键：默认值 = 菜单文字
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Classes\Directory\shell\LocalToolbox",
    ) as key:
        text, _ = winreg.QueryValueEx(key, None)
    assert "LocalToolbox" in text, text

    # 子命令结构：<verb>\command 存在且命令行正确
    for key, _t, _a, root in shell_menu.VERBS:
        cmd = _read_default(
            winreg.HKEY_CURRENT_USER,
            r"Software\Classes\%s\shell\LocalToolbox\subcommands\shell\%s\command"
            % (root, key),
        )
        assert '"%1"' in cmd, cmd
        assert ".exe" in cmd.lower() or "pythonw" in cmd.lower(), cmd
        # 子项显示文字存在
        sub = _read_default(
            winreg.HKEY_CURRENT_USER,
            r"Software\Classes\%s\shell\LocalToolbox\subcommands\shell\%s"
            % (root, key),
        )
        assert "LocalToolbox" in sub or "剪贴板" in sub or "SMB" in sub or "WebDAV" in sub, sub

    # 通配（任意文件）根的菜单也安装
    st2 = shell_menu.installed_keys()
    assert "LocalToolboxClip" in st2

    ok, errors = shell_menu.remove()
    assert not errors, errors
    assert not any(shell_menu.status().values())

    # 幂等：重复卸载不报错
    ok, errors = shell_menu.remove()
    assert not errors, errors
    print("SHELL MENU TEST PASSED")


test_roundtrip()