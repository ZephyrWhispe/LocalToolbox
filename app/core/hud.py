"""文件传输 HUD：无边框置顶悬浮小窗，显示进行中任务进度。

实现：tkinter 在独立守护线程初始化（pywebview 主窗口不受影响）；
UI 只能在该线程操作，故更新指令经队列 + root.after 轮询消费。
不可用环境（tkinter 初始化失败）静默降级为 None，不影响传输主流程。
"""

import threading


class HudController:
    """进度悬浮窗控制器。start() 后即可跨线程 update()。"""

    def __init__(self, log=None):
        self._log = log or (lambda m: None)
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._queue = []
        self._stop = False
        self._thread = None
        self._root = None
        self._label = None

    @property
    def available(self):
        return self._root is not None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop = False
        self._queue.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="xfer-hud")
        self._thread.start()
        self._ready.wait(3.0)

    def stop(self):
        with self._lock:
            self._stop = True
            self._queue.append([])  # 触发一次渲染（隐藏），随后线程退出
        if self._thread:
            self._thread.join(timeout=2)

    def update(self, tasks):
        """任何线程可调用：替换 HUD 显示的任务列表（空列表 = 隐藏）。"""
        with self._lock:
            self._queue.append(list(tasks))
            if len(self._queue) > 5:
                self._queue = self._queue[-1:]

    # -- tkinter 线程 --------------------------------------------------
    def _run(self):
        try:
            import tkinter as tk

            root = tk.Tk()
        except Exception as e:
            self._log("进度悬浮窗初始化失败，已禁用：%s" % e)
            self._ready.set()
            return
        try:
            root.withdraw()
            root.overrideredirect(True)  # 无边框
            root.attributes("-topmost", True)  # 置顶
            root.attributes("-alpha", 0.92)  # 半透明
            label = tk.Label(
                root,
                justify="left",
                anchor="w",
                padx=12,
                pady=8,
                bg="#161b22",
                fg="#e6edf3",
                font=("Microsoft YaHei UI", 9),
                bd=1,
                relief="solid",
                highlightbackground="#39424e",
                highlightthickness=1,
            )
            label.pack()
        except Exception as e:
            self._log("进度悬浮窗初始化失败，已禁用：%s" % e)
            self._ready.set()
            return
        self._root = root
        self._label = label
        self._ready.set()
        root.after(150, self._poll)
        root.mainloop()
        # mainloop 已退出：必须在 tkinter 线程内销毁窗口并解除跨线程引用，
        # 否则自引用残留在主线程 GC 时析构 Tcl 对象 → "Tcl_AsyncDelete: wrong thread" 崩溃
        try:
            label.destroy()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass
        self._label = None
        self._root = None

    def _poll(self):
        root = self._root
        if root is None:
            return
        tasks = None
        with self._lock:
            stop = self._stop
            if self._queue:
                tasks = self._queue.pop(0)
        if stop:
            try:
                root.destroy()
            except Exception:
                pass
            return
        if tasks is not None:
            self._render(tasks, root)
        root.after(150, self._poll)

    def _render(self, tasks, root):
        try:
            if not tasks:
                if root.winfo_viewable():
                    root.withdraw()
                return
            lines = []
            for t in tasks:
                total = t.get("total") or 0
                done = t.get("done") or 0
                pct = min(100, int(done * 100 / total)) if total > 0 else 0
                name = t.get("current") or "…"
                speed = t.get("speed") or 0
                lines.append(
                    "%s · %s\n  %d%%  %s/%s%s"
                    % (
                        t.get("peer", "?"),
                        name,
                        pct,
                        self._fmt(done),
                        self._fmt(total),
                        ("  %s/s" % self._fmt(speed)) if speed > 0 else "",
                    )
                )
            self._label.config(
                text="传输中\n" + "\n".join(lines),
                width=max(len(max(lines, key=len)), 8),
            )
            root.update_idletasks()
            w = root.winfo_reqwidth()
            h = root.winfo_reqheight()
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            root.geometry("+%d+%d" % (max(sw - w - 24, 0), max(sh - h - 80, 0)))
            if not root.winfo_viewable():
                root.deiconify()
        except Exception:
            pass

    @staticmethod
    def _fmt(n):
        n = float(n or 0)
        if n >= 1 << 30:
            return "%.2f GiB" % (n / (1 << 30))
        if n >= 1 << 20:
            return "%.1f MiB" % (n / (1 << 20))
        if n >= 1 << 10:
            return "%.0f KiB" % (n / (1 << 10))
        return "%d B" % int(n)