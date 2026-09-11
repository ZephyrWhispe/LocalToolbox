"""键鼠共享：把控制端的键盘鼠标实时延伸到局域网内的被控端。

- 被控端 KmTarget：监听 TCP，接收事件流并用 SendInput 注入；
- 控制端 KmController：连接被控端，低级钩子（WH_KEYBOARD_LL/WH_MOUSE_LL）捕获
  本机输入并转发；控制期间事件被拦截、不再作用于本机；
- 切换热键：Ctrl+Alt+K 开始/释放控制（MVP，见 功能设计文档 §4.4）。
"""

import ctypes
import json
import queue
import socket
import sys
import threading
from ctypes import wintypes

from . import logger as applog
from .discovery import local_hostname

log = applog.get_logger("kmshare")

KM_PORT_DEFAULT = 41892

# ---------------- Windows 常量 ----------------
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

# T-03 边缘滑动：鼠标到达本机虚拟屏边缘 N 物理像素内视为「穿越」目标方向
EDGE_MARGIN = 4
# 控制中切回检测：远端光标离开入口边超过该归一化距离后才武装（约 1/10 屏宽，
# 防止入口抖动或轻微回拉就误触切回）
_BACK_ARM_NORM = 6554

_OPP_EDGE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10   # 键盘 LL 钩子注入标志
LLMHF_INJECTED = 0x01   # 鼠标 LL 钩子注入标志
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_K = 0x4B

_BTN_FLAGS = {
    (0, False): MOUSEEVENTF_LEFTDOWN,
    (0, True): MOUSEEVENTF_LEFTUP,
    (1, False): MOUSEEVENTF_RIGHTDOWN,
    (1, True): MOUSEEVENTF_RIGHTUP,
    (2, False): MOUSEEVENTF_MIDDLEDOWN,
    (2, True): MOUSEEVENTF_MIDDLEUP,
}

_ON_WINDOWS = sys.platform == "win32"
if _ON_WINDOWS:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", wintypes.POINT),
            ("mouseData", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    LRESULT = ctypes.c_ssize_t  # Python 3.12 的 wintypes 已无 LRESULT
    HOOKPROC = ctypes.WINFUNCTYPE(
        LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )
    user32.CallNextHookEx.restype = LRESULT
    user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        HOOKPROC,
        wintypes.HINSTANCE,
        wintypes.DWORD,
    ]
else:
    user32 = None
    kernel32 = None


def _send_input(inp):
    arr = (INPUT * 1)(inp)
    return user32.SendInput(1, arr, ctypes.sizeof(INPUT))


def set_cursor_pos(x, y):
    """移动本机光标到指定物理坐标（控制端切回本机时归位用）。"""
    if not _ON_WINDOWS:
        return
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.SetCursorPos(int(x), int(y))


def _hook_thread_main(kb_proc, ms_proc, tid_box):
    """低级钩子线程主循环（控制端捕获 / 被控端锁定共用）。

    线程 id 写入 tid_box["tid"]，外部用 PostThreadMessageW(WM_QUIT) 停止。
    """
    tid_box["tid"] = kernel32.GetCurrentThreadId()
    hkb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kb_proc, None, 0)
    hms = user32.SetWindowsHookExW(WH_MOUSE_LL, ms_proc, None, 0)
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        pass
    if hkb:
        user32.UnhookWindowsHookEx(hkb)
    if hms:
        user32.UnhookWindowsHookEx(hms)


def _stop_hook_thread(hook_thread, tid_box):
    tid = tid_box.get("tid") or 0
    if tid:
        user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
    if hook_thread:
        hook_thread.join(timeout=3)
    tid_box["tid"] = 0


