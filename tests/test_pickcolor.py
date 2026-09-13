"""屏幕取色器模块单元测试：rgb_to_hex 纯函数断言。

运行：python test_pickcolor.py
（取色器交互与真机像素采样属实机验收项。）
"""

import unittest

from app.core import pickcolor


class TestRgbToHex(unittest.TestCase):
    def test_01_basic(self):
        self.assertEqual(pickcolor.rgb_to_hex(255, 0, 0), "#FF0000")
        self.assertEqual(pickcolor.rgb_to_hex(0, 128, 255), "#0080FF")
        self.assertEqual(pickcolor.rgb_to_hex(1, 2, 3), "#010203")
        self.assertEqual(pickcolor.rgb_to_hex(0, 0, 0), "#000000")
        self.assertEqual(pickcolor.rgb_to_hex(255, 255, 255), "#FFFFFF")

    def test_02_clamp(self):
        self.assertEqual(pickcolor.rgb_to_hex(-5, 300, 12.7), "#00FF0C")
        self.assertEqual(pickcolor.rgb_to_hex(999, -999, "15"), "#FF000F")

    def test_03_rgb_roundtrip(self):
        for rgb in [(200, 60, 60), (10, 200, 10), (33, 44, 55)]:
            hx = pickcolor.rgb_to_hex(*rgb)
            self.assertEqual(len(hx), 7)
            self.assertEqual(hx[0], "#")
            r = int(hx[1:3], 16)
            g = int(hx[3:5], 16)
            b = int(hx[5:7], 16)
            self.assertEqual((r, g, b), rgb)


if __name__ == "__main__":
    unittest.main(verbosity=2)