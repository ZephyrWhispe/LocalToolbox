"""批量图片处理核心：resize、格式转换、水印、特效流水线。"""

import io
import os
import threading

from PIL import Image

from . import image_editor as editor


class BatchProcessor:
    def __init__(self):
        self._cancel = False
        self._running = False
        self._lock = threading.Lock()
        self._progress = {"current": 0, "total": 0, "path": "", "errors": []}

    @property
    def running(self):
        return self._running

    def get_state(self):
        with self._lock:
            return dict(self._progress)

    def cancel(self):
        self._cancel = True

    def run(self, file_paths, operations, output_dir, on_progress=None):
        self._cancel = False
        self._running = True
        self._progress = {"current": 0, "total": len(file_paths),
                          "path": "", "errors": []}
        os.makedirs(output_dir, exist_ok=True)
        results = []
        try:
            for i, src in enumerate(file_paths):
                if self._cancel:
                    break
                with self._lock:
                    self._progress["current"] = i + 1
                    self._progress["path"] = os.path.basename(src)
                try:
                    data = self._process_one(src, operations)
                    base = os.path.splitext(os.path.basename(src))[0]
                    out_fmt = self._get_output_format(operations)
                    ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp",
                           "BMP": ".bmp"}.get(out_fmt, ".png")
                    out_path = os.path.join(output_dir, base + "_processed" + ext)
                    with open(out_path, "wb") as f:
                        f.write(data)
                    results.append(out_path)
                except Exception as e:
                    with self._lock:
                        self._progress["errors"].append(
                            {"file": os.path.basename(src), "error": str(e)})
                if on_progress:
                    on_progress(i + 1, len(file_paths))
        finally:
            self._running = False
        return {"ok": True, "data": {"count": len(results),
                                      "errors": self._progress["errors"]}}

    def _process_one(self, src, operations):
        with open(src, "rb") as f:
            data = f.read()
        for op in operations:
            kind = op.get("type")
            if kind == "resize":
                data = editor.resize_image(data, int(op["width"]))
            elif kind == "effect":
                data = editor.apply_effect(data, op["effect"], op.get("params"))
            elif kind == "watermark_text":
                data = editor.add_text_watermark(
                    data, op["text"],
                    font_size=int(op.get("font_size", 36)),
                    color=op.get("color", "#FFFFFF"),
                    opacity=int(op.get("opacity", 128)),
                    position=op.get("position", "bottom-right"),
                )
            elif kind == "convert":
                data = editor.convert_format(data, op["format"])
        return data

    def _get_output_format(self, operations):
        for op in reversed(operations):
            if op.get("type") == "convert":
                fmt = op["format"].upper().lstrip(".")
                return {"JPG": "JPEG", "JPEG": "JPEG", "PNG": "PNG",
                        "WEBP": "WEBP", "BMP": "BMP"}.get(fmt, "PNG")
        return "PNG"
