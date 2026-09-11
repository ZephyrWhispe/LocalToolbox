"""剪贴板同步协议测试：两个实例在 localhost 上互传 + 回声抑制 + 去重。"""

import time

from app.core.clipboard_sync import ClipboardSync

PORT_A = 41991  # A 接收端口
PORT_B = 41992  # B 接收端口


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def make_sync(port, device_id, name, writer_log):
    return ClipboardSync(
        sync_port=port,
        device_id=device_id,
        name=name,
        reader=lambda: None,  # 不读真实剪贴板
        writer=lambda t: writer_log.append(t),
    )


# 1) A → B 单向同步
awrites, bwrites = [], []
a = make_sync(PORT_A, "dev-a", "A机", awrites)
b = make_sync(PORT_B, "dev-b", "B机", bwrites)
a.start(send_enabled=True, recv_enabled=True)
b.start(send_enabled=False, recv_enabled=True)
assert a.running and b.running
a.add_manual_peer("127.0.0.1", PORT_B)
a.broadcast_text("你好，B")
assert wait_for(lambda: b.history and b.history[0]["text"] == "你好，B"), b.history
assert b.history[0]["device"] == "A机"
assert b.history[0]["remote"] is True
assert wait_for(lambda: bwrites == ["你好，B"]), bwrites
assert wait_for(lambda: len(a.history) == 1 and a.history[0]["device"] == "本机")
print("1) A -> B push OK")

# 2) 回声抑制：B 本机剪贴板变成刚从 A 收到的内容时，不再转发/重复记录
before = len(b.history)
b._on_clip_change("你好，B")  # 该内容哈希在 B 的 _suppress 中
assert len(b.history) == before
print("2) echo suppression OK")

# 3) 60 秒内容去重：同内容短时间重复广播只投递一次
a._sent.clear()
b._recv.clear()
a.broadcast_text("重复内容")
assert wait_for(lambda: any(e["text"] == "重复内容" for e in b.history))
count_after_first = len([e for e in b.history if e["text"] == "重复内容"])
a.broadcast_text("重复内容")
time.sleep(0.6)
count_after_second = len([e for e in b.history if e["text"] == "重复内容"])
assert count_after_first == 1 and count_after_second == 1, (
    count_after_first,
    count_after_second,
)
print("3) dedup OK")

# 4) B → A 反向同步
b.add_manual_peer("127.0.0.1", PORT_A)
b.stop()
b.start(send_enabled=True, recv_enabled=True)
b.add_manual_peer("127.0.0.1", PORT_A)
b.broadcast_text("回复，A")
assert wait_for(lambda: any(e["text"] == "回复，A" for e in a.history)), a.history
assert wait_for(lambda: "回复，A" in awrites), awrites
print("4) B -> A push OK")

# 5) 接收开关关闭时不接收
b.stop()
b.start(send_enabled=True, recv_enabled=False)
a._sent.clear()
a.broadcast_text("不应收到")
time.sleep(0.8)
assert not any(e["text"] == "不应收到" for e in b.history)
print("5) recv-disabled OK")

a.stop()
b.stop()
assert not a.running and not b.running
print("CLIPBOARD SYNC TEST PASSED")
