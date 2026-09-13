"""v5.0 备忘录测试：MemoStore CRUD/分组/搜索/pin/备份导出 + memo_api 桩测。"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.core.memo_store import MemoStore, derive_title  # noqa: E402


def make_store():
    tmp = tempfile.mkdtemp(prefix="memo-")
    store = MemoStore(str(Path(tmp) / "memos.db"))
    store._tmp = tmp
    return store


class TestDeriveTitle(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(derive_title("# 项目计划\n正文"), "项目计划")
        self.assertEqual(derive_title("- [ ] 待办事项"), "待办事项")
        self.assertEqual(derive_title("普通首行"), "普通首行")
        self.assertEqual(derive_title(""), "无标题")
        self.assertEqual(derive_title("x" * 100), "x" * 40)


class TestMemoStore(unittest.TestCase):
    def setUp(self):
        self.s = make_store()
        self.addCleanup(shutil.rmtree, self.s._tmp, ignore_errors=True)

    def test_group_lifecycle(self):
        gid = self.s.group_add("工作")
        self.assertGreater(gid, 0)
        with self.assertRaises(ValueError):
            self.s.group_add("工作")  # 重名
        self.s.group_rename(gid, "工作2")
        names = [g["name"] for g in self.s.groups()]
        self.assertIn("工作2", names)
        mid = self.s.add("内容", gid)
        self.s.group_delete(gid)
        m = self.s.get(mid)
        self.assertIsNone(m["group_id"])  # 删组 → 备忘移入未分组
        self.assertNotIn("工作2", [g["name"] for g in self.s.groups()])

    def test_crud_and_search(self):
        gid = self.s.group_add("笔记")
        mid = self.s.add("# 标题A\n包含python关键词", gid)
        m = self.s.get(mid)
        self.assertEqual(m["title"], "标题A")
        self.assertIn("python", m["content"])
        # 更新
        self.s.save(mid, "新标题", "新内容", gid, pinned=True)
        self.s.set_pin(mid, False)
        m = self.s.get(mid)
        self.assertEqual(m["title"], "新标题")
        self.assertFalse(m["pinned"])
        # 搜索（标题 + 内容）
        hits = self.s.list("新内容")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], mid)
        hits = self.s.list("新标题")
        self.assertEqual(len(hits), 1)
        # 组过滤：0 = 未分组
        other = self.s.add("未分组内容", None)
        self.assertEqual([x["id"] for x in self.s.list("", 0)], [other])
        self.assertEqual([x["id"] for x in self.s.list("", gid)], [mid])
        # 删除
        self.assertTrue(self.s.delete(mid))
        self.assertFalse(self.s.delete(mid))

    def test_pin_order_and_preview(self):
        a = self.s.add("第一条\n内容A")
        b = self.s.add("第二条\n内容B")
        self.s.set_pin(b, True)
        ids = [m["id"] for m in self.s.list()]
        self.assertEqual(ids[0], b)  # 置顶在前
        first = self.s.list()[0]
        self.assertIn("内容B", first["preview"])
        self.assertTrue(first["pinned"])
        self.s.delete(a)
        self.s.delete(b)

    def test_export_replace_merge(self):
        gid = self.s.group_add("旧组")
        self.s.add("# 备份测试\n正文", gid)
        data = self.s.export()
        self.assertEqual(len(data["memos"]), 1)
        self.assertEqual(len(data["groups"]), 1)
        # 替换恢复到新库
        s2 = make_store()
        self.addCleanup(shutil.rmtree, s2._tmp, ignore_errors=True)
        self.s.add("会被清掉")
        n = self.s.replace_all(data)
        self.assertEqual(n, 1)
        self.assertEqual(len(self.s.list("会被清掉")), 0)
        self.assertEqual([g["name"] for g in self.s.groups() if g["id"]], ["旧组"])
        # 合并恢复：同名分组复用，条目追加
        n2 = s2.merge_in(data)
        self.assertEqual(n2, 1)
        self.assertEqual(s2.groups()[1]["name"], "旧组")


class TestMemoApi(unittest.TestCase):
    def setUp(self):
        from app.bridge.bridge import Bridge
        from app.core.config import AppConfig

        self.Bridge = Bridge
        tmp = tempfile.mkdtemp(prefix="memoapi-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        class Stub:
            pass

        self.stub = Stub()
        self.stub.cfg = AppConfig(path=str(Path(tmp) / "config.json"))
        self.stub._memos = MemoStore(str(Path(tmp) / "memos.db"))
        self.stub._memo_groups = lambda: self.stub._memos.groups()

    def test_save_and_list(self):
        r = self.Bridge.memo_save(self.stub, 0, "", "# Hello\nworld", None, False)
        self.assertTrue(r["ok"])
        mid = r["data"]["id"]
        r = self.Bridge.memo_list(self.stub, "world", None)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["data"]["memos"]), 1)
        self.assertEqual(r["data"]["memos"][0]["title"], "Hello")
        # 更新
        r = self.Bridge.memo_save(self.stub, mid, "T2", "c2", None, True)
        self.assertTrue(r["ok"])
        r = self.Bridge.memo_get(self.stub, mid)
        self.assertEqual(r["data"]["title"], "T2")
        self.assertTrue(r["data"]["pinned"])
        # 删除
        self.assertTrue(self.Bridge.memo_delete(self.stub, mid)["data"])
        self.assertFalse(self.Bridge.memo_get(self.stub, mid)["ok"])

    def test_groups_api(self):
        r = self.Bridge.memo_group_add(self.stub, "工作")
        self.assertTrue(r["ok"])
        gid = r["data"]["id"]
        r = self.Bridge.memo_group_add(self.stub, "工作")
        self.assertFalse(r["ok"])
        r = self.Bridge.memo_group_rename(self.stub, gid, "生活")
        self.assertTrue(r["ok"])
        mid = self.Bridge.memo_save(self.stub, 0, "", "内容", gid, False)["data"]["id"]
        r = self.Bridge.memo_group_delete(self.stub, gid)
        self.assertTrue(r["ok"])
        self.assertIsNone(self.Bridge.memo_get(self.stub, mid)["data"]["group_id"])


if __name__ == "__main__":
    unittest.main()