def inject_event(ev):
    """把事件注入本机（被控端用）。"""
    if not _ON_WINDOWS:
        raise RuntimeError("仅支持 Windows")
    t = ev.get("type")
    if t == "move":
        inp = INPUT(type=INPUT_MOUSE)
        inp.union.mi = MOUSEINPUT(
            dx=int(ev["x"]) & 0xFFFF,
            dy=int(ev["y"]) & 0xFFFF,
            mouseData=0,
            dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            time=0,
            dwExtraInfo=None,
        )
        _send_input(inp)
    elif t == "btn":
        flags = _BTN_FLAGS.get((int(ev.get("btn", 0)), bool(ev.get("up"))))
        if not flags:
            return
        inp = INPUT(type=INPUT_MOUSE)
        inp.union.mi = MOUSEINPUT(
            dx=0, dy=0, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=None
        )
        _send_input(inp)
    elif t == "wheel":
        inp = INPUT(type=INPUT_MOUSE)
        inp.union.mi = MOUSEINPUT(
            dx=0,
            dy=0,
            mouseData=int(ev.get("delta", 0)) & 0xFFFFFFFF,
            dwFlags=MOUSEEVENTF_WHEEL,
            time=0,
            dwExtraInfo=None,
        )
        _send_input(inp)
    elif t == "key":
        flags = 0
        if ev.get("ext"):
            flags |= KEYEVENTF_EXTENDEDKEY
        if ev.get("up"):
            flags |= KEYEVENTF_KEYUP
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.union.ki = KEYBDINPUT(
            wVk=int(ev.get("vk", 0)) & 0xFFFF,
            wScan=int(ev.get("scan", 0)) & 0xFFFF,
            dwFlags=flags,
            time=0,
            dwExtraInfo=None,
        )
        _send_input(inp)


def _virtual_screen():
    x0 = user32.GetSystemMetrics(76)
    y0 = user32.GetSystemMetrics(77)
    w = user32.GetSystemMetrics(78) or 1
    h = user32.GetSystemMetrics(79) or 1
    return x0, y0, w, h


def _screen_info():
    """本机虚拟屏参数（T-05 DPI 精确映射）：随 hello/welcome 交换。"""
    if not _ON_WINDOWS:
        return {"screen": {"x": 0, "y": 0, "w": 0, "h": 0}, "dpi": 96}
    try:
        x0, y0, w, h = _virtual_screen()
        dpi = int(user32.GetDpiForSystem())  # Win10 1607+；旧系统返回 0 时兜底
        if dpi <= 0:
            dpi = 96
        return {"screen": {"x": x0, "y": y0, "w": w, "h": h}, "dpi": dpi}
    except Exception:
        return {"screen": {"x": 0, "y": 0, "w": 0, "h": 0}, "dpi": 96}


def _encode(msg):
    return json.dumps(msg).encode("utf-8") + b"\n"


