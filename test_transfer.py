# -*- coding: utf-8 -*-
"""P0-3 文件传输引擎端到端测试：本机起两个引擎（收/发）走真实 TCP。

覆盖：普通收发与哈希、目录结构、断点续传、秒传去重、拒绝、自动接收、
路径安全工具函数。运行：python test_transfer.py
"""

import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.transfer import (  # noqa: E402
    TransferEngine,
    _safe_rel,
    _unique_path,
    expand_paths,
    sha256_file,
)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Harness:
    """包一个引擎，收集事件供断言。auto_respond=True 时模拟用户点「接收」。"""

    def __init__(self, name, save_dir, auto_accept=False, auto_respond=False,
                 queue_path=None):
        self.name = name
        self.save_dir = save_dir
        self.auto_respond = auto_respond
        self.lock = threading.Lock()
        self.offers = []
        self.dones = []
        self.logs = []
        self.engine = TransferEngine(
            port=free_port(),
            device_id=name,
            device_name=name,
            get_save_dir=lambda: save_dir,
            get_auto_accept=lambda: auto_accept,
            on_event=self._on_event,
            queue_path=queue_path,
        )
        self.engine.start()

    def _on_event(self, n, p):
        with self.lock:
            if n == "xfer_offer":
                self.offers.append(p)
                if self.auto_respond:
                    self.engine.respond(p["tid"], True)
            elif n == "xfer_done":
                self.dones.append(p)
            elif n == "xfer_log":
                self.logs.append(str(p))

    def wait_done(self, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if self.dones:
                    return self.dones[0]
            time.sleep(0.05)
        raise AssertionError("等待 xfer_done 超时")

    def pop_done(self):
        with self.lock:
            return self.dones.pop(0) if self.dones else None

    def stop(self):
        self.engine.stop()


class TestTransfer(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp(prefix="xfer-src-")
        self.dst = tempfile.mkdtemp(prefix="xfer-dst-")
        self.tx = Harness("发送端", self.src)
        self.rx = Harness("接收端", self.dst, auto_respond=True)

    def tearDown(self):
        self.tx.stop()
        self.rx.stop()
        shutil.rmtree(self.src, ignore_errors=True)
        shutil.rmtree(self.dst, ignore_errors=True)

    # -- 工具 ----------------------------------------------------------
    def _make_file(self, rel, size, pattern=b"xfer"):
        full = os.path.join(self.src, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        data = (pattern * (size // max(len(pattern), 1) + 1))[:size]
        with open(full, "wb") as f:
            f.write(data)
        return full

    def _send_ok(self, paths, peer="接收端"):
        r = self.tx.engine.send_to("127.0.0.1", self.rx.engine.port, paths, peer_name=peer)
        self.assertTrue(r["ok"], r.get("err"))
        return r["data"]["tid"]

    # -- 用例 ----------------------------------------------------------
    def test_01_basic_send_recv_with_hash(self):
        self._make_file("hello.txt", 2048, "你好，局域网！".encode("utf-8"))
        self._make_file("sub/dir/big.bin", 1024 * 1024 + 640 * 1024)  # 跨 1MiB 分块
        self._make_file("empty.dat", 0)
        self._send_ok([os.path.join(self.src, "hello.txt"),
                       os.path.join(self.src, "sub"),
                       os.path.join(self.src, "empty.dat")])
        done = self.tx.wait_done()
        self.assertTrue(done["ok"], done)
        self.assertEqual(done["dir"], "send")
        rdone = self.rx.wait_done()
        self.assertTrue(rdone["ok"], rdone)
        # 内容与哈希逐一比对
        for rel in ("hello.txt", "sub/dir/big.bin", "empty.dat"):
            a = os.path.join(self.src, rel.replace("/", os.sep))
            b = os.path.join(self.dst, rel.replace("/", os.sep))
            self.assertTrue(os.path.isfile(b), rel)
            self.assertEqual(sha256_file(a), sha256_file(b), rel)
        self.assertNotExists(os.path.join(self.dst, "sub/dir/big.bin.part"))

    def assertNotExists(self, path):
        self.assertFalse(os.path.exists(path), path)

    def test_02_resume_from_part_file(self):
        size = 1024 * 1024 + 123456
        full = self._make_file("big.bin", size)
        # 接收端预先有前 500KB 的 .part（模拟上次中断）；单文件发送 rel = 文件名
        part = os.path.join(self.dst, "big.bin.part")
        with open(full, "rb") as f:
            head = f.read(500 * 1024)
        with open(part, "wb") as f:
            f.write(head)
        self._send_ok([full])
        sdone = self.tx.wait_done()
        self.assertTrue(sdone["ok"], sdone)
        self.tx.pop_done()
        rdone = self.rx.wait_done()
        self.assertTrue(rdone["ok"], rdone)
        # 只应补传剩余字节
        self.assertEqual(sdone["done"], size - 500 * 1024, sdone)
        final = os.path.join(self.dst, "big.bin")
        self.assertEqual(sha256_file(full), sha256_file(final))
        self.assertNotExists(part)

    def test_03_dedupe_skip(self):
        full = self._make_file("same.bin", 4096)
        # 接收端已有同内容文件 → 秒传
        os.makedirs(self.dst, exist_ok=True)
        shutil.copyfile(full, os.path.join(self.dst, "same.bin"))
        self._send_ok([full])
        sdone = self.tx.wait_done()
        self.assertTrue(sdone["ok"], sdone)
        self.assertEqual(sdone["done"], 0, sdone)  # 一个字节都没发
        self.tx.pop_done()
        self.rx.wait_done()
        self.assertTrue(any("秒传" in m for m in self.tx.logs), self.tx.logs)

    def test_04_reject(self):
        rx3 = Harness("接收端", self.dst)  # 不自动响应，模拟用户拒绝
        try:
            full = self._make_file("no.txt", 128)
            r = self.tx.engine.send_to("127.0.0.1", rx3.engine.port, [full], peer_name="接收端")
            self.assertTrue(r["ok"], r)
            tid = r["data"]["tid"]
            deadline = time.time() + 5
            while time.time() < deadline and not rx3.offers:
                time.sleep(0.05)
            self.assertTrue(rx3.offers, "接收端未收到 offer")
            rx3.engine.respond(rx3.offers[0]["tid"], False)
            sdone = self.tx.wait_done()
            self.assertFalse(sdone["ok"], sdone)
            self.assertEqual(sdone["tid"], tid)
            self.assertTrue(any("拒绝" in m for m in self.tx.logs), self.tx.logs)
        finally:
            rx3.stop()

    def test_05_auto_accept(self):
        rx2 = Harness("自动接收端", self.dst, auto_accept=True)
        try:
            full = self._make_file("auto.txt", 4096)
            r = self.tx.engine.send_to("127.0.0.1", rx2.engine.port, [full], peer_name="自动接收端")
            self.assertTrue(r["ok"], r)
            sdone = self.tx.wait_done()
            self.assertTrue(sdone["ok"], sdone)
            self.assertEqual(sha256_file(full),
                             sha256_file(os.path.join(self.dst, "auto.txt")))
        finally:
            rx2.stop()

    def test_06_unique_path_on_conflict(self):
        full = self._make_file("conflict.txt", 256)
        # 接收端已存在不同内容的同名文件 → 应重命名保存而不是覆盖
        with open(os.path.join(self.dst, "conflict.txt"), "wb") as f:
            f.write(b"different-content")
        self._send_ok([full])
        self.tx.wait_done()
        self.tx.pop_done()
        self.rx.wait_done()
        renamed = os.path.join(self.dst, "conflict (1).txt")
        self.assertTrue(os.path.isfile(renamed), os.listdir(self.dst))
        self.assertEqual(sha256_file(full), sha256_file(renamed))
        with open(os.path.join(self.dst, "conflict.txt"), "rb") as f:
            self.assertEqual(f.read(), b"different-content")

    # -- 工具函数单元 ---------------------------------------------------
    def test_07_safe_rel_blocks_traversal(self):
        base = tempfile.gettempdir()
        with self.assertRaises(ValueError):
            _safe_rel("../escape.txt", base)
        with self.assertRaises(ValueError):
            _safe_rel("a/../../b", base)
        with self.assertRaises(ValueError):
            _safe_rel("C:/abs/path", base)
        self.assertEqual(
            os.path.normcase(os.path.abspath(_safe_rel("a/b.txt", base))),
            os.path.normcase(os.path.abspath(os.path.join(base, "a", "b.txt"))),
        )

    def test_08_expand_paths_common_base(self):
        f1 = self._make_file("p/a.txt", 10)
        f2 = self._make_file("p/b/c.txt", 20)
        # 发送目录：rel 以该目录为根
        files = expand_paths([os.path.join(self.src, "p")])
        rels = {f["rel"] for f in files}
        self.assertEqual(rels, {"a.txt", "b/c.txt"})
        # 发送单个文件：rel = 文件名（以其父目录为根）
        one = expand_paths([f2])
        self.assertEqual(one[0]["rel"], "c.txt")
        sizes = {f["rel"]: f["size"] for f in files}
        self.assertEqual(sizes["a.txt"], 10)

    def test_09_unique_path(self):
        base = tempfile.mkdtemp(prefix="xfer-u-")
        try:
            p = os.path.join(base, "f.txt")
            self.assertEqual(_unique_path(p), p)
            open(p, "w").close()
            self.assertEqual(_unique_path(p), os.path.join(base, "f (1).txt"))
            open(os.path.join(base, "f (1).txt"), "w").close()
            self.assertEqual(_unique_path(p), os.path.join(base, "f (2).txt"))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    # -- 发送队列（持久化） -------------------------------------------
    def test_10_enqueue_auto_send(self):
        """入队 → 队列线程自动发送 → 成功出队。"""
        full = self._make_file("q.txt", 2048, "队列测试".encode("utf-8"))
        r = self.tx.engine.enqueue(
            "127.0.0.1", self.rx.engine.port, [full], peer_name="接收端")
        self.assertTrue(r["ok"], r)
        sdone = self.tx.wait_done()
        self.assertTrue(sdone["ok"], sdone)
        got = os.path.join(self.dst, "q.txt")
        self.assertTrue(os.path.isfile(got))
        self.assertEqual(sha256_file(full), sha256_file(got))
        # 成功后任务移出队列
        for _ in range(50):
            if not self.tx.engine.snapshot()["queue"]:
                break
            time.sleep(0.05)
        self.assertEqual(self.tx.engine.snapshot()["queue"], [])

    def test_11_enqueue_fail_then_retry_remove(self):
        """连不上 → 失败任务保留；可手动重试/移出。"""
        full = self._make_file("bad.txt", 128)
        dead = free_port()  # 无监听的端口
        r = self.tx.engine.enqueue(
            "127.0.0.1", dead, [full], peer_name="离线设备")
        self.assertTrue(r["ok"], r)
        sdone = self.tx.wait_done()
        self.assertFalse(sdone["ok"], sdone)
        # 轮询直到快照里的失败任务出现
        tid = None
        deadline = time.time() + 5
        while time.time() < deadline:
            q = self.tx.engine.snapshot()["queue"]
            if q and q[0]["status"] == "failed":
                tid = q[0]["tid"]
                break
            time.sleep(0.05)
        self.assertTrue(tid, "队列中应保留失败任务")
        # 手动重试（端口仍无监听 → 再次失败，但接口需返回 ok）
        r = self.tx.engine.queue_retry(tid)
        self.assertTrue(r["ok"], r)
        # 移出队列
        r = self.tx.engine.queue_remove(tid)
        self.assertTrue(r["ok"], r)
        for _ in range(50):
            if not self.tx.engine.snapshot()["queue"]:
                break
            time.sleep(0.05)
        self.assertEqual(self.tx.engine.snapshot()["queue"], [])

    def test_12_queue_persist_restore(self):
        """重启后从 JSON 恢复队列：运行中任务续发，故障保留重试。"""
        full = self._make_file("persist.txt", 512)
        qp = os.path.join(self.src, "queue.json")
        # 目标为不可达 IP：connect 会超时，留出「运行中被打断」的窗口
        a = Harness("队列端A", self.src, queue_path=qp)
        try:
            r = a.engine.enqueue("10.255.255.1", free_port(), [full], peer_name="离线设备")
            self.assertTrue(r["ok"], r)
            a.engine.stop()  # 不等完成：任务处于 running（或刚失败）即落盘
        finally:
            a.stop()
        self.assertTrue(os.path.isfile(qp), "队列应已持久化")
        # 新引擎（模拟重启）同一 queue_path → 恢复；running 转 pending 自动续发
        # （10.255.255.1 仍不可达 → 最终 failed 保留）
        b = Harness("队列端B", self.src, queue_path=qp)
        try:
            deadline = time.time() + 12  # 容忍一次 connect 超时（≤5s）+ 收尾
            q = None
            while time.time() < deadline:
                q = b.engine.snapshot()["queue"]
                if q and q[0]["status"] == "failed":
                    break
                time.sleep(0.1)
            self.assertTrue(q, "重启后应恢复队列任务")
            self.assertEqual(q[0]["status"], "failed", q)
            self.assertEqual(q[0]["ip"], "10.255.255.1")
        finally:
            b.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
