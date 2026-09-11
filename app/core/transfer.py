"""局域网文件传输引擎（P0-3 + P1 配对加密）：TCP 长连接 + 分块 + 断点续传 + 哈希。

协议（每帧 = 一行 JSON + 可选二进制负载）：
  发送端 -> 接收端:
    {"type":"offer","tid":..,"id":..,"name":..,"pk":静态公钥,"eph":临时公钥,
     "files":[{"path":rel,"size":n,"hash":sha256}],"total":n}
    {"type":"begin","path":rel,"size":n,"hash":sha256}
    {"type":"chunk","bin":N}  + N 字节原始数据
    {"type":"end","path":rel}
    {"type":"cancel","reason":..}
    配对（独立连接）: {"type":"pair_hello","id":..,"name":..,"pk":..,"eph":..}
  接收端 -> 发送端:
    {"type":"accept","have":{rel:已有字节数}}                 （明文模式）
    {"type":"accept","enc":true,"have":..,"pk":..,"eph":..}   （加密模式）
    {"type":"reject","reason":..}
    {"type":"pair_start","id":..,"name":..,"pk":..,"eph":..}  （要求配对）
    {"type":"file_ok","path":rel,"hash_ok":bool,"skipped":bool}
    {"type":"file_fail","path":rel,"reason":..}
    {"type":"summary","ok":n,"fail":[...]}
  配对确认（加密通道建立后）:
    {"type":"pair_confirm"} / {"type":"pair_confirm","ok":false}
    {"type":"pair_result","ok":bool,"reason":..}

加密：配对/互信后，后续所有帧封装为 AES-256-GCM 加密帧
（见 pairing.EncryptedChannel）；SAS 6 位配对码人工核对防中间人。

断点续传：接收端以 ``<final>.part`` 保存未完成文件，accept 时回给已有字节数；
发送端 seek 后只传剩余部分。哈希始终按整文件 sha256。
"""

import hashlib
import json
import os
import socket
import threading
import time
import uuid
from collections import deque

from . import logger as applog
from .pairing import EncryptedChannel, Identity, KeyAgreement, TrustStore

log = applog.get_logger("transfer")

FILE_PORT_DEFAULT = 41893
CHUNK_SIZE = 1024 * 1024  # 1 MiB
PROGRESS_INTERVAL = 0.2  # 进度事件最小间隔（秒）
OFFER_TIMEOUT = 120.0  # 等待用户确认接收/配对的超时（秒）
LINE_LIMIT = 256 * 1024  # 控制帧最大长度