class KmTarget:
    """被控端：接受一个控制端连接并注入其事件。"""

    def __init__(
        self,
        port=KM_PORT_DEFAULT,
        injector=None,
        discovery=None,
        name=None,
        log=None,
    ):
        self.port = int(port)
        self.injector = injector or inject_event
        self.discovery = discovery
        self.device_name = name or local_hostname()
        self.log = log
        self.enabled = True  # 「允许被控制」总开关
        self.lock_input = False  # 被控时屏蔽本机物理键鼠
        self.controlled = False
        self.controller_name = ""
        self._server_sock = None
        self._running = False
        self._hook_thread = None
        self._kb_proc = None
        self._ms_proc = None
        self._tid_box = {}

    @property
    def running(self):
        return self._running

    def start(self):
        if self._running:
            return
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("", self.port))
            server.listen(1)
            server.settimeout(1.0)
        except OSError as e:
            log.error("被控端监听 %d 失败：%s", self.port, e)
            self._emit_log(f"监听 {self.port} 失败：{e}")
            return
        self._server_sock = server
        self._running = True
        if self.discovery is not None:
            self.discovery.advertise["km_port"] = self.port
        threading.Thread(target=self._accept_loop, name="km-target", daemon=True).start()
        log.info("被控端已就绪 port=%d", self.port)
        self._emit_log(f"被控端已就绪，等待控制端连接（端口 {self.port}）。")

    def stop(self):
        self._running = False
        self.controlled = False
        self._update_lock_hooks()
        sock = self._server_sock
        self._server_sock = None
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        if self.discovery is not None:
            self.discovery.advertise["km_port"] = None
        log.info("被控端已停止")
        self._emit_log("被控端已停止。")

    def set_lock_input(self, enabled):
        """设置「被控时屏蔽本机物理键鼠」；被控状态下即时安装/卸载钩子。"""
        self.lock_input = bool(enabled)
        self._update_lock_hooks()

    def _update_lock_hooks(self):
        """按（被控中 且 锁输入开启）安装/卸载本机输入屏蔽钩子。"""
        want = bool(self.controlled and self.lock_input)
        if want and not self._hook_thread:
            if not _ON_WINDOWS:
                return
            self._kb_proc = HOOKPROC(self._kb_lock_proc)
            self._ms_proc = HOOKPROC(self._ms_lock_proc)
            self._tid_box = {}
            self._hook_thread = threading.Thread(
                target=_hook_thread_main,
                args=(self._kb_proc, self._ms_proc, self._tid_box),
                name="km-lock", daemon=True,
            )
            self._hook_thread.start()
            log.info("被控锁定：已屏蔽本机物理键鼠")
            self._emit_log("被控中：本机物理键鼠已屏蔽（对方操作不受影响）。")
        elif not want and self._hook_thread:
            _stop_hook_thread(self._hook_thread, self._tid_box)
            self._hook_thread = None
            self._kb_proc = None
            self._ms_proc = None
            log.info("被控锁定：已恢复本机键鼠")
            self._emit_log("本机键鼠已恢复。")

    def _kb_lock_proc(self, code, wparam, lparam):
        if code == 0 and self.controlled and self.lock_input:
            kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if not (kb.flags & LLKHF_INJECTED):
                return 1  # 屏蔽本机物理键盘；注入事件带 INJECTED 标志放行
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _ms_lock_proc(self, code, wparam, lparam):
        if code == 0 and self.controlled and self.lock_input:
            ms = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if not (ms.flags & LLMHF_INJECTED):
                return 1  # 屏蔽本机物理鼠标
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _accept_loop(self):
        while self._running:
            server = self._server_sock
            if not server:
                break
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self.enabled:
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            log.info("控制端 %s 已连接（被控）", addr[0])
            self._emit_log(f"控制端 {addr[0]} 已连接。")
            try:
                self._client_loop(conn, addr)
            finally:
                self.controlled = False
                self.controller_name = ""
                self._update_lock_hooks()
                log.info("控制端 %s 已断开（被控）", addr[0])
                self._emit_log("控制端已断开。")

    def _client_loop(self, conn, addr):
        conn.settimeout(3.0)
        buf = b""
        try:
            welcome = {"type": "welcome", "name": self.device_name}
            welcome.update(_screen_info())
            conn.sendall(_encode(welcome))
            while self._running:
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line:
                        self._handle_line(line, addr)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handle_line(self, line, addr):
        try:
            msg = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        t = msg.get("type")
        if t == "control":
            self.controlled = bool(msg.get("active")) and self.enabled
            self.controller_name = str(msg.get("name") or addr[0])
            if msg.get("active") and not self.enabled:
                log.warning("拒绝控制请求：「允许被控制」未开启")
                self._emit_log("已拒绝控制请求：「允许被控制」未开启。")
            else:
                log.info("控制%s（被控端）", "开始" if self.controlled else "结束")
                self._emit_log(
                    "控制开始。" if self.controlled else "控制结束。"
                )
            self._update_lock_hooks()
            return
        if t in ("key", "move", "btn", "wheel") and self.controlled and self.enabled:
            try:
                self.injector(msg)
            except Exception as e:
                log.warning("事件注入失败：%s", e)
                self._emit_log(f"注入失败：{e}")

    def _emit_log(self, msg):
        if self.log:
            try:
                self.log(msg)
            except Exception:
                pass


