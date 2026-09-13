"""工具箱桥接：哈希校验/清单校验、目录快照、列表导出、端口查询、二维码。"""

import os
import threading

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

    # -- Wi-Fi 密码查看（v5.2 工具箱） -----------------------------------
    def tool_wifi_list(self):
        try:
            names = tools.wifi_profiles()
            return {"ok": True, "data": {"count": len(names), "names": names}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def tool_wifi_password(self, name):
        try:
            name = str(name or "").strip()
            if not name:
                return {"ok": False, "err": "请指定 WLAN 配置文件名。"}
            pwd, auth = tools.wifi_password(name)
            return {"ok": True, "data": {"name": name, "auth": auth,
                                         "has_pwd": bool(pwd),
                                         "password": pwd or ""}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 硬件信息（v5.3 工具箱，CIM/WMI 只读） ----------------------------
    def tool_hw_summary(self):
        try:
            return {"ok": True, "data": tools.hw_summary()}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def tool_hw_detail(self):
        try:
            return {"ok": True, "data": tools.hw_detail()}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def tool_hw_disks(self):
        try:
            return {"ok": True, "data": tools.hw_disks()}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def tool_hw_network(self):
        try:
            return {"ok": True, "data": tools.hw_network()}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def tool_hw_live(self):
        try:
            return {"ok": True, "data": tools.hw_live()}
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

    # -- OCR 图片识别（v5.1：winrt / rapid / umi 三引擎 + 独立页） --------
    def _ocr_engine(self):
        return str(self.cfg.get("ocr_engine", "winrt") or "winrt")

    def _ocr_run(self, png, source=""):
        """统一识别：引擎分发 + 后处理，返回 processed/raw 双文本。"""
        from ..core import ocr
        from ..core.ocr_umi import UmiError
        import io as _io
        from PIL import Image
        img = Image.open(_io.BytesIO(png))
        engine = self._ocr_engine()
        umi_kw = {}
        if engine == "umi":
            umi_kw = {"url": str(self.cfg.get("ocr_umi_url", "") or None),
                      "exe_path": str(self.cfg.get("ocr_umi_path", "") or ""),
                      "autostart": bool(self.cfg.get("ocr_umi_autostart", True))}
        try:
            out = ocr.recognize_any(img, engine=engine, umi_opts=umi_kw or None)
        except UmiError as e:
            return {"ok": False, "err": str(e)}
        merge = bool(self.cfg.get("ocr_merge_lines", True))
        out["raw_text"] = out["text"]
        out["text"] = ocr.postprocess(out["text"], merge_lines=merge)
        out["engine"] = engine
        out["source"] = source
        return {"ok": True, "data": out}

    def tool_ocr_file(self, path=None):
        """识别图片：带 path 直接识别（批量/拖放），否则弹多选对话框逐张返回。"""
        try:
            from ..core import ocr
            if path:
                path = str(path)
                if not os.path.isfile(path):
                    return {"ok": False, "err": "文件不存在：%s" % path}
                with open(path, "rb") as f:
                    png = f.read()
                r = self._ocr_run(png, source=path)
                if r.get("ok") and not (r["data"]["text"] or "").strip():
                    r["data"]["empty"] = True
                return r
            if self._window is None:
                return {"ok": False, "err": "窗口尚未就绪。"}
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, directory="", allow_multiple=True,
                file_types=("图片文件 (*.png;*.jpg;*.jpeg;*.bmp;*.webp)",),
            )
            paths = [str(p) for p in (result or []) if p]
            if not paths:
                return {"ok": False, "err": "未选择图片文件。"}
            return {"ok": True, "data": {"paths": paths}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def tool_ocr_clipboard(self):
        """识别剪贴板中的图片（v5.1 粘贴识别）。"""
        try:
            from ..core import ocr, screenshot
            png = screenshot.read_clipboard_png()
            if not png:
                return {"ok": False, "err": "剪贴板中没有图片，请先复制一张图片。"}
            r = self._ocr_run(png, source="剪贴板")
            if r.get("ok") and not (r["data"]["text"] or "").strip():
                r["data"]["empty"] = True
            return r
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def tool_ocr_engines(self):
        """引擎状态（winrt/rapid 可用性 + umi 服务探测 + 当前选择）。"""
        from ..core import ocr, ocr_umi
        url = str(self.cfg.get("ocr_umi_url", "") or ocr_umi.DEFAULT_URL)
        return {"ok": True, "data": {
            "engine": self._ocr_engine(),
            "winrt": ocr.is_available(),
            "rapid": ocr.rapid_available(),
            "umi_ready": ocr_umi.server_ready(url),
            "umi_path": str(self.cfg.get("ocr_umi_path", "") or ""),
            "umi_url": url,
            "umi_autostart": bool(self.cfg.get("ocr_umi_autostart", True)),
        }}

    def ocr_umi_download(self):
        """下载 Umi-OCR 内核（后台线程，进度经 ocr_download_progress 事件）。"""
        if getattr(self, "_ocr_dl_running", False):
            return {"ok": False, "err": "内核正在下载中"}
        self._ocr_dl_running = True

        def worker():
            from ..core import ocr_umi
            try:
                def cb(done, total):
                    self.emit("ocr_download_progress", {
                        "stage": "download", "done": done, "total": total})
                r = ocr_umi.download_kernel(progress_cb=cb)
                self.emit("ocr_download_progress", {
                    "stage": "done", "exe": r["exe"], "cached": r.get("cached")})
                self.emit_log("Umi-OCR 内核就绪：%s" % r["exe"])
            except Exception as e:
                self.emit("ocr_download_progress", {"stage": "error", "err": str(e)})
                self.emit_log("Umi-OCR 内核下载失败：%s" % e)
            finally:
                self._ocr_dl_running = False

        threading.Thread(target=worker, daemon=True, name="ocr-umi-dl").start()
        return {"ok": True, "data": True}

    def ocr_umi_start(self):
        """手动启动 Umi 内核服务。"""
        try:
            from ..core import ocr_umi
            r = ocr_umi.ensure_server(
                str(self.cfg.get("ocr_umi_url", "") or None) or None,
                exe_path=str(self.cfg.get("ocr_umi_path", "") or ""),
                autostart=bool(self.cfg.get("ocr_umi_autostart", True)))
            self._push_state_ocr()
            return {"ok": True, "data": r}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def _push_state_ocr(self):
        try:
            r = self.tool_ocr_engines()
            if r.get("ok"):
                self.emit("ocr_state", r["data"])
        except Exception:
            pass

    # -- 屏幕取色器 + 放大镜 ------------------------------------------------
    def tool_pick_color(self):
        """运行屏幕取色器（阻塞直至锁定/取消），返回 {rgb,hex} 或 None。"""
        try:
            from ..core import pickcolor
            return {"ok": True, "data": pickcolor.pick_color_blocking()}
        except Exception as e:
            return {"ok": False, "err": str(e)}