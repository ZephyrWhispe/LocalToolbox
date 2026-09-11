"""统一日志框架：JSON 单行格式 + 异步写入 + 大小轮转 + 分级存储。

设计要点
--------
1. **分级存储**
   - ``logs/app.log``    : 全级别（默认 INFO 起，可运行时调级），5 MiB × 3 份轮转
   - ``logs/error.log``  : 仅 ERROR/FATAL，1 MiB × 3 份轮转（快速定位错误）
2. **格式统一（一行一条 JSON，便于程序化解析）**
   ``{"ts","level","logger","func","line","dev","pid","path","msg"[,"event","tid","dur_ms","exc"]}``
   级别输出统一为 DEBUG / INFO / WARN / ERROR / FATAL。
3. **异步防阻塞**：QueueHandler + 后台线程（QueueListener）消费写出，主流程不等待 IO；
   队列满时自动丢弃低级别日志（``_dropped`` 计数），业务不受影响。
4. **运行时调级**：``set_level("DEBUG"|"INFO"|...)`` 影响 app.log 与根级别，不重启生效。
5. **用户标识**：``set_user(name)`` 由入口注入设备名，所有记录带 ``dev`` 字段。
"""

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener

# 归一化：logging 的 WARNING/CRITICAL → 文档级 WARN/FATAL
_LEVEL_TO_NAME = {logging.DEBUG: "DEBUG", logging.INFO: "INFO", logging.WARNING: "WARN",
                  logging.ERROR: "ERROR", logging.CRITICAL: "FATAL"}
_NAME_TO_LEVEL = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARN": logging.WARNING,
                  "WARNING": logging.WARNING, "ERROR": logging.ERROR, "FATAL": logging.CRITICAL,
                  "CRITICAL": logging.CRITICAL}

APP_NAME = "app"          # 根 logger 命名空间
LOG_DIR_NAME = "logs"
_APP_MAX_BYTES = 5 * 1024 * 1024    # app.log 5MiB
_APP_BACKUPS = 3
_ERR_MAX_BYTES = 1 * 1024 * 1024    # error.log 1MiB
_ERR_BACKUPS = 3

_state = {
    "queue": None,      # queue.Queue
    "listener": None,   # QueueListener
    "handlers": [],     # [app_handler, err_handler]
    "dropped": 0,       # 队列满丢弃计数
    "lock": threading.Lock(),
}
_user = {"name": ""}
_EXTRA_KEYS = ("event", "tid", "dur_ms")

# 供 prepare 格式化异常文本（Handler 无 formatException，Formatter 才有）
_EXC_FMT = logging.Formatter()


def set_user(name):
    """注入用户/设备标识（启动时由 main 调用）。"""
    _user["name"] = str(name or "")


def get_logger(name):
    """获取模块日志器：``logging.getLogger("app.<name>")``。"""
    return logging.getLogger(APP_NAME + ("." + name if name else ""))


class _NoBlockQueueHandler(QueueHandler):
    """队列满时丢弃（不阻塞主流程）；prepare 保留异常信息供文件 handler 序列化。

    Python 3.12 的 QueueHandler.prepare 会用默认 Formatter 把异常回溯拼进 msg
    再清空 exc_info，导致下游 _JsonFormatter 拿不到结构化异常——这里覆写，
    仅解析消息文本并缓存 exc_text，msg/exc_info 原样投递。
    """

    def prepare(self, record):
        record.message = record.getMessage()
        if record.exc_info:
            record.exc_text = record.exc_text or _EXC_FMT.formatException(record.exc_info)
        return record

    def enqueue(self, record):
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            _state["dropped"] += 1


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        d = {
            "ts": ts,
            "level": _LEVEL_TO_NAME.get(record.levelno, record.levelname),
            "logger": record.name,
            "func": record.funcName or "",
            "line": record.lineno or 0,
            "dev": _user["name"],
            "pid": os.getpid(),
            "msg": record.getMessage(),
        }
        for k in _EXTRA_KEYS:
            v = getattr(record, k, None)
            if v is not None:
                d[k] = v
        exc_text = getattr(record, "exc_text", None)
        if record.exc_info:
            exc_text = exc_text or self.formatException(record.exc_info)
        if exc_text:
            d["exc"] = exc_text
        return json.dumps(d, ensure_ascii=False, default=str)


class _RetryRotatingHandler(RotatingFileHandler):
    """轮转重试：Windows 上日志文件可能被杀软/索引器短暂占用，rename 失败重试。"""

    def doRollover(self):
        for _ in range(10):
            try:
                super().doRollover()
                return
            except PermissionError:
                time.sleep(0.05)
        super().doRollover()  # 最后一次，失败则抛给 logging 记录

    def rotate(self, source, dest):
        for _ in range(10):
            try:
                if os.path.exists(source):
                    os.rename(source, dest)
                return
            except PermissionError:
                time.sleep(0.05)
        os.rename(source, dest)  # 最后一次，失败则抛给 logging 记录


def init_logging(data_home=None, level="INFO"):
    """初始化日志（幂等）：创建目录、注册 handler、启动异步队列线程。

    :param data_home: 数据目录（%APPDATA%/LocalToolbox），日志落在其下 logs/ 子目录
    :param level: 初始级别 ("DEBUG"|"INFO"|"WARN"|"ERROR")
    """
    global APP_NAME
    log_dir = data_home and os.path.join(data_home, LOG_DIR_NAME) or LOG_DIR_NAME
    os.makedirs(log_dir, exist_ok=True)

    fmt = _JsonFormatter()
    app_log = os.path.join(log_dir, "app.log")
    err_log = os.path.join(log_dir, "error.log")

    app_handler = _RetryRotatingHandler(app_log, maxBytes=_APP_MAX_BYTES,
                                        backupCount=_APP_BACKUPS, encoding="utf-8")
    app_handler.setFormatter(fmt)
    app_handler.setLevel(_NAME_TO_LEVEL.get(str(level).upper(), logging.INFO))

    err_handler = _RetryRotatingHandler(err_log, maxBytes=_ERR_MAX_BYTES,
                                        backupCount=_ERR_BACKUPS, encoding="utf-8")
    err_handler.setFormatter(fmt)
    err_handler.setLevel(logging.ERROR)

    root = logging.getLogger(APP_NAME)
    root.setLevel(_NAME_TO_LEVEL.get(str(level).upper(), logging.INFO))
    root.propagate = False
    # 幂等：清掉旧 handler 防止重复 init（测试/重载）
    for h in list(root.handlers):
        root.removeHandler(h)

    q = queue.Queue(maxsize=10000)
    qh = _NoBlockQueueHandler(q)
    qh.setLevel(logging.DEBUG)
    root.addHandler(qh)

    listener = QueueListener(q, app_handler, err_handler, respect_handler_level=True)
    listener.start()

    with _state["lock"]:
        if _state["listener"]:
            _state["listener"].stop()
        for h in _state["handlers"]:
            try:
                h.close()
            except Exception:
                pass
        _state.update(queue=q, listener=listener,
                      handlers=[app_handler, err_handler], dropped=0)

    get_logger("__init__").info("日志系统就绪 level=%s dir=%s", str(level).upper(), log_dir)


def set_level(level):
    """运行时调整日志级别（DEBUG/INFO/WARN/ERROR，FATAL 不可关闭）。"""
    lvl = _NAME_TO_LEVEL.get(str(level).upper())
    if lvl is None:
        return False
    root = logging.getLogger(APP_NAME)
    root.setLevel(lvl)
    app_handler = _state["handlers"][0] if _state["handlers"] else None
    if app_handler:
        app_handler.setLevel(lvl)
    get_logger("__init__").info("日志级别调整为 %s", str(level).upper())
    return True


def get_status():
    """当前日志状态（供 log_status js_api）。"""
    app_log = _state["handlers"][0].baseFilename if _state["handlers"] else ""
    err_log = _state["handlers"][1].baseFilename if len(_state["handlers"]) > 1 else ""
    root = logging.getLogger(APP_NAME)
    return {
        "level": _LEVEL_TO_NAME.get(root.level, "INFO"),
        "app_log": app_log.replace("\\", "/"),
        "error_log": err_log.replace("\\", "/"),
        "dir": os.path.dirname(app_log).replace("\\", "/") if app_log else "",
        "app_size": os.path.getsize(app_log) if app_log and os.path.exists(app_log) else 0,
        "dropped": _state["dropped"],
    }


def stop_logging():
    """停止并刷新异步队列（进程退出前调用，保证日志落盘）。"""
    with _state["lock"]:
        listener = _state["listener"]
        _state["listener"] = None
    if listener:
        listener.stop()