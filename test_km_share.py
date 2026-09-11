"""键鼠共享协议测试：控制端 -> 被控端事件流（注入用桩函数，不碰真实输入）。

含 T-03 边缘滑动、T-04 多目标直连拓扑、T-05 DPI/屏幕参数交换（v2.9）。
"""

import ctypes
import time

from app.core.km_share import (
    _BACK_ARM_NORM,
    EDGE_MARGIN,
    KBDLLHOOKSTRUCT,
    LLKHF_INJECTED,
    LLMHF_INJECTED,
    MSLLHOOKSTRUCT,
    WM_KEYDOWN,
    WM_MOUSEMOVE,
    KmController,
    KmTarget,
    _virtual_screen,
)

PORT = 41993


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


logs = []
injected = []

target = KmTarget(port=PORT, injector=injected.append, name="T机", log=logs.append)
target.start()
assert target.running

controller = KmController(name="C机", log=logs.append, hooks=False)
controller.connect("127.0.0.1", PORT)
assert controller.connected
assert controller.peer == "T机"

# 1) 未激活控制时事件不注入
controller._enqueue({"type": "key", "vk": 65, "scan": 30, "ext": False, "up": False})
time.sleep(0.6)
assert not injected and not target.controlled
print("0) inactive -> no injection OK")

# 2) 开始控制后四类事件全部注入
controller.set_control(True)
assert wait_for(lambda: target.controlled), logs
controller._enqueue({"type": "move", "x": 100, "y": 200})
controller._enqueue({"type": "btn", "btn": 0, "up": False})
controller._enqueue({"type": "wheel", "delta": -120})
controller._enqueue({"type": "key", "vk": 65, "scan": 30, "ext": False, "up": True})
assert wait_for(lambda: len(injected) >= 4), (injected, logs)
kinds = [e["type"] for e in injected]
assert kinds == ["move", "btn", "wheel", "key"], kinds
assert injected[0]["x"] == 100 and injected[0]["y"] == 200
assert injected[1] == {"type": "btn", "btn": 0, "up": False}
assert injected[2]["delta"] == -120
assert injected[3]["vk"] == 65 and injected[3]["up"] is True
print("1) control + 4 event types OK")

# 3) 释放控制后事件不再注入
controller.set_control(False)
assert wait_for(lambda: not target.controlled)
controller._enqueue({"type": "move", "x": 1, "y": 1})
time.sleep(0.6)
assert len(injected) == 4
print("2) release -> no injection OK")

# 4) 「允许被控制」关闭时拒绝控制
target.enabled = False
controller.set_control(True)
assert wait_for(lambda: controller.active)  # 控制端已发出
time.sleep(0.6)
assert not target.controlled, logs
target.enabled = True
print("3) refuse when disabled OK")

controller.disconnect()
assert not controller.connected and not controller.active
target.stop()
assert not target.running
print("KM SHARE TEST PASSED")
print("---- v2.9 多目标 / 边缘 / DPI ----")

# ---- T-04 多目标直连拓扑：两个被控端，切换 active 目标 ----
injected2, injected3 = [], []
t2 = KmTarget(port=PORT + 1, injector=injected2.append, name="T2机", log=logs.append)
t3 = KmTarget(port=PORT + 2, injector=injected3.append, name="T3机", log=logs.append)
t2.start()
t3.start()
assert t2.running and t3.running

c2 = KmController(name="C机", log=logs.append, hooks=False)
c2.attach("127.0.0.1", PORT + 1)          # 主目标（localhost:41994）
c2.attach("127.0.0.2", PORT + 2, activate=False)  # 第二目标（loopback 别名，Windows）
assert c2.connected and len(c2._targets) == 2
assert c2.active_ip == "127.0.0.1"

# 4) 控制开始后事件只发给 active 目标
c2.set_control(True)
assert wait_for(lambda: t2.controlled), logs
c2._enqueue({"type": "move", "x": 100, "y": 200})
assert wait_for(lambda: len(injected2) == 1), injected2
assert not injected3
print("4) multi-target routing OK")

