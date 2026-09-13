# -*- coding: utf-8 -*-
"""自定义上传目标（v5.4 O2）测试：validate_target / _extract_url /
upload_to_target（multipart 与 json，本地 HTTP 服务核验）/ UploadApi CRUD。

运行：python -m pytest tests/test_upload_targets.py -q
"""

import base64
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.core.image_upload import (  # noqa: E402
    ImageUploader, _extract_url, validate_target,
)


def _png_bytes():
    # 最小 PNG 头（不必是合法图片，仅用于字节核验）
    return b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes" * 4


class TestValidateTarget(unittest.TestCase):
    def test_01_ok_defaults(self):
        t = validate_target({"name": "测试", "url": "https://x.com/up"})
        self.assertEqual(t["name"], "测试")
        self.assertEqual(t["method"], "POST")
        self.assertEqual(t["body"], "multipart")
        self.assertEqual(t["file_field"], "file")
        self.assertEqual(t["headers"], {})
        self.assertEqual(t["arguments"], {})

    def test_02_empty_name(self):
        with self.assertRaises(ValueError):
            validate_target({"name": "  ", "url": "https://x.com"})

    def test_03_bad_url(self):
        with self.assertRaises(ValueError):
            validate_target({"name": "a", "url": "ftp://x.com"})
        with self.assertRaises(ValueError):
            validate_target({"name": "a", "url": ""})

    def test_04_bad_method_body(self):
        with self.assertRaises(ValueError):
            validate_target({"name": "a", "url": "https://x.com", "method": "DELETE"})
        with self.assertRaises(ValueError):
            validate_target({"name": "a", "url": "https://x.com", "body": "xml"})

    def test_05_dict_headers_normalized(self):
        t = validate_target({"name": "a", "url": "https://x.com",
                             "headers": ["not-a-dict"]})
        self.assertEqual(t["headers"], {})


class TestExtractUrl(unittest.TestCase):
    def test_01_json_dot_path(self):
        body = json.dumps({"data": {"url": "https://a.b/c.png"}})
        self.assertEqual(_extract_url(body, "data.url"), "https://a.b/c.png")

    def test_02_fallback_common_paths(self):
        self.assertEqual(_extract_url('{"url":"https://a/1"}'), "https://a/1")
        self.assertEqual(_extract_url('{"link":"https://a/2"}'), "https://a/2")

    def test_03_regex_capture(self):
        body = 'err_msg ok, "url":"https://a.b/x.png", tail'
        self.assertEqual(_extract_url(body, "", r'"url":"(.+?)"'),
                         "https://a.b/x.png")

    def test_04_plain_text(self):
        self.assertEqual(_extract_url("https://a.b/pic.png"),
                         "https://a.b/pic.png")

    def test_05_no_hit(self):
        self.assertEqual(_extract_url("server error"), "")


class _Handler(BaseHTTPRequestHandler):
    """记录最近一次请求，并按 mode 返回 JSON/纯文本响应。"""

    last = {}
    mode = "json"          # json | plain
    json_path = "data.url"

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        _Handler.last = {
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        }

    def _reply(self):
        if _Handler.mode == "json":
            payload = json.dumps({"data": {"url": "https://test.local/ok.png"}})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            payload = "uploaded https://test.local/plain.png done"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(payload.encode("utf-8"))

    def do_POST(self):
        self._record()
        self._reply()

    def do_PUT(self):
        self._record()
        self._reply()

    def log_message(self, *a):  # 静默
        pass


