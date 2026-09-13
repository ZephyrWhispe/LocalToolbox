# -*- coding: utf-8 -*-
"""全量已装应用检测工厂/静默卸载（v5.4 架构级复刻一期）测试。

PowerShell 输出以 mock 样本注入（run_powershell_json patch），快照落盘隔离到临时目录。

运行：python -m pytest tests/test_appscan.py -q
"""

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from app.core import appscan  # noqa: E402


def _reg(name, ver="1.0", un="C:/un.exe", quiet="", sys_=0,
         key="K1", view="x64", scope="machine", pub=""):
    return {"name": name, "ver": ver, "pub": pub, "date": "20260101",
            "size": 1024, "un": un, "quiet": quiet, "sys": sys_,
            "key": key, "view": view, "scope": scope}


def _patch_registry(rows):
    """patch run_powershell_json：注册表脚本返回 rows 样本。"""
    def fake(script, timeout=None):
        if "Uninstall" in script:
            return rows
        return None  # 其它脚本（UWP Appx 等）不命中
    return mock.patch.object(appscan, "run_powershell_json",
                             side_effect=fake)


class TestScanRegistry(unittest.TestCase):
    def setUp(self):
        appscan.apps_cache_clear()
        # UWP 源返回空（本组用例只关注注册表）
        self._uwp = mock.patch.object(appscan, "uwp_list", return_value=[])
        self._uwp.start()
        self.addCleanup(self._uwp.stop)

    def test_01_dedup_wow64_keeps_x64(self):
        rows = [
            _reg("AppA", "1.0", key="K64", view="x64",
                 quiet="C:/q64.exe"),
            _reg("appa", "1.0", key="K86", view="x86",
                 quiet="C:/q86.exe"),
        ]
        with _patch_registry(rows):
            entries = appscan.scan_registry()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].view, "x64")
        self.assertEqual(entries[0].quiet_cmd, "C:/q64.exe")

    def test_02_filter_system_component_and_kb(self):
        rows = [
            _reg("正常应用"),
            _reg("隐藏组件", sys_=1),
            _reg("KB5001234 安全更新"),
            _reg("2026-01 累积更新"),
            _reg("Hotfix 补丁"),
            _reg("无卸载串应用", un="", quiet=""),
        ]
        with _patch_registry(rows):
            entries = appscan.scan_registry()
        self.assertEqual([e.name for e in entries], ["正常应用"])

    def test_03_quiet_possible_msi_guid(self):
        rows = [_reg("MsiApp", un="MsiExec.exe /I{12345678-1234-1234-1234-1234567890AB}")]
        with _patch_registry(rows):
            entries = appscan.scan_registry()
        e = entries[0]
        self.assertTrue(e.quiet_possible)
        self.assertEqual(e.effective_quiet_cmd(),
                         "msiexec /x {12345678-1234-1234-1234-1234567890AB}"
                         " /qn /norestart")

    def test_04_quiet_possible_false(self):
        with _patch_registry([_reg("OnlyInteractive")]):
            entries = appscan.scan_registry()
        self.assertFalse(entries[0].quiet_possible)

    def test_05_listkey_and_protected_prefix(self):
        rows = [
            _reg("Microsoft.WindowsStore", key="StoreKey"),
            _reg("普通应用", key="NormalKey"),
        ]
        with _patch_registry(rows):
            entries = appscan.scan_registry()
        self.assertEqual([e.name for e in entries], ["普通应用"])
        self.assertEqual(entries[0].listkey, "registry|NormalKey")


class TestScanAppsCache(unittest.TestCase):
    def setUp(self):
        appscan.apps_cache_clear()

    def test_06_cache_ttl_hit(self):
        with _patch_registry([_reg("App1")]), \
                mock.patch.object(appscan, "uwp_list", return_value=[]) as muwp:
            r1 = appscan.scan_apps()
            r2 = appscan.scan_apps()
        self.assertEqual(len(r1), 1)
        self.assertIs(r1, r2)          # TTL 内命中缓存
        self.assertEqual(muwp.call_count, 1)
        appscan.apps_cache_clear()
        with _patch_registry([_reg("App1")]):
            appscan.scan_apps()        # 过期后重扫不再命中旧缓存
        self.assertIsNot(appscan.cache_get(), r1)

    def test_07_cache_get_none_before_scan(self):
        self.assertIsNone(appscan.cache_get())


