# -*- coding: utf-8 -*-
"""剪贴板相同内容合并（v5.4）测试：dedup 保留规则（置顶优先 > 最新）。"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.core.clip_monitor import ClipboardMonitor  # noqa: E402


class TestClipDedup(unittest.TestCase):
    def setUp(self):
        import shutil
        from app.core import clip_monitor as cm
        self.dir = tempfile.mkdtemp(prefix="clip-dedup-")
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        # 探针隔离：真实 DATA_HOME/clip_history.db 绝不被触碰
        self._patch = mock.patch.object(cm, "DATA_HOME", self.dir)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.mon = ClipboardMonitor(limit=100, monitor_images=False)

    def tearDown(self):
        try:
            self.mon.stop()
        except Exception:
            pass

    def _add(self, text, ts_offset=0, pinned=False):
        # 直接入库（绕过剪贴板监听）
        import sqlite3
        conn = sqlite3.connect(self.mon._db_path)
        try:
            conn.execute(
                "INSERT INTO clip_history (kind, text, img_path, ts, pinned) "
                "VALUES ('text', ?, '', ?, ?)",
                (text, time.time() + ts_offset, int(pinned)))
            conn.commit()
        finally:
            conn.close()

    def _texts(self):
        return sorted(e["text"] for e in self.mon.list_entries(100))

    def test_01_no_dup_noop(self):
        self._add("a")
        self._add("b")
        self.assertEqual(self.mon.dedup(), 0)
        self.assertEqual(self._texts(), ["a", "b"])

    def test_02_keep_newest(self):
        self._add("dup", ts_offset=-30)
        self._add("dup", ts_offset=-20)
        self._add("dup", ts_offset=-10)
        self._add("other")
        removed = self.mon.dedup()
        self.assertEqual(removed, 2)
        self.assertEqual(self._texts(), ["dup", "other"])
        # 保留的必须是最新一条（ts 最大）
        kept = [e for e in self.mon.list_entries(100) if e["text"] == "dup"][0]
        self.assertAlmostEqual(kept["ts"] - time.time(), -10, delta=2)

    def test_03_pinned_wins(self):
        self._add("dup", ts_offset=-30, pinned=True)
        self._add("dup", ts_offset=-10)
        removed = self.mon.dedup()
        self.assertEqual(removed, 1)
        kept = [e for e in self.mon.list_entries(100) if e["text"] == "dup"]
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0]["pinned"])

    def test_04_images_not_merged(self):
        import sqlite3
        conn = sqlite3.connect(self.mon._db_path)
        try:
            for i in range(2):
                conn.execute(
                    "INSERT INTO clip_history (kind, text, img_path, ts, pinned) "
                    "VALUES ('image', '', ?, 0, 0)", ("img%d.png" % i,))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.mon.dedup(), 0)
        self.assertEqual(len(self.mon.list_entries(100)), 2)

    def test_05_api_bridge(self):
        """Bridge 层：监控未启动报错；启动后经 API 调用返回删除数。"""
        from app.bridge.cliphist_api import ClipHistApi
        from app.core.config import AppConfig

        class Stub(ClipHistApi):
            pass

        api = Stub()
        api.cfg = AppConfig()
        api._clip_monitor = None
        self.assertFalse(api.cliphist_dedup()["ok"])
        api._clip_monitor = self.mon
        self._add("x")
        self._add("x")
        r = api.cliphist_dedup()
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["removed"], 1)


if __name__ == "__main__":
    unittest.main()
