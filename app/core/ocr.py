"""OCR 识别：Windows.Media.Ocr（系统原生引擎）+ winrt-Python 绑定。

惰性引入 winrt 包，缺失或平台不可用时 is_available()=False，功能自动禁用。
需要 Windows 10 1803+ 与对应语言的 OCR 语言包（识别 zh-CN 前需系统安装中文
语言包，缺失时回退用户配置语言）。不依赖网络与云服务。
"""

import io

try:
    import PIL.Image as _PILImage
except Exception:  # pragma: no cover
    _PILImage = None

_UNAVAILABLE_MSG = (
    "OCR 不可用：缺少 winrt-Windows.Media.Ocr 依赖或系统未安装 OCR 语言包"
    "（Win10 1803+ 自带引擎；中文识别需在系统设置安装中文语言包）。"
)


def is_available():
    """winrt OCR 依赖是否可用（惰性探测，失败不抛异常）。"""
    try:
        from winrt.windows.media.ocr import OcrEngine  # noqa: F401
        from winrt.windows.graphics.imaging import (  # noqa: F401
            SoftwareBitmap,
            BitmapPixelFormat,
            BitmapAlphaMode,
        )
        from winrt.windows.storage.streams import (  # noqa: F401
            DataWriter,
            InMemoryRandomAccessStream,
        )
        return True
    except Exception:
        return False


def _block_on(awaitable):
    """在同步上下文阻塞等待 WinRT 异步操作返回结果。"""
    import asyncio

    try:
        asyncio.get_running_loop()
        return asyncio.run_coroutine_threadsafe(
            _await_result(awaitable), asyncio.new_event_loop()
        ).result()
    except RuntimeError:
        return asyncio.run(_await_result(awaitable))


async def _await_result(awaitable):
    return await awaitable


def _pil_to_softbitmap(img):
    """PIL Image → WinRT SoftwareBitmap（RGBA8/STRAIGHT）。

    构造空 SoftwareBitmap 后 copy_from_buffer 直填 RGBA 像素，
    规避 BitmapDecoder.create_async 对 InMemoryRandomAccessStream 的
    E_INVALIDARG（winrt 3.x 下 DataWriter→stream→decoder 链不稳定）。
    """
    from winrt.windows.graphics.imaging import (
        SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode)
    from winrt.windows.storage.streams import DataWriter

    if img.mode != "RGBA":
        img = img.convert("RGBA")
    sb = SoftwareBitmap(
        BitmapPixelFormat.RGBA8, img.width, img.height, BitmapAlphaMode.STRAIGHT)
    writer = DataWriter()
    writer.write_bytes(img.tobytes())
    sb.copy_from_buffer(writer.detach_buffer())
    return sb


def _pick_language():
    """选用 zh-CN 识别；未安装中文语言包时回退用户配置语言（None=用户配置）。

    winrt 3.x 的 OcrEngine 未暴露 get_available_recognizer_languages，
    改为对候选标签逐个 try_create_from_language 探测（返回非 None 即可用），
    不依赖语言枚举 API。
    """
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language

    for tag in ("zh-CN", "zh-Hans-CN", "zh-Hans", "zh"):
        try:
            eng = OcrEngine.try_create_from_language(Language(tag))
        except Exception:
            eng = None
        if eng is not None:
            return Language(tag)
    return None


def recognize(img, lang=None):
    """识别 PIL Image 中的文字。

    返回 {"text": 全文, "lines": [{"text","rect":{"x","y","w","h"}}]}；
    不可用抛 RuntimeError（中文原因）。
    """
    if not is_available():
        raise RuntimeError(_UNAVAILABLE_MSG)
    from winrt.windows.media.ocr import OcrEngine

    target = lang or _pick_language()
    engine = (
        OcrEngine.try_create_from_language(target)
        if target is not None
        else OcrEngine.try_create_from_user_profile_languages()
    )
    if engine is None:
        raise RuntimeError(_UNAVAILABLE_MSG)
    soft = _pil_to_softbitmap(img)
    result = _block_on(engine.recognize_async(soft))
    lines = []
    for line in result.lines:
        # winrt 3.x：OcrLine 无 bounding_rect，行矩形由 words 聚合；
        # 行文本按字符类型重建（Windows OCR 中文每个字拆一个 word 且带尾随空格）
        lines.append({
            "text": _line_text(line.words),
            "rect": _line_rect(line),
        })
    return {"text": "\n".join(ln["text"] for ln in lines), "lines": lines}


def _is_ascii_alnum(ch):
    return ch.isascii() and (ch.isalnum() or ch in "/._+-")


def _needs_space(a, b):
    """相邻两段文本之间是否需要空格（ASCII 字母数字连体相邻时保留）。"""
    if not a or not b:
        return False
    return _is_ascii_alnum(a[-1]) and _is_ascii_alnum(b[0])


def _line_text(words):
    """按词重建行文本：去尾随空格，非 ASCII 相邻字符不加空格。"""
    parts = [w.text.rstrip(" ") for w in words if w.text.strip(" ")]
    buf = ""
    for p in parts:
        buf += (" " if _needs_space(buf, p) else "") + p
    return buf


def _line_rect(line):
    """聚合 OcrLine 内 words 的 bounding_rect 得整行矩形（无词时返回空矩形）。"""
    xs, ys, x2s, y2s = [], [], [], []
    for w in line.words:
        r = w.bounding_rect
        xs.append(r.x)
        ys.append(r.y)
        x2s.append(r.x + r.width)
        y2s.append(r.y + r.height)
    if not xs:
        return {"x": 0, "y": 0, "w": 0, "h": 0}
    x, y = min(xs), min(ys)
    return {"x": x, "y": y, "w": max(x2s) - x, "h": max(y2s) - y}


def recognize_bytes(png_bytes, lang=None):
    """识别 PNG 字节（data URL 解码后场景）。返回同 recognize()。"""
    if _PILImage is None:
        raise RuntimeError("PIL 不可用，无法解码图片")
    img = _PILImage.open(io.BytesIO(png_bytes))
    return recognize(img, lang=lang)


# ---------------------------------------------------------------------------
# v5.1 引擎调度层：winrt（内置）/ rapid（本地 RapidOCR 包）/ umi（Umi-OCR HTTP）
# ---------------------------------------------------------------------------

_ENGINES = ("winrt", "rapid", "umi")
_UPSCALE_MIN_W = 1000


def rapid_available():
    """本地 RapidOCR python 包是否可用（exe 默认不带，源码运行 pip 安装即生效）。"""
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def engine_status():
    """各引擎可用性（设置页/OCR 页状态卡用）。"""
    return {
        "winrt": is_available(),
        "rapid": rapid_available(),
    }


def _upscale_small(img):
    """小图放大 2x（winrt/rapid 对小字号识别率明显提升），宽 ≥1000px 不动。"""
    if _PILImage is None or img.width >= _UPSCALE_MIN_W:
        return img
    try:
        return img.resize((img.width * 2, img.height * 2), _PILImage.LANCZOS)
    except Exception:
        return img


def _recognize_winrt(img, lang=None):
    if not is_available():
        raise RuntimeError(_UNAVAILABLE_MSG)
    return recognize(_upscale_small(img), lang=lang)


def _recognize_rapid(img):
    if not rapid_available():
        raise RuntimeError(
            "RapidOCR 内核不可用：源码运行请先 pip install rapidocr_onnxruntime"
            "（打包版请改用 winrt 引擎或下载 Umi-OCR 内核）")
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    global _RAPID_ENGINE
    try:
        _RAPID_ENGINE
    except NameError:
        _RAPID_ENGINE = RapidOCR()
    arr = np.array(_upscale_small(img.convert("RGB")))
    result, _elapse = _RAPID_ENGINE(arr)
    lines = []
    for item in result or []:
        box, text, _score = item[0], item[1], item[2]
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        lines.append({
            "text": str(text),
            "rect": {"x": int(min(xs)), "y": int(min(ys)),
                     "w": int(max(xs) - min(xs)), "h": int(max(ys) - min(ys))},
        })
    return {"text": "\n".join(ln["text"] for ln in lines), "lines": lines}


def recognize_any(img, engine="winrt", lang=None, umi_opts=None):
    """统一识别入口：按引擎分发；未知引擎回退 winrt；失败抛 RuntimeError（中文）。

    umi_opts：{"url","exe_path","autostart"}（仅 umi 引擎使用，由桥接层从 cfg 组装）。
    """
    engine = str(engine or "winrt")
    if engine == "rapid":
        return _recognize_rapid(img)
    if engine == "umi":
        from .ocr_umi import recognize_umi
        return recognize_umi(img, **(umi_opts or {}))
    return _recognize_winrt(img, lang=lang)


# -- 文本后处理 ---------------------------------------------------------------

_CJK = r"\u4e00-\u9fff\u3400-\u4dbf"
_SENT_END = "。！？；…?"
_NO_MERGE_START = "，、；：）」』】》〉"


def _tidy_inline(line):
    """行内规整：去首尾空格、去除 CJK 字符（含全角标点）之间的空格。"""
    import re
    line = str(line).replace("\u3000", " ").strip()
    if not line:
        return ""
    # 汉字 + CJK 标点 + 全角符号之间的空白去掉（OCR 常见）；中英之间保留
    cjk = r"\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff01-\uff5e\u2018\u2019\u201c\u201d"
    line = re.sub(r"(?<=[%s])\s+(?=[%s])" % (cjk, cjk), "", line)
    line = re.sub(r"\s{2,}", " ", line)
    return line


def _should_merge(prev, nxt):
    """中文相邻行合并判定：上行不以句末标点收尾，下行不以起始标点开头。"""
    if not prev or not nxt:
        return False
    if prev[-1] in _SENT_END:
        return False
    if nxt[0] in _NO_MERGE_START or nxt[0] in _SENT_END:
        return False
    return True


def postprocess(text, merge_lines=True):
    """识别文本后处理：去空行/行首尾空格、CJK 间空格规整、中文相邻行智能合并。

    空行视为段落分隔（不跨空行合并）；纯英文/代码截图建议关掉合并。
    """
    raw_lines = str(text or "").replace("\r\n", "\n").split("\n")
    lines = [_tidy_inline(l) for l in raw_lines]
    merged = []
    prev_empty = True
    for l in lines:
        if not l:
            prev_empty = True
            continue
        if merge_lines and merged and not prev_empty \
                and _should_merge(merged[-1], l):
            merged[-1] = merged[-1] + (" " if _needs_space(merged[-1], l) else "") + l
        else:
            merged.append(l)
        prev_empty = False
    return "\n".join(merged)