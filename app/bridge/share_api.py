"""共享文件夹（SMB）页桥接：共享列表 / 创建共享 / 取消共享 / 目录选择。"""

from ..core import share_manager


def _pick_folder(bridge):
    """打开系统目录选择对话框，取消返回 None（对应旧页 QFileDialog）。"""
    import webview

    if bridge._window is None:
        raise RuntimeError("窗口尚未就绪，无法打开目录选择框。")
    result = bridge._window.create_file_dialog(webview.FOLDER_DIALOG)
    if not result:
        return None
    if isinstance(result, (list, tuple)):
        return result[0] if result else None
    return result


class ShareApi:
    def _init_share(self):
        pass

    # -- API -----------------------------------------------------------
    def share_list(self):
        try:
            return {"ok": True, "data": share_manager.list_shares()}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def share_create(self, name, path):
        name = str(name or "").strip()
        path = str(path or "").strip()
        try:
            ok, msg = share_manager.create_share(name, path)
        except Exception as e:
            return {"ok": False, "err": str(e)}
        if ok:
            return {"ok": True, "data": msg}
        return {"ok": False, "err": msg}

    def share_delete(self, name):
        name = str(name or "").strip()
        try:
            ok, msg = share_manager.delete_share(name)
        except Exception as e:
            return {"ok": False, "err": str(e)}
        if ok:
            return {"ok": True, "data": msg}
        return {"ok": False, "err": msg}

    def share_pick_folder(self):
        try:
            return {"ok": True, "data": _pick_folder(self)}
        except Exception as e:
            return {"ok": False, "err": str(e)}
