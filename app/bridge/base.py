"""桥接基座：事件推送（Python -> JS）、就绪管理与 js_api 调用日志。

约定：
- js_api 方法名一律 ``页面_动作``（如 clip_start），全部返回
  {"ok": True, "data": ...} 或 {"ok": False, "err": "..."}；
- pywebview 6 每次 js_api 调用在独立线程执行，方法用普通 ``def`` 即可，
  慢操作直接阻塞本线程不会卡 UI；
- 后台线程推送事件用 ``self.emit(name, payload)``，在窗口加载完成前自动缓冲；
- 所有 js_api 调用自动记日志（INFO，不落参数值；异常 ERROR + 堆栈；慢调用 WARN），
  高频查询类方法记 DEBUG，避免刷屏。
"""

import json
import threading
import time

from ..core import logger as applog

# 命中这些前缀的方法走自动日志
_JS_API_PREFIXES = (
    "clip_", "km_", "xfer_", "cfg_", "app_", "devices_", "device_",
    "share_", "web_", "ftp_", "scan_", "network_", "file_", "shell_", "log_",
    "tool_", "shot_", "pan_", "rec_",
    "editor_", "pin_", "cliphist_", "combine_", "split_", "batch_",
    "scroll_", "upload_", "video_", "dns_",
    "memo_", "vault_", "backup_", "ocr_", "cap_",
)
# 末尾命中的高频查询方法记 DEBUG（调用次数多，避免 INFO 刷屏）
_QUIET_SUFFIX = ("_get_state", "_list", "_status", "_info", "_mapped",
                 "_letters", "_places", "_mapped_letters")
_SLOW_MS = 2000  # 超过此耗时记 WARN


class BridgeBase:
    def __init__(self):
        self._window = None
        self._ready = False
        self._pending_actions = []  # CLI 预填动作，加载后推给前端
        self._buf = []
        self._lock = threading.Lock()

    # -- js_api 调用自动日志 ---------------------------------------------
    def __getattribute__(self, name):
        attr = object.__getattribute__(self, name)
        if (
            not name.startswith("_")
            and callable(attr)
            and any(name.startswith(p) for p in _JS_API_PREFIXES)
        ):
            return object.__getattribute__(self, "_wrap_js_api")(name, attr)
        return attr

    def _wrap_js_api(self, name, fn):
        def wrapper(*args, **kwargs):
            log = applog.get_logger("jsapi")
            t0 = time.time()
            quiet = name.endswith(_QUIET_SUFFIX)
            (log.debug if quiet else log.info)(
                "js_api 调用 %s（参数 %d 个）", name, len(args) + len(kwargs))
            try:
                result = fn(*args, **kwargs)
            except Exception:
                log.error("js_api 异常 %s", name, exc_info=True)
                raise
            dt_ms = (time.time() - t0) * 1000
            if dt_ms >= _SLOW_MS:
                log.warning("js_api 慢调用 %s（%.0f ms）", name, dt_ms)
            return result

        return wrapper

    # -- 生命周期 ------------------------------------------------------
    def attach(self, window):
        self._window = window
        # pywebview 6 事件回调：0 参函数直接调用
        window.events.loaded += lambda: self._on_loaded()
        window.events.closed += lambda: setattr(self, "_ready", False)

    def _on_loaded(self):
        # v5.1c：置位与排空同锁完成——避免后台线程在「置位」与「排空」之间
        # emit 绕过缓冲直达 _push，导致事件先于缓冲事件到达前端
        with self._lock:
            buffered, self._buf = self._buf, []
            pending, self._pending_actions = self._pending_actions, []
            self._ready = True
        for item in buffered:
            self._push(item[0], item[1])
        for action in pending:
            self._push("cli_action", action)

    def set_pending_action(self, action, path):
        self._pending_actions.append({"action": action, "path": path})

    # -- 事件推送 ------------------------------------------------------
    def _push(self, name, payload):
        if self._window is None:
            return
        import logging as _logging
        _log = _logging.getLogger("bridge.push")
        try:
            # 大 payload 如截图的 data URL 可能很大（数 MB），
            # 跳过 JSON 序列化的日志输出体积，但 keep 序列化本身
            data = json.dumps({"event": name, "data": payload},
                              ensure_ascii=False, default=str)
            self._window.evaluate_js("window.__push(%s)" % data)
        except Exception as e:
            _log.warning("事件推送失败 %s: %s", name, e, exc_info=True)

    def emit(self, name, payload=None):
        """线程安全：向前端推送事件，未就绪时先缓冲。

        v5.1c：_ready 判定与入缓冲同锁，防止「检查未就绪 → loaded 排空 →
        才 append」的窗口把事件永久滞留缓冲。
        """
        with self._lock:
            if self._ready:
                push_now = True
            else:
                self._buf.append((name, payload))
                push_now = False
        if push_now:
            self._push(name, payload)

    def emit_log(self, msg):
        self.emit("app_log", str(msg))
