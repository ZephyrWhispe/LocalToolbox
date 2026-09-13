"""全局热键桥接：总开关 + 三个自定义槽位（剪贴板呼出 / 区域截图 / 全屏截图）。

槽位组合键存于 cfg（hotkey_clip / hotkey_shot / hotkey_full），支持
"Ctrl+Alt+V" 等常规组合；含 Win 的组合若被系统占用（如 Win+V 被系统剪贴板
历史占用），由内核自动转为键盘钩子接管（详见 app/core/hotkey.py），本应用
退出后系统快捷键恢复原样。
"""

import threading

from ..core.hotkey import HotkeyError, HotkeyManager

DEFAULT_CLIP = "Ctrl+Alt+V"
DEFAULT_SHOT = "Ctrl+Alt+A"
DEFAULT_FULL = "Ctrl+Alt+F"
DEFAULT_POP = "Win+V"
DEFAULT_MEMO = "Ctrl+Alt+M"
DEFAULT_OCR = "Ctrl+Alt+O"

_CFG_KEYS = {"clip": "hotkey_clip", "shot": "hotkey_shot",
             "full": "hotkey_full", "pop": "hotkey_pop",
             "memo": "hotkey_memo", "ocr": "hotkey_ocr"}
_SLOT_NAMES = {"clip": "剪贴板热键", "shot": "截图热键", "full": "全屏截图热键",
               "pop": "剪贴板弹窗热键", "memo": "备忘录热键", "ocr": "截图识字热键"}