class TestUninstall(unittest.TestCase):
    def setUp(self):
        appscan.apps_cache_clear()
        self.dir = tempfile.mkdtemp(prefix="appscan-")
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self._snap = mock.patch.object(
            appscan, "APPS_SNAPSHOT_PATH",
            str(Path(self.dir) / "removed_apps.json"))
        self._snap.start()
        self.addCleanup(self._snap.stop)
        self._uwp = mock.patch.object(appscan, "uwp_list", return_value=[])
        self._uwp.start()
        self.addCleanup(self._uwp.stop)

    def _seed(self):
        with _patch_registry([_reg("QuietApp", key="Q1",
                                   quiet="C:/quiet.exe /S"),
                              _reg("MsiApp", key="M1",
                                   un="MsiExec.exe /I{12345678-1234-1234-1234-1234567890AB}"),
                              _reg("ManualApp", key="X1")]):
            appscan.scan_apps()

    def test_08_reject_non_quiet(self):
        self._seed()
        with self.assertRaises(ValueError) as ctx:
            appscan.uninstall_apps(["registry|X1"])
        self.assertIn("不支持静默卸载", str(ctx.exception))

    def test_09_uninstall_parses_lt_un_and_snapshot(self):
        self._seed()
        out = ("LT_UN:{\"key\":\"registry|Q1\",\"code\":0}\n"
               "LT_UN:{\"key\":\"registry|M1\",\"code\":3010}\n")

        class R:
            stdout = out
            returncode = 0

        with mock.patch.object(appscan, "run_elevated", return_value=R):
            results = appscan.uninstall_apps(
                ["registry|Q1", "registry|M1"],
                progress_cb=lambda d, t, n: None)
        by = {x["listkey"]: x for x in results}
        self.assertTrue(by["registry|Q1"]["ok"])
        # 3010 = 重启要求，非 0 → 失败路径
        self.assertFalse(by["registry|M1"]["ok"])
        snap = appscan.removed_list()
        self.assertEqual([x["listkey"] for x in snap], ["registry|Q1"])

    def test_10_missed_entry_reported(self):
        self._seed()
        with mock.patch.object(appscan, "run_elevated",
                               return_value=type("R", (), {"stdout": "",
                                                           "returncode": 1})):
            results = appscan.uninstall_apps(["registry|Q1"])
        self.assertFalse(results[0]["ok"])
        self.assertIn("失败", results[0]["err"])


class TestAppsApi(unittest.TestCase):
    def _api(self):
        import threading

        from app.bridge.optimize_api import OptimizeApi
        from app.core.config import AppConfig

        class Stub(OptimizeApi):
            pass

        api = Stub()
        api.cfg = AppConfig()
        api._init_optimize()
        return api

    def setUp(self):
        appscan.apps_cache_clear()
        self.dir = tempfile.mkdtemp(prefix="appscan-api-")
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self._snap = mock.patch.object(
            appscan, "APPS_SNAPSHOT_PATH",
            str(Path(self.dir) / "removed_apps.json"))
        self._snap.start()
        self.addCleanup(self._snap.stop)

    def test_11_list_requires_scan(self):
        api = self._api()
        r = api.opt_apps_list()
        self.assertFalse(r["ok"])
        self.assertIn("扫描", r["err"])

    def test_12_list_filter_and_paging(self):
        with _patch_registry([_reg("Alpha"), _reg("Beta", pub="Microsoft"),
                              _reg("Gamma", key="G1", view="x86")]), \
                mock.patch.object(appscan, "uwp_list", return_value=[]):
            appscan.apps_cache_clear()
            appscan.scan_apps()
        api = self._api()
        r = api.opt_apps_list(kw="micro")
        self.assertTrue(r["ok"])
        self.assertEqual([x["name"] for x in r["data"]["items"]], ["Beta"])
        r2 = api.opt_apps_list(page_size=100)
        self.assertEqual(r2["data"]["total"], 3)   # x64 去重后 Alpha/Beta；Gamma 与 Alpha 同名同版本被并
        self.assertTrue(all("quiet_possible" in x for x in r2["data"]["items"]))

    def test_13_uninstall_busy_reject_and_done(self):
        api = self._api()
        with _patch_registry([_reg("QuietApp", key="Q1",
                                   quiet="C:/q.exe /S")]), \
                mock.patch.object(appscan, "uwp_list", return_value=[]):
            appscan.apps_cache_clear()
            appscan.scan_apps()
        # 占用互斥锁 → 第二次受理被拒
        self.assertTrue(api._opt_busy_start())
        r = api.opt_apps_uninstall(["registry|Q1"])
        self.assertFalse(r["ok"])
        self.assertIn("稍候", r["err"])
        api._opt_busy_end()
        # 正常受理 → worker 完成 → opt_done(tag=apps)
        done = {}
        with mock.patch.object(appscan, "run_elevated",
                               return_value=type(
                                   "R", (), {"stdout":
                                             'LT_UN:{"key":"registry|Q1","code":0}',
                                             "returncode": 0})):
            with mock.patch.object(api, "emit",
                                   side_effect=lambda n, d: done.setdefault(n, d)):
                r = api.opt_apps_uninstall(["registry|Q1"])
                self.assertTrue(r["ok"], r)
                for _ in range(60):
                    if "opt_done" in done:
                        break
                    time.sleep(0.05)
        self.assertEqual(done.get("opt_done", {}).get("tag"), "apps")
        self.assertTrue(done["opt_done"]["ok"])


