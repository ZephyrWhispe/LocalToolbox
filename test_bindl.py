"""bindl 二进制自动下载器测试。

运行：python test_bindl.py
"""

import io
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from app.core import bindl

TMP = tempfile.mkdtemp(prefix="bindl-test-")
BINDL_DIR = os.path.join(TMP, "bindl")
CACHE_FILE = os.path.join(BINDL_DIR, "cache.json")


class FakeResp:
    def __init__(self, data=b"", headers=None):
        self.data = data
        self.headers = {"Content-Length": str(len(data))}
        if headers:
            self.headers.update(headers)

    def read(self, n=-1):
        if n < 0:
            return self.data
        if not hasattr(self, "_pos"):
            self._pos = 0
        chunk = self.data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def geturl(self):
        return "https://github.com/x/releases/tag/v1.0"


def make_zip(exe_name="openlist.exe", content=b"FAKE-EXE"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(exe_name, content)
    return buf.getvalue()


class TestBindl(unittest.TestCase):
    def setUp(self):
        bindl.BINDL_DIR = BINDL_DIR
        bindl.CACHE_FILE = CACHE_FILE
        os.makedirs(BINDL_DIR, exist_ok=True)

    def tearDown(self):
        for root, _dirs, files in os.walk(BINDL_DIR, topdown=False):
            for f in files:
                os.remove(os.path.join(root, f))
        bindl.BINDL_DIR = os.path.join(TMP, "bindl")
        bindl.CACHE_FILE = bindl.BINDL_DIR

    @mock.patch.object(bindl, "_latest_release",
                       return_value=("v1.2", ["https://x/openlist-windows-amd64.zip"]))
    @mock.patch.object(bindl, "_open_http",
                       return_value=FakeResp(make_zip()))
    def test_01_download_extract(self, _open, _latest):
        dest = os.path.join(TMP, "dest1")
        r = bindl.download_binary("openlist", dest_dir=dest)
        self.assertEqual(r["version"], "v1.2")
        self.assertFalse(r["cached"])
        exe = os.path.join(dest, "openlist.exe")
        self.assertTrue(os.path.isfile(exe), "应解压出 openlist.exe")
        with open(exe, "rb") as f:
            self.assertEqual(f.read(), b"FAKE-EXE")

    @mock.patch.object(bindl, "_latest_release",
                       return_value=("v1.2", ["https://x/openlist-windows-amd64.zip"]))
    @mock.patch.object(bindl, "_open_http",
                       return_value=FakeResp(make_zip()))
    def test_02_cache_hit_skips_download(self, _open, _latest):
        dest = os.path.join(TMP, "dest2")
        r1 = bindl.download_binary("openlist", dest_dir=dest)
        _open.reset_mock()
        _open.side_effect = AssertionError("不应再次网络下载")
        r2 = bindl.download_binary("openlist", dest_dir=dest)
        self.assertTrue(r2["cached"])
        self.assertEqual(r1["exe"], r2["exe"])
        self.assertTrue(os.path.isfile(r2["exe"]))

    @mock.patch.object(bindl, "_latest_release",
                       return_value=("v1.0", []))
    def test_03_no_asset_raises(self, _latest):
        with self.assertRaises(bindl.BindlError):
            bindl.download_binary("openlist", dest_dir=os.path.join(TMP, "dest3"))

    @mock.patch.object(bindl, "_latest_release",
                       return_value=("v1.0", ["https://x/anything.zip"]))
    @mock.patch.object(bindl, "_open_http",
                       return_value=FakeResp(b"NOT A ZIP"))
    def test_04_bad_zip_raises(self, _open, _latest):
        with self.assertRaises(bindl.BindlError):
            bindl.download_binary("openlist", dest_dir=os.path.join(TMP, "dest4"))

    def test_05_unknown_kind(self):
        with self.assertRaises(bindl.BindlError):
            bindl.download_binary("nope", dest_dir=TMP)

    # -- v3.2：rclone / winfsp / latest / cached_version ---------------------
    @mock.patch.object(bindl, "_latest_release",
                       return_value=("v1.66.0", ["https://x/rclone-v1.66.0-windows-amd64.zip"]))
    @mock.patch.object(bindl, "_open_http",
                       return_value=FakeResp(make_zip("rclone.exe", b"RCLONE-EXE")))
    def test_06_rclone_download(self, _open, _latest):
        dest = os.path.join(TMP, "dest-rclone")
        r = bindl.download_binary("rclone", dest_dir=dest)
        self.assertEqual(r["version"], "v1.66.0")
        exe = os.path.join(dest, "rclone.exe")
        self.assertTrue(os.path.isfile(exe))
        with open(exe, "rb") as f:
            self.assertEqual(f.read(), b"RCLONE-EXE")

    @mock.patch.object(bindl, "_latest_release",
                       return_value=("2.4.3", ["https://x/winfsp-2.4.3.msi"]))
    @mock.patch.object(bindl, "_open_http",
                       return_value=FakeResp(b"M" * 2048))  # raw 需 ≥1024 字节
    def test_07_winfsp_raw_download(self, _open, _latest):
        dest = os.path.join(TMP, "dest-winfsp")
        r = bindl.download_binary("winfsp", dest_dir=dest)
        self.assertEqual(r["version"], "2.4.3")
        msi = os.path.join(dest, "winfsp.msi")
        self.assertTrue(os.path.isfile(msi))
        self.assertEqual(os.path.getsize(msi), 2048)

    @mock.patch.object(bindl, "_latest_release",
                       return_value=("v9.9.9", ["https://x/rclone-windows-amd64.zip"]))
    def test_08_latest(self, _latest):
        info = bindl.latest("rclone")
        self.assertEqual(info, {"kind": "rclone", "version": "v9.9.9"})
        with self.assertRaises(bindl.BindlError):
            bindl.latest("nope")

    @mock.patch.object(bindl, "_latest_release",
                       return_value=("v1.66.0", ["https://x/rclone-v1.66.0-windows-amd64.zip"]))
    @mock.patch.object(bindl, "_open_http",
                       return_value=FakeResp(make_zip("rclone.exe", b"RCLONE-EXE")))
    def test_09_cached_version(self, _open, _latest):
        self.assertIsNone(bindl.cached_version("rclone"))
        dest = os.path.join(TMP, "dest-cache")
        bindl.download_binary("rclone", dest_dir=dest)
        self.assertEqual(bindl.cached_version("rclone"), "v1.66.0")
        self.assertIsNone(bindl.cached_version("nope"))


    @mock.patch.object(bindl, "_open_http",
                       side_effect=lambda *a, **k: FakeResp(make_zip("mihomo.exe")))
    def test_02b_asset_switch_forces_redownload(self, _open):
        """同版本但架构构建变了（v1→v3）必须重新下载，不能命中缓存。"""
        dest = os.path.join(TMP, "dest2b")
        with mock.patch.object(bindl, "_latest_release",
                               return_value=("v1.2", ["https://x/mihomo-windows-amd64-v1-go120-v1.19.30.zip"])):
            r1 = bindl.download_binary("mihomo", dest_dir=dest)
        self.assertIn("-v1-", r1["asset"])
        _open.reset_mock()
        with mock.patch.object(bindl, "_latest_release",
                               return_value=("v1.2", ["https://x/mihomo-windows-amd64-v3-go125-v1.19.30.zip"])):
            r2 = bindl.download_binary("mihomo", dest_dir=dest)
        self.assertFalse(r2["cached"], "换了构建应重新下载")
        self.assertIn("-v3-", r2["asset"])
        self.assertGreaterEqual(_open.call_count, 1)

    @mock.patch.object(bindl, "_open_http", return_value=FakeResp(make_zip()))
    def test_02c_cache_records_asset(self, _open):
        """缓存里要落 asset，否则同版本换构建会被误判为已下载。"""
        dest = os.path.join(TMP, "dest2c")
        with mock.patch.object(bindl, "_latest_release",
                               return_value=("v1.2", ["https://x/openlist-windows-amd64.zip"])):
            bindl.download_binary("openlist", dest_dir=dest)
        info = bindl.cached_info("openlist")
        self.assertEqual(info["asset"], "openlist-windows-amd64.zip")
        self.assertEqual(info["version"], "v1.2")


if __name__ == "__main__":
    unittest.main()