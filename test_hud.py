# -*- coding: utf-8 -*-
"""P2-3 HUD 进度悬浮窗冒烟测试：启停幂等、update 跨线程不抛错。

无显示器/无 tkinter 环境会静默降级（available=False），测试同样通过。
"""

import time
import unittest

from app.core.hud import HudController


class TestHud(unittest.TestCase):
    def test_01_start_stop_idempotent(self):
        h = HudController(log=lambda m: None)
        h.start()
        h.start()  # 幂等：不重复起线程
        h.stop()
        h.stop()  # 幂等
        time.sleep(0.1)

    def test_02_update_never_raises(self):
        h = HudController(log=lambda m: None)
        h.start()
        try:
            h.update([])
            h.update([
                {"peer": "设备B", "current": "a.txt", "done": 100,
                 "total": 200, "speed": 1024},
                {"peer": "设备C", "current": "dir/b.bin", "done": 10,
                 "total": 10, "speed": 0},
            ])
            h.update([])
            time.sleep(0.5)  # 给 tkinter 线程渲染机会
        finally:
            h.stop()
        # 无论 tkinter 是否可用，都不得抛错
        self.assertTrue(True)

    def test_03_available_after_start_display(self):
        h = HudController(log=lambda m: None)
        h.start()
        try:
            # 有显示器环境应为 True；无显示器降级 False。不强制，仅验证属性可读
            self.assertIn(h.available, (True, False))
        finally:
            h.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)