class KmController:
    """控制端：捕获本机键鼠发往被控端，热键 Ctrl+Alt+K 切换。"""

    def __init__(self, name=None, log=None, hooks=True):
        self.device_name = name or local_hostname()
        self.log = log
        self.hooks_enabled = bool(hooks)  # 测试可关闭真实低级钩子
        self.connected = False
        self.active = False
        self.peer = ""
        self.active_ip = ""
        self._targets = {}  # ip -> {sock, peer, queue, sender, screen, dpi}
        self._hook_thread = None
        self._kb_proc = None
        self._ms_proc = None
        self._tid_box = {}  # 钩子线程 id（PostThreadMessageW 停止用）
        self._lock = threading.Lock()
        self._edge_fired = False
        # T-03 边缘滑动回调：on_edge(direction, frac) → API 层据此切换 active 目标
        self.on_edge = None
        # 边缘穿越（v4.7）：入口重映射 + 控制中切回本机
        self.edge_margin = EDGE_MARGIN  # 边缘判定宽度（物理 px，设置可覆盖）
        self._entry = None      # 入口 {"dir": 本机出口边, "frac": 沿边分数}
        self._vc = None         # 远端虚拟光标（目标屏归一化 0-65535）
        self._last_raw = None   # 上个 move 事件的本机轨迹位置（差分基准）
        self._armed = False     # 切回检测武装标志
        self.on_edge_back = None  # on_edge_back(本机出口边, 沿边分数)

    # ---------------- 连接管理（T-04 多目标直连拓扑） ----------------
    def attach(self, ip, port=KM_PORT_DEFAULT, activate=True):
        """连接并登记一个被控端目标；可挂多个（直连星型）。"""
        ip = str(ip).strip()
        if ip in self._targets:
            if activate and not self.active_ip:
                self.active_ip = ip
            return
        try:
            sock = socket.create_connection((ip, int(port)), timeout=3)
        except OSError as e:
            log.warning("连接被控端 %s:%s 失败：%s", ip, port, e)
            raise
        sock.settimeout(3.0)
        hello = {"type": "hello", "role": "controller", "name": self.device_name}
        hello.update(_screen_info())
        sock.sendall(_encode(hello))
        buf = b""
        while b"\n" not in buf:
            data = sock.recv(4096)
            if not data:
                sock.close()
                log.warning("被控端 %s 无响应", ip)
                raise OSError("对方无响应")
            buf += data
        reply = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        if reply.get("type") != "welcome":
            sock.close()
            log.warning("被控端 %s 不是键鼠共享被控端", ip)
            raise OSError("对方不是键鼠共享被控端")
        target = {
            "sock": sock,
            "peer": str(reply.get("name") or ip),
            "queue": queue.Queue(maxsize=1024),
            "sender": None,
            "screen": reply.get("screen") or {},
            "dpi": reply.get("dpi") or 96,
        }
        target["sender"] = threading.Thread(
            target=self._sender_loop, args=(ip,), name="km-send", daemon=True
        )
        with self._lock:
            self._targets[ip] = target
        target["sender"].start()
        if not self.active_ip or activate:
            self.active_ip = ip
        self.connected = True
        self.peer = self._targets.get(self.active_ip, {}).get("peer", "")
        if self.hooks_enabled and not (self._hook_thread and self._hook_thread.is_alive()):
            self.start_hooks()
        log.info("已连接被控端 %s", target["peer"])
        self._emit_log(f"已连接被控端：{target['peer']}。Ctrl+Alt+K 开始控制，鼠标滑出屏幕边缘可切换。")

    def connect(self, ip, port=KM_PORT_DEFAULT):
        """兼容旧接口：替换全部目标为单目标连接。"""
        self.disconnect()
        self.attach(ip, port)

    def detach(self, ip):
        """断开某个目标（其余目标不受影响）。"""
        ip = str(ip or "")
        with self._lock:
            target = self._targets.pop(ip, None)
            self.connected = bool(self._targets)
        if not target:
            return
        sock = target["sock"]
        try:
            sock.close()
        except OSError:
            pass
        sender = target["sender"]
        if sender:
            sender.join(timeout=2)
        if self.active_ip == ip:
            self.active_ip = next(iter(self._targets), self.active_ip)
        if self.active and not self._targets:
            self.active = False
        self.peer = self._targets.get(self.active_ip, {}).get("peer", "")
        if self.active_ip in self._targets:
            self.peer = self._targets[self.active_ip]["peer"]
        log.info("已断开被控端 %s", target["peer"])
        self._emit_log(f"已断开被控端：{target['peer']}。")
        if self.connected is False and not self._targets:
            self._edge_fired = False
            self.stop_hooks()

    def disconnect(self):
        """断开全部目标。"""
        for ip in list(self._targets.keys()):
            self.detach(ip)
        self.active_ip = ""
        self.peer = ""

    def set_active(self, ip):
        """切换当前被控制的目标（保持 active 状态迁移）。"""
        ip = str(ip or "")
        if ip not in self._targets:
            return
        if ip == self.active_ip:
            return
        old_t = self._targets.get(self.active_ip)
        if old_t and self.active:
            old_t["queue"].put(
                {"type": "control", "active": False, "name": self.device_name})
        self.active_ip = ip
        self.peer = self._targets[ip]["peer"]
        self._reset_entry()  # 手动切换无入口信息，远端光标回到镜像比例
        if self.active:
            self._targets[ip]["queue"].put(
                {"type": "control", "active": True, "name": self.device_name})
        self._emit_log(f"已切换控制目标：{self.peer}。")

    def set_control(self, active):
        t = self._targets.get(self.active_ip)
        if not t:
            return
        self.active = bool(active)
        t["queue"].put({"type": "control", "active": self.active, "name": self.device_name})
        if self.active:
            self._edge_fired = False
        else:
            self._reset_entry()
        log.info("控制端%s控制 %s", "开始" if self.active else "释放", t["peer"])
        self._emit_log("开始控制对方。" if self.active else "已释放控制。")

    def _reset_entry(self):
        """清除边缘穿越入口状态（释放控制/手动切换/掉线时）。"""
        self._entry = None
        self._vc = None
        self._last_raw = None
        self._armed = False

    def set_entry(self, direction, frac):
        """记录边缘穿越入口并把远端光标放到目标屏对侧边缘。

        direction：本机滑出的边（right=目标在本机右侧）；远端光标从目标屏的
        opp(direction) 边进入，沿边位置按 frac 比例对齐。
        """
        if direction not in _OPP_EDGE:
            return
        frac = min(1.0, max(0.0, float(frac or 0.0)))
        self._entry = {"dir": direction, "frac": frac}
        self._last_raw = None
        self._armed = False
        edge = _OPP_EDGE[direction]
        if edge == "left":
            nx, ny = 0, int(frac * 65535)
        elif edge == "right":
            nx, ny = 65535, int(frac * 65535)
        elif edge == "top":
            nx, ny = int(frac * 65535), 0
        else:
            nx, ny = int(frac * 65535), 65535
        self._vc = (nx, ny)
        self._enqueue({"type": "move", "x": nx, "y": ny})

    def toggle(self):
        self.set_control(not self.active)

    def targets_list(self):
        """当前挂接的目标详情（供 UI/调试）。"""
        with self._lock:
            return [
                {
                    "peer": t["peer"], "ip": ip, "active": ip == self.active_ip,
                    "screen": t["screen"], "dpi": t["dpi"],
                }
                for ip, t in self._targets.items()
            ]

    # ---------------- 事件发送 ----------------
    def _sender_loop(self, ip):
        while True:
            with self._lock:
                target = self._targets.get(ip)
            if not target:
                break
            try:
                msg = target["queue"].get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                target["sock"].sendall(_encode(msg))
            except OSError as e:
                log.warning("与被控端 %s 失去连接：%s", ip, e)
                self._emit_log(f"与被控端 {ip} 失去连接：{e}")
                self._drop(ip)
                break

    def _drop(self, ip):
        """连接异常时的内部清理（不重复发日志）。"""
        with self._lock:
            target = self._targets.pop(ip, None)
            self.connected = bool(self._targets)
        if not target:
            return
        try:
            target["sock"].close()
        except OSError:
            pass
        if self.active_ip == ip:
            self.active_ip = next(iter(self._targets), "")
        self.active = self.active and bool(self._targets)
        self.peer = self._targets.get(self.active_ip, {}).get("peer", "") or ""
        self._reset_entry()
        if not self._targets:
            self.stop_hooks()

    # ---------------- 低级钩子 ----------------
    def start_hooks(self):
        if not _ON_WINDOWS or (self._hook_thread and self._hook_thread.is_alive()):
            return
        self._kb_proc = HOOKPROC(self._kb_proc_impl)
        self._ms_proc = HOOKPROC(self._ms_proc_impl)
        self._hook_thread = threading.Thread(
            target=self._hook_loop, name="km-hook", daemon=True
        )
        self._hook_thread.start()

    def stop_hooks(self):
        _stop_hook_thread(self._hook_thread, self._tid_box)
        self._kb_proc = None
        self._ms_proc = None

    def _hook_loop(self):
        _hook_thread_main(self._kb_proc, self._ms_proc, self._tid_box)

    def _hotkey_pressed(self, vk):
        return bool(
            user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
            and user32.GetAsyncKeyState(VK_MENU) & 0x8000
            and vk == VK_K
        )

    def _kb_proc_impl(self, code, wparam, lparam):
        if code == 0:
            kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            if self._hotkey_pressed(kb.vkCode):
                if down and not self.active:
                    self.toggle()
                return 1  # 热键不上屏
            if self.active:
                self._enqueue(
                    {
                        "type": "key",
                        "vk": kb.vkCode,
                        "scan": kb.scanCode,
                        "ext": bool(kb.flags & LLKHF_EXTENDED),
                        "up": not down,
                    }
                )
                return 1  # 控制期间拦截，不作用于本机
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _ms_proc_impl(self, code, wparam, lparam):
        if code != 0:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        ms = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        if self.active:
            if wparam == WM_MOUSEMOVE:
                x0, y0, w, h = _virtual_screen()
                # 轨迹位置不钳制：pt 会随输入流持续累积，可越出本机屏
                raw_x = (ms.pt.x - x0) * 65536 // w
                raw_y = (ms.pt.y - y0) * 65536 // h
                self._forward_move(raw_x, raw_y)
            elif wparam in (WM_LBUTTONDOWN, WM_LBUTTONUP):
                self._enqueue({"type": "btn", "btn": 0, "up": wparam == WM_LBUTTONUP})
            elif wparam in (WM_RBUTTONDOWN, WM_RBUTTONUP):
                self._enqueue({"type": "btn", "btn": 1, "up": wparam == WM_RBUTTONUP})
            elif wparam in (WM_MBUTTONDOWN, WM_MBUTTONUP):
                self._enqueue({"type": "btn", "btn": 2, "up": wparam == WM_MBUTTONUP})
            elif wparam == WM_MOUSEWHEEL:
                delta = ctypes.c_short(ms.mouseData >> 16).value
                self._enqueue({"type": "wheel", "delta": delta})
            return 1  # 控制期间拦截鼠标
        # 未控制：T-03 边缘滑动 —— 鼠标到本机虚拟屏边缘触发穿越回调
        if wparam == WM_MOUSEMOVE:
            x0, y0, w, h = _virtual_screen()
            pt = ms.pt
            direction = None
            frac = 0.0
            if pt.x <= x0 + self.edge_margin:
                direction, frac = "left", (pt.y - y0) / h
            elif pt.x >= x0 + w - 1 - self.edge_margin:
                direction, frac = "right", (pt.y - y0) / h
            elif pt.y <= y0 + self.edge_margin:
                direction, frac = "top", (pt.x - x0) / w
            elif pt.y >= y0 + h - 1 - self.edge_margin:
                direction, frac = "bottom", (pt.x - x0) / w
            if direction:
                if not self._edge_fired:
                    self._edge_fired = True
                    if self.on_edge:
                        try:
                            self.on_edge(direction, min(1.0, max(0.0, frac)))
                        except Exception:
                            pass
            else:
                self._edge_fired = False
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _forward_move(self, raw_x, raw_y):
        """轨迹差分转发：增量驱动远端虚拟光标，不受本机屏边界钳制。

        首个事件只建立差分基准；带入口（边缘穿越）时远端光标已由 set_entry
        放置，无入口（热键控制）时从本机当前比例位置镜像开始（原行为）。
        """
        if self._last_raw is None:
            self._last_raw = (raw_x, raw_y)
            if self._vc is None:
                vx = min(65535, max(0, raw_x))
                vy = min(65535, max(0, raw_y))
                self._vc = (vx, vy)
                self._enqueue({"type": "move", "x": vx, "y": vy})
            return
        step_x = raw_x - self._last_raw[0]
        step_y = raw_y - self._last_raw[1]
        self._last_raw = (raw_x, raw_y)
        vx = min(65535, max(0, self._vc[0] + step_x))
        vy = min(65535, max(0, self._vc[1] + step_y))
        if (vx, vy) == self._vc:
            return
        self._vc = (vx, vy)
        self._enqueue({"type": "move", "x": vx, "y": vy})
        self._maybe_arm(vx, vy)
        self._check_edge_back()

    def _maybe_arm(self, vx, vy):
        """远端光标离开入口对侧边足够远后，武装切回检测。"""
        if not self._entry or self._armed:
            return
        back = _OPP_EDGE[self._entry["dir"]]
        if back in ("left", "right"):
            d = abs(vx - (0 if back == "left" else 65535))
        else:
            d = abs(vy - (0 if back == "top" else 65535))
        if d > _BACK_ARM_NORM:
            self._armed = True

    def _check_edge_back(self):
        """控制中：远端光标滑回入口对侧边 → 回调切回本机。

        仅边缘穿越进入的控制生效（热键开始的控制须热键释放，避免拖拽到
        目标屏边角时误触）。
        """
        if not self._entry or not self._armed or not self.on_edge_back:
            return
        back = _OPP_EDGE[self._entry["dir"]]
        t = self._targets.get(self.active_ip) or {}
        tw = (t.get("screen") or {}).get("w") or 1920
        th = (t.get("screen") or {}).get("h") or 1080
        margin_x = max(0, int(self.edge_margin * 65536 / max(1, tw)))
        margin_y = max(0, int(self.edge_margin * 65536 / max(1, th)))
        vx, vy = self._vc
        hit = (
            (back == "left" and vx <= margin_x)
            or (back == "right" and vx >= 65535 - margin_x)
            or (back == "top" and vy <= margin_y)
            or (back == "bottom" and vy >= 65535 - margin_y)
        )
        if not hit:
            return
        frac = (vy / 65535.0) if back in ("left", "right") else (vx / 65535.0)
        self._armed = False
        try:
            self.on_edge_back(self._entry["dir"], frac)
        except Exception:
            pass

    def _enqueue(self, ev):
        t = self._targets.get(self.active_ip)
        if not t:
            return
        try:
            t["queue"].put_nowait(ev)
        except queue.Full:
            pass  # 拥塞时丢弃输入事件，避免钩子回调阻塞

    def _emit_log(self, msg):
        if self.log:
            try:
                self.log(msg)
            except Exception:
                pass
