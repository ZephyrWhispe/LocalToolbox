"""v5.0 云备份测试：多目标并行、openlist 目标动态组装、时间戳命名、keep prune。

用 FakeWebDavClient 替换 cloud_backup.WebDavClient（不发起真实网络请求）。
"""

import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from app.core import cloud_backup  # noqa: E402
from app.core.cloud_backup import CloudBackup, normalize_targets  # noqa: E402


class FakeClient:
    """记录调用的假 WebDAV 客户端。"""

    created = []          # [(base_url, user, password)]
    remote_files = {}     # dir(str) -> {name: bytes}

    def __init__(self, base_url, user="", password="", timeout=10):
        FakeClient.created.append((base_url, user, password))
        self.base_url = base_url
        self.user = user
        self.password = password

    def mkdir(self, path):
        FakeClient.remote_files.setdefault(path, {})
        return True

    def listdir(self, path):
        files = FakeClient.remote_files.get(path, {})
        entries = [{"name": n, "path": f"/{n}", "is_dir": False,
                    "size": len(c), "mtime": ""} for n, c in files.items()]
        return {"entries": entries, "used": None, "avail": None}

    def upload(self, local_path, remote_dir=None, remote_path=None,
               progress_cb=None):
        with open(local_path, "rb") as f:
            data = f.read()
        name = remote_path.rsplit("/", 1)[-1]
        FakeClient.remote_files.setdefault(remote_dir, {})[name] = data
        return True

    def delete(self, path):
        dirp, name = path.rsplit("/", 1)
        FakeClient.remote_files.get(dirp, {}).pop(name, None)
        return True

    def download(self, remote_path, local_path, progress_cb=None):
        dirp, name = remote_path.rsplit("/", 1)
        data = FakeClient.remote_files.get(dirp, {})[name]
        with open(local_path, "wb") as f:
            f.write(data)
        return local_path


def make_backup(cfg):
    return CloudBackup(cfg)


def make_cfg(tmp, targets=None):
    from app.core.config import AppConfig

    cfg = AppConfig(path=str(Path(tmp) / "config.json"))
    if targets is not None:
        cfg.set("backup_targets", targets)
    cfg.set("openlist_host", "127.0.0.1")
    cfg.set("openlist_port", 15244)
    cfg.set("rclone_user", "ol-user")
    cfg.set("rclone_pwd", "ol-pwd")
    return cfg


class TestNormalize(unittest.TestCase):
    def test_normalize(self):
        out = normalize_targets([
            {"type": "bad", "name": "x" * 50, "url": " u ", "dir": ""},
            "not-a-dict",
            {"type": "webdav", "enabled": False},
        ])
        self.assertEqual(len(out), 2)  # 非 dict 丢弃
        self.assertEqual(out[0]["type"], "openlist")  # 非法类型回退
        self.assertEqual(out[0]["name"], "x" * 32)
        self.assertEqual(out[0]["dir"], "LocalToolboxBackup")
        self.assertEqual(out[0]["url"], "u")
        self.assertEqual(out[1]["type"], "webdav")
        self.assertFalse(out[1]["enabled"])

    def test_cap5(self):
        out = normalize_targets([{"type": "webdav"}] * 8)
        self.assertEqual(len(out), 5)


