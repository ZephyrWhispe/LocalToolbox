"""图片合并桥接层。"""

import base64

from .base import BridgeBase


class CombineApi(BridgeBase):
    def _init_combine(self):
        pass

    def combine_images(self, data_urls, direction="vertical", gap=0, bg_color="#FFFFFF"):
        try:
            raws = [_decode_data_url(d) for d in data_urls]
            from ..core.image_combine import combine_vertical, combine_horizontal, combine_grid
            gap = int(gap)
            if direction == "horizontal":
                result = combine_horizontal(raws, gap, bg_color)
            elif direction == "grid":
                cols = max(2, int(len(raws) ** 0.5))
                result = combine_grid(raws, cols, gap, bg_color)
            else:
                result = combine_vertical(raws, gap, bg_color)
            out = "data:image/png;base64," + base64.b64encode(result).decode()
            return {"ok": True, "data": {"data_url": out}}
        except Exception as e:
            return {"ok": False, "err": "合并失败: %s" % e}

    def combine_pick_files(self):
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.webp *.gif"),
                       ("所有文件", "*.*")])
        if not paths:
            return {"ok": False, "err": "未选择文件"}
        data_urls = []
        for p in paths:
            with open(p, "rb") as f:
                raw = f.read()
            from ..core.image_editor import open_image
            png = open_image(p)
            data_urls.append("data:image/png;base64," + base64.b64encode(png).decode())
        return {"ok": True, "data": {"data_urls": data_urls, "paths": list(paths)}}

    def combine_save(self, data_url, directory=""):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            initialdir=directory or None,
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("所有文件", "*.*")])
        if not path:
            return {"ok": False, "err": "未选择路径"}
        try:
            raw = _decode_data_url(data_url)
            with open(path, "wb") as f:
                f.write(raw)
            return {"ok": True, "data": {"path": path}}
        except Exception as e:
            return {"ok": False, "err": "保存失败: %s" % e}


def _decode_data_url(data_url):
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    return base64.b64decode(data_url)
