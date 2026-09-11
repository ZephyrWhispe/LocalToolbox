"""独立图片编辑器核心：打开、特效、水印、格式转换、保存。"""

import io
import os

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


def open_image(path):
    with Image.open(path) as im:
        im = im.convert("RGBA") if im.mode != "RGBA" else im.copy()
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


def apply_effect(png_bytes, effect, params=None):
    params = params or {}
    with Image.open(io.BytesIO(png_bytes)) as im:
        im = im.copy()
        if effect == "blur":
            r = float(params.get("radius", 2))
            im = im.filter(ImageFilter.GaussianBlur(radius=r))
        elif effect == "sharpen":
            f = float(params.get("factor", 1.5))
            im = ImageEnhance.Sharpness(im).enhance(f)
        elif effect == "brightness":
            f = float(params.get("factor", 1.2))
            im = ImageEnhance.Brightness(im).enhance(f)
        elif effect == "contrast":
            f = float(params.get("factor", 1.3))
            im = ImageEnhance.Contrast(im).enhance(f)
        elif effect == "saturation":
            f = float(params.get("factor", 1.5))
            im = ImageEnhance.Color(im).enhance(f)
        elif effect == "grayscale":
            im = im.convert("L").convert("RGBA")
        elif effect == "invert":
            if im.mode == "RGBA":
                r, g, b, a = im.split()
                rgb = Image.merge("RGB", (r, g, b))
                from PIL import ImageOps
                rgb = ImageOps.invert(rgb)
                im = Image.merge("RGBA", (*rgb.split(), a))
            else:
                from PIL import ImageOps
                im = ImageOps.invert(im).convert("RGBA")
        elif effect == "sepia":
            gray = im.convert("L")
            sepia = Image.merge("RGBA", (
                gray.point(lambda x: min(255, int(x * 1.2))),
                gray.point(lambda x: int(x * 1.0)),
                gray.point(lambda x: int(x * 0.8)),
                im.split()[-1] if im.mode == "RGBA" else Image.new("L", im.size, 255),
            ))
            im = sepia
        elif effect == "round_corners":
            r = int(params.get("radius", 20))
            w, h = im.size
            mask = Image.new("L", (w, h), 0)
            draw = ImageDraw.Draw(mask)
            draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=r, fill=255)
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            im.putalpha(mask)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


def add_text_watermark(png_bytes, text, font_size=36, color="#FFFFFF",
                       opacity=128, position="bottom-right"):
    with Image.open(io.BytesIO(png_bytes)) as im:
        im = im.convert("RGBA").copy()
        overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except (OSError, IOError):
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        w, h = im.size
        margin = 16
        positions = {
            "top-left": (margin, margin),
            "top-right": (w - tw - margin, margin),
            "bottom-left": (margin, h - th - margin),
            "bottom-right": (w - tw - margin, h - th - margin),
            "center": ((w - tw) // 2, (h - th) // 2),
        }
        x, y = positions.get(position, positions["bottom-right"])
        c = _parse_color(color, opacity)
        draw.text((x, y), text, fill=c, font=font)
        result = Image.alpha_composite(im, overlay)
        buf = io.BytesIO()
        result.save(buf, format="PNG")
        return buf.getvalue()


def add_image_watermark(png_bytes, wm_bytes, position="bottom-right", scale=0.2):
    with Image.open(io.BytesIO(png_bytes)) as im, Image.open(io.BytesIO(wm_bytes)) as wm:
        im = im.convert("RGBA").copy()
        wm = wm.convert("RGBA")
        w, h = im.size
        new_w = int(w * scale)
        new_h = int(wm.height * new_w / wm.width)
        wm = wm.resize((new_w, new_h), Image.LANCZOS)
        margin = 16
        positions = {
            "top-left": (margin, margin),
            "top-right": (w - new_w - margin, margin),
            "bottom-left": (margin, h - new_h - margin),
            "bottom-right": (w - new_w - margin, h - new_h - margin),
            "center": ((w - new_w) // 2, (h - new_h) // 2),
        }
        x, y = positions.get(position, positions["bottom-right"])
        im.paste(wm, (x, y), wm)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


def convert_format(png_bytes, target_fmt):
    fmt = target_fmt.upper().lstrip(".")
    fmt_map = {"JPG": "JPEG", "TIF": "TIFF", "WEBP": "WEBP"}
    fmt = fmt_map.get(fmt, fmt)
    with Image.open(io.BytesIO(png_bytes)) as im:
        if fmt == "JPEG" and im.mode == "RGBA":
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[3])
            im = bg
        elif fmt == "JPEG" and im.mode != "RGB":
            im = im.convert("RGB")
        buf = io.BytesIO()
        save_kwargs = {}
        if fmt == "JPEG":
            save_kwargs["quality"] = 92
        elif fmt == "WEBP":
            save_kwargs["quality"] = 90
        im.save(buf, format=fmt, **save_kwargs)
        return buf.getvalue()


def resize_image(png_bytes, target_width):
    with Image.open(io.BytesIO(png_bytes)) as im:
        im = im.copy()
        w, h = im.size
        ratio = target_width / w
        new_h = int(h * ratio)
        im = im.resize((target_width, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


def image_info(png_bytes):
    with Image.open(io.BytesIO(png_bytes)) as im:
        return {
            "width": im.width,
            "height": im.height,
            "mode": im.mode,
            "format": im.format or "PNG",
            "size_bytes": len(png_bytes),
        }


def _parse_color(hex_str, alpha=255):
    hex_str = hex_str.lstrip("#")
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    return (r, g, b, alpha)