class TestResolve(unittest.TestCase):
    """真实 WebDavClient 构造（纯字符串处理，无网络）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bak-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_openlist_dynamic(self):
        import base64
        cfg = make_cfg(self.tmp)
        b = make_backup(cfg)
        client, segs = b._resolve({"type": "openlist", "dir": "BK/A"})
        self.assertEqual(client.base_path, "/dav")
        self.assertEqual((client.host, client.port), ("127.0.0.1", 15244))
        auth = client.headers["Authorization"]
        self.assertEqual(base64.b64decode(auth.split()[1]).decode(),
                         "ol-user:ol-pwd")
        self.assertEqual(segs, ["BK", "A"])

    def test_webdav_custom(self):
        cfg = make_cfg(self.tmp)
        b = make_backup(cfg)
        client, segs = b._resolve({
            "type": "webdav", "url": "https://dav.example.com/dav/",
            "user": "u", "pwd": "p", "dir": ""})
        self.assertEqual(client.host, "dav.example.com")
        self.assertEqual(client.port, 443)
        self.assertEqual(segs, ["LocalToolboxBackup"])  # 空 dir 回默认
        with self.assertRaises(cloud_backup.BackupTargetError):
            b._resolve({"type": "webdav", "url": ""})  # 未填地址


class TestBackupFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bak-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        FakeClient.created = []
        FakeClient.remote_files = {}
        self.patches = [
            mock.patch.object(cloud_backup, "WebDavClient", FakeClient),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_backup_one_and_prune(self):
        cfg = make_cfg(self.tmp, targets=[
            {"type": "openlist", "name": "网盘", "dir": "BK", "enabled": True}])
        cfg.set("backup_keep", 3)
        b = make_backup(cfg)
        target = b.targets()[0]
        for i in range(5):
            p = Path(self.tmp) / f"memo_{i}.json"
            p.write_text(f"{{{i}}}", encoding="utf-8")
            name = b.backup_one(target, "memo", str(p))
            self.assertTrue(name.startswith("memo_") and name.endswith(".json"))
        files = sorted(FakeClient.remote_files["BK"])
        self.assertEqual(len(files), 3)  # keep=3，最旧 2 份被清理
        self.assertTrue(all(n.startswith("memo_") for n in files))

    def test_list_remote_filters(self):
        cfg = make_cfg(self.tmp, targets=[
            {"type": "webdav", "url": "http://x/dav", "dir": "BK"}])
        b = make_backup(cfg)
        t = b.targets()[0]
        FakeClient.remote_files["BK"] = {
            "memo_1.json": b"{}", "vault_1.dat": b"x",
            "other.txt": b"y",
        }
        memo = b.list_remote(t, "memo")
        vault = b.list_remote(t, "vault")
        self.assertEqual([f["name"] for f in memo], ["memo_1.json"])
        self.assertEqual([f["name"] for f in vault], ["vault_1.dat"])

    def test_worker_multi_target(self):
        from app.bridge.backup_api import BackupApi
        from app.core.config import AppConfig

        cfg = AppConfig(path=str(Path(self.tmp) / "config.json"))
        cfg.set("backup_targets", [
            {"type": "webdav", "name": "A", "url": "http://a/dav", "dir": "A"},
            {"type": "webdav", "name": "B", "url": "http://b/dav", "dir": "B"},
        ])
        events = []

        class Stub:
            pass

        stub = Stub()
        stub.cfg = cfg
        stub._backup = CloudBackup(cfg)
        stub._backup_lock = threading.Lock()  # v5.1c：对齐 _init_backup
        stub._backup_running = False
        memo_src = Path(self.tmp) / "memos.db"
        memo_src.write_text("{}", encoding="utf-8")
        stub._memos = mock.Mock()
        stub._memos.export.return_value = {"groups": [], "memos": []}
        stub._vault_path = lambda: str(memo_src)  # 复用既有文件代表 vault
        stub._memo_export_file = lambda: BackupApi._memo_export_file(stub)
        stub.emit = lambda name, payload=None: events.append((name, payload))
        stub.emit_log = lambda m: events.append(("log", m))
        stub._backup_worker = lambda t, k: BackupApi._backup_worker(stub, t, k)
        stub._backup_worker_inner = lambda t, k: BackupApi._backup_worker_inner(stub, t, k)

        r = BackupApi.backup_run(stub, "all")
        self.assertTrue(r["ok"])
        for _ in range(100):
            if any(n == "backup_done" for n, _ in events):
                break
            threading.Event().wait(0.05)
        done = [p for n, p in events if n == "backup_done"][0]
        self.assertEqual(done["ok_count"], 4)  # 2 目标 × memo+vault
        self.assertEqual(done["fail_count"], 0)
        self.assertIn("memo_", " ".join(FakeClient.remote_files["A"]))
        self.assertIn("vault_", " ".join(FakeClient.remote_files["B"]))

    def test_restore_memo(self):
        from app.bridge.backup_api import BackupApi
        from app.core.config import AppConfig
        from app.core.memo_store import MemoStore

        cfg = AppConfig(path=str(Path(self.tmp) / "config.json"))
        cfg.set("backup_targets", [
            {"type": "webdav", "name": "A", "url": "http://a/dav", "dir": "BK"}])
        FakeClient.remote_files["BK"] = {
            "memo_1.json": json.dumps({
                "groups": [{"id": 1, "name": "G", "sort": 0}],
                "memos": [{"id": 1, "title": "T", "content": "C",
                           "group_id": 1, "pinned": 0, "created": 1, "updated": 1}],
            }).encode("utf-8"),
        }
        store = MemoStore(str(Path(self.tmp) / "m.db"))
        store.add("将被清空")

        class Stub:
            pass

        stub = Stub()
        stub.cfg = cfg
        stub._backup = CloudBackup(cfg)
        stub._memos = store
        stub.emit = lambda *a, **k: None
        stub.emit_log = lambda *a, **k: None
        r = BackupApi.backup_restore(stub, "memo", 0, "memo_1.json", "replace")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["data"]["count"], 1)
        self.assertEqual(store.list("C")[0]["title"], "T")


if __name__ == "__main__":
    unittest.main()
