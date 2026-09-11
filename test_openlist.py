"""OpenList 进程托管测试。

运行：python test_openlist.py
"""

import json
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from app.core import openlist as ol
from app.core import firewall

TMP = tempfile.mkdtemp(prefix="openlist-test-")


class FakeProc:
    def __init__(self, code=None):
        self.code = code
        self.returncode = code
        self.pid = 12345
        self.killed = False
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 0
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.code = -9
        self.returncode = -9

    def wait(self, timeout=None):
        return self.code


def make_mgr():
    mgr = ol.OpenListManager(log_callback=lambda m: None)
    mgr.data_dir = os.path.join(TMP, "ol-data")
    mgr.metrics_enabled = False  # 单测默认关后台采样线程（避免真实跑 PowerShell）
    return mgr


class TestOpenList(unittest.TestCase):
    def setUp(self):
        ol.DEFAULT_DATA_DIR = os.path.join(TMP, "ol-default")

    def test_01_detect_bin(self):
        bdir = os.path.join(TMP, "bin1")
        os.makedirs(bdir, exist_ok=True)
        exe = os.path.join(bdir, "openlist.exe")
        open(exe, "wb").close()
        mgr = make_mgr()
        path = mgr.detect_bin(exe)
        self.assertEqual(path, os.path.abspath(exe))

    def test_02_detect_missing(self):
        mgr = make_mgr()
        # 隔离环境：真机可能装有真实 openlist.exe（数据目录/程序目录候选命中），
        # 换成哑候选后仅探测不存在的配置路径，才能稳定触发 ValueError
        with mock.patch.object(ol, "_BIN_CANDIDATES",
                               (os.path.join(TMP, "no-fallback", "openlist.exe"),)), \
                mock.patch.object(ol, "runtime_bin_candidate", return_value=""):
            with self.assertRaises(ValueError):
                mgr.detect_bin(os.path.join(TMP, "nope.exe"))

    def test_03_invalid_port(self):
        mgr = make_mgr()
        with self.assertRaises(ValueError):
            mgr.start(port=0)

    @mock.patch.object(firewall, "add_ports", return_value=(True, ""))
    @mock.patch.object(firewall, "remove", return_value=(True, ""))
    def test_04_start_stop(self, _rm, _add):
        bdir = os.path.join(TMP, "bin2")
        os.makedirs(bdir, exist_ok=True)
        exe = os.path.join(bdir, "openlist.exe")
        open(exe, "wb").close()

        fake = FakeProc()
        mgr = make_mgr()
        mgr.bin_path = exe
        with mock.patch("subprocess.Popen", return_value=fake) as popen, \
             mock.patch.object(mgr, "_tcp_probe", return_value=True):
            mgr.start("127.0.0.1", 15244, fw=True)
        self.assertTrue(mgr.running)
        self.assertTrue(mgr.healthy)
        self.assertTrue(popen.called)
        self.assertEqual(popen.call_args[0][0][0], exe)
        _add.assert_called_once_with("LocalToolboxOpenList", [(15244, "TCP")])
        st = mgr.state()
        self.assertTrue(st["running"])
        self.assertEqual(st["port"], 15244)

        mgr.stop()
        self.assertFalse(mgr.running)
        _rm.assert_called_once()
        self.assertTrue(fake.terminated)

    @mock.patch.object(firewall, "add_ports", return_value=(True, ""))
    def test_05_exit_detected(self, _add):
        bdir = os.path.join(TMP, "bin3")
        os.makedirs(bdir, exist_ok=True)
        exe = os.path.join(bdir, "openlist.exe")
        open(exe, "wb").close()
        mgr = make_mgr()
        mgr.bin_path = exe
        fake = FakeProc(code=None)
        with mock.patch("subprocess.Popen", return_value=fake), \
             mock.patch.object(mgr, "_tcp_probe", return_value=True):
            mgr.start("127.0.0.1", 15244, fw=False)
        fake.code = 1  # 进程退出
        mgr._monitor_loop()  # 模拟监控线程一轮（proc.poll() 返回 1）
        self.assertFalse(mgr.running)
        self.assertEqual(mgr.status, "stopped")

    @mock.patch.object(firewall, "add_ports", return_value=(True, ""))
    def test_06_early_exit_raises(self, _add):
        bdir = os.path.join(TMP, "bin4")
        os.makedirs(bdir, exist_ok=True)
        exe = os.path.join(bdir, "openlist.exe")
        open(exe, "wb").close()
        mgr = make_mgr()
        mgr.bin_path = exe
        fake = FakeProc(code=2)  # 启动即退出
        with mock.patch("subprocess.Popen", return_value=fake):
            with self.assertRaises(ValueError):
                mgr.start("127.0.0.1", 15244, fw=False)
        self.assertFalse(mgr.running)

    def test_07_import_config(self):
        mgr = make_mgr()
        # 非 JSON
        bad = os.path.join(TMP, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{not json")
        with self.assertRaises(ValueError):
            mgr.import_config(bad)
        # 缺少 version
        nover = os.path.join(TMP, "nover.json")
        with open(nover, "w", encoding="utf-8") as f:
            json.dump({"drivers": []}, f)
        with self.assertRaises(ValueError):
            mgr.import_config(nover)
        # 合法
        good = os.path.join(TMP, "good.json")
        with open(good, "w", encoding="utf-8") as f:
            json.dump({"version": "0.9", "drivers": [{"id": "d1"}]}, f)
        target = mgr.import_config(good)
        self.assertTrue(os.path.isfile(target))
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["version"], "0.9")

    # -- v3.2：重启 / 运行时长 / 性能采样 / 日志尾部 / 退出回调 --------------
    def _make_exe(self, tag):
        bdir = os.path.join(TMP, "bin" + tag)
        os.makedirs(bdir, exist_ok=True)
        exe = os.path.join(bdir, "openlist.exe")
        open(exe, "wb").close()
        return exe

    @mock.patch.object(firewall, "add_ports", return_value=(True, ""))
    @mock.patch.object(firewall, "remove", return_value=(True, ""))
    def test_08_restart(self, _rm, _add):
        exe = self._make_exe("8")
        mgr = make_mgr()
        mgr.bin_path = exe
        procs = iter([FakeProc(), FakeProc()])
        with mock.patch("subprocess.Popen", side_effect=lambda *a, **k: next(procs)) as popen, \
             mock.patch.object(mgr, "_tcp_probe", return_value=True):
            mgr.start("127.0.0.1", 15244, fw=True)
            st = mgr.restart(port=15245)
        self.assertTrue(mgr.running)
        self.assertTrue(mgr.healthy)
        self.assertEqual(mgr.port, 15245)
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(st["port"], 15245)
        self.assertIsNotNone(st["uptime"])
        self.assertIsNone(st["cpu"])  # 采样关闭时指标为空值
        mgr.stop()

    @mock.patch.object(firewall, "add_ports", return_value=(True, ""))
    def test_09_uptime_state(self, _add):
        exe = self._make_exe("9")
        mgr = make_mgr()
        mgr.bin_path = exe
        fake = FakeProc()
        with mock.patch("subprocess.Popen", return_value=fake), \
             mock.patch.object(mgr, "_tcp_probe", return_value=True):
            mgr.start("127.0.0.1", 15244, fw=False)
        st = mgr.state()
        self.assertTrue(st["running"])
        self.assertIsNotNone(st["uptime"])
        self.assertEqual(st["host"], "127.0.0.1")
        mgr.stop()
        st2 = mgr.state()
        self.assertFalse(st2["running"])
        self.assertIsNone(st2["uptime"])

    @mock.patch.object(firewall, "add_ports", return_value=(True, ""))
    def test_10_metrics_loop(self, _add):
        exe = self._make_exe("10")
        mgr = make_mgr()
        mgr.bin_path = exe
        mgr.metrics_enabled = True
        fake = FakeProc()  # poll() -> None：进程存活
        mgr.proc = fake

        def fake_run(cmd, **kw):
            return SimpleNamespace(
                returncode=0,
                stdout='{"TotalProcessorTime": "4.0", "WorkingSet64": 3145728}',
            )

        with mock.patch("subprocess.run", side_effect=fake_run):
            t = threading.Thread(target=mgr._metrics_loop, daemon=True)
            t.start()
            time.sleep(2.5)  # 首轮 wait(2.0) 后采样
            mgr._metrics_stop.set()
            t.join(1.0)
        self.assertIsNotNone(mgr.metrics.get("cpu"))
        self.assertIsNotNone(mgr.metrics.get("mem_mb"))
        self.assertAlmostEqual(mgr.metrics["mem_mb"], 3.0, places=1)
        mgr.stop()

    def test_11_tail_log(self):
        mgr = make_mgr()
        # 空数据目录
        name, content = mgr.tail_log(5)
        self.assertEqual((name, content), ("", ""))
        # 写入日志文件
        os.makedirs(mgr.data_dir, exist_ok=True)
        log_path = os.path.join(mgr.data_dir, "openlist.log")
        lines = ["line%d" % i for i in range(30)]
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        name, content = mgr.tail_log(5)
        self.assertEqual(name, "openlist.log")
        self.assertIn("line29", content)
        self.assertEqual(len(content.splitlines()), 5)

    @mock.patch.object(firewall, "add_ports", return_value=(True, ""))
    def test_12_exit_callback(self, _add):
        exe = self._make_exe("12")
        mgr = make_mgr()
        mgr.bin_path = exe
        fired = []
        mgr.exit_callback = lambda: fired.append(True)
        fake = FakeProc(code=None)
        with mock.patch("subprocess.Popen", return_value=fake), \
             mock.patch.object(mgr, "_tcp_probe", return_value=True):
            mgr.start("127.0.0.1", 15244, fw=False)
        fake.code = 3  # 进程退出
        mgr._monitor_loop()
        self.assertEqual(fired, [True])
        self.assertFalse(mgr.running)

    # -- 启动命令行 / 配置文件（历史缺陷回归） --------------------------------
    def _fresh_mgr(self, tag):
        mgr = make_mgr()
        mgr.data_dir = os.path.join(TMP, "ol-data-" + tag)
        return mgr

    def test_13_cmd_uses_server_subcommand(self):
        """启动命令必须是 `openlist server --data <目录>`。

        历史缺陷：写成 `--data <目录> start --addr host:port`——openlist.exe
        没有 --addr 参数（报 unknown flag 立即退出），且 start 是「静默后台
        启动」（前台进程立刻结束，托管/健康检查全部失效）。
        """
        exe = self._make_exe("13")
        mgr = self._fresh_mgr("13")
        mgr.bin_path = exe
        with mock.patch.object(mgr, "_tcp_probe", return_value=True), \
             mock.patch("subprocess.Popen", return_value=FakeProc()) as popen:
            mgr.start("127.0.0.1", 15244, fw=False)
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[0], exe)
        self.assertEqual(cmd[1], "server")
        self.assertIn("--data", cmd)
        self.assertNotIn("--addr", cmd)
        self.assertNotIn("start", cmd[1:])   # 不得使用 start 子命令
        mgr.stop()

    def test_14_config_written_with_scheme(self):
        """监听地址/端口写入 <data>/config.json 的 scheme 段（该程序无 --addr）。"""
        exe = self._make_exe("14")
        mgr = self._fresh_mgr("14")
        mgr.bin_path = exe
        data_dir = os.path.join(mgr.data_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        # 已有配置：必须保留其它字段，仅覆盖 scheme
        with open(os.path.join(data_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"site_url": "http://example.com",
                       "scheme": {"address": "0.0.0.0", "http_port": 5244,
                                  "https_port": -1}}, f)
        with mock.patch.object(mgr, "_tcp_probe", return_value=True), \
             mock.patch("subprocess.Popen", return_value=FakeProc()):
            mgr.start("127.0.0.1", 15245, fw=False)
        with open(os.path.join(data_dir, "config.json"), encoding="utf-8") as f:
            conf = json.load(f)
        self.assertEqual(conf["scheme"]["address"], "127.0.0.1")
        self.assertEqual(conf["scheme"]["http_port"], 15245)
        self.assertEqual(conf["scheme"]["https_port"], -1)   # 保留原字段
        self.assertEqual(conf["site_url"], "http://example.com")
        mgr.stop()

    def test_15_config_created_when_missing(self):
        exe = self._make_exe("15")
        mgr = self._fresh_mgr("15")
        mgr.bin_path = exe
        with mock.patch.object(mgr, "_tcp_probe", return_value=True), \
             mock.patch("subprocess.Popen", return_value=FakeProc()):
            mgr.start("127.0.0.1", 15246, fw=False)
        path = os.path.join(mgr.data_dir, "data", "config.json")
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as f:
            conf = json.load(f)
        self.assertEqual(conf["scheme"], {"address": "127.0.0.1", "http_port": 15246})
        mgr.stop()

    def test_16_pump_output_forwards_and_strips_ansi(self):
        """子进程输出转发到日志回调（首次运行的初始管理员密码可见），并去掉 ANSI。"""
        mgr = self._fresh_mgr("16")
        logs = []
        mgr.log_callback = logs.append
        lines = [
            b"\x1b[36mINFO\x1b[0m[2026] Successfully created the admin user "
            b"and the initial password is: xNE1ae0F\n",
            b"start HTTP server @ 127.0.0.1:15244\n",
            b"",   # 空行忽略
        ]
        proc = SimpleNamespace(stdout=SimpleNamespace(
            readline=(lambda it=iter(lines): next(it, b"")),
            close=lambda: None,
        ))
        mgr._pump_output(proc)
        text = "\n".join(logs)
        self.assertIn("initial password is: xNE1ae0F", text)
        self.assertIn("start HTTP server", text)
        self.assertNotIn("\x1b[", text)


if __name__ == "__main__":
    unittest.main()