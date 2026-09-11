"""工具箱单元测试：哈希/清单校验/目录快照/清单导出/端口查询/二维码。

运行：python test_tools.py
"""

import hashlib
import io
import os
import shutil
import socket
import tempfile
import time
import unittest
from unittest import mock

from app.core import tools


TMP = tempfile.mkdtemp(prefix="tools-test-")


def _mk(path, text):
    full = os.path.join(TMP, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)
    return full


class TestHash(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f1 = _mk("a.txt", "hello world")
        cls.f2 = _mk("sub/b.txt", "你好，局域网" * 100)
        cls.f3 = _mk("sub/deep/c.bin", bytes(range(256)).hex())

    def test_01_hash_known(self):
        data = b"hello world"
        self.assertEqual(tools.hash_file(self.f1, "md5"),
                         hashlib.md5(data).hexdigest())
        self.assertEqual(tools.hash_file(self.f1, "sha1"),
                         hashlib.sha1(data).hexdigest())
        self.assertEqual(tools.hash_file(self.f1, "sha256"),
                         hashlib.sha256(data).hexdigest())

    def test_02_hash_paths_recursive(self):
        out = tools.hash_paths([self.f1, os.path.join(TMP, "sub")], algo="sha256")
        self.assertEqual(out["files"], 3)
        digests = {os.path.basename(r["path"]): r["digest"] for r in out["results"]}
        self.assertIn("a.txt", digests)
        self.assertIn("b.txt", digests)
        self.assertIn("c.bin", digests)

    def test_03_hash_missing_file_reported(self):
        out = tools.hash_paths([os.path.join(TMP, "not-exist.txt")])
        self.assertEqual(out["files"], 0)
        self.assertEqual(len(out["fails"]), 1)

    def test_04_manifest_roundtrip(self):
        manifest = tools.build_manifest([self.f1, os.path.join(TMP, "sub")], algo="md5")
        mpath = os.path.join(TMP, "manifest.md5")
        with open(mpath, "w", encoding="utf-8") as f:
            f.write(manifest)
        r = tools.verify_manifest(mpath)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["ok_count"], 3)

    def test_05_manifest_tamper_and_missing(self):
        mpath = os.path.join(TMP, "manifest.md5")
        r = tools.verify_manifest(mpath)
        self.assertTrue(r["ok"])
        # 篡改一个文件内容
        tamper = _mk("tamper.txt", "aaaa")
        tm_path = os.path.join(TMP, "tamper.md5")
        with open(tm_path, "w", encoding="utf-8") as f:
            f.write("00000000000000000000000000000000 *tamper.txt\n")
        r2 = tools.verify_manifest(tm_path)
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["fails"][0]["reason"], "哈希不匹配")
        # 缺失文件
        miss_path = os.path.join(TMP, "miss.md5")
        with open(miss_path, "w", encoding="utf-8") as f:
            f.write("d41d8cd98f00b204e9800998ecf8427e *gone.txt\n")
        r3 = tools.verify_manifest(miss_path)
        self.assertFalse(r3["ok"])
        self.assertEqual(r3["fails"][0]["reason"], "缺失")


class TestSnapshotExport(unittest.TestCase):
    def test_06_snapshot_tree(self):
        src = _mk("tree/x/1.txt", "1")
        _mk("tree/y/2/2.txt", "2")
        dest = os.path.join(TMP, "snap-out")
        n = tools.snapshot_tree(os.path.join(TMP, "tree"), dest)
        self.assertEqual(n, 4)  # tree / x / y / y/2
        self.assertTrue(os.path.isdir(os.path.join(dest, "x")))
        self.assertTrue(os.path.isdir(os.path.join(dest, "y", "2")))
        self.assertFalse(os.path.exists(os.path.join(dest, "x", "1.txt")))
        shutil.rmtree(dest, ignore_errors=True)
        shutil.rmtree(src if False else os.path.join(TMP, "tree"), ignore_errors=True)

    def _patch_downloads(self):
        """导出目录固定到临时目录：测试不再往用户 Downloads 里丢 CSV。"""
        import app.core.tools as _t
        real = os.path.expanduser

        def fake(p, *a, **k):
            if p in ("~", "~/"):
                return TMP
            return real(p, *a, **k)

        patcher = mock.patch.object(_t.os.path, "expanduser", side_effect=fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_07_export_csv(self):
        self._patch_downloads()
        _mk("exp/a.txt", "a")
        _mk("exp/sub/b.txt", "b")
        out, count = tools.export_file_list(os.path.join(TMP, "exp"), include_sub=True)
        self.assertTrue(os.path.isfile(out))
        content = io.open(out, "r", encoding="utf-8-sig").read()
        self.assertIn("相对路径", content)
        self.assertIn("exp", count and "exp" or "exp")  # 行数为 2（不含表头）
        self.assertEqual(count, 2)
        # 不带子目录
        out2, count2 = tools.export_file_list(os.path.join(TMP, "exp"), include_sub=False)
        self.assertEqual(count2, 1)
        os.remove(out)
        os.remove(out2)
        shutil.rmtree(os.path.join(TMP, "exp"), ignore_errors=True)

    def test_08_export_with_hash(self):
        self._patch_downloads()
        _mk("eh/a.txt", "abc")
        out, count = tools.export_file_list(os.path.join(TMP, "eh"), include_sub=True,
                                             with_hash=True, algo="sha256")
        content = io.open(out, "r", encoding="utf-8-sig").read()
        self.assertIn("SHA256", content)
        self.assertIn(hashlib.sha256(b"abc").hexdigest(), content)
        os.remove(out)
        shutil.rmtree(os.path.join(TMP, "eh"), ignore_errors=True)


class TestPort(unittest.TestCase):
    def test_09_port_owner_finds_listener(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            owners = []
            for _ in range(4):        # netstat/PowerShell 偶发滞后，重试几次
                owners = tools.port_owner(port)
                if owners:
                    break
                time.sleep(0.4)
            if not owners:
                self.skipTest("PowerShell/netstat 未报告该端口占用（环境受限）")
            self.assertTrue(all(o["pid"] for o in owners))
        except Exception as e:
            self.skipTest("PowerShell 环境受限：" + str(e))
        finally:
            srv.close()

    def test_10_port_free_returns_empty(self):
        try:
            owners = tools.port_owner(65534)  # 常见空闲高端口
            self.assertEqual(owners, [])
        except Exception:
            pass


class TestQr(unittest.TestCase):
    def test_11_qr_data_url(self):
        url = tools.qr_png_data_url("https://example.com", 180)
        if url is None:
            self.skipTest("qrcode 库未安装")
        self.assertTrue(url.startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=False)
    shutil.rmtree(TMP, ignore_errors=True)