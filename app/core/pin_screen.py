"""贴图核心：将图片钉在屏幕最顶层（tkinter Toplevel）。

所有贴图共享同一个隐藏 Tk root，每个贴图是一个 Toplevel 窗口，
避免多个 Tk mainloop 线程导致崩溃。
"""

import io
import threading
import tkinter as tk
from tkinter import Menu

from PIL import Image, ImageTk


class PinManager:
    """单 Tk root 管理器：所有 PinnedImage 共享同一 mainloop 线程。"""

    def __init__(self):
        self._pins = {}
        self._lock = threading.Lock()
        self._root = None
        self._thread = None
        self._ready = threading.Event()

    def _ensure_root(self):
        if self._root is not None:
            return
        self._thread = threading.Thread(target=self._run_root, daemon=True,
                                        name="pin-root")
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run_root(self):
        try:
            self._root = tk.Tk()
            self._root.withdraw()
            self._root.title("PinHost")
            self._root.configure(bg="black")
            self._ready.set()
            self._root.mainloop()
        except Exception:
            self._ready.set()

    def pin(self, png_bytes, opacity=1.0):
        self._ensure_root()
        pid = id(png_bytes) & 0x7FFFFFFF
        top = tk.Toplevel(self._root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.attributes("-alpha", opacity)
        top.configure(bg="black")

        img = Image.open(io.BytesIO(png_bytes))
        max_w, max_h = 800, 600
        ratio = min(max_w / img.width, max_h / img.height, 1.0)
        if ratio < 1.0:
            img = img.resize((int(img.width * ratio), int(img.height * ratio)),
                             Image.LANCZOS)
        tk_img = ImageTk.PhotoImage(img)
        label = tk.Label(top, image=tk_img, bd=0, highlightthickness=0)
        label.pack()

        state = {"tk_img": tk_img, "opacity": opacity, "png_bytes": png_bytes,
                 "id": pid, "click_through": False}

        def on_press(e):
            state["sx"] = e.x_root - top.winfo_x()
            state["sy"] = e.y_root - top.winfo_y()

        def on_drag(e):
            top.geometry("+%d+%d" % (e.x_root - state["sx"], e.y_root - state["sy"]))

        def on_scroll(e):
            delta = 0.05 if e.delta > 0 else -0.05
            state["opacity"] = max(0.1, min(1.0, state["opacity"] + delta))
            top.attributes("-alpha", state["opacity"])

        def on_right_click(e):
            menu = Menu(top, tearoff=0)
            menu.add_command(label="保存", command=lambda: _save_as(state))
            menu.add_command(label="复制到剪贴板", command=lambda: _copy_clipboard(state))
            menu.add_separator()
            op_menu = Menu(menu, tearoff=0)
            for pct in (100, 80, 60, 40, 20):
                op_menu.add_command(
                    label="%d%%" % pct,
                    command=lambda p=pct: _set_opacity(state, top, p / 100.0),
                )
            menu.add_cascade(label="透明度", menu=op_menu)
            ct_var = tk.BooleanVar(value=state["click_through"])
            menu.add_checkbutton(
                label="点击穿透",
                command=lambda: _toggle_click_through(state, top, ct_var),
                variable=ct_var,
            )
            menu.add_separator()
            menu.add_command(label="关闭", command=lambda: self.close(pid))
            try:
                menu.tk_popup(e.x_root, e.y_root)
            finally:
                menu.grab_release()

        label.bind("<ButtonPress-1>", on_press)
        label.bind("<B1-Motion>", on_drag)
        label.bind("<Button-3>", on_right_click)
        label.bind("<MouseWheel>", on_scroll)

        top.geometry("+100+100")

        with self._lock:
            self._pins[pid] = {"top": top, "state": state}
        return pid

    def close(self, pin_id):
        with self._lock:
            entry = self._pins.pop(pin_id, None)
        if entry:
            try:
                entry["top"].destroy()
            except Exception:
                pass

    def close_all(self):
        with self._lock:
            entries = list(self._pins.values())
            self._pins.clear()
        for entry in entries:
            try:
                entry["top"].destroy()
            except Exception:
                pass

    def list_pins(self):
        with self._lock:
            return [{"id": pid, "opacity": e["state"]["opacity"]}
                    for pid, e in self._pins.items()]

    def shutdown(self):
        self.close_all()
        if self._root:
            try:
                self._root.after(0, self._root.quit)
            except Exception:
                pass


def _set_opacity(state, top, alpha):
    state["opacity"] = alpha
    top.attributes("-alpha", alpha)


def _save_as(state):
    from tkinter import filedialog
    path = filedialog.asksaveasfilename(
        defaultextension=".png",
        filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("所有文件", "*.*")],
    )
    if path:
        with open(path, "wb") as f:
            f.write(state["png_bytes"])


def _copy_clipboard(state):
    try:
        from ..core.screenshot import copy_png_to_clipboard
        copy_png_to_clipboard(state["png_bytes"])
    except Exception:
        pass


def _toggle_click_through(state, top, var):
    state["click_through"] = not state["click_through"]
    import ctypes
    try:
        hwnd = ctypes.windll.user32.GetParent(
            ctypes.windll.user32.GetParent(top.winfo_id()))
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x80000
        WS_EX_TRANSPARENT = 0x20
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if state["click_through"]:
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass
