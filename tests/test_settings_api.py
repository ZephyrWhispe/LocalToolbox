"""v3.5d 设置自定义选项测试：cfg_set 新键校验 + 关闭按钮行为分支。"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import DEFAULTS, AppConfig  # noqa: E402

NEW_BOOL_KEYS = ("tray_close_exit", "start_minimized", "tray_notify", "notify_sound",
                 "clip_autostart", "win_on_top", "ui_reduce_motion", "win_remember")


def make_stub(with_clip=False):
    """最小 Bridge 替身：只带 cfg（cfg_set 仅访问 cfg 与个别键的附属对象）。"""

    class Stub:
        pass

    s = Stub()
    tmp = tempfile.mkdtemp(prefix="cfgset-")
    s._tmp = tmp
    s.cfg = AppConfig(path=str(Path(tmp) / "config.json"))
    if with_clip:

        class _Clip:
            history_limit = 100
            history = []
            store = None

        s.clip = _Clip()
    return s


class TestDefaults(unittest.TestCase):
    def test_01_new_keys_present(self):
        for k in NEW_BOOL_KEYS:
            self.assertIn(k, DEFAULTS)
        self.assertEqual(DEFAULTS["tray_notify"], True)  # 通知默认开
        self.assertEqual(DEFAULTS["tray_close_exit"], False)  # 默认隐藏到托盘
        self.assertEqual(DEFAULTS["start_minimized"], False)
        self.assertEqual(DEFAULTS["notify_sound"], False)
        self.assertEqual(DEFAULTS["ui_zoom"], 1.0)
        self.assertEqual(DEFAULTS["accent_color"], "")
        self.assertEqual(DEFAULTS["device_name_custom"], "")
        self.assertEqual(DEFAULTS["clip_autostart"], False)
        self.assertEqual(DEFAULTS["win_on_top"], False)
        self.assertEqual(DEFAULTS["ui_reduce_motion"], False)
        self.assertEqual(DEFAULTS["clip_retain_days"], 0)
        self.assertEqual(DEFAULTS["win_remember"], False)
        self.assertEqual(DEFAULTS["win_geometry"], {})


class TestCfgSet(unittest.TestCase):
    def setUp(self):
        from app.bridge.bridge import Bridge

        self.Bridge = Bridge
        self.stub = make_stub()
        self.addCleanup(lambda: shutil.rmtree(self.stub._tmp, ignore_errors=True))

    def set(self, key, value):
        return self.Bridge.cfg_set(self.stub, key, value)

    def test_02_bool_keys_coerced(self):
        for k in NEW_BOOL_KEYS:
            r = self.set(k, "yes")  # 非布尔输入 → bool() 收紧
            self.assertTrue(r["ok"], (k, r))
            self.assertIs(r["data"], True)
            self.assertIs(self.stub.cfg.get(k), True)

    def test_03_zoom_clamped(self):
        r = self.set("ui_zoom", 9.9)
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], 1.4)  # 上限钳位
        r = self.set("ui_zoom", "0.1")
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], 0.85)  # 下限钳位
        r = self.set("ui_zoom", "abc")
        self.assertFalse(r["ok"])

    def test_04_accent_color_validated(self):
        r = self.set("accent_color", "#4c8dff")
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], "#4c8dff")
        r = self.set("accent_color", "")  # 空 = 恢复默认
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], "")
        for bad in ("4c8dff", "#12345", "red", "#GGHHII"):
            r = self.set("accent_color", bad)
            self.assertFalse(r["ok"], bad)

    def test_05_unknown_key_rejected(self):
        r = self.set("no_such_key", 1)
        self.assertFalse(r["ok"])

    def test_09_device_name_updates_broadcast(self):
        """v3.5e：自定义设备名 → 截断 32 字符 + 实时更新 discovery/clip 广播名。"""
        stub = self.stub

        class _Discovery:
            device_name = "old-host"

        class _Clip:
            device_name = "old-host"

        stub.discovery = _Discovery()
        stub.clip = _Clip()
        r = self.set("device_name_custom", "  我的设备  ")
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], "我的设备")
        self.assertEqual(stub.discovery.device_name, "我的设备")
        self.assertEqual(stub.clip.device_name, "我的设备")
        # 空 = 恢复主机名（cfg_set 内是函数级 import，补丁目标为源模块）
        with mock.patch("app.core.discovery.local_hostname",
                        return_value="fallback-host") as lh:
            r = self.set("device_name_custom", "   ")
            self.assertTrue(r["ok"])
            self.assertEqual(r["data"], "")
            self.assertEqual(stub.discovery.device_name, "fallback-host")
            self.assertEqual(lh.call_count, 1)  # fallback 只计算一次，discovery/clip 共用
        # 超长截断
        r = self.set("device_name_custom", "x" * 50)
        self.assertEqual(r["data"], "x" * 32)

    def test_12_theme_auto_allowed(self):
        """v3.5f：theme 支持 auto（跟随系统），非法值回落 dark。"""
        r = self.set("theme", "auto")
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], "auto")
        r = self.set("theme", "blue")
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], "dark")

    def test_13_clip_retain_days(self):
        """v3.5f：保留天数钳位 0-365，立即清理 store 与内存历史。"""
        pruned = []

        class _Store:
            def set_days(self, d):
                pruned.append(d)

        class _Clip:
            history = [
                {"ts": 1.0},   # 远古条目
                {"ts": 9999999999.0},  # 未来时间（保留）
            ]

        self.stub.clip = _Clip()
        self.stub.clip.store = _Store()
        r = self.set("clip_retain_days", 9999)
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], 365)  # 上限钳位
        self.assertEqual(pruned[-1], 365)
        self.assertEqual(len(self.stub.clip.history), 1)  # 远古条目被过滤
        r = self.set("clip_retain_days", "abc")
        self.assertFalse(r["ok"])


class TestWinHideToTray(unittest.TestCase):
    def setUp(self):
        from app.bridge.win_api import WindowApi

        self.win = WindowApi.win_hide_to_tray
        self.stub = make_stub()
        self.addCleanup(lambda: shutil.rmtree(self.stub._tmp, ignore_errors=True))
        self.stub._window = None
        self.stub._tray_notify = None
        self.quit_calls = []
        self.stub._tray_quit = lambda: self.quit_calls.append(1)

    def test_06_default_hides_without_quit(self):
        self.stub.cfg.set("tray_close_exit", False)
        r = self.win(self.stub)
        self.assertTrue(r["ok"])
        self.assertNotIn("exit", (r["data"] or {}))
        self.assertEqual(self.quit_calls, [])

    def test_07_close_exit_config_quits(self):
        self.stub.cfg.set("tray_close_exit", True)
        r = self.win(self.stub)
        self.assertTrue(r["ok"])
        self.assertTrue(r["data"]["exit"])
        self.assertEqual(len(self.quit_calls), 1)

    def test_08_close_exit_without_quit_cb_falls_back(self):
        """配置了退出但 _tray_quit 未注入 → 兜底隐藏到托盘，不抛错。"""
        self.stub.cfg.set("tray_close_exit", True)
        self.stub._tray_quit = None
        r = self.win(self.stub)
        self.assertTrue(r["ok"])
        self.assertNotIn("exit", (r["data"] or {}))


class TestWinSetOnTop(unittest.TestCase):
    """v3.5e：主窗口置顶切换。"""

    def _stub(self):
        from app.bridge.win_api import WindowApi

        stub = make_stub()

        class _Native:
            Handle = 0x12345  # 假句柄：SetWindowPos 调用失败也只返回 False，不抛错

        class _Win:
            native = _Native()

        stub._window = _Win()
        return stub, WindowApi.win_set_on_top

    def test_10_on_top_toggle(self):
        stub, fn = self._stub()
        r = fn(stub, True)
        self.assertTrue(r["ok"])
        self.assertIs(r["data"], True)
        r = fn(stub, False)
        self.assertTrue(r["ok"])
        self.assertIs(r["data"], False)

    def test_11_on_top_no_window(self):
        from app.bridge.win_api import WindowApi

        stub = make_stub()
        stub._window = None
        r = WindowApi.win_set_on_top(stub, True)
        self.assertFalse(r["ok"])
        self.assertIn("窗口尚未就绪", r["err"])


class TestClipStoreDays(unittest.TestCase):
    """v3.5f：ClipboardStore 天数保留（真实 SQLite）。"""

    def _make_store(self):
        from app.core.clipboard_store import ClipboardStore

        tmp = tempfile.mkdtemp(prefix="clipdays-")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        st = ClipboardStore(str(Path(tmp) / "h.db"), limit=100)
        self.addCleanup(st.close)
        return st

    @staticmethod
    def _entry(h, ts):
        return {"ts": ts, "device": "d", "hash": h, "kind": "text",
                "text": "t-" + h, "remote": False}

    def test_14_set_days_prunes_old(self):
        import time as _time

        st = self._make_store()
        now = _time.time()
        st.insert(self._entry("old", now - 30 * 86400))
        st.insert(self._entry("new", now - 60))
        n = st.set_days(7)
        self.assertEqual(n, 1)  # 30 天前那条被清理
        hashes = [e["hash"] for e in st.load()]
        self.assertEqual(hashes, ["new"])

    def test_15_zero_days_keeps_all(self):
        import time as _time

        st = self._make_store()
        now = _time.time()
        st.insert(self._entry("old", now - 400 * 86400))
        self.assertEqual(st.set_days(0), 0)  # 0=永久，不清理
        self.assertEqual(len(st.load()), 1)
        # 再改回 365 也会清理
        self.assertEqual(st.set_days(365), 1)
        self.assertEqual(st.load(), [])

    def test_16_insert_prunes_by_days(self):
        import time as _time

        st = self._make_store()
        st.days = 7  # 模拟启动即带天数
        now = _time.time()
        st.insert(self._entry("expired", now - 30 * 86400))
        st.insert(self._entry("fresh", now))
        hashes = sorted(e["hash"] for e in st.load())
        self.assertEqual(hashes, ["fresh"])


class TestToolFavRecent(unittest.TestCase):
    """v4.5：工具箱收藏 / 最近使用（cfg_set 列表收紧）。"""

    def setUp(self):
        from app.bridge.bridge import Bridge

        self.Bridge = Bridge
        self.stub = make_stub()
        self.addCleanup(lambda: shutil.rmtree(self.stub._tmp, ignore_errors=True))

    def set(self, key, value):
        return self.Bridge.cfg_set(self.stub, key, value)

    def test_17_known_ids_kept_and_dedup(self):
        r = self.set("tool_favs", ["tool-uuid", "bad-id", "tool-uuid", "editor"])
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], ["tool-uuid", "editor"])

    def test_18_cap_20_and_type(self):
        many = ["tool-uuid"] + [f"tool-{i}" for i in range(30)]
        r = self.set("tool_favs", many)
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], ["tool-uuid"])  # 非法 id 全部过滤
        r = self.set("tool_recent", "not-a-list")
        self.assertFalse(r["ok"])
        valid = ["tool-text", "tool-files", "tool-radix", "tool-uuid",
                 "tool-timestamp", "tool-regex", "tool-password", "tool-qr",
                 "tool-port", "tool-dns", "tool-monitor", "tool-proxy",
                 "tool-color", "tool-ocr", "tool-palette", "cliphist",
                 "combine", "split", "batch", "video", "editor", "tool-uuid"]
        r = self.set("tool_recent", valid)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["data"]), 20)          # cap 20
        self.assertNotIn("tool-uuid", r["data"][:2])  # 去重保序（尾部重复被裁掉）

    def test_19_defaults_present(self):
        self.assertEqual(DEFAULTS["tool_favs"], [])
        self.assertEqual(DEFAULTS["tool_recent"], [])


class TestKmCfg(unittest.TestCase):
    """v4.7：键鼠共享设置（km_* 键收紧 + 即时生效副作用）。"""

    class _Target:
        def __init__(self):
            self.enabled = True
            self.controlled = False
            self.lock_input = False
            self.lock_calls = []

        def set_lock_input(self, v):
            self.lock_input = v
            self.lock_calls.append(v)

    class _Controller:
        def __init__(self):
            self.edge_margin = 4

    def setUp(self):
        from app.bridge.bridge import Bridge

        self.Bridge = Bridge
        self.stub = make_stub()
        self.addCleanup(lambda: shutil.rmtree(self.stub._tmp, ignore_errors=True))
        self.stub._km_edges = {}
        self.stub._km_target = self._Target()
        self.stub._km_controller = self._Controller()

    def set(self, key, value):
        return self.Bridge.cfg_set(self.stub, key, value)

    def test_20_defaults(self):
        self.assertEqual(DEFAULTS["km_allow"], True)
        self.assertEqual(DEFAULTS["km_lock_input"], False)
        self.assertEqual(DEFAULTS["km_edge_switch"], True)
        self.assertEqual(DEFAULTS["km_edge_margin"], 4)
        self.assertEqual(DEFAULTS["km_edges"], {})

    def test_21_bool_keys(self):
        for k in ("km_allow", "km_lock_input", "km_edge_switch"):
            r = self.set(k, "yes")  # 非布尔输入 → bool() 收紧
            self.assertTrue(r["ok"], (k, r))
            self.assertIs(r["data"], True)

    def test_22_margin_clamped(self):
        r = self.set("km_edge_margin", 99)
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], 50)
        r = self.set("km_edge_margin", 0)
        self.assertEqual(r["data"], 1)
        r = self.set("km_edge_margin", "abc")
        self.assertFalse(r["ok"])

    def test_23_edges_normalized(self):
        r = self.set("km_edges", {"left": " 192.168.1.5 ", "bad": "x", "right": ""})
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], {"left": "192.168.1.5", "right": "", "top": "", "bottom": ""})
        r = self.set("km_edges", "nope")
        self.assertFalse(r["ok"])

    def test_24_side_effects(self):
        r = self.set("km_edge_margin", 20)
        self.assertTrue(r["ok"])
        self.assertEqual(self.stub._km_controller.edge_margin, 20)
        r = self.set("km_allow", False)
        self.assertTrue(r["ok"])
        self.assertIs(self.stub._km_target.enabled, False)
        self.assertIs(self.stub._km_target.controlled, False)
        r = self.set("km_lock_input", True)
        self.assertTrue(r["ok"])
        self.assertIs(self.stub._km_target.lock_input, True)
        self.assertEqual(self.stub._km_target.lock_calls, [True])


class TestV50Cfg(unittest.TestCase):
    """v5.0：备忘录 / 密码库 / 备份 / 文件收藏 cfg 键。"""

    def setUp(self):
        from app.bridge.bridge import Bridge

        self.Bridge = Bridge
        self.stub = make_stub()
        self.addCleanup(lambda: shutil.rmtree(self.stub._tmp, ignore_errors=True))

    def set(self, key, value):
        return self.Bridge.cfg_set(self.stub, key, value)

    def test_25_defaults(self):
        self.assertEqual(DEFAULTS["hotkey_memo"], "Ctrl+Alt+M")
        self.assertEqual(DEFAULTS["vault_autolock_min"], 15)
        self.assertEqual(DEFAULTS["vault_clip_clear_sec"], 30)
        self.assertEqual(DEFAULTS["backup_keep"], 10)
        self.assertEqual(DEFAULTS["backup_autoupload"], False)
        self.assertEqual(DEFAULTS["backup_autoupload_hours"], 24)
        self.assertIsInstance(DEFAULTS["backup_targets"], list)
        self.assertEqual(DEFAULTS["backup_targets"][0]["type"], "openlist")
        self.assertEqual(DEFAULTS["file_favs"], [])

    def test_26_hotkey_memo_parsed(self):
        r = self.set("hotkey_memo", "Ctrl+Alt+J")
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], "Ctrl+Alt+J")

    def test_27_vault_keys_clamped(self):
        self.assertEqual(self.set("vault_autolock_min", 0)["data"], 0)
        self.assertEqual(self.set("vault_autolock_min", 99999)["data"], 1440)
        self.assertEqual(self.set("vault_autolock_min", "abc")["ok"], False)
        self.assertEqual(self.set("vault_clip_clear_sec", 5)["data"], 5)
        self.assertEqual(self.set("vault_clip_clear_sec", "x")["ok"], False)

    def test_28_backup_keys(self):
        self.assertEqual(self.set("backup_keep", 0)["data"], 1)
        self.assertEqual(self.set("backup_keep", 99)["data"], 50)
        r = self.set("backup_targets", [
            {"type": "webdav", "name": "坚果云", "url": " https://dav.x/dav ",
             "user": "u", "pwd": "p", "dir": "", "enabled": 1},
            {"type": "openlist"}, "junk",
        ])
        self.assertTrue(r["ok"])
        data = r["data"]
        self.assertEqual(len(data), 2)  # junk 丢弃
        self.assertEqual(data[0]["type"], "webdav")
        self.assertEqual(data[0]["url"], "https://dav.x/dav")
        self.assertEqual(data[0]["dir"], "LocalToolboxBackup")
        self.assertIs(data[0]["enabled"], True)
        self.assertEqual(data[1]["type"], "openlist")
        self.assertEqual(self.set("backup_targets", "nope")["ok"], False)
        self.assertIs(self.set("backup_autoupload", "yes")["data"], True)
        self.assertEqual(self.set("backup_autoupload_hours", 2)["data"], 6)

    def test_29_file_favs_and_memo_group(self):
        r = self.set("file_favs", [
            {"name": "文档", "path": "C:\\Users\\x\\Documents"},
            {"path": "D:\\proj"},  # name 缺省 → basename
            {"name": "bad"},       # 无 path 丢弃
            {"name": "dup", "path": "D:\\proj"},  # 去重
        ])
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["data"]), 2)
        self.assertEqual(r["data"][1]["name"], "proj")
        self.assertEqual(self.set("file_favs", "nope")["ok"], False)
        self.assertEqual(self.set("memo_pop_group_last", "7")["data"], 7)


class TestV51OcrCfg(unittest.TestCase):
    """v5.1：OCR 设置键（引擎白名单 / Umi 地址 / 后处理开关）。"""

    def setUp(self):
        from app.bridge.bridge import Bridge

        self.Bridge = Bridge
        self.stub = make_stub()
        self.addCleanup(lambda: shutil.rmtree(self.stub._tmp, ignore_errors=True))

    def set(self, key, value):
        return self.Bridge.cfg_set(self.stub, key, value)

    def test_30_defaults(self):
        self.assertEqual(DEFAULTS["hotkey_ocr"], "Ctrl+Alt+O")
        self.assertEqual(DEFAULTS["ocr_engine"], "winrt")
        self.assertIs(DEFAULTS["ocr_merge_lines"], True)
        self.assertEqual(DEFAULTS["ocr_umi_url"], "http://127.0.0.1:1224")
        self.assertEqual(DEFAULTS["ocr_umi_path"], "")
        self.assertIs(DEFAULTS["ocr_umi_autostart"], True)

    def test_31_engine_whitelist(self):
        self.assertEqual(self.set("ocr_engine", "umi")["data"], "umi")
        self.assertEqual(self.set("ocr_engine", "rapid")["data"], "rapid")
        r = self.set("ocr_engine", "hack")
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], "winrt")  # 非法回退默认

    def test_32_strings_and_bools(self):
        self.assertIs(self.set("ocr_merge_lines", "yes")["data"], True)
        self.assertIs(self.set("ocr_umi_autostart", 0)["data"], False)
        r = self.set("ocr_umi_url", " http://192.168.1.5:1224 ")
        self.assertEqual(r["data"], "http://192.168.1.5:1224")
        self.assertEqual(self.set("ocr_umi_path", " D:\\Umi\\Umi-OCR.exe ")["data"],
                         "D:\\Umi\\Umi-OCR.exe")


if __name__ == "__main__":
    unittest.main()
