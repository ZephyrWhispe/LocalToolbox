"""剪贴板文件同步测试（T-01 / v2.8）：A → B 文件推送 + 落盘 + 写回 + 越界防护 + 去重。

注入 file_reader/file_writer 桩，不触碰真实系统剪贴板（CF_HDROP 读写单独由
test_clip_windows 或人工验收覆盖）。
"""

import base64
import json
import os
import shutil
import tempfile
import time

from app.core import clipboard_sync as cs
from app.core.clipboard_sync import ClipboardSync

PORT_A = 41993
PORT_B = 41994


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def make_fsync(port, device_id, name, source, b_writes):
    """source：可调用返回伪造 CF_HDROP 路径列表；b_writes：B 写回剪贴板记录。"""
    return ClipboardSync(
        sync_port=port,
        device_id=device_id,
        name=name,
        reader=lambda: None,
        writer=lambda t: None,
        img_reader=lambda: None,
        file_reader=lambda: source() or None,
        file_writer=lambda ps: b_writes.extend(ps),
    )


tmpdir = tempfile.mkdtemp(prefix="clip-file-")
b_writes = []
a = make_fsync(PORT_A, "dev-fa", "A机", lambda: [src_path], b_writes)
b = make_fsync(PORT_B, "dev-fb", "B机", lambda: None, b_writes)
src_path = os.path.join(tmpdir, "notes.txt")
with open(src_path, "w", encoding="utf-8") as f:
    f.write("剪贴板文件同步内容 1234567890")

a.start(send_enabled=True, recv_enabled=True)
b.start(send_enabled=False, recv_enabled=True)
a.add_manual_peer("127.0.0.1", PORT_B)

try:
    # 6) 文件同步 A → B：历史 + 落盘 + 内容一致 + 写回剪贴板
    a._on_clip_files([src_path])
    assert wait_for(lambda: b.history and b.history[0].get("kind") == "file"), b.history
    e = b.history[0]
    assert e["file_name"] == "notes.txt", e
    assert e["device"] == "A机" and e["remote"] is True, e
    assert os.path.isfile(e["file_path"]), e
    with open(e["file_path"], "rb") as f:
        assert f.read() == open(src_path, "rb").read()
    assert wait_for(lambda: len(b_writes) == 1 and os.path.isfile(b_writes[0])), b_writes
    print("6) file push + paint + disk OK")

    # 7) 目录跳过：不产生新历史
    before = len(b.history)
    a._on_clip_files([tmpdir])
    time.sleep(0.6)
    assert len(b.history) == before, "文件夹不应被同步"
    print("7) folder skip OK")

    # 8) 超限跳过：临时调低 MAX_FILE_BYTES
    old_max = cs.MAX_FILE_BYTES
    cs.MAX_FILE_BYTES = 100
    try:
        big = os.path.join(tmpdir, "big.bin")
        with open(big, "wb") as f:
            f.write(b"x" * 4096)
        a._on_clip_files([big])
        time.sleep(0.6)
        assert all(not (x.get("kind") == "file" and x["file_name"] == "big.bin")
                   for x in b.history), "超限文件不应发送"
    finally:
        cs.MAX_FILE_BYTES = old_max
    print("8) oversize skip OK")

    # 9) 接收端防路径穿越：文件名清洗后无目录段，且落在 clip_files 目录内
    old_dir = cs.CLIP_FILE_DIR
    cs.CLIP_FILE_DIR = os.path.join(tmpdir, "clip_files")
    try:
        evil = {
            "type": "clip", "id": "x", "name": "恶意端",
            "kind": "file", "fname": "..\\..\\evil.txt", "size": 4,
            "payload": base64.b64encode(b"evil").decode(), "ts": time.time(),
        }
        b._handle_line(json.dumps(evil).encode(), "127.0.0.1")
        time.sleep(0.3)
        got = [x for x in b.history if x.get("kind") == "file" and x["file_size"] == 4]
        assert got, "穿越文件消息应被处理"
        assert got[0]["file_name"] == "evil.txt", got[0]["file_name"]
        assert os.path.abspath(got[0]["file_path"]).startswith(
            os.path.abspath(cs.CLIP_FILE_DIR) + os.sep), got[0]["file_path"]
    finally:
        cs.CLIP_FILE_DIR = old_dir
    print("9) path-traversal guard OK")

    # 10) 60 秒去重：同内容再次触发不重复推送
    b._recv.clear()
    n = len(b.history)
    a._on_clip_files([src_path])
    time.sleep(0.6)
    assert len(b.history) == n, "同内容 60 秒内不应重复推送"
    print("10) file dedup OK")

    # 11) 接收端写回剪贴板后不再由本机监听重推（suppress 生效）
    #   —— 用 _handle_file 路径模拟：b 收到后 file_writer 已记录，此处验证 suppress 命中
    from app.core.clipboard_sync import hash_bytes
    with open(src_path, "rb") as f:
        h = hash_bytes(f.read())
    assert b._suppress_has(h), "接收写入后应记录抑制哈希"
    print("11) receive-suppress OK")

finally:
    a.stop()
    b.stop()
    shutil.rmtree(tmpdir, ignore_errors=True)

assert not a.running and not b.running
print("CLIPBOARD FILE SYNC TEST PASSED")