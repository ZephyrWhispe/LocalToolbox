"""批量处理桥接层。"""

import base64
import threading

from .base import BridgeBase


class BatchApi(BridgeBase):
    def _init_batch(self):
        from ..core.batch_process import BatchProcessor
        self._batch = BatchProcessor()

    def batch_pick_files(self):
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.webp *.tiff *.gif"),
                       ("所有文件", "*.*")])
        if not paths:
            return {"ok": False, "err": "未选择文件"}
        return {"ok": True, "data": {"paths": list(paths)}}

    def batch_pick_dir(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(title="选择图片目录")
        if not d:
            return {"ok": False, "err": "未选择目录"}
        import os
        exts = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff", ".gif"}
        files = [os.path.join(d, f) for f in os.listdir(d)
                 if os.path.splitext(f)[1].lower() in exts]
        if not files:
            return {"ok": False, "err": "目录中无图片文件"}
        return {"ok": True, "data": {"paths": files}}

    def batch_start(self, file_paths, operations, output_dir):
        if self._batch.running:
            return {"ok": False, "err": "正在处理中，请等待完成"}
        if not file_paths or not operations or not output_dir:
            return {"ok": False, "err": "参数不完整"}

        def _on_progress(current, total):
            self.emit("batch_progress", {
                "current": current, "total": total,
                "path": self._batch.get_state().get("path", "")})

        threading.Thread(
            target=self._run_batch,
            args=(list(file_paths), list(operations), output_dir, _on_progress),
            daemon=True, name="batch-process").start()
        return {"ok": True, "data": {"started": True}}

    def _run_batch(self, paths, ops, out_dir, on_progress):
        result = self._batch.run(paths, ops, out_dir, on_progress)
        self.emit("batch_done", result.get("data", {}))

    def batch_stop(self):
        self._batch.cancel()
        return {"ok": True}

    def batch_get_state(self):
        return {"ok": True, "data": self._batch.get_state()}
