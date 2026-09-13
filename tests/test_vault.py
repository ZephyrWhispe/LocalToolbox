"""v5.0 密码库测试：加密容器生命周期、口令错误、改口令、条目 CRUD、生成器。"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.core.vault import Vault, VaultError, generate_password  # noqa: E402


def make_vault():
    tmp = tempfile.mkdtemp(prefix="vault-")
    v = Vault(str(Path(tmp) / "vault.dat"))
    v._tmp = tmp
    return v


class TestVault(unittest.TestCase):
    def setUp(self):
        self.v = make_vault()
        self.addCleanup(shutil.rmtree, self.v._tmp, ignore_errors=True)

    def test_lifecycle(self):
        self.assertFalse(self.v.exists)
        with self.assertRaises(VaultError):
            self.v.unlock("pw")  # 未创建
        self.v.create("master-pw")
        self.assertTrue(self.v.exists)
        with self.assertRaises(VaultError):
            self.v.create("again")  # 重复创建
        # 错口令 → GCM InvalidTag →「口令错误」
        self.v.lock()
        with self.assertRaises(VaultError) as cm:
            self.v.unlock("wrong")
        self.assertIn("口令错误", str(cm.exception))
        # 正确解锁
        self.v.unlock("master-pw")
        self.assertTrue(self.v.unlocked)
        self.assertEqual(self.v.entries(), [])
        self.v.lock()
        self.assertFalse(self.v.unlocked)
        with self.assertRaises(VaultError):
            self.v.entries()  # 锁定后拒绝

    def test_entries_crud(self):
        self.v.create("pw")
        eid = self.v.upsert({"title": "站点A", "username": "u1",
                             "password": "p1", "url": "https://a",
                             "note": "", "group": "工作"})
        eid2 = self.v.upsert({"title": "站点B", "username": "u2",
                              "password": "p2", "group": ""})
        self.assertEqual(eid2, eid + 1)
        self.assertEqual(len(self.v.entries()), 2)
        self.assertEqual(self.v.groups(), ["工作"])
        # 更新
        self.v.upsert({"id": eid, "title": "站点A2", "username": "u1",
                       "password": "p9", "group": "工作"})
        self.assertEqual(self.v.entry(eid)["password"], "p9")
        # 锁定重开后数据仍在
        self.v.lock()
        self.v.unlock("pw")
        self.assertEqual(self.v.entry(eid)["title"], "站点A2")
        # 删除
        self.assertTrue(self.v.delete(eid2))
        with self.assertRaises(VaultError):
            self.v.delete(eid2)

    def test_change_password(self):
        self.v.create("old")
        self.v.upsert({"title": "T", "username": "u", "password": "p",
                       "group": ""})
        with self.assertRaises(VaultError):
            self.v.change_password("bad", "new")
        self.v.change_password("old", "new")
        # 旧口令失效、新口令可用（跨实例验证）
        fresh = Vault(self.v.path)
        with self.assertRaises(VaultError):
            fresh.unlock("old")
        fresh.unlock("new")
        self.assertEqual(fresh.entries()[0]["password"], "p")

    def test_restore_corrupt_rejected(self):
        self.v.create("pw")
        Path(self.v.path).write_text("NOT A VAULT", encoding="utf-8")
        fresh = Vault(self.v.path)
        with self.assertRaises(VaultError):
            fresh.unlock("pw")


class TestGenerate(unittest.TestCase):
    def test_length_and_charset(self):
        for n in (8, 16, 32, 64):
            p = generate_password(n, True)
            self.assertEqual(len(p), n)
            self.assertTrue(any(c.islower() for c in p))
            self.assertTrue(any(c.isupper() for c in p))
            self.assertTrue(any(c.isdigit() for c in p))
        p = generate_password(16, False)
        self.assertFalse(any(c in "!@#$%^&*()-_=+[]{};:,.<>?" for c in p))


if __name__ == "__main__":
    unittest.main()
