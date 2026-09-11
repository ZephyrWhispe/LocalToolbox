"""录屏：GIF（纯 Pillow）或 MP4 视频（opencv，可选 WASAPI 系统声音）。

- GIF 模式：fps ∈ (2,5,10)，帧存内存后由 Pillow 编码（默认低配格式）；
- 视频模式：fps ∈ (10,15,30)，逐帧 cv2.VideoWriter(mp4v) 写临时 mp4，
  同程并行 WASAPI 回环录音（app/core/loopback.py）写 WAV；停止后用
  imageio-ffmpeg 自带 ffmpeg 把音轨(AAC)合并进最终 MP4（无有效声音则仅视频）；
- 范围：全屏 = 整个虚拟桌面（多显示器合并图）；区域 = 传入合并图像素矩形 box。

兼容旧接口：start(fps=...) 默认 GIF；模块常量 FPS_CHOICES/MAX_FRAMES 语义保留
（GIF 路径），视频上限按帧率×时长上限计算。
"""

import os
import shutil
import subprocess
import threading
import time

from PIL import ImageGrab

from . import logger as applog
from . import loopback, screenshot

log = applog.get_logger("recorder")

try:
    import cv2 as _cv2
    _CV2_OK = True
except Exception:  # opencv 未安装：视频模式不可用，GIF 不受影响
    _cv2 = None
    _CV2_OK = False

FPS_CHOICES = (2, 5, 10)          # GIF 帧率白名单（向后兼容引用）
GIF_FPS_CHOICES = (2, 5, 10)
VIDEO_FPS_CHOICES = (10, 15, 30)
DEFAULT_FPS = {"gif": 5, "video": 15}

MAX_FRAMES = 1200                 # GIF 自动停止上限（兼容旧引用/单测）
MAX_MINUTES = 30                  # 视频录制时长上限（分钟）
MAX_PNG_BYTES = int(1.4 * 1024 * 1024)

# 判定音轨"有效"的最低条件（否则只保留无声视频）
AUDIO_MIN_SECONDS = 0.3
AUDIO_MIN_PEAK = 0.003


