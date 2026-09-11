"""全局热键：系统级快捷键（组合键可自定义，支持接管被系统占用的 Win+ 组合）。

实现分两级：
1. user32.RegisterHotKey 注册到消息队列线程（常规路径，组合键未被占用时使用）；
2. 注册失败且组合含 Win（典型：Win+V 被系统剪贴板历史占用）时，降级为
   WH_KEYBOARD_LL 底层键盘钩子拦截接管——本应用运行期间该组合归本应用，
   退出后自动恢复原样。

组合键格式：修饰键（Ctrl/Alt/Shift/Win）任意数量 + 一个按键，以 "+" 连接，
如 "Ctrl+Alt+V"、"Win+V"、"Ctrl+Shift+F8"；字母/数字必须带至少一个修饰键
（避免普通打字时误触发），F1–F24 可单独使用。
"""

import ctypes
import threading
from ctypes import wintypes

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WH_KEYBOARD_LL = 13
VK_V = 0x56  # 字母 V（兼容旧引用）
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C

_MOD_NAMES = {
    "CTRL": MOD_CONTROL, "CONTROL": MOD_CONTROL,
    "ALT": MOD_ALT,
    "SHIFT": MOD_SHIFT,
    "WIN": MOD_WIN, "WINDOWS": MOD_WIN, "META": MOD_WIN,
}
_DISPLAY_ORDER = (("Ctrl", MOD_CONTROL), ("Alt", MOD_ALT),
                  ("Shift", MOD_SHIFT), ("Win", MOD_WIN))


def vk_for(key):
    """按键名 → 虚拟键码。支持 A-Z / 0-9 / F1-F24。"""
    k = str(key).strip()
    if len(k) == 1 and k.isascii() and (k.isalpha() or k.isdigit()):
        return ord(k.upper())
    m = len(k) >= 2 and k[0] in "Ff"
    if m and k[1:].isdigit() and 1 <= int(k[1:]) <= 24:
        return 0x70 + int(k[1:]) - 1
    raise ValueError("不支持的按键：%s（支持字母、数字、F1-F24）" % key)


def vk_to_key(vk):
    if 0x70 <= vk <= 0x87:
        return "F" + str(vk - 0x70 + 1)
    if 48 <= vk <= 90:
        return chr(vk)
    return None


def parse_combo(text):
    """解析 "Ctrl+Alt+V" / "Win+V" / "F8" → (mods 位掩码, vk, 规范显示名)。"""
    parts = [p.strip() for p in str(text or "").split("+") if p.strip()]
    if not parts:
        raise ValueError("快捷键不能为空")
    mods = 0
    key = None
    for p in parts:
        upper = p.upper()
        if upper in _MOD_NAMES:
            mods |= _MOD_NAMES[upper]
        else:
            if key is not None:
                raise ValueError("快捷键格式错误：%s" % text)
            key = p
    if key is None:
        raise ValueError("快捷键缺少按键：%s" % text)
    vk = vk_for(key)
    if not (0x70 <= vk <= 0x87) and not mods:
        raise ValueError("字母/数字键需搭配 Ctrl/Alt/Shift/Win 修饰键")
    return mods, vk, combo_to_text(mods, vk)


def combo_to_text(mods, vk):
    parts = [name for name, bit in _DISPLAY_ORDER if mods & bit]
    k = vk_to_key(vk)
    if k is None:
        raise ValueError("不支持的键码：%s" % vk)
    parts.append(k)
    return "+".join(parts)


class HotkeyError(Exception):
    """热键设置失败（格式错误 / 被其他程序占用等）。"""


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# 64 位 ctypes 必须声明签名，否则指针/句柄被截断
_user32.RegisterHotKey.argtypes = [
    wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT,
]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.UnregisterHotKey.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [
    ctypes.POINTER(_MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
]
_user32.GetMessageW.restype = ctypes.c_int
_user32.TranslateMessage.argtypes = [ctypes.POINTER(_MSG)]
_user32.TranslateMessage.restype = wintypes.BOOL
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(_MSG)]
_user32.DispatchMessageW.restype = ctypes.c_long
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL

# 线程消息循环至少须消费一条消息（SetTimer）才稳定，注册后立即触发一次回调即可
CONSUME_LIMIT = 64


