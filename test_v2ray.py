"""V2rayN 集成测试：分享链接解析 / 节点库 / 订阅导入 / 配置生成 / 核心托管。

运行：python test_v2ray.py
"""

import base64
import json
import time
import os
import tempfile
import unittest
from unittest import mock

from app.core import bindl, v2ray_core as v2c

TMP = tempfile.mkdtemp(prefix="v2ray-test-")
V2RAYN_DIR = os.path.join(TMP, "v2rayn")


def fake_geo_dir():
    """临时 GEO_DIR：内置 4 件 .srs（SRS magic 头）+ dat 占位，供动态映射测试。"""
    d = tempfile.mkdtemp(prefix="geo-test-")
    for n in ("geosite-cn.srs", "geosite-ads.srs", "geoip-cn.srs",
              "geoip-private.srs", "geoip.dat", "geosite.dat"):
        with open(os.path.join(d, n), "wb") as f:
            f.write(b"SRS\x01" if n.endswith(".srs") else b"x" * 2048)
    return d


def b64url(s):
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


VMESS = b64url(json.dumps({
    "v": "2", "ps": "国测", "add": "1.2.3.4", "port": "443", "id": "uuid-123",
    "aid": "0", "net": "ws", "type": "none", "host": "cdn.example.com",
    "path": "/ws", "tls": "tls", "sni": "cdn.example.com",
}, ensure_ascii=False))


class TestParse(unittest.TestCase):
    def test_01_vmess(self):
        n = v2c.parse_share_link("vmess://%s#%s" % (VMESS, b64url("备注名")))
        self.assertIsNotNone(n)
        self.assertEqual(n["type"], "vmess")
        self.assertEqual(n["addr"], "1.2.3.4")
        self.assertEqual(n["port"], 443)
        self.assertEqual(n["id"], "uuid-123")
        self.assertEqual(n["network"], "ws")
        self.assertEqual(n["tls"], "tls")
        self.assertEqual(n["host"], "cdn.example.com")
        self.assertEqual(n["path"], "/ws")

    def test_02_vless(self):
        n = v2c.parse_share_link(
            "vless://uuid-456@1.2.3.5:8443?encryption=none&security=tls"
            "&sni=cdn2.example.com&type=grpc&serviceName=svc#节点A"
        )
        self.assertIsNotNone(n)
        self.assertEqual(n["type"], "vless")
        self.assertEqual(n["addr"], "1.2.3.5")
        self.assertEqual(n["id"], "uuid-456")
        self.assertEqual(n["tls"], "tls")
        self.assertEqual(n["sni"], "cdn2.example.com")
        self.assertEqual(n["network"], "grpc")
        self.assertEqual(n["grpc_mode"], "svc")
        self.assertEqual(n["remark"], "节点A")

    def test_03_trojan(self):
        n = v2c.parse_share_link("trojan://pass-1@1.2.3.6:443?security=tls&sni=x#t")
        self.assertIsNotNone(n)
        self.assertEqual(n["type"], "trojan")
        self.assertEqual(n["id"], "pass-1")
        self.assertEqual(n["addr"], "1.2.3.6")

    def test_04_ss_userinfo(self):
        auth = b64url("aes-256-gcm:my-pass")
        n = v2c.parse_share_link(
            "ss://%s@1.2.3.7:8388#ss-node" % auth
        )
        self.assertIsNotNone(n)
        self.assertEqual(n["type"], "ss")
        self.assertEqual(n["method"], "aes-256-gcm")
        self.assertEqual(n["id"], "my-pass")
        self.assertEqual(n["addr"], "1.2.3.7")

    def test_05_socks_http(self):
        n = v2c.parse_share_link("socks5://u1:p2@1.2.3.8:1080")
        self.assertIsNotNone(n)
        self.assertEqual(n["type"], "socks")
        self.assertEqual(n["user"], "u1")
        self.assertEqual(n["pass"], "p2")
        n2 = v2c.parse_share_link("http://u1:p2@1.2.3.9:8080")
        self.assertIsNotNone(n2)
        self.assertEqual(n2["type"], "http")
        self.assertEqual(n2["user"], "u1")
        self.assertEqual(n2["pass"], "p2")

    def test_06_invalid(self):
        self.assertIsNone(v2c.parse_share_link("# 注释"))
        self.assertIsNone(v2c.parse_share_link("https://x.com/a"))
        self.assertIsNone(v2c.parse_share_link(""))


class TestSubText(unittest.TestCase):
    def test_01_mixed_lines(self):
        text = "\n".join([
            "vmess://%s" % VMESS,
            "vless://uuid-9@1.2.3.10:443?security=none#plain",
            "# 注释行",
            "",
        ])
        nodes = v2c.parse_sub_text(text)
        self.assertEqual(len(nodes), 2)

    def test_02_base64_blob(self):
        text = "\n".join([
            "vmess://%s" % VMESS,
            "trojan://pw@1.2.3.11:443#tt",
        ])
        blob = base64.b64encode(text.encode("utf-8")).decode("ascii")
        nodes = v2c.parse_sub_text(blob)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[1]["type"], "trojan")


class TestNodeStore(unittest.TestCase):
    def setUp(self):
        self.store = v2c.NodeStore(os.path.join(TMP, "store-%d.json" % id(self)))

    def test_01_merge_dedup(self):
        n1 = v2c.parse_share_link("vless://uuid-a@1.2.3.4:443?security=none#a")
        n2 = v2c.parse_share_link("vless://uuid-a@1.2.3.4:443?security=none#a-dup")
        added, total = self.store.merge([n1, n2, n1])
        self.assertEqual(added, 1)
        self.assertEqual(total, 1)

    def test_02_delete_and_selected(self):
        nodes = [
            v2c.parse_share_link("vless://u1@1.2.3.4:1?security=none#n1"),
            v2c.parse_share_link("vless://u2@1.2.3.5:2?security=none#n2"),
        ]
        self.store.merge(nodes)
        self.store.set_selected(1)
        self.assertEqual(self.store.data["selected"], 1)
        self.assertTrue(self.store.delete(1))
        self.assertEqual(self.store.data["selected"], 0)
        self.assertFalse(self.store.delete(9))

    def test_03_replace_all(self):
        nodes = [
            v2c.parse_share_link("vless://u1@1.2.3.4:1?security=none#n1"),
            v2c.parse_share_link("vless://u2@1.2.3.5:2?security=none#n2"),
        ]
        self.store.merge(nodes)
        keep = nodes[0]
        total = self.store.replace_all([keep, nodes[1], keep])
        self.assertEqual(total, 2)


class TestConfig(unittest.TestCase):
    def test_01_vless_ws(self):
        n = v2c.parse_share_link(
            "vless://uuid@1.2.3.4:443?security=tls&sni=c&type=ws&path=/p#x")
        conf = json.loads(v2c.build_core_config(n, 10809, 10808))
        self.assertEqual(conf["inbounds"][0]["port"], 10808)
        self.assertEqual(conf["inbounds"][1]["port"], 10809)
        ob = conf["outbounds"][0]
        self.assertEqual(ob["protocol"], "vless")
        self.assertEqual(ob["streamSettings"]["network"], "ws")
        self.assertEqual(ob["streamSettings"]["tlsSettings"]["serverName"], "c")
        self.assertEqual(conf["outbounds"][1]["protocol"], "freedom")

    def test_02_ss_http_proto(self):
        n = v2c.parse_share_link("ss://%s@1.2.3.7:8388" % b64url("aes-256-gcm:pw"))
        conf = json.loads(v2c.build_core_config(n, 10809, 10808))
        ob = conf["outbounds"][0]
        self.assertEqual(ob["protocol"], "shadowsocks")
        self.assertEqual(ob["settings"]["servers"][0]["password"], "pw")


