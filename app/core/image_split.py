"""图片拆分核心：按行列网格切割。"""

import io
import os

from PIL import Image


def split_grid(png_bytes, rows, cols):
    with Image.open(io.BytesIO(png_bytes)) as im:
        im = im.copy()
    w, h = im.size
    tile_w = w // cols
    tile_h = h // rows
    tiles = []
    for r in range(rows):
        for c in range(cols):
            left = c * tile_w
            upper = r * tile_h
            right = left + tile_w if c < cols - 1 else w
            lower = upper + tile_h if r < rows - 1 else h
            tile = im.crop((left, upper, right, lower))
            buf = io.BytesIO()
            tile.save(buf, format="PNG")
            tiles.append(buf.getvalue())
    return tiles


def split_by_size(png_bytes, tile_w, tile_h):
    with Image.open(io.BytesIO(png_bytes)) as im:
        im = im.copy()
    w, h = im.size
    tiles = []
    for y in range(0, h, tile_h):
        for x in range(0, w, tile_w):
            right = min(x + tile_w, w)
            lower = min(y + tile_h, h)
            tile = im.crop((x, y, right, lower))
            buf = io.BytesIO()
            tile.save(buf, format="PNG")
            tiles.append(buf.getvalue())
    return tiles


def save_tiles(tiles_bytes, output_dir, prefix="tile"):
    os.makedirs(output_dir, exist_ok=True)
    paths = []
    for i, tb in enumerate(tiles_bytes):
        name = "%s_%03d.png" % (prefix, i + 1)
        path = os.path.join(output_dir, name)
        with open(path, "wb") as f:
            f.write(tb)
        paths.append(path)
    return paths


def create_tiled_preview(tiles_bytes, rows, cols, gap=2, bg_color="#333333"):
    from .image_combine import _make_bg, _to_png
    imgs = [Image.open(io.BytesIO(b)) for b in tiles_bytes]
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
