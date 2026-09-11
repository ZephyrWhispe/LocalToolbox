"""OpenList 进程托管：定位/启动/停止/健康监控统一 WebDAV 后端。

架构对齐 web_server.py / ftp_server.py：
- start() 校验 + 子进程 + daemon 监控线程 + 防火墙放行
- stop() 优雅关闭并撤销防火墙
- log_callback 向桥接层推送日志（桥接层转发为 pan_log 事件）

OpenList 数据目录在本应用数据目录下（DATA_HOME/openlist/），驱动配置
通过官方管理界面或导入 openlist.json 完成，本模块不直接写其 SQLite。
"""

import os
import socket
import subprocess
import threading
import time

from . import firewall
from .config import DATA_HOME

# 窗口化进程（console=False 的 exe）里启动控制台程序会弹出黑窗，
# 用户关掉窗口即杀掉服务（表现为「必须开着窗口才运行」）——必须禁用
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEFAULT_DATA_DIR = os.path.join(DATA_HOME, "openlist")
_BIN_CANDIDATES = (
    os.path.join(DATA_HOME, "openlist", "bin", "openlist.exe"),
)


def runtime_bin_candidate():
    """应用安装目录下 runtime/openlist.exe（支持随程序附带二进制）。"""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(here, "runtime", "openlist.exe")


class OpenListManager:
    def __init__(self, log_callback=None):
        self.bin_path = ""
        self.data_dir = DEFAULT_DATA_DIR
        self.host = "127.0.0.1"
        self.port = 0
        self.running = False
        self.healthy = False
        self.fw_open = False
        self.proc = None
        self.status = "stopped"  # stopped | starting | running | starting_error
        self._monitor = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.log_callback = log_callback or (lambda m: None)
        # v3.2：运行时长 + 性能采样 + 异常退出回调（托盘通知用）
        self._start_ts = None
        self.metrics = {"cpu": None, "mem_mb": None}
        self._metrics_thread = None
        self._metrics_stop = threading.Event()
        self.exit_callback = None
        self.metrics_enabled = True  # 单测可关（避免后台真实跑 PowerShell）

    def _log(self, msg):
        try:
            self.log_callback(str(msg))
        except Exception:
            pass

    # -- 二进制定位 -----------------------------------------------------
    def detect_bin(self, configured=""):
        """按优先级探测二进制：配置路径 → 应用数据目录 → 程序附带 runtime/。"""
        cands = []
        if configured:
            cands.append(configured)
        cands.extend(_BIN_CANDIDATES)
        cands.append(runtime_bin_candidate())
        for c in cands:
            if c and os.path.isfile(c):
                self.bin_path = os.path.abspath(c)
                return self.bin_path
        raise ValueError(
            "未找到 openlist.exe。请点击「自动下载」，或手动放置到 %s" % _BIN_CANDIDATES[0]
        )

    # -- 启动参数 ---------------------------------------------------------
    def _ensure_config(self, data_dir, host, port):
        """把监听地址/端口写入 OpenList 的 config.json。

        openlist.exe 没有 --addr/--port 之类的启动参数（`server` 子命令只接受
        --data/--config 等全局标志，地址定义在配置文件里），因此监听配置必须
        落盘到 <data>/config.json 的 scheme 段。已有配置保留其它字段，仅覆盖
        scheme.address / scheme.http_port（首次运行会生成默认配置，缺失字段由
        程序按内置默认值补齐）。
        """
        import json

        path = os.path.join(data_dir, "config.json")
        conf = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    conf = loaded
            except (OSError, ValueError):
                conf = {}
        scheme = conf.get("scheme")
        if not isinstance(scheme, dict):
            scheme = {}
        scheme["address"] = str(host)
        scheme["http_port"] = int(port)
        conf["scheme"] = scheme
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(conf, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path

    def _build_cmd(self, host, port):
        """OpenList CLI 启动参数：``openlist server --data <数据目录>``。

        监听地址/端口不在命令行里（该程序无 --addr 参数），经 _ensure_config
        写入 config.json 的 scheme 段。用 server 而非 start 子命令：start 是
        「静默后台启动」（立即返回，进程托管/健康检查全部失效）。
        """
        local_dir = os.path.join(self.data_dir, "data")
        os.makedirs(local_dir, exist_ok=True)
        self._ensure_config(local_dir, host, port)
        cmd = [self.bin_path, "server", "--data", local_dir]
        self._log("启动命令：%s" % subprocess.list2cmdline(cmd))
        return cmd

    # -- 探活 -------------------------------------------------------------
    def _tcp_probe(self, timeout=1.0):
        if not self.port:
            return False
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except OSError:
            return False

    def _pump_output(self, proc):
        """子进程 stdout/stderr → 日志回调（strip ANSI；进程结束/被终止即退出）。"""
        import re as _re

        ansi = _re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                self._log(ansi.sub("", line)[:500])
        except Exception:
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    # -- 启动 -------------------------------------------------------------
    def start(self, host="127.0.0.1", port=15244, fw=True):
        with self._lock:
            if self.running:
                return
            if not 1 <= int(port) <= 65535:
                raise ValueError("无效端口：%s" % port)
            if not self.bin_path:
                self.detect_bin("")
            if not os.path.isfile(self.bin_path):
                raise ValueError("OpenList 程序不存在：%s" % self.bin_path)
            self.host = str(host or "127.0.0.1")
            self.port = int(port)
            self.status = "starting"
            cmd = self._build_cmd(self.host, self.port)
            try:
                self.proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=self.data_dir,
                    creationflags=_NOWIN,
                )
            except Exception as e:
                self.status = "starting_error"
                self.proc = None
                raise ValueError("OpenList 启动失败：%s" % e)
            # 转发子进程输出到日志回调：首次运行会在 stdout 打印「初始管理员密码」，
            # 丢弃输出会让用户无法登录 Web 管理界面
            threading.Thread(target=self._pump_output, args=(self.proc,),
                             daemon=True, name="openlist-out").start()

        # 健康轮询（最长 30s）
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                with self._lock:
                    self.status = "starting_error"
                    self.running = False
                raise ValueError(
                    "OpenList 进程启动后立即退出（退出码 %s）。请检查端口占用或程序完整性。"
                    % self.proc.returncode
                )
            if self._tcp_probe():
                break
            time.sleep(0.5)
        else:
            with self._lock:
                self.status = "starting_error"
            if self.proc.poll() is None:
                self.proc.terminate()
            raise ValueError("OpenList 在 30 秒内未就绪（端口 %d 未监听）。" % self.port)

        with self._lock:
            self.running = True
            self.healthy = True
            self.status = "running"
        self._log("OpenList 已启动：http://%s:%d（数据目录 %s）" % (self.host, self.port, self.data_dir))
        if fw:
            ok, msg = firewall.add_ports("LocalToolboxOpenList", [(self.port, "TCP")])
            if ok:
                self.fw_open = True
                self._log("已放行防火墙入站端口 %d" % self.port)
            else:
                self.fw_open = False
                self._log("防火墙放行失败（%s），不影响本机访问。" % ((msg or "").strip()[:120] or "未授权"))
        self._stop_event.clear()
        self._monitor = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor.start()
        self._start_ts = time.time()
        if self.metrics_enabled:
            self._metrics_stop.clear()
            self._metrics_thread = threading.Thread(target=self._metrics_loop, daemon=True)
            self._metrics_thread.start()

    # -- 性能采样 ---------------------------------------------------------
    def _metrics_loop(self):
        """PowerShell 采样：2s 一次 CPU 差速% / 内存 WorkingSet MB。
        参照 traffic.py 先例，进程退出后自动结束循环。
        """
        last_cpu = 0.0
        last_t = time.time()
        while not self._metrics_stop.wait(2.0):
            pid = self.proc.pid if (self.proc and self.proc.poll() is None) else None
            if not pid:
                self.metrics = {"cpu": None, "mem_mb": None}
                continue
            # PowerShell：取得 PID 的 CPU 累计时间 (TotalProcessorTime) 与 工作集大小
            try:
                cmd = (
                    "Get-Process -Id %d | Select-Object TotalProcessorTime, WorkingSet64 | "
                    "ConvertTo-Json -Compress"
                ) % pid
                r = subprocess.run(
                    ["powershell.exe", "-NonInteractive", "-NoProfile", "-Command", cmd],
                    capture_output=True, text=True, timeout=5, errors="replace",
                    creationflags=_NOWIN,
                )
                if r.returncode != 0:
                    self.metrics = {"cpu": None, "mem_mb": None}
                    continue
                import json
                data = json.loads(r.stdout)
                if not data or "TotalProcessorTime" not in data:
                    self.metrics = {"cpu": None, "mem_mb": None}
                    continue
                cpu_t = float(data["TotalProcessorTime"])
                mem_bytes = int(data.get("WorkingSet64", 0))
                now = time.time()
                dt = max(now - last_t, 0.5)
                cpu_percent = max(0.0, (cpu_t - last_cpu) / (dt * 1) * 100)  # 单核心 CPU 百分比
                mem_mb = mem_bytes / (1024 * 1024) if mem_bytes > 0 else None
                self.metrics = {"cpu": cpu_percent, "mem_mb": mem_mb}
                last_cpu, last_t = cpu_t, now
            except Exception:
                self.metrics = {"cpu": None, "mem_mb": None}

    # -- 监控 -------------------------------------------------------------
    def _monitor_loop(self):
        while not self._stop_event.wait(3.0):
            proc = self.proc
            if proc is None:
                return
            code = proc.poll()
            if code is not None:
                with self._lock:
                    was = self.running
                    self.running = False
                    self.healthy = False
                    self._metrics_stop.set()
                    self.status = "stopped"
                if was:
                    self._log("OpenList 进程已退出（退出码 %s）。" % code)
                    try:
                        if callable(self.exit_callback):
                            self.exit_callback()
                    except Exception:
                        pass
                return

    # -- 停止 -------------------------------------------------------------
    def stop(self):
        with self._lock:
            proc, was_running = self.proc, self.running
            self._stop_event.set()
            self._metrics_stop.set()
            self.proc = None
            self.running = False
            self.healthy = False
            self._start_ts = None
            self.metrics = {"cpu": None, "mem_mb": None}
            self.status = "stopped"
            if self.fw_open and self.port:
                try:
                    firewall.remove("LocalToolboxOpenList", self.port)
                except Exception as e:
                    self._log("移除防火墙规则失败：%s" % e)
                self.fw_open = False
        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            except OSError:
                pass
            self._log("OpenList 已停止。")
        elif was_running is False and proc is None:
            pass

    # -- 状态 -------------------------------------------------------------
    def state(self):
        pid = self.proc.pid if (self.proc and self.proc.poll() is None) else None
        uptime = int(time.time() - self._start_ts) if (self.running and self._start_ts) else None
        st = {
            "running": self.running,
            "healthy": self.healthy and pid is not None,
            "status": self.status,
            "pid": pid,
            "bin": self.bin_path or "",
            "host": self.host,
            "port": self.port,
            "fw_open": self.fw_open,
            "data_dir": self.data_dir,
            "uptime": uptime,
            "cpu": self.metrics.get("cpu"),
            "mem_mb": self.metrics.get("mem_mb"),
            "drives": None,
        }
        if self.running and pid:
            try:
                from .webdav_client import WebDavClient

                client = WebDavClient("http://%s:%d/dav" % (self.host, self.port), timeout=3)
                root = client.listdir("/")
                st["drives"] = sum(1 for e in root.get("entries", []) if e["is_dir"])
                st["space"] = {"used": root.get("used"), "avail": root.get("avail")}
            except Exception:
                st["drives"] = 0
        return st

    # -- 重启 -------------------------------------------------------------
    def restart(self, host=None, port=None, fw=None):
        """重启服务：停止后以（默认沿用旧参数或给定参数）重新启动。"""
        host = host or self.host or "127.0.0.1"
        port = port or self.port or 15244
        fw = True if fw is None else bool(fw)
        self.stop()
        self.start(str(host), int(port), fw)
        return self.state()

    # -- 日志尾部 -----------------------------------------------------------
    def tail_log(self, lines=200):
        """读取数据目录内最新日志文件的末尾 lines 行。返回 (文件名, 内容)。"""
        try:
            if not os.path.isdir(self.data_dir):
                return "", ""
            cands = []
            for name in os.listdir(self.data_dir):
                if name.lower().endswith(".log"):
                    p = os.path.join(self.data_dir, name)
                    if os.path.isfile(p):
                        cands.append((os.path.getmtime(p), p))
            if not cands:
                return "", ""
            _, path = max(cands)
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-max(int(lines), 1):]
            return os.path.basename(path), "".join(tail)
        except (OSError, ValueError):
            return "", ""

    # -- 配置导入 -----------------------------------------------------------
    def import_config(self, path):
        """导入 OpenList 配置（openlist.json）。校验通过后写入数据目录，重启后生效。"""
        import json

        if not os.path.isfile(path):
            raise ValueError("配置文件不存在：%s" % path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise ValueError("无法解析配置文件：%s" % e)
        if not isinstance(data, dict) or not isinstance(data.get("version"), str):
            raise ValueError("不是有效的 OpenList 配置文件（缺少 version 字段）。")
        os.makedirs(self.data_dir, exist_ok=True)
        target = os.path.join(self.data_dir, "openlist.json")
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
        self._log("已导入 OpenList 配置（%d 个驱动条目），重启服务后生效。" % len(data.get("drivers", [])))
        return target

    def open_web(self):
        return "http://%s:%d" % (self.host or "127.0.0.1", self.port or 0)