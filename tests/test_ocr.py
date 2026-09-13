"""OCR 模块单元测试：依赖缺失路径 + mock OcrEngine 的 recognize 流程。

运行：python test_ocr.py
（真实 OCR 引擎输出属实机验收项，不在本测试范围内。）
"""

import io
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from app.core import ocr


def _make_png(w=64, h=32):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _make_img(w=64, h=32):
    return Image.open(io.BytesIO(_make_png(w, h)))


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


class TestPostprocess(unittest.TestCase):
    """v5.1 文本后处理：去空行/行尾空格、CJK 间空格规整、智能合并。"""

    def test_20_tidy_inline(self):
        self.assertEqual(ocr.postprocess("  你好，  世界  "), "你好，世界")
        self.assertEqual(ocr.postprocess("中文  english  混排"), "中文 english 混排")
        self.assertEqual(ocr.postprocess("A\n\n\nB"), "A\nB")  # 空行去除

    def test_21_merge_lines(self):
        # 中文断行合并：上行非句末 → 拼接
        self.assertEqual(ocr.postprocess("今天天气\n很好"), "今天天气很好")
        # 上行句末标点 → 不合并
        self.assertEqual(ocr.postprocess("今天天气好。\n很好"), "今天天气好。\n很好")
        # 下行以起始标点开头 → 不合并
        self.assertEqual(ocr.postprocess("列表如下\n、第二项"), "列表如下\n、第二项")
        # 英文行保留空格连接
        self.assertEqual(ocr.postprocess("hello\nworld"), "hello world")
        # 关闭合并：仅规整
        self.assertEqual(ocr.postprocess("今天天气\n很好", merge_lines=False),
                         "今天天气\n很好")

    def test_22_needs_space_ascii(self):
        self.assertTrue(ocr._needs_space("abc", "def"))
        self.assertFalse(ocr._needs_space("你好", "世界"))
        self.assertFalse(ocr._needs_space("", "x"))


class TestEngineDispatch(unittest.TestCase):
    """v5.1 三引擎调度：未知回退 winrt；rapid 不可用报中文；umi 走 HTTP mock。"""

    def test_30_unknown_engine_falls_back(self):
        with mock.patch.object(ocr, "is_available", return_value=False):
            with self.assertRaises(RuntimeError) as ctx:
                ocr.recognize_any(_make_img(), engine="whatever")
            self.assertIn("OCR 不可用", str(ctx.exception))

    def test_31_rapid_unavailable_message(self):
        with mock.patch.object(ocr, "rapid_available", return_value=False):
            with self.assertRaises(RuntimeError) as ctx:
                ocr.recognize_any(_make_img(), engine="rapid")
            self.assertIn("rapidocr_onnxruntime", str(ctx.exception))

    def test_32_rapid_engine(self):
        """rapid 包可用 → RapidOCR() 识别并归一化行矩形。"""
        fake_engine = mock.Mock(return_value=([
            [[[0, 0], [10, 0], [10, 8], [0, 8]], "你好", 0.9],
            [[[2, 12], [30, 12], [30, 20], [2, 20]], "hi", 0.8],
        ], [0.1, 0.1]))
        fake_mod = types.ModuleType("rapidocr_onnxruntime")
        fake_mod.RapidOCR = mock.Mock(return_value=fake_engine)
        with mock.patch.dict(sys.modules, {"rapidocr_onnxruntime": fake_mod}), \
                mock.patch.dict(sys.modules, {"numpy": mock.MagicMock()}):
            r = ocr.recognize_any(_make_img(), engine="rapid")
        self.assertEqual(r["text"], "你好\nhi")
        self.assertEqual(r["lines"][0]["rect"]["w"], 10)
        self.assertEqual(r["lines"][1]["rect"]["x"], 2)

    def test_33_umi_engine(self):
        """umi 引擎：健康检查 + HTTP 解析归一化（全程 mock 网络）。"""
        from app.core import ocr_umi

        class FakeResp:
            def __init__(self, body):
                self._body = json.dumps(body).encode("utf-8")
                self.status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self._body

        body = {"code": 100, "data": [
            {"text": "你好世界", "box": [[0, 0], [60, 0], [60, 12], [0, 12]], "score": 0.9},
            {"text": "hello", "box": [[0, 20], [40, 20], [40, 30], [0, 30]], "score": 0.8},
        ]}
        with mock.patch.object(ocr_umi, "server_ready", return_value=True), \
                mock.patch.object(ocr_umi.urllib.request, "urlopen",
                                  return_value=FakeResp(body)):
            r = ocr.recognize_any(_make_img(), engine="umi")
        self.assertEqual(r["text"], "你好世界\nhello")
        self.assertEqual(r["lines"][0]["rect"]["w"], 60)
        self.assertEqual(r["lines"][1]["rect"]["h"], 10)

    def test_34_umi_error_code(self):
        from app.core import ocr_umi

        class FakeResp:
            def __init__(self, body):
                self._body = json.dumps(body).encode("utf-8")
                self.status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self._body

        with mock.patch.object(ocr_umi, "server_ready", return_value=True), \
                mock.patch.object(ocr_umi.urllib.request, "urlopen",
                                  return_value=FakeResp({"code": 101, "message": "模型未加载"})):
            with self.assertRaises(RuntimeError) as ctx:
                ocr.recognize_any(_make_img(), engine="umi")
        self.assertIn("模型未加载", str(ctx.exception))


class TestUmiKernel(unittest.TestCase):
    """v5.1 Umi 内核管理：定位/就绪探测（不触网）。"""

    def test_40_find_kernel_missing(self):
        from app.core import ocr_umi
        r = ocr_umi.find_kernel_exe(str(Path(ocr_umi.UMI_DIR) / "nope.exe"))
        # 显式路径不存在 → 回落数据目录扫描（空目录应返回 None，不抛）
        self.assertTrue(r is None or os.path.isfile(r))

    def test_41_normalize_url_default(self):
        from app.core import ocr_umi
        self.assertTrue(ocr_umi.DEFAULT_URL.startswith("http://127.0.0.1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
