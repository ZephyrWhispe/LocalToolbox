# -*- coding: utf-8 -*-
"""统一日志框架测试：

- 格式：一行一条 JSON，字段齐全（ts/level/logger/func/line/dev/pid/msg）；
- 分级存储：app.log 全量，error.log 仅 ERROR/FATAL；
- 级别归一：WARNING→WARN、CRITICAL→FATAL；
- 运行时调级：WARN 后 INFO 不再落盘；
- 用户标识：set_user 注入 dev 字段；
- 大小轮转：缩小 maxBytes 后日志按份数轮转且不丢失；
- 异步不阻塞：队列满丢弃并有计数（dropped）。
运行：python test_logger.py
"""

import json
import logging
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core import logger as applog  # noqa: E402


def _tail(path, n=100):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.readlines()[-n:]
    except FileNotFoundError:
        return []


def _lines(path):
    out = []
    for ln in _tail(path):
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:
            out.append({"bad": ln})
    return out


class LoggerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="log-")
        self.log_dir = os.path.join(self.tmp, "logs")
        self.old_max = applog._APP_MAX_BYTES
        self.old_err_max = applog._ERR_MAX_BYTES
        applog._APP_MAX_BYTES = 64 * 1024   # 轮转测试用小体积
        applog._ERR_MAX_BYTES = 64 * 1024
        applog.init_logging(self.tmp, level="DEBUG")
        applog.set_user("dev-单元测试")

    def tearDown(self):
        applog.stop_logging()
        applog._APP_MAX_BYTES = self.old_max
        applog._ERR_MAX_BYTES = self.old_err_max
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _app(self):
        return os.path.join(self.log_dir, "app.log")

    def _err(self):
        return os.path.join(self.log_dir, "error.log")

    def _wait_written(self, path, count=1, timeout=5):
        """异步写盘：轮询直到文件行数满足（QueueListener 后台线程）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(_lines(path)) >= count:
                return True
            time.sleep(0.05)
        return False

    # -- 基本字段与格式 --------------------------------------------
    def test_01_json_fields(self):
        log = applog.get_logger("testmod")
        log.info("你好 %s", "世界")
        deadline = time.time() + 5
        while time.time() < deadline:
            recs = [r for r in _lines(self._app()) if r.get("logger") == "app.testmod"]
            if recs:
                break
            time.sleep(0.05)
        self.assertTrue(recs, "testmod 日志未落盘")
        rec = recs[0]
        for key in ("ts", "level", "logger", "func", "line", "dev", "pid", "msg"):
            self.assertIn(key, rec, "缺少字段 %s" % key)
        self.assertEqual(rec["level"], "INFO")
        self.assertEqual(rec["msg"], "你好 世界")
        self.assertEqual(rec["dev"], "dev-单元测试")
        self.assertGreater(rec["line"], 0)

    def test_02_level_normalize(self):
        log = applog.get_logger("levels")
        log.warning("注意")
        log.critical("致命")
        self.assertTrue(self._wait_written(self._app(), 2))
        recs = [r for r in _lines(self._app()) if r.get("logger") == "app.levels"]
        names = {r["msg"]: r["level"] for r in recs}
        self.assertEqual(names.get("注意"), "WARN")
        self.assertEqual(names.get("致命"), "FATAL")

    # -- 分级存储 ---------------------------------------------------
    def test_03_error_log_only_error(self):
        log = applog.get_logger("split")
        log.debug("调试")
        log.warning("警告")
        log.error("错误", exc_info=False)
        log.critical("致命")
        self.assertTrue(self._wait_written(self._app(), 4))
        err_recs = _lines(self._err())
        err_levels = {r["level"] for r in err_recs}
        self.assertTrue(err_levels <= {"ERROR", "FATAL"})
        names = {r["msg"]: r["level"] for r in err_recs}
        self.assertEqual(names.get("错误"), "ERROR")
        self.assertEqual(names.get("致命"), "FATAL")
        self.assertNotIn("警告", names)
        self.assertNotIn("调试", names)

    def test_04_exception_exc_field(self):
        log = applog.get_logger("exc")
        try:
            1 / 0
        except ZeroDivisionError:
            log.error("除零", exc_info=True)
        self.assertTrue(self._wait_written(self._err(), 1))
        rec = next(r for r in _lines(self._app()) if r.get("logger") == "app.exc")
        self.assertIn("ZeroDivisionError", rec["exc"])

    # -- 运行时调级 --------------------------------------------------
    def test_05_set_level_runtime(self):
        log = applog.get_logger("rt")
        log.warning("w1")
        self.assertTrue(applog.set_level("WARN"))
        log.info("不应落盘")
        self.assertFalse(applog.set_level("BOGUS"))  # 非法级别返回 False
        log.warning("w2")
        # 等待 w2 落盘
        deadline = time.time() + 5
        while time.time() < deadline:
            msgs = {r["msg"] for r in _lines(self._app()) if r.get("logger") == "app.rt"}
            if "w2" in msgs:
                break
            time.sleep(0.05)
        msgs = {r["msg"] for r in _lines(self._app()) if r.get("logger") == "app.rt"}
        self.assertIn("w1", msgs)
        self.assertNotIn("不应落盘", msgs)
        self.assertIn("w2", msgs)

    # -- 用户标识 --------------------------------------------
    def test_06_set_user(self):
        applog.set_user("rename-设备")
        log = applog.get_logger("user")
        log.info("ui")
        self.assertTrue(self._wait_written(self._app(), 1))
        rec = next(r for r in _lines(self._app()) if r.get("logger") == "app.user")
        self.assertEqual(rec["dev"], "rename-设备")

    # -- 大小轮转 ----------------------------------------------------
    def test_07_rotation(self):
        log = applog.get_logger("rot")
        # 写 ~300KB > 64KB，应当触发多份轮转
        for i in range(3000):
            log.debug("填充日志 %d %s", i, "x" * 80)
        # 轮询直到目录中出现 app.log.* 备份（异步监听线程写盘 + rotate）
        deadline = time.time() + 15
        while time.time() < deadline:
            names = os.listdir(self.log_dir)
            backups = [f for f in names if f.startswith("app.log")]
            if len(backups) > 1:
                break
            time.sleep(0.05)
        dirs = os.listdir(self.log_dir)
        backups = [f for f in dirs if f == "app.log" or f.startswith("app.log.")]
        self.assertGreater(len(backups), 1, "应有轮转备份：%s" % dirs)
        # 备份文件也应是合法 JSON（解析不抛异常即可）
        for name in backups:
            for rec in _lines(os.path.join(self.log_dir, name)):
                if "bad" in rec:
                    self.fail("轮转文件 %s 中有非法 JSON 行" % name)

    # -- 队列丢弃计数 ------------------------------------------------
    def test_08_dropped_counter(self):
        import queue as _queue

        # 用独立队列验证（不污染真实共享队列，避免 Listener 处理到裸对象）
        q = _queue.Queue(maxsize=2)
        q.put_nowait(object())
        q.put_nowait(object())
        h = applog._NoBlockQueueHandler(q)
        before = applog._state["dropped"]
        # 队列已满 → enqueue 丢弃该条并计数（不阻塞、不抛异常）
        h.emit(logging.LogRecord("app.dummy", logging.DEBUG, __file__, 1,
                                 "d", tuple(), None))
        self.assertEqual(applog._state["dropped"], before + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)