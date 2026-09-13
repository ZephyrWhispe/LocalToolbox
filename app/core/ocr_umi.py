"""Umi-OCR 内核管理（可选引擎）：下载自解压包 / 静默解压 / 服务拉起 / HTTP 识别。

设计（用户决策 v5.1）：OCR 引擎三选（winrt / rapid / umi），Umi-OCR 为**可选
依赖**——内置「下载内核」按钮从 GitHub Releases 拉取单文件自解压包
（Umi-OCR_Rapid_v2.1.5.7z.exe，RapidOCR 内核 ~103MB），静默解压到数据目录；
识别时服务未运行则自动 `Umi-OCR.exe --server` 拉起并健康检查。不选 umi
引擎时完全不依赖它。下载复用 bindl 的直连+镜像链。
"""

import base64
import io
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request

from .bindl import BindlError, _download_file, _github_urls
from .config import DATA_HOME

UMI_REPO = "hiroi-sora/Umi-OCR"
UMI_TAG = "v2.1.5"
UMI_ASSET = "Umi-OCR_Rapid_v2.1.5.7z.exe"
UMI_DIR = os.path.join(DATA_HOME, "ocr", "umi")
KERNEL_EXE = "Umi-OCR.exe"
DEFAULT_URL = "http://127.0.0.1:1224"
_MIN_BYTES = 50 * 1024 * 1024  # 自解压包 ~103MB，防止下到错误页

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_lock = threading.Lock()
_proc = None  # 本应用拉起的 Umi-OCR 服务子进程


class UmiError(RuntimeError):
    """Umi 内核/服务错误（中文直显）。"""


def kernel_dir():
    return UMI_DIR


def find_kernel_exe(explicit=""):
    """定位 Umi-OCR.exe：显式路径 → 数据目录常规位置 → None。"""
    cands = []
    if explicit and os.path.isfile(explicit):
        return explicit
    root = UMI_DIR
    if os.path.isdir(root):
        cands.append(os.path.join(root, KERNEL_EXE))
        try:
            for name in os.listdir(root):
                sub = os.path.join(root, name)
                if os.path.isdir(sub):
                    cands.append(os.path.join(sub, KERNEL_EXE))
        except OSError:
            pass
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def server_ready(url, timeout=2):
    """Umi HTTP 服务是否就绪（GET /api/ocr/get_options 探测）。"""
    try:
        req = urllib.request.Request(
            url.rstrip("/") + "/api/ocr/get_options",
            headers={"User-Agent": "LocalToolbox/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def download_kernel(progress_cb=None, timeout=120, dest_dir=None):
    """下载内核自解压包并静默解压。返回 {"exe": Umi-OCR.exe 路径}。"""
    dest = dest_dir or UMI_DIR
    existed = find_kernel_exe()
    if existed:
        return {"exe": existed, "cached": True}
    url = "https://github.com/%s/releases/download/%s/%s" % (
        UMI_REPO, UMI_TAG, UMI_ASSET)
    os.makedirs(dest, exist_ok=True)
    tmp = os.path.join(dest, ".dl_umi_sfx.exe")
    try:
        _download_file(UMI_ASSET, url, tmp, dest, progress_cb, timeout,
                       validator=lambda p: os.path.getsize(p) >= _MIN_BYTES)
        # 静默解压：标准 7z SFX 支持 -y -o；cwd 设为 dest 兜底默认解压位置
        subprocess.run(
            [tmp, "-y", "-o" + dest], cwd=dest, timeout=600,
            creationflags=_NOWIN, capture_output=True)
        exe = find_kernel_exe()
        if not exe:
            raise UmiError(
                "解压完成但未找到 Umi-OCR.exe。请手动运行 %s 解压，"
                "然后在设置中指定 Umi-OCR.exe 路径。" % UMI_ASSET)
        return {"exe": exe, "cached": False}
    except BindlError as e:
        raise UmiError(str(e))
    except subprocess.TimeoutExpired:
        raise UmiError("解压超时，请手动运行下载的自解压包并指定路径。")
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass


def ensure_server(url=DEFAULT_URL, exe_path="", autostart=True, wait=15):
    """确保 Umi HTTP 服务可用；需要时自拉起内核。返回 {"ready":bool,"started":bool}。"""
    if server_ready(url):
        return {"ready": True, "started": False}
    if not autostart:
        return {"ready": False, "started": False}
    exe = find_kernel_exe(exe_path)
    if not exe:
        raise UmiError(
            "Umi-OCR 内核未安装：请在 设置→OCR 中点击「下载内核」，"
            "或手动指定 Umi-OCR.exe 路径；也可改用 winrt 引擎。")
    global _proc
    with _lock:
        if not server_ready(url):
            if _proc is not None and _proc.poll() is not None:
                _proc = None
            if _proc is None:
                try:
                    _proc = subprocess.Popen(
                        [exe, "--server"], cwd=os.path.dirname(exe),
                        creationflags=_NOWIN)
                except OSError as e:
                    raise UmiError("启动 Umi-OCR 内核失败：%s" % e)
            deadline = time.time() + wait
            while time.time() < deadline:
                if server_ready(url, timeout=1.5):
                    break
                time.sleep(0.5)
            else:
                raise UmiError(
                    "Umi-OCR 内核已拉起但服务在 %ds 内未就绪，请稍后重试"
                    "或检查端口（默认 1224）。" % wait)
    return {"ready": True, "started": True}


def recognize_umi(img, url=DEFAULT_URL, exe_path="", autostart=True,
                  timeout=30):
    """Umi HTTP 识别 PIL Image。返回 {text, lines:[{text,rect}]}（与 winrt 同构）。"""
    url = str(url or DEFAULT_URL)
    st = ensure_server(url, exe_path=exe_path, autostart=autostart)
    if not st["ready"]:
        raise UmiError("Umi-OCR 服务未运行（自拉起已关闭）。请启动 Umi-OCR"
                       " 或在设置中开启自动拉起。")
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    payload = json.dumps({"base64": base64.b64encode(buf.getvalue()).decode("ascii")})
    req = urllib.request.Request(
        url.rstrip("/") + "/api/ocr/data", data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "LocalToolbox/5.0"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise UmiError("Umi-OCR 服务调用失败：%s" % e)
    except (ValueError, UnicodeDecodeError):
        raise UmiError("Umi-OCR 返回了无法解析的内容")
    if body.get("code") != 100:
        raise UmiError("Umi-OCR 识别失败：%s" % (body.get("message") or body.get("code")))
    lines = []
    for item in body.get("data") or []:
        text = str(item.get("text") or "")
        box = item.get("box") or []
        if box:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            rect = {"x": int(min(xs)), "y": int(min(ys)),
                    "w": int(max(xs) - min(xs)), "h": int(max(ys) - min(ys))}
        else:
            rect = {"x": 0, "y": 0, "w": 0, "h": 0}
        lines.append({"text": text, "rect": rect})
    return {"text": "\n".join(ln["text"] for ln in lines), "lines": lines}


def stop_server():
    """收束本应用拉起的 Umi 服务子进程（主程序退出时调用）。"""
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            try:
                _proc.terminate()
            except OSError:
                pass
        _proc = None