def sha256_file(path, cancel=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(CHUNK_SIZE)
            if not block:
                break
            h.update(block)
            if cancel is not None and cancel.is_set():
                raise OSError("已取消")
    return h.hexdigest()


def _rel_of(full, base):
    return os.path.relpath(full, base).replace(os.sep, "/")


def expand_paths(paths):
    """把用户选择的文件/目录展开为 [{rel, full, size}]（相对公共父目录）。"""
    files = []
    for p in paths or []:
        p = os.path.abspath(str(p))
        if os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                for n in names:
                    files.append(os.path.join(root, n))
        elif os.path.isfile(p):
            files.append(p)
    files = sorted(set(files))
    if not files:
        return []
    try:
        base = os.path.commonpath(files)
        if os.path.isfile(base):
            base = os.path.dirname(base)
    except ValueError:
        base = os.path.dirname(files[0])
    if not base or base == os.sep:
        base = os.path.dirname(files[0])
    return [
        {"rel": _rel_of(f, base), "full": f, "size": os.path.getsize(f)} for f in files
    ]


def _safe_rel(rel, base_dir):
    """校验相对路径并转换为绝对路径，防目录穿越。"""
    rel = str(rel).replace("\\", "/").strip("/")
    if not rel or ":" in rel:
        raise ValueError("非法路径：" + str(rel))
    parts = rel.split("/")
    if any(part in ("..", "") for part in parts):
        raise ValueError("非法路径：" + str(rel))
    return os.path.join(base_dir, *parts)


def _unique_path(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 1
    while True:
        cand = "%s (%d)%s" % (stem, i, ext)
        if not os.path.exists(cand):
            return cand
        i += 1


class _Speed:
    """滚动一秒窗口的速率估计。"""

    def __init__(self):
        self._samples = deque()

    def add(self, n):
        now = time.time()
        self._samples.append((now, n))
        self._trim(now)

    def _trim(self, now):
        while self._samples and now - self._samples[0][0] > 1.0:
            self._samples.popleft()

    def speed(self):
        now = time.time()
        self._trim(now)
        if not self._samples:
            return 0
        total = sum(n for _t, n in self._samples)
        span = self._samples[-1][0] - self._samples[0][0]
        return int(total / max(span, 0.1))


def _send_line(sock, obj):
    sock.sendall(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")


def _read_line(sock, buf):
    """读一行 JSON。返回 (dict, buf)；连接关闭返回 (None, buf)。"""
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            return None, buf
        buf += chunk
        if len(buf) > LINE_LIMIT:
            raise ValueError("控制帧超限")
    raw, buf = buf.split(b"\n", 1)
    return json.loads(raw.decode("utf-8")), buf


def _read_exact(sock, buf, n):
    """从 buf/sock 中精确读取 n 字节。返回 (data, buf)。"""
    data = b""
    while len(data) < n:
        if not buf:
            buf = sock.recv(min(65536, n - len(data)))
            if not buf:
                raise OSError("连接中断")
        take = min(len(buf), n - len(data))
        data += buf[:take]
        buf = buf[take:]
    return data, buf


class TransferEngine:
    """文件传输引擎：常驻接收监听 + 按需发送 + 配对加密。

    get_save_dir()/get_auto_accept()/get_require_pairing() 由桥接层注入（读配置）。
    on_event(name, payload) 向 UI 推事件：
      xfer_log / xfer_offer / xfer_progress / xfer_done /
      xfer_pair_show（接收方显示配对码）/ xfer_pair_input（发起方输入配对码）/
      xfer_pair_done（配对结束）。
    """

    def __init__(
        self,
        port=FILE_PORT_DEFAULT,
        device_id=None,
        device_name=None,
        get_save_dir=None,
        get_auto_accept=None,
        get_require_pairing=None,
        identity=None,
        trust=None,
        on_event=None,
        log=None,
        queue_path=None,
    ):
        self.port = int(port)
        self.device_id = device_id or "unknown"
        self.device_name = device_name or "unknown"
        self._get_save_dir = get_save_dir or (lambda: os.getcwd())
        self._get_auto_accept = get_auto_accept or (lambda: False)
        self._get_require_pairing = get_require_pairing or (lambda: False)
        self.identity = identity or Identity()
        self.trust = trust if trust is not None else TrustStore()
        self.on_event = on_event or (lambda n, p: None)
        self.log = log

        # 发送队列（持久化）：None = 不落盘（内存队列，测试友好）
        self.queue_path = queue_path
        self._queue = []  # 有序：{tid, ip, port, paths, peer, ts, status: pending|running|failed, err}
        self._queue_evt = threading.Event()  # 唤醒队列线程
        self._queue_stop = False

        self._lock = threading.Lock()
        self._transfers = {}  # tid -> state
        self._offers = {}  # tid -> (Event, decision list)
        self._pair_sessions = {}  # tid -> 配对会话
        self._server_sock = None
        self._running = False
        self._threads = []
        self.history = deque(maxlen=50)

    # ------------------------------------------------------------------
    def start(self):
        if self._running:
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("", self.port))
            server.listen(4)
        except OSError as e:
            log.error("传输服务监听 %d 失败：%s", self.port, e)
            self._emit_log("传输服务监听 %d 失败：%s（无法接收文件）" % (self.port, e))
            return
        server.settimeout(1.0)
        self._server_sock = server
        self._running = True
        t = threading.Thread(target=self._accept_loop, name="xfer-srv", daemon=True)
        qt = threading.Thread(target=self._queue_loop, name="xfer-queue", daemon=True)
        self._threads = [t, qt]
        if self.queue_path:
            self._load_queue()
        if self._queue:
            self._queue_evt.set()
        t.start()
        qt.start()
        log.info("文件传输服务已启动 port=%d", self.port)
        self._emit_log("文件传输服务已就绪（端口 TCP %d）。" % self.port)

    def stop(self):
        self._running = False
        self._queue_stop = True
        self._queue_evt.set()
        if self.queue_path:
            try:
                self._save_queue()
            except Exception:
                pass
        sock = self._server_sock
        self._server_sock = None
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        with self._lock:
            for st in self._transfers.values():
                st["cancel"].set()
        for t in self._threads:
            t.join(timeout=3)
        self._threads = []

    def _emit(self, name, payload):
        try:
            self.on_event(name, payload)
        except Exception:
            pass

    def _emit_log(self, msg):
        self._emit("xfer_log", str(msg))
        log.info("%s", msg)  # 传输运行日志同步落盘（INFO，含路径/状态，无敏感凭据）
        if self.log:
            try:
                self.log(msg)
            except Exception:
                pass

    # -- 对外查询 -------------------------------------------------------
    def snapshot(self):
        with self._lock:
            active = [
                {
                    "tid": tid,
                    "dir": st["dir"],
                    "peer": st["peer"],
                    "files": st["file_count"],
                    "total": st["total"],
                    "done": st["done_bytes"],
                    "current": st.get("current", ""),
                    "status": st["status"],
                    "speed": st["speed"].speed(),
                    "encrypted": st.get("encrypted", False),
                }
                for tid, st in self._transfers.items()
            ]
            active.sort(key=lambda s: s["tid"])
            return {"running": self._running, "port": self.port, "active": active,
                    "history": list(self.history), "trusted": self.trust.list(),
                    "queue": self._queue_snapshot()}

    def _queue_snapshot(self):
        return [
            {
                "tid": q["tid"], "peer": q["peer"], "ip": q["ip"], "port": q["port"],
                "paths": q["paths"], "ts": q["ts"],
                "status": q["status"], "err": q.get("err") or "",
            }
            for q in self._queue
        ]

    # -- 发送队列（持久化） ---------------------------------------------
    def enqueue(self, ip, port, paths, peer_name=None):
        """把发送任务加入队列（持久化），队列线程依次执行，失败可手动重试。"""
        paths = [str(p) for p in (paths or []) if p]
        if not paths:
            return {"ok": False, "err": "没有可发送的文件"}
        if not port:
            return {"ok": False, "err": "对方未开启文件传输服务"}
        tid = uuid.uuid4().hex[:8]
        item = {
            "tid": tid, "ip": str(ip), "port": int(port), "paths": paths,
            "peer": peer_name or str(ip), "ts": time.time(),
            "status": "pending", "err": "",
        }
        with self._lock:
            self._queue.append(item)
        self._save_queue()
        self._queue_evt.set()
        self._emit("xfer_queue", self._queue_snapshot())
        self._emit_log("已加入发送队列：%s（%d 个路径）" % (item["peer"], len(item["paths"])))
        return {"ok": True, "data": {"tid": tid, "peer": item["peer"]}}

    def queue_remove(self, tid):
        tid = str(tid)
        with self._lock:
            before = len(self._queue)
            self._queue = [q for q in self._queue if q["tid"] != tid]
            removed = len(self._queue) < before
        if removed:
            self._save_queue()
            self._emit("xfer_queue", self._queue_snapshot())
            return {"ok": True, "data": True}
        return {"ok": False, "err": "队列中没有该任务"}

    def queue_retry(self, tid):
        tid = str(tid)
        item = None
        with self._lock:
            item = next((q for q in self._queue if q["tid"] == tid), None)
            if item and item["status"] == "failed":
                item["status"] = "pending"
                item["err"] = ""
                self._queue_evt.set()
        if not item:
            return {"ok": False, "err": "任务不存在或不在失败状态"}
        self._save_queue()
        self._emit("xfer_queue", self._queue_snapshot())
        return {"ok": True, "data": True}

    def _save_queue(self):
        if not self.queue_path:
            return
        try:
            os.makedirs(os.path.dirname(self.queue_path), exist_ok=True)
            tmp = self.queue_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"items": list(self._queue)}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.queue_path)
        except OSError:
            pass

    def _load_queue(self):
        try:
            with open(self.queue_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return
        for it in items:
            if not isinstance(it, dict) or not it.get("paths"):
                continue
            if it.get("status") == "running":
                it["status"] = "pending"  # 运行中被中断（崩溃/退出）→ 重启续发
            elif it.get("status") not in ("pending", "failed"):
                continue  # done 已出队，不恢复
            it["tid"] = str(it.get("tid") or uuid.uuid4().hex[:8])
            it["peer"] = it.get("peer") or str(it.get("ip", ""))
            it.setdefault("err", "")
            self._queue.append(it)
        if self._queue:
            log.info("已恢复 %d 个待发送队列任务", len(self._queue))

    def _queue_loop(self):
        """串行执行队列中 pending 任务；失败任务保留供 UI 手动重试。"""
        while not self._queue_stop:
            item = None
            with self._lock:
                for q in self._queue:
                    if q["status"] == "pending":
                        item = q
                        break
            if item is None:
                self._queue_evt.wait(1.0)
                self._queue_evt.clear()
                continue
            with self._lock:
                item["status"] = "running"
                item["err"] = ""
            self._save_queue()
            self._emit("xfer_queue", self._queue_snapshot())
            files = expand_paths(item["paths"])
            if not files:
                with self._lock:
                    item["status"] = "failed"
                    item["err"] = "文件不存在或不可读"
                self._save_queue()
                self._emit("xfer_queue", self._queue_snapshot())
                continue
            st = {
                "dir": "send",
                "peer": item["peer"],
                "file_count": len(files),
                "total": sum(f["size"] for f in files),
                "done_bytes": 0,
                "current": "",
                "status": "准备中",
                "speed": _Speed(),
                "cancel": threading.Event(),
                "encrypted": False,
            }
            with self._lock:
                self._transfers[item["tid"]] = st
            entry = self._run_job(item["tid"], item["ip"], item["port"], files, st)
            with self._lock:
                if item["status"] == "running":
                    item["status"] = "done" if entry["ok"] else "failed"
                    item["err"] = entry.get("err") or ""
            self._save_queue()
            self._emit("xfer_queue", self._queue_snapshot())
            if item["status"] == "done":
                with self._lock:
                    self._queue = [q for q in self._queue if q["tid"] != item["tid"]]
                self._save_queue()
                self._emit("xfer_queue", self._queue_snapshot())

    def cancel(self, tid):
        with self._lock:
            st = self._transfers.get(str(tid))
        if not st:
            return {"ok": False, "err": "传输不存在或已结束"}
        st["cancel"].set()
        st["status"] = "取消中"
        return {"ok": True, "data": True}

    def respond(self, tid, accept):
        tid = str(tid)
        with self._lock:
            entry = self._offers.get(tid)
        if not entry:
            return {"ok": False, "err": "没有等待确认的传输"}
        event, box = entry
        box.append(bool(accept))
        event.set()
        return {"ok": True, "data": True}

    # -- 配对 API -------------------------------------------------------
    def pair_to(self, ip, port, peer_name=None):
        """发起独立配对连接。"""
        if not port:
            return {"ok": False, "err": "对方未开启文件传输服务"}
        tid = uuid.uuid4().hex[:8]
        sess = {
            "ka": None, "ch": None, "conn": None,
            "pin_event": threading.Event(), "pin": [],
            "decide_event": threading.Event(), "decide": [],
            "peer": peer_name or str(ip), "ip": str(ip), "port": int(port),
            "sas": None, "result": None,
        }
        with self._lock:
            self._pair_sessions[tid] = sess
        threading.Thread(target=self._pair_sender, args=(tid, sess), daemon=True).start()
        return {"ok": True, "data": {"tid": tid}}

    def pair_submit(self, tid, pin):
        """发起方提交对方屏幕上的配对码。"""
        tid = str(tid)
        with self._lock:
            sess = self._pair_sessions.get(tid)
        if not sess:
            return {"ok": False, "err": "配对会话不存在或已结束"}
        sess["pin"].append(str(pin or "").strip())
        sess["pin_event"].set()
        return {"ok": True, "data": True}

    def pair_decide(self, tid, allow):
        """接收方允许/拒绝配对。"""
        tid = str(tid)
        with self._lock:
            sess = self._pair_sessions.get(tid)
        if not sess:
            return {"ok": False, "err": "配对会话不存在或已结束"}
        sess["decide"].append(bool(allow))
        sess["decide_event"].set()
        if not allow and sess.get("conn"):
            try:
                sess["conn"].close()
            except OSError:
                pass
        return {"ok": True, "data": True}

    def untrust(self, device_id):
        ok = self.trust.untrust(device_id)
        return {"ok": True, "data": ok}

    # -- 发送端 ---------------------------------------------------------
    def send_to(self, ip, port, paths, peer_name=None):
        files = expand_paths(paths)
        if not files:
            return {"ok": False, "err": "没有可发送的文件"}
        if not port:
            return {"ok": False, "err": "对方未开启文件传输服务"}
        tid = uuid.uuid4().hex[:8]
        st = {
            "dir": "send",
            "peer": peer_name or str(ip),
            "file_count": len(files),
            "total": sum(f["size"] for f in files),
            "done_bytes": 0,
            "current": "",
            "status": "准备中",
            "speed": _Speed(),
            "cancel": threading.Event(),
            "encrypted": False,
        }
        with self._lock:
            self._transfers[tid] = st
        threading.Thread(
            target=self._run_job, args=(tid, str(ip), int(port), files, st),
            daemon=True,
        ).start()
        return {"ok": True, "data": {"tid": tid, "files": len(files), "total": st["total"]}}

    def _progress(self, tid, st):
        return {
            "tid": tid,
            "dir": st["dir"],
            "peer": st["peer"],
            "current": st.get("current", ""),
            "done": st["done_bytes"],
            "total": st["total"],
            "speed": st["speed"].speed(),
            "status": st["status"],
            "encrypted": st.get("encrypted", False),
        }

    def _maybe_progress(self, tid, st, force=False):
        now = time.time()
        if force or now - st.get("last_emit", 0.0) >= PROGRESS_INTERVAL:
            st["last_emit"] = now
            self._emit("xfer_progress", self._progress(tid, st))

    def _new_agreement(self):
        ka = KeyAgreement(self.identity)
        hello = ka.start(peer_id=self.device_id, peer_name=self.device_name)
        return ka, hello

    def _run_job(self, tid, ip, port, files, st):
        """执行一次发送任务（即时发送与队列共用）。返回 _finish 的历史条目。"""
        results = {"ok": 0, "fail": []}
        ch = None
        try:
            sock = socket.create_connection((ip, port), timeout=5)
        except OSError as e:
            entry = self._finish(tid, st, False, "连接 %s:%d 失败：%s" % (ip, port, e), results)
            return entry
        buf = b""
        try:
            sock.settimeout(15)
            # 预读计算整文件哈希（可被取消）
            offer_files = []
            st["status"] = "校验中"
            for f in files:
                if f["size"] == 0:
                    f["hash"] = hashlib.sha256(b"").hexdigest()
                else:
                    f["hash"] = sha256_file(f["full"], cancel=st["cancel"])
                offer_files.append({"path": f["rel"], "size": f["size"], "hash": f["hash"]})
            if st["cancel"].is_set():
                raise OSError("已取消")

            ka, hello = self._new_agreement()
            _send_line(sock, {
                "type": "offer", "tid": tid, "id": self.device_id,
                "name": self.device_name, "files": offer_files, "total": st["total"],
                "pk": hello["pk"], "eph": hello["eph"],
            })
            reply, buf = _read_line(sock, buf)
            if not reply:
                entry = self._finish(tid, st, False, "连接中断", results)
                return entry
            rtype = reply.get("type")
            if rtype == "reject":
                reason = reply.get("reason", "对方拒绝接收")
                self._emit_log("%s 拒绝了传输：%s" % (st["peer"], reason))
                entry = self._finish(tid, st, False, reason, results)
                return entry
            if rtype == "pair_start":
                # 传输中配对：协商密钥 → 用户输入配对码 → 等 pair_result/accept
                ka.accept_peer(reply, peer_id=reply.get("id"), peer_name=reply.get("name"))
                ch, ok, err, buf = self._pair_handshake_sender(tid, sock, ka, st["peer"], buf)
                if not ok:
                    entry = self._finish(tid, st, False, err or "配对失败", results)
                    return entry
                # 配对成功：保存信任，随后读加密 accept（用回传的 buf，可能已含 accept 残帧）
                self.trust.trust(reply.get("id") or st["peer"], reply.get("name") or st["peer"], reply["pk"])
                st["encrypted"] = True
                msg = None
                try:
                    msg, _raw, buf = ch.recv(buf)
                except (OSError, ValueError) as e:
                    entry = self._finish(tid, st, False, "配对后连接中断：%s" % e, results)
                    return entry
                if not msg or msg.get("type") != "accept":
                    entry = self._finish(tid, st, False, "配对后协议异常", results)
                    return entry
                have = msg.get("have") or {}
            elif rtype == "accept":
                if reply.get("enc"):
                    # 已配对设备：验证对端公钥可信后建立加密通道
                    ka.accept_peer(reply, peer_id=reply.get("id"), peer_name=reply.get("name"))
                    trusted = self.trust.by_pubkey(reply.get("pk", ""))
                    if not trusted:
                        _send_line(sock, {"type": "cancel", "reason": "对端身份未配对，已中止"})
                        entry = self._finish(tid, st, False, "对端不在已配对列表，已中止（防冒充）", results)
                        return entry
                    ch = ka.channel(sock)
                    st["encrypted"] = True
                    self._emit_log("与 %s 的传输已加密（AES-256-GCM）。" % st["peer"])
                else:
                    ch = None
                have = reply.get("have") or {}
            else:
                entry = self._finish(tid, st, False, "协议异常：%s" % rtype, results)
                return entry
            st["status"] = "传输中"

            for f in files:
                if st["cancel"].is_set():
                    self._send_frame(ch, sock, {"type": "cancel", "reason": "用户取消"})
                    break
                cont, buf = self._send_one(
                    ch, sock, tid, f, int(have.get(f["rel"], 0)), st, results, buf)
                if not cont:
                    self._send_frame(ch, sock, {"type": "cancel", "reason": "发送中止"})
                    break

            # 半关闭写端：接收端读到 EOF 后回 summary（避免双方互等死锁）
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            if ch:
                summary, _raw, buf = ch.recv(buf)
            else:
                summary, buf = _read_line(sock, buf)
            if summary and summary.get("type") == "summary":
                results = {"ok": summary.get("ok", 0), "fail": summary.get("fail", [])}
            canceled = st["cancel"].is_set()
            entry = self._finish(tid, st, not canceled and not results["fail"], None, results)
            return entry
        except (OSError, ValueError) as e:
            self._emit_log("发送到 %s 失败：%s" % (st["peer"], e))
            entry = self._finish(tid, st, False, str(e), results)
            return entry
        finally:
            with self._lock:
                self._pair_sessions.pop(tid, None)
            try:
                sock.close()
            except OSError:
                pass

    def _pair_handshake_sender(self, tid, sock, ka, peer, buf):
        """发起方配对握手：弹输入框等用户输入 SAS，核对后发 pair_confirm，等 pair_result。

        返回 (channel, ok, err, buf)。"""
        ch = ka.channel(sock)
        sess = {
            "ka": ka, "ch": ch, "conn": sock,
            "pin_event": threading.Event(), "pin": [],
            "peer": peer, "sas": ka.sas(),
            "decide_event": threading.Event(), "decide": [],
        }
        with self._lock:
            self._pair_sessions[tid] = sess
        self._emit("xfer_pair_input", {"tid": tid, "peer": peer})
        if not sess["pin_event"].wait(OFFER_TIMEOUT):
            return ch, False, "配对超时（未输入配对码）", buf
        pin = sess["pin"][0] if sess["pin"] else ""
        if pin != ka.sas():
            ch.send({"type": "pair_confirm", "ok": False, "reason": "配对码错误"})
            return ch, False, "配对码不匹配，已中止（谨防中间人）", buf
        ch.send({"type": "pair_confirm"})
        # 等 pair_result
        sock.settimeout(OFFER_TIMEOUT)
        try:
            msg, _raw, buf = ch.recv(buf)
        except (OSError, ValueError) as e:
            return ch, False, "等待配对结果失败：%s" % e, buf
        finally:
            sock.settimeout(15)
        if not msg or not msg.get("ok"):
            return ch, False, (msg or {}).get("reason", "对方拒绝了配对"), buf
        self._emit("xfer_pair_done", {"tid": tid, "peer": peer, "ok": True})
        self._emit_log("与 %s 配对成功，通道已加密。" % peer)
        return ch, True, None, buf

    def _send_frame(self, ch, sock, obj):
        if ch is not None:
            ch.send(obj)
        else:
            _send_line(sock, obj)

    def _send_one(self, ch, sock, tid, f, offset, st, results, buf):
        rel, size = f["rel"], f["size"]
        try:
            self._send_frame(ch, sock, {"type": "begin", "path": rel, "size": size, "hash": f["hash"]})
            if offset < size:
                with open(f["full"], "rb") as fp:
                    if offset > 0:
                        fp.seek(offset)
                    st["current"] = rel
                    while True:
                        if st["cancel"].is_set():
                            st["status"] = "已取消"
                            return False, buf
                        block = fp.read(CHUNK_SIZE)
                        if not block:
                            break
                        if ch is not None:
                            ch.send({"type": "chunk", "bin": len(block)}, raw=block)
                        else:
                            _send_line(sock, {"type": "chunk", "bin": len(block)})
                            sock.sendall(block)
                        st["done_bytes"] += len(block)
                        st["speed"].add(len(block))
                        self._maybe_progress(tid, st)
            self._maybe_progress(tid, st, force=True)
            self._send_frame(ch, sock, {"type": "end", "path": rel})
            if ch is not None:
                reply, _raw, buf = ch.recv(buf)
            else:
                reply, buf = _read_line(sock, buf)
            rtype = (reply or {}).get("type")
            if rtype == "file_ok":
                results["ok"] += 1
                self._emit_log(
                    "%s %s（%s）"
                    % ("秒传" if reply.get("skipped") else "已发送", rel,
                       "哈希一致" if reply.get("hash_ok", True) else "哈希不一致!")
                )
                return True, buf
            if rtype == "file_fail":
                results["fail"].append({"path": rel, "reason": reply.get("reason", "接收失败")})
                self._emit_log("发送失败 %s：%s" % (rel, reply.get("reason", "")))
                return not st["cancel"].is_set(), buf  # 单文件失败继续下一个
            return False, buf
        except (OSError, ValueError) as e:
            results["fail"].append({"path": rel, "reason": str(e)})
            self._emit_log("发送失败 %s：%s" % (rel, e))
            return False, buf

    def _finish(self, tid, st, ok, err, results):
        if st["cancel"].is_set():
            st["status"] = "已取消"
        elif ok and not err:
            st["status"] = "已完成"
        else:
            st["status"] = "失败"
        entry = {
            "tid": tid,
            "dir": st["dir"],
            "peer": st["peer"],
            "files": st["file_count"],
            "total": st["total"],
            "done": st["done_bytes"],
            "ok": ok,
            "err": err,
            "results": results or {},
            "encrypted": st.get("encrypted", False),
            "ts": time.time(),
        }
        self.history.appendleft(entry)
        self._emit("xfer_done", entry)
        with self._lock:
            self._transfers.pop(tid, None)
        return entry

    # -- 接收端 ---------------------------------------------------------
    def _accept_loop(self):
        while self._running:
            try:
                conn, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except (OSError, AttributeError):
                break
            t = threading.Thread(target=self._recv_dispatch, args=(conn, addr), daemon=True)
            t.start()

    def _recv_dispatch(self, conn, addr):
        """读第一帧分流：pair_hello → 独立配对；offer → 文件接收。"""
        buf = b""
        try:
            conn.settimeout(15)
            first, buf = _read_line(conn, buf)
        except (OSError, ValueError):
            try:
                conn.close()
            except OSError:
                pass
            return
        if first is None:
            try:
                conn.close()
            except OSError:
                pass
            return
        if first.get("type") == "pair_hello":
            self._pair_receiver(conn, first, addr)
        elif first.get("type") == "offer":
            self._recv_worker(conn, addr, first, buf)
        else:
            try:
                conn.close()
            except OSError:
                pass

    def _pair_receiver(self, conn, hello, addr):
        """独立配对连接（接收方）。"""
        peer = str(hello.get("name") or addr[0])
        tid = uuid.uuid4().hex[:8]
        ka = KeyAgreement(self.identity)
        mine = ka.start(peer_id=self.device_id, peer_name=self.device_name)
        try:
            ka.accept_peer(hello, peer_id=hello.get("id"), peer_name=peer)
        except (ValueError, KeyError) as e:
            _send_line(conn, {"type": "pair_result", "ok": False, "reason": "公钥无效：%s" % e})
            conn.close()
            return
        _send_line(conn, {
            "type": "pair_start", "id": self.device_id, "name": self.device_name,
            "pk": mine["pk"], "eph": mine["eph"],
        })
        ch = ka.channel(conn)
        sess = {
            "ka": ka, "ch": ch, "conn": conn,
            "pin_event": threading.Event(), "pin": [],
            "decide_event": threading.Event(), "decide": [],
            "peer": peer, "sas": ka.sas(),
        }
        with self._lock:
            self._pair_sessions[tid] = sess
        self._emit("xfer_pair_show", {"tid": tid, "peer": peer, "sas": ka.sas()})

        ok = False
        reason = "配对超时或被拒绝"
        conn.settimeout(2.0)
        deadline = time.time() + OFFER_TIMEOUT
        buf = b""
        confirmed = False
        try:
            while time.time() < deadline:
                if sess["decide"] and not sess["decide"][0]:
                    reason = "用户拒绝了配对"
                    break
                try:
                    msg, _raw, buf = ch.recv(buf)
                except socket.timeout:
                    continue
                except (OSError, ValueError) as e:
                    reason = "用户拒绝了配对" if (sess["decide"] and not sess["decide"][0]) else "连接中断：%s" % e
                    break
                if msg is None:
                    break
                if msg.get("type") == "pair_confirm":
                    if msg.get("ok") is False:
                        reason = msg.get("reason", "对方配对码输入错误")
                    else:
                        confirmed = True
                    break
            if confirmed:
                # 等用户点「允许」
                while time.time() < deadline and not sess["decide"]:
                    time.sleep(0.2)
                if sess["decide"] and sess["decide"][0]:
                    self.trust.trust(hello.get("id") or peer, peer, hello["pk"])
                    ch.send({"type": "pair_result", "ok": True})
                    self._emit("xfer_pair_done", {"tid": tid, "peer": peer, "ok": True})
                    self._emit_log("已与 %s 配对，后续传输自动加密。" % peer)
                    ok = True
                else:
                    reason = "用户拒绝了配对"
                    ch.send({"type": "pair_result", "ok": False, "reason": reason})
        except (OSError, ValueError) as e:
            reason = str(e)
        finally:
            if not ok:
                self._emit("xfer_pair_done", {"tid": tid, "peer": peer, "ok": False, "err": reason})
            with self._lock:
                self._pair_sessions.pop(tid, None)
            try:
                conn.close()
            except OSError:
                pass

    def _pair_sender(self, tid, sess):
        """独立配对连接（发起方）。"""
        ip, port = sess["ip"], sess["port"]
        peer = sess["peer"]
        try:
            sock = socket.create_connection((ip, port), timeout=5)
        except OSError as e:
            self._emit("xfer_pair_done", {"tid": tid, "peer": peer, "ok": False, "err": "连接失败：%s" % e})
            return
        sess["conn"] = sock
        buf = b""
        msg = None
        try:
            sock.settimeout(15)
            ka = KeyAgreement(self.identity)
            hello = ka.start(peer_id=self.device_id, peer_name=self.device_name)
            _send_line(sock, {
                "type": "pair_hello", "id": self.device_id, "name": self.device_name,
                "pk": hello["pk"], "eph": hello["eph"],
            })
            reply, buf = _read_line(sock, buf)
            if not reply or reply.get("type") != "pair_start":
                self._emit("xfer_pair_done", {"tid": tid, "peer": peer, "ok": False,
                                              "err": "对方拒绝配对或版本不支持"})
                return
            ka.accept_peer(reply, peer_id=reply.get("id"), peer_name=reply.get("name"))
            sess["ka"] = ka
            sess["ch"] = ka.channel(sock)
            sess["sas"] = ka.sas()
            self._emit("xfer_pair_input", {"tid": tid, "peer": peer})
            if not sess["pin_event"].wait(OFFER_TIMEOUT):
                self._emit("xfer_pair_done", {"tid": tid, "peer": peer, "ok": False, "err": "配对超时"})
                return
            pin = sess["pin"][0] if sess["pin"] else ""
            if pin != ka.sas():
                sess["ch"].send({"type": "pair_confirm", "ok": False, "reason": "配对码错误"})
                self._emit("xfer_pair_done", {"tid": tid, "peer": peer, "ok": False,
                                              "err": "配对码不匹配，已中止（谨防中间人）"})
                return
            sess["ch"].send({"type": "pair_confirm"})
            sock.settimeout(OFFER_TIMEOUT)
            try:
                msg, _raw, buf = sess["ch"].recv(buf)
            except (OSError, ValueError) as e:
                self._emit("xfer_pair_done", {"tid": tid, "peer": peer, "ok": False, "err": str(e)})
                return
            finally:
                sock.settimeout(15)
            if msg and msg.get("ok"):
                self.trust.trust(reply.get("id") or peer, reply.get("name") or peer, reply["pk"])
                self._emit("xfer_pair_done", {"tid": tid, "peer": peer, "ok": True})
                self._emit_log("已与 %s 配对，后续传输自动加密。" % peer)
            else:
                self._emit("xfer_pair_done", {"tid": tid, "peer": peer, "ok": False,
                                              "err": (msg or {}).get("reason", "对方拒绝了配对")})
        except (OSError, ValueError) as e:
            self._emit("xfer_pair_done", {"tid": tid, "peer": peer, "ok": False, "err": str(e)})
        finally:
            with self._lock:
                self._pair_sessions.pop(tid, None)
            try:
                sock.close()
            except OSError:
                pass

    def _recv_worker(self, conn, addr, offer, buf):
        peer = addr[0]
        st = None
        tid = None
        fp = None
        current = None
        ch = None
        results = {"ok": 0, "fail": []}
        try:
            conn.settimeout(15)
            st, ch, proceed = self._handle_offer(conn, offer, peer, buf)
            if not proceed:
                return
            tid = str(offer.get("tid") or uuid.uuid4().hex[:8])
            while True:
                if ch is not None:
                    msg, raw, buf = ch.recv(buf)
                    if msg is None:
                        break
                else:
                    msg, buf = _read_line(conn, buf)
                    if msg is None:
                        break
                    raw = b""
                mtype = msg.get("type")

                if mtype == "begin":
                    if fp:
                        fp.close()
                        fp = None
                    fp, current = self._open_part(msg, st)
                    st["current"] = str(msg.get("path", ""))
                    st["status"] = "传输中"
                elif mtype == "chunk":
                    n = int(msg.get("bin") or 0)
                    if ch is not None:
                        data = raw
                        if n and len(data) != n:
                            raise ValueError("加密块长度异常")
                    else:
                        data, buf = _read_exact(conn, buf, n)
                    if fp is None:
                        raise ValueError("未开始接收就收到数据块")
                    fp.write(data)
                    st["done_bytes"] += len(data)
                    st["speed"].add(len(data))
                    self._maybe_progress(tid, st)
                elif mtype == "end":
                    if fp:
                        fp.close()
                        fp = None
                    reply = self._finalize(current, st, results)
                    current = None
                    self._send_frame(ch, conn, reply)
                elif mtype == "cancel":
                    st["cancel"].set()
                    self._emit_log("%s 取消了传输" % st["peer"])
                    break
                if st["cancel"].is_set():
                    break
        except (OSError, ValueError) as e:
            self._emit_log("接收自 %s 的传输出错：%s" % (peer, e))
            if st is not None:
                results["fail"].append({"path": st.get("current", ""), "reason": str(e)})
        finally:
            if fp:
                try:
                    fp.close()
                except OSError:
                    pass
            if st is not None:
                try:
                    self._send_frame(ch, conn, {"type": "summary", "ok": results["ok"], "fail": results["fail"]})
                except OSError:
                    pass
            try:
                conn.close()
            except OSError:
                pass
            if st is not None:
                canceled = st["cancel"].is_set()
                self._finish(tid, st, not canceled and not results["fail"], None, results)

    def _handle_offer(self, conn, msg, peer, buf):
        """处理 offer。返回 (state, channel或None, 是否继续)。"""
        save_dir = self._get_save_dir()
        files = msg.get("files") or []
        hashes = {}
        have = {}
        for f in files:
            rel = f.get("path")
            size = int(f.get("size") or 0)
            fhash = str(f.get("hash") or "")
            hashes[rel] = fhash
            try:
                _safe_rel(rel, save_dir)
            except ValueError:
                self._emit_log("对方发来的路径非法，已拒绝：" + str(rel))
                _send_line(conn, {"type": "reject", "reason": "路径非法：" + str(rel)})
                return None, None, False
            final = _safe_rel(rel, save_dir)
            part = final + ".part"
            if size > 0 and os.path.exists(final) and os.path.getsize(final) == size:
                try:
                    if not fhash or sha256_file(final) == fhash:
                        have[rel] = size  # 秒传：同哈希文件已存在
                except OSError:
                    have[rel] = 0
            elif os.path.exists(part):
                have[rel] = min(os.path.getsize(part), size)

        peer = str(msg.get("name") or peer)
        peer_id = str(msg.get("id") or "")
        st = {
            "dir": "recv",
            "peer": peer,
            "file_count": len(files),
            "total": int(msg.get("total") or 0),
            "done_bytes": sum(have.values()),
            "current": "",
            "status": "等待确认",
            "speed": _Speed(),
            "cancel": threading.Event(),
            "hashes": hashes,
            "have": have,
            "encrypted": False,
        }

        # -- 加密/配对协商 ------------------------------------------------
        ka = None
        ch = None
        pk_sender = str(msg.get("pk") or "")
        if pk_sender:
            ka = KeyAgreement(self.identity)
            mine = ka.start(peer_id=self.device_id, peer_name=self.device_name)
            try:
                ka.accept_peer(msg, peer_id=peer_id, peer_name=peer)
            except (ValueError, KeyError) as e:
                _send_line(conn, {"type": "reject", "reason": "公钥无效：%s" % e})
                return None, None, False
            trusted = self.trust.by_pubkey(pk_sender)

        if ka is not None and pk_sender and self.trust.by_pubkey(pk_sender):
            # 已配对：直接加密接收，无需再确认
            _send_line(conn, {
                "type": "accept", "enc": True, "have": have,
                "pk": mine["pk"], "eph": mine["eph"],
                "id": self.device_id, "name": self.device_name,
            })
            ch = ka.channel(conn)
            st["encrypted"] = True
            with self._lock:
                self._transfers[str(msg.get("tid"))] = st
            st["status"] = "传输中"
            self._emit_log("开始加密接收来自 %s 的 %d 个文件（AES-256-GCM）。" % (peer, len(files)))
            self._maybe_progress(str(msg.get("tid")), st, force=True)
            return st, ch, True

        if ka is not None and pk_sender and self._get_require_pairing():
            # 未配对但强制加密：走配对流程
            ok, reason, ch = self._rcv_pair_shared(conn, ka, mine, msg, peer, peer_id, st, buf)
            if not ok:
                _send_line(conn, {"type": "reject", "reason": reason or "配对失败"})
                self._emit_log("来自 %s 的配对失败：%s" % (peer, reason))
                return None, None, False
            st["encrypted"] = True
            ch.send({"type": "accept", "have": have})
            with self._lock:
                self._transfers[str(msg.get("tid"))] = st
            st["status"] = "传输中"
            self._emit_log("配对成功，开始加密接收来自 %s 的 %d 个文件。" % (peer, len(files)))
            self._maybe_progress(str(msg.get("tid")), st, force=True)
            return st, ch, True

        # -- 明文模式：逐次确认（现状） -----------------------------------
        accepted = self._get_auto_accept()
        if not accepted:
            event = threading.Event()
            with self._lock:
                self._offers[str(msg.get("tid"))] = (event, [])
            self._emit("xfer_offer", {
                "tid": msg.get("tid"), "peer": peer,
                "files": [{"path": f.get("path"), "size": int(f.get("size") or 0)} for f in files],
                "total": int(msg.get("total") or 0),
            })
            event.wait(OFFER_TIMEOUT)
            with self._lock:
                entry = self._offers.pop(str(msg.get("tid")), None)
            decisions = entry[1] if entry else []
            accepted = bool(decisions and decisions[0])
        if not accepted:
            _send_line(conn, {"type": "reject", "reason": "对方未确认（超时或拒绝）"})
            self._emit_log("来自 %s 的传输请求未被确认，已拒绝。" % peer)
            return None, None, False
        with self._lock:
            self._transfers[str(msg.get("tid"))] = st
        st["status"] = "传输中"
        _send_line(conn, {"type": "accept", "have": have})
        self._emit_log("开始接收来自 %s 的 %d 个文件。" % (peer, len(files)))
        self._maybe_progress(str(msg.get("tid")), st, force=True)
        return st, None, True

    def _rcv_pair_shared(self, conn, ka, mine, msg, peer, peer_id, st, buf):
        """传输中配对（接收方）。返回 (ok, reason, ch)。成功时信任已保存，ch 复用其
        加密通道（计数器与发送侧一致，供后续 accept/文件帧继续使用）。"""
        tid = str(msg.get("tid"))
        _send_line(conn, {
            "type": "pair_start",
            "pk": mine["pk"], "eph": mine["eph"],
            "id": self.device_id, "name": self.device_name,
        })
        ch = ka.channel(conn)
        sess = {
            "ka": ka, "ch": ch, "conn": conn,
            "pin_event": threading.Event(), "pin": [],
            "decide_event": threading.Event(), "decide": [],
            "peer": peer, "sas": ka.sas(),
        }
        with self._lock:
            self._pair_sessions[tid] = sess
        self._emit("xfer_pair_show", {"tid": tid, "peer": peer, "sas": ka.sas()})

        conn.settimeout(2.0)
        deadline = time.time() + OFFER_TIMEOUT
        confirmed = False
        reason = "配对超时"
        try:
            while time.time() < deadline:
                if sess["decide"] and not sess["decide"][0]:
                    return False, "用户拒绝了配对", ch
                try:
                    m, _raw, buf = ch.recv(buf)
                except socket.timeout:
                    continue
                except (OSError, ValueError) as e:
                    if sess["decide"] and not sess["decide"][0]:
                        return False, "用户拒绝了配对", ch
                    return False, "连接中断：%s" % e, ch
                if m is None:
                    return False, "连接中断", ch
                if m.get("type") == "pair_confirm":
                    if m.get("ok") is False:
                        return False, m.get("reason", "对方配对码输入错误"), ch
                    confirmed = True
                    break
            if not confirmed:
                return False, reason, ch
            # 等用户允许
            while time.time() < deadline and not sess["decide"]:
                time.sleep(0.2)
            if not (sess["decide"] and sess["decide"][0]):
                ch.send({"type": "pair_result", "ok": False, "reason": "用户拒绝了配对"})
                return False, "用户拒绝了配对", ch
            self.trust.trust(peer_id or peer, peer, str(msg.get("pk") or ""))
            ch.send({"type": "pair_result", "ok": True})
            self._emit("xfer_pair_done", {"tid": tid, "peer": peer, "ok": True})
            self._emit_log("与 %s 配对成功。" % peer)
            return True, None, ch
        finally:
            conn.settimeout(15)
            with self._lock:
                self._pair_sessions.pop(tid, None)

    def _open_part(self, msg, st):
        """begin：打开 .part 文件。返回 (fp_or_None, current)。"""
        save_dir = self._get_save_dir()
        rel = str(msg.get("path"))
        final = _safe_rel(rel, save_dir)
        size = int(msg.get("size") or 0)
        have = int(st["have"].get(rel, 0))
        skip = have >= size and size > 0
        if skip:
            return None, {"final": final, "rel": rel, "size": size, "skip": True}
        part = final + ".part"
        os.makedirs(os.path.dirname(final) or ".", exist_ok=True)
        offset = 0
        if os.path.exists(part):
            offset = min(os.path.getsize(part), size)
        fp = open(part, "r+b" if offset else "w+b")
        fp.seek(offset)
        return fp, {"final": final, "rel": rel, "size": size, "skip": False}

    def _finalize(self, current, st, results):
        rel = current["rel"]
        size = current["size"]
        final = current["final"]
        want = str(st["hashes"].get(rel) or "")
        try:
            if current["skip"]:
                # 未传输：本地已有内容（秒传/断点恰好收完），校验后回执
                if os.path.exists(final) and os.path.getsize(final) == size:
                    path = final
                else:
                    return {"type": "file_fail", "path": rel, "reason": "本地缺少完整文件"}
            else:
                part = final + ".part"
                path = _unique_path(final)
                os.replace(part, path)
            hash_ok = (not want) or sha256_file(path) == want
            results["ok"] += 1
            self._emit_log(
                "%s %s（%s）"
                % ("秒传" if current["skip"] else "已接收", rel,
                   "哈希一致" if hash_ok else "哈希不一致!")
            )
            return {"type": "file_ok", "path": rel, "hash_ok": hash_ok,
                    "skipped": current["skip"]}
        except OSError as e:
            results["fail"].append({"path": rel, "reason": str(e)})
            return {"type": "file_fail", "path": rel, "reason": str(e)}
