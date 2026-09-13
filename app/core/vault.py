"""密码库：主口令 PBKDF2 派生 AES-256-GCM 加密的单文件库（参考 KeeWeb/KDBX）。

文件格式（单行文本）：``LTVAULT1|salt_b64|iters|iv_b64|ct_b64``
- PBKDF2-HMAC-SHA256（默认 600_000 次迭代，随头部存储便于演进）
- AES-256-GCM，AAD 绑定头部（magic+salt+iters）防篡改——错口令即
  InvalidTag，无需单独的口令校验字段；口令遗忘不可恢复（无后门），
  WebDAV 上的历史备份即唯一恢复途径。
- 明文载荷：{"version":1, "entries":[{id,title,username,password,url,note,group,updated}]}
- 写盘 tmp + os.replace（同 config.save），每次保存换新 salt。

解锁后条目驻内存；锁定/退出即清空。线程安全：内部锁。
"""

import base64
import hashlib
import json
import os
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = "LTVAULT1"
DEFAULT_ITERS = 600_000


class VaultError(Exception):
    """口令错误 / 库文件损坏等业务错误（err 直显）。"""


def _derive(password, salt, iters):
    return hashlib.pbkdf2_hmac(
        "sha256", str(password).encode("utf-8"), salt, int(iters))


def _encrypt_container(payload, password, iters=DEFAULT_ITERS):
    """payload dict → 加密单行容器文本（LTVAULT1|salt|iters|iv|ct）。

    供 vault.dat 读写与「加密导出」共用：同一容器格式，口令独立。
    """
    salt = os.urandom(16)
    key = _derive(password, salt, iters)
    header = "%s|%s|%d" % (MAGIC, base64.b64encode(salt).decode("ascii"), iters)
    iv = os.urandom(12)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ct = AESGCM(key).encrypt(iv, raw, header.encode("utf-8"))
    return header + "|" + base64.b64encode(iv).decode("ascii") + "|" + \
        base64.b64encode(ct).decode("ascii")


def _decrypt_container(line, password):
    """解密容器单行 → payload dict。口令错误/损坏抛 VaultError。"""
    parts = str(line).strip().split("|")
    if len(parts) != 5 or parts[0] != MAGIC:
        raise VaultError("密码库文件损坏")
    try:
        salt = base64.b64decode(parts[1])
        iters = int(parts[2])
        iv = base64.b64decode(parts[3])
        ct = base64.b64decode(parts[4])
    except (ValueError, TypeError):
        raise VaultError("密码库文件损坏")
    aad = ("%s|%s|%s" % (MAGIC, parts[1], parts[2])).encode("utf-8")
    key = _derive(password, salt, iters)
    try:
        raw = AESGCM(key).decrypt(iv, ct, aad)
    except InvalidTag:
        raise VaultError("口令错误")
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise VaultError("密码库文件损坏")


