"""Rclone 本地挂载管理：把 OpenList 的 WebDAV 挂载为盘符或文件夹。

前置依赖：WinFsp 驱动（app/core/winfsp.py 检测/安装）与 rclone.exe（bindl 下载）。
- 盘符目标：加 --network-mode（WinFsp 下免管理员映射盘符）与 --volname；
- 文件夹目标：挂到任意绝对路径（权限要求最低）。
- 进程自维护 _mounts 表 + 监控线程，退出即清理；stop() 批量卸载。
"""

import os
import re
import subprocess

# 窗口化进程里启动控制台程序会弹黑窗（关窗即杀挂载），必须禁用
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
import threading
import time

from .config import DATA_HOME

RCLONE_DIR = os.path.join(DATA_HOME, "rclone")
RCLONE_BIN = os.path.join(RCLONE_DIR, "bin", "rclone.exe")
CONF_PATH = os.path.join(DATA_HOME, "openlist", "rclone.conf")
LOG_PATH = os.path.join(DATA_HOME, "openlist", "rclone.log")


def runtime_rclone_candidate():
    """应用安装目录下 runtime/rclone.exe（支持随程序附带二进制）。"""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(here, "runtime", "rclone.exe")


def evaluate(target):
    """挂载点是否已生效（盘符存在 / 文件夹存在）。"""
    if not target:
        return False
    try:
        return os.path.exists(target)
    except OSError:
        return False