def make_manager(nodes):
    # 独立临时节点库，避免测试写入真实应用数据目录
    store = v2c.NodeStore(os.path.join(TMP, "mgr-%d.json" % id(nodes)))
    store.data["nodes"] = list(nodes)
    store.data["selected"] = 0
    return v2c.V2rayCoreManager(log_callback=lambda m: None, store=store)


class FakeProc:
    def __init__(self, code=None):
        self.code = code
        self.returncode = code
        self.pid = 999
        self.killed = self.terminated = False

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


N = v2c.parse_share_link("vless://uuid@1.2.3.4:443?security=none#n1")
N2 = v2c.parse_share_link("vless://uuid2@1.2.3.5:443?security=none#n2")


class TestManager(unittest.TestCase):
    def setUp(self):
        self.pdir = os.path.join(TMP, "proxy")
        os.makedirs(self.pdir, exist_ok=True)
        self.patch_dir = mock.patch.object(v2c, "PROXY_DIR", self.pdir)
        self.patch_dir.start()
        self.addCleanup(self.patch_dir.stop)
        self.mgr = make_manager([N, N2])
        self.mgr.bin_path = os.path.join(self.pdir, "xray.exe")
        open(self.mgr.bin_path, "wb").close()

    @mock.patch.object(v2c.V2rayCoreManager, "_cleanup_stale_proxy", return_value=None)
    @mock.patch.object(v2c.V2rayCoreManager, "set_sys_proxy")
    @mock.patch.object(v2c.V2rayCoreManager, "_probe_latency", return_value=42)
    @mock.patch("subprocess.Popen", return_value=FakeProc())
    def test_01_start_healthy(self, popen, probe, sysp, stale):
        sysp.side_effect = lambda on: setattr(self.mgr, "sys_proxy", bool(on))
        self.mgr.start(10900, 10901, index=0)
        self.assertTrue(self.mgr.running)
        self.assertTrue(self.mgr.healthy)
        self.assertEqual(self.mgr.latency, 42)
        self.assertEqual(self.mgr.sys_proxy, True)
        self.assertTrue(self.mgr.config_file and os.path.isfile(self.mgr.config_file))
        sysp.assert_any_call(True)
        self.mgr.stop()
        self.assertFalse(self.mgr.running)
        sysp.assert_any_call(False)

    @mock.patch.object(v2c.V2rayCoreManager, "_cleanup_stale_proxy", return_value=None)
    @mock.patch.object(v2c.V2rayCoreManager, "set_sys_proxy")
    @mock.patch.object(v2c.V2rayCoreManager, "_probe_latency", return_value=None)
    @mock.patch("subprocess.Popen", return_value=FakeProc())
    @mock.patch("time.sleep", return_value=None)
    def test_02_start_probe_timeout(self, sleep, popen, probe, sysp, stale):
        # 探测一直失败 → 20 秒后启动失败并清理核心
        with mock.patch("time.time", side_effect=[0, 0, 0, 21]):
            with self.assertRaises(ValueError):
                self.mgr.start(10900, 10901, index=0)
        self.assertFalse(self.mgr.running)
        self.assertFalse(self.mgr.sys_proxy)

    @mock.patch.object(v2c.V2rayCoreManager, "_cleanup_stale_proxy", return_value=None)
    @mock.patch.object(v2c.V2rayCoreManager, "set_sys_proxy")
    @mock.patch("subprocess.Popen", return_value=FakeProc())
    def test_03_start_early_exit(self, popen, sysp, stale):
        popen.return_value = FakeProc(code=3)
        with self.assertRaises(ValueError):
            self.mgr.start(10900, 10901, index=0)
        self.assertFalse(self.mgr.running)

    @mock.patch.object(v2c.V2rayCoreManager, "_cleanup_stale_proxy", return_value=None)
    @mock.patch.object(v2c.V2rayCoreManager, "set_sys_proxy")
    @mock.patch.object(v2c.V2rayCoreManager, "_probe_latency", return_value=10)
    @mock.patch("subprocess.Popen", return_value=FakeProc())
    def test_04_monitor_switch(self, popen, probe, sysp, stale):
        # 不经过 start()，直接构造运行态，避免真实监控线程竞争 mock
        self.mgr.proc = FakeProc()
        self.mgr.running = True
        self.mgr.current = 0
        seq = iter([None, None])  # 两次探测失败 → 触发切换
        self.mgr._probe_latency = lambda: next(seq, None)
        fake_switch = mock.Mock(return_value=True)
        self.mgr._switch = fake_switch
        with mock.patch.object(self.mgr._stop_event, "wait",
                               side_effect=[False, False, True]):
            self.mgr._monitor_loop()
        fake_switch.assert_called_once_with()
        self.assertTrue(self.mgr.running)

    @mock.patch.object(v2c.V2rayCoreManager, "_cleanup_stale_proxy", return_value=None)
    @mock.patch.object(v2c.V2rayCoreManager, "set_sys_proxy")
    @mock.patch.object(v2c.V2rayCoreManager, "_probe_latency", return_value=10)
    @mock.patch("subprocess.Popen", return_value=FakeProc())
    def test_05_monitor_exit_restores_proxy(self, popen, probe, sysp, stale):
        self.mgr.proc = FakeProc(code=1)  # 核心已退出
        self.mgr.running = True
        self.mgr.sys_proxy = True
        calls = []
        sysp.side_effect = lambda on: calls.append(on)
        with mock.patch.object(self.mgr._stop_event, "wait",
                               side_effect=[False, True]):
            self.mgr._monitor_loop()
        self.assertFalse(self.mgr.running)
        self.assertIn(False, calls)

    @mock.patch.object(v2c.V2rayCoreManager, "_probe_latency", return_value=None)
    @mock.patch("subprocess.Popen", return_value=FakeProc())
    def test_06_select_backtrack(self, popen, probe):
        self.mgr.select(1)  # 未运行：仅记录 selected
        self.assertEqual(self.mgr.store.data["selected"], 1)

    @mock.patch.object(v2c, "_probe_http_port", return_value=42)
    @mock.patch("subprocess.Popen", return_value=FakeProc())
    def test_07_test_node_ok(self, popen, probe):
        r = self.mgr.test_node(0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["latency"], 42)

    @mock.patch.object(v2c, "_probe_http_port", return_value=None)
    @mock.patch("subprocess.Popen", return_value=FakeProc())
    @mock.patch("time.sleep", return_value=None)
    def test_08_test_node_timeout(self, sleep, popen, probe):
        # 首次 time.time 计算 deadline，之后探测均失败 → 12 秒超时
        with mock.patch("time.time", side_effect=[0, 0, 0, 21]):
            r = self.mgr.test_node(0)
        self.assertFalse(r["ok"])
        self.assertIn("12 秒", r["err"])

    def test_09_state_shape(self):
        st = self.mgr.state()
        self.assertEqual(st["node_count"], 2)
        self.assertIn("running", st)
        self.assertFalse(st["proc_alive"])


