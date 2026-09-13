# -*- coding: utf-8 -*-
"""提权重启目标选择（v5.4 二期：pythonw 消除命令行窗口）测试。

运行：python -m pytest tests/test_privilege_relaunch.py -q
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from app.core.privilege import _relaunch_target  # noqa: E402


class TestRelaunchTarget(unittest.TestCase):
    def test_01_source_prefers_pythonw(self):
        """源码运行且 pythonw.exe 存在 → 用 pythonw（无控制台窗口）。"""
        pydir = Path(tempfile.mkdtemp(prefix="relaunch-"))
        self.addCleanup(lambda: shutil.rmtree(pydir, ignore_errors=True))
        py = pydir / "python.exe"
        pyw = pydir / "pythonw.exe"
        py.write_bytes(b"")
        pyw.write_bytes(b"")
        script = pydir / "main.py"
        with mock.patch.object(sys, "frozen", False, create=True), \
                mock.patch.object(sys, "executable", str(py)), \
                mock.patch.object(sys, "argv", [str(script)]), \
                mock.patch.object(os.path, "isfile",
                                  side_effect=lambda p: p == str(pyw)):
            file, params = _relaunch_target(["--relaunch-admin"])
        self.assertEqual(file, str(pyw))
        self.assertIn("main.py", params)
        self.assertIn("--relaunch-admin", params)

    def test_02_source_falls_back_to_python(self):
        """pythonw 不存在 → 回退 python.exe。"""
        import shutil
        import tempfile
        pydir = Path(tempfile.mkdtemp(prefix="relaunch-"))
        self.addCleanup(lambda: shutil.rmtree(pydir, ignore_errors=True))
        py = pydir / "python.exe"
        py.write_bytes(b"")
        script = pydir / "main.py"
        with mock.patch.object(sys, "frozen", False, create=True), \
                mock.patch.object(sys, "executable", str(py)), \
                mock.patch.object(sys, "argv", [str(script)]), \
                mock.patch.object(os.path, "isfile", return_value=False):
            file, params = _relaunch_target([])
        self.assertEqual(file, str(py))

    def test_03_frozen_uses_exe(self):
        """打包场景：直接用 exe（PyInstaller console=False 本就无控制台）。"""
        exe = "C:/dist/LocalToolbox.exe"
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", exe):
            file, params = _relaunch_target(["--relaunch-admin"])
        self.assertEqual(file, os.path.abspath(exe))
        self.assertEqual(params, "--relaunch-admin")   # 无空格参数不加引号


if __name__ == "__main__":
    unittest.main()
