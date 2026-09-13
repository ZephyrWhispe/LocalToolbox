"""自动检查更新（ShareX 式"保持最新"理念）：定时查询 GitHub 最新版本，
可选自动下载新版本二进制。

设计：
- AutoUpdateChecker 守护线程：启动后延迟 30s 首查，之后每 update_interval_hours
  重复一次；stop() 置位事件收束（进程退出由 daemon 兜底）；
- 范围 scope：["openlist", "rclone", "core"]——"core" 动态解析为当前代理核心
  （cfg v2ray_core → xray / sing-box / v2ray）；
- 判定：cached_version 非空且 != latest 才算"有更新"（未下载过的组件不提示）；
- 自动下载：有更新且开启时后台线程逐个 download_binary（幂等，同版本复用缓存），
  进度经 emit("update_progress", ...) 推送，完成后 emit("update_found")；
  下载不热替换运行中的服务，提示重启生效。
"""

import threading
import time

from . import bindl
from . import logger as applog

log = applog.get_logger("auto-update")

FIRST_DELAY_SECONDS = 30
DEFAULT_INTERVAL_HOURS = 24
DEFAULT_SCOPE = ("openlist", "rclone", "core")


def resolve_kinds(scope, get_cfg):
    """scope 数组 → bindl kind 数组；"core" 解析为当前代理核心。"""
    core_kind = str(get_cfg().get("v2ray_core") or "xray")
    core_kind = core_kind if core_kind in bindl._REPOS else "xray"
    out = []
    for item in scope or DEFAULT_SCOPE:
        if item == "core":
            if core_kind not in out:
                out.append(core_kind)
        elif item in bindl._REPOS and item not in out:
            out.append(item)
    return out


class AutoUpdateChecker:
    """定时检查更新控制器：start / stop / check_once。"""

    def __init__(self, get_cfg, emit, log=None):
        self._get_cfg = get_cfg
        self._emit = emit or (lambda name, data: None)
        self._log = log or (lambda m: None)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.last_check = None       # 最近一次检查时间戳（成功时更新）
        self.last_results = None     # 最近一次检查结果（供设置页展示）

    # -- 配置 -----------------------------------------------------------
    def _settings(self):
        c = self._get_cfg()
        scope = c.get("update_scope")
        if not isinstance(scope, list) or not scope:
            scope = list(DEFAULT_SCOPE)
        try:
            hours = max(1, int(c.get("update_interval_hours") or DEFAULT_INTERVAL_HOURS))
        except (TypeError, ValueError):
            hours = DEFAULT_INTERVAL_HOURS
        return {
            "auto_check": bool(c.get("update_auto_check")),
            "auto_download": bool(c.get("update_auto_download")),
            "scope": scope,
            "interval_hours": hours,
        }

    # -- 生命周期 ---------------------------------------------------------
    def start(self):
        """按 cfg 决定是否启动检查线程（已运行则跳过）。"""
        if not self._settings()["auto_check"]:
            self._log("自动检查更新未开启")
            return False
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="auto-update")
            self._thread.start()
            self._log("自动检查更新已启动（每 %d 小时一次）"
                      % self._settings()["interval_hours"])
            return True

    def stop(self):
        self._stop.set()
        self._thread = None

    def restart(self):
        self.stop()
        time.sleep(0.05)
        return self.start()

    def _run(self):
        # 首查延迟 30s：避开启动高峰（服务恢复 / 界面加载）
        if self._stop.wait(FIRST_DELAY_SECONDS):
            return
        while not self._stop.is_set():

            try:
                self.check_once(auto=True)
            except Exception as e:
                self._log("自动检查更新失败：%s" % e)
            st = self._settings()
            if not st["auto_check"]:   # 运行中被关闭 → 收束
                break
            if self._stop.wait(st["interval_hours"] * 3600):
                return

    # -- 检查与下载 ---------------------------------------------------------
    def check_once(self, auto=False):
        """检查 scope 内全部组件；返回 [{kind, latest, cached, has_update}]。

        auto=True（定时触发）：发现更新且开启自动下载时后台逐个下载；
        auto=False（手动）：仅检查返回，由调用方（设置页）决定是否下载。
        网络失败逐项记入 error 字段，不中断其它项。
        """
        st = self._settings()
        kinds = resolve_kinds(st["scope"], self._get_cfg)
        results = []
        for kind in kinds:
            try:
                latest = bindl.latest(kind)["version"]
                cached = bindl.cached_version(kind)
                results.append({
                    "kind": kind, "latest": latest, "cached": cached,
                    "has_update": bool(cached and latest != cached),
                })
            except Exception as e:
                results.append({"kind": kind, "error": str(e)})
        self.last_check = time.time()
        self.last_results = results

        pending = [r for r in results if r.get("has_update")]
        if pending and auto and st["auto_download"]:
            threading.Thread(target=self._auto_download, args=(pending,),
                             daemon=True, name="auto-update-dl").start()
        elif pending:
            self._log("发现新版本：%s" % ", ".join(
                "%s %s" % (r["kind"], r["latest"]) for r in pending))
        self._emit("update_found", {
            "auto": auto, "results": results,
            "downloaded": bool(pending and auto and st["auto_download"]),
        })
        return results

    def _auto_download(self, pending):
        """后台逐个下载新版本（幂等缓存；进度与结果经事件推送）。"""
        ok, fail = [], []
        for r in pending:
            kind = r["kind"]
            try:
                def cb(done, total, kind=kind):
                    self._emit("update_progress", {
                        "kind": kind, "done": done, "total": total,
                        "status": "running",
                    })
                res = bindl.download_binary(kind, progress_cb=cb)
                ok.append("%s %s" % (kind, res["version"]))
                self._emit("update_progress", {
                    "kind": kind, "done": 0, "total": 0, "status": "done",
                })
                self._log("%s 已更新到 %s（重启对应服务后生效）" % (kind, res["version"]))
            except Exception as e:
                fail.append("%s：%s" % (kind, e))
                self._log("%s 自动下载失败：%s" % (kind, e))
                self._emit("update_progress", {
                    "kind": kind, "done": 0, "total": 0, "status": "error",
                    "err": str(e),
                })
        if ok:
            self._emit("update_downloaded", {"ok": ok, "fail": fail})
