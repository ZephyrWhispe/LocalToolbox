"""备忘录快速捕捉弹窗（Ctrl+Alt+M）桥接层。

复用剪贴板弹窗（clip_pop_api.py）的设计：单例常驻第二窗口（懒建窗、光标
定位、失焦自动隐藏 ever_active 门控、抢前台三件套），独立状态 dict 与
watchdog 线程。注意：与 ClipPopApi 同挂一个 Bridge，内部方法一律使用
``_mp_`` 前缀避免 MRO 方法名冲突（v5.0 探针教训——同名 `_pop_build` 会
静默调到 ClipPopApi 的实现，建出剪贴板弹窗）。js_api=self 共享整桥，弹窗
内可直调 memo_* 全部方法；向弹窗推 UI 必须 evaluate_js 直推（emit 只到主窗）。
"""

import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes

import webview

from .base import BridgeBase

POP_W = 440
POP_H = 420


class MemoPopApi(BridgeBase):
    def _init_memo_pop(self):
        self._memo_pop = {"win": None, "visible": False, "prev_hwnd": 0,
                          "ever_active": False, "wd_stop": None}

    # -- 窗口生命周期 --------------------------------------------------------
    def _mp_ui_base(self):
        if getattr(sys, "frozen", False):
            return sys._MEIPASS
        return os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))

    def _mp_build_html(self):
        base = self._mp_ui_base()
        with open(os.path.join(base, "webui", "memo_pop.html"),
                  "r", encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(base, "webui", "js", "memo_pop.js"),
                  "r", encoding="utf-8") as f:
            js = f.read()
        cfg = json.dumps({
            "theme": str(self.cfg.get("theme", "dark")),
            "accent": str(self.cfg.get("accent_color", "") or ""),
        }, ensure_ascii=False)
        return html.replace("/*__POP_JS__*/", js).replace(
            "/*__CFG_JSON__*/", cfg)

    def _mp_build(self):
        if self._memo_pop.get("win"):
            return True
        dark = str(self.cfg.get("theme", "dark")) != "light"
        try:
            win = webview.create_window(
                "", html=self._mp_build_html(), frameless=True, shadow=False,
                easy_drag=False, js_api=self, width=POP_W, height=POP_H,
                hidden=True, background_color="#0f1218" if dark else "#f2f4f9",
                on_top=True)
        except TypeError:   # 旧版 pywebview 无 on_top 参数
            win = webview.create_window(
                "", html=self._mp_build_html(), frameless=True, shadow=False,
                easy_drag=False, js_api=self, width=POP_W, height=POP_H,
                hidden=True, background_color="#0f1218" if dark else "#f2f4f9")
        if win is None:
            raise RuntimeError("备忘录弹窗创建失败")
        self._memo_pop["win"] = win
        try:
            win.events.closed += self._on_mp_closed
        except Exception:
            pass
        return True

    def _on_mp_closed(self):
        self._memo_pop["win"] = None
        self._memo_pop["visible"] = False
        self._mp_stop_watchdog()

    def _mp_hwnd(self) -> int:
        native = getattr(self._memo_pop.get("win"), "native", None)
        if native is None:
            return 0
        h = (getattr(native, "Handle", None) or getattr(native, "hwnd", None)
             or (native if isinstance(native, int) else None))
        if h is None:
            return 0
        try:
            return int(h) if isinstance(h, int) else int(h.ToInt64())
        except Exception:
            return 0

    def _mp_position_at_cursor(self):
        win = self._memo_pop.get("win")
        if not win:
            return
        from ..core import screen as screen_mod
        from .clip_pop_api import _work_area_at
        pt = screen_mod.cursor_point() or (0, 0)
        x, y = pt[0] + 12, pt[1] + 12
        wa = _work_area_at(pt[0], pt[1])
        if wa:
            wl, wt, wr, wb = wa
            x = min(max(x, wl), max(wl, wr - POP_W))
            y = min(max(y, wt), max(wt, wb - POP_H))
        try:
            win.move(x, y)
        except Exception:
            pass

    def _mp_stop_watchdog(self):
        ev = self._memo_pop.get("wd_stop")
        if ev:
            ev.set()
        self._memo_pop["wd_stop"] = None

    def _mp_raise(self):
        """置顶 + 抢前台（同 clip_pop 三件套：ALT 解锁 / AttachThreadInput / 重试）。"""
        win = self._memo_pop.get("win")
        if not win:
            return
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPVOID]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                             wintypes.BOOL]
        user32.AttachThreadInput.restype = wintypes.BOOL
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        cur_tid = kernel32.GetCurrentThreadId()
        for attempt in range(15):
            hwnd = self._mp_hwnd()
            if not hwnd or not self._memo_pop.get("visible"):
                return
            if int(user32.GetForegroundWindow() or 0) == hwnd:
                self._memo_pop["ever_active"] = True
                return
            try:
                user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-1),
                                    0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
                if attempt >= 1:
                    fg = user32.GetForegroundWindow()
                    ftid = user32.GetWindowThreadProcessId(fg, None)
                    if ftid and ftid != cur_tid:
                        user32.AttachThreadInput(cur_tid, ftid, True)
                user32.keybd_event(wintypes.BYTE(0x12), 0, 0, 0)
                user32.keybd_event(wintypes.BYTE(0x12), 0, 0x0002, 0)
                user32.SetForegroundWindow(wintypes.HWND(hwnd))
                if attempt >= 1:
                    user32.AttachThreadInput(cur_tid, ftid, False)
            except Exception:
                pass
            time.sleep(0.2)

    def _mp_watchdog(self, stop):
        """失焦自动隐藏（ever_active 门控，同 clip_pop）。"""
        while not stop.wait(0.25):
            if not self._memo_pop.get("visible"):
                break
            hwnd = self._mp_hwnd()
            if not hwnd:
                break
            fg = int(ctypes.windll.user32.GetForegroundWindow() or 0) == hwnd
            if fg:
                self._memo_pop["ever_active"] = True
                continue
            if self._memo_pop.get("ever_active"):
                try:
                    self.memo_pop_hide()
                except Exception:
                    pass
                break

    # -- 显示 / 隐藏 ----------------------------------------------------------
    def memo_pop_show(self):
        self._mp_build()
        self._mp_position_at_cursor()
        win = self._memo_pop.get("win")
        if not win:
            return {"ok": False, "err": "弹窗窗口未就绪"}
        win.show()
        self._memo_pop["visible"] = True
        self._memo_pop["ever_active"] = False
        self._mp_stop_watchdog()
        stop = threading.Event()
        self._memo_pop["wd_stop"] = stop
        threading.Thread(target=self._mp_watchdog, args=(stop,),
                         daemon=True, name="memo-pop-watchdog").start()
        threading.Thread(target=self._mp_raise, daemon=True,
                         name="memo-pop-raise").start()
        data = {"theme": str(self.cfg.get("theme", "dark")),
                "accent": str(self.cfg.get("accent_color", "") or "")}
        try:
            win.evaluate_js("window.MemoPop && MemoPop.onShow(%s)" %
                            json.dumps(data, ensure_ascii=False))
        except Exception:
            pass
        return {"ok": True, "data": data}

    def memo_pop_hide(self):
        self._memo_pop["visible"] = False
        self._mp_stop_watchdog()
        win = self._memo_pop.get("win")
        if win:
            try:
                win.hide()
            except Exception:
                pass
        return {"ok": True}

    def _mp_push(self, script):
        win = self._memo_pop.get("win")
        if not win:
            return
        try:
            win.evaluate_js(script)
        except Exception:
            pass

    def _mp_toast(self, msg, kind="ok"):
        self._mp_push("window.MemoPop && MemoPop.toast(%s, %r)" %
                      (json.dumps(msg, ensure_ascii=False), kind))

    # -- 弹窗数据 / 快存 ------------------------------------------------------
    def memo_pop_data(self, query=""):
        """弹窗数据：分组（含上次分组）+ 按关键词的最近备忘（前 50 条预览）。"""
        try:
            q = str(query or "").strip()
            groups = self._memos.groups()
            memos = self._memos.list(q, None)[:50]
            return {"ok": True, "data": {
                "groups": groups,
                "memos": memos,
                "last_group": int(self.cfg.get("memo_pop_group_last", 0) or 0),
            }}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def memo_pop_save(self, text, group_id=None):
        """快速保存：首行为标题；成功后清空输入并刷新列表。"""
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "err": "内容为空"}
        try:
            gid = int(group_id) if group_id else None
            mid = self._memos.add(text, gid)
            if gid:
                self.cfg.set("memo_pop_group_last", int(gid))
            self._mp_toast("已保存")
            self._mp_push("window.MemoPop && MemoPop.onSaved()")
            return {"ok": True, "data": {"id": mid}}
        except Exception as e:
            return {"ok": False, "err": "保存失败：%s" % e}

    def memo_pop_open_main(self, query=""):
        """隐藏弹窗 → 唤起主窗口 → 跳转备忘录页（带搜索词）。"""
        self.memo_pop_hide()
        try:
            if self._window is not None:
                self._window.show()
                self._window.restore()
                self._window.evaluate_js(
                    "App.navigate('memo')"
                    + ("; App.setMemoSearch(%s)" % json.dumps(
                        str(query or ""), ensure_ascii=False)
                       if str(query or "").strip() else ""))
        except Exception as e:
            self.emit_log("打开备忘录页失败：%s" % e)
        return {"ok": True}
