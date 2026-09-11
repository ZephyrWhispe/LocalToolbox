"""剪贴板弹窗（Win+V，类 Ditto）桥接层。

单例常驻 pywebview 第二窗口（懒建窗、光标定位、失焦自动隐藏），与截图
遮罩窗口同一套机制（js_api 共享整桥、_raise_overlay_window 置顶）。
粘贴编排：写回剪贴板 → 隐藏弹窗 → SendInput 模拟 Ctrl+V 到之前的前台窗口
（app/core/clip_paste.py），自动粘贴可在设置中关闭（关闭则仅复制）。

列表 / 删除 / 置顶 / 缩略图复用 cliphist_* 现有方法（js_api=self 整桥暴露）。
"""

import base64
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes

import webview

from .base import BridgeBase

POP_W = 420
POP_H = 560
MONITOR_DEFAULTTONEAREST = 2
# 项目约定：所有子进程调用一律抑制控制台窗口（explorer 虽为 GUI 程序，仍统一处理）
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ClipPopApi(BridgeBase):
    def _init_clip_pop(self):
        self._pop = {"win": None, "visible": False, "prev_hwnd": 0,
                     "ever_active": False, "wd_stop": None}

    # -- 窗口生命周期 --------------------------------------------------------
    def _pop_ui_base(self):
        if getattr(sys, "frozen", False):
            return sys._MEIPASS
        return os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))

    def _build_pop_html(self):
        base = self._pop_ui_base()
        with open(os.path.join(base, "webui", "clip_pop.html"),
                  "r", encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(base, "webui", "js", "clip_pop.js"),
                  "r", encoding="utf-8") as f:
            js = f.read()
        cfg = json.dumps({
            "theme": str(self.cfg.get("theme", "dark")),
            "accent": str(self.cfg.get("accent_color", "") or ""),
            "autopaste": bool(self.cfg.get("clip_pop_autopaste", True)),
        }, ensure_ascii=False)
        return html.replace("/*__POP_JS__*/", js).replace(
            "/*__CFG_JSON__*/", cfg)

    def _pop_build(self):
        if self._pop.get("win"):
            return True
        dark = str(self.cfg.get("theme", "dark")) != "light"
        try:
            win = webview.create_window(
                "", html=self._build_pop_html(), frameless=True, shadow=False,
                easy_drag=False, js_api=self, width=POP_W, height=POP_H,
                hidden=True, background_color="#0f1218" if dark else "#f2f4f9",
                on_top=True)
        except TypeError:   # 旧版 pywebview 无 on_top 参数
            win = webview.create_window(
                "", html=self._build_pop_html(), frameless=True, shadow=False,
                easy_drag=False, js_api=self, width=POP_W, height=POP_H,
                hidden=True, background_color="#0f1218" if dark else "#f2f4f9")
        if win is None:
            raise RuntimeError("弹窗窗口创建失败")
        self._pop["win"] = win
        try:
            win.events.closed += self._on_pop_closed
        except Exception:
            pass
        return True

    def _on_pop_closed(self):
        """用户/系统直接关闭弹窗窗口（非 hide 流程）→ 清状态。"""
        self._pop["win"] = None
        self._pop["visible"] = False
        self._pop_stop_watchdog()

    def _pop_hwnd(self) -> int:
        native = getattr(self._pop.get("win"), "native", None)
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

    def _pop_position_at_cursor(self):
        """光标处弹出并钳位到所在显示器工作区（多显示器 / 任务栏安全）。"""
        win = self._pop.get("win")
        if not win:
            return
        from ..core import screen as screen_mod
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

    def _pop_stop_watchdog(self):
        ev = self._pop.get("wd_stop")
        if ev:
            ev.set()
        self._pop["wd_stop"] = None

    def _pop_raise(self):
        """置顶 + 强制抢前台（Ditto 同款三件套：ALT 解锁 / AttachThreadInput /
        SetForegroundWindow 重试循环）。

        Win+V 由低级钩子捕获，本进程并非前台——Windows 前台锁会拒绝
        SetForegroundWindow，必须用 ALT 注入或 AttachThreadInput 解锁，
        否则弹窗可见但键盘焦点仍在原窗口，输入无效且 ever_active 不置位。
        """
        win = self._pop.get("win")
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
            hwnd = self._pop_hwnd()
            if not hwnd or not self._pop.get("visible"):
                return
            if int(user32.GetForegroundWindow() or 0) == hwnd:
                self._pop["ever_active"] = True
                return
            try:
                user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-1),
                                    0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
                if attempt >= 1:
                    fg = user32.GetForegroundWindow()
                    ftid = user32.GetWindowThreadProcessId(fg, None)
                    if ftid and ftid != cur_tid:
                        user32.AttachThreadInput(cur_tid, ftid, True)
                # ALT 注入：解锁 SetForegroundWindow 的前台限制
                user32.keybd_event(wintypes.BYTE(0x12), 0, 0, 0)
                user32.keybd_event(wintypes.BYTE(0x12), 0, 0x0002, 0)
                user32.SetForegroundWindow(wintypes.HWND(hwnd))
                if attempt >= 1:
                    user32.AttachThreadInput(cur_tid, ftid, False)
            except Exception:
                pass
            time.sleep(0.2)

    def _pop_watchdog(self, stop):
        """失焦自动隐藏（ever_active 门控）：
        - 弹窗曾到过前台、之后失焦 → 隐藏（用户点了别处）；
        - 从未抢到前台 → **不自动隐藏**（窗口 TOPMOST 可见，鼠标点击即可
          激活使用；自动隐藏反而让用户面对"闪现即逝"无路可走）。"""
        while not stop.wait(0.25):
            if not self._pop.get("visible"):
                break
            hwnd = self._pop_hwnd()
            if not hwnd:
                break
            fg = int(ctypes.windll.user32.GetForegroundWindow() or 0) == hwnd
            if fg:
                self._pop["ever_active"] = True
                continue
            if self._pop.get("ever_active"):
                try:
                    self.clip_pop_hide()
                except Exception:
                    pass
                break

    # -- 显示 / 隐藏 ----------------------------------------------------------
    def clip_pop_show(self):
        self._pop_build()
        self._pop_position_at_cursor()
        win = self._pop.get("win")
        if not win:
            return {"ok": False, "err": "弹窗窗口未就绪"}
        win.show()
        self._pop["visible"] = True
        self._pop["ever_active"] = False
        self._pop_stop_watchdog()
        stop = threading.Event()
        self._pop["wd_stop"] = stop
        threading.Thread(target=self._pop_watchdog, args=(stop,),
                         daemon=True, name="clip-pop-watchdog").start()
        threading.Thread(target=self._pop_raise, daemon=True,
                         name="clip-pop-raise").start()
        data = {"theme": str(self.cfg.get("theme", "dark")),
                "accent": str(self.cfg.get("accent_color", "") or ""),
                "autopaste": bool(self.cfg.get("clip_pop_autopaste", True))}
        try:
            win.evaluate_js("window.ClipPop && ClipPop.onShow(%s)" %
                            json.dumps(data, ensure_ascii=False))
        except Exception:
            pass
        return {"ok": True, "data": data}

    def clip_pop_hide(self):
        self._pop["visible"] = False
        self._pop_stop_watchdog()
        win = self._pop.get("win")
        if win:
            try:
                win.hide()
            except Exception:
                pass
        return {"ok": True}

    def _pop_push(self, script):
        """弹窗内直推 JS（emit 只到主窗口，弹窗须直呼 evaluate_js）。"""
        win = self._pop.get("win")
        if not win:
            return
        try:
            win.evaluate_js(script)
        except Exception:
            pass

    def _pop_toast(self, msg, kind="ok"):
        self._pop_push("window.ClipPop && ClipPop.toast(%s, %r)" %
                       (json.dumps(msg, ensure_ascii=False), kind))

    # -- 粘贴编排 --------------------------------------------------------------
    def _autopaste(self, autopaste):
        if autopaste is None:
            return bool(self.cfg.get("clip_pop_autopaste", True))
        return bool(autopaste)

    def _finish_paste(self, autopaste):
        """写回剪贴板完成后的收尾：隐藏弹窗；autopaste 开启时向前台注入 Ctrl+V。"""
        autopaste = self._autopaste(autopaste)
        prev = int(self._pop.get("prev_hwnd") or 0)
        self.clip_pop_hide()
        if not autopaste:
            self._pop_toast("已复制到剪贴板，可在原窗口 Ctrl+V 粘贴")
            return {"pasted": False, "err": ""}
        if not prev:
            self._pop_toast("未记录原前台窗口，内容已复制到剪贴板", "warn")
            return {"pasted": False, "err": "未记录原前台窗口"}
        time.sleep(0.12)
        from ..core.clip_paste import send_paste
        if send_paste(prev):
            return {"pasted": True, "err": ""}
        self._pop_toast("无法切换回原窗口（可能为管理员程序），内容已复制", "warn")
        return {"pasted": False, "err": "无法切换回原窗口（可能为管理员程序），内容已复制"}

    def clip_pop_paste(self, entry_id, plain=False, no_newline=False,
                       autopaste=None):
        """弹窗内选中条目：写回剪贴板（可选纯文本/去换行）+ Ditto 式自动粘贴。"""
        if not self._clip_monitor:
            return {"ok": False, "err": "剪贴板监控未启动"}
        entry = self._clip_monitor.get_entry(int(entry_id))
        if not entry:
            return {"ok": False, "err": "条目不存在"}
        if plain or no_newline:
            if entry["kind"] != "text" or not entry["text"]:
                return {"ok": False, "err": "图片条目不支持特殊粘贴"}
            text = entry["text"]
            if no_newline:
                text = text.replace("\r\n", "").replace("\n", "").replace("\r", "")
            import pyperclip
            pyperclip.copy(text)
        elif not self._clip_monitor.paste_to_clipboard(int(entry_id)):
            return {"ok": False, "err": "写入剪贴板失败"}
        fin = self._finish_paste(autopaste)
        if fin["err"]:
            self._pop_toast(fin["err"], "warn")
        return {"ok": True, "data": fin}

    def clip_pop_multi_paste(self, ids, autopaste=None):
        """多选合并粘贴：多条文本按选择顺序换行拼接后写回 + 粘贴。"""
        if not self._clip_monitor:
            return {"ok": False, "err": "剪贴板监控未启动"}
        id_list = [int(i) for i in (ids or [])]
        if not id_list:
            return {"ok": False, "err": "未选择条目"}
        texts = []
        for i in id_list:
            e = self._clip_monitor.get_entry(i)
            if not e:
                continue
            if e["kind"] != "text" or not e["text"]:
                return {"ok": False, "err": "图片条目不支持合并粘贴"}
            texts.append(e["text"])
        if not texts:
            return {"ok": False, "err": "条目不存在"}
        import pyperclip
        pyperclip.copy("\n".join(texts))
        fin = self._finish_paste(autopaste)
        if fin["err"]:
            self._pop_toast(fin["err"], "warn")
        return {"ok": True, "data": fin}

    def clip_pop_get_entry(self, entry_id):
        """弹窗预览面板：单条全文 / 图片 data_url。"""
        if not self._clip_monitor:
            return {"ok": False, "err": "剪贴板监控未启动"}
        e = self._clip_monitor.get_entry(int(entry_id))
        if not e:
            return {"ok": False, "err": "条目不存在"}
        data = {"id": e["id"], "kind": e["kind"], "text": e["text"], "ts": e["ts"],
                "pinned": e["pinned"]}
        if e["kind"] == "image":
            png = self._clip_monitor.get_image_data(int(entry_id))
            if png:
                data["data_url"] = "data:image/png;base64," + \
                    base64.b64encode(png).decode()
        return {"ok": True, "data": data}

    def clip_pop_reveal(self, entry_id):
        """右键菜单「打开路径」：图片条目在资源管理器中定位文件。"""
        if not self._clip_monitor:
            return {"ok": False, "err": "剪贴板监控未启动"}
        e = self._clip_monitor.get_entry(int(entry_id))
        if not e or e["kind"] != "image" or not e["img_path"]:
            return {"ok": False, "err": "该条目没有关联文件"}
        p = e["img_path"]
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(p)],
                             creationflags=_NOWIN)
        else:
            subprocess.Popen(["open", os.path.dirname(p)], creationflags=_NOWIN)
        return {"ok": True}


def _work_area_at(x, y):
    """点所在显示器的工作区（排除任务栏）(left, top, right, bottom)；失败 None。"""
    try:
        user32 = ctypes.windll.user32
        pt = wintypes.POINT(x, y)
        MONITOR_DEFAULTTONEAREST = 2
        hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return None
        rc = mi.rcWork
        return rc.left, rc.top, rc.right, rc.bottom
    except Exception:
        return None