class TestExtraSources(unittest.TestCase):
    """v5.4 二期：Scoop/Chocolatey 检测源 + 跨源去重。"""

    def setUp(self):
        appscan.apps_cache_clear()

    def tearDown(self):
        appscan.apps_cache_clear()

    def test_14_scoop_export_json(self):
        r = type("R", (), {"stdout": '{"apps":[{"name":"fzf","version":"0.44"},'
                                      '{"name":"jq","version":"1.7"}]}',
                           "returncode": 0})
        with mock.patch.object(appscan, "_cli_available", return_value=True), \
                mock.patch.object(appscan, "run", return_value=r):
            entries = appscan.scan_scoop()
        self.assertEqual([e.name for e in entries], ["fzf", "jq"])
        self.assertTrue(all(e.source == "scoop" and e.quiet_possible
                            for e in entries))
        self.assertEqual(entries[0].quiet_cmd, "scoop uninstall fzf")

    def test_15_scoop_absent_or_bad_json(self):
        with mock.patch.object(appscan, "_cli_available", return_value=False):
            self.assertEqual(appscan.scan_scoop(), [])
        r = type("R", (), {"stdout": "not-json", "returncode": 0})
        with mock.patch.object(appscan, "_cli_available", return_value=True), \
                mock.patch.object(appscan, "run", return_value=r):
            self.assertEqual(appscan.scan_scoop(), [])

    def test_16_choco_list_r(self):
        r = type("R", (), {"stdout": "chocolatey v2.2.2\n7zip|23.01\n"
                                     "git|2.43.0\n", "returncode": 0})
        with mock.patch.object(appscan, "_cli_available", return_value=True), \
                mock.patch.object(appscan, "run", return_value=r):
            entries = appscan.scan_choco()
        self.assertEqual([e.name for e in entries], ["7zip", "git"])
        self.assertTrue(all(e.quiet_possible for e in entries))

    def test_17_cross_source_dedupe(self):
        """注册表已有同名同版本 → Scoop/Choco 条目被跳过。"""
        rows = [_reg("git", "2.43.0", key="GitKey")]
        scoop = [appscan.AppEntry(name="git", source="scoop", key="git",
                                  version="2.43.0", scope="user",
                                  quiet_cmd="scoop uninstall git")]
        choco = [appscan.AppEntry(name="fzf", source="choco", key="fzf",
                                  version="0.44", scope="machine",
                                  quiet_cmd="choco uninstall fzf -y")]
        with mock.patch.object(appscan, "scan_registry",
                               return_value=[appscan.AppEntry(
                                   name="git", source="registry", key="GitKey",
                                   version="2.43.0", quiet_cmd="x")]), \
                mock.patch.object(appscan, "scan_uwp", return_value=[]), \
                mock.patch.object(appscan, "scan_scoop", return_value=scoop), \
                mock.patch.object(appscan, "scan_choco", return_value=choco):
            merged = appscan.scan_apps()
        srcs = sorted(e.source for e in merged)
        self.assertEqual(srcs, ["choco", "registry"])   # scoop 的 git 被并入注册表条目


class TestWingetUpgrade(unittest.TestCase):
    """v5.4 二期：winget 可升级检测与批量升级。"""

    def setUp(self):
        from app.core import optimizer
        self.optimizer = optimizer
        self._avail = mock.patch.object(
            optimizer, "winget_available", return_value=True)
        self._avail.start()
        self.addCleanup(self._avail.stop)

    def test_18_upgrade_list_parse(self):
        out = ('正在升级...\n'
               'Name            Id               Version     Available   Source\n'
               '-----------------------------------------------------------------\n'
               'Google Chrome   Google.Chrome    133.0.1     134.0.2     winget\n'
               '7-Zip           7zip.7zip        23.01       24.00       winget\n'
               '\n5 upgrades available.\n')
        r = type("R", (), {"stdout": out, "returncode": 0, "ok": True})
        with mock.patch.object(self.optimizer, "run", return_value=r):
            rows = self.optimizer.winget_upgrade_list()
        self.assertEqual([x["id"] for x in rows],
                         ["Google.Chrome", "7zip.7zip"])
        self.assertEqual(rows[0]["available"], "134.0.2")

    def test_19_upgrade_list_empty_when_no_winget(self):
        with mock.patch.object(self.optimizer, "winget_available",
                               return_value=False):
            self.assertEqual(self.optimizer.winget_upgrade_list(), [])

    def test_20_upgrade_batch(self):
        calls = []

        def fake_run(cmd, timeout=None, encoding="gbk"):
            calls.append(cmd)
            self.assertEqual(encoding, "utf-8")   # winget 输出必须按 UTF-8 解
            return type("R", (), {"ok": True, "output": "", "returncode": 0})

        with mock.patch.object(self.optimizer, "run", side_effect=fake_run):
            results = self.optimizer.winget_upgrade(
                ["A.B", "C.D"], progress_cb=lambda d, t, n: None)
        self.assertTrue(all(x["ok"] for x in results))
        self.assertEqual(len(calls), 2)
        self.assertIn("--silent", calls[0])

    def test_21_upgrade_empty_rejected(self):
        with self.assertRaises(ValueError):
            self.optimizer.winget_upgrade([])


if __name__ == "__main__":
    unittest.main()

