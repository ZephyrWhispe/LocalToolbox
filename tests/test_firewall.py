"""防火墙核心逻辑测试（v5.5）：不触碰真实防火墙，全部注入假 PowerShell 结果。

覆盖：枚举归一化、写操作脚本拼接与转义、管理员门禁、一键拦截判重、
高级规则参数校验、备份清理与审计记录。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import firewall as fw  # noqa: E402
from app.core.runner import CommandResult  # noqa: E402


def _ok_ps():
    """run_powershell 假对象：返回成功且记录脚本。"""
    calls = []

    def fake(script, timeout=None):
        calls.append(script)
        return CommandResult(0, "OK", "")

    fake.calls = calls
    return fake


FAKE_ENUM = {
    "profiles": [
        {"id": 1, "enabled": 1, "inbound": 1, "outbound": 1},
        {"id": 2, "enabled": True, "inbound": 0, "outbound": 1},
        {"id": 4, "enabled": 0, "inbound": 0, "outbound": 0},
        {"id": 99, "enabled": 1, "inbound": 1, "outbound": 1},  # 非法 id 应被丢弃
    ],
    "rules": [
        {"name": "test rule", "app": "C:\\a.exe", "svc": "",
         "en": 1, "dir": 1, "act": 1, "proto": 6,
         "lp": "80", "rp": "443", "ra": "10.0.0.0/8", "prof": 3},
        {"name": "LocalToolbox 拦截 foo（入站）", "app": "C:\\foo.exe", "svc": "",
         "en": True, "dir": 2, "act": 0, "proto": 256,
         "lp": "", "rp": "", "ra": "", "prof": 0},
        {"name": "LocalToolbox·自定义", "app": "", "svc": "",
         "en": 0, "dir": 2, "act": 1, "proto": 17,
         "lp": "53", "rp": "", "ra": "", "prof": 4},
    ],
}


class TestEnum(unittest.TestCase):
    def test_01_normalize(self):
        with mock.patch.object(fw, "run_powershell_json", return_value=FAKE_ENUM), \
             mock.patch.object(fw, "is_admin", return_value=False):
            st = fw.enum_state()
        self.assertEqual(len(st["profiles"]), 3)          # 非法 id 被丢弃
        by_name = {p["name"]: p for p in st["profiles"]}
        self.assertTrue(by_name["domain"]["enabled"])
        self.assertEqual(by_name["private"]["inbound"], "block")
        self.assertEqual(len(st["rules"]), 3)
        r0, r1, r2 = st["rules"]
        self.assertEqual(r0["dir"], "in")
        self.assertEqual(r0["action"], "allow")
        self.assertEqual(r0["proto"], "TCP")
        self.assertEqual(r0["profiles"], ["domain", "private"])
        self.assertFalse(r0["ours"])
        self.assertEqual(r1["dir"], "out")
        self.assertEqual(r1["action"], "block")
        self.assertEqual(r1["proto"], "Any")
        self.assertTrue(r1["ours"])
        self.assertEqual(r2["profiles"], ["public"])      # mask 0 → 全部
        self.assertFalse(st["admin"])

    def test_02_enum_failure(self):
        with mock.patch.object(fw, "run_powershell_json", return_value=None):
            with self.assertRaises(OSError):
                fw.enum_state()


class TestAdminGate(unittest.TestCase):
    def test_01_writes_require_admin(self):
        with mock.patch.object(fw, "is_admin", return_value=False):
            for fn, args in [
                (fw.set_profile_enabled, (1, True)),
                (fw.set_default_action, (1, "in", "allow")),
                (fw.toggle_rule, ("x", True)),
                (fw.delete_rule, ("x",)),
                (fw.block_app, ("C:\\nope.exe",)),
                (fw.unblock_all, ()),
                (fw.create_rule, ({"name": "n"},)),
                (fw.backup_now, ()),
                (fw.restore_backup, ("C:\\x.wfw",)),
            ]:
                r = fn(*args)
                self.assertFalse(r["ok"], fn.__name__)
                self.assertIn("提权重启", r["err"], fn.__name__)


class TestWriteScripts(unittest.TestCase):
    def setUp(self):
        self.ps = _ok_ps()

    def test_01_toggle_escape(self):
        with mock.patch.object(fw, "is_admin", return_value=True), \
             mock.patch.object(fw, "run_powershell", self.ps):
            r = fw.toggle_rule("it's a rule", True)
        self.assertTrue(r["ok"])
        self.assertIn("Rules.Item('it''s a rule')", self.ps.calls[0])
        self.assertIn("$r.Enabled = 1", self.ps.calls[0])

    def test_02_toggle_off(self):
        with mock.patch.object(fw, "is_admin", return_value=True), \
             mock.patch.object(fw, "run_powershell", self.ps):
            r = fw.toggle_rule("x", False)
        self.assertTrue(r["ok"])
        self.assertIn("$r.Enabled = 0", self.ps.calls[0])

    def test_03_delete_checks_exist(self):
        with mock.patch.object(fw, "is_admin", return_value=True), \
             mock.patch.object(fw, "run_powershell", self.ps):
            r = fw.delete_rule("a'b")
        self.assertTrue(r["ok"])
        self.assertIn("Item('a''b')", self.ps.calls[0])
        self.assertIn("Remove('a''b')", self.ps.calls[0])

    def test_04_ps_failure_bubbles(self):
        def fail(script, timeout=None):
            return CommandResult(1, "", "denied")
        with mock.patch.object(fw, "is_admin", return_value=True), \
             mock.patch.object(fw, "run_powershell", fail):
            r = fw.toggle_rule("x", True)
        self.assertFalse(r["ok"])
        self.assertIn("denied", r["err"])


class TestBlockApp(unittest.TestCase):
    def setUp(self):
        self.exe = os.path.join(tempfile.gettempdir(), "fw_test_app.exe")
        with open(self.exe, "w") as f:
            f.write("M")

    def tearDown(self):
        try:
            os.remove(self.exe)
        except OSError:
            pass

    def test_01_missing_file(self):
        with mock.patch.object(fw, "is_admin", return_value=True):
            r = fw.block_app("C:\\definitely_not_exist_9x.exe")
        self.assertFalse(r["ok"])
        self.assertIn("不存在", r["err"])

    def test_02_already_blocked(self):
        st = {"rules": [
            {"name": "LocalToolbox 拦截 fw_test_app（入站）", "app": self.exe},
            {"name": "LocalToolbox 拦截 fw_test_app（出站）", "app": self.exe},
        ]}
        with mock.patch.object(fw, "is_admin", return_value=True), \
             mock.patch.object(fw, "enum_state", return_value=st):
            r = fw.block_app(self.exe)
        self.assertTrue(r["ok"])
        self.assertTrue(r["data"]["already"])

    def test_03_creates_two_rules(self):
        ps = _ok_ps()
        st = {"rules": []}
        with mock.patch.object(fw, "is_admin", return_value=True), \
             mock.patch.object(fw, "enum_state", return_value=st), \
             mock.patch.object(fw, "run_powershell", ps):
            r = fw.block_app(self.exe)
        self.assertTrue(r["ok"])
        self.assertFalse(r["data"]["already"])
        self.assertEqual(len(r["data"]["names"]), 2)
        script = ps.calls[0]
        self.assertIn("LocalToolbox 拦截 fw_test_app（入站）", script)
        self.assertIn("LocalToolbox 拦截 fw_test_app（出站）", script)
        self.assertIn("$r.Action = 0", script)
        self.assertIn("Rules.Add($r)", script)

    def test_04_unblock_all_with_backup(self):
        ps = _ok_ps()
        st = {"rules": [
            {"name": "LocalToolbox 拦截 a（入站）", "app": "C:\\a.exe"},
            {"name": "其它规则", "app": ""},
        ]}
        with mock.patch.object(fw, "is_admin", return_value=True), \
             mock.patch.object(fw, "enum_state", return_value=st), \
             mock.patch.object(fw, "run_powershell", ps), \
             mock.patch.object(fw, "backup_now", return_value={
                 "ok": True, "data": {"name": "b.wfw"}}):
            r = fw.unblock_all()
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["removed"], 1)     # 只移除拦截规则
        self.assertIn("Remove('LocalToolbox 拦截 a（入站）')", ps.calls[-1])
        self.assertNotIn("其它规则", ps.calls[-1])

    def test_05_blocked_rules_filter(self):
        rules = fw._normalize_rules(FAKE_ENUM["rules"])
        blocked = fw.blocked_rules(rules)
        self.assertEqual(len(blocked), 1)
        self.assertIn("拦截", blocked[0]["name"])


class TestCreateRule(unittest.TestCase):
    def setUp(self):
        self.ps = _ok_ps()

    def _create(self, spec):
        with mock.patch.object(fw, "is_admin", return_value=True), \
             mock.patch.object(fw, "run_powershell", self.ps):
            return fw.create_rule(spec)

    def test_01_validation(self):
        cases = [
            ({}, "规则名"),
            ({"name": "x" * 200}, "过长"),
            ({"name": "n", "direction": "bad"}, "方向"),
            ({"name": "n", "direction": "in", "action": "bad"}, "动作"),
            ({"name": "n", "direction": "in", "action": "allow",
              "proto": "GRE"}, "协议"),
            ({"name": "n", "direction": "in", "action": "allow",
              "lports": "80"}, "仅 TCP/UDP"),
            ({"name": "n", "direction": "in", "action": "allow",
              "proto": "TCP", "lports": "99999"}, "端口"),
            ({"name": "n", "direction": "in", "action": "allow",
              "proto": "TCP", "lports": "abc"}, "端口格式"),
            ({"name": "n", "direction": "in", "action": "allow",
              "raddrs": "not an ip!!"}, "远程地址"),
            ({"name": "n", "direction": "in", "action": "allow",
              "profiles": ["lan"]}, "配置文件"),
            ({"name": "n", "direction": "in", "action": "allow",
              "program": "C:\\no_such_fw_tool.exe"}, "不存在"),
        ]
        for spec, kw in cases:
            r = self._create(spec)
            self.assertFalse(r["ok"], str(spec))
            self.assertIn(kw, r["err"], str(spec))

    def test_02_success_script(self):
        r = self._create({"name": "测试规则", "direction": "out",
                          "action": "block", "proto": "TCP",
                          "lports": "80,443", "raddrs": "10.0.0.0/8",
                          "profiles": ["domain", "public"]})
        self.assertTrue(r["ok"])
        self.assertTrue(r["data"]["name"].startswith("LocalToolbox·"))
        script = self.ps.calls[0]
        self.assertIn("$r.Name = 'LocalToolbox·测试规则'", script)
        self.assertIn("$r.Direction = 2", script)
        self.assertIn("$r.Action = 0", script)
        self.assertIn("$r.Protocol = 6", script)
        self.assertIn("$r.LocalPorts = '80,443'", script)
        self.assertIn("$r.RemoteAddresses = '10.0.0.0/8'", script)
        self.assertIn("$r.Profiles = 5", script)     # domain(1) + public(4)

    def test_03_any_proto_no_protocol_line(self):
        r = self._create({"name": "n", "direction": "in", "action": "allow"})
        self.assertTrue(r["ok"])
        self.assertNotIn("$r.Protocol", self.ps.calls[0])


class TestBackups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fwbk-")
        self._orig_dir = fw.BACKUP_DIR
        fw.BACKUP_DIR = self.tmp

    def tearDown(self):
        fw.BACKUP_DIR = self._orig_dir

    def test_01_prune_keeps_10(self):
        for i in range(12):
            with open(os.path.join(self.tmp, "fw_backup_%02d.wfw" % i), "w") as f:
                f.write("x")
        fw._prune_backups()
        left = os.listdir(self.tmp)
        self.assertEqual(len(left), 10)
        self.assertNotIn("fw_backup_00.wfw", left)   # 旧的被清
        self.assertIn("fw_backup_11.wfw", left)

    def test_02_backup_now_success(self):
        ps = _ok_ps()

        def make_file(script, timeout=None):
            # 从脚本中提取目标路径并真实创建，模拟 netsh export 成功
            path = script.split("'")[1]
            with open(path, "w") as f:
                f.write("wfw")
            return CommandResult(0, "Ok.", "")

        with mock.patch.object(fw, "is_admin", return_value=True), \
             mock.patch.object(fw, "run_powershell", make_file):
            r = fw.backup_now()
        self.assertTrue(r["ok"])
        self.assertTrue(r["data"]["name"].startswith("fw_backup_"))
        self.assertTrue(os.path.isfile(r["data"]["path"]))

    def test_03_backup_now_failure(self):
        def fail(script, timeout=None):
            return CommandResult(1, "", "boom")
        with mock.patch.object(fw, "is_admin", return_value=True), \
             mock.patch.object(fw, "run_powershell", fail):
            r = fw.backup_now()
        self.assertFalse(r["ok"])
        self.assertIn("boom", r["err"])

    def test_04_restore_validation(self):
        with mock.patch.object(fw, "is_admin", return_value=True):
            r = fw.restore_backup("C:\\nope.txt")
        self.assertFalse(r["ok"])

    def test_05_backup_list(self):
        with open(os.path.join(self.tmp, "fw_backup_a.wfw"), "w") as f:
            f.write("x")
        with open(os.path.join(self.tmp, "other.txt"), "w") as f:
            f.write("x")
        items = fw.backup_list()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "fw_backup_a.wfw")


class TestAudit(unittest.TestCase):
    def setUp(self):
        fd, self.tmp = tempfile.mkstemp(prefix="fwaudit-")
        os.close(fd)
        os.remove(self.tmp)
        self._orig = fw.AUDIT_PATH
        fw.AUDIT_PATH = self.tmp

    def tearDown(self):
        fw.AUDIT_PATH = self._orig
        try:
            os.remove(self.tmp)
        except OSError:
            pass

    def test_01_roundtrip_order(self):
        fw.audit_add("toggle", "规则A 启用")
        fw.audit_add("delete", "规则B")
        items = fw.audit_list()
        self.assertEqual(items[0]["action"], "delete")     # 最新在前
        self.assertEqual(items[1]["detail"], "规则A 启用")

    def test_02_limit(self):
        for i in range(10):
            fw.audit_add("op", str(i))
        self.assertEqual(len(fw.audit_list(3)), 3)

    def test_03_empty(self):
        self.assertEqual(fw.audit_list(), [])


if __name__ == "__main__":
    unittest.main()
