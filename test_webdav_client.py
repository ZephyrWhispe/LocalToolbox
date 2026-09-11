"""WebDAV 客户端全链路测试：以本机 WsgiDAV 为真实服务端。

运行：python test_webdav_client.py
"""

import os
import socket
import tempfile
import unittest

from app.core.web_server import WebServer
from app.core.webdav_client import WebDavClient, WebDavError


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


TMP = tempfile.mkdtemp(prefix="webdav-test-")
USER, PWD = "tester", "secret"


class TestWebDavClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = os.path.join(TMP, "root")
        os.makedirs(cls.root, exist_ok=True)
        cls.port = free_port()
        cls.srv = WebServer()
        cls.srv.start("127.0.0.1", cls.port, cls.root,
                      username=USER, password=PWD, allow_write=True)
        cls.base = "http://127.0.0.1:%d/" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def client(self, user=USER, pwd=PWD):
        return WebDavClient(self.base, user, pwd, timeout=10)

    def test_01_auth_required(self):
        with self.assertRaises(WebDavError) as ctx:
            self.client("wrong", "bad").listdir("/")
        self.assertEqual(ctx.exception.status, 401)

    def test_02_crud_cycle(self):
        c = self.client()
        # 新建目录
        self.assertTrue(c.mkdir("/dirA"))
        # 上传文件（含中文名与空格）
        local = os.path.join(TMP, "up 文件.txt")
        content = "LocalToolbox WebDAV 测试内容\n" * 100
        with open(local, "w", encoding="utf-8") as f:
            f.write(content)
        name = os.path.basename(local)
        self.assertTrue(c.upload(local, remote_dir="/dirA"))

        # 浏览
        info = c.listdir("/")
        names = [e["name"] for e in info["entries"]]
        self.assertIn("dirA", names)
        for e in info["entries"]:
            self.assertTrue(e["is_dir"])
        info2 = c.listdir("/dirA")
        self.assertEqual(len(info2["entries"]), 1)
        e = info2["entries"][0]
        self.assertFalse(e["is_dir"])
        self.assertEqual(e["name"], name)
        # 远端大小与本地磁盘文件字节数一致（真实基准，不依赖字符串编码假设）
        self.assertEqual(e["size"], os.path.getsize(local))
        with open(local, "r", encoding="utf-8") as f:
            expected_content = f.read()

        # 下载并校验内容
        dl = os.path.join(TMP, "down 文件.txt")
        c.download(e["path"], dl)
        with open(dl, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), expected_content)

        # 重命名
        self.assertTrue(c.rename("/dirA/" + name, "/dirA/renamed.txt"))
        info3 = c.listdir("/dirA")
        self.assertEqual(info3["entries"][0]["name"], "renamed.txt")

        # 删除
        self.assertTrue(c.delete("/dirA/renamed.txt"))
        self.assertEqual(len(c.listdir("/dirA")["entries"]), 0)

    def test_03_errors(self):
        c = self.client()
        with self.assertRaises(WebDavError) as ctx:
            c.listdir("/no-such-dir-xyz")
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(WebDavError):
            c.delete("/no-such-file-xyz")
        # 路径穿越被拒绝
        with self.assertRaises(WebDavError):
            c.listdir("/..")

    def test_04_upload_missing_local(self):
        with self.assertRaises(WebDavError):
            self.client().upload(os.path.join(TMP, "no-this-file.bin"), remote_dir="/")


if __name__ == "__main__":
    unittest.main()