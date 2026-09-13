"""v5.1 密码库导入导出测试：多来源解析 / 加密容器往返 / 校验与坏行报告。"""

import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from app.core import vault_io
from app.core.vault import Vault

sys.path.insert(0, str(Path(__file__).parent))


def _w(tmp, name, content, binary=False):
    p = Path(tmp) / name
    if binary:
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return str(p)


CHROME_CSV = ("name,url,username,password\n"
              "GitHub,https://github.com,tom,gh-pw\n"
              "简书,https://jianshu.com,li,js-pw\n")
FIREFOX_CSV = ('"url","username","password","httpRealm","formActionOrigin","guid"\n'
               '"https://moz.org","ff","ff-pw","","","g1"\n')
SAFARI_CSV = ("Title,URL,Username,Password,Notes,OTPAuth\n"
              "邮箱,https://mail.qq.com,alice,qq-pw,工作邮箱,\n")
KEEPASS_CSV = ('"Account","Login Name","Password","Web Site","Comments"\n'
               '"NAS","admin","nas-pw","https://nas.local","内网"\n')
BITWARDEN_JSON = {
    "folders": [{"id": "f1", "name": "工作"}],
    "items": [
        {"name": "BW 站点", "notes": "n1",
         "login": {"username": "bw", "password": "bw-pw",
                   "uris": [{"uri": "https://bw.com"}]},
         "folderId": "f1"},
        {"name": "无密码条目", "login": {}},
    ],
}
BAD_CSV = ("name,url,username,password\n"
           "好的,https://ok.com,u1,p1\n"
           "缺两列,,\n")


class TestSniff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vio-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_sniff_all(self):
        self.assertEqual(vault_io.sniff(_w(self.tmp, "c.csv", CHROME_CSV)), "csv_generic")
        self.assertEqual(vault_io.sniff(_w(self.tmp, "f.csv", FIREFOX_CSV)), "csv_firefox")
        self.assertEqual(vault_io.sniff(_w(self.tmp, "s.csv", SAFARI_CSV)), "csv_generic")
        self.assertEqual(vault_io.sniff(_w(self.tmp, "k.csv", KEEPASS_CSV)), "csv_keepass")
        self.assertEqual(vault_io.sniff(_w(self.tmp, "bw.json", json.dumps(BITWARDEN_JSON))),
                         "bitwarden_json")
        self.assertEqual(vault_io.sniff(_w(self.tmp, "loc.json", '{"entries":[]}')),
                         "local_json")
        self.assertEqual(vault_io.sniff(_w(self.tmp, "e.lvt", "LTVAULT1|x|x|x|x")),
                         "encrypted")


class TestParse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vio-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _parse(self, name, content):
        return vault_io.parse_file(_w(self.tmp, name, content))

    def test_chrome(self):
        r = self._parse("c.csv", CHROME_CSV)
        self.assertEqual(r["format"], "csv_generic")
        self.assertEqual(len(r["entries"]), 2)
        e = r["entries"][0]
        self.assertEqual((e["title"], e["username"], e["password"]), ("GitHub", "tom", "gh-pw"))
        self.assertEqual(e["url"], "https://github.com")

    def test_firefox_title_from_host(self):
        r = self._parse("f.csv", FIREFOX_CSV)
        self.assertEqual(len(r["entries"]), 1)
        self.assertEqual(r["entries"][0]["title"], "moz.org")  # 无标题列 → 取 host

    def test_safari_note(self):
        r = self._parse("s.csv", SAFARI_CSV)
        self.assertEqual(r["entries"][0]["note"], "工作邮箱")
        self.assertEqual(r["entries"][0]["group"], "")

    def test_keepass(self):
        r = self._parse("k.csv", KEEPASS_CSV)
        self.assertEqual(r["entries"][0]["username"], "admin")
        self.assertEqual(r["entries"][0]["note"], "内网")

    def test_bitwarden_json(self):
        r = self._parse("bw.json", json.dumps(BITWARDEN_JSON))
        self.assertEqual(len(r["entries"]), 1)  # 无密码且无用户名的条目进 errors
        e = r["entries"][0]
        self.assertEqual((e["title"], e["username"], e["group"]), ("BW 站点", "bw", "工作"))
        self.assertEqual(len(r["errors"]), 1)
        self.assertIn("均为空", r["errors"][0]["reason"])

    def test_bad_rows_reported(self):
        r = self._parse("bad.csv", BAD_CSV)
        self.assertEqual(len(r["entries"]), 1)
        self.assertEqual(len(r["errors"]), 1)
        self.assertEqual(r["errors"][0]["row"], 3)

    def test_local_json(self):
        r = self._parse("l.json", json.dumps({"entries": [
            {"title": "A", "username": "u", "password": "p", "group": "G"}]}))
        self.assertEqual(r["entries"][0]["group"], "G")


class TestEncryptedRoundtrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vio-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_roundtrip(self):
        entries = [{"title": "站点", "username": "u", "password": "p&.,x",
                    "url": "https://x.com", "note": "备注,带逗号", "group": "G"}]
        p = _w(self.tmp, "out.lvt", "")
        vault_io.write_encrypted(entries, p, "export-pw")
        self.assertTrue(Path(p).read_text(encoding="utf-8").startswith("LTVAULT1|"))
        r = vault_io.parse_encrypted_line(Path(p).read_text(encoding="utf-8"), "export-pw")
        self.assertEqual(len(r["entries"]), 1)
        self.assertEqual(r["entries"][0]["password"], "p&.,x")
        # 错口令 → 报告形式错误
        bad = vault_io.parse_encrypted_line(Path(p).read_text(encoding="utf-8"), "wrong")
        self.assertEqual(len(bad["entries"]), 0)
        self.assertIn("口令错误", bad["errors"][0]["reason"])

    def test_csv_roundtrip(self):
        entries = [{"title": "A,B", "url": "https://a", "username": "u,1",
                    "password": "p\"q", "note": "n", "group": ""}]
        p = _w(self.tmp, "out.csv", "")
        vault_io.write_csv(entries, p)
        r = vault_io.parse_file(p)
        self.assertEqual(len(r["entries"]), 1)
        e = r["entries"][0]
        self.assertEqual((e["title"], e["username"], e["password"]), ("A,B", "u,1", 'p"q'))


class TestVaultAddMany(unittest.TestCase):
    def test_add_many_single_write(self):
        tmp = tempfile.mkdtemp(prefix="vio-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        v = Vault(str(Path(tmp) / "vault.dat"))
        v.create("pw")
        n = v.add_many([{"title": "T%d" % i, "username": "u", "password": "p"}
                        for i in range(50)])
        self.assertEqual(n, 50)
        self.assertEqual(len(v.entries()), 50)
        v.lock()
        v.unlock("pw")
        self.assertEqual(len(v.entries()), 50)


if __name__ == "__main__":
    unittest.main()