class Vault:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._password = None
        self._entries = None      # 解锁后的条目列表（内存态）
        self._iters = DEFAULT_ITERS
        self.last_used = time.time()   # 自动锁定空闲计时

    # -- 状态 --------------------------------------------------------------
    @property
    def exists(self):
        return os.path.exists(self.path)

    @property
    def unlocked(self):
        return self._entries is not None

    def touch(self):
        self.last_used = time.time()

    def status(self):
        return {"exists": self.exists, "unlocked": self.unlocked,
                "count": len(self._entries) if self._entries else 0}

    # -- 文件编解码 --------------------------------------------------------
    def _read(self):
        """读文件并解密；返回 payload dict。口令错误/损坏抛 VaultError。"""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                line = f.read().strip()
        except OSError as e:
            raise VaultError("无法读取密码库：%s" % e)
        payload = _decrypt_container(line, self._password)
        return payload

    def _write(self, password):
        """用指定口令加密当前内存条目并落盘（新 salt）。"""
        self._iters = max(self._iters, DEFAULT_ITERS)
        line = _encrypt_container(
            {"version": 1, "entries": self._entries or []}, password, self._iters)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(line)
        os.replace(tmp, self.path)

    # -- 生命周期 ----------------------------------------------------------
    def create(self, password):
        """首建库（已存在则报错）。"""
        if self.exists:
            raise VaultError("密码库已存在，请直接解锁")
        if not str(password or ""):
            raise VaultError("主口令不能为空")
        with self._lock:
            self._password = str(password)
            self._entries = []
            self._write(self._password)
            self.touch()

    def unlock(self, password):
        with self._lock:
            if not self.exists:
                raise VaultError("密码库尚未创建")
            self._password = str(password or "")
            payload = self._read()
            self._entries = list(payload.get("entries") or [])
            self.touch()

    def lock(self):
        with self._lock:
            self._password = None
            self._entries = None

    def change_password(self, old, new):
        """改主口令：校验旧口令后全库重加密。"""
        with self._lock:
            if not self.unlocked:
                raise VaultError("请先解锁密码库")
            # 用旧口令对文件重新校验（错口令抛 VaultError「口令错误」）
            check = Vault(self.path)
            check._password = str(old or "")
            check._read()
            if not str(new or ""):
                raise VaultError("新口令不能为空")
            self._password = str(new)
            self._write(self._password)
            self.touch()

    # -- 条目 --------------------------------------------------------------
    def _require_unlocked(self):
        if not self.unlocked:
            raise VaultError("密码库已锁定")

    def entries(self):
        """条目列表（含密码字段——仅 unlock 后内部用；对前端须脱敏）。"""
        with self._lock:
            self._require_unlocked()
            self.touch()
            return [dict(e) for e in self._entries]

    def entry(self, eid):
        with self._lock:
            self._require_unlocked()
            self.touch()
            for e in self._entries:
                if e.get("id") == int(eid):
                    return dict(e)
            return None

    def upsert(self, entry):
        """新增/更新条目（dict 含 id 则更新）。返回条目 id。"""
        with self._lock:
            self._require_unlocked()
            self.touch()
            eid = entry.get("id")
            clean = {
                "id": 0,
                "title": str(entry.get("title") or "")[:120] or "无标题",
                "username": str(entry.get("username") or "")[:200],
                "password": str(entry.get("password") or ""),
                "url": str(entry.get("url") or "")[:500],
                "note": str(entry.get("note") or "")[:4000],
                "group": str(entry.get("group") or "")[:32],
                "updated": time.time(),
            }
            if eid:
                eid = int(eid)
                for i, e in enumerate(self._entries):
                    if e.get("id") == eid:
                        clean["id"] = eid
                        self._entries[i] = clean
                        break
                else:
                    raise VaultError("条目不存在")
            else:
                clean["id"] = max([e.get("id") or 0 for e in self._entries] or [0]) + 1
                self._entries.append(clean)
            self._write(self._password)
            return clean["id"]

    def delete(self, eid):
        with self._lock:
            self._require_unlocked()
            self.touch()
            before = len(self._entries)
            self._entries = [e for e in self._entries
                             if e.get("id") != int(eid)]
            if len(self._entries) == before:
                raise VaultError("条目不存在")
            self._write(self._password)
            return True

    def add_many(self, entries):
        """批量新增（导入用）：仅落盘一次。返回新增条数。"""
        with self._lock:
            self._require_unlocked()
            self.touch()
            nid = max([e.get("id") or 0 for e in self._entries] or [0])
            added = 0
            now = time.time()
            for e in entries or []:
                nid += 1
                self._entries.append({
                    "id": nid,
                    "title": str(e.get("title") or "")[:120] or "无标题",
                    "username": str(e.get("username") or "")[:200],
                    "password": str(e.get("password") or ""),
                    "url": str(e.get("url") or "")[:500],
                    "note": str(e.get("note") or "")[:4000],
                    "group": str(e.get("group") or "")[:32],
                    "updated": now,
                })
                added += 1
            if added:
                self._write(self._password)
            return added

    def replace_entries(self, entries):
        """恢复模式：整体替换条目并落盘（调用方须已解锁）。"""
        with self._lock:
            self._require_unlocked()
            clean = []
            for e in entries or []:
                clean.append({
                    "id": int(e.get("id") or 0),
                    "title": str(e.get("title") or "")[:120] or "无标题",
                    "username": str(e.get("username") or "")[:200],
                    "password": str(e.get("password") or ""),
                    "url": str(e.get("url") or "")[:500],
                    "note": str(e.get("note") or "")[:4000],
                    "group": str(e.get("group") or "")[:32],
                    "updated": float(e.get("updated") or time.time()),
                })
            ids = [e["id"] for e in clean if e["id"]]
            used = set()
            for e in clean:
                if not e["id"] or e["id"] in used:
                    e["id"] = max(ids or [0]) + 1
                    ids.append(e["id"])
                used.add(e["id"])
            self._entries = clean
            self._write(self._password)
            self.touch()
            return len(clean)

    def groups(self):
        """现有分组名去重列表（分组输入建议）。"""
        with self._lock:
            self._require_unlocked()
            seen = []
            for e in self._entries:
                g = e.get("group") or ""
                if g and g not in seen:
                    seen.append(g)
            return seen


def generate_password(length=16, symbols=True):
    """secrets 强密码：至少各含一个小写/大写/数字，可选符号。"""
    import secrets
    import string
    length = max(8, min(64, int(length or 16)))
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits]
    if symbols:
        pools.append("!@#$%^&*()-_=+[]{};:,.<>?")
    while True:
        chars = [secrets.choice(p) for p in pools]
        chars += [secrets.choice("".join(pools)) for _ in range(length - len(pools))]
        secrets.SystemRandom().shuffle(chars)
        pwd = "".join(chars)
        # 保证满足每类至少一个（shuffle 后前缀恰好是每类一个，无额外校验必要）
        if (any(c in string.ascii_lowercase for c in pwd)
                and any(c in string.ascii_uppercase for c in pwd)
                and any(c in string.digits for c in pwd)):
            return pwd
