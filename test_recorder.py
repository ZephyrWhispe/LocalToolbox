"""GIF 录制模块单元测试：帧采集（mock ImageGrab）、自动停、退化 PNG、保存路径与类型。

运行：python test_recorder.py
"""

import shutil
import tempfile
import time
import unittest
from unittest import mock

from PIL import Image

from app.core import recorder, screenshot


def _frame(color):
    return Image.new("RGB", (16, 16), color)


class TestRecorderManager(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rec-test-")
        self.done = []
        self.states = []
        self.mgr = recorder.RecorderManager(
            on_state=lambda s: self.states.append(s),
            on_done=lambda r: self.done.append(r),
        )
        self.mgr.shot_dir = self.dir
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))

    def wait_idle(self, timeout=5.0):
        """等待采集线程退出且 on_done 已回调。"""
        t0 = time.time()
        while self.mgr.running and time.time() - t0 < timeout:
            time.sleep(0.02)
        while not self.done and time.time() - t0 < timeout:
            time.sleep(0.02)
        self.assertFalse(self.mgr.running, "采集线程未退出")
        self.assertTrue(self.done, "on_done 未回调")

    def test_01_invalid_fps(self):
        r = self.mgr.start(fps=7)
        self.assertFalse(r["ok"])
        self.assertIn("2/5/10", r["err"])

    def test_02_invalid_fps_type(self):
        r = self.mgr.start(fps="abc")
        self.assertFalse(r["ok"])
        self.assertIn("2/5/10", r["err"])

    def test_03_auto_stop_gif(self):
        with mock.patch.object(recorder, "MAX_FRAMES", 3), \
                mock.patch("PIL.ImageGrab.grab",
                           side_effect=[_frame((200, 0, 0)), _frame((0, 200, 0)), _frame((0, 0, 200))]):
            r = self.mgr.start(fps=5)
            self.assertTrue(r["ok"], r)
            self.assertTrue(self.mgr.running)
            self.wait_idle()
        d = self.done[0]
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["frames"], 3)
        self.assertTrue(d["path"], d)
        self.assertEqual(d["type"], "gif")
        self.assertTrue(os_path(d["path"]))
        with Image.open(d["path"]) as im:
            self.assertEqual(im.n_frames, 3)          # GIF 帧数一致
            self.assertEqual(im.info["duration"], 200)  # 5fps → 200ms/帧

    def test_04_single_frame_degrade_png(self):
        with mock.patch.object(recorder, "MAX_FRAMES", 1), \
                mock.patch("PIL.ImageGrab.grab", return_value=_frame((0, 200, 0))):
            self.mgr.start(fps=5)
            self.wait_idle()
        d = self.done[0]
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["type"], "png")
        self.assertTrue(d["path"].endswith(".png"))

    def test_05_double_start_denied(self):
        with mock.patch("PIL.ImageGrab.grab", return_value=_frame((0, 0, 200))):
            self.mgr.start(fps=5)
            r2 = self.mgr.start(fps=5)
            self.assertFalse(r2["ok"])
            self.assertIn("进行中", r2["err"])
            self.mgr.stop()
            self.wait_idle()

    def test_06_stop_then_done(self):
        with mock.patch.object(recorder, "MAX_FRAMES", 1000), \
                mock.patch("PIL.ImageGrab.grab", return_value=_frame((1, 2, 3))):
            self.mgr.start(fps=5)
            # 等首帧入队后再 stop（避免空队列竞态）
            t0 = time.time()
            while not self.mgr._frames and time.time() - t0 < 3:
                time.sleep(0.01)
            r = self.mgr.stop()
            self.assertTrue(r["ok"], r)
            self.wait_idle()
        self.assertTrue(self.done[0]["ok"], self.done[0])
        self.assertTrue(os_path(self.done[0].get("path")))

    def test_07_stop_when_idle(self):
        r = self.mgr.stop()
        self.assertFalse(r["ok"])
        self.assertIn("没有进行中的录制", r["err"])

    def test_08_state_shape(self):
        st = self.mgr.get_state()
        self.assertIn("running", st)
        self.assertIn("fps", st)
        self.assertIn("frames", st)
        self.assertIn("elapsed", st)
        self.assertFalse(st["running"])

    def test_09_gif_region_crop(self):
        """GIF 区域录制：帧按 region box 裁剪为 20×20（ShareX 式区域 GIF）。"""
        full = Image.new("RGB", (100, 100), (50, 100, 150))
        with mock.patch("PIL.ImageGrab.grab", return_value=full), \
                mock.patch.object(recorder, "MAX_FRAMES", 3):
            r = self.mgr.start(fps=5, fmt="gif", region=(10, 10, 20, 20))
            self.assertTrue(r["ok"], r)
            self.wait_idle()
        d = self.done[0]
        self.assertTrue(d["ok"], d)
        with Image.open(d["path"]) as im:
            im.seek(0)
            self.assertEqual(im.size, (20, 20))

    def test_10_gif_invalid_region(self):
        r = self.mgr.start(fps=5, fmt="gif", region=(0, 0, 0, 10))
        self.assertFalse(r["ok"])
        self.assertIn("区域", r["err"])


def os_path(p):
    import os
    return p and os.path.isfile(p)


class TestSaveFrames(unittest.TestCase):
    def test_10_frames_to_gif_duration_fps(self):
        d = tempfile.mkdtemp(prefix="rec-save-")
        try:
            frames = [_frame((i, 0, 0)) for i in range(3)]
            path = screenshot.save_frames(d, frames, 10)
            self.assertTrue(path.endswith(".gif"), path)
            with Image.open(path) as im:
                self.assertEqual(im.n_frames, 3)
                self.assertEqual(im.info["duration"], 100)  # 10fps → 100ms
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_11_single_frame_png(self):
        d = tempfile.mkdtemp(prefix="rec-save-")
        try:
            path = screenshot.save_frames(d, [_frame((0, 0, 0))], 5)
            self.assertTrue(path.endswith(".png"), path)
            self.assertTrue(os_path(path))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_12_unique_names(self):
        d = tempfile.mkdtemp(prefix="rec-save-")
        try:
            p1 = screenshot.save_frames(d, [_frame((1, 1, 1))], 5)
            p2 = screenshot.save_frames(d, [_frame((2, 2, 2))], 5)
            self.assertNotEqual(p1, p2)
            self.assertTrue(p2.endswith("_001.png"), p2)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)