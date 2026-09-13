"""Rclone 本地挂载测试（WinFsp 检测注入 winfsp_ok，不依赖真实进程）。

运行：python test_rclone.py
"""

import os
import tempfile
import unittest
from unittest import mock

from app.core import rclone_mount as rc

TMP = tempfile.mkdtemp(prefix="rclone-test-")

WINPFSP_URL = "https://github.com/winfsp/winfsp/releases"


class FakeProc:
    def __init__(self, code=None):
        self.code = code
        self.returncode = code
        self.pid = 777
        self.killed = False
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.code


def make_mgr():
    mgr = rc.RcloneMountManager(openlist=None)  # openlist=None 走兜底默认 URL
    exe = os.path.join(TMP, "rclone.exe")
    open(exe, "wb").close()
    mgr.bin_path = exe
    mgr.config_path = os.path.join(TMP, "rclone.conf")
    mgr.log_path = os.path.join(TMP, "rclone.log")
    # mount() 内部会再 detect_bin("")，测试直接返回已注入的临时 exe
    mgr.detect_bin = lambda configured="": os.path.abspath(mgr.bin_path)
    return mgr


class TestRclone(unittest.TestCase):
    def test_01_detect_bin(self):
        mgr = make_mgr()
        path = mgr.detect_bin(mgr.bin_path)
        self.assertEqual(path, os.path.abspath(mgr.bin_path))

    def test_02_detect_missing(self):
        exe = os.path.join(TMP, "none.exe")
        with mock.patch.object(rc, "RCLONE_BIN", os.path.join(TMP, "nope.exe")), \
             mock.patch.object(rc, "runtime_rclone_candidate",
                               return_value=os.path.join(TMP, "rt.exe")):
            with self.assertRaises(ValueError):
                rc.RcloneMountManager().detect_bin(exe)

    def test_03_ensure_config(self):
        mgr = make_mgr()
        # 匿名
        mgr.ensure_config()
        with open(mgr.config_path, "r", encoding="utf-8") as f:
            conf = f.read()
        self.assertIn("[openlist]", conf)
        self.assertIn("type = webdav", conf)
        self.assertIn("http://127.0.0.1:15244/dav", conf)
        self.assertNotIn("user =", conf)
        # 带账号
        mgr.ensure_config("admin", "p@ss")
        with open(mgr.config_path, "r", encoding="utf-8") as f:
            conf = f.read()
        self.assertIn("user = admin", conf)
        self.assertIn("pass = p@ss", conf)

    @mock.patch.object(rc, "evaluate", side_effect=[False, True])
    @mock.patch("subprocess.Popen", return_value=FakeProc())
    def test_04_mount_letter(self, _popen, _eval):
        mgr = make_mgr()
        r = mgr.mount("letter", "V", "", winfsp_ok=True)
        self.assertEqual(r["target"], "V:")
        self.assertTrue(r["mounted"])
        args = _popen.call_args[0][0]
        self.assertEqual(args[0], mgr.bin_path)
        self.assertIn("mount", args)
        self.assertIn("--network-mode", args)
        self.assertIn("--volname", args)
        self.assertIn("OpenList-V", args)
        self.assertEqual([m["target"] for m in mgr.list_mounted()], ["V:"])

    @mock.patch.object(rc, "evaluate", side_effect=[None, True])
    @mock.patch("subprocess.Popen", return_value=FakeProc())
    def test_05_mount_folder(self, _popen, _eval):
        folder = os.path.join(TMP, "mnt-dir")
        mgr = make_mgr()
        r = mgr.mount("folder", "", folder, winfsp_ok=True)
        self.assertEqual(r["target"], folder)
        self.assertTrue(os.path.isdir(folder))
        args = _popen.call_args[0][0]
        self.assertNotIn("--network-mode", args)
        self.assertNotIn("--volname", args)

    def test_06_mount_winfsp_missing(self):
        mgr = make_mgr()
        with self.assertRaises(ValueError) as ctx:
            mgr.mount("letter", "V", "", winfsp_ok=False)
        self.assertIn("WinFsp", str(ctx.exception))

    @mock.patch.object(rc, "evaluate", return_value=True)
    def test_07_mount_letter_occupied(self, _eval):
        mgr = make_mgr()
        with self.assertRaises(ValueError) as ctx:
            mgr.mount("letter", "V", "", winfsp_ok=True)
        self.assertIn("已被占用", str(ctx.exception))

    @mock.patch.object(rc, "evaluate", return_value=False)
    @mock.patch("subprocess.Popen", return_value=FakeProc())
    def test_08_mount_fail_cleans_up(self, _popen, _eval):
        """挂载点始终不出现 → 报「挂载未生效」并终止子进程（加速时间窗口）。"""
        mgr = make_mgr()

        class FastTime:
            def __init__(self):
                self.t = 1000.0

            def time(self):
                t, self.t = self.t, self.t + 11.0  # 每轮跨越 deadline
                return t

            def sleep(self, s):
                pass

        ft = FastTime()
        with mock.patch.object(rc.time, "time", ft.time), \
             mock.patch.object(rc.time, "sleep", ft.sleep):
            with self.assertRaises(ValueError) as ctx:
                mgr.mount("letter", "A", "", winfsp_ok=True)
        self.assertIn("挂载未生效", str(ctx.exception))
        self.assertTrue(_popen.return_value.terminated)

    @mock.patch.object(rc, "evaluate", side_effect=[False, True])
    @mock.patch("subprocess.Popen", return_value=FakeProc())
    @mock.patch("subprocess.run",
                return_value=mock.Mock(returncode=0, stdout="", stderr=""))
    def test_09_umount(self, _run, _popen, _eval):
        mgr = make_mgr()
        mgr.mount("letter", "V", "", winfsp_ok=True)
        self.assertTrue(mgr.umount("V:"))
        self.assertEqual(mgr.list_mounted(), [])

    def test_10_umount_absent(self):
        mgr = make_mgr()
        self.assertFalse(mgr.umount("Z:"))

    def test_11_invalid_target_type(self):
        mgr = make_mgr()
        with self.assertRaises(ValueError):
            mgr.mount("bogus", "V", "", winfsp_ok=True)


if __name__ == "__main__":
    unittest.main()