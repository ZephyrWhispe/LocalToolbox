"""滚动截图桥接层。"""

import base64
import threading

from .base import BridgeBase


class ScrollApi(BridgeBase):
    def _init_scroll(self):
        self._scroll_frames = []
        self._scroll_running = False

    def scroll_pick_window(self):
        """提示用户将鼠标移到目标窗口，然后获取窗口句柄。"""
        import time
        self.emit("scroll_hint", "请将鼠标移到要截图的窗口上，3 秒后自动捕获...")
        time.sleep(3)
        from ..core.scroll_capture import find_scrollable_window
        hwnd = find_scrollable_window()
        if not hwnd:
            return {"ok": False, "err": "未找到目标窗口"}
        return {"ok": True, "data": {"hwnd": hwnd}}

    def scroll_capture(self, hwnd, step=50, max_scrolls=40):
        if self._scroll_running:
            return {"ok": False, "err": "正在截图中"}
        self._scroll_running = True
        self._scroll_frames = []

        def _run():
            try:
                from ..core.scroll_capture import scroll_and_capture, stitch_vertical, image_to_png_bytes
                self.emit("scroll_progress", {"status": "capturing", "frames": 0})
                frames = scroll_and_capture(int(hwnd), step=int(step),
                                            max_scrolls=int(max_scrolls))
                self._scroll_frames = frames
                self.emit("scroll_progress", {"status": "stitching",
                                               "frames": len(frames)})
                result = stitch_vertical(frames)
                if result:
                    png = image_to_png_bytes(result)
                    data_url = "data:image/png;base64," + base64.b64encode(png).decode()
                    self.emit("scroll_done", {"data_url": data_url,
                                               "frames": len(frames)})
                else:
                    self.emit("scroll_done", {"error": "拼接失败"})
            except Exception as e:
                self.emit("scroll_done", {"error": str(e)})
            finally:
                self._scroll_running = False

        threading.Thread(target=_run, daemon=True, name="scroll-capture").start()
        return {"ok": True, "data": {"started": True}}

    def scroll_get_state(self):
        return {"ok": True, "data": {"running": self._scroll_running,
                                      "frames": len(self._scroll_frames)}}
