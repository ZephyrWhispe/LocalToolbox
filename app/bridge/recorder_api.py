"""录屏桥接：rec_start / rec_stop / rec_get_state / rec_set_fps。

GIF（2/5/10fps，低配）与 MP4 视频（10/15/30fps，可选系统声音，区域/全屏）。
视频录制启动后自动隐藏主窗口（避免把自己录进去），完成后恢复。
停止立即返回，编码完成后发 ``rec_done`` 事件（含路径/类型/是否含声音）。
"""

from ..core import logger as applog
from ..core import recorder
from ..core.recorder import RecorderManager

log = applog.get_logger("rec")


class RecorderApi:
    def _init_rec(self):
        mgr = RecorderManager(
            on_state=lambda s: self.emit("rec_state", s),
            on_done=self._rec_done,
            log=self.emit_log,
        )
        mgr.shot_dir = self._shot_dir()
        self._rec = mgr
        self._rec_hidden = False  # 录制期间窗口是否被本模块隐藏（v4 补初始化）

    def _rec_fps(self, fmt="gif"):
        """当前配置帧率；不在该格式白名单内则回落默认值。"""
        try:
            fps = int(self.cfg.get("recorder_fps", 0) or 0)
        except (TypeError, ValueError):
            fps = 0
        choices = (recorder.GIF_FPS_CHOICES if fmt == "gif"
                   else recorder.VIDEO_FPS_CHOICES)
        return fps if fps in choices else recorder.DEFAULT_FPS.get(fmt, 5)

    def _rec_done(self, r):
        """录制完成：通知前端；恢复被隐藏的主窗口。"""
        if self._rec_hidden:
            self._rec_hidden = False
            try:
                if self._window is not None:
                    time.sleep(0.1)  # 给系统一点时间处理窗口状态变化
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
        self.emit("rec_done", r)

    # -- API -----------------------------------------------------------
    def rec_start(self, opts=None):
        """开始录制。

        opts: {format: "gif"|"video", fps?: int, region?: {x,y,w,h},
               audio?: bool}；缺省取配置（recorder_format/audio/fps）。
        """
        try:
            opts = opts or {}
            fmt = str(opts.get("format") or self.cfg.get("recorder_format", "gif"))
            if fmt not in ("gif", "video"):
                fmt = "gif"
            fps = opts.get("fps")
            region = opts.get("region")
            if isinstance(region, dict):
                region = (int(region.get("x", 0)), int(region.get("y", 0)),
                          int(region.get("w", 0)), int(region.get("h", 0)))
            audio = bool(opts.get("audio", self.cfg.get("recorder_audio", True)))
            if fps is None:
                fps = self._rec_fps(fmt)
            res = self._rec.start(fps=fps, fmt=fmt, region=region, audio=audio)
            if res.get("ok"):
                self._rec.shot_dir = self._shot_dir()
                # GIF / 视频录制期间均隐藏主窗口（避免把自己录进去），结束恢复
                try:
                    if self._window is not None:
                        self._window.hide()
                        self._rec_hidden = True
                except Exception:
                    pass
                self.emit("rec_state", self._rec.get_state())
                # 统一 {ok, data} 契约（recorder.start 成功返回顶层 fmt/fps）
                return {"ok": True,
                        "data": {"fmt": res.get("fmt"), "fps": res.get("fps")}}
            return {"ok": False, "err": res.get("err") or "录制启动失败"}
        except Exception as e:
            return {"ok": False, "err": "录制启动失败：%s" % e}

    def rec_stop(self):
        """请求停止录制，立即返回；编码完成后由 rec_done 事件带回结果。"""
        res = self._rec.stop()
        if res.get("ok"):
            return {"ok": True, "data": None}
        return {"ok": False, "err": res.get("err") or "停止失败"}

    def rec_get_state(self):
        return {"ok": True, "data": self._rec.get_state()}

    def rec_set_fps(self, fps, fmt=None):
        """保存默认帧率（须在对应格式白名单内）。"""
        try:
            fps = int(fps)
        except (TypeError, ValueError):
            return {"ok": False, "err": "fps 需为数字"}
        fmt = str(fmt or self.cfg.get("recorder_format", "gif"))
        choices = (recorder.GIF_FPS_CHOICES if fmt == "gif"
                   else recorder.VIDEO_FPS_CHOICES)
        if fps not in choices:
            return {"ok": False,
                    "err": "%s 模式 fps 仅支持 %s"
                    % (fmt, "/".join(map(str, choices)))}
        self.cfg.set("recorder_fps", fps)
        return {"ok": True, "data": fps}
