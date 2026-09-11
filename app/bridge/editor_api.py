"""图片编辑器桥接层。"""

import base64
import io
import os

from .base import BridgeBase


class EditorApi(BridgeBase):
    def _init_editor(self):
        pass

    def editor_open(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            filetypes=[
                ("图片文件", "*.png *.jpg *.jpeg *.bmp *.webp *.tiff *.gif"),
                ("所有文件", "*.*"),
            ])
        if not path:
            return {"ok": False, "err": "未选择文件"}
        try:
            from ..core.image_editor import open_image
            png = open_image(path)
            data_url = "data:image/png;base64," + base64.b64encode(png).decode()
            return {"ok": True, "data": {"data_url": data_url, "path": path}}
        except Exception as e:
            return {"ok": False, "err": "打开失败: %s" % e}

    def editor_save(self, data_url, path):
        try:
            raw = _decode_data_url(data_url)
            with open(path, "wb") as f:
                f.write(raw)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "err": "保存失败: %s" % e}

    def editor_save_as(self, data_url, fmt, directory=""):
        from tkinter import filedialog
        ext_map = {"png": ".png", "jpg": ".jpg", "jpeg": ".jpg",
                   "bmp": ".bmp", "webp": ".webp", "tiff": ".tiff"}
        ext = ext_map.get(fmt.lower(), ".png")
        path = filedialog.asksaveasfilename(
            initialdir=directory or None,
            defaultextension=ext,
            filetypes=[("%s 文件" % fmt.upper(), "*%s" % ext),
                       ("所有文件", "*.*")])
        if not path:
            return {"ok": False, "err": "未选择路径"}
        try:
            raw = _decode_data_url(data_url)
            if fmt.lower() != "png":
                from ..core.image_editor import convert_format
                raw = convert_format(raw, fmt)
            with open(path, "wb") as f:
                f.write(raw)
            return {"ok": True, "data": {"path": path}}
        except Exception as e:
            return {"ok": False, "err": "保存失败: %s" % e}

    def editor_apply_effect(self, data_url, effect, params=None):
        try:
            raw = _decode_data_url(data_url)
            from ..core.image_editor import apply_effect
            result = apply_effect(raw, effect, params)
            out = "data:image/png;base64," + base64.b64encode(result).decode()
            return {"ok": True, "data": {"data_url": out}}
        except Exception as e:
            return {"ok": False, "err": "应用特效失败: %s" % e}

    def editor_watermark_text(self, data_url, text, font_size=36,
                              color="#FFFFFF", opacity=128, position="bottom-right"):
        try:
            raw = _decode_data_url(data_url)
            from ..core.image_editor import add_text_watermark
            result = add_text_watermark(raw, text, int(font_size),
                                        color, int(opacity), position)
            out = "data:image/png;base64," + base64.b64encode(result).decode()
            return {"ok": True, "data": {"data_url": out}}
        except Exception as e:
            return {"ok": False, "err": "添加水印失败: %s" % e}

    def editor_watermark_image(self, data_url, wm_path, position="bottom-right", scale=0.2):
        try:
            raw = _decode_data_url(data_url)
            with open(wm_path, "rb") as f:
                wm_bytes = f.read()
            from ..core.image_editor import add_image_watermark
            result = add_image_watermark(raw, wm_bytes, position, float(scale))
            out = "data:image/png;base64," + base64.b64encode(result).decode()
            return {"ok": True, "data": {"data_url": out}}
        except Exception as e:
            return {"ok": False, "err": "添加图片水印失败: %s" % e}

    def editor_convert_format(self, data_url, fmt):
        try:
            raw = _decode_data_url(data_url)
            from ..core.image_editor import convert_format
            result = convert_format(raw, fmt)
            out = "data:image/png;base64," + base64.b64encode(result).decode()
            return {"ok": True, "data": {"data_url": out}}
        except Exception as e:
            return {"ok": False, "err": "格式转换失败: %s" % e}

    def editor_resize(self, data_url, width):
        try:
            raw = _decode_data_url(data_url)
            from ..core.image_editor import resize_image
            result = resize_image(raw, int(width))
            out = "data:image/png;base64," + base64.b64encode(result).decode()
            return {"ok": True, "data": {"data_url": out}}
        except Exception as e:
            return {"ok": False, "err": "调整大小失败: %s" % e}

    def editor_info(self, data_url):
        try:
            raw = _decode_data_url(data_url)
            from ..core.image_editor import image_info
            return {"ok": True, "data": image_info(raw)}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def editor_extract_palette(self, data_url, n_colors=8):
        try:
            raw = _decode_data_url(data_url)
            from ..core.palette import extract_palette
            colors = extract_palette(raw, int(n_colors))
            return {"ok": True, "data": colors}
        except Exception as e:
            return {"ok": False, "err": str(e)}


def _decode_data_url(data_url):
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    return base64.b64decode(data_url)