class TestUploadToTarget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.up = ImageUploader()
        # 探针隔离：不写真实 upload_history.db
        self.up._save_history = lambda *a, **k: None

    def test_01_multipart(self):
        _Handler.mode = "json"
        target = {"name": "t1", "url": "http://127.0.0.1:%d/up" % self.port,
                  "method": "POST", "body": "multipart", "file_field": "image",
                  "arguments": {"token": "abc"}}
        r = self.up.upload_to_target(_png_bytes(), target)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["data"]["url"], "https://test.local/ok.png")
        req = _Handler.last
        self.assertEqual(req["method"], "POST")
        ct = req["headers"].get("Content-Type", "")
        self.assertIn("multipart/form-data", ct)
        self.assertIn(b'name="image"', req["body"])
        self.assertIn(b'name="token"', req["body"])
        self.assertIn(_png_bytes(), req["body"])

    def test_02_json_body(self):
        _Handler.mode = "json"
        target = {"name": "t2", "url": "http://127.0.0.1:%d/up" % self.port,
                  "method": "POST", "body": "json", "file_field": "photo",
                  "arguments": {"appid": "x"}}
        r = self.up.upload_to_target(_png_bytes(), target)
        self.assertTrue(r["ok"], r)
        payload = json.loads(_Handler.last["body"])
        self.assertEqual(payload["appid"], "x")
        self.assertEqual(base64.b64decode(payload["photo"]), _png_bytes())
        self.assertIn("filename", payload)

    def test_03_plain_text_regex(self):
        _Handler.mode = "plain"
        target = {"name": "t3", "url": "http://127.0.0.1:%d/up" % self.port,
                  "url_regex": r"(https://\S+\.png)"}
        r = self.up.upload_to_target(_png_bytes(), target)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["data"]["url"], "https://test.local/plain.png")

    def test_04_invalid_target(self):
        r = self.up.upload_to_target(_png_bytes(), {"name": "", "url": ""})
        self.assertFalse(r["ok"])

    def test_05_extract_miss(self):
        _Handler.mode = "plain"
        target = {"name": "t5", "url": "http://127.0.0.1:%d/up" % self.port,
                  "url_path": "no.such.path"}
        r = self.up.upload_to_target(_png_bytes(), target)
        self.assertFalse(r["ok"])
        self.assertIn("提取", r["err"])


class TestUploadApiCrud(unittest.TestCase):
    def _api(self):
        from app.bridge.upload_api import UploadApi

        class Stub(UploadApi):
            pass

        api = Stub()
        api.cfg = _FakeCfg({"upload_targets": []})
        return api

    def test_01_add_and_list(self):
        api = self._api()
        r = api.upload_targets_add({"name": "a", "url": "https://x.com/u"})
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["data"]), 1)
        r = api.upload_targets_list()
        self.assertEqual(r["data"][0]["name"], "a")

    def test_02_add_invalid(self):
        api = self._api()
        r = api.upload_targets_add({"name": "", "url": "nope"})
        self.assertFalse(r["ok"])
        r = api.upload_targets_add({"name": "a", "url": "nope"})
        self.assertFalse(r["ok"])
        self.assertIn("http", r["err"])

    def test_03_add_cap(self):
        api = self._api()
        for i in range(10):
            ok = api.upload_targets_add(
                {"name": "t%d" % i, "url": "https://x.com/%d" % i})["ok"]
            self.assertTrue(ok)
        r = api.upload_targets_add({"name": "over", "url": "https://x.com/z"})
        self.assertFalse(r["ok"])
        self.assertIn("10", r["err"])

    def test_04_del(self):
        api = self._api()
        api.upload_targets_add({"name": "a", "url": "https://x.com/u"})
        r = api.upload_targets_del(0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["removed"], "a")
        self.assertEqual(r["data"]["targets"], [])
        r = api.upload_targets_del(5)
        self.assertFalse(r["ok"])
        self.assertIn("不存在", r["err"])

    def test_05_upload_image_bad_target(self):
        api = self._api()
        png = _png_bytes()
        data_url = "data:image/png;base64," + base64.b64encode(png).decode()
        r = api.upload_image(data_url, "target:9")
        self.assertFalse(r["ok"])
        self.assertIn("不存在", r["err"])


class _FakeCfg:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, k, d=None):
        return self.data.get(k, d)

    def set(self, k, v):
        self.data[k] = v


if __name__ == "__main__":
    unittest.main()