class GlobalHotkey:
    """常规全局热键（RegisterHotKey 路径）。start() 注册并监听，on_activate 在工作线程回调。"""

    def __init__(self, hotkey_id=0x4D41, mods=MOD_CONTROL | MOD_ALT, vk=VK_V,
                 desc=None, log=None):
        self._log = log or (lambda m: None)
        self._id = int(hotkey_id)
        self._mods = int(mods)
        self._vk = int(vk)
        try:
            self._desc = desc or combo_to_text(self._mods, self._vk)
        except ValueError:
            self._desc = str(desc or "自定义快捷键")
        self._cb = None
        self._stop = False
        self._registered = False
        self._thread = None
        self._ready = threading.Event()
        self._tid = 0

    @property
    def active(self):
        return bool(self._thread and self._thread.is_alive() and self._registered)

    def start(self, on_activate=None):
        if on_activate is not None:
            self._cb = on_activate
        self.stop()
        self._stop = False
        self._ready.clear()
        self._registered = False
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="global-hotkey")
        self._thread.start()
        self._ready.wait(1.0)

    def stop(self):
        self._stop = True
        if self._tid:
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self):
        self._tid = _kernel32.GetCurrentThreadId()
        if not _user32.RegisterHotKey(None, self._id, self._mods, self._vk):
            self._log("全局热键注册失败（可能被其他程序占用），已禁用：%s" % self._desc)
            self._ready.set()
            return
        self._registered = True
        self._ready.set()
        msg = _MSG()
        while not self._stop:
            r = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            if msg.message == WM_HOTKEY and msg.wParam == self._id:
                try:
                    if self._cb:
                        self._cb()
                except Exception as e:
                    self._log("全局热键回调异常：%s" % e)
            else:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        if self._registered:
            _user32.UnregisterHotKey(None, self._id)
            self._registered = False


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


_LOWLEVELHOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


def _mods_state():
    """当前按下的修饰键位掩码（与 RegisterHotKey 的 MOD_* 同义）。"""
    def down(vk):
        return _user32.GetAsyncKeyState(vk) & 0x8000

    mods = 0
    if down(VK_CONTROL):
        mods |= MOD_CONTROL
    if down(VK_MENU):
        mods |= MOD_ALT
    if down(VK_SHIFT):
        mods |= MOD_SHIFT
    if down(VK_LWIN) or down(VK_RWIN):
        mods |= MOD_WIN
    return mods


class LowLevelHook:
    """WH_KEYBOARD_LL 底层钩子：接管 RegisterHotKey 无法注册的 Win+ 组合键。

    同一钩子线程维护多组 (mods, vk) 监听，命中即吞掉该按键并触发回调。
    本应用退出（stop）后钩子卸载，系统恢复原快捷键行为。
    """

    def __init__(self, log=None):
        self._log = log or (lambda m: None)
        self._lock = threading.Lock()
        self._entries = []
        self._proc = None
        self._hook = None
        self._thread = None
        self._tid = 0
        self._stop = False
        self._ready = threading.Event()

    @property
    def active(self):
        return bool(self._hook and self._thread and self._thread.is_alive())

    def set_entries(self, entries):
        """entries: [(mods, vk, on_activate), ...]。整体替换后重建钩子。"""
        with self._lock:
            self._entries = list(entries)
            if not self._entries and self._thread:
                self._stop_hook()
            elif self._entries:
                self._ensure_running()

    def _ensure_running(self):
        if self.active:
            return
        self._stop = False
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="lowlevel-hotkey")
        self._thread.start()
        self._ready.wait(1.0)
        if not self.active:
            self._log("键盘钩子启动失败（Win+ 组合接管不可用）")

    def _stop_hook(self):
        self._stop = True
        if self._tid:
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def stop(self):
        with self._lock:
            self._entries = []
            self._stop_hook()

    def _run(self):
        self._tid = _kernel32.GetCurrentThreadId()

        def proc(nCode, wParam, lParam):
            if nCode >= 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kbd = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                vk = int(kbd.vkCode)
                if vk == VK_LWIN or vk == VK_RWIN:
                    return 0  # 让 Win 键本身的行为（开始菜单等）不受影响
                mods = _mods_state()
                cb = None
                with self._lock:
                    for m, v, c in self._entries:
                        if v == vk and m == mods:
                            cb = c
                            break
                if cb is not None:
                    threading.Thread(target=cb, daemon=True).start()
                    return 1  # 吞掉按键，阻止系统/其他程序处理
            return _user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

        self._proc = _LOWLEVELHOOKPROC(proc)
        _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        _user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, _LOWLEVELHOOKPROC, wintypes.HINSTANCE, wintypes.DWORD,
        ]
        _user32.SetWindowsHookExW.restype = wintypes.HHOOK
        _user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        _user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        _user32.CallNextHookEx.argtypes = [
            wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
        ]
        _user32.CallNextHookEx.restype = ctypes.c_ssize_t
        hook = _user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc,
            ctypes.windll.kernel32.GetModuleHandleW(None), 0)
        if not hook:
            self._ready.set()
            return
        self._hook = hook
        self._ready.set()
        msg = _MSG()
        while not self._stop:
            r = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None


