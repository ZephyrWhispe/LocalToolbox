# -*- coding: utf-8 -*-
"""P2-2 剪贴板历史 SQLite 落盘测试：存储单测 + ClipboardSync 接入 + 重启恢复。

含图片条目删除/清空时同步清理落盘文件（v2.7 低优先级改进）。
"""

import io
import os
import shutil
import sys
import tempfile
import time
import unittest

from PIL import Image

from app.core import clipboard_sync as cs
from app.core.clipboard_store import ClipboardStore
from app.core.clipboard_sync import ClipboardSync


def _png_bytes(w=48, h=32, color=(30, 180, 120)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


class TestClipboardStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="clip-store-")
        self.path = os.path.join(self.dir, "history.db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _st(self, limit=100):
        return ClipboardStore(self.path, limit=limit)

    def _entry(self, text, ts=None):
        return {
            "ts": ts if ts is not None else time.time(),
            "device": "本机",
            "hash": text[-8:],
            "remote": False,
            "kind": "text",
            "text": text,
            "img_path": None,
        }

    def test_01_insert_load_order(self):
        st = self._st()
        st.insert(self._entry("第一条", ts=100.0))
        st.insert(self._entry("第二条", ts=200.0))
        st.insert(self._entry("第三条", ts=150.0))
        rows = st.load(10)
        self.assertEqual([r["text"] for r in rows], ["第二条", "第三条", "第一条"])
        st.close()

    def test_02_delete_clear(self):
        st = self._st()
        st.insert(self._entry("要删的", ts=1.0))
        st.insert(self._entry("保留的", ts=2.0))
        st.delete("要删的")  # 按 hash 删
        rows = st.load(10)
        self.assertEqual([r["text"] for r in rows], ["保留的"])
        st.clear()
        self.assertEqual(st.count(), 0)
        st.close()

    def test_03_prune_limit(self):
        st = self._st(limit=3)
        for i in range(5):
            st.insert(self._entry("第%d条" % i, ts=float(i)))
        rows = st.load(10)
        # ts 最大 3 条保留：第 2/3/4 条
        self.assertEqual([r["text"] for r in rows], ["第4条", "第3条", "第2条"])
        self.assertEqual(st.count(), 3)
        st.close()

    def test_04_persist_across_instances(self):
        """关闭 store 后重建（模拟重启）→ 历史可恢复。"""
        st1 = self._st()
        st1.insert(self._entry("重启后还在", ts=100.0))
        st1.insert(self._entry("机器名", ts=90.0))
        st1.close()
        st2 = self._st()
        rows = st2.load(10)
        self.assertEqual([r["text"] for r in rows], ["重启后还在", "机器名"])
        st2.close()

    def test_10_file_entry_persist_across_instances(self):
        """文件条目（文件名/大小/路径）写库并重启回填（v2.8 新列兼容旧库）。"""
        st1 = self._st()
        st1.insert({
            "ts": time.time(), "device": "B机", "hash": "filehash1",
            "remote": True, "kind": "file", "text": None, "img_path": None,
            "file_path": "C:/data/clipboard_files/ab_notes.txt",
            "file_name": "notes.txt", "file_size": 1234,
        })
        st1.close()
        st2 = self._st()
        rows = st2.load(10)
        self.assertEqual(len(rows), 1)
        e = rows[0]
        self.assertEqual(e["kind"], "file")
        self.assertEqual(e["file_name"], "notes.txt")
        self.assertEqual(e["file_size"], 1234)
        self.assertIn("ab_notes.txt", e["file_path"])
        st2.close()


class TestSyncWithStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="clip-sync-store-")
        self.path = os.path.join(self.dir, "history.db")
        self.awrites = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _sync(self, port, device_id, name, store=None):
        return ClipboardSync(
            sync_port=port,
            device_id=device_id,
            name=name,
            reader=lambda: None,
            writer=lambda t: self.awrites.append(t),
            store=store,
        )

    def test_05_write_through_and_reload(self):
        """广播文本 → 历史写库；重建实例（同库）→ 历史自动回填。"""
        st = ClipboardStore(self.path, limit=100)
        a = self._sync(42001, "dev-a", "A机", store=st)
        try:
            a.broadcast_text("落盘的文本")
            self.assertEqual(st.count(), 1)
            # 内存历史与库一致
            self.assertEqual(a.history[0]["text"], "落盘的文本")
        finally:
            a.stop()
        st.close()
        # 模拟重启：新同步实例用同一个库
        st2 = ClipboardStore(self.path, limit=100)
        b = self._sync(42002, "dev-b", "B机", store=st2)
        try:
            self.assertEqual(len(b.history), 1)
            self.assertEqual(b.history[0]["text"], "落盘的文本")
            self.assertEqual(b.history[0]["device"], "本机")
        finally:
            b.stop()
        st2.close()

    def test_06_delete_and_clear_synced(self):
        st = ClipboardStore(self.path, limit=100)
        a = self._sync(42003, "dev-a", "A机", store=st)
        try:
            a.broadcast_text("将被删除")
            a.broadcast_text("将保留")
            del_hash = next(
                e["hash"] for e in a.history if e["text"] == "将被删除")
            a.delete_entry(del_hash)
            self.assertEqual([e["text"] for e in a.history], ["将保留"])
            self.assertEqual(st.count(), 1)
            a.clear_history()
            self.assertEqual(a.history, [])
            self.assertEqual(st.count(), 0)
        finally:
            a.stop()

    def test_07_delete_image_removes_disk_file(self):
        """删除图片条目时同步清理落盘 PNG（原实现会留孤儿文件）。"""
        old = cs.CLIP_IMG_DIR
        cs.CLIP_IMG_DIR = os.path.join(self.dir, "imgs")
        st = ClipboardStore(self.path, limit=100)
        a = self._sync(42004, "dev-a", "A机", store=st)
        try:
            a._on_clip_image(_png_bytes())
            entry = a.history[0]
            self.assertEqual(entry["kind"], "image")
            self.assertTrue(os.path.isfile(entry["img_path"]))
            a.delete_entry(entry["hash"])
            self.assertFalse(os.path.exists(entry["img_path"]))
            self.assertEqual(st.count(), 0)
        finally:
            a.stop()
            cs.CLIP_IMG_DIR = old

    def test_08_clear_image_removes_all_disk_files(self):
        old = cs.CLIP_IMG_DIR
        cs.CLIP_IMG_DIR = os.path.join(self.dir, "imgs")
        st = ClipboardStore(self.path, limit=100)
        a = self._sync(42005, "dev-a", "A机", store=st)
        try:
            a._on_clip_image(_png_bytes(color=(1, 2, 3)))
            a._on_clip_image(_png_bytes(color=(4, 5, 6)))
            a.broadcast_text("纯文本不受影响")
            paths = [e["img_path"] for e in a.history if e.get("kind") == "image"]
            self.assertEqual(len(paths), 2)
            self.assertTrue(all(os.path.isfile(p) for p in paths))
            a.clear_history()
            self.assertFalse(any(os.path.exists(p) for p in paths))
        finally:
            a.stop()
            cs.CLIP_IMG_DIR = old

    def test_09_external_img_path_not_deleted(self):
        """防御：img_path 越出 clipboard_images 目录（篡改/穿越）时不得删除。"""
        old = cs.CLIP_IMG_DIR
        cs.CLIP_IMG_DIR = os.path.join(self.dir, "imgs")
        a = self._sync(42006, "dev-a", "A机")
        outside = os.path.join(self.dir, "外部文件.png")
        with open(outside, "wb") as f:
            f.write(_png_bytes())
        try:
            a._add_history(h="external-hash", device="本机", remote=False,
                           kind="image", img_path=outside)
            a.delete_entry("external-hash")
            self.assertTrue(os.path.isfile(outside), "越界路径不应被删除")
        finally:
            a.stop()
            cs.CLIP_IMG_DIR = old

    def test_11_delete_file_removes_disk_file(self):
        """删除文件条目时同步清理落盘文件（对齐图片清理）。"""
        old = cs.CLIP_FILE_DIR
        cs.CLIP_FILE_DIR = os.path.join(self.dir, "clipfile")
        st = ClipboardStore(self.path, limit=100)
        a = self._sync(42007, "dev-a", "A机", store=st)
        try:
            data = b"file-content-xyz"
            h = cs.hash_bytes(data)
            path = a._save_file(data, h, "报告.txt")
            self.assertTrue(os.path.isfile(path))
            a._add_history(h=h, device="B机", remote=True, kind="file",
                           file_path=path, file_name="报告.txt", file_size=len(data))
            a.delete_entry(h)
            self.assertFalse(os.path.exists(path), "删除条目应清理落盘文件")
            self.assertEqual(st.count(), 0)
        finally:
            a.stop()
            cs.CLIP_FILE_DIR = old


class TestEncryption(unittest.TestCase):
    """T-02 历史落盘加密：AES-256-GCM + DPAPI 密钥（非 Windows 自动禁用）。"""

    def _tmp_path(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return os.path.join(d, "enc.db")

    def test_12_text_encrypted_at_rest(self):
        """开启加密后库中不落明文，load 返回原文。"""
        import sqlite3
        path = self._tmp_path()
        key = os.urandom(32)
        st = ClipboardStore(path, limit=100, key=key)
        st.insert({
            "ts": 1.0, "device": "A机", "hash": "enc1",
            "remote": False, "kind": "text", "text": "银行卡 6217 0001 8888",
        })
        conn = sqlite3.connect(path)
        rows = conn.execute("SELECT text FROM clip_history").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        raw = rows[0][0]
        self.assertTrue(raw.startswith(cs._ENC_PREFIX) if hasattr(cs, "_ENC_PREFIX")
                        else raw.startswith("enc:v1:"), "库中应为密文")
        self.assertNotIn("银行卡", raw)
        out = st.load(10)
        self.assertEqual(out[0]["text"], "银行卡 6217 0001 8888")
        self.assertFalse(out[0].get("enc_lost"))

    def test_13_missing_key_marks_lost(self):
        """密钥缺失（换机/换用户）时密文条目标 enc_lost，不崩；密钥恢复后可解。"""
        path = self._tmp_path()
        key = os.urandom(32)
        st = ClipboardStore(path, limit=100, key=key)
        st.insert({"ts": 1.0, "device": "A机", "hash": "enc2",
                   "remote": True, "kind": "text", "text": "机密内容"})
        st.close()
        st0 = ClipboardStore(path, limit=100, key=None)
        out = st0.load(10)
        self.assertEqual(out[0]["text"], "")
        self.assertTrue(out[0]["enc_lost"])
        st0.close()
        st1 = ClipboardStore(path, limit=100, key=key)
        out = st1.load(10)
        self.assertEqual(out[0]["text"], "机密内容")
        st1.close()

    @unittest.skipUnless(sys.platform == "win32", "DPAPI 仅 Windows")
    def test_14_dpapi_key_roundtrip(self):
        """clip_store_key：首次生成 32B 并落盘，再次调用返回同一密钥。"""
        from app.core.clipboard_store import clip_store_key
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        k1 = clip_store_key(d)
        self.assertIsNotNone(k1)
        self.assertEqual(len(k1), 32)
        self.assertTrue(os.path.isfile(os.path.join(d, "clip_enc.key")))
        k2 = clip_store_key(d)
        self.assertEqual(k1, k2, "同一用户的 DPAPI 密钥应可解出原值")


if __name__ == "__main__":
    unittest.main(verbosity=2)