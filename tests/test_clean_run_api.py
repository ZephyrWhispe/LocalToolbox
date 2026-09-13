# -*- coding: utf-8 -*-
"""垃圾清理 API（v5.4 修复回归）测试：回收站确认后随批次提交不再被拒。

运行：python -m pytest tests/test_clean_run_api.py -q
"""

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))


class TestCleanRunApi(unittest.TestCase):
    def _api(self):
        import threading

        from app.bridge.optimize_api import OptimizeApi
        from app.core.config import AppConfig

        class Stub(OptimizeApi):
            pass

        api = Stub()
        api.cfg = AppConfig()
        api._init_optimize()
        return api

    def test_01_recycle_accepted_with_batch(self):
        """前端二次确认后 recycle 随批次提交：不再被"单独执行"拦截。"""
        api = self._api()
        done = {}
        with mock.patch("app.core.optimizer.clean_run",
                        return_value=(True, "")) as m:
            r = api.opt_clean_run(["recycle", "dns", "user-temp"])
            self.assertTrue(r["ok"], r)
            # 异步 worker：等待完成（mock 立即返回，1s 足够）
            for _ in range(50):
                if "opt_done" in done:
                    break
                time.sleep(0.05)
            m.assert_called_once()
            ids = m.call_args[0][0]
            self.assertIn("recycle", ids)

    def test_02_empty_rejected(self):
        api = self._api()
        r = api.opt_clean_run([])
        self.assertFalse(r["ok"])
        self.assertIn("勾选", r["err"])

    def test_03_admin_requirement_message(self):
        """非管理员提交系统级清理项 → 明确中文提示而非异常。"""
        api = self._api()
        with mock.patch("app.core.optimizer.clean_run",
                        return_value=(False, "部分清理项需要管理员权限，请以管理员身份运行")):
            r = api.opt_clean_run(["win-temp"])
            self.assertTrue(r["ok"])   # 异步受理成功
            # 结果经 opt_done 事件送达（此处仅验证受理链路不抛异常）


if __name__ == "__main__":
    unittest.main()
