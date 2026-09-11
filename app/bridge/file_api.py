"""文件管理页桥接：目录浏览、常用位置、复制/剪切/粘贴、新建/删除/打开。

浏览目录属同步快速操作，直接返回数据；剪贴板（复制/剪切状态）保存在
Python 侧，页面刷新后不丢失，与旧 Qt 页 self.clipboard 行为一致。
"""

import os

from ..core import file_manager


class FileApi:
    def _init_file(self):
        # 内部「文件剪贴板」：{"action": "copy"|"cut", "paths": [...]}
        self._file_clip = None

    # -- 常用位置 -------------------------------------------------------
    def file_places(self):
        """对应旧页 refresh_places：文档 / 桌面 / 全部驱动器。"""
        try:
            home = os.path.expanduser("~")
            places = [
                {"name": "文档", "path": os.path.join(home, "Documents")},
                {"name": "桌面", "path": os.path.join(home, "Desktop")},
            ]
            for drive in file_manager.drive_letters():
                places.append({"name": "驱动器 %s" % drive, "path": drive})
            return {"ok": True, "data": places}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 浏览 -----------------------------------------------------------
    def file_list(self, path=""):
        """列出目录；path 为空时回退到用户主目录（同旧页 refresh）。"""
        try:
            path = str(path or "").strip()
            if not path:
                path = os.path.expanduser("~")
            entries = file_manager.list_directory(path)
            if entries is None:
                return {"ok": False, "err": "无法访问目录：\n%s" % path}
            cur = path.rstrip("\\/")
            parent = os.path.dirname(cur)
            return {
                "ok": True,
                "data": {
                    "path": path,
                    "parent": "" if parent == cur else parent,
                    "entries": entries,
                },
            }
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 文件剪贴板 -----------------------------------------------------
    def file_copy(self, paths):
        paths = [str(p) for p in (paths or [])]
        if not paths:
            return {"ok": False, "err": "请先选择要复制的项目。"}
        self._file_clip = {"action": "copy", "paths": paths}
        return {"ok": True, "data": {"action": "copy", "count": len(paths)}}

    def file_cut(self, paths):
        paths = [str(p) for p in (paths or [])]
        if not paths:
            return {"ok": False, "err": "请先选择要剪切的项目。"}
        self._file_clip = {"action": "cut", "paths": paths}
        return {"ok": True, "data": {"action": "cut", "count": len(paths)}}

    def file_paste(self, dest):
        clip = self._file_clip
        if not clip or not clip["paths"]:
            return {"ok": False, "err": "剪贴板为空，请先复制或剪切。"}
        try:
            if clip["action"] == "cut":
                msg = file_manager.move_paths(clip["paths"], str(dest))
                self._file_clip = None
            else:
                msg = file_manager.copy_paths(clip["paths"], str(dest))
            return {"ok": True, "data": {"msg": msg, "cleared": clip["action"] == "cut"}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 其他操作 -------------------------------------------------------
    def file_delete(self, paths):
        paths = [str(p) for p in (paths or [])]
        if not paths:
            return {"ok": False, "err": "请先选择要删除的项目。"}
        try:
            return {"ok": True, "data": {"msg": file_manager.delete_paths(paths)}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def file_new_folder(self, parent, name):
        name = str(name or "").strip()
        if not name:
            return {"ok": False, "err": "文件夹名称不能为空。"}
        try:
            return {"ok": True, "data": {"msg": file_manager.new_folder(str(parent), name)}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def file_open(self, path):
        try:
            file_manager.open_path(str(path))
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def file_drop(self, paths, dest):
        """拖放文件到当前目录（对应旧页 dropEvent → copy_paths）。"""
        paths = [str(p) for p in (paths or []) if p]
        if not paths:
            return {"ok": False, "err": "无法获取拖入文件的本地路径。"}
        try:
            return {"ok": True, "data": {"msg": file_manager.copy_paths(paths, str(dest))}}
        except Exception as e:
            return {"ok": False, "err": str(e)}
