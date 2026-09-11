"""图片合并核心：垂直/水平/网格拼接。"""

import io

from PIL import Image


def combine_vertical(images_bytes, gap=0, bg_color="#FFFFFF"):
    imgs = [Image.open(io.BytesIO(b)) for b in images_bytes]
    max_w = max(im.width for im in imgs)
    total_h = sum(im.height for im in imgs) + gap * (len(imgs) - 1)
    bg = _make_bg(max_w, total_h, bg_color)
    y = 0
    for im in imgs:
        bg.paste(im, (0, y))
        y += im.height + gap
    return _to_png(bg)


def combine_horizontal(images_bytes, gap=0, bg_color="#FFFFFF"):
    imgs = [Image.open(io.BytesIO(b)) for b in images_bytes]
    max_h = max(im.height for im in imgs)
    total_w = sum(im.width for im in imgs) + gap * (len(imgs) - 1)
    bg = _make_bg(total_w, max_h, bg_color)
    x = 0
    for im in imgs:
        bg.paste(im, (x, 0))
        x += im.width + gap
    return _to_png(bg)


def combine_grid(images_bytes, cols, gap=0, bg_color="#FFFFFF"):
    imgs = [Image.open(io.BytesIO(b)) for b in images_bytes]
    rows = (len(imgs) + cols - 1) // cols
    cell_w = max(im.width for im in imgs)
    cell_h = max(im.height for im in imgs)
    total_w = cell_w * cols + gap * (cols - 1)
    total_h = cell_h * rows + gap * (rows - 1)
    bg = _make_bg(total_w, total_h, bg_color)
    for i, im in enumerate(imgs):
        r, c = divmod(i, cols)
        x = c * (cell_w + gap)
        y = r * (cell_h + gap)
        bg.paste(im, (x, y))
    return _to_png(bg)


def _make_bg(w, h, color):
    color = color.lstrip("#")
    r = int(color[0:2], 16)
    g = int(color[2:4], 16)
    b = int(color[4:6], 16)
    return Image.new("RGBA", (w, h), (r, g, b, 255))


def _to_png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
