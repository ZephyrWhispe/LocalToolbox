"""设备配对与传输加密（P1）：X25519 ECDH + HKDF-SHA256 + AES-256-GCM。

安全模型：
- 每台设备首次运行生成长期 X25519 身份密钥（PEM 存盘）；
- 配对时双方交换「长期公钥 + 临时公钥」，由两段 ECDH 派生主密钥
  （临时×临时保前向安全，长期×长期绑定身份，均交换对称故双方一致）；
- 主密钥经 HKDF 派生：
  - **SAS**（6 位数字，Short Authentication String）：两端屏幕显示同一数字，
    人工核对（一端显示、另一端输入），防中间人替换公钥；
  - **会话密钥**（AES-256-GCM）：配对/传输后续所有帧加密；
- 配对成功后互存对方长期公钥（trusted.json），后续连接自动加密、无需再核对。

线上加密帧：``{"type":"enc","bin":N}\\n`` + N 字节（nonce12 + 密文含16字节tag）；
明文 = JSON 控制帧 + ``\\x00`` + 可选二进制负载（chunk 数据）。
"""

import json
import os
import threading

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import logger as applog

log = applog.get_logger("pairing")

SALT = b"localtoolbox-pairing-v1"
ENC_MAGIC = b"\x00"  # 加密明文内 JSON 与二进制负载的分隔符


def app_data_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "LocalToolbox")


# ---------------------------------------------------------------------------
# 身份密钥
# ---------------------------------------------------------------------------
class Identity:
    """长期 X25519 身份密钥对，PEM 持久化（keys/identity.key）。"""

    def __init__(self, path=None):
        if path is None:
            path = os.path.join(app_data_dir(), "keys", "identity.key")
        self.path = path
        self._key = self._load_or_create()

    def _load_or_create(self):
        try:
            with open(self.path, "rb") as f:
                return serialization.load_pem_private_key(f.read(), password=None)
        except (OSError, ValueError):
            key = X25519PrivateKey.generate()
            self._save(key)
            return key

    def _save(self, key):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            pem = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            tmp = self.path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(pem)
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError:
            pass

    @property
    def private_key(self):
        return self._key

    def public_hex(self):
        return self._key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()


# ---------------------------------------------------------------------------
# 信任表
# ---------------------------------------------------------------------------
class TrustStore:
    """已配对设备：{device_id: {name, pk, ts}}，trusted.json 持久化。"""

    def __init__(self, path=None):
        if path is None:
            path = os.path.join(app_data_dir(), "trusted.json")
        self.path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def trust(self, device_id, name, pk_hex):
        import time
        with self._lock:
            self._data[str(device_id)] = {
                "name": str(name or ""), "pk": str(pk_hex), "ts": time.time(),
            }
            self._save()

    def untrust(self, device_id):
        with self._lock:
            existed = self._data.pop(str(device_id), None)
            if existed:
                self._save()
            return bool(existed)

    def get(self, device_id):
        with self._lock:
            entry = self._data.get(str(device_id))
            return dict(entry) if entry else None

    def by_pubkey(self, pk_hex):
        """按公钥反查（连接对端可能先只给公钥）。返回 (device_id, entry) 或 None。"""
        with self._lock:
            for did, entry in self._data.items():
                if entry.get("pk") == pk_hex:
                    return did, dict(entry)
        return None

    def list(self):
        with self._lock:
            return [
                {"id": did, "name": e.get("name", ""), "pk": e.get("pk", ""),
                 "ts": e.get("ts", 0)}
                for did, e in self._data.items()
            ]


# ---------------------------------------------------------------------------
# 密钥协商
# ---------------------------------------------------------------------------
def _hkdf(master, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=SALT, info=info).derive(master)


def _pub_from_hex(h):
    return X25519PublicKey.from_public_bytes(bytes.fromhex(h))


