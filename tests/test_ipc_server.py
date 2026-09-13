# -*- coding: utf-8 -*-
"""本地 IPC 只读接口（v5.4 二期）测试：仅回环、默认关、端点行为。

运行：python -m pytest tests/test_ipc_server.py -q
"""

import json
import shutil
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from app.core import ipc_server  # noqa: E402


class TestIpcServer(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ipc-")
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self.addCleanup(ipc_server.stop)
        ipc_server.stop()

    def _get(self, path, port):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_01_start_ping_and_stop(self):
        port = ipc_server.start(0)   # 0 = 随机空闲端口
        self.assertTrue(ipc_server.running())
        status, body = self._get("/api/v1/ping", port)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["app"], "LocalToolbox")
        ipc_server.stop()
        self.assertFalse(ipc_server.running())
        with self.assertRaises(Exception):
            self._get("/api/v1/ping", port)

    def test_02_apps_endpoint_uses_cache(self):
        from app.core import appscan
        with mock.patch.object(appscan, "cache_get", return_value=[]):
            port = ipc_server.start(0)
            status, body = self._get("/api/v1/apps", port)
        self.assertTrue(body["ok"])
        self.assertTrue(body["cached"])
        self.assertEqual(body["items"], [])

    def test_03_unknown_endpoint_404_and_post_blocked(self):
        port = ipc_server.start(0)
        # urllib 对 4xx 抛 HTTPError
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/v1/nothing", port)
        self.assertEqual(ctx.exception.code, 404)
        # POST 一律 405（只读）
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v1/ping", method="POST", data=b"")
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("POST 应被拒绝")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 405)

    def test_04_loopback_only(self):
        port = ipc_server.start(0)
        # 服务地址必须是 127.0.0.1（不暴露局域网）
        srv = ipc_server._server
        self.assertEqual(srv.server_address[0], "127.0.0.1")

    def test_05_start_idempotent(self):
        p1 = ipc_server.start(0)
        p2 = ipc_server.start(0)
        self.assertEqual(p1, p2)   # 已运行时返回原端口而非重复启动


if __name__ == "__main__":
    unittest.main()
