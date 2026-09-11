"""剪贴板图片支持测试：DIB 转换、本机图片历史、局域网图片同步、回声抑制。

运行：python test_clipboard_img.py
"""

import base64
import io
import json
import os
import shutil
import struct
import tempfile
import unittest

from PIL import Image

from app.core import clipboard_sync as cs
from app.core.clipboard_sync import (
    ClipboardSync,
    hash_bytes,
    hash_text,
)

TMP = tempfile.mkdtemp(prefix="clipimg-test-")


def _png(w=64, h=48, color=(30, 120, 200)):
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_dib(w=32, h=24, color=(1, 2, 3), bpp=32, topdown=False):
    """构造 CF_DIB 内存布局（BITMAPINFOHEADER + 像素）。"""
    bp = bpp // 8
    stride = ((w * bp + 3) // 4) * 4
    pix = bytes([color[2], color[1], color[0]]) + bytes([255]) * (bp - 3)
    row = (pix * w) + b"\x00" * (stride - w * bp)
    pixels = row * h
    hdr = struct.pack("<LiiHHIIiiII", 40, w, -h if topdown else h, 1, bpp, 0, len(pixels), 0, 0, 0, 0)
    return hdr + pixels


def _dib_gen(bpp, topdown):
    return lambda: _make_dib(color=(200, 60, 60), bpp=bpp, topdown=topdown)


def _line(msg):
    return json.dumps(msg).encode("utf-8")


class TestDib(unittest.TestCase):
    def test_01_dib32_bottomup(self):
        png = cs._dib_to_png(_dib_gen(32, False)())
        self.assertIsNotNone(png)
        img = Image.open(io.BytesIO(png))
        self.assertEqual(img.size, (32, 24))

    def test_02_dib24_topdown(self):
        png = cs._dib_to_png(_dib_gen(24, True)())
        self.assertIsNotNone(png)
        img = Image.open(io.BytesIO(png))
        self.assertEqual(img.size, (32, 24))

    def test_03_dib_invalid(self):
        self.assertIsNone(cs._dib_to_png(b""))
        self.assertIsNone(cs._dib_to_png(_make_dib(bpp=16)))


class _FakeDiscovery:
    """最小发现桩：不起 UDP，advertise 可写。"""

    def __init__(self):
        self.advertise = {}

    def devices(self):
        return []

    def start(self):
        pass

    def stop(self):
        pass


class TestClipboardImage(unittest.TestCase):
    """双实例：发送端 _on_clip_image → 接收端 _handle_line → 历史/剪贴板/落盘。"""

    @classmethod
    def setUpClass(cls):
        cs.CLIP_IMG_DIR = os.path.join(TMP, "clip-imgs")  # 落盘隔离

    def _engines(self, port_a=51000, port_b=51001):
        recv = {}

        def fake_writer(png):
            recv["png"] = png
            return True

        a = ClipboardSync(
            sync_port=port_a, device_id="AAA", name="甲",
            discovery=_FakeDiscovery(), img_writer=lambda p: recv.setdefault("self", p),
        )
        b = ClipboardSync(
            sync_port=port_b, device_id="BBB", name="乙",
            discovery=_FakeDiscovery(), img_writer=fake_writer,
        )
        a.send_enabled = True
        b.recv_enabled = True
        return a, b, recv

    def test_04_local_image_history_and_disk(self):
        a, b, _ = self._engines()
        png = _png()
        a._on_clip_image(png)
        self.assertEqual(a.history[0]["kind"], "image")
        self.assertEqual(a.history[0]["hash"], hash_bytes(png))
        self.assertTrue(os.path.isfile(a.history[0]["img_path"]))

    def test_05_sync_image_remote(self):
        a, b, recv = self._engines()
        png = _png(80, 50)
        a._push_image(png, hash_bytes(png))
        # 模拟对端收到同一行
        raw = {
            "type": "clip", "id": "AAA", "name": "甲",
            "hash": hash_bytes(png), "kind": "image",
            "img": base64.b64encode(png).decode("ascii"), "ts": 0,
        }
        b._handle_line(_line(raw), "127.0.0.1")
        self.assertEqual(b.history[0]["kind"], "image")
        self.assertTrue(recv.get("png") == png, "写剪贴板调用缺失")
        self.assertTrue(os.path.isfile(b.history[0]["img_path"]))

    def test_06_echo_suppressed(self):
        a, b, recv = self._engines()
        png = _png(66, 44)
        b.copy_image_to_clipboard(png)  # 网络写入剪贴板（记录 suppress）
        b._on_clip_image(png)           # 监听再次读到同一张图 → 跳过
        self.assertEqual(len(b.history), 0)

    def test_07_remote_echo_not_resent(self):
        a, b, recv = self._engines()
        png = _png(70, 40)
        raw = {
            "type": "clip", "id": "AAA", "name": "甲",
            "hash": hash_bytes(png), "kind": "image",
            "img": base64.b64encode(png).decode("ascii"), "ts": 0,
        }
        b._handle_line(_line(raw), "127.0.0.1")
        b._handle_line(_line(raw), "127.0.0.1")  # 去重窗口内重复行 → 忽略
        self.assertEqual(len(b.history), 1)

    def test_08_text_still_works(self):
        a, b, _ = self._engines()
        b._handle_line(_line({
            "type": "clip", "id": "AAA", "name": "甲",
            "hash": hash_text("hello"), "text": "hello", "ts": 0,
        }), "127.0.0.1")
        self.assertEqual(b.history[0]["kind"], "text")
        self.assertEqual(b.history[0]["text"], "hello")

    def test_09_history_limit_respected(self):
        a, b, _ = self._engines()
        a.history_limit = 2
        for i in range(3):
            a._on_clip_image(_png(w=10 + i, h=10))
        self.assertEqual(len(a.history), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=False)
    shutil.rmtree(TMP, ignore_errors=True)