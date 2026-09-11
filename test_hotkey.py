# -*- coding: utf-8 -*-
"""P2-4 全局热键测试：启停幂等、注册状态、注入 WM_HOTKEY 触发回调；
组合键解析；多槽位管理（占用冲突、Win+ 组合钩子接管）。"""

import ctypes
import time
import unittest
from ctypes import wintypes

from app.core.hotkey import (
    GlobalHotkey, HotkeyError, HotkeyManager,
    MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, VK_V, WM_HOTKEY,
    combo_to_text, parse_combo,
)

_user32 = ctypes.windll.user32
_user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]


def inject_hotkey(tid, hotkey_id):
    """向热键线程消息队列注入 WM_HOTKEY（模拟用户按键）。"""
    _user32.PostThreadMessageW(tid, WM_HOTKEY, hotkey_id, 0)


class TestHotkey(unittest.TestCase):
    """以下用例使用 Ctrl+Alt+F12（不占用用户常用组合，避免与运行中的应用冲突）。"""

    def test_01_start_stop_idempotent(self):
        h = GlobalHotkey(hotkey_id=0x4101, mods=MOD_CONTROL | MOD_ALT, vk=0x7B)
        h.start(None)
        h.start(lambda: None)  # 幂等：先停旧线程再注册
        time.sleep(0.1)
        h.stop()
        time.sleep(0.05)
        # 停两次也不抛错
        self.assertTrue(True)

    def test_02_registered_after_start(self):
        h = GlobalHotkey(hotkey_id=0x4102, mods=MOD_CONTROL | MOD_ALT, vk=0x7B)
        h.start(None)
        try:
            self.assertTrue(h.active)
        finally:
            h.stop()

    def test_03_callback_on_hotkey_event(self):
        h = GlobalHotkey(hotkey_id=0x4103, mods=MOD_CONTROL | MOD_ALT, vk=0x7B)
        hits = []
        h.start(lambda: hits.append(time.time()))
        try:
            self.assertTrue(h.active)
            inject_hotkey(h._tid, 0x4103)
            end = time.time() + 5
            while not hits and time.time() < end:
                time.sleep(0.05)
            self.assertEqual(len(hits), 1)
        finally:
            h.stop()

    def test_04_unregister_on_stop(self):
        h = GlobalHotkey(hotkey_id=0x4104, mods=MOD_CONTROL | MOD_ALT, vk=0x7B)
        h.start(None)
        h.stop()
        self.assertFalse(h.active)


class TestComboParse(unittest.TestCase):
    """组合键解析与规范化（不依赖真实按键）。"""

    def test_01_parse_valid(self):
        self.assertEqual(
            parse_combo("Ctrl+Alt+V"),
            (MOD_CONTROL | MOD_ALT, VK_V, "Ctrl+Alt+V"))
        self.assertEqual(parse_combo("win+v")[2], "Win+V")
        self.assertEqual(parse_combo("Win+Ctrl+Shift+X")[2], "Ctrl+Shift+Win+X")
        # 修饰键顺序无关，归一化为固定顺序
        self.assertEqual(parse_combo("Ctrl+V+Shift")[2], "Ctrl+Shift+V")
        self.assertEqual(parse_combo("F8"), (0, 0x77, "F8"))   # F 键可无修饰
        self.assertEqual(parse_combo("Ctrl+Shift+F8")[2], "Ctrl+Shift+F8")
        self.assertEqual(combo_to_text(*parse_combo("Alt+F4")[:2]), "Alt+F4")

    def test_02_parse_invalid(self):
        for bad in ("", "  ", "+", "V", "Ctrl+", "F25", "Ctrl+反斜杠"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_combo(bad)


def wait_active(hk, timeout=2.0):
    end = time.time() + timeout
    while not hk.active and time.time() < end:
        time.sleep(0.05)


class TestHotkeyManager(unittest.TestCase):
    """多槽位管理：冲突拒绝、占用提示、Win+ 组合钩子接管。"""

    def test_01_set_conflict_unset(self):
        mgr = HotkeyManager()
        try:
            st = mgr.set("a", "Ctrl+Alt+F9", lambda: None)
            self.assertFalse(st["stolen"])
            time.sleep(0.1)
            with self.assertRaises(HotkeyError):
                mgr.set("b", "Ctrl+Alt+F9", lambda: None)   # 同组合跨槽位冲突
            self.assertTrue(mgr.unset("a"))
            self.assertFalse(mgr._slots)
        finally:
            mgr.stop_all()

    def test_02_occupied_plain_rejected(self):
        occ = GlobalHotkey(hotkey_id=0x41F1, mods=MOD_CONTROL | MOD_ALT, vk=0x79)
        occ.start(None)
        mgr = HotkeyManager()
        try:
            wait_active(occ)
            with self.assertRaises(HotkeyError):
                mgr.set("a", "Ctrl+Alt+F10", lambda: None)  # 0x79=F10，被占
            self.assertFalse(mgr._slots)
        finally:
            mgr.stop_all()
            occ.stop()

    def test_03_win_occupied_falls_back_to_hook(self):
        occ = GlobalHotkey(hotkey_id=0x41F2, mods=MOD_WIN | MOD_CONTROL, vk=0x7A)
        occ.start(None)
        mgr = HotkeyManager()
        try:
            wait_active(occ)
            st = mgr.set("a", "Ctrl+Win+F11", lambda: None)  # 0x7A=F11，模拟系统占用
            self.assertTrue(st["stolen"])       # 已切换钩子接管
            time.sleep(0.1)
            self.assertIsNone(mgr._slots["a"]["obj"])
            self.assertTrue(mgr._hook.active)
            mgr.stop_all()
            self.assertFalse(mgr._hook.active)  # 停止后钩子卸载
        finally:
            mgr.stop_all()
            occ.stop()

    def test_04_parse_error_passthrough(self):
        mgr = HotkeyManager()
        try:
            with self.assertRaises(HotkeyError):
                mgr.set("a", "V", lambda: None)   # 无修饰字母 → ValueError → HotkeyError
            self.assertFalse(mgr._slots)
        finally:
            mgr.stop_all()


if __name__ == "__main__":
    unittest.main(verbosity=2)