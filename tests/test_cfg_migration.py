# -*- coding: utf-8 -*-
"""整机配置导出/导入（v5.4 O7）测试：脱敏 / 校验和 / 逐键合并策略。

运行：python -m pytest tests/test_cfg_migration.py -q
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import DEFAULTS, AppConfig  # noqa: E402


def make_stub():
    from app.bridge.bridge import Bridge

    class _Clip:
        history_limit = 100
        history = []
        store = None

    class Stub:
        pass

    s = Stub()
    tmp = tempfile.mkdtemp(prefix="cfg-mig-")
    s._tmp = tmp
    s.cfg = AppConfig(path=str(Path(tmp) / "config.json"))
    s.clip = _Clip()
    return s, Bridge


class TestCfgExport(unittest.TestCase):
    def setUp(self):
        self.stub, self.Bridge = make_stub()
        self.addCleanup(lambda: shutil.rmtree(self.stub._tmp, ignore_errors=True))
        self.path = str(Path(self.stub._tmp) / "exp.json")

    def test_01_export_structure(self):
        r = self.Bridge.cfg_export(self.stub, self.path, sanitize=True)
        self.assertTrue(r["ok"])
        payload = json.loads(Path(self.path).read_text(encoding="utf-8"))
        self.assertEqual(payload["app"], "LocalToolbox")
        self.assertTrue(payload["version"])
        self.assertIn("checksum", payload)
        self.assertIn("theme", payload["data"])

    def test_02_sanitize_strips_secrets(self):
        self.stub.cfg.set("rclone_pwd", "super-secret")
        self.stub.cfg.set("upload_custom_key", "key-xyz")
        self.stub.cfg.set("backup_targets", [
            {"type": "webdav", "name": "n", "url": "u", "user": "a",
             "pwd": "webdav-pwd", "dir": "d", "enabled": True}])
        r = self.Bridge.cfg_export(self.stub, self.path, sanitize=True)
        self.assertTrue(r["ok"])
        payload = json.loads(Path(self.path).read_text(encoding="utf-8"))
        self.assertNotIn("rclone_pwd", payload["data"])
        self.assertNotIn("upload_custom_key", payload["data"])
        bt = payload["data"]["backup_targets"][0]
        self.assertEqual(bt["pwd"], "")
        self.assertEqual(bt["url"], "u")
        # 不脱敏时保留原值
        r2 = self.Bridge.cfg_export(self.stub, self.path, sanitize=False)
        self.assertTrue(r2["ok"])
        payload2 = json.loads(Path(self.path).read_text(encoding="utf-8"))
        self.assertEqual(payload2["data"]["rclone_pwd"], "super-secret")

    def test_03_export_default_path(self):
        r = self.Bridge.cfg_export(self.stub, None, sanitize=True)
        self.assertTrue(r["ok"])
        self.assertIn("exports", r["data"]["path"])


class TestCfgImport(unittest.TestCase):
    def setUp(self):
        self.stub, self.Bridge = make_stub()
        self.addCleanup(lambda: shutil.rmtree(self.stub._tmp, ignore_errors=True))
        self.path = str(Path(self.stub._tmp) / "exp.json")

    def _export(self):
        r = self.Bridge.cfg_export(self.stub, self.path, sanitize=True)
        self.assertTrue(r["ok"])

    def test_04_roundtrip_overwrite(self):
        self.stub.cfg.set("desensitize", False)
        self._export()
        # 模拟新机器：改回默认再导入
        self.stub.cfg.set("desensitize", True)
        r = self.Bridge.cfg_import(self.stub, self.path, "overwrite")
        self.assertTrue(r["ok"], r)
        self.assertGreater(r["data"]["merged"], 0)
        self.assertEqual(self.stub.cfg.get("desensitize"), False)

    def test_05_skip_existing_keeps_local(self):
        self.stub.cfg.set("desensitize", False)
        self.stub.cfg.set("history_limit", 500)
        self._export()
        # 本机 start_page 已改为非默认值 → 存量优先应保留
        self.stub.cfg.set("start_page", "tools")
        r = self.Bridge.cfg_import(self.stub, self.path, "skip_existing")
        self.assertTrue(r["ok"])
        self.assertEqual(self.stub.cfg.get("start_page"), "tools")
        # 本机为默认值的键被导入
        self.assertEqual(self.stub.cfg.get("history_limit"), 500)

    def test_06_checksum_tamper_rejected(self):
        self._export()
        payload = json.loads(Path(self.path).read_text(encoding="utf-8"))
        payload["data"]["desensitize"] = not payload["data"]["desensitize"]
        Path(self.path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        r = self.Bridge.cfg_import(self.stub, self.path, "overwrite")
        self.assertFalse(r["ok"])
        self.assertIn("校验和", r["err"])

    def test_07_bad_file_rejected(self):
        Path(self.path).write_text('{"hello": 1}', encoding="utf-8")
        r = self.Bridge.cfg_import(self.stub, self.path, "overwrite")
        self.assertFalse(r["ok"])
        Path(self.path).write_text("not json", encoding="utf-8")
        r = self.Bridge.cfg_import(self.stub, self.path, "overwrite")
        self.assertFalse(r["ok"])

    def test_08_unknown_keys_skipped(self):
        self._export()
        payload = json.loads(Path(self.path).read_text(encoding="utf-8"))
        payload["data"]["not_a_real_key"] = 1
        # 重新计算校验和（模拟新版本应用导出的额外键）
        import hashlib
        body = json.dumps({k: v for k, v in payload.items() if k != "checksum"},
                          ensure_ascii=False, sort_keys=True, indent=2)
        payload["checksum"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        Path(self.path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        r = self.Bridge.cfg_import(self.stub, self.path, "overwrite")
        self.assertTrue(r["ok"])
        self.assertNotIn("not_a_real_key", self.stub.cfg.data)


if __name__ == "__main__":
    unittest.main()