class TestImport(unittest.TestCase):
    def setUp(self):
        self.store = v2c.NodeStore(os.path.join(TMP, "imp-%d.json" % id(self)))

    @mock.patch("urllib.request.urlopen")
    def test_01_import_sub_url(self, urlopen):
        text = "vless://uuid@1.2.3.4:443?security=none#sub"
        response = mock.Mock()
        response.read.return_value = text.encode("utf-8")
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        urlopen.return_value = response
        added, total, nodes = v2c.import_sub_url("https://example.com/sub", self.store)
        self.assertEqual(added, 1)
        self.assertEqual(nodes[0]["remark"], "sub")
        self.assertEqual(self.store.data["sub_url"], "https://example.com/sub")

    @mock.patch("urllib.request.urlopen", side_effect=OSError("net down"))
    def test_02_import_sub_fail(self, urlopen):
        with self.assertRaises(ValueError):
            v2c.import_sub_url("https://example.com/sub", self.store)

    def test_03_import_v2rayn_dir(self):
        os.makedirs(V2RAYN_DIR, exist_ok=True)
        with open(os.path.join(V2RAYN_DIR, "config.json"), "w", encoding="utf-8") as f:
            json.dump({
                "log": {},
                "outbounds": [{
                    "protocol": "vless",
                    "tag": "官方入口",
                    "settings": {"vnext": [{
                        "address": "9.9.9.9", "port": 443,
                        "users": [{"id": "abc", "flow": ""}],
                    }]},
                    "streamSettings": {"network": "tcp", "security": "tls",
                                       "tlsSettings": {"serverName": "x"}},
                }],
            }, f)
        added, total, nodes = v2c.import_v2rayn_dir(V2RAYN_DIR, self.store)
        self.assertEqual(added, 1)
        self.assertEqual(nodes[0]["type"], "vless")
        self.assertEqual(nodes[0]["addr"], "9.9.9.9")

    def test_04_import_v2rayn_missing(self):
        with self.assertRaises(ValueError):
            v2c.import_v2rayn_dir(os.path.join(TMP, "no-dir"), self.store)


# =====================================================================
# v3.1：新协议解析（hy2 / tuic / wireguard / anytls / v2rayn 内部格式）
# =====================================================================
class TestNewProtocols(unittest.TestCase):
    def test_01_hy2(self):
        n = v2c.parse_share_link(
            "hy2://pass-1@1.2.3.4:443?sni=x.example.com&insecure=1"
            "&obfs=salamander&obfs-password=ob#hy")
        self.assertIsNotNone(n)
        self.assertEqual(n["type"], "hy2")
        self.assertEqual(n["id"], "pass-1")
        self.assertEqual(n["sni"], "x.example.com")
        self.assertEqual(n["obfs"], "salamander")
        self.assertEqual(n["obfs_password"], "ob")
        self.assertTrue(n["allow_insecure"])
        conf = json.loads(v2c.build_core_config(n, 10809, 10808, core_type=v2c.CORE_SING))
        ob = conf["outbounds"][0]
        self.assertEqual(ob["type"], "hysteria2")
        self.assertEqual(ob["password"], "pass-1")
        self.assertEqual(ob["obfs"]["type"], "salamander")
        self.assertEqual(ob["obfs"]["password"], "ob")
        self.assertTrue(ob["tls"]["insecure"])

    def test_02_tuic(self):
        n = v2c.parse_share_link(
            "tuic://uuid-9:tok@1.2.3.4:443?sni=x&alpn=h3%2Ch3-16"
            "&congestion_control=reno&udp_relay_mode=quic#tu")
        self.assertIsNotNone(n)
        self.assertEqual(n["type"], "tuic")
        self.assertEqual(n["id"], "uuid-9")
        self.assertEqual(n["pass"], "tok")
        self.assertEqual(n["congestion_control"], "reno")
        conf = json.loads(v2c.build_core_config(n, 10809, 10808, core_type=v2c.CORE_SING))
        ob = conf["outbounds"][0]
        self.assertEqual(ob["type"], "tuic")
        self.assertEqual(ob["uuid"], "uuid-9")
        self.assertEqual(ob["password"], "tok")
        self.assertEqual(ob["congestion_control"], "reno")
        self.assertEqual(ob["tls"]["alpn"], ["h3", "h3-16"])

    def test_03_wireguard_json(self):
        wg = {
            "address": ["172.16.0.2/32"],
            "private_key": "priv-key-1",
            "peers": [{"public_key": "pub-key-1", "endpoint": "5.6.7.8:51820",
                       "allowed_ips": "0.0.0.0/0,::/0"}],
        }
        n = v2c.parse_share_link("wireguard://%s/?remarks=wg1" % b64url(json.dumps(wg)))
        self.assertIsNotNone(n)
        self.assertEqual(n["type"], "wireguard")
        self.assertEqual(n["addr"], "5.6.7.8")
        self.assertEqual(n["port"], 51820)
        self.assertEqual(n["wg_private"], "priv-key-1")
        conf = json.loads(v2c.build_core_config(n, 10809, 10808, core_type=v2c.CORE_SING))
        ob = conf["outbounds"][0]
        self.assertEqual(ob["type"], "wireguard")
        self.assertEqual(ob["server"], "5.6.7.8")
        self.assertEqual(ob["private_key"], "priv-key-1")
        self.assertEqual(ob["peer_public_key"], "pub-key-1")

    def test_04_wireguard_simple(self):
        n = v2c.parse_share_link(
            "wireguard://privkey@1.2.3.4:51820?pk=pubkey&local_address=10.0.0.2#wg")
        self.assertIsNotNone(n)
        self.assertEqual(n["addr"], "1.2.3.4")
        self.assertEqual(n["wg_private"], "privkey")
        self.assertEqual(n["wg_peers"][0]["public_key"], "pubkey")

    def test_05_anytls(self):
        n = v2c.parse_share_link("anytls://pw@1.2.3.4:443?security=tls&sni=x#any")
        self.assertIsNotNone(n)
        self.assertEqual(n["type"], "anytls")
        self.assertEqual(n["id"], "pw")
        self.assertEqual(n["tls"], "tls")
        conf = json.loads(v2c.build_core_config(n, 10809, 10808, core_type=v2c.CORE_SING))
        ob = conf["outbounds"][0]
        self.assertEqual(ob["type"], "anytls")
        self.assertEqual(ob["password"], "pw")
        self.assertTrue(ob["tls"]["enabled"])

    def test_06_v2rayn_internal(self):
        prof = {"configType": 5, "address": "1.2.3.4", "port": 443, "id": "uuid-7",
                "remarks": "内网节点", "network": "ws", "streamSecurity": "tls",
                "sni": "cdn.example.com", "path": "/x"}
        n = v2c.parse_share_link("v2rayn://vless/%s" % b64url(json.dumps(prof)))
        self.assertIsNotNone(n)
        self.assertEqual(n["type"], "vless")
        self.assertEqual(n["addr"], "1.2.3.4")
        self.assertEqual(n["network"], "ws")
        self.assertEqual(n["tls"], "tls")
        self.assertEqual(n["sni"], "cdn.example.com")
        # 数字 ConfigType 兜底
        n2 = v2c.parse_share_link("v2rayn://other/%s" % b64url(json.dumps(prof)))
        self.assertIsNotNone(n2)
        self.assertEqual(n2["type"], "vless")


# =====================================================================
# v3.1：sing-box 配置生成（三核心之一）
# =====================================================================
class TestSingboxConfig(unittest.TestCase):
    def setUp(self):
        self.n = v2c.parse_share_link(
            "vless://uuid@1.2.3.4:443?security=tls&sni=c&type=ws&path=/p#x")

    def test_01_vless_ws_tls_snapshot(self):
        conf = json.loads(v2c.build_core_config(
            self.n, 10809, 10808, core_type=v2c.CORE_SING, mode=v2c.MODE_SMART))
        self.assertEqual(conf["inbounds"][0]["type"], "socks")
        self.assertEqual(conf["inbounds"][0]["listen_port"], 10808)
        self.assertEqual(conf["inbounds"][1]["type"], "http")
        self.assertEqual(conf["inbounds"][1]["listen_port"], 10809)
        ob = conf["outbounds"][0]
        self.assertEqual(ob["type"], "vless")
        self.assertEqual(ob["server"], "1.2.3.4")
        self.assertEqual(ob["uuid"], "uuid")
        self.assertTrue(ob["tls"]["enabled"])
        self.assertEqual(ob["transport"]["type"], "ws")
        self.assertEqual(conf["route"]["final"], "proxy-main")
        self.assertIn({"type": "direct", "tag": "direct"}, conf["outbounds"])

    def test_02_global_no_rules(self):
        conf = json.loads(v2c.build_core_config(
            self.n, 10809, 10808, core_type=v2c.CORE_SING, mode=v2c.MODE_GLOBAL))
        self.assertEqual(conf["route"]["rules"], [])

    def test_03_tun_inbound(self):
        conf = json.loads(v2c.build_core_config(
            self.n, 10809, 10808, core_type=v2c.CORE_SING, mode=v2c.MODE_GLOBAL, tun=True))
        self.assertEqual(conf["inbounds"][0]["type"], "tun")
        self.assertTrue(conf["inbounds"][0]["auto_route"])

    def test_04_advanced_rules_list(self):
        adv = {"routing": [
            {"outboundTag": "block", "domain": ["geosite:category-ads-all"]},
            {"outboundTag": "direct", "ip": ["geoip:cn"]},
        ]}
        conf = json.loads(v2c.build_core_config(
            self.n, 10809, 10808, core_type=v2c.CORE_SING, mode=v2c.MODE_GLOBAL,
            advanced=adv))
        tags = [r.get("outbound") for r in conf["route"]["rules"]]
        self.assertEqual(tags, ["block", "direct"])
        self.assertIn({"type": "block", "tag": "block"}, conf["outbounds"])

    def test_05_advanced_dns(self):
        # 旧格式（字符串 servers）需迁移为 1.12+ 对象形式，否则 sing-box FATAL
        adv = {"dns": {"servers": ["8.8.8.8"]}}
        conf = json.loads(v2c.build_core_config(
            self.n, 10809, 10808, core_type=v2c.CORE_SING, advanced=adv))
        servers = conf["dns"]["servers"]
        self.assertTrue(all(isinstance(x, dict) for x in servers))
        self.assertEqual(servers[0]["server"], "8.8.8.8")
        self.assertEqual(conf["dns"]["strategy"], "ipv4_only")


# =====================================================================
# v3.1：路由规则 / 广告拦截
# =====================================================================
class TestRouting(unittest.TestCase):
    def setUp(self):
        self.n = v2c.parse_share_link("vless://uuid@1.2.3.4:443?security=none#n")

    def test_01_xray_smart_with_geo(self):
        with mock.patch.object(v2c, "_geo_available", return_value=True):
            conf = json.loads(v2c.build_core_config(self.n, 10809, 10808, mode=v2c.MODE_SMART))
        rules = conf["routing"]["rules"]
        tags = [r.get("outboundTag") for r in rules]
        self.assertIn("block", tags)  # 广告拦截 geosite:category-ads-all
        self.assertIn("direct", tags)
        self.assertIn("proxy-main", tags)
        ad = next(r for r in rules if r.get("outboundTag") == "block")
        self.assertIn("geosite:category-ads-all", ad["domain"])

    def test_02_xray_fallback_no_geo(self):
        with mock.patch.object(v2c, "_geo_available", return_value=False):
            conf = json.loads(v2c.build_core_config(self.n, 10809, 10808, mode=v2c.MODE_SMART))
        rules = conf["routing"]["rules"]
        self.assertIn("10.0.0.0/8", rules[0]["ip"])  # 内置内网直连 CIDR

    def test_03_global_only_proxy(self):
        with mock.patch.object(v2c, "_geo_available", return_value=True):
            conf = json.loads(v2c.build_core_config(self.n, 10809, 10808, mode=v2c.MODE_GLOBAL))
        self.assertEqual([r.get("outboundTag") for r in conf["routing"]["rules"]], ["proxy-main"])

    def test_04_v2rayn_rules_convert_xray(self):
        rules = v2c._xray_rules_from_v2rayn([
            {"outboundTag": "proxy", "port": "80,443", "network": "tcp", "enabled": True},
            {"outboundTag": "direct", "ip": ["1.1.1.1"], "enabled": True},
            {"outboundTag": "block", "domain": ["ads.com"], "enabled": False},  # 禁用跳过
            {},
        ])
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0]["outboundTag"], "proxy-main")
        self.assertEqual(rules[0]["port"], "80,443")
        self.assertEqual(rules[1]["outboundTag"], "direct")

    def test_05_v2rayn_rules_convert_singbox(self):
        rules = v2c._singbox_rules_from_v2rayn([
            {"outboundTag": "block", "domain": ["ads.com"], "enabled": True},
            {"outboundTag": "direct", "port": "8000-8100", "enabled": True},
        ])
        self.assertEqual(rules[0]["outbound"], "block")
        self.assertEqual(rules[1]["port_range"], "8000-8100")

    def test_06_advanced_rules_override_xray(self):
        adv = {"routing": [
            {"outboundTag": "direct", "domain": ["example.com"], "enabled": True},
        ]}
        conf = json.loads(v2c.build_core_config(self.n, 10809, 10808,
                                                mode=v2c.MODE_SMART, advanced=adv))
        rules = conf["routing"]["rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["outboundTag"], "direct")

    def test_07_singbox_smart_uses_rule_set(self):
        """sing-box 智能分流必须用 rule_set（.srs）——旧 geoip/geosite 字段在
        1.12 起被移除，继续输出会让核心 FATAL 起不来（实测报
        "geoip database is deprecated ... removed in sing-box 1.12.0"）。"""
        geo = fake_geo_dir()
        with mock.patch.object(v2c, "GEO_DIR", geo), \
             mock.patch.object(v2c, "_geo_sing_available", return_value=True):
            conf = json.loads(v2c.build_core_config(
                self.n, 10809, 10808, core_type=v2c.CORE_SING, mode=v2c.MODE_SMART))
        route = conf["route"]
        tags = [r["tag"] for r in route["rule_set"]]
        # 扫描 GEO_DIR 全部 .srs（标签=文件名去扩展名，字母序）
        self.assertEqual(tags, ["geoip-cn", "geoip-private", "geosite-ads", "geosite-cn"])
        for rs in route["rule_set"]:
            self.assertEqual(rs["type"], "local")
            self.assertEqual(rs["format"], "binary")
            self.assertTrue(rs["path"].endswith(".srs"))
        # 不得出现已移除的旧字段
        self.assertNotIn("geoip", route)
        self.assertNotIn("geosite", route)
        for r in route["rules"]:
            self.assertNotIn("geoip", r)
            self.assertNotIn("geosite", r)
        # 广告拦截用 action=reject（新版写法）
        self.assertIn({"rule_set": ["geosite-ads"], "action": "reject"}, route["rules"])
        # 域名规则在 IP 规则之前：国内域名无需先解析即可直连
        rs_order = [r.get("rule_set") for r in route["rules"] if r.get("rule_set")]
        self.assertLess(rs_order.index(["geosite-cn"]),
                        rs_order.index(["geoip-private", "geoip-cn"]))

    def test_07b_singbox_dns_prefers_ipv4_public_resolver(self):
        """DNS 段必须指定解析器且只走 IPv4。

        用系统解析器（local 类型）在本机实测每次冷解析约 5 秒（IPv6/AAAA 等待），
        表现为「每个新网站第一次打开都要等好几秒」；换公共 DNS + ipv4_only 后
        首连 0.11 秒。此外 route.default_domain_resolver 引用的标签必须存在，
        否则核心直接 FATAL。
        """
        conf = json.loads(v2c.build_core_config(
            self.n, 10809, 10808, core_type=v2c.CORE_SING, mode=v2c.MODE_SMART))
        dns = conf["dns"]
        self.assertEqual(dns["strategy"], "ipv4_only")
        servers = dns["servers"]
        self.assertTrue(servers and servers[0].get("server"),
                        "必须显式指定 DNS 服务器地址，不能用系统解析器")
        tags = {s["tag"] for s in servers}
        self.assertIn(conf["route"]["default_domain_resolver"], tags)

    def test_08_v2rayn_rules_convert_to_rule_set(self):
        """v2rayN 规则里的 geoip:/geosite: 前缀 → rule_set 引用（1.12+ 移除旧字段）。"""
        geo = fake_geo_dir()
        with mock.patch.object(v2c, "GEO_DIR", geo):
            rules = v2c._singbox_rules_from_v2rayn([
                {"outboundTag": "direct", "ip": ["geoip:cn", "1.1.1.1"], "enabled": True},
                {"outboundTag": "block", "domain": ["geosite:category-ads-all", "ads.com"],
                 "enabled": True},
            ])
        self.assertEqual(rules[0]["rule_set"], ["geoip-cn"])
        self.assertEqual(rules[0]["ip_cidr"], ["1.1.1.1"])
        self.assertEqual(rules[1]["rule_set"], ["geosite-ads"])
        self.assertEqual(rules[1]["domain"], ["ads.com"])
        # 无法映射的 geo 前缀不能原样输出（会生成非法配置）
        with mock.patch.object(v2c, "GEO_DIR", geo):
            rules2 = v2c._singbox_rules_from_v2rayn([
                {"outboundTag": "direct", "domain": ["geosite:google"], "enabled": True},
            ])
        self.assertEqual(rules2, [])

    def test_08b_geo_prefix_maps_any_downloaded_category(self):
        """统一分流规则按类别下载的 .srs（如 geosite-telegram.srs）自动可映射。"""
        geo = fake_geo_dir()
        with open(os.path.join(geo, "geosite-telegram.srs"), "wb") as f:
            f.write(b"SRS\x01")
        with mock.patch.object(v2c, "GEO_DIR", geo):
            rules = v2c._singbox_rules_from_v2rayn([
                {"outboundTag": "proxy", "domain": ["geosite:telegram"], "enabled": True},
                {"outboundTag": "direct", "ip": ["geoip:private"], "enabled": True},
            ])
        self.assertEqual(rules[0]["rule_set"], ["geosite-telegram"])
        self.assertEqual(rules[1]["rule_set"], ["geoip-private"])


# =====================================================================
# v3.1：批量测速
# =====================================================================
class TestBurstTest(unittest.TestCase):
    def setUp(self):
        self.mgr = make_manager([N, N2])

    def test_01_sort_and_persist(self):
        self.mgr.test_node = lambda i: {"ok": True, "latency": {0: 90, 1: 30}[i]}
        with mock.patch("time.sleep"):
            results = self.mgr.burst_test()
        self.assertEqual([r["latency"] for r in results], [30, 90])
        nodes = self.mgr.store.nodes()
        self.assertEqual([n["latency"] for n in nodes], [90, 30])  # 按原索引回写

    def test_02_failure_last(self):
        self.mgr.test_node = lambda i: {"ok": True, "latency": 10} if i == 0 else \
            {"ok": False, "err": "timeout"}
        with mock.patch("time.sleep"):
            results = self.mgr.burst_test([0, 1])
        self.assertFalse(results[-1]["ok"])  # 失败排在最后
        self.assertEqual(results[0]["index"], 0)

    def test_03_index_filter(self):
        self.mgr.test_node = lambda i: {"ok": True, "latency": 20}
        with mock.patch("time.sleep"):
            results = self.mgr.burst_test([1])
        self.assertEqual([r["index"] for r in results], [1])


# =====================================================================
# v3.1：系统代理策略（对齐 v2rayN 四策略）
# =====================================================================
class TestSysProxyStrategy(unittest.TestCase):
    def setUp(self):
        self.mgr = make_manager([N])

    def test_01_switch_mode(self):
        self.mgr.sys_proxy_mode = v2c.SYS_PROXY_AUTO
        m = self.mgr.set_sys_proxy_mode(v2c.SYS_PROXY_PAC)
        self.assertEqual(m, v2c.SYS_PROXY_PAC)
        self.assertEqual(self.mgr.sys_proxy_mode, v2c.SYS_PROXY_PAC)

    def test_02_invalid_mode(self):
        with self.assertRaises(ValueError):
            self.mgr.set_sys_proxy_mode("bogus")

    @mock.patch.object(v2c.V2rayCoreManager, "set_sys_proxy_pac")
    @mock.patch.object(v2c.V2rayCoreManager, "set_sys_proxy")
    @mock.patch.object(v2c.V2rayCoreManager, "_apply_sysproxy_start")
    def test_03_switch_while_running(self, ap_start, sp, pac):
        # 运行中切策略：先还原旧配置，再按新策略应用
        self.mgr.running = True
        self.mgr._old_proxy = {"enable": 1, "server": "1.2.3.4:8080", "override": ""}
        self.mgr.set_sys_proxy_mode(v2c.SYS_PROXY_AUTO)
        sp.assert_any_call(False)
        ap_start.assert_called_once_with()

    def test_04_state_exposes_strategy(self):
        self.mgr.sys_proxy_mode = v2c.SYS_PROXY_CLEAR
        self.assertEqual(self.mgr.state()["sys_proxy_mode"], v2c.SYS_PROXY_CLEAR)
        self.assertIn("core_type", self.mgr.state())
        self.assertIn("tun", self.mgr.state())


# =====================================================================
# v3.1：二进制下载 spec（三核心 + GeoIP 规则库 + wintun）
# =====================================================================
class TestBindlSpecs(unittest.TestCase):
    def test_01_three_cores(self):
        for kind in ("xray", "v2ray", "sing-box"):
            self.assertIn(kind, bindl._REPOS)

    def test_02_singbox_wintun_companion(self):
        spec = bindl._REPOS["sing-box"]
        self.assertIn("wintun.dll", spec.get("companions", ()))

    def test_03_geo_rules_raw(self):
        for kind in ("geoip", "geosite"):
            self.assertTrue(bindl._REPOS[kind].get("raw"))
            self.assertEqual(bindl._REPOS[kind]["repo"], "Loyalsoldier/v2ray-rules-dat")

    def test_04_singbox_srs_rulesets(self):
        # sing-box 规则集：.srs 固定 URL（旧 .db 在 1.12 已移除，官方仓库只发 .db → 改用 MetaCubeX sing 分支）
        for kind in ("geosite-cn", "geosite-ads", "geoip-cn", "geoip-private"):
            self.assertIn(kind, bindl._SRS_URLS)
            self.assertTrue(bindl._SRS_URLS[kind].endswith(".srs"))
        self.assertTrue(callable(bindl.download_ruleset))

    def test_05_verify_srs_magic(self):
        # 规则集校验看文件头 SRS magic，不看体积（private.srs 仅 144 字节）
        import tempfile as _tf
        ok = os.path.join(TMP, "ok.srs")
        bad = os.path.join(TMP, "bad.srs")
        with open(ok, "wb") as f:
            f.write(b"SRS" + b"x" * 10)
        with open(bad, "wb") as f:
            f.write(b"<html>not a ruleset</html>")
        self.assertTrue(bindl._verify_srs(ok))
        self.assertFalse(bindl._verify_srs(bad))


"""被误删的测试类（合并回 test_v2ray.py 用，合并后本文件即删）。"""


class TestCoreCompat(unittest.TestCase):
    """协议↔核心兼容：anytls/hysteria2/tuic 仅 sing-box（Xray 官方无 anytls）。"""

    def test_01_protocol_matrix(self):
        for proto in ("vless", "vmess", "trojan", "ss", "socks", "http"):
            for core in v2c.CORES:
                self.assertTrue(v2c.protocol_supported(proto, core),
                                "%s 应支持 %s" % (core, proto))
        for proto in ("anytls", "hy2", "tuic"):
            self.assertFalse(v2c.protocol_supported(proto, v2c.CORE_XRAY))
            self.assertFalse(v2c.protocol_supported(proto, v2c.CORE_V2RAY))
            self.assertTrue(v2c.protocol_supported(proto, v2c.CORE_SING))
        self.assertEqual(set(v2c.protocol_cores("")), set(v2c.CORES))

    def test_02_auto_switch_to_available_core(self):
        m = v2c.V2rayCoreManager()
        m.core_type = v2c.CORE_XRAY
        m.bin_path = ""
        with mock.patch.object(
                v2c, "_core_bin_candidates",
                side_effect=lambda core, *a: ((r"X:\none.exe",) if core == v2c.CORE_XRAY
                                              else (__file__,))):
            m._ensure_core_for_node({"type": "anytls", "addr": "a", "port": 1})
        self.assertEqual(m.core_type, v2c.CORE_SING)
        self.assertTrue(m.bin_path)

    def test_03_clear_error_when_no_core(self):
        m = v2c.V2rayCoreManager()
        m.core_type = v2c.CORE_XRAY
        with mock.patch.object(v2c, "_core_bin_candidates",
                               return_value=(r"X:\none.exe",)):
            with self.assertRaises(ValueError) as ctx:
                m._ensure_core_for_node({"type": "anytls", "addr": "a", "port": 1})
        msg = str(ctx.exception)
        self.assertIn("anytls", msg)
        self.assertIn("sing-box", msg)
        self.assertIn("不支持", msg)

    def test_04_supported_node_no_change(self):
        m = v2c.V2rayCoreManager()
        m.core_type = v2c.CORE_XRAY
        m.bin_path = "x"
        m._ensure_core_for_node({"type": "vless", "addr": "a", "port": 1})
        self.assertEqual(m.core_type, v2c.CORE_XRAY)
        self.assertEqual(m.bin_path, "x")

    def test_05_download_dir_in_candidates(self):
        """下载器落盘目录（DATA_HOME/<核心>/bin）必须在候选里。

        历史缺陷：只在 proxy/bin、proxy/ 找，导致「已下载却提示未找到核心」。
        """
        for core, exe in ((v2c.CORE_XRAY, "xray.exe"),
                          (v2c.CORE_SING, "sing-box.exe"),
                          (v2c.CORE_V2RAY, "v2ray.exe")):
            cands = v2c._core_bin_candidates(core)
            self.assertTrue(any(c.endswith(os.path.join(core, "bin", exe))
                                for c in cands), (core, cands))


class TestInfoNodes(unittest.TestCase):
    """订阅信息行（剩余流量/套餐到期）：不当节点导入，存量数据加载时清理。"""

    def test_01_detect(self):
        self.assertTrue(v2c.is_info_node({"remark": "剩余流量：82.72 GB"}))
        self.assertTrue(v2c.is_info_node({"remark": "套餐到期：2026-10-01"}))
        self.assertFalse(v2c.is_info_node({"remark": "🇭🇰 香港 01"}))
        self.assertFalse(v2c.is_info_node({}))

    def test_02_parse_skips_info_rows(self):
        text = "\n".join([
            "anytls://pw@kkkhhh.xs-us.net:8001#%s" % b64url("剩余流量：82.72 GB"),
            "anytls://pw@kkkhhh.xs-us.net:8011#%s" % b64url("台湾01"),
        ])
        nodes = v2c.parse_sub_text(text)
        self.assertEqual(len(nodes), 1)
        self.assertIn("台湾", nodes[0]["remark"])

    def test_03_load_purges_and_clamps_selected(self):
        path = os.path.join(TMP, "nodes_info.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"sub_url": "", "selected": 1, "nodes": [
                {"type": "anytls", "addr": "a", "port": 1, "id": "i1",
                 "remark": "剩余流量：82.72 GB"},
                {"type": "anytls", "addr": "b", "port": 2, "id": "i2",
                 "remark": "日本01"},
                {"type": "anytls", "addr": "c", "port": 3, "id": "i3",
                 "remark": "美国01"},
            ]}, f, ensure_ascii=False)
        store = v2c.NodeStore(path)
        self.assertEqual([n["remark"] for n in store.nodes()], ["日本01", "美国01"])
        self.assertEqual(store.data["selected"], 0)

    def test_04_switch_skips_info_and_advances(self):
        """自动切换必须真的往后走（历史缺陷：原地重试同一节点）。"""
        m = v2c.V2rayCoreManager()
        m.current = 0
        nodes = [{"type": "anytls", "addr": "n%d" % i, "port": i, "id": "id%d" % i,
                  "remark": "节点%d" % i} for i in range(4)]
        nodes.insert(1, {"type": "anytls", "addr": "x", "port": 9, "id": "ix",
                         "remark": "剩余流量：1 GB"})
        m._resolve_nodes = lambda: nodes
        tried = []

        def fake_start(idx):
            tried.append(idx)
            if idx < 4:           # 前面的节点启动失败，最后一个成功
                raise ValueError("boom")

        m._start_core = fake_start
        self.assertTrue(m._switch())
        self.assertNotIn(1, tried)          # 信息行被跳过
        self.assertEqual(tried, [2, 3, 4])  # 依次向后，而非原地重试


class TestAnyTlsConfig(unittest.TestCase):
    def test_01_singbox_outbound_no_padding_field(self):
        """anytls 出站不得带 padding 字段：sing-box 会 FATAL unknown field。"""
        ob = v2c._singbox_outbound({
            "type": "anytls", "addr": "a.example.com", "port": 443,
            "id": "pw", "sni": "s.example.com",
        })
        self.assertEqual(ob["type"], "anytls")
        self.assertEqual(ob["password"], "pw")
        self.assertNotIn("padding", ob)
        self.assertTrue(ob["tls"]["enabled"])
        self.assertEqual(ob["tls"]["server_name"], "s.example.com")


class TestRefreshBins(unittest.TestCase):
    """刷新组件：本地探测立即返回，版本检查后台推送（不阻塞、不弹窗）。"""

    def _shim(self):
        from app.bridge.proxy_api import ProxyApi
        from app.core.config import AppConfig

        class Shim(ProxyApi):
            def __init__(self, cfg):
                self.cfg = cfg
                self.events = []
                self._init_proxy()

            def emit(self, name, data=None):
                self.events.append((name, data))

        return Shim(AppConfig(os.path.join(TMP, "cfg-refresh.json")))

    def test_01_returns_immediately_and_emits_async(self):
        api = self._shim()
        with mock.patch("app.core.bindl.latest_version", return_value="v9.9.9"):
            t0 = time.time()
            r = api.proxy_refresh_bins()
            dt = time.time() - t0
        self.assertTrue(r["ok"], r)
        self.assertLess(dt, 3.0, "必须立即返回，不能等网络查询")
        self.assertTrue(r["data"]["checking"])
        deadline = time.time() + 10
        evt = None
        while time.time() < deadline and evt is None:
            evt = next((d for n, d in api.events if n == "proxy_bins"), None)
            if evt is None:
                time.sleep(0.1)
        self.assertIsNotNone(evt, "应推送 proxy_bins 事件")
        self.assertEqual(evt["latest"], "v9.9.9")
        self.assertIn("outdated", evt)

    def test_02_geo_available_helper(self):
        self.assertIsInstance(v2c.geo_available(v2c.CORE_XRAY), bool)
        self.assertIsInstance(v2c.geo_available(v2c.CORE_SING), bool)


class TestNoConsoleWindow(unittest.TestCase):
    """窗口化进程里启动控制台程序必须加 CREATE_NO_WINDOW（否则弹黑窗，关窗即杀服务）。"""

    def test_all_subprocess_calls_suppress_console(self):
        import glob
        import re
        bad = []
        for path in glob.glob("app/**/*.py", recursive=True):
            with open(path, encoding="utf-8") as f:
                src = f.read()
            for m in re.finditer(r"subprocess\.(run|Popen)\(", src):
                depth, i = 0, m.end() - 1
                while i < len(src):
                    if src[i] == "(":
                        depth += 1
                    elif src[i] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                block = src[m.start():i]
                if "creationflags" in block or "explorer" in block:
                    continue
                line_no = src.count("\n", 0, m.start()) + 1
                bad.append("%s:%d" % (path, line_no))
        self.assertEqual(bad, [], "以下子进程调用未禁用控制台窗口：%s" % bad)


"""本轮审计新增的回归测试（合并进 test_v2ray.py 后删除本文件）。"""


class TestCoreAudit(unittest.TestCase):
    """其他核心审计：xray/v2ray 测速环境变量、探测误判、sing-box DNS 迁移。"""

    def test_01_core_env_for_geo_assets(self):
        """xray/v2ray 子进程必须带规则库环境变量（启动、测速、批量测速三条路径）。

        历史缺陷：test_node/burst_test 的临时核心没带环境变量 →
        "failed to open file: geosite.dat" 启动失败，测速全部失败。
        """
        m = v2c.V2rayCoreManager()
        for core, key in ((v2c.CORE_XRAY, "XRAY_LOCATION_ASSET"),
                          (v2c.CORE_V2RAY, "V2RAY_LOCATION_ASSET")):
            m.core_type = core
            env = m._core_env()
            self.assertIsInstance(env, dict)
            self.assertEqual(env.get(key), v2c.GEO_DIR)
        m.core_type = v2c.CORE_SING
        self.assertIsNone(m._core_env())      # sing-box 用绝对路径，无需环境变量

    def test_02_test_node_passes_env(self):
        """test_node 启动临时核心时必须传环境变量（否则 xray/v2ray 必失败）。"""
        m = v2c.V2rayCoreManager()
        m.core_type = v2c.CORE_XRAY
        m.bin_path = __file__                 # 任意存在的文件即可（Popen 被 mock）
        m._resolve_nodes = lambda: [{"type": "trojan", "addr": "a", "port": 1, "id": "x"}]
        m._node = lambda i: {"type": "trojan", "addr": "a", "port": 1, "id": "x"}
        fake = mock.MagicMock()
        fake.poll.return_value = 0            # 立即退出 → 走"启动失败"分支
        with mock.patch("subprocess.Popen", return_value=fake) as popen, \
             mock.patch.object(v2c, "_free_ports", return_value=(1, 2)):
            m.test_node(0)
        kwargs = popen.call_args[1]
        self.assertIn("env", kwargs)
        self.assertEqual((kwargs["env"] or {}).get("XRAY_LOCATION_ASSET"), v2c.GEO_DIR)
        self.assertIn("creationflags", kwargs)

    def test_03_probe_never_bypasses_proxy(self):
        """探测必须全程走本地代理端口。

        历史缺陷：ProxyHandler 只给了 http，https 探测 URL 会**直连**——
        本地代理已死也能"探测成功"，启动探活/健康检查/测速全部误判
        （实测出现 12 秒的假成功）。
        """
        # 死端口必须探测失败（若 https 直连生效，这里会返回数值）
        self.assertIsNone(v2c._probe_http_port(9, timeout=3))
        # 检查实现：http/https 都必须映射到代理
        import inspect
        src = inspect.getsource(v2c._probe_http_port)
        self.assertIn('"https"', src)
        self.assertIn("proxy_bypass", src)

    def test_04_migrate_singbox_legacy_dns(self):
        """旧格式 DNS（字符串 servers）必须迁移为 1.12+ 对象形式。

        历史缺陷：高级配置里的 `{"servers": ["223.5.5.5"]}` 会让 sing-box
        FATAL（cannot unmarshal string into option._DNSServerOptions）。
        """
        got = v2c._migrate_singbox_dns({
            "servers": ["223.5.5.5", "https://dns.google/dns-query", "local",
                        "tls://8.8.8.8"],
            "rules": [{"domain": ["cn"], "server": "223.5.5.5"}],
        })
        servers = got["servers"]
        self.assertTrue(all(isinstance(x, dict) for x in servers))
        self.assertEqual(servers[0]["type"], "udp")
        self.assertEqual(servers[0]["server"], "223.5.5.5")
        self.assertEqual(servers[1]["type"], "https")
        self.assertEqual(servers[1]["server"], "dns.google")
        self.assertEqual(servers[1]["path"], "/dns-query")
        self.assertEqual(servers[2]["type"], "local")
        # 域名类服务器要有引导解析器，否则 FATAL
        self.assertEqual(servers[1]["domain_resolver"], servers[0]["tag"])
        # rules 里的地址引用换成迁移后的 tag
        self.assertEqual(got["rules"][0]["server"], servers[0]["tag"])

    def test_05_migrate_bootstrap_when_only_domain_servers(self):
        got = v2c._migrate_singbox_dns({"servers": ["https://dns.google/dns-query"]})
        tags = [s["tag"] for s in got["servers"]]
        self.assertIn("bootstrap", tags)                       # 自动补引导解析器
        dom = next(s for s in got["servers"] if s["type"] == "https")
        self.assertEqual(dom["domain_resolver"], "bootstrap")

    def test_06_advanced_dns_merge_keeps_valid_refs(self):
        """自定义 DNS 合并后 final / default_domain_resolver 必须指向存在的标签。"""
        node = {"type": "anytls", "addr": "a.example.com", "port": 443,
                "id": "pw", "sni": "s.example.com"}
        conf = json.loads(v2c.build_singbox_config(
            node, 10809, 10808, mode="smart",
            advanced={"dns": {"servers": ["1.1.1.1"]}}))
        tags = {s["tag"] for s in conf["dns"]["servers"]}
        self.assertIn(conf["dns"]["final"], tags)
        resolver = conf["route"].get("default_domain_resolver")
        if isinstance(resolver, dict):
            resolver = resolver.get("server")
        self.assertIn(resolver, tags)
        self.assertEqual(conf["dns"]["strategy"], "ipv4_only")   # 保留我们的默认

    def test_07_advanced_routing_keeps_rule_set_defs(self):
        """自定义路由列表替换规则时，必须保留 rule_set 定义与解析器标签。"""
        node = {"type": "anytls", "addr": "a.example.com", "port": 443,
                "id": "pw", "sni": "s.example.com"}
        with mock.patch.object(v2c, "GEO_DIR", fake_geo_dir()):
            conf = json.loads(v2c.build_singbox_config(
                node, 10809, 10808, mode="smart",
                advanced={"routing": [
                    {"outboundTag": "direct", "ip": ["geoip:cn"], "enabled": True},
                    {"outboundTag": "block", "domain": ["geosite:category-ads-all"], "enabled": True},
                ]}))
        route = conf["route"]
        defined = {rs["tag"] for rs in route.get("rule_set", [])}
        used = {t for r in route["rules"] for t in r.get("rule_set", [])}
        self.assertTrue(used)
        self.assertTrue(used <= defined, "引用了未定义的规则集：%s" % (used - defined))
        self.assertIn("default_domain_resolver", route)

    def test_08_advanced_routing_drops_undefined_rule_sets(self):
        """规则库缺失时，自定义规则里的 rule_set 引用必须被剔除（否则 FATAL）。"""
        node = {"type": "anytls", "addr": "a.example.com", "port": 443,
                "id": "pw", "sni": "s.example.com"}
        with mock.patch.object(v2c, "_geo_sing_available", return_value=False):
            conf = json.loads(v2c.build_singbox_config(
                node, 10809, 10808, mode="smart",
                advanced={"routing": [
                    {"outboundTag": "direct", "ip": ["geoip:cn"], "enabled": True},
                    {"outboundTag": "proxy", "domain": ["example.com"], "enabled": True},
                ]}))
        for r in conf["route"]["rules"]:
            self.assertNotIn("rule_set", r)


"""Clash Verge 风格功能（策略组/连接/订阅自动更新）的回归测试（合并后删除本文件）。"""


class TestGroups(unittest.TestCase):
    """策略组：定义存取、成员过滤、出站生成、配置集成。"""

    def setUp(self):
        self._bak = v2c.GROUPS_FILE
        self.path = os.path.join(TMP, "groups-test.json")
        v2c.GROUPS_FILE = self.path

    def tearDown(self):
        v2c.GROUPS_FILE = self._bak

    def test_01_save_load_roundtrip(self):
        saved = v2c.save_groups([
            {"name": "自动选择", "type": "urltest", "filter": "*", "interval": "3m"},
            {"name": "香港", "type": "selector", "filter": "香港, HK", "default": True},
            {"name": "", "type": "selector"},          # 空名丢弃
        ])
        self.assertEqual(len(saved), 2)
        got = v2c.load_groups()
        self.assertEqual([g["name"] for g in got], ["自动选择", "香港"])
        self.assertEqual(got[0]["type"], "urltest")
        self.assertEqual(got[1]["filter"], "香港, HK")
        self.assertTrue(got[1].get("default"))

    def test_02_group_members_filter(self):
        nodes = [
            {"remark": "🇭🇰 香港 01", "addr": "a.com", "type": "trojan"},
            {"remark": "🇯🇵 日本01", "addr": "b.com", "type": "anytls"},
            {"remark": "🇭🇰 香港 02", "addr": "c.com", "type": "trojan"},
        ]
        self.assertEqual(v2c.group_members({"filter": "*"}, nodes), [0, 1, 2])
        self.assertEqual(v2c.group_members({"filter": ""}, nodes), [0, 1, 2])
        self.assertEqual(v2c.group_members({"filter": "香港"}, nodes), [0, 2])
        self.assertEqual(v2c.group_members({"filter": "日本, anytls"}, nodes), [1])
        self.assertEqual(v2c.group_members({"filter": "不存在"}, nodes), [])

    def test_03_build_group_outbounds(self):
        nodes = [{"remark": "n%d" % i, "addr": "a%d" % i, "port": 1, "type": "anytls",
                  "id": "p"} for i in range(3)]
        groups = [{"name": "自动", "type": "urltest", "filter": "*"},
                  {"name": "手动", "type": "selector", "filter": "*", "default": True}]
        out, tags, primary = v2c.build_group_outbounds(nodes, groups)
        self.assertEqual(tags, ["自动", "手动"])
        self.assertEqual(primary, "手动")           # default 标记生效
        by_tag = {o["tag"]: o for o in out}
        self.assertEqual(sorted(by_tag["自动"]["outbounds"]), ["node-0", "node-1", "node-2"])
        self.assertEqual(by_tag["自动"]["type"], "urltest")
        # selector 嵌套已定义的组（Clash 的「节点选择」含「自动选择」）
        self.assertEqual(by_tag["手动"]["outbounds"][0], "自动")
        self.assertEqual(by_tag["手动"]["type"], "selector")

    def test_04_config_integration(self):
        nodes = [{"remark": "n%d" % i, "addr": "a%d" % i, "port": 1, "type": "anytls",
                  "id": "p"} for i in range(2)]
        groups = [{"name": "自动", "type": "urltest", "filter": "*", "default": True}]
        conf = json.loads(v2c.build_singbox_config(
            nodes[0], 10809, 10808, mode="smart", nodes=nodes, groups=groups,
            clash_api={"external_controller": "127.0.0.1:1", "secret": "s"}))
        tags = [o["tag"] for o in conf["outbounds"]]
        self.assertIn("node-0", tags)
        self.assertIn("node-1", tags)
        self.assertIn("自动", tags)
        self.assertIn("direct", tags)
        self.assertEqual(conf["route"]["final"], "自动")
        self.assertIn("clash_api", conf.get("experimental", {}))
        # 智能分流规则里的 proxy-main 已改名为主组
        self.assertNotIn("proxy-main", json.dumps(conf["route"]["rules"]))
        # 无分组时保持原行为（单节点 proxy-main）
        conf2 = json.loads(v2c.build_singbox_config(nodes[0], 10809, 10808, mode="smart"))
        self.assertEqual(conf2["route"]["final"], "proxy-main")
        self.assertNotIn("experimental", conf2)

    def test_05_clash_api_only_singbox(self):
        node = {"remark": "n", "addr": "a", "port": 1, "type": "vless", "id": "u",
                "security": "none"}
        conf = json.loads(v2c.build_core_config(
            node, 10809, 10808, core_type=v2c.CORE_XRAY, clash_api={"x": 1}))
        self.assertNotIn("experimental", conf)       # xray 不受影响
        self.assertIn("proxy-main", json.dumps(conf["routing"]))


class TestBridgeSurface(unittest.TestCase):
    """桥接层与前端调用的方法必须存在（防漏接/拼写错误）。"""

    def test_01_frontend_calls_exist(self):
        """所有页面文件里调用的 proxy_* 桥接方法都必须存在。"""
        import glob
        import re
        from app.bridge.bridge import Bridge
        files = sorted(glob.glob("webui/js/pages/*.js")) + ["webui/js/app.js"]
        checked = 0
        for path in files:
            with open(path, encoding="utf-8") as f:
                src = f.read()
            calls = set(re.findall(r'App\.(?:tryCall|call)\(\s*"([a-z_]+)"', src))
            events = set(re.findall(r'App\.on\(\s*"([a-z_]+)"', src))
            calls -= events          # 事件名不是接口方法
            if not calls:
                continue
            checked += len(calls)
            missing = [c for c in sorted(calls) if not hasattr(Bridge, c)]
            self.assertEqual(missing, [], "%s 调用了不存在的桥接方法：%s" % (path, missing))
        self.assertGreater(checked, 5)

    def test_02_group_bridge_methods(self):
        from app.bridge.proxy_api import ProxyApi
        for name in ("proxy_groups", "proxy_save_groups", "proxy_group_select",
                     "proxy_connections", "proxy_close_connection",
                     "proxy_close_all_connections", "proxy_set_sub_autoupdate"):
            self.assertTrue(hasattr(ProxyApi, name), "缺少桥接方法：%s" % name)

    def test_03_sub_autoupdate_cfg_allowed(self):
        import re
        with open("app/bridge/bridge.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"sub_autoupdate_hours"', src)


if __name__ == "__main__":
    unittest.main()