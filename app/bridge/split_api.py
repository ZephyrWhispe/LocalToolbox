"""图片拆分桥接层。"""

import base64
import os
import tempfile

from .base import BridgeBase


class SplitApi(BridgeBase):
    def _init_split(self):
        pass

    def split_pick_file(self):
        try:
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                title="选择图片",
                filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.webp"), ("所有文件", "*.*")],
            )
            if not path:
                return {"ok": False, "err": "未选择文件"}
            with open(path, "rb") as f:
                raw = f.read()
            data_url = "data:image/png;base64," + base64.b64encode(raw).decode()
            return {"ok": True, "data": {"data_url": data_url}}
        except Exception as e:
            return {"ok": False, "err": "读取失败: %s" % e}

    def split_image(self, data_url, rows=2, cols=2):
        try:
            raw = _decode_data_url(data_url)
            from ..core.image_split import split_grid
            tiles = split_grid(raw, int(rows), int(cols))
            data_urls = ["data:image/png;base64," + base64.b64encode(t).decode()
                         for t in tiles]
            return {"ok": True, "data": {"tiles": data_urls,
                                          "count": len(tiles)}}
        except Exception as e:
            return {"ok": False, "err": "拆分失败: %s" % e}

    def split_copy_tile(self, data_url):
        try:
            raw = _decode_data_url(data_url)
            from ..core.screenshot import copy_png_to_clipboard
            copy_png_to_clipboard(raw)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "err": "复制失败: %s" % e}

    def split_save_tile(self, data_url):
        try:
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(
                defaultextension=".png",
                filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("所有文件", "*.*")],
            )
            if not path:
                return {"ok": False, "err": "未选择路径"}
            raw = _decode_data_url(data_url)
            with open(path, "wb") as f:
                f.write(raw)
            return {"ok": True, "data": {"path": path}}
        except Exception as e:
            return {"ok": False, "err": "保存失败: %s" % e}

    def split_save_all(self, tiles_data_urls):
        try:
            from tkinter import filedialog
            output_dir = filedialog.askdirectory(title="选择输出目录")
            if not output_dir:
                return {"ok": False, "err": "未选择目录"}
            raw_list = [_decode_data_url(url) for url in tiles_data_urls]
            from ..core.image_split import save_tiles
            paths = save_tiles(raw_list, output_dir)
            return {"ok": True, "data": {"paths": paths, "count": len(paths), "dir": output_dir}}
        except Exception as e:
            return {"ok": False, "err": "保存失败: %s" % e}

    def split_save(self, data_url, rows=2, cols=2, output_dir=""):
        if not output_dir:
            from tkinter import filedialog
            output_dir = filedialog.askdirectory(title="选择输出目录")
            if not output_dir:
                return {"ok": False, "err": "未选择目录"}
        try:
            raw = _decode_data_url(data_url)
            from ..core.image_split import split_grid, save_tiles
            tiles = split_grid(raw, int(rows), int(cols))
            paths = save_tiles(tiles, output_dir)
            return {"ok": True, "data": {"paths": paths, "count": len(paths)}}
        except Exception as e:
            return {"ok": False, "err": "保存失败: %s" % e}


def _decode_data_url(data_url):
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    return base64.b64decode(data_url)
