"""视频编辑桥接层。"""

import base64
import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
import socketserver

from .base import BridgeBase


class VideoFileHandler(SimpleHTTPRequestHandler):
    """简单的视频文件处理器，用于本地HTTP服务。"""
    
    def do_GET(self):
        # 只允许访问特定的视频文件
        if hasattr(self.server, 'allowed_path'):
            import urllib.parse
            requested = urllib.parse.unquote(self.path.lstrip('/'))
            if requested == os.path.basename(self.server.allowed_path):
                path = self.server.allowed_path
                try:
                    size = os.path.getsize(path)
                except OSError:
                    self.send_response(404)
                    self.end_headers()
                    return
                start, end, partial = 0, size - 1, False
                rng = self.headers.get('Range')
                if rng and rng.startswith('bytes='):
                    try:
                        spec = rng[len('bytes='):].split(',')[0].strip()
                        s, _, e = spec.partition('-')
                        if s:
                            start = int(s)
                            if e:
                                end = int(e)
                        elif e:                     # bytes=-N 后缀范围
                            start = max(0, size - int(e))
                        if start >= size or start > end:
                            self.send_response(416)
                            self.send_header('Content-Range', 'bytes */%d' % size)
                            self.end_headers()
                            return
                        end = min(end, size - 1)
                        partial = True
                    except (ValueError, TypeError):
                        start, end, partial = 0, size - 1, False
                length = end - start + 1
                self.send_response(206 if partial else 200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(length))
                if partial:
                    self.send_header('Content-Range',
                                     'bytes %d-%d/%d' % (start, end, size))
                self.end_headers()
                with open(path, 'rb') as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
        self.send_response(404)
        self.end_headers()
    
    def log_message(self, format, *args):
        pass  # 静默日志


class VideoEditApi(BridgeBase):
    def _init_video(self):
        self._video_path = None
        self._video_server = None
        self._video_port = None

    def video_pick_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            filetypes=[("视频文件", "*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm"),
                       ("所有文件", "*.*")])
        if not path:
            return {"ok": False, "err": "未选择文件"}
        self._video_path = path
        
        # 启动本地HTTP服务器来提供视频文件
        try:
            self._start_video_server(path)
        except Exception as e:
            # 如果服务器启动失败，仍然返回文件信息但使用file://协议作为兜底
            pass
        
        try:
            from ..core.video_edit import video_info
            info = video_info(path)
            # 添加视频URL（HTTP或file://）
            if self._video_port:
                import urllib.parse
                filename = os.path.basename(path)
                info['video_url'] = f"http://localhost:{self._video_port}/{urllib.parse.quote(filename)}"
            else:
                info['video_url'] = "file://" + path
            return {"ok": True, "data": info}
        except Exception as e:
            return {"ok": False, "err": str(e)}
    
    def _start_video_server(self, video_path):
        """启动本地HTTP服务器来提供视频文件。"""
        # 先停止之前的服务器
        self._stop_video_server()
        
        # 查找可用端口
        for port in range(8765, 8775):
            try:
                server = HTTPServer(('127.0.0.1', port), VideoFileHandler)
                server.allowed_path = video_path
                server_thread = threading.Thread(
                    target=server.serve_forever,
                    daemon=True,
                    name=f"video-server-{port}"
                )
                server_thread.start()
                self._video_server = server
                self._video_port = port
                return
            except OSError:
                continue
        # 如果所有端口都不可用，保持 None
        self._video_port = None
    
    def _stop_video_server(self):
        """停止本地HTTP服务器。"""
        if self._video_server:
            try:
                self._video_server.shutdown()
            except Exception:
                pass
            self._video_server = None
            self._video_port = None

    def video_get_info(self):
        if not self._video_path:
            return {"ok": False, "err": "未打开视频"}
        from ..core.video_edit import video_info
        return {"ok": True, "data": video_info(self._video_path)}

    def video_trim(self, start, end):
        if not self._video_path:
            return {"ok": False, "err": "未打开视频"}
        from tkinter import filedialog
        out = filedialog.asksaveasfilename(
            defaultextension=".mp4",
            filetypes=[("MP4", "*.mp4"), ("所有文件", "*.*")])
        if not out:
            return {"ok": False, "err": "未选择保存路径"}

        def _done(result):
            self.emit("video_trim_done", result)

        from ..core.video_edit import trim_video

        def worker():
            trim_video(self._video_path, float(start), float(end), out, _done)

        threading.Thread(target=worker, daemon=True, name="video-trim").start()
        return {"ok": True, "data": {"started": True}}

    def video_to_gif(self, fps=10, start=0, end=None, width=480):
        if not self._video_path:
            return {"ok": False, "err": "未打开视频"}
        from tkinter import filedialog
        out = filedialog.asksaveasfilename(
            defaultextension=".gif",
            filetypes=[("GIF", "*.gif"), ("所有文件", "*.*")])
        if not out:
            return {"ok": False, "err": "未选择保存路径"}

        def _done(result):
            self.emit("video_gif_done", result)

        from ..core.video_edit import video_to_gif as _video_to_gif

        def worker():
            _video_to_gif(self._video_path, out, int(fps), float(start),
                          float(end) if end else None, int(width), on_done=_done)

        threading.Thread(target=worker, daemon=True, name="video-to-gif").start()
        return {"ok": True, "data": {"started": True}}

    def video_extract_frame(self, time_sec):
        if not self._video_path:
            return {"ok": False, "err": "未打开视频"}
        from tkinter import filedialog
        out = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("所有文件", "*.*")])
        if not out:
            return {"ok": False, "err": "未选择保存路径"}

        def _done(result):
            self.emit("video_frame_done", result)

        from ..core.video_edit import extract_frame

        def worker():
            extract_frame(self._video_path, float(time_sec), out, _done)

        threading.Thread(target=worker, daemon=True, name="video-frame").start()
        return {"ok": True, "data": {"started": True}}
