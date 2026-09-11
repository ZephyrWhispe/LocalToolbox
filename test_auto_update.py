"""自动检查更新模块单元测试：版本判定 / 自动下载触发 / 生命周期 / 设置桥接。

运行：python test_auto_update.py
（真实 GitHub 查询不在测试范围，bindl 全部 mock。）
"""

import os
import shutil
import tempfile
import time
import types
import unittest
from unittest import mock

from app.bridge.update_api import UpdateApi
from app.core import auto_update


class StubCfg:
    """最小 cfg 桩：dict 行为 + set 落内存（不写盘）。"""

    def __init__(self, data=None):
        self.data = {"v2ray_core": "xray", "update_auto_check": False,
                     "update_auto_download": False,
                     "update_scope": ["openlist", "rclone", "core"],
                     "update_interval_hours": 24}
        self.data.update(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class StubApi(UpdateApi):
    def __init__(self, cfg):
        self.cfg = cfg
        self.emits = []
        self.emit_log = lambda m: None
        self._init_update()

    def emit(self, name, data=None):
        self.emits.append((name, data))

    def _open_path(self, p):
        return True, "ok"


class TestResolveKinds(unittest.TestCase):
    def test_01_scope_to_kinds(self):
        cfg = StubCfg({"v2ray_core": "sing-box"})
        kinds = auto_update.resolve_kinds(["openlist", "core"], lambda: cfg)
        self.assertEqual(kinds, ["openlist", "sing-box"])
        # 非法 scope 项被过滤；core 去重
        kinds2 = auto_update.resolve_kinds(
            ["core", "core", "weird"], lambda: StubCfg({"v2ray_core": "xray"}))
        self.assertEqual(kinds2, ["xray"])
        # 空 scope → 默认
        kinds3 = auto_update.resolve_kinds(None, lambda: StubCfg())
        self.assertEqual(kinds3, ["openlist", "rclone", "xray"])


class TestChecker(unittest.TestCase):
    def setUp(self):
        self.cfg = StubCfg()
        self.emits = []
        self.ck = auto_update.AutoUpdateChecker(
            get_cfg=lambda: self.cfg,
            emit=lambda n, d: self.emits.append((n, d)),
            log=lambda m: None,
        )
        self.tmp = tempfile.mkdtemp(prefix="upd-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_02_check_no_update_when_never_downloaded(self):
        """未下载过的组件（cached=None）不算"有更新"，不推送下载。"""
        with mock.patch.object(auto_update.bindl, "latest",
                               return_value={"kind": "openlist", "version": "v9"}), \
                mock.patch.object(auto_update.bindl, "cached_version",
                                  return_value=None):
            results = self.ck.check_once(auto=True)
        self.assertEqual(results[0]["has_update"], False)
        self.assertFalse(any(n == "update_downloaded" for n, _ in self.emits))

    def test_03_auto_download_on_update(self):
        """有更新 + 自动下载开 → 后台调用 download_binary 并推送事件。

        scope 默认 3 项且 mock 让全部组件都有更新 → 下载 3 次。
        """
        self.cfg.data["update_auto_download"] = True
        with mock.patch.object(auto_update.bindl, "latest",
                               return_value={"kind": "openlist", "version": "v2"}), \
                mock.patch.object(auto_update.bindl, "cached_version",
                                  return_value="v1"), \
                mock.patch.object(auto_update.bindl, "download_binary",
                                  return_value={"kind": "openlist",
                                                "version": "v2", "exe": "x",
                                                "cached": False}) as dl:
            self.ck.check_once(auto=True)
            deadline = time.time() + 5
            while time.time() < deadline and dl.call_count < 3:
                time.sleep(0.02)
        self.assertEqual(dl.call_count, 3)   # scope 内 3 个组件全部有更新
        names = [n for n, _ in self.emits]
        self.assertIn("update_progress", names)
        self.assertIn("update_downloaded", names)
        done = [d for n, d in self.emits if n == "update_downloaded"][0]
        self.assertEqual(len(done["ok"]), 3)

    def test_04_no_auto_download_when_disabled(self):
        """有更新但自动下载关 → 仅推送 update_found，不下载。"""
        with mock.patch.object(auto_update.bindl, "latest",
                               return_value={"kind": "rclone", "version": "v2"}), \
                mock.patch.object(auto_update.bindl, "cached_version",
                                  return_value="v1"), \
                mock.patch.object(auto_update.bindl, "download_binary") as dl:
            self.ck.check_once(auto=True)
        self.assertFalse(dl.called)
        found = [d for n, d in self.emits if n == "update_found"][0]
        self.assertTrue(found["results"][0]["has_update"])
        self.assertFalse(found["downloaded"])

    def test_05_lifecycle_start_stop(self):
        """开启自动检查 → 线程运行；stop 后收束；关开关不启动。"""
        self.cfg.data["update_auto_check"] = True
        self.cfg.data["update_interval_hours"] = 1
        with mock.patch.object(auto_update.bindl, "latest",
                               return_value={"kind": "openlist", "version": "v1"}), \
                mock.patch.object(auto_update.bindl, "cached_version",
                                  return_value="v1"):
            self.assertTrue(self.ck.start())
            time.sleep(0.15)
            self.assertIsNotNone(self.ck._thread)
            self.ck.stop()
            time.sleep(0.1)
            self.assertTrue(self.ck._stop.is_set())
        # 关闭状态：start 返回 False 且不建线程
        self.cfg.data["update_auto_check"] = False
        ck2 = auto_update.AutoUpdateChecker(
            get_cfg=lambda: self.cfg, emit=lambda n, d: None, log=lambda m: None)
        self.assertFalse(ck2.start())
        self.assertIsNone(ck2._thread)


class TestUpdateApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="upd-api-")
        self.cfg = StubCfg()
        self.api = StubApi(self.cfg)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_06_settings_roundtrip_and_restart(self):
        """设置读写 roundtrip；开启 auto_check 触发线程重启。"""
        r = self.api.update_set_settings(
            auto_check=True, auto_download=True, scope=["openlist", "core"],
            interval_hours=12)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["data"]["auto_check"])
        self.assertEqual(r["data"]["scope"], ["openlist", "core"])
        self.assertEqual(r["data"]["interval_hours"], 12)
        self.assertIsNotNone(self.api._updater._thread)
        # 关闭 → 线程停止
        r2 = self.api.update_set_settings(auto_check=False)
        self.assertFalse(r2["data"]["auto_check"])
        self.assertTrue(self.api._updater._stop.is_set())
        # 非法 scope 过滤 + interval 钳位
        r3 = self.api.update_set_settings(scope=["weird"], interval_hours=999)
        self.assertEqual(r3["data"]["scope"], ["openlist", "rclone", "core"])
        self.assertEqual(r3["data"]["interval_hours"], 168)

    def test_07_download_one_whitelist_and_cfg_backfill(self):
        """下载白名单校验；cfg bin 为空时回填、非空不覆盖。"""
        with mock.patch.object(auto_update.bindl, "download_binary",
                               return_value={"kind": "openlist",
                                             "version": "v3", "exe": "E:/o.exe",
                                             "cached": False}):
            r = self.api.update_download_one("openlist")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.cfg.data.get("openlist_bin"), "E:/o.exe")
        self.cfg.data["openlist_bin"] = "C:/custom/openlist.exe"
        with mock.patch.object(auto_update.bindl, "download_binary",
                               return_value={"kind": "openlist",
                                             "version": "v3", "exe": "E:/o.exe",
                                             "cached": True}):
            self.api.update_download_one("openlist")
        self.assertEqual(self.cfg.data.get("openlist_bin"), "C:/custom/openlist.exe")
        bad = self.api.update_download_one("weird")
        self.assertFalse(bad["ok"])

    def test_08_check_now_emits_found(self):
        with mock.patch.object(auto_update.bindl, "latest",
                               return_value={"kind": "rclone", "version": "v5"}), \
                mock.patch.object(auto_update.bindl, "cached_version",
                                  return_value="v4"):
            r = self.api.update_check_now()
        self.assertTrue(r["ok"])
        self.assertTrue(r["data"][0]["has_update"])
        self.assertIn(("update_found", mock.ANY),
                      [(n, d) for n, d in self.api.emits])


if __name__ == "__main__":
    unittest.main(verbosity=2)
