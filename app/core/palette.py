"""调色板提取核心：从图片中提取主色调（k-means 聚类）。"""

import io

from PIL import Image


def extract_palette(png_bytes, n_colors=8):
    try:
        import cv2
        import numpy as np
        return _extract_cv2(png_bytes, n_colors, cv2, np)
    except ImportError:
        return _extract_pillow(png_bytes, n_colors)


def _extract_cv2(png_bytes, n_colors, cv2, np):
    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return []
    img = cv2.resize(img, (150, 150))
    pixels = img.reshape(-1, 3).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    flags = cv2.KMEANS_PP_CENTERS
    _, labels, centers = cv2.kmeans(
        pixels, n_colors, None, criteria, 5, flags)
    unique, counts = np.unique(labels, return_counts=True)
    total = counts.sum()
    colors = []
    for idx in np.argsort(-counts):
        bgr = centers[idx].astype(int)
        count = int(counts[idx])
        r, g, b = int(bgr[2]), int(bgr[1]), int(bgr[0])
        hex_str = "#%02X%02X%02X" % (r, g, b)
        colors.append({
            "hex": hex_str, "rgb": [r, g, b],
            "count": count, "percent": round(count / total * 100, 1),
        })
    return colors


def _extract_pillow(png_bytes, n_colors):
    with Image.open(io.BytesIO(png_bytes)) as im:
        im = im.convert("RGB").resize((150, 150))
        im = im.quantize(colors=n_colors, method=Image.Quantize.MEDIANCUT)
        palette = im.getpalette()
    colors = []
    total = 150 * 150
    for i in range(n_colors):
        offset = i * 3
        r, g, b = palette[offset], palette[offset + 1], palette[offset + 2]
        hex_str = "#%02X%02X%02X" % (r, g, b)
        colors.append({
            "hex": hex_str, "rgb": [r, g, b],
            "count": total // n_colors,
            "percent": round(100.0 / n_colors, 1),
        })
    return colors