class HotkeyManager:
    """多槽位热键管理：普通注册优先，含 Win 的组合冲突时自动转钩子接管。

    用法：
        mgr = HotkeyManager(log=...)
        status = mgr.set("clip_show", "Ctrl+Alt+V", on_activate)   # {combo, stolen}
        mgr.set("shot_region", "Ctrl+Alt+A", on_activate2)
        mgr.stop_all()
    """

    def __init__(self, log=None):
        self._log = log or (lambda m: None)
        self._lock = threading.Lock()
        self._slots = {}        # slot -> {"combo","mods","vk","stolen","obj","cb"}
        self._next_id = 0x4D01
        self._hook = LowLevelHook(log=log)

    def set(self, slot, combo_text, on_activate):
        """注册/更新某槽位的热键。失败抛 HotkeyError（格式或占用）。"""
        try:
            mods, vk, display = parse_combo(combo_text)
        except ValueError as e:
            raise HotkeyError(str(e))
        with self._lock:
            for s, info in self._slots.items():
                active = info["obj"] is not None or info.get("stolen")
                if s != slot and active and info["combo"] == display:
                    raise HotkeyError("%s 已被其他功能占用，请换一组快捷键" % display)
            old = self._slots.pop(slot, None)
            if old and old["obj"] is not None:
                try:
                    old["obj"].stop()
                except Exception:
                    pass
            hk = GlobalHotkey(hotkey_id=self._next_id, mods=mods, vk=vk,
                              desc=display, log=self._log)
            self._next_id += 1
            stolen = False
            hk.start(on_activate=on_activate)
            if not hk.active:
                if mods & MOD_WIN:
                    # 含 Win 且被占用（如系统剪贴板历史的 Win+V）：钩子接管
                    hk = None
                    stolen = True
                    self._log("热键 %s 被系统占用，已切换为键盘钩子接管" % display)
                else:
                    raise HotkeyError(
                        "%s 已被其他程序占用，请换一组快捷键" % display)
            self._slots[slot] = {
                "combo": display, "mods": mods, "vk": vk,
                "stolen": stolen, "obj": hk, "cb": on_activate,
            }
        self._sync_hook()
        return {"combo": display, "stolen": stolen}

    def unset(self, slot):
        with self._lock:
            old = self._slots.pop(slot, None)
            obj = old["obj"] if old else None
        if old and obj is not None:
            try:
                obj.stop()
            except Exception:
                pass
        if old:
            self._sync_hook()
        return bool(old)

    def _sync_hook(self):
        """把「钩子接管」槽位的组合同步给底层钩子（整体替换）。"""
        entries = []
        with self._lock:
            for info in self._slots.values():
                if info.get("stolen") and info["obj"] is None and info.get("cb"):
                    entries.append((info["mods"], info["vk"], info["cb"]))
        self._hook.set_entries(entries)

    def stop_all(self):
        with self._lock:
            slots = list(self._slots.values())
            self._slots.clear()
        for info in slots:
            obj = info.get("obj")
            if obj is not None:
                try:
                    obj.stop()
                except Exception:
                    pass
        try:
            self._hook.stop()
        except Exception:
            pass

    def stop(self):
        """别名：bridge.shutdown 兼容调用。"""
        self.stop_all()
