"""屏幕取色器：全屏放大预览 + 实时通道值，锁定复制十六进制颜色。

实现：tkinter 无边框置顶全屏窗口（在调用线程直接运行 mainloop，阻塞直至
用户锁定或取消；js_api 自带独立线程，不会卡 UI 主流程）。屏幕取点用
Pillow ImageGrab 拉取指针周围小区域并最近邻放大，避免直接操作像素格式。
"""

import io
import threading

from PIL import Image, ImageGrab

ZOOM = 9  # 放大倍数
_SIDE = 17  # 采样区域边长（奇数，含中心点在中央）
_SIZE = _SIDE * ZOOM  # 放大后显示边长像素
_SAMPLE_MS = 30  # 采样间隔


def rgb_to_hex(r, g, b):
    """(r,g,b) → "#RRGGBB"（各通道 0-255，钳位）。"""
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    return "#%02X%02X%02X" % (r, g, b)


class PickPersonalPickle:
    pass


class PickColor:
    """屏幕取色器。run_blocking() 返回 (r,g,b,hex) 或 None（取消）。"""

    def __init__(self, log=None):
        self._log = log or (lambda m: None)

    def run_blocking(self, copy_to_clipboard=True):
        """阻塞运行取色器直到锁定/取消。

        返回 {"rgb":[r,g,b],"hex":"#RRGGBB"}；取消返回 None。
        copy_to_clipboard：锁定后是否写入系统剪贴板（十六进制）。
        """
        try:
            import tkinter as tk
        except Exception:
            return None
        state = {"result": None, "capture": None}
        try:
            root = tk.Tk()
            root.withdraw()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.9)
            root.configure(bg="#000000")
        except Exception as e:
            self._log("取色器窗口初始化失败：%s" % e)
            return None

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        root.geometry("%dx%d+0+0" % (sw, sh))
        # 中央放大画布（左上角小窗展示）
        preview = tk.Canvas(
            root, width=_SIZE, height=_SIZE,
            bg="#111111", highlightthickness=0)
        preview.place(x=_SIZE // 4, y=_SIZE // 4)
        info = tk.Label(
            root, text="", justify="left", anchor="w",
            bg="#161b22", fg="#e6edf3",
            font=("Consolas", 13), padx=10, pady=6,
        )
        info.place(x=0, y=sh - 64)
        hint = tk.Label(
            root, text="左键/空格/回车 锁定并复制 · ESC/右键 取消",
            bg="#161b22", fg="#8b949e",
            font=("Microsoft YaHei UI", 10), padx=10, pady=4,
        )
        hint.place(x=0, y=sh - 100)

        def sample():
            try:
                x = root.winfo_pointerx()
                y = root.winfo_pointery()
                x1, y1 = max(x - _SIDE // 2, 0), max(y - _SIDE // 2, 0)
                img = ImageGrab.grab(bbox=(x1, y1, x1 + _SIDE, y1 + _SIDE))
                px = img.getpixel((min(_SIDE // 2, img.width - 1),
                                   min(_SIDE // 2, img.height - 1)))[:3]
                zoom = img.resize((_SIZE, _SIZE), Image.NEAREST)
                img_tk = _photo(zoom)
                state["capture"] = img_tk
                preview.delete("all")
                preview.create_image(0, 0, anchor="nw", image=img_tk)
                # 十字准线
                mid = _SIZE / 2
                preview.create_line(mid, 0, mid, _SIZE, fill="#ffffff", dash=(2, 2))
                preview.create_line(0, mid, _SIZE, mid, fill="#ffffff", dash=(2, 2))
                hexc = rgb_to_hex(*px)
                info.config(
                    text="R %3d   G %3d   B %3d\n%s    %s"
                         % (px[0], px[1], px[2], hexc, _xy(x, y)))
            except Exception:
                pass
            root.after(_SAMPLE_MS, sample)

        def done_copy(rgb, hexc):
            state["result"] = {"rgb": [rgb[0], rgb[1], rgb[2]], "hex": hexc}
            if copy_to_clipboard:
                try:
                    import pyperclip

                    pyperclip.copy(hexc)
                except Exception:
                    pass
            root.destroy()

        def on_left(_e=None):
            try:
                x = root.winfo_pointerx()
                y = root.winfo_pointery()
                img = ImageGrab.grab(bbox=(x, y, x + 1, y + 1))
                rgb = img.getpixel((0, 0))[:3]
            except Exception:
                rgb = (0, 0, 0)
            done_copy(rgb, rgb_to_hex(*rgb))

        def on_escape(_e=None):
            root.destroy()

        root.bind("<KeyPress-space>", on_left)
        root.bind("<KeyPress-Return>", on_left)
        root.bind("<KeyPress-Escape>", on_escape)
        root.bind("<Button-1>", on_left)
        root.bind("<Button-3>", on_escape)
        try:
            preview.bind("<Enter>", lambda e: root.focus_force())
        except Exception:
            pass
        try:
            root.attributes("-fullscreen", False)
        except Exception:
            pass
        root.deiconify()
        root.after(_SAMPLE_MS, sample)
        try:
            root.focus_force()
            root.mainloop()
        except Exception:
            pass
        finally:
            # 销毁在创建它的 tkinter 线程内完成，并显式断引用触发本线程 GC，
            # 避免进程退出时 Tk 对象被其它线程回收，触发
            # "Tcl_AsyncDelete: async handler deleted by the wrong thread"
            for w in (preview, info, hint):
                try:
                    w.destroy()
                except Exception:
                    pass
            state["capture"] = None
            try:
                root.update_idletasks()
                root.destroy()
            except Exception:
                pass
            for obj in (preview, info, hint, root):
                try:
                    del obj
                except Exception:
                    pass
        return state["result"]

    @staticmethod
    def _xy(x, y):
        return "(%d, %d)" % (x, y)


def _photo(img):
    """PIL Image → tkinter.PhotoImage（PNG 中间格式，规避 Tk 版本差异）。"""
    import tkinter as tk

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return tk.PhotoImage(data=buf.read())


def pick_color_blocking(copy_to_clipboard=True):
    """便捷入口：默认复制十六进制到剪贴板；返回 {rgb,hex} 或 None。"""
    return PickColor().run_blocking(copy_to_clipboard=copy_to_clipboard)


# 线程留存：取色器在调用线程主循环，不留后台线程
_keep = threading.Thread  # noqa: F841（占位避免误删导入）