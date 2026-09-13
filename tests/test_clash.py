"""Clash（mihomo）模块测试：节点转换 / 策略组 / 配置生成 / 内核发现。

运行：python test_clash.py
"""

import json
import os
import tempfile
import time
import unittest
from unittest import mock

from app.core import bindl, clash_core as cc, routing_core as rc

TMP = tempfile.mkdtemp(prefix="clash-test-")

NODE_ANYTLS = {"type": "anytls", "addr": "a.example.com", "port": 443,
               "id": "pw", "sni": "s.example.com", "remark": "台湾01"}
NODE_TROJAN = {"type": "trojan", "addr": "b.example.com", "port": 8443,
               "id": "pw2", "sni": "t.example.com", "network": "ws",
               "path": "/ws", "host": "cdn.example.com", "remark": "香港01"}
NODE_VLESS = {"type": "vless", "addr": "c.example.com", "port": 443,
              "id": "uuid-1", "tls": "tls", "sni": "v.example.com",
              "network": "ws", "path": "/v", "host": "v.example.com",
              "flow": "xtls-rprx-vision", "remark": "日本01"}
NODE_HY2 = {"type": "hy2", "addr": "d.example.com", "port": 443, "id": "pw3",
            "sni": "h.example.com", "obfs": "salamander",
            "obfs_password": "op", "remark": "美国01"}
NODES = [NODE_ANYTLS, NODE_TROJAN, NODE_VLESS, NODE_HY2]


class TestNodeToProxy(unittest.TestCase):
    """节点 → Clash proxies（mihomo 字段）。"""

    def test_01_anytls(self):
        p = cc.node_to_clash_proxy(NODE_ANYTLS, "n1")
        self.assertEqual(p["type"], "anytls")
        self.assertEqual(p["password"], "pw")
        self.assertEqual(p["sni"], "s.example.com")
        self.assertTrue(p["udp"])
        for bad in ("padding", "users"):
            self.assertNotIn(bad, p)      # Clash 无这些字段

    def test_02_trojan_ws(self):
        p = cc.node_to_clash_proxy(NODE_TROJAN, "n2")
        self.assertEqual(p["type"], "trojan")
        self.assertEqual(p["sni"], "t.example.com")
        self.assertEqual(p["network"], "ws")
        self.assertEqual(p["ws-opts"]["path"], "/ws")
        self.assertEqual(p["ws-opts"]["headers"], {"Host": "cdn.example.com"})

    def test_03_vless_tls_flow(self):
        p = cc.node_to_clash_proxy(NODE_VLESS, "n3")
        self.assertEqual(p["type"], "vless")
        self.assertEqual(p["uuid"], "uuid-1")
        self.assertTrue(p["tls"])
        self.assertEqual(p["servername"], "v.example.com")
        self.assertEqual(p["flow"], "xtls-rprx-vision")
        self.assertEqual(p["ws-opts"]["path"], "/v")

    def test_04_hy2(self):
        p = cc.node_to_clash_proxy(NODE_HY2, "n4")
        self.assertEqual(p["type"], "hysteria2")
        self.assertEqual(p["obfs"], "salamander")
        self.assertEqual(p["obfs-password"], "op")

    def test_05_ss_and_socks(self):
        ss = cc.node_to_clash_proxy({"type": "ss", "addr": "e", "port": 1,
                                     "id": "pw", "method": "aes-256-gcm"}, "s")
        self.assertEqual(ss["cipher"], "aes-256-gcm")
        so = cc.node_to_clash_proxy({"type": "socks", "addr": "f", "port": 2,
                                     "user": "u", "pass": "p"}, "t")
        self.assertEqual(so["type"], "socks5")
        self.assertEqual(so["username"], "u")

    def test_06_reality(self):
        p = cc.node_to_clash_proxy({"type": "vless", "addr": "g", "port": 443,
                                    "id": "u", "tls": "reality", "sni": "r",
                                    "pbk": "PUBKEY", "spx": "SPX"}, "r")
        self.assertEqual(p["reality-opts"], {"public-key": "PUBKEY",
                                             "short-id": "SPX"})

    def test_07_unsupported_raises(self):
        with self.assertRaises(ValueError):
            cc.node_to_clash_proxy({"type": "量子协议", "addr": "x", "port": 1}, "q")
        with self.assertRaises(ValueError):
            cc.node_to_clash_proxy({"type": "ss", "addr": "x"}, "no-port")


class TestGroups(unittest.TestCase):
    def setUp(self):
        self._bak = cc.GROUPS_FILE
        cc.GROUPS_FILE = os.path.join(TMP, "groups.json")

    def tearDown(self):
        cc.GROUPS_FILE = self._bak

    def test_01_roundtrip_and_clamp(self):
        saved = cc.save_groups([
            {"name": "自动选择", "type": "url-test", "filter": "*", "interval": 5},
            {"name": "香港组", "type": "手动", "filter": "香港, HK", "default": True},
            {"name": "", "type": "select"},
        ])
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["interval"], 30)        # 下限 30s
        self.assertEqual(saved[1]["type"], "select")      # 非法类型回落
        got = cc.load_groups()
        self.assertTrue(got[1].get("default"))

    def test_02_members_filter(self):
        nodes = [{"remark": "🇭🇰 香港 01"}, {"remark": "🇯🇵 日本01"},
                 {"remark": "🇭🇰 香港 02", "addr": "hk.example.com"}]
        self.assertEqual(cc.group_members({"filter": "*"}, nodes), [0, 1, 2])
        self.assertEqual(cc.group_members({"filter": "香港"}, nodes), [0, 2])
        self.assertEqual(cc.group_members({"filter": "hk.example"}, nodes), [2])
        self.assertEqual(cc.group_members({"filter": "无"}, nodes), [])


class TestBuildConfig(unittest.TestCase):
    def setUp(self):
        self._bak = cc.GROUPS_FILE
        cc.GROUPS_FILE = os.path.join(TMP, "groups2.json")
        # 隔离真实机器状态：统一分流规则若已接管（unified.json 存在）会旁路内置 GEOSITE 行
        self._routing_bak = getattr(rc, "UNIFIED_FILE", None)
        self._routing_patch = mock.patch.object(
            rc, "UNIFIED_FILE", os.path.join(TMP, "no-unified.json"))
        self._routing_patch.start()

    def tearDown(self):
        cc.GROUPS_FILE = self._bak
        if self._routing_patch:
            self._routing_patch.stop()
            if self._routing_bak is not None:
                rc.UNIFIED_FILE = self._routing_bak

    def test_01_groups_and_primary(self):
        cc.save_groups([
            {"name": "自动选择", "type": "url-test", "filter": "*"},
            {"name": "节点选择", "type": "select", "filter": "*", "default": True},
        ])
        cfg, info = cc.build_config(NODES, cc.load_groups(), 7891, 9091, "s")
        self.assertEqual(info["primary"], "节点选择")
        self.assertEqual(info["count"], 4)
        groups = {g["name"]: g for g in cfg["proxy-groups"]}
        self.assertEqual(groups["自动选择"]["type"], "url-test")
        self.assertEqual(len(groups["自动选择"]["proxies"]), 4)
        self.assertEqual(groups["节点选择"]["type"], "select")
        self.assertEqual(groups["节点选择"]["proxies"][0], "自动选择")   # 嵌套
        self.assertEqual(cfg["mixed-port"], 7891)
        self.assertEqual(cfg["external-controller"], "127.0.0.1:9091")
        self.assertEqual(cfg["secret"], "s")
        self.assertEqual(len(cfg["proxies"]), 4)
        self.assertTrue(cfg["proxies"][0]["name"].startswith("#01"))

    def test_02_auto_group_when_none(self):
        cfg, info = cc.build_config(NODES, [], 7891, 9091, "s")
        self.assertEqual(info["primary"], "PROXY")
        self.assertEqual(cfg["proxy-groups"][0]["name"], "PROXY")
        self.assertEqual(cfg["proxy-groups"][0]["type"], "url-test")
        self.assertTrue(cfg["rules"][-1].endswith("PROXY"))

    def test_03_skips_unsupported_nodes(self):
        nodes = [dict(NODE_ANYTLS),
                 {"type": "未知", "addr": "z", "port": 1, "remark": "坏节点"}]
        cfg, info = cc.build_config(nodes, [], 1, 2, "s")
        self.assertEqual(info["count"], 1)
        self.assertEqual(len(info["skipped"]), 1)
        self.assertIn("坏节点", info["skipped"][0])

    def test_04_empty_raises(self):
        with self.assertRaises(ValueError):
            cc.build_config([], [], 1, 2, "s")

    def test_05_rules_use_geo_when_ready(self):
        with mock.patch.object(cc, "geo_ready_in", return_value=True):
            cfg, _ = cc.build_config(NODES, [], 1, 2, "s", data_dir="/x")
        rules = cfg["rules"]
        self.assertIn("GEOSITE,category-ads-all,REJECT", rules)
        self.assertIn("GEOSITE,cn,DIRECT", rules)
        self.assertIn("GEOIP,CN,DIRECT", rules)

    def test_06_rules_fallback_without_geo(self):
        with mock.patch.object(cc, "geo_ready_in", return_value=False):
            cfg, _ = cc.build_config(NODES, [], 1, 2, "s")
        rules = cfg["rules"]
        self.assertTrue(any(r.startswith("IP-CIDR,") for r in rules))
        self.assertTrue(any(r.startswith("DOMAIN-SUFFIX,") for r in rules))

    def test_07_dns_and_tun(self):
        cfg, _ = cc.build_config(NODES, [], 1, 2, "s", tun=True)
        self.assertTrue(cfg["tun"]["enable"])
        self.assertTrue(cfg["dns"]["enable"])
        self.assertFalse(cfg["dns"]["fallback-filter"]["geoip"])   # 不依赖 MMDB
        cfg2, _ = cc.build_config(NODES, [], 1, 2, "s", mode="bogus")
        self.assertEqual(cfg2["mode"], "rule")

    def test_08_yaml_dump_loadable(self):
        import yaml
        cfg, _ = cc.build_config(NODES, [], 7891, 9091, "s")
        p = cc.dump_yaml(cfg, os.path.join(TMP, "cfg.yaml"))
        back = yaml.safe_load(open(p, encoding="utf-8"))
        self.assertEqual(back["mixed-port"], 7891)
        self.assertEqual(len(back["proxies"]), 4)


class TestManagerHelpers(unittest.TestCase):
    def test_01_ensure_geo_copies(self):
        data_dir = os.path.join(TMP, "dd1")
        bin_dir = os.path.join(TMP, "bin1")
        os.makedirs(bin_dir, exist_ok=True)
        for name, size in (("geoip.dat", 2048), ("geosite.dat", 2048),
                           ("country.mmdb", 2048)):
            with open(os.path.join(bin_dir, name), "wb") as f:
                f.write(b"x" * size)
        m = cc.ClashCoreManager(data_dir=data_dir)
        with mock.patch.object(cc, "MIHOMO_DIR", bin_dir):
            self.assertTrue(m.ensure_geo())
        self.assertTrue(cc.geo_ready_in(data_dir))
        self.assertTrue(os.path.isfile(os.path.join(data_dir, "country.mmdb")))

    def test_02_state_shape(self):
        m = cc.ClashCoreManager(data_dir=os.path.join(TMP, "dd2"))
        st = m.state()
        for k in ("running", "healthy", "core_ok", "mixed_port", "api_port",
                  "mode", "tun", "sys_proxy", "admin", "geo_ok", "node_count"):
            self.assertIn(k, st)
        self.assertFalse(st["running"])

    def test_03_proxy_name_unique(self):
        names = [cc._proxy_name(i, n) for i, n in enumerate(NODES)]
        self.assertEqual(len(set(names)), len(names))
        self.assertTrue(names[0].startswith("#01"))

    def test_04_detect_bin_missing(self):
        with mock.patch.object(cc, "bin_candidates", return_value=("X:/none.exe",)), \
             mock.patch("shutil.which", return_value=None):
            with self.assertRaises(ValueError):
                cc.detect_bin("")


class TestBindlClashSpecs(unittest.TestCase):
    def test_01_mihomo_spec(self):
        spec = bindl._REPOS["mihomo"]
        self.assertEqual(spec["exe"], "mihomo.exe")
        self.assertTrue(spec["match"].search("mihomo-windows-amd64-v1-go125-v1.19.30.zip"))
        self.assertFalse(spec["match"].search("mihomo-windows-386-v1.19.30.zip"))

    def test_02_geo_specs(self):
        for kind, exe in (("clash-geoip", "geoip.dat"),
                          ("clash-geosite", "geosite.dat"),
                          ("clash-mmdb", "country.mmdb")):
            self.assertIn(kind, bindl._REPOS)
            self.assertTrue(bindl._REPOS[kind]["raw"])
            self.assertEqual(bindl._REPOS[kind]["exe"], exe)

    def test_03_find_exe_in_zip_platform_suffix(self):
        """压缩包内 exe 带平台后缀也能定位（mihomo 的实际打包形式）。"""
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("mihomo-windows-amd64-v1-go125.exe", b"x" * 2048)
        buf.seek(0)
        with zipfile.ZipFile(buf) as z:
            info = bindl._find_exe_in_zip(z, "mihomo.exe")
        self.assertIsNotNone(info)
        self.assertIn("mihomo-windows-amd64", info.filename)


class FakeCfg:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        return True


class TestClashBridgeExtras(unittest.TestCase):
    """桥接层：规则 / 日志级别 / 日志流 / 架构信息（不启动内核）。"""

    def setUp(self):
        from app.bridge.clash_api import ClashApi

        self.dir = tempfile.mkdtemp(prefix="clash-bridge-")
        self._orig = cc.RULES_FILE
        cc.RULES_FILE = os.path.join(self.dir, "rules.json")

        outer = self

        class FakeBridge(ClashApi):
            def __init__(self):
                self.emits = []
                self.cfg = FakeCfg()
                self._clash = cc.ClashCoreManager(
                    data_dir=os.path.join(outer.dir, "data"),
                    log_callback=lambda m: self.emits.append(("clash_log", str(m))))
                self._clash_log_stop = None

            def emit(self, ev, data=None):
                self.emits.append((ev, data))
                return True

        self.b = FakeBridge()

    def tearDown(self):
        cc.RULES_FILE = self._orig

    def test_01_rules_ok(self):
        r = self.b.clash_rules()
        self.assertTrue(r["ok"], r)
        self.assertIn("rules", r["data"])
        self.assertIn("custom", r["data"])
        self.assertEqual(r["data"]["path"], cc.RULES_FILE)

    def test_02_save_rules_persists_and_logs(self):
        r = self.b.clash_save_rules(["DOMAIN,x.com,DIRECT", " ", "# 注释"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["data"], ["DOMAIN,x.com,DIRECT"])
        self.assertEqual(cc.load_custom_rules(), ["DOMAIN,x.com,DIRECT"])
        self.assertTrue(any("自定义规则已保存" in str(d) for _, d in self.b.emits))

    def test_03_save_rules_accepts_single_string(self):
        r = self.b.clash_save_rules("DOMAIN,y.com,DIRECT")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["data"], ["DOMAIN,y.com,DIRECT"])

    def test_04_set_log_level_persists(self):
        r = self.b.clash_set_log_level("DEBUG")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["data"], "debug")
        self.assertEqual(self.b.cfg.get("clash_log_level"), "debug")
        bad = self.b.clash_set_log_level("verbose")
        self.assertFalse(bad["ok"])

    def test_05_log_start_requires_running(self):
        r = self.b.clash_log_start("info")
        self.assertFalse(r["ok"])
        self.assertIn("未运行", r["err"])
        self.assertTrue(self.b.clash_log_stop()["ok"])   # 停止可重复调用

    def test_06_state_has_arch_and_log_level(self):
        st = self.b.clash_get_state()["data"]
        for k in ("arch", "core_asset", "core_version", "log_level", "custom_rules"):
            self.assertIn(k, st)
        self.assertTrue(st["arch"])
        self.assertEqual(st["log_level"], "warning")

    def test_07_apply_settings_reads_cfg(self):
        self.b.cfg.set("clash_log_level", "error")
        self.b.cfg.set("clash_mixed_port", 7899)
        self.b._apply_clash_settings()
        self.assertEqual(self.b._clash.log_level, "error")
        self.assertEqual(self.b._clash.mixed_port, 7899)
        self.b.cfg.set("clash_log_level", "bogus")
        self.b._apply_clash_settings()
        self.assertEqual(self.b._clash.log_level, "warning")

    def test_08_cfg_set_whitelists_log_level(self):
        from app.bridge.bridge import Bridge
        r = Bridge.cfg_set(self.b, "clash_log_level", "INFO")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.b.cfg.get("clash_log_level"), "info")
        r2 = Bridge.cfg_set(self.b, "clash_log_level", "bogus")
        self.assertTrue(r2["ok"])
        self.assertEqual(self.b.cfg.get("clash_log_level"), "warning")

    def test_09_log_line_emits_event(self):
        self.b._clash_log_line("warning", "hello")
        ev, data = self.b.emits[-1]
        self.assertEqual(ev, "clash_kernel_log")
        self.assertEqual(data["level"], "warning")
        self.assertEqual(data["text"], "hello")
        self.assertTrue(data["ts"] > 0)


class FakeSock:
    def __init__(self):
        self.shutdown_called = False

    def shutdown(self, how):
        self.shutdown_called = True


class FakeLogResp:
    """模拟 /logs 的流式响应：先给若干行，然后阻塞直到 socket 被 shutdown。"""

    def __init__(self, lines):
        self._lines = [l.encode("utf-8") + b"\n" for l in lines]
        self.sock = FakeSock()
        # 结构对齐真实对象：HTTPResponse.fp.raw._sock
        self.fp = type("FP", (), {"raw": type("Raw", (), {"_sock": self.sock})()})()
        self.closed = False

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        # 模拟 socket 上的阻塞读：被 shutdown 唤醒后返回空（EOF）
        for _ in range(200):
            if self.sock.shutdown_called:
                return b""
            time.sleep(0.01)
        return b""

    def close(self):
        self.closed = True


class TestLogStreamMechanics(unittest.TestCase):
    """日志流：逐行解析 / 阻塞读不吞日志 / 停止不死锁 / 级别下发内核。"""

    def _mgr(self, name):
        m = cc.ClashCoreManager(data_dir=os.path.join(TMP, name))
        m.running = True
        m.api_port = 9099
        m.secret = "sec"
        return m

    def test_01_lines_parsed_and_level_patched(self):
        m = self._mgr("dd-stream1")
        resp = FakeLogResp([json.dumps({"type": "warning", "payload": "hello"}),
                            "not-json",
                            json.dumps({"type": "info", "payload": "world"})])
        got, patched = [], []
        with mock.patch.object(m, "api",
                               side_effect=lambda *a, **k: patched.append(a)),              mock.patch("urllib.request.urlopen", return_value=resp):
            stop = m.start_log_stream("debug", lambda lv, tx: got.append((lv, tx)))
            for _ in range(200):
                if len(got) >= 2:
                    break
                time.sleep(0.01)
            stop()
        self.assertEqual(got, [("warning", "hello"), ("info", "world")])
        self.assertEqual(m.log_level, "debug")
        self.assertTrue(any(a[0] == "PATCH" and a[2] == {"log-level": "debug"}
                            for a in patched if len(a) >= 3), patched)

    def test_02_stop_shuts_down_socket_without_hanging(self):
        m = self._mgr("dd-stream2")
        resp = FakeLogResp([])       # 立刻进入阻塞读
        with mock.patch.object(m, "api", return_value={}),              mock.patch("urllib.request.urlopen", return_value=resp):
            stop = m.start_log_stream("info", lambda lv, tx: None)
            time.sleep(0.5)
            t0 = time.time()
            stop()
            self.assertLess(time.time() - t0, 2.0, "stop() 不应阻塞")
            for _ in range(100):
                if resp.sock.shutdown_called:
                    break
                time.sleep(0.01)
            self.assertTrue(resp.sock.shutdown_called, "应 shutdown 底层 socket")

    def test_03_error_reported_when_not_stopped(self):
        m = self._mgr("dd-stream3")
        errs = []
        with mock.patch.object(m, "api", return_value={}),              mock.patch("urllib.request.urlopen", side_effect=OSError("boom")):
            m.start_log_stream("info", lambda lv, tx: None,
                               on_error=lambda e: errs.append(e))
            for _ in range(100):
                if errs:
                    break
                time.sleep(0.01)
        self.assertTrue(errs and "boom" in errs[0], errs)


class TestLogStartBridge(unittest.TestCase):
    """桥接 clash_log_start/stop：事件推送 + 句柄释放。"""

    def setUp(self):
        from app.bridge.clash_api import ClashApi
        outer = self

        class B(ClashApi):
            def __init__(self):
                self.emits = []
                self.cfg = FakeCfg()
                self._clash = cc.ClashCoreManager(
                    data_dir=os.path.join(TMP, "dd-logstart"))
                self._clash_log_stop = None

            def emit(self, ev, data=None):
                self.emits.append((ev, data))
                return True

        self.b = B()

    def test_01_start_stop_and_event(self):
        stops = []
        seen = {}

        def fake_stream(level, on_line, on_error=None):
            seen["level"] = level
            seen["on_line"] = on_line
            stops.append(True)
            return lambda: stops.append("stopped")

        with mock.patch.object(self.b._clash, "start_log_stream", fake_stream):
            r = self.b.clash_log_start("warning")
            self.assertTrue(r["ok"], r)
            self.assertEqual(seen["level"], "warning")
            self.assertEqual(self.b._clash_log_level, "warning")
            seen["on_line"]("error", "boom")
            r2 = self.b.clash_log_stop()
        self.assertTrue(r2["ok"])
        self.assertIn("stopped", stops)
        self.assertIsNone(self.b._clash_log_stop)
        ev, data = self.b.emits[-1]
        self.assertEqual(ev, "clash_kernel_log")
        self.assertEqual(data["level"], "error")
        self.assertEqual(data["text"], "boom")

    def test_02_stop_is_idempotent(self):
        self.assertTrue(self.b.clash_log_stop()["ok"])

    def test_03_start_requires_running(self):
        r = self.b.clash_log_start("info")
        self.assertFalse(r["ok"])
        self.assertIn("未运行", r["err"])


class TestBinVersion(unittest.TestCase):
    """内核自报版本（界面用来显示实际使用的构建变体）。"""

    def test_01_missing_bin_returns_empty(self):
        m = cc.ClashCoreManager(data_dir=os.path.join(TMP, "dd-ver1"))
        self.assertEqual(m.bin_version(), "")

    def test_02_parses_first_line_and_caches(self):
        exe = os.path.join(TMP, "fake-mihomo.exe")
        with open(exe, "wb") as f:
            f.write(b"x" * 64)
        m = cc.ClashCoreManager(data_dir=os.path.join(TMP, "dd-ver2"))
        m.bin_path = exe
        out = mock.Mock(stdout=b"Mihomo Meta v1.19.30 windows amd64 with go1.25.13" + b"\n"
                               + b"extra" + b"\n",
                        stderr=b"")
        with mock.patch("subprocess.run", return_value=out) as run:
            v = m.bin_version()
            self.assertIn("go1.25.13", v)
            self.assertNotIn("extra", v)
            m.bin_version()
        self.assertEqual(run.call_count, 1, "第二次应命中缓存")

    def test_03_state_includes_build(self):
        m = cc.ClashCoreManager(data_dir=os.path.join(TMP, "dd-ver3"))
        with mock.patch.object(m, "bin_version", return_value="Mihomo Meta vX"):
            st = m.state()
        self.assertEqual(st["build"], "Mihomo Meta vX")

    def test_04_failure_is_swallowed(self):
        exe = os.path.join(TMP, "fake-mihomo2.exe")
        with open(exe, "wb") as f:
            f.write(b"x" * 8)
        m = cc.ClashCoreManager(data_dir=os.path.join(TMP, "dd-ver4"))
        m.bin_path = exe
        with mock.patch("subprocess.run", side_effect=OSError("boom")):
            self.assertEqual(m.bin_version(), "")


class TestSubUserinfo(unittest.TestCase):
    """订阅流量/到期：响应头解析 + 桥接展示 + 自动采用提供方建议周期。"""

    def test_01_parse(self):
        from app.core.v2ray_core import _parse_sub_userinfo as P
        self.assertEqual(P("upload=1; download=2; total=3; expire=4"),
                         {"upload": 1, "download": 2, "total": 3, "expire": 4})
        self.assertEqual(P(""), {})
        self.assertEqual(P("garbage; total=abc"), {})
        self.assertEqual(P("TOTAL=1.5e3"), {"total": 1500})
        self.assertEqual(P("unknown=9"), {})

    def _bridge(self, name):
        from app.bridge.clash_api import ClashApi
        outer = self

        class B(ClashApi):
            def __init__(self):
                self.emits = []
                self.cfg = FakeCfg()
                self._clash = cc.ClashCoreManager(
                    data_dir=os.path.join(TMP, name))
                self._clash_log_stop = None

            def emit(self, ev, data=None):
                self.emits.append((ev, data))
                return True

        return B()

    def test_02_import_exposes_userinfo(self):
        b = self._bridge("dd-sub1")

        def fake_import(url, store, timeout=20):
            store.data["sub_userinfo"] = {"upload": 10, "download": 20,
                                          "total": 1000, "expire": 1790000000}
            store.data["sub_interval_hours"] = 12
            store.merge([{"type": "trojan", "addr": "a", "port": 443, "id": "p"}],
                        sub_url=url)
            return 1, 1, []

        with mock.patch("app.core.v2ray_core.import_sub_url", fake_import):
            r = b.clash_import_sub("https://sub.example/x")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["data"]["userinfo"]["total"], 1000)
        st = b._clash_state()
        self.assertEqual(st["sub_userinfo"]["download"], 20)
        self.assertEqual(st["sub_interval_hours"], 12)
        # 用户没设置自动更新 → 采用提供方建议
        self.assertEqual(b.cfg.get("clash_sub_autoupdate_hours"), 12)
        self.assertTrue(any("已按此开启自动更新" in str(d) for _, d in b.emits))

    def test_03_user_setting_wins(self):
        b = self._bridge("dd-sub2")
        b.cfg.set("clash_sub_autoupdate_hours", 24)

        def fake_import(url, store, timeout=20):
            store.data["sub_interval_hours"] = 6
            store.merge([{"type": "trojan", "addr": "a", "port": 443, "id": "p"}],
                        sub_url=url)
            return 1, 1, []

        with mock.patch("app.core.v2ray_core.import_sub_url", fake_import):
            b.clash_import_sub("https://sub.example/x")
        self.assertEqual(b.cfg.get("clash_sub_autoupdate_hours"), 24,
                         "用户已设置的周期不应被订阅建议覆盖")

    def test_04_no_suggestion_is_noop(self):
        b = self._bridge("dd-sub3")

        def fake_import(url, store, timeout=20):
            store.merge([{"type": "trojan", "addr": "a", "port": 443, "id": "p"}],
                        sub_url=url)
            return 1, 1, []

        with mock.patch("app.core.v2ray_core.import_sub_url", fake_import):
            b.clash_import_sub("https://sub.example/x")
        self.assertIsNone(b.cfg.get("clash_sub_autoupdate_hours"))
        self.assertEqual(b._clash_state()["sub_userinfo"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=False)


class TestCustomRules(unittest.TestCase):
    """自定义规则：存取、清洗、生成配置时置顶。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="clash-rules-")
        self.rules_file = os.path.join(self.dir, "rules.json")
        self._orig = cc.RULES_FILE
        cc.RULES_FILE = self.rules_file

    def tearDown(self):
        cc.RULES_FILE = self._orig

    def test_01_empty_and_save_load(self):
        self.assertEqual(cc.load_custom_rules(), [])
        saved = cc.save_custom_rules(["  DOMAIN,x.com,DIRECT  ", "", "   ",
                                      "# 注释行", "IP-CIDR,1.1.1.1/32,REJECT"])
        self.assertEqual(saved, ["DOMAIN,x.com,DIRECT", "IP-CIDR,1.1.1.1/32,REJECT"])
        self.assertEqual(cc.load_custom_rules(), saved)

    def test_02_broken_json_returns_empty(self):
        os.makedirs(self.dir, exist_ok=True)
        with open(self.rules_file, "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertEqual(cc.load_custom_rules(), [])

    def test_03_long_rule_truncated(self):
        saved = cc.save_custom_rules(["DOMAIN,x.com," + "P" * 500])
        self.assertEqual(len(saved[0]), 300)

    def test_04_prepended_in_config(self):
        cfg, _info = cc.build_config(NODES, [], data_dir="",
                                     custom_rules=["DOMAIN-SUFFIX,corp.example,DIRECT"])
        self.assertEqual(cfg["rules"][0], "DOMAIN-SUFFIX,corp.example,DIRECT")
        # 自定义规则置于自动分流之前，可覆盖内置规则
        self.assertEqual(cfg["rules"][1], "GEOIP,private,DIRECT,no-resolve"
                         if cfg["rules"][1].startswith("GEOIP,private") else cfg["rules"][1])

    def test_05_manager_rules_shape(self):
        cc.save_custom_rules(["DOMAIN-SUFFIX,corp.example,DIRECT"])
        m = cc.ClashCoreManager(data_dir=os.path.join(TMP, "dd-rules"))
        data = m.rules()          # 未运行：内核 API 失败也要有结构
        self.assertEqual(data["rules"], [])
        self.assertEqual(data["custom"], ["DOMAIN-SUFFIX,corp.example,DIRECT"])
        self.assertFalse(data["geo_ok"])

    def test_06_rules_marks_custom(self):
        """内核下发的规则若与自定义规则一致，标记 custom=True。"""
        line = "DOMAIN-SUFFIX,corp.example,DIRECT"
        cc.save_custom_rules([line])
        m = cc.ClashCoreManager(data_dir=os.path.join(TMP, "dd-rules2"))
        m.running = True
        with mock.patch.object(m, "api", return_value={"rules": [
                # 内核 API 返回的是规范化类型（DomainSuffix），不是标准写法
                {"type": "DomainSuffix", "payload": "corp.example", "proxy": "DIRECT"},
                {"type": "GeoIP", "payload": "CN", "proxy": "DIRECT"}]}):
            data = m.rules()
        self.assertTrue(data["rules"][0]["custom"])
        self.assertFalse(data["rules"][1]["custom"])


class TestLogLevelAndStream(unittest.TestCase):
    """日志级别校验 + 日志流前置条件。"""

    def test_01_set_log_level(self):
        m = cc.ClashCoreManager(data_dir=os.path.join(TMP, "dd-log"))
        self.assertEqual(m.set_log_level("Info"), "info")
        self.assertEqual(m.log_level, "info")
        with self.assertRaises(ValueError):
            m.set_log_level("verbose")

    def test_02_build_config_level_clamped(self):
        cfg, _ = cc.build_config(NODES, [], data_dir="", log_level="debug")
        self.assertEqual(cfg["log-level"], "debug")
        cfg2, _ = cc.build_config(NODES, [], data_dir="", log_level="bogus")
        self.assertEqual(cfg2["log-level"], "warning")

    def test_03_stream_requires_running(self):
        m = cc.ClashCoreManager(data_dir=os.path.join(TMP, "dd-log2"))
        with self.assertRaises(ValueError):
            m.start_log_stream("info", lambda a, b: None)

    def test_04_reload_requires_running(self):
        m = cc.ClashCoreManager(data_dir=os.path.join(TMP, "dd-log3"))
        with self.assertRaises(ValueError):
            m.reload()

    def test_05_reload_rewrites_and_puts(self):
        m = cc.ClashCoreManager(data_dir=os.path.join(TMP, "dd-log4"))
        m.store.merge(NODES)
        m.running = True
        calls = []
        with mock.patch.object(m, "api", side_effect=lambda *a, **k: calls.append(a) or {}), \
             mock.patch.object(m, "ensure_geo", return_value=False):
            m.reload()
        self.assertTrue(os.path.isfile(os.path.join(m.data_dir, "config.yaml")))
        self.assertEqual(calls[0][0], "PUT")
        self.assertIn("/configs", calls[0][1])


class TestArchDetect(unittest.TestCase):
    """架构识别 + 按架构挑内核构建（x86-64 v1/v2/v3、ARM64、386）。"""

    MI_AM64 = [
        "mihomo-windows-amd64-compatible-v1.19.30.zip",
        "mihomo-windows-amd64-go123-v1.19.30.zip",
        "mihomo-windows-amd64-v1.19.30.zip",
        "mihomo-windows-amd64-v1-go120-v1.19.30.zip",
        "mihomo-windows-amd64-v2-go122-v1.19.30.zip",
        "mihomo-windows-amd64-v3-go125-v1.19.30.zip",
    ]
    MI_ARM = [
        "mihomo-windows-arm64-v1.19.30.zip",
        "mihomo-windows-amd64-v3-v1.19.30.zip",
    ]
    MI_386 = [
        "mihomo-windows-386-v1.19.30.zip",
        "mihomo-windows-amd64-v3-v1.19.30.zip",
    ]

    def _pick(self, arch, level, assets):
        with mock.patch.object(bindl, "arch_tag", return_value=arch), \
             mock.patch.object(bindl, "cpu_level_amd64", return_value=level):
            return bindl._pick_asset_by_arch(assets, "mihomo")

    def test_01_amd64_v3_prefers_v3(self):
        self.assertIn("amd64-v3", self._pick("amd64", 3, self.MI_AM64))

    def test_02_amd64_v2(self):
        self.assertIn("amd64-v2", self._pick("amd64", 2, self.MI_AM64))

    def test_03_amd64_v1_avoids_v2_v3(self):
        got = self._pick("amd64", 1, self.MI_AM64)
        self.assertIn("amd64", got)
        for bad in ("-v2-", "-v3-"):
            self.assertNotIn(bad, got)

    def test_04_newer_go_toolchain_wins(self):
        """同一架构层级有多个 go 版本构建时，取 go 版本较新的。"""
        assets = ["mihomo-windows-amd64-v3-go120-v1.19.30.zip",
                  "mihomo-windows-amd64-v3-go125-v1.19.30.zip"]
        got = self._pick("amd64", 3, assets)
        self.assertIn("go125", got)

    def test_05_arm64_never_gets_amd64(self):
        got = self._pick("arm64", 1, self.MI_ARM)
        self.assertIn("arm64", got)
        self.assertNotIn("amd64", got)

    def test_06_386_picks_386(self):
        self.assertIn("386", self._pick("386", 1, self.MI_386))

    def test_07_v3_falls_back_when_missing(self):
        """只提供低层级构建时不能返回 None。"""
        got = self._pick("amd64", 3, ["mihomo-windows-amd64-v1.19.30.zip"])
        self.assertIsNotNone(got)

    def test_08_arch_tag_and_label(self):
        self.assertIn(bindl.arch_tag(), ("amd64", "arm64", "386"))
        self.assertIn(bindl.cpu_level_amd64(), (1, 2, 3))
        label = bindl.arch_label()
        self.assertTrue(label)
        with mock.patch.object(bindl, "arch_tag", return_value="arm64"):
            self.assertEqual(bindl.arch_label(), "ARM64")
            self.assertEqual(bindl.cpu_level_amd64(), 1)

    def test_09_cached_info_roundtrip(self):
        cache_file = os.path.join(TMP, "cache-arch.json")
        with mock.patch.object(bindl, "CACHE_FILE", cache_file):
            self.assertEqual(bindl.cached_info("mihomo"), {})
            bindl._save_cache({"mihomo": {"version": "v1", "exe": "x.exe",
                                          "asset": "mihomo-windows-amd64-v3-x.zip"}})
            info = bindl.cached_info("mihomo")
        self.assertEqual(info["asset"], "mihomo-windows-amd64-v3-x.zip")