class HotkeyApi:
    def _init_hotkey(self):
        self.hotkey = HotkeyManager(log=self.emit_log)
        if self.cfg.get("hotkey_enabled", True):
            self._apply_slots()

    # -- 动作 -----------------------------------------------------------
    def _show_window(self):
        """呼出主窗口（前台显示 + 还原）。窗口未就绪时静默跳过。"""
        if self._window is not None:
            try:
                self._window.show()
                self._window.restore()
                # 显式激活主窗口，确保正确聚焦
                try:
                    import ctypes
                    from ctypes import wintypes
                    native = getattr(self._window, "native", None)
                    hwnd = None
                    if native is not None:
                        hwnd = (getattr(native, "Handle", None)
                                or getattr(native, "hwnd", None)
                                or (native if isinstance(native, int) else None))
                    if hwnd:
                        user32 = ctypes.windll.user32
                        HWND_TOP = 0
                        SWP_NOSIZE = 0x0001
                        SWP_NOMOVE = 0x0002
                        user32.SetWindowPos(wintypes.HWND(hwnd), HWND_TOP,
                                            0, 0, 0, 0,
                                            SWP_NOSIZE | SWP_NOMOVE)
                        user32.SetForegroundWindow(wintypes.HWND(hwnd))
                except Exception:
                    pass
            except Exception:
                pass

    def _clip_activate(self):
        """任意应用内按下热键：显示主窗口并切到剪贴板历史页。"""
        try:
            self._show_window()
            if self._window is not None:
                self._window.evaluate_js("App.navigate('clipboard')")
        except Exception as e:
            self.emit_log("剪贴板热键呼出失败：%s" % e)

    def _shot_activate(self):
        """截图热键：显示主窗口并进入区域截图（前端 App.hotkeyAction 执行）。"""
        try:
            self._show_window()
            if self._window is not None:
                self._window.evaluate_js("App.hotkeyAction('shot')")
        except Exception as e:
            self.emit_log("截图热键呼出失败：%s" % e)

    def _full_activate(self):
        """全屏截图热键：无需呼出主窗口——隐藏窗口（避免把自己截进去）→
        稍候由前端直接捕获虚拟桌面并走 After Capture 任务链，完成后恢复窗口。"""
        def run():
            try:
                if self._window is not None:
                    self._window.hide()
                    self._shot_restore_pending = True
                    threading.Event().wait(0.2)
                if self._window is not None:
                    self._window.evaluate_js("App.hotkeyAction('full')")
            except Exception as e:
                self.emit_log("全屏截图热键失败：%s" % e)
        threading.Thread(target=run, daemon=True, name="hotkey-full").start()

    def _pop_activate(self):
        """剪贴板弹窗热键：记录之前的前台窗口 → 光标处唤起弹窗（类 Ditto）。"""
        try:
            if not hasattr(self, "_pop") or self._pop is None:
                self._init_clip_pop()
            if not (self.cfg.get("cliphist_enabled", False)):
                self._start_clip_monitor()
            import ctypes
            self._pop["prev_hwnd"] = int(
                ctypes.windll.user32.GetForegroundWindow() or 0)
            self.clip_pop_show()
        except Exception as e:
            self.emit_log("剪贴板弹窗呼出失败：%s" % e)

    def _memo_pop_activate(self):
        """备忘录热键：光标处唤起快速捕捉弹窗（独立于剪贴板弹窗的实例）。"""
        try:
            if not hasattr(self, "_memo_pop") or self._memo_pop is None:
                self._init_memo_pop()
            import ctypes
            self._memo_pop["prev_hwnd"] = int(
                ctypes.windll.user32.GetForegroundWindow() or 0)
            self.memo_pop_show()
        except Exception as e:
            self.emit_log("备忘录弹窗呼出失败：%s" % e)

    def _ocr_activate(self):
        """截图识字热键：显主窗并进入区域圈选（ocr 模式），识别后复制+弹窗。"""
        try:
            self._show_window()
            if self._window is not None:
                self._window.evaluate_js("App.hotkeyAction('ocr')")
        except Exception as e:
            self.emit_log("截图识字热键呼出失败：%s" % e)

    # -- 注册 -----------------------------------------------------------
    def _apply_slots(self):
        """按 cfg 注册全部槽位；单个失败仅记日志，不阻塞其余槽位。

        pop 槽位（默认 Win+V）注册彻底失败（RegisterHotKey + 低级钩子都不可用）
        时自动回退 Ctrl+Alt+V，保证弹窗始终有热键可达。
        """
        handlers = {"clip": self._clip_activate, "shot": self._shot_activate,
                    "full": self._full_activate, "pop": self._pop_activate,
                    "memo": self._memo_pop_activate, "ocr": self._ocr_activate}
        defaults = {"clip": DEFAULT_CLIP, "shot": DEFAULT_SHOT,
                    "full": DEFAULT_FULL, "pop": DEFAULT_POP,
                    "memo": DEFAULT_MEMO, "ocr": DEFAULT_OCR}
        for slot, cb in handlers.items():
            combo = str(self.cfg.get(_CFG_KEYS[slot]) or defaults[slot])
            try:
                self.hotkey.set(slot, combo, cb)
            except HotkeyError as e:
                if slot == "pop" and combo.upper() != "CTRL+ALT+V":
                    try:
                        self.hotkey.set(slot, "Ctrl+Alt+V", cb)
                        self.cfg.set(_CFG_KEYS[slot], "Ctrl+Alt+V")
                        self.emit_log("Win+V 不可用（%s），剪贴板弹窗热键已回退 Ctrl+Alt+V" % e)
                        continue
                    except HotkeyError:
                        pass
                self.emit_log("%s：%s（%s）" % (_SLOT_NAMES[slot], combo, e))

    def _current_combo(self, slot):
        key = _CFG_KEYS.get(slot)
        if key:
            return str(self.cfg.get(key) or "").strip()
        return ""

    def hotkey_set(self, slot, combo):
        """设置槽位热键（持久化 + 立即热注册）。

        slot: "clip" | "shot" | "full" | "pop" | "memo" | "ocr"；
        combo 如 "Ctrl+Alt+V" / "Win+V"。
        返回 {ok, combo, stolen}：stolen=True 表示系统占用后已转钩子接管。
        """
        key = _CFG_KEYS.get(str(slot))
        if not key:
            return {"ok": False, "err": "未知的热键槽位：%s" % slot}
        combo = str(combo or "").strip()
        if not combo:
            return {"ok": False, "err": "快捷键不能为空"}
        handlers = {"clip": self._clip_activate, "shot": self._shot_activate,
                    "full": self._full_activate, "pop": self._pop_activate,
                    "memo": self._memo_pop_activate, "ocr": self._ocr_activate}
        cb = handlers[slot]
        old_combo = self._current_combo(slot)
        try:
            status = self.hotkey.set(slot, combo, cb)
        except HotkeyError as e:
            # 注册失败时恢复旧组合，避免槽位空置
            if old_combo and old_combo != combo:
                try:
                    self.hotkey.set(slot, old_combo, cb)
                except HotkeyError:
                    self.emit_log("恢复旧热键 %s 失败" % old_combo)
            return {"ok": False, "err": str(e)}
        self.cfg.set(key, status["combo"])
        self.emit_log("%s 已设为 %s%s" % (
            _SLOT_NAMES[slot], status["combo"],
            "（钩子接管）" if status["stolen"] else ""))
        return {"ok": True, "data": status}

    def hotkey_set_enabled(self, value):
        """总开关：关闭停用全部槽位，开启按 cfg 重新注册。"""
        value = bool(value)
        self.cfg.set("hotkey_enabled", value)
        if value:
            self._apply_slots()
        else:
            self.hotkey.stop_all()
        return {"ok": True, "data": value}
