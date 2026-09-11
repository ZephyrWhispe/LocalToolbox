"""右键菜单页桥接：状态查询 / 安装 / 卸载。"""

from ..core import shell_menu


class ShellMenuApi:
    def _init_shellmenu(self):
        pass

    def shell_status(self):
        try:
            status = shell_menu.status()
            return {
                "ok": True,
                "data": [
                    {"key": k, "text": t, "installed": status.get(k, False)}
                    for k, t, _a, _r in shell_menu.VERBS
                ],
            }
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def shell_install(self):
        ok, errors = shell_menu.install()
        if errors:
            return {"ok": False, "err": "部分菜单项安装失败：\n" + "\n".join(errors)}
        return {"ok": True, "data": ok}

    def shell_remove(self):
        ok, errors = shell_menu.remove()
        if errors:
            return {"ok": False, "err": "部分菜单项卸载失败：\n" + "\n".join(errors)}
        return {"ok": True, "data": ok}