class RcloneMountManager:
    def __init__(self, openlist=None, log_callback=None):
        self.openlist = openlist  # OpenListManager 引用（取 host/port 组装 dav url）
        self.bin_path = ""
        self.config_path = CONF_PATH
        self.log_path = LOG_PATH
        self._mounts = {}  # target -> {"proc","pid","ts"}
        self._lock = threading.Lock()
        self.log_callback = log_callback or (lambda m: None)

    def _log(self, msg):
        try:
            self.log_callback(str(msg))
        except Exception:
            pass

    # -- 二进制定位 -----------------------------------------------------
    def detect_bin(self, configured=""):
        """按优先级探测：配置路径 → 应用数据目录 → 程序附带 runtime/。"""
        cands = []
        if configured:
            cands.append(str(configured))
        cands.append(RCLONE_BIN)
        cands.append(runtime_rclone_candidate())
        for c in cands:
            if c and os.path.isfile(c):
                self.bin_path = os.path.abspath(c)
                return self.bin_path
        raise ValueError(
            "未找到 rclone.exe。请点击「自动下载」，或手动放置到 %s" % RCLONE_BIN
        )

    def _base_url(self):
        if self.openlist is not None:
            port = getattr(self.openlist, "port", 0) or 0
            host = getattr(self.openlist, "host", "127.0.0.1") or "127.0.0.1"
            if port:
                return "http://%s:%d/dav" % (host, port)
        return "http://127.0.0.1:15244/dav"  # 兜底默认端口

    # -- 配置 -------------------------------------------------------------
    def ensure_config(self, user="", pwd=""):
        """生成 rclone.conf（remote `openlist` = 本地 OpenList WebDAV），原子写。"""
        cfg_dir = os.path.dirname(self.config_path)
        try:
            os.makedirs(cfg_dir, exist_ok=True)
        except OSError:
            pass
        lines = ["[openlist]",
                 "type = webdav",
                 "url = %s" % self._base_url()]
        if (user or "").strip():
            lines.append("user = %s" % str(user).strip())
            lines.append("pass = %s" % str(pwd or ""))
        tmp = self.config_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, self.config_path)
        self._log("已生成 rclone 配置：%s（挂载点 %s）" % (self.config_path, self._base_url()))
        return self.config_path

    # -- 挂载 -------------------------------------------------------------
    def mount(self, target_type="letter", letter="", folder="", user="", pwd="",
              winfsp_ok=None):
        """挂载 OpenList WebDAV 到盘符或文件夹。

        winfsp_ok：可选注入的检测结果（测试用）；为空则实时检测。
        返回 {"target","pid","mounted"}；失败抛 ValueError。
        """
        target_type = str(target_type or "letter").lower()
        if target_type not in ("letter", "folder"):
            raise ValueError("目标类型无效（可选 letter / folder）")

        from . import winfsp

        ok = winfsp.is_winfsp_installed() if winfsp_ok is None else bool(winfsp_ok)
        if not ok:
            raise ValueError(
                "未安装 WinFsp 驱动，rclone 挂载依赖它。请点击「安装 WinFsp」"
                "或手动从 %s 安装。" % winfsp.WINPFSP_URL
            )

        try:
            self.detect_bin("")
        except ValueError:
            raise
        if not os.path.isfile(self.bin_path):
            raise ValueError("rclone 程序不存在：%s" % self.bin_path)

        if target_type == "letter":
            target = str(letter or "").strip().upper()
            if not re.fullmatch(r"[A-Z]", target):
                raise ValueError("盘符需为单个字母（A~Z）")
            target += ":"
            if evaluate(target):
                raise ValueError("盘符 %s 已被占用，请换一个盘符。" % target)
        else:
            target = str(folder or "").strip()
            if not target:
                raise ValueError("请填写挂载文件夹路径")
            if not os.path.isabs(target):
                raise ValueError("请填写绝对路径（如 D:\\openlist-mount）")
            try:
                os.makedirs(target, exist_ok=True)
            except OSError as e:
                raise ValueError("无法创建挂载目录 %s：%s" % (target, e))

        with self._lock:
            if target in self._mounts:
                e = self._mounts[target]
                return {"target": target, "pid": e["pid"], "mounted": True}

        self.ensure_config(user, pwd)
        cmd = [
            self.bin_path, "mount", "openlist:", target,
            "--config", self.config_path,
            "--write-back-cache", "--vfs-cache-mode", "minimal",
            "--log-file", self.log_path, "--log-level", "INFO",
            "--no-console",
        ]
        if target_type == "letter":
            cmd += ["--network-mode", "--volname", "OpenList-" + target[0]]
        self._log("挂载命令：%s" % subprocess.list2cmdline(cmd))
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NOWIN,
            )
        except OSError as e:
            raise ValueError("启动 rclone mount 失败：%s" % e)

        # 短暂等待进程存活 + 挂载点出现
        deadline = time.time() + 10
        ok = False
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            if evaluate(target):
                ok = True
                break
            time.sleep(0.5)
        if not ok:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                pass
            tail = self._tail_log(20)
            hint = "（旧版 WinFsp 可能不支持字母盘挂载，可改用文件夹挂载）" if target_type == "letter" else ""
            raise ValueError(
                "挂载未生效：%s%s %s" % (target, hint, tail or "（请查看 rclone.log）")
            )

        with self._lock:
            self._mounts[target] = {"proc": proc, "pid": proc.pid, "ts": time.time()}
        threading.Thread(target=self._watch, args=(target, proc), daemon=True).start()
        self._log("已挂载 openlist: → %s（pid %d）" % (target, proc.pid))
        return {"target": target, "pid": proc.pid, "mounted": True}

    def _watch(self, target, proc):
        while proc.poll() is None:
            time.sleep(2.0)
        with self._lock:
            self._mounts.pop(target, None)
        self._log("挂载 %s 已退出（退出码 %s）。" % (target, proc.returncode))

    # -- 卸载 -------------------------------------------------------------
    def umount(self, target):
        """卸载指定挂载点：优先 rclone umount，兜底 terminate/kill 进程。"""
        target = str(target or "").strip()
        with self._lock:
            entry = self._mounts.pop(target, None)
        if not entry:
            return False
        proc = entry["proc"]
        try:
            r = subprocess.run(
                [self.bin_path, "umount", "openlist:", target],
                capture_output=True, timeout=6, creationflags=_NOWIN,
            )
            if r.returncode != 0:
                raise OSError("umount 返回码 %s" % r.returncode)
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except (subprocess.TimeoutExpired, OSError):
                    pass
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            except OSError:
                pass
        self._log("已卸载 %s。" % target)
        return True

    def list_mounted(self):
        """存活挂载列表；进程已退出的条目自动清理。"""
        out = []
        with self._lock:
            for target, e in list(self._mounts.items()):
                if e["proc"].poll() is None:
                    out.append({"target": target, "pid": e["pid"], "ts": e["ts"]})
                else:
                    self._mounts.pop(target, None)
        return out

    # -- 停止 -------------------------------------------------------------
    def stop(self):
        """批量卸载所有挂载（应用退出时调用）。"""
        for target in list(self._mounts.keys()):
            try:
                self.umount(target)
            except Exception as e:
                self._log("卸载 %s 失败：%s" % (target, e))

    # -- 日志 -------------------------------------------------------------
    def _tail_log(self, lines=40):
        try:
            if not os.path.isfile(self.log_path):
                return ""
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-lines:]
            return "".join(tail).strip()
        except OSError:
            return ""

    def state(self):
        return {
            "bin": self.bin_path or "",
            "mounted": self.list_mounted(),
            "config": self.config_path,
        }