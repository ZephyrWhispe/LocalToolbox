"""工具箱桥接：哈希校验/清单校验、目录快照、列表导出、端口查询、二维码。"""

import os

import webview

from ..core import logger as applog
from ..core import tools

log = applog.get_logger("tools")


class ToolsApi:
    def _init_tools(self):
        self.tools = tools  # 便于测试引用

    def _pick(self):
        """文件夹选择器（未选返回 None）。"""
        if self._window is None:
            return None
        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory="")
            return str(result[0]) if result else None
        except Exception:
            return None

    # -- 哈希 -----------------------------------------------------------
    def tool_hash(self, paths, algo="sha256"):
        try:
            paths = [str(p) for p in (paths or []) if p]
            if not paths:
                return {"ok": False, "err": "请先选择文件或目录。"}
            algo = str(algo or "sha256") if str(algo or "") in tools.HASH_ALGOS else "sha256"
            out = tools.hash_paths(paths, algo=algo)
            return {"ok": True, "data": {"algo": algo, "files": out["files"],
                                        "fails": out["fails"], "results": out["results"]}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def tool_hash_pick(self, folder=False):
        """选择文件或目录（供前端点按钮时二次弹出）。"""
        if self._window is None:
            return {"ok": False, "err": "窗口尚未就绪。"}
        try:
            result = self._window.create_file_dialog(
                webview.FOLDER_DIALOG if folder else webview.OPEN_DIALOG,
                directory="", allow_multiple=not folder,
            )
            return {"ok": True, "data": list(result or [])}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def tool_manifest_pick(self):
        """选择 .md5 清单文件。"""
        return self.tool_hash_pick()

    def tool_verify(self, manifest_path):
        try:
            path = str(manifest_path or "")
            if not os.path.isfile(path):
                return {"ok": False, "err": "清单文件不存在：%s" % path}
            return {"ok": True, "data": tools.verify_manifest(path)}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 目录快照 -------------------------------------------------------
    def tool_snapshot(self, source=None, dest=None):
        try:
            if not source:
                source = self._pick()
                if not source:
                    return {"ok": False, "err": "未选择源目录"}
            if not dest:
                dest = self._pick()
                if not dest:
                    return {"ok": False, "err": "未选择目标目录"}
            created = tools.snapshot_tree(source, dest)
            log.info("目录结构快照 %s → %s 共 %d 个目录", source, dest, created)
            return {"ok": True, "data": {"created": created, "source": source, "dest": dest}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 列表导出 -------------------------------------------------------
    def tool_export(self, path, include_sub=True, with_hash=False, algo="sha256"):
        try:
            path = str(path or "")
            if not os.path.isdir(path):
                return {"ok": False, "err": "目录不存在：%s" % path}
            algo = str(algo)
            if algo not in tools.HASH_ALGOS:
                return {"ok": False, "err": "不支持的哈希算法：%s" % algo}
            out, count = tools.export_file_list(
                path, include_sub=bool(include_sub), with_hash=bool(with_hash), algo=algo)
            log.info("文件清单导出 %s → %s（%d 行）", path, out, count)
            return {"ok": True, "data": {"path": out, "count": count}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def tool_export_pick(self):
        """选择要导出的目录。"""
        return self.tool_hash_pick(folder=True)

    # -- 端口占用 -------------------------------------------------------
    def tool_port_query(self, port):
        try:
            port = int(port)
            if not (1 <= port <= 65535):
                return {"ok": False, "err": "端口号需在 1-65535 之间。"}
            return {"ok": True, "data": tools.port_owner(port)}
        except (TypeError, ValueError):
            return {"ok": False, "err": "端口号需为数字。"}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def tool_port_kill(self, pid):
        try:
            ok, msg = tools.kill_pid(int(pid))
            return {"ok": ok, "data": None} if ok else {"ok": False, "err": msg}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 二维码 ---------------------------------------------------------
    def tool_qr(self, text, size=280):
        try:
            text = str(text or "").strip()
            if not text:
                return {"ok": False, "err": "请输入要生成二维码的内容。"}
            size = max(128, min(1024, int(size or 280)))
            url = tools.qr_png_data_url(text, size)
            if not url:
                return {"ok": False, "err": "二维码组件不可用（缺少 qrcode 库）。"}
            return {"ok": True, "data": {"img": url, "text": text}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- OCR 图片识别（Windows.Media.Ocr） ----------------------------------
    def tool_ocr_file(self):
        """选择图片文件并识别文字，返回全文与逐行坐标。"""
        try:
            from ..core import ocr
            import io as _io
            if not ocr.is_available():
                return {"ok": False, "err": ocr._UNAVAILABLE_MSG}
            if self._window is None:
                return {"ok": False, "err": "窗口尚未就绪。"}
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, directory="",
                file_types=("图片文件 (*.png;*.jpg;*.jpeg;*.bmp;*.webp)",),
            )
            path = str(result[0]) if result else ""
            if not path or not os.path.isfile(path):
                return {"ok": False, "err": "未选择图片文件。"}
            with open(path, "rb") as f:
                png = f.read()
            out = ocr.recognize_bytes(png)
            if not (out["text"] or "").strip():
                return {"ok": True, "data": {"path": path, "text": "", "lines": [], "empty": True}}
            log.info("OCR 识别 %s：%d 行文字", path, len(out["lines"]))
            return {"ok": True, "data": {"path": path, **out}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 屏幕取色器 + 放大镜 ------------------------------------------------
    def tool_pick_color(self):
        """运行屏幕取色器（阻塞直至锁定/取消），返回 {rgb,hex} 或 None。"""
        try:
            from ..core import pickcolor
            return {"ok": True, "data": pickcolor.pick_color_blocking()}
        except Exception as e:
            return {"ok": False, "err": str(e)}