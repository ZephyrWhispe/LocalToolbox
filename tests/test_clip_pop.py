"""剪贴板弹窗（Win+V，类 Ditto）测试：热键解析 / cfg 键 / 粘贴编排 / SendInput 序列。

运行：python -m pytest test_clip_pop.py -q
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import DEFAULTS  # noqa: E402

E_TEXT1 = {"id": 1, "kind": "text", "text": "hello", "img_path": "",
           "ts": 1000.0, "pinned": False}
E_TEXT2 = {"id": 2, "kind": "text", "text": "world", "img_path": "",
           "ts": 2000.0, "pinned": False}
E_IMG = {"id": 3, "kind": "image", "text": "", "img_path": "x.png",
         "ts": 3000.0, "pinned": False}


class FakeMonitor:
    def __init__(self, entries):
        self._e = {e["id"]: e for e in entries}
        self.pasted = []

    def get_entry(self, i):
        return self._e.get(int(i))

    def paste_to_clipboard(self, i):
        self.pasted.append(int(i))
        return self._e.get(int(i)) is not None


class FakeCfg:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, k, d=None):
        return self.data.get(k, d)

    def set(self, k, v):
        self.data[k] = v


def make_api(entries=None, cfg=None):
    """ClipPopApi 的 Mixin 桩：不建真窗口，_pop["win"] 恒为 None。"""
    from app.bridge.clip_pop_api import ClipPopApi

    class Stub(ClipPopApi):
        pass

    api = Stub()
    api._init_clip_pop()
    api._clip_monitor = FakeMonitor(entries or [E_TEXT1, E_TEXT2, E_IMG])
    api.cfg = FakeCfg(cfg or {"clip_pop_autopaste": False})
    return api


class TestHotkeyPop(unittest.TestCase):
    def test_01_parse_win_v(self):
        from app.core.hotkey import parse_combo
        mods, vk, display = parse_combo("Win+V")
        self.assertIn("Win", display)
        self.assertEqual(vk, ord("V"))

    def test_02_defaults(self):
        self.assertEqual(DEFAULTS["hotkey_pop"], "Win+V")
        self.assertEqual(DEFAULTS["clip_pop_autopaste"], True)


class TestCfgSetPop(unittest.TestCase):
    def setUp(self):
        from app.bridge.bridge import Bridge
        self.Bridge = Bridge
        tmp = tempfile.mkdtemp(prefix="clippop-")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        from app.core.config import AppConfig
        self.stub = type("S", (), {})()
        self.stub.cfg = AppConfig(path=str(Path(tmp) / "config.json"))

    def set(self, key, value):
        return self.Bridge.cfg_set(self.stub, key, value)

    def test_01_hotkey_pop_normalized(self):
        r = self.set("hotkey_pop", "win+v")
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], "Win+V")
        r = self.set("hotkey_pop", "not a combo!!")
        self.assertFalse(r["ok"])

    def test_02_autopaste_bool(self):
        r = self.set("clip_pop_autopaste", "yes")
        self.assertTrue(r["ok"])
        self.assertIs(r["data"], True)


class TestPasteFlow(unittest.TestCase):
    def setUp(self):
        self.api = make_api()
        # 弹窗窗口不存在：clip_pop_hide/_pop_toast 均为安全空操作

    def test_01_paste_autopaste_off_writes_only(self):
        with mock.patch("pyperclip.copy") as pc:
            r = self.api.clip_pop_paste(1)
        self.assertTrue(r["ok"])
        self.assertFalse(r["data"]["pasted"])
        self.assertEqual(self.api._clip_monitor.pasted, [1])
        pc.assert_not_called()          # 非特殊粘贴走 monitor.paste_to_clipboard

    def test_02_plain_text_paste(self):
        with mock.patch("pyperclip.copy") as pc:
            r = self.api.clip_pop_paste(1, plain=True)
        self.assertTrue(r["ok"])
        pc.assert_called_once_with("hello")
        self.assertEqual(self.api._clip_monitor.pasted, [])  # 特殊粘贴不走 monitor

    def test_03_no_newline(self):
        e = {"id": 4, "kind": "text", "text": "a\nb\r\nc", "img_path": "",
             "ts": 1.0, "pinned": False}
        api = make_api([e])
        with mock.patch("pyperclip.copy") as pc:
            r = api.clip_pop_paste(4, no_newline=True)
        self.assertTrue(r["ok"])
        pc.assert_called_once_with("abc")

    def test_04_plain_on_image_rejected(self):
        r = self.api.clip_pop_paste(3, plain=True)
        self.assertFalse(r["ok"])
        self.assertIn("不支持", r["err"])

    def test_05_multi_paste_joins_newline(self):
        with mock.patch("pyperclip.copy") as pc:
            r = self.api.clip_pop_multi_paste([2, 1])
        self.assertTrue(r["ok"])
        pc.assert_called_once_with("world\nhello")

    def test_06_multi_paste_image_rejected(self):
        r = self.api.clip_pop_multi_paste([1, 3])
        self.assertFalse(r["ok"])
        self.assertIn("合并", r["err"])

    def test_07_missing_entry(self):
        r = self.api.clip_pop_paste(999)
        self.assertFalse(r["ok"])


class TestSendPaste(unittest.TestCase):
    def setUp(self):
        from app.core import clip_paste

        self.cp = clip_paste
        self.sent = []
        self.p_restore = mock.patch.object(self.cp, "_send_inputs",
                                           side_effect=self._cap)
        self.p_restore.start()
        self.addCleanup(self.p_restore.stop)

    def _cap(self, arr):
        for inp in arr:
            ki = inp.union.ki
            self.sent.append((ki.wVk, bool(ki.dwFlags & 0x0002)))
        return True

    def test_01_send_combo_sequence(self):
        self.assertTrue(self.cp.send_combo(ord("V"), ctrl=True))
        self.assertEqual(self.sent, [
            (0x11, False), (0x56, False), (0x56, True), (0x11, True),
        ])

    def test_02_send_paste_switches_and_sends(self):
        user32 = self.cp.user32
        with mock.patch.object(self.cp, "_ensure_foreground", return_value=True), \
             mock.patch.object(self.cp.time, "sleep"):
            self.assertTrue(self.cp.send_paste(0x1234))
        self.assertEqual(self.sent, [
            (0x11, False), (0x56, False), (0x56, True), (0x11, True),
        ])

    def test_03_send_paste_foreground_fail(self):
        with mock.patch.object(self.cp, "_ensure_foreground", return_value=False):
            self.assertFalse(self.cp.send_paste(0x1234))
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
