# -*- coding: utf-8 -*-
"""托盘服务快捷开关（v5.4 O5）测试：service_submenu 构建与互斥置灰逻辑。

不启动真实托盘图标（不进消息循环），仅构建菜单结构验证。

运行：python -m pytest tests/test_tray_menu.py -q
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.core import tray as tray_mod  # noqa: E402


class TestServiceSubmenu(unittest.TestCase):
    def test_01_structure(self):
        if not tray_mod._AVAILABLE:
            self.skipTest("pystray 缺库")
        item = tray_mod.service_submenu(
            "剪贴板同步", lambda: None, lambda: None, lambda: True)
        self.assertIsNotNone(item)
        self.assertEqual(item.text, "剪贴板同步")
        # 子菜单含启/停两项
        texts = [m.text for m in item.submenu]
        self.assertEqual(texts, ["开启", "停止"])

    def test_02_exclusive_enable(self):
        if not tray_mod._AVAILABLE:
            self.skipTest("pystray 缺库")
        on, off = lambda: None, lambda: None
        state = {"running": True}
        item = tray_mod.service_submenu(
            "s", on, off, lambda: state["running"])
        on_item, off_item = list(item.submenu)
        self.assertTrue(off_item.enabled)     # 运行中 → 停止可点
        self.assertFalse(on_item.enabled)     # 运行中 → 开启置灰
        state["running"] = False
        self.assertTrue(on_item.enabled)
        self.assertFalse(off_item.enabled)

    def test_03_unavailable_returns_none(self):
        old = tray_mod._AVAILABLE
        try:
            tray_mod._AVAILABLE = False
            self.assertIsNone(tray_mod.service_submenu(
                "s", lambda: None, lambda: None, lambda: False))
        finally:
            tray_mod._AVAILABLE = old


if __name__ == "__main__":
    unittest.main()
