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