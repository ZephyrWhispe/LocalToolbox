"""实时流量统计：系统网卡计数采样（对接 v2rayN「实时监控与统计」）。

对齐设计：不引入 grpcio 等新依赖。通过 PowerShell Get-NetAdapterStatistics
读取各网卡累计收发字节，相邻两次采样差值 / 时间间隔 = 实时上行/下行速率。
对核心类型无感（xray / v2ray / sing-box 均可用）；PowerShell 不可用或
执行失败时降级（回调不再产生数据，调用方展示“统计不可用”）。
"""

import subprocess

# 窗口化进程（console=False 的 exe）里跑 PowerShell 会反复闪黑窗，必须禁用
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
import threading
import time

_CMD = (
    "$s = Get-NetAdapterStatistics; "
    "$r = ($s | Measure-Object -Property ReceivedBytes -Sum).Sum; "
    "$t = ($s | Measure-Object -Property SentBytes -Sum).Sum; "
    "Write-Output ('{0},{1}' -f $r, $t)"
)


def sample_bytes():
    """读取系统累计收发字节 (rx, tx)；失败返回 None。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _CMD],
            capture_output=True, text=True, timeout=8,
            creationflags=_NOWIN,
        )
        if out.returncode != 0:
            return None
        lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
        for ln in reversed(lines):
            if "," in ln:
                r, t = ln.rsplit(",", 1)
                try:
                    return int(r), int(t)
                except ValueError:
                    return None
        return None
    except Exception:
        return None


class TrafficMonitor:
    """采样线程：每 interval 秒回调一次 (up_bps, down_bps)。

    up 为上行（本机发出的字节速率），down 为下行（本机接收的字节速率）。
    """

    def __init__(self, callback, interval=2.0):
        self._cb = callback
        self._interval = max(interval, 1.0)
        self._stop = threading.Event()
        self._thread = None
        self._last = None
        self._last_t = None
        self.ok = True
        self.last = {"up": 0.0, "down": 0.0}

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self):
        self._last = sample_bytes()
        self._last_t = time.monotonic()
        while not self._stop.wait(self._interval):
            now = time.monotonic()
            s = sample_bytes()
            if s is None:
                self.ok = False
                continue
            self.ok = True
            if self._last:
                dt = max(now - self._last_t, 0.5)
                rx, tx = s
                up = max(0.0, (tx - self._last[1]) / dt)
                down = max(0.0, (rx - self._last[0]) / dt)
                self.last = {"up": up, "down": down}
                try:
                    self._cb(up, down)
                except Exception:
                    pass
            self._last = s
            self._last_t = now