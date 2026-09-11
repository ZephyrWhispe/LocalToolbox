# -*- coding: utf-8 -*-
"""P1 配对加密测试：

- 单元：SAS 一致性、会话密钥一致、AES-GCM 往返、篡改/错密钥检测、信任表持久化；
- 端到端（双引擎真实 TCP）：独立配对成功后加密传输、错误配对码、拒绝配对、
  强制加密（require_pairing）下未配对设备自动走配对。
运行：python test_pairing.py
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

from app.core.pairing import (  # noqa: E402
    EncryptedChannel,
    Identity,
    KeyAgreement,
    TrustStore,
)
from app.core.transfer import TransferEngine, sha256_file  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Harness:
    """双引擎测试装置：独立身份/信任表（临时目录），收集配对事件。"""

    def __init__(self, name, save_dir, require_pairing=False):
        self.name = name
        self.dir = tempfile.mkdtemp(prefix="pair-%s-" % name)
        self.save_dir = save_dir
        identity = Identity(path=os.path.join(self.dir, "identity.key"))
        trust = TrustStore(path=os.path.join(self.dir, "trusted.json"))
        self.lock = threading.Lock()
        self.pair_shows = []   # 接收方：{tid, peer, sas}
        self.pair_inputs = []  # 发起方：{tid, peer}
        self.pair_dones = []
        self.dones = []
        self.logs = []
        self.engine = TransferEngine(
            port=free_port(),
            device_id="id-" + name,
            device_name=name,
            get_save_dir=lambda: save_dir,
            get_auto_accept=lambda: True,
            get_require_pairing=lambda: require_pairing,
            identity=identity,
            trust=trust,
            on_event=self._on_event,
        )
        self.engine.start()

    def _on_event(self, n, p):
        with self.lock:
            if n == "xfer_pair_show":
                self.pair_shows.append(p)
            elif n == "xfer_pair_input":
                self.pair_inputs.append(p)
            elif n == "xfer_pair_done":
                self.pair_dones.append(p)
            elif n == "xfer_done":
                self.dones.append(p)
            elif n == "xfer_log":
                self.logs.append(str(p))

    def wait(self, bucket, timeout=15, pred=None):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                items = getattr(self, bucket)
                if pred is None:
                    if items:
                        return items[-1]
                else:
                    hit = [x for x in items if pred(x)]
                    if hit:
                        return hit[-1]
            time.sleep(0.05)
        raise AssertionError("等待 %s 超时" % bucket)

    def stop(self):
        self.engine.stop()
        shutil.rmtree(self.dir, ignore_errors=True)


def make_file(base, rel, size):
    full = os.path.join(base, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(os.urandom(size))
    return full


class TestPairingCrypto(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pair-unit-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_sas_and_session_key_match(self):
        id_a = Identity(path=os.path.join(self.tmp, "a.key"))
        id_b = Identity(path=os.path.join(self.tmp, "b.key"))
        a = KeyAgreement(id_a)
        b = KeyAgreement(id_b)
        hello_a = a.start(peer_id="A", peer_name="A")
        hello_b = b.start(peer_id="B", peer_name="B")
        a.accept_peer(hello_b, peer_id="B", peer_name="B")
        b.accept_peer(hello_a, peer_id="A", peer_name="A")
        self.assertEqual(a.sas(), b.sas())
        self.assertTrue(a.sas().isdigit() and len(a.sas()) == 6)
        self.assertEqual(a.session_key(), b.session_key())
        self.assertEqual(len(a.session_key()), 32)

    def test_02_channel_roundtrip(self):
        id_a = Identity(path=os.path.join(self.tmp, "a2.key"))
        id_b = Identity(path=os.path.join(self.tmp, "b2.key"))
        a = KeyAgreement(id_a).start()
        b = KeyAgreement(id_b).start()
        ka_a = KeyAgreement(id_a)
        ka_b = KeyAgreement(id_b)
        ha = ka_a.start()
        hb = ka_b.start()
        ka_a.accept_peer(hb)
        ka_b.accept_peer(ha)

        a_srv, b_cli = socket.socketpair()
        try:
            ch_a = ka_a.channel(a_srv)
            ch_b = ka_b.channel(b_cli)
            ch_a.send({"type": "begin", "path": "x.bin"}, raw=b"hello-binary")
            ch_b.send({"type": "file_ok", "hash_ok": True})

            msg, raw, _ = ch_b.recv(b"")
            self.assertEqual(msg["type"], "begin")
            self.assertEqual(raw, b"hello-binary")
            msg2, raw2, _ = ch_a.recv(b"")
            self.assertEqual(msg2["type"], "file_ok")
            self.assertEqual(raw2, b"")
        finally:
            a_srv.close()
            b_cli.close()

    def test_03_tamper_detected(self):
        from cryptography.exceptions import InvalidTag
        id_a = Identity(path=os.path.join(self.tmp, "a3.key"))
        id_b = Identity(path=os.path.join(self.tmp, "b3.key"))
        ka_a = KeyAgreement(id_a)
        ka_b = KeyAgreement(id_b)
        ha = ka_a.start()
        hb = ka_b.start()
        ka_a.accept_peer(hb)
        ka_b.accept_peer(ha)
        a_srv, b_cli = socket.socketpair()
        try:
            ch_a = ka_a.channel(a_srv)
            # ch_a.send 写到 a_srv，在 socketpair 中数据到达 b_cli 接收缓冲
            ch_a.send({"type": "chunk", "bin": 4}, raw=b"abcd")
            frame = b""
            while b"\n" not in frame:
                frame += b_cli.recv(4096)
            header, blob = frame.split(b"\n", 1)
            import json as _json
            n = _json.loads(header)["bin"]
            while len(blob) < n:
                blob += b_cli.recv(4096)
            # 翻转一个密文负载字节（nonce 12 字节之后）
            tampered = bytearray(blob)
            tampered[15] ^= 0xFF
            ch_b = ka_b.channel(b_cli)  # 取其 aead 做手动解密
            with self.assertRaises(InvalidTag):
                ch_b._aead.decrypt(bytes(tampered[:12]), bytes(tampered[12:]), None)
        finally:
            a_srv.close()
            b_cli.close()

    def test_04_wrong_key_fails(self):
        from cryptography.exceptions import InvalidTag
        id_a = Identity(path=os.path.join(self.tmp, "a4.key"))
        id_b = Identity(path=os.path.join(self.tmp, "b4.key"))
        id_c = Identity(path=os.path.join(self.tmp, "c4.key"))
        ka = KeyAgreement(id_a)
        kb = KeyAgreement(id_b)
        kc = KeyAgreement(id_c)
        ha = ka.start()
        hb = kb.start()
        ka.accept_peer(hb)
        kb.accept_peer(ha)
        # C 与 B 协商出的密钥 ≠ A 与 B 的会话密钥
        hc = kc.start()
        kc.accept_peer(hb)
        self.assertNotEqual(ka.session_key(), kc.session_key())

        a_srv, b_cli = socket.socketpair()
        try:
            ch_a = ka.channel(a_srv)
            ch_c = EncryptedChannel(b_cli, kc.session_key())
            ch_a.send({"type": "end"})
            with self.assertRaises(InvalidTag):
                ch_c.recv(b"")
        finally:
            a_srv.close()
            b_cli.close()

    def test_05_trust_store_persistence(self):
        path = os.path.join(self.tmp, "trust.json")
        t1 = TrustStore(path=path)
        t1.trust("dev-1", "电脑A", "aabb")
        t2 = TrustStore(path=path)
        self.assertEqual(t2.get("dev-1")["pk"], "aabb")
        self.assertTrue(t2.untrust("dev-1"))
        t3 = TrustStore(path=path)
        self.assertIsNone(t3.get("dev-1"))


class TestPairingE2E(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp(prefix="pair-src-")
        self.dst = tempfile.mkdtemp(prefix="pair-dst-")
        self.a = Harness("甲机", self.src)
        self.b = Harness("乙机", self.dst)

    def tearDown(self):
        self.a.stop()
        self.b.stop()
        shutil.rmtree(self.src, ignore_errors=True)
        shutil.rmtree(self.dst, ignore_errors=True)

    def _do_pair(self, pin_provider=None, b_allow=True):
        """驱动一次完整配对：A 发起，B 允许，A 提交配对码（默认从 B 的屏幕事件取）。"""
        r = self.a.engine.pair_to("127.0.0.1", self.b.engine.port, peer_name="乙机")
        self.assertTrue(r["ok"], r)
        tid = r["data"]["tid"]
        # B：等配对请求弹出 → 允许/拒绝
        show = self.b.wait("pair_shows", pred=lambda p: p["tid"] is not None)
        # A：等输入请求
        self.a.wait("pair_inputs")
        if not b_allow:
            self.b.engine.pair_decide(show["tid"], False)
        else:
            self.b.engine.pair_decide(show["tid"], True)
        # A 提交配对码（模拟人工核对：正确码来自 B 屏幕事件）
        pin = pin_provider(show["sas"]) if pin_provider else show["sas"]
        self.a.engine.pair_submit(tid, pin)
        done_a = self.a.wait("pair_dones")
        done_b = self.b.wait("pair_dones")
        return done_a, done_b

    def test_06_pair_then_encrypted_transfer(self):
        done_a, done_b = self._do_pair()
        self.assertTrue(done_a["ok"], done_a)
        self.assertTrue(done_b["ok"], done_b)
        # 双方互存信任
        self.assertTrue(self.a.engine.trust.by_pubkey(self.b.identity_public()))
        self.assertTrue(self.b.engine.trust.by_pubkey(self.a.identity_public()))

        # 配对后发文件：应自动加密
        f = make_file(self.src, "secret.bin", 300 * 1024)
        r = self.a.engine.send_to("127.0.0.1", self.b.engine.port, [f], peer_name="乙机")
        self.assertTrue(r["ok"], r)
        da = self.a.wait("dones", timeout=20)
        db = self.b.wait("dones", timeout=20)
        self.assertTrue(da["ok"], da)
        self.assertTrue(db["ok"], db)
        self.assertTrue(da["encrypted"], da)
        self.assertTrue(db["encrypted"], db)
        out = os.path.join(self.dst, "secret.bin")
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(sha256_file(f), sha256_file(out))
        self.assertTrue(any("加密" in m for m in self.b.logs), self.b.logs)

    def test_07_wrong_pin(self):
        done_a, done_b = self._do_pair(pin_provider=lambda real: "000000" if real != "000000" else "111111")
        self.assertFalse(done_a["ok"], done_a)
        self.assertFalse(done_b["ok"], done_b)
        self.assertIsNone(self.a.engine.trust.by_pubkey(self.b.identity_public()))
        self.assertIsNone(self.b.engine.trust.by_pubkey(self.a.identity_public()))

    def test_08_pair_rejected(self):
        done_a, done_b = self._do_pair(b_allow=False)
        self.assertFalse(done_a["ok"], done_a)
        self.assertFalse(done_b["ok"], done_b)

    def test_09_require_pairing_forces_handshake(self):
        """B 开启强制加密：未配对发送文件应自动走配对，完成后加密传输。"""
        self.b.stop()
        shutil.rmtree(self.b.dir, ignore_errors=True)
        self.b = Harness("乙机", self.dst, require_pairing=True)

        f = make_file(self.src, "forced.bin", 128 * 1024)
        r = self.a.engine.send_to("127.0.0.1", self.b.engine.port, [f], peer_name="乙机")
        self.assertTrue(r["ok"], r)
        tid = r["data"]["tid"]

        # B 弹配对码（传输中配对，tid 与传输相同）
        show = self.b.wait("pair_shows", pred=lambda p: p["tid"] == tid)
        self.a.wait("pair_inputs", pred=lambda p: p["tid"] == tid)
        self.b.engine.pair_decide(show["tid"], True)
        self.a.engine.pair_submit(tid, show["sas"])

        da = self.a.wait("dones", timeout=20)
        db = self.b.wait("dones", timeout=20)
        self.assertTrue(da["ok"], da)
        self.assertTrue(db["ok"], db)
        self.assertTrue(da["encrypted"], da)
        out = os.path.join(self.dst, "forced.bin")
        self.assertEqual(sha256_file(f), sha256_file(out))


# 给 Harness 补一个取本机公钥的辅助
def _identity_public(self):
    return self.engine.identity.public_hex()


Harness.identity_public = _identity_public


if __name__ == "__main__":
    unittest.main(verbosity=2)
