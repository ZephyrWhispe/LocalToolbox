"""日志管理桥接：查询日志状态、运行时调整级别、打开日志目录/文件。"""

import os
import subprocess

from ..core import logger as applog
from ..core.config import DATA_HOME


class LogApi:
    def _open_path(self, path):
        """用系统默认程序打开目录/文件（explorer / 记事本）。"""
        if not path or not os.path.exists(path):
            return False, "路径不存在：%s" % path
        try:
            os.startfile(path)
            return True, "ok"
        except Exception:
            # 非 Windows 兜底（本应用 Windows-only，此处仅防御）
            try:
                subprocess.Popen(["explorer", path])
                return True, "ok"
            except Exception as e:
                return False, str(e)

    # -- API -----------------------------------------------------------
    def log_status(self):
        st = applog.get_status()
        st["levels"] = ["DEBUG", "INFO", "WARN", "ERROR"]
        return {"ok": True, "data": st}

    def log_set_level(self, level):
        if applog.set_level(str(level or "").upper()):
            return {"ok": True, "data": applog.get_status()["level"]}
        return {"ok": False, "err": "无效的日志级别：%s" % level}

    def log_open_dir(self):
        ok, msg = self._open_path(applog.get_status()["dir"])
        return {"ok": ok, "err": msg} if not ok else {"ok": True, "data": None}

    def log_open_data(self):
        """打开数据根目录（config.json / clipboard_history.db / clipboard_images 等）。"""
        ok, msg = self._open_path(DATA_HOME)
        return {"ok": ok, "err": msg} if not ok else {"ok": True, "data": None}

    def log_open_app(self):
        ok, msg = self._open_path(applog.get_status()["app_log"])
        return {"ok": ok, "err": msg} if not ok else {"ok": True, "data": None}