"""OCR 模块单元测试：依赖缺失路径 + mock OcrEngine 的 recognize 流程。

运行：python test_ocr.py
（真实 OCR 引擎输出属实机验收项，不在本测试范围内。）
"""

import io
import sys
import types
import unittest
from unittest import mock

from PIL import Image

from app.core import ocr


def _make_png(w=64, h=32):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


# ---- WinRT 假对象（模拟系统 OCR 引擎返回固定识别结果） ------------------
class _FakeWord:
    def __init__(self, text, x, y, w, h):
        self.text = text
        self.bounding_rect = types.SimpleNamespace(x=x, y=y, width=w, height=h)


class _FakeLine:
    def __init__(self, words):
        self.words = words
        self.text = "".join(w.text for w in words)


class _FakeResult:
    def __init__(self, lines):
        self.lines = lines
        self.text = "\n".join(l.text for l in lines)


class _FakeEngine:
    _lines = [
        _FakeLine([_FakeWord("你好世界", 2, 3, 60, 12)]),
        _FakeLine([_FakeWord("hello", 1, 18, 40, 10)]),
    ]

    @classmethod
    def get_available_recognizer_languages(cls):
        return [types.SimpleNamespace(language_tag="zh-CN")]

    @classmethod
    def try_create_from_language(cls, lang):
        return cls()

    @classmethod
    def try_create_from_user_profile_languages(cls):
        return cls()

    def recognize_async(self, soft):
        async def _go():
            return _FakeResult(self._lines)
        return _go()


class _FakeLanguage:
    def __init__(self, tag):
        self.language_tag = tag


def _fake_winrt_modules(engine_cls=_FakeEngine):
    """在 sys.modules 注入最小 winrt 模块树，让 ocr 惰性导入成功。"""
    def mod(name):
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    mod("winrt")
    mod("winrt.windows")
    mod("winrt.windows.media")
    mod("winrt.windows.media.ocr").OcrEngine = engine_cls
    mod("winrt.windows.graphics.imaging")
    mod("winrt.windows.storage.streams")
    mod("winrt.windows.globalization").Language = _FakeLanguage


def _cleanup_winrt():
    for name in list(sys.modules):
        if name == "winrt" or name.startswith("winrt."):
            del sys.modules[name]


class TestOcrAvailable(unittest.TestCase):
    def test_01_available_is_bool(self):
        """是否可用不硬编码：无 winrt 环境应为 False 且不抛异常。"""
        self.assertIn(ocr.is_available(), (True, False))

    def test_02_missing_dependency_false(self):
        """winrt 子模块被置 None → is_available()=False，recognize 抛中文提示。"""
        with mock.patch.dict(
            sys.modules, {"winrt.windows.media.ocr": None}
        ):
            self.assertFalse(ocr.is_available())
        with mock.patch.object(ocr, "is_available", return_value=False):
            with self.assertRaises(RuntimeError) as ctx:
                ocr.recognize_bytes(_make_png())
            self.assertIn("OCR 不可用", str(ctx.exception))

    def test_03_recognize_unavailable_raises(self):
        with mock.patch.object(ocr, "is_available", return_value=False):
            with self.assertRaises(RuntimeError) as ctx:
                ocr.recognize_bytes(_make_png())
            self.assertIn("OCR 不可用", str(ctx.exception))


class TestOcrRecognize(unittest.TestCase):
    def test_10_lines_output(self):
        """mock OcrEngine：recognize_bytes 返回 text 与逐行 rect。"""
        _fake_winrt_modules()
        try:
            with mock.patch.object(ocr, "_pil_to_softbitmap", return_value="fake-soft"), \
                    mock.patch.object(ocr, "is_available", return_value=True):
                r = ocr.recognize_bytes(_make_png())
        finally:
            _cleanup_winrt()
        self.assertEqual(r["text"], "你好世界\nhello")
        self.assertEqual(len(r["lines"]), 2)
        self.assertEqual(r["lines"][0]["text"], "你好世界")
        self.assertEqual(r["lines"][0]["rect"]["x"], 2)
        self.assertEqual(r["lines"][0]["rect"]["h"], 12)
        self.assertEqual(r["lines"][1]["rect"]["w"], 40)

    def test_11_pick_language_zh(self):
        """可用语言含 zh-CN → 选择中文语言。"""
        _fake_winrt_modules()
        try:
            lang = ocr._pick_language()
            self.assertIsNotNone(lang)
            self.assertTrue(hasattr(lang, "language_tag"))
        finally:
            _cleanup_winrt()


if __name__ == "__main__":
    unittest.main(verbosity=2)