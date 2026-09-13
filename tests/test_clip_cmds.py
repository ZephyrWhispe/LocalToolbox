# -*- coding: utf-8 -*-
"""剪贴板自定义命令（v5.4 O4）测试：模板 CRUD 校验 / 执行核验（cmd /c echo 类无害命令）。

运行：python -m pytest tests/test_clip_cmds.py -q
"""

import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import DEFAULTS  # noqa: E402


class FakeMonitor:
    def __init__(self, entries):
        self._e = {e["id"]: e for e in entries}

    def get_entry(self, i):
        return self._e.get(int(i))


class FakeCfg:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, k, d=None):
        return self.data.get(k, d)

    def set(self, k, v):
        self.data[k] = v


def make_api(cmds, entries):
    from app.bridge.cliphist_api import ClipHistApi

    class Stub(ClipHistApi):
        pass

    api = Stub()
    api.cfg = FakeCfg({"clip_cmds": cmds})
    api._clip_monitor = FakeMonitor(entries)
    return api


E_TEXT = {"id": 1, "kind": "text", "text": "hello-clip-cmd", "img_path": "",
          "ts": 1000.0, "pinned": False}
E_IMG = {"id": 2, "kind": "image", "text": "", "img_path": "x.png",
         "ts": 2000.0, "pinned": False}


class TestClipCmdsCrud(unittest.TestCase):
    def test_01_default_empty(self):
        self.assertEqual(DEFAULTS["clip_cmds"], [])

    def test_02_get_empty(self):
        api = make_api([], [E_TEXT])
        r = api.clip_cmds_get()
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"], [])

    def test_03_set_ok(self):
        api = make_api([], [E_TEXT])
        r = api.clip_cmds_set([{"name": "百度", "cmd": "cmd /c start x{text}"}])
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"][0]["name"], "百度")
        self.assertEqual(api.cfg.data["clip_cmds"], r["data"])

    def test_04_set_cap5(self):
        api = make_api([], [E_TEXT])
        many = [{"name": "n%d" % i, "cmd": "echo %d" % i} for i in range(6)]
        r = api.clip_cmds_set(many)
        self.assertFalse(r["ok"])
        self.assertIn("5", r["err"])

    def test_05_set_missing_field(self):
        api = make_api([], [E_TEXT])
        r = api.clip_cmds_set([{"name": "only-name", "cmd": ""}])
        self.assertFalse(r["ok"])
        self.assertIn("第 1 条", r["err"])

    def test_06_set_bad_type(self):
        api = make_api([], [E_TEXT])
        self.assertFalse(api.clip_cmds_set("nope")["ok"])
        self.assertFalse(api.clip_cmds_set([42])["ok"])


class TestClipExecCmd(unittest.TestCase):
    def test_01_exec_echo_to_file(self):
        out = Path(__file__).parent / "_clip_cmd_out.txt"
        if out.exists():
            out.unlink()
        cmds = [{"name": "落盘",
                 "cmd": 'cmd /c echo {text} > "%s"' % out}]
        api = make_api(cmds, [E_TEXT])
        r = api.clip_exec_cmd(0, 1)
        self.assertTrue(r["ok"], r)
        # POPEN 异步：轮询等待文件出现（≤5s）
        content = ""
        for _ in range(50):
            if out.exists():
                content = out.read_text(encoding="gbk", errors="replace")
                if "hello-clip-cmd" in content:
                    break
            time.sleep(0.1)
        try:
            self.assertIn("hello-clip-cmd", content)
        finally:
            if out.exists():
                out.unlink()

    def test_02_image_entry_rejected(self):
        cmds = [{"name": "n", "cmd": "cmd /c echo {text}"}]
        api = make_api(cmds, [E_TEXT, E_IMG])
        r = api.clip_exec_cmd(0, 2)
        self.assertFalse(r["ok"])
        self.assertIn("文本", r["err"])

    def test_03_missing_entry(self):
        api = make_api([{"name": "n", "cmd": "echo {text}"}], [E_TEXT])
        r = api.clip_exec_cmd(0, 99)
        self.assertFalse(r["ok"])
        self.assertIn("不存在", r["err"])

    def test_04_bad_index(self):
        api = make_api([{"name": "n", "cmd": "echo {text}"}], [E_TEXT])
        self.assertFalse(api.clip_exec_cmd(5, 1)["ok"])
        self.assertFalse(api.clip_exec_cmd("x", 1)["ok"])

    def test_05_no_cmds(self):
        api = make_api([], [E_TEXT])
        r = api.clip_exec_cmd(0, 1)
        self.assertFalse(r["ok"])
        self.assertIn("不存在", r["err"])


if __name__ == "__main__":
    unittest.main()
