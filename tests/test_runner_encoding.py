# -*- coding: utf-8 -*-
"""runner.run 编码参数（v5.4 二期：winget UTF-8 输出乱码修复）测试。

运行：python -m pytest tests/test_runner_encoding.py -q
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.core.runner import run  # noqa: E402

PY = sys.executable or "python"


class TestRunEncoding(unittest.TestCase):
    def test_01_default_gbk_decodes_gbk_output(self):
        r = run([PY, "-c",
                 "import sys; sys.stdout.buffer.write('中文OK'.encode('gbk'))"])
        self.assertIn("中文OK", r.stdout)

    def test_02_utf8_param_decodes_utf8_output(self):
        r = run([PY, "-c",
                 "import sys; sys.stdout.buffer.write('中文OK'.encode('utf-8'))"],
                encoding="utf-8")
        self.assertIn("中文OK", r.stdout)

    def test_03_wrong_encoding_replaces_not_crash(self):
        """编码不匹配 → errors=replace 出替换符但不抛异常（现状语义）。"""
        r = run([PY, "-c",
                 "import sys; sys.stdout.buffer.write('中文OK'.encode('utf-8'))"])
        self.assertTrue(r.stdout)   # 有输出（含替换符），进程不崩


if __name__ == "__main__":
    unittest.main()