class KeyAgreement:
    """一次配对/加密握手的协商状态。

    用法（双方对称）：
      a = KeyAgreement(identity); a.start()
      b = KeyAgreement(identity); b.start()
      # 交换 hello：{pk: 长期公钥hex, eph: 临时公钥hex}
      b.accept_peer(hello_a)  → 内部算出 master
      a.accept_peer(hello_b)
      a.sas() == b.sas()      # 人工核对
      a.session_key()         # 核对通过后用于加密
    """

    def __init__(self, identity):
        self.identity = identity
        self._eph = None
        self.peer_id = None
        self.peer_name = None
        self.peer_pk_hex = None
        self._master = None
        self._session_key = None

    def start(self, peer_id=None, peer_name=None):
        self._eph = X25519PrivateKey.generate()
        self.peer_id = peer_id
        self.peer_name = peer_name
        return {
            "pk": self.identity.public_hex(),
            "eph": self._eph.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            ).hex(),
        }

    def accept_peer(self, hello, peer_id=None, peer_name=None):
        """用对端 hello（含 pk/eph）完成 ECDH，派生 master。"""
        self.peer_pk_hex = str(hello["pk"])
        self.peer_id = peer_id or self.peer_id
        self.peer_name = peer_name or self.peer_name
        static_pub = _pub_from_hex(self.peer_pk_hex)
        eph_pub = _pub_from_hex(str(hello["eph"]))
        s_priv = self.identity.private_key
        # 两段 ECDH 均为交换对称，双方派生的主密钥一致：
        #   ss_ee  = 临时×临时   → 前向保密（PFS）
        #   ss_ss  = 长期×长期   → 绑定长期身份，防密钥替换
        ss_ee = self._eph.exchange(eph_pub)
        ss_ss = s_priv.exchange(static_pub)
        self._master = ss_ee + ss_ss
        return self

    def sas(self):
        """6 位十进制短认证码（双方一致）。"""
        if self._master is None:
            raise RuntimeError("尚未完成对端公钥交换")
        digest = _hkdf(self._master, b"sas", 8)
        return "%06d" % (int.from_bytes(digest, "big") % 1000000)

    def session_key(self):
        if self._master is None:
            raise RuntimeError("尚未完成对端公钥交换")
        if self._session_key is None:
            self._session_key = _hkdf(self._master, b"session", 32)
        return self._session_key

    def channel(self, sock):
        return EncryptedChannel(sock, self.session_key())


# ---------------------------------------------------------------------------
# 加密信道
# ---------------------------------------------------------------------------
class EncryptedChannel:
    """AES-256-GCM 帧封装；与 transfer 的行帧协议配合。"""

    def __init__(self, sock, key):
        self.sock = sock
        self._aead = AESGCM(key)
        self._send_n = 0
        self._recv_n = 0
        self._send_lock = threading.Lock()
        self._recv_lock = threading.Lock()

    def _nonce(self, n):
        # 96 位计数器 nonce（单向连接内方向独立计数，无重复风险）
        return n.to_bytes(12, "big")

    def send(self, obj, raw=b""):
        """加密发送一帧：明文 = JSON + 0x00 + 二进制负载。"""
        plain = json.dumps(obj, ensure_ascii=False).encode("utf-8") + ENC_MAGIC + bytes(raw)
        with self._send_lock:
            nonce = self._nonce(self._send_n)
            self._send_n += 1
            blob = nonce + self._aead.encrypt(nonce, plain, None)
        from .transfer import _send_line  # 避免循环导入：延迟引用
        _send_line(self.sock, {"type": "enc", "bin": len(blob)})
        self.sock.sendall(blob)

    def recv(self, buf):
        """读取并解密一帧。返回 (obj, raw, buf)；连接关闭返回 (None, b"", buf)。"""
        from .transfer import _read_exact, _read_line
        with self._recv_lock:
            frame, buf = _read_line(self.sock, buf)
            if frame is None:
                return None, b"", buf
            if frame.get("type") != "enc":
                raise ValueError("加密通道收到非加密帧：%s" % frame.get("type"))
            blob, buf = _read_exact(self.sock, buf, int(frame.get("bin") or 0))
            nonce, ct = blob[:12], blob[12:]
            expected = self._nonce(self._recv_n)
            self._recv_n += 1
            if nonce != expected:
                raise ValueError("加密帧序号异常（可能被篡改或重放）")
            try:
                plain = self._aead.decrypt(nonce, ct, None)
            except ValueError:
                log.warning("加密帧校验失败（篡改/错钥/重放，sock=%s）", self.sock)
                raise
        if ENC_MAGIC not in plain:
            raise ValueError("加密帧格式错误")
        json_part, raw = plain.split(ENC_MAGIC, 1)
        return json.loads(json_part.decode("utf-8")), raw, buf