class RecorderManager:
    """录制控制器：start / stop / get_state。

    on_state(state)：采集线程内约每秒节流回调 ``{running, elapsed, frames}``；
    on_done(result)：编码完成后回调 ``{ok, path|err, frames, size, type}``。
    """

    def __init__(self, on_state=None, on_done=None, log=None):
        self.shot_dir = ""  # 保存目录，空 = screenshot.DEFAULT_DIR
        self._on_state = on_state or (lambda s: None)
        self._on_done = on_done or (lambda r: None)
        self._log = log or (lambda m: None)
        self._stop = threading.Event()
        self._thread = None
        self._frames = []
        self._video_frames = 0     # 视频路径的帧数（_frames 只在 GIF 路径累积）
        self._fps = 5
        self._fmt = "gif"
        self._region = None     # 视频模式区域（合并图像素 box (x,y,w,h)）
        self._audio = False
        self._started_at = 0.0

    @property
    def running(self):
        t = self._thread
        return t is not None and t.is_alive()

    @property
    def format(self):
        return self._fmt

    def start(self, fps=None, fmt=None, region=None, audio=False):
        """开始录制。

        fmt: "gif" | "video"（None = gif，保持旧行为）；fps 白名单按格式校验；
        region: 视频模式区域 box (x, y, w, h)（整屏合并图像素坐标），None = 全屏；
        audio: 视频模式是否录制系统声音。
        """
        if self.running:
            return {"ok": False, "err": "录制已在进行中，请先停止"}
        fmt = str(fmt or "gif").lower()
        if fmt not in ("gif", "video"):
            return {"ok": False, "err": "不支持的录制格式：%s" % fmt}
        if fmt == "video" and not _CV2_OK:
            return {"ok": False,
                    "err": "MP4 编码组件未安装（opencv-python-headless）。"
                           "请先安装后再录视频，或改用 GIF 模式"}
        choices = GIF_FPS_CHOICES if fmt == "gif" else VIDEO_FPS_CHOICES
        if fps is not None:
            try:
                fps = int(fps)
            except (TypeError, ValueError):
                fps = -1
            if fps not in choices:
                return {"ok": False,
                        "err": "fps 仅支持 %s" % "/".join(map(str, choices))}
        else:
            fps = DEFAULT_FPS[fmt]
        if region is not None:
            region = tuple(int(v) for v in region[:4])
            if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
                return {"ok": False, "err": "录制区域无效"}
        self._fmt = fmt
        self._fps = fps
        self._region = region
        self._audio = bool(audio)
        self._stop.clear()
        self._frames = []
        self._video_frames = 0
        self._started_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._run, daemon=True,
            name=("gif-recorder" if fmt == "gif" else "video-recorder"))
        self._thread.start()
        return {"ok": True, "fmt": fmt, "fps": self._fps}

    def stop(self):
        if not self.running:
            return {"ok": False, "err": "当前没有进行中的录制"}
        self._stop.set()
        return {"ok": True}

    def get_state(self):
        running = self.running
        return {
            "running": running,
            "fmt": self._fmt,
            "fps": self._fps,
            "frames": len(self._frames) or self._video_frames,
            "elapsed": int(time.monotonic() - self._started_at) if running else 0,
        }

    # -- 采集线程 ------------------------------------------------------
    def _grab(self):
        """抓一帧：全屏 = 整屏合并图；区域 = 裁剪该区域。"""
        img = ImageGrab.grab(all_screens=True)
        if self._region:
            x, y, w, h = self._region
            img = img.crop((x, y, x + w, y + h))
        return img

    def _run(self):
        try:
            if self._fmt == "gif":
                self._run_gif()
            else:
                self._run_video()
        except Exception as e:
            self._log("录制异常：%s" % e)
            try:
                self._on_done({"ok": False, "err": "录制异常：%s" % e})
            except Exception:
                pass

    # -- GIF 路径（保持旧实现语义） -------------------------------------
    def _run_gif(self):
        interval = 1.0 / self._fps
        last_state = 0.0
        while not self._stop.is_set():
            t0 = time.monotonic()
            self._frames.append(self._grab())
            if len(self._frames) >= MAX_FRAMES:
                self._log("已达最大帧数 %d，自动停止" % MAX_FRAMES)
                break
            now = time.monotonic()
            if now - last_state >= 1.0:
                last_state = now
                self._emit_state()
            wait = interval - (time.monotonic() - t0)
            if wait > 0:
                self._stop.wait(wait)
        self._finish_gif()

    def _finish_gif(self):
        try:
            if not self._frames:
                self._on_done({"ok": False, "err": "未采集到任何画面"})
                return
            path = screenshot.save_frames(self.shot_dir, self._frames, self._fps)
            self._on_done({
                "ok": bool(path),
                "path": path,
                "frames": len(self._frames),
                "size": os.path.getsize(path) if path and os.path.isfile(path) else 0,
                "type": "gif" if path and path.lower().endswith(".gif") else "png",
                **({} if path else {"err": "编码保存失败（未生成文件）"}),
            })
        except Exception as e:
            self._log("GIF 保存失败：%s" % e)
            self._on_done({"ok": False, "err": "保存失败：%s" % e})

    # -- 视频路径（opencv + 可选系统声音） ------------------------------
    def _run_video(self):
        import numpy as np
        import cv2

        interval = 1.0 / self._fps
        last_state = 0.0
        frame_cap = self._fps * 60 * MAX_MINUTES
        writer = None
        writer_path = os.path.join(
            self.shot_dir or screenshot.DEFAULT_DIR, ".dl_rec_video.mp4")
        wav_path = os.path.join(
            self.shot_dir or screenshot.DEFAULT_DIR, ".dl_rec_audio.wav")
        os.makedirs(os.path.dirname(writer_path), exist_ok=True)
        audio_rec = None
        audio_err = ""
        frames = 0
        try:
            if self._audio:
                audio_rec = loopback.LoopbackRecorder()
                try:
                    audio_rec.start(wav_path)
                except loopback.AudioLoopbackError as e:
                    audio_err = str(e)
                    audio_rec = None
                    self._log("系统声音不可用，本次仅录画面：%s" % e)
            while not self._stop.is_set():
                t0 = time.monotonic()
                img = self._grab()
                if writer is None:
                    w, h = img.size
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    writer = cv2.VideoWriter(
                        writer_path, fourcc, self._fps, (w, h))
                    if not writer.isOpened():
                        raise RuntimeError("视频编码器初始化失败")
                writer.write(np.asarray(img)[:, :, ::-1])  # RGB → BGR
                frames += 1
                self._video_frames = frames     # 供 get_state 显示实时帧数
                if frames >= frame_cap:
                    self._log("已达时长上限 %d 分钟，自动停止" % MAX_MINUTES)
                    break
                now = time.monotonic()
                if now - last_state >= 1.0:
                    last_state = now
                    self._emit_state()
                wait = interval - (time.monotonic() - t0)
                if wait > 0:
                    self._stop.wait(wait)
        except Exception as e:
            self._log("视频采集中断：%s" % e)
            try:
                if writer is not None:
                    writer.release()
            except Exception:
                pass
            self._cleanup_tmp(writer_path, wav_path)
            self._on_done({"ok": False, "err": "录制失败：%s" % e})
            return
        if writer is not None:
            writer.release()
        if audio_rec is not None:
            audio_result = audio_rec.stop()
        else:
            audio_result = {"ok": False, "err": audio_err or "未启用声音"}
        self._finish_video(frames, writer_path, wav_path, audio_result)

    def _finish_video(self, frames, video_tmp, wav_path, audio_result):
        audio_used = False
        note = ""
        try:
            if not frames:
                self._cleanup_tmp(video_tmp, wav_path)
                self._on_done({"ok": False, "err": "未采集到任何画面"})
                return
            final = screenshot.recording_path(self.shot_dir, "mp4")
            audio_ok = (
                audio_result.get("ok")
                and float(audio_result.get("seconds", 0)) > AUDIO_MIN_SECONDS
                and float(audio_result.get("peak", 0)) > AUDIO_MIN_PEAK
                and os.path.isfile(wav_path)
            )
            if audio_ok:
                try:
                    from imageio_ffmpeg import get_ffmpeg_exe
                    exe = get_ffmpeg_exe()
                    subprocess.run(
                        [exe, "-y", "-hide_banner", "-loglevel", "error",
                         "-i", video_tmp, "-i", wav_path,
                         "-c:v", "copy", "-c:a", "aac",
                         "-shortest", "-movflags", "+faststart", final],
                        check=True, timeout=600,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    audio_used = True
                except Exception as e:
                    note = "（声音合并失败，已保留无声视频：%s）" % e
                    self._log("声音合并失败：%s" % e)
            if not audio_used:
                shutil.move(video_tmp, final)
            self._cleanup_tmp(video_tmp, wav_path)
            self._on_done({
                "ok": True,
                "path": final,
                "frames": frames,
                "size": os.path.getsize(final) if os.path.isfile(final) else 0,
                "type": "video",
                "audio": audio_used,
                "note": note,
            })
        except Exception as e:
            self._log("视频保存失败：%s" % e)
            self._cleanup_tmp(video_tmp, wav_path)
            self._on_done({"ok": False, "err": "视频保存失败：%s" % e})

    def _cleanup_tmp(self, *paths):
        for p in paths:
            try:
                if p and os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass

    def _emit_state(self):
        try:
            self._on_state({
                "running": True,
                "elapsed": int(time.monotonic() - self._started_at),
                "frames": len(self._frames),
            })
        except Exception:
            pass
