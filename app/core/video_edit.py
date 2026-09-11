"""视频编辑核心：裁剪、转 GIF、帧提取、视频信息。"""

import json
import os
import subprocess

# 窗口化进程里跑 ffmpeg 等控制台程序会弹黑窗，必须禁用
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
import threading

import imageio_ffmpeg


def get_ffmpeg():
    return imageio_ffmpeg.get_ffmpeg_exe()


def video_info(path):
    ffmpeg = get_ffmpeg()
    try:
        result = subprocess.run(
            [ffmpeg, "-i", path, "-hide_banner"],
            capture_output=True, text=True, timeout=10,
            creationflags=_NOWIN)
        stderr = result.stderr
    except Exception:
        stderr = ""
    info = {"path": path, "duration": 0, "width": 0, "height": 0,
            "fps": 0, "codec": "", "size": 0}
    try:
        info["size"] = os.path.getsize(path)
    except OSError:
        pass
    import re
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
    if m:
        h, mi, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
        info["duration"] = h * 3600 + mi * 60 + s
    m = re.search(r"(\d{2,5})x(\d{2,5})", stderr)
    if m:
        info["width"] = int(m.group(1))
        info["height"] = int(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)\s*fps", stderr)
    if m:
        info["fps"] = float(m.group(1))
    m = re.search(r"Video:\s*(\w+)", stderr)
    if m:
        info["codec"] = m.group(1)
    return info


def trim_video(input_path, start_sec, end_sec, output_path, on_done=None):
    ffmpeg = get_ffmpeg()
    cmd = [
        ffmpeg, "-y",
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-i", input_path,
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        output_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=300, creationflags=_NOWIN)
        if on_done:
            on_done({"ok": True, "data": {"path": output_path}})
    except Exception as e:
        if on_done:
            on_done({"ok": False, "err": str(e)})


def video_to_gif(input_path, output_path, fps=10, start=0, end=None,
                 width=480, on_progress=None, on_done=None):
    ffmpeg = get_ffmpeg()
    cmd = [
        ffmpeg, "-y",
        "-ss", str(start),
        "-i", input_path,
    ]
    if end is not None:
        cmd.extend(["-to", str(end)])
    cmd.extend([
        "-vf", "fps=%d,scale=%d:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
               % (fps, width),
        "-loop", "0",
        output_path,
    ])
    try:
        subprocess.run(cmd, capture_output=True, timeout=600, creationflags=_NOWIN)
        if on_done:
            on_done({"ok": True, "data": {"path": output_path}})
    except Exception as e:
        if on_done:
            on_done({"ok": False, "err": str(e)})


def extract_frame(input_path, time_sec, output_path, on_done=None):
    ffmpeg = get_ffmpeg()
    cmd = [
        ffmpeg, "-y",
        "-ss", str(time_sec),
        "-i", input_path,
        "-vframes", "1",
        output_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30, creationflags=_NOWIN)
        if on_done:
            on_done({"ok": True, "data": {"path": output_path}})
    except Exception as e:
        if on_done:
            on_done({"ok": False, "err": str(e)})