# 5) 切换目标：T2 释放、T3 接管
n2 = len(injected2)
c2.set_active("127.0.0.2")
assert c2.active_ip == "127.0.0.2"
assert wait_for(lambda: not t2.controlled and t3.controlled), (t2.controlled, t3.controlled)
c2._enqueue({"type": "key", "vk": 65, "scan": 30, "ext": False, "up": False})
assert wait_for(lambda: injected3), injected3
assert len(injected2) == n2, "非 active 目标不应再收事件"
c2._enqueue({"type": "wheel", "delta": -120})
time.sleep(0.4)
assert len(injected2) == n2 and len(injected3) == 2, (injected2, injected3)
print("5) set_active switch + routing OK")

# 6) T-05 屏幕参数交换：hello/welcome 携带 virtual screen 与 DPI
tl = c2.targets_list()
assert len(tl) == 2
for x in tl:
    assert "screen" in x and isinstance(x.get("dpi"), int)
    assert x["screen"].get("w", 0) > 0 and x["screen"].get("h", 0) > 0, x
print("6) dpi/screen handshake OK")

# 7) T-03 边缘滑动：未控制时鼠标到达右边缘触发 on_edge("right")，连续触发只回掉一次
hits = []
c2.on_edge = lambda d, f: hits.append((d, round(f, 2)))
c2.set_control(False)
time.sleep(0.3)
ms = MSLLHOOKSTRUCT()
x0, y0, w, h = _virtual_screen()
ms.pt.x = int(x0 + w - 1 - EDGE_MARGIN // 2)
ms.pt.y = int(y0 + h // 2)
lp = ctypes.cast(ctypes.byref(ms), ctypes.c_void_p).value
c2._ms_proc_impl(0, WM_MOUSEMOVE, lp)
c2._ms_proc_impl(0, WM_MOUSEMOVE, lp)  # 仍在边缘：不重复触发
assert [d for d, _ in hits] == ["right"], hits
# 离开边缘后再次进入 → 再次触发
ms.pt.x = int(x0 + w // 2)
c2._ms_proc_impl(0, WM_MOUSEMOVE, lp)
ms.pt.x = int(x0 + w - 2)
c2._ms_proc_impl(0, WM_MOUSEMOVE, lp)
assert hits == ["right", "right"] or [d for d, _ in hits] == ["right", "right"], hits
print("7) edge-swipe trigger OK")

c2.disconnect()
assert not c2.connected and not c2.active
t2.stop()
t3.stop()
assert not t2.running and not t3.running
print("---- KM EXTENDED PASSED ----")
print("---- v4.7 入口重映射 / 边缘切回 / 锁定输入 ----")

# ---- v4.7：控制器逻辑用「伪目标」直测（queue 直接记录，免去 TCP 异步竞态） ----
class _FakeQ:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(ev)

    def put_nowait(self, ev):
        self.items.append(ev)


c4 = KmController(name="C机", log=logs.append, hooks=False)
x0, y0, w, h = _virtual_screen()
fake = {"sock": None, "peer": "FAKE", "queue": _FakeQ(), "sender": None,
        "screen": {"x": x0, "y": y0, "w": w, "h": h}, "dpi": 96}
with c4._lock:
    c4._targets["fake"] = fake
c4.active_ip = "fake"
c4.connected = True
c4.peer = "FAKE"


def _moves():
    return [e for e in fake["queue"].items if e["type"] == "move"]


def _ms_move(ctrl, px, py):
    ms = MSLLHOOKSTRUCT()
    ms.pt.x = int(px)
    ms.pt.y = int(py)
    lp = ctypes.cast(ctypes.byref(ms), ctypes.c_void_p).value
    ctrl._ms_proc_impl(0, WM_MOUSEMOVE, lp)


def _last_move():
    return _moves()[-1]


# 8) 入口重映射：从本机右缘穿越（E=right, frac=0.5）→ 远端光标出现在目标左缘
c4.set_control(True)
c4.set_entry("right", 0.5)
mv = _last_move()
assert mv["x"] == 0 and mv["y"] == int(0.5 * 65535), mv
print("8) entry placement at opposite edge OK")

mid_x, mid_y = x0 + w // 2, y0 + h // 2
_ms_move(c4, mid_x, mid_y)  # 首个事件：建立差分基准（不发 move，远端已在入口）
assert len(_moves()) == 1, _moves()

# 9) 差分转发：轨迹越出本机屏不再钳死（持续右移，远端光标持续前进）
step_norm = 100 * 65536 // w
for i in range(1, 41):
    _ms_move(c4, mid_x + 100 + i * 100, mid_y)
m = _last_move()
assert m["x"] == min(65535, step_norm * 40), (m, step_norm)
assert m["x"] > _BACK_ARM_NORM, (m["x"],)  # 已武装切回检测
print("9) delta forwarding beyond screen bounds OK")

# 10) 控制中切回：远端光标滑回入口对侧边 → on_edge_back(本机出口边, 沿边分数)
backs = []
c4.on_edge_back = lambda d, f: backs.append((d, round(f, 2)))
_ms_move(c4, mid_x, mid_y)  # 回到入口水平 → 触发切回
assert backs == [("right", 0.5)], backs
# 武装重置：停在边缘不再重复触发
_ms_move(c4, mid_x + 50, mid_y)
_ms_move(c4, mid_x, mid_y)
assert len(backs) == 1, backs
print("10) edge-back switch OK")

# 11) 热键开始的控制（无入口）：镜像模式 + 无切回检测
c4.set_control(False)
c4.set_control(True)
_ms_move(c4, mid_x, mid_y)
first = _last_move()
_ms_move(c4, mid_x + 100, mid_y)
second = _last_move()
assert second["x"] == first["x"] + step_norm, (first, second)
_ms_move(c4, x0 + 1, mid_y)  # 轨迹到左缘：无入口不触发切回
assert backs == [("right", 0.5)], backs
c4.set_control(False)
print("11) hotkey control mirrors + no edge-back OK")

# 12) 边缘宽度可调（margin=30：距边 20px 即触发；margin=4 不触发）
edges = []
c4.on_edge = lambda d, f: edges.append(d)
c4.edge_margin = 30
_ms_move(c4, x0 + w - 1 - 20, y0 + h // 2)
assert edges == ["right"], edges
c4.edge_margin = 4
_ms_move(c4, x0 + w // 2, y0 + h // 2)  # 离开边缘复位 _edge_fired
_ms_move(c4, x0 + w - 1 - 2, y0 + h // 2)  # 2px 距离在 margin=4 内仍触发
assert len(edges) == 2, edges
c4.on_edge = None
print("12) edge margin configurable OK")

# 13) KmTarget 锁定钩子判定（回调级，不装真实钩子）：物理吞掉、注入放行
t4 = KmTarget(injector=lambda ev: None, name="T4机", log=logs.append)
t4.controlled = True
t4.lock_input = True
kb = KBDLLHOOKSTRUCT()
kb.vkCode = 65
kb.flags = 0  # 物理按键
lp = ctypes.cast(ctypes.byref(kb), ctypes.c_void_p).value
assert t4._kb_lock_proc(0, WM_KEYDOWN, lp) == 1
kb.flags = LLKHF_INJECTED  # 注入按键
assert t4._kb_lock_proc(0, WM_KEYDOWN, lp) != 1
ms = MSLLHOOKSTRUCT()
ms.pt.x = 100
ms.flags = LLMHF_INJECTED  # 注入鼠标（对方操作）
lp = ctypes.cast(ctypes.byref(ms), ctypes.c_void_p).value
assert t4._ms_lock_proc(0, WM_MOUSEMOVE, lp) != 1
ms.flags = 0  # 物理鼠标（被控端本地用户）
lp = ctypes.cast(ctypes.byref(ms), ctypes.c_void_p).value
assert t4._ms_lock_proc(0, WM_MOUSEMOVE, lp) == 1
# 未被控 / 未开锁时不拦截
t4.controlled = False
ms.flags = 0
lp = ctypes.cast(ctypes.byref(ms), ctypes.c_void_p).value
assert t4._ms_lock_proc(0, WM_MOUSEMOVE, lp) != 1
t4.lock_input = False
print("13) target lock-input procs OK")
print("---- KM V4.7 PASSED ----")
