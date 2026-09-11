"""统一分流规则中心测试：模型 / 编译器 / 双写应用 / 备份源更新。

运行：python -m pytest test_routing.py -q
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from app.core import bindl, clash_core as cc, routing_core as rc, v2ray_core as v2c


def _fake_geo():
    """临时 GEO_DIR：dat 占位 + 内置 4 件 .srs（SRS magic 头）。"""
    d = tempfile.mkdtemp(prefix="rt-geo-")
    for n in ("geoip.dat", "geosite.dat"):
        with open(os.path.join(d, n), "wb") as f:
            f.write(b"x" * 2048)
    for n in ("geosite-cn.srs", "geosite-ads.srs", "geoip-cn.srs", "geoip-private.srs"):
        with open(os.path.join(d, n), "wb") as f:
            f.write(b"SRS\x01")
    return d


class RoutingTestBase(unittest.TestCase):
    """目录常量全部指向临时目录，绝不读写真实数据目录。"""

    def setUp(self):
        d = tempfile.mkdtemp(prefix="rt-case-")
        self.rules_dir = os.path.join(d, "rules")
        self.clash_dir = os.path.join(d, "clash")
        self.proxy_dir = os.path.join(d, "proxy")
        self.geo = _fake_geo()
        self.patches = [
            mock.patch.object(rc, "UNIFIED_FILE", os.path.join(self.rules_dir, "unified.json")),
            mock.patch.object(cc, "CLASH_DIR", self.clash_dir),
            mock.patch.object(cc, "RULES_FILE", os.path.join(self.clash_dir, "rules.json")),
            mock.patch.object(v2c, "ADVANCED_FILE", os.path.join(self.proxy_dir, "advanced.json")),
            mock.patch.object(v2c, "PROXY_DIR", self.proxy_dir),
            mock.patch.object(v2c, "GEO_DIR", self.geo),
            mock.patch.object(cc, "MIHOMO_DIR", os.path.join(d, "mihomo", "bin")),
            mock.patch.object(cc, "GROUPS_FILE", os.path.join(self.clash_dir, "groups.json")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()


def _off():
    """全部精选集显式关闭（save_unified 对缺失键会回填默认开启值）。"""
    return {k: False for k in rc.PRESET_KEYS}


# =====================================================================
# 模型：parse_line / to_line / normalize
# =====================================================================
class TestModel(RoutingTestBase):
    def test_01_line_roundtrip(self):
        e = rc.parse_line("DOMAIN-SUFFIX,example.com,REJECT")
        self.assertEqual(e["type"], "DOMAIN-SUFFIX")
        self.assertEqual(e["policy"], "REJECT")
        self.assertEqual(rc.to_line(e), "DOMAIN-SUFFIX,example.com,REJECT")

    def test_02_policy_default_and_case(self):
        e = rc.parse_line("domain-keyword,ads")
        self.assertEqual((e["type"], e["policy"]), ("DOMAIN-KEYWORD", "PROXY"))
        e2 = rc.parse_line("IP-CIDR,10.0.0.0/8,direct")
        self.assertEqual(e2["policy"], "DIRECT")

    def test_03_geoip_no_resolve_value_kept(self):
        e = rc.parse_line("GEOIP,private,DIRECT,no-resolve")
        self.assertEqual(e["value"], "private,no-resolve")
        self.assertEqual(e["policy"], "DIRECT")

    def test_04_invalid_inputs_raise(self):
        for line in ("", "# 注释", "BADTYPE,x", "DOMAIN", "DOMAIN-SUFFIX, ,DIRECT",
                     "DOMAIN,x,WRONG"):
            with self.assertRaises(ValueError):
                rc.parse_line(line)

    def test_05_value_truncated(self):
        e = rc.parse_line("DOMAIN," + "a" * 500)
        self.assertEqual(len(e["value"]), 200)

    def test_06_to_line_proxy_maps_primary(self):
        self.assertEqual(rc.to_line({"type": "DOMAIN", "value": "a.com", "policy": "PROXY"},
                                    primary="自动选择"), "DOMAIN,a.com,自动选择")

    def test_07_is_managing_requires_file(self):
        self.assertFalse(rc.has_unified_file())
        self.assertFalse(rc.is_managing())
        rc.save_unified({"custom": [{"type": "DOMAIN", "value": "a.com",
                                     "policy": "DIRECT", "enabled": True}]})
        self.assertTrue(rc.is_managing())


# =====================================================================
# 编译器
# =====================================================================
class TestCompile(RoutingTestBase):
    def _data(self, **kw):
        d = rc.load_unified()
        d.update(kw)
        return d

    def test_01_mihomo_custom_first_then_presets(self):
        d = self._data(custom=[{"type": "DOMAIN-SUFFIX", "value": "example.com",
                                "policy": "REJECT", "enabled": True}])
        lines, warns = rc.compile_mihomo(d, primary="PROXY")
        self.assertEqual(lines[0], "DOMAIN-SUFFIX,example.com,REJECT")
        self.assertIn("GEOSITE,category-ads-all,REJECT", lines)
        self.assertIn("GEOSITE,cn,DIRECT", lines)
        self.assertIn("GEOIP,cn,DIRECT", lines)
        self.assertIn("GEOIP,private,DIRECT,no-resolve", lines)
        self.assertEqual(warns, [])

    def test_02_mihomo_proxy_maps_primary_group(self):
        d = self._data(custom=[{"type": "DOMAIN", "value": "a.com",
                                "policy": "PROXY", "enabled": True}],
                       presets=_off())
        lines, _ = rc.compile_mihomo(d, primary="自动选择")
        self.assertEqual(lines, ["DOMAIN,a.com,自动选择"])

    def test_03_mihomo_geo_missing_skips_with_warning(self):
        d = self._data(custom=[{"type": "GEOSITE", "value": "cn", "policy": "DIRECT",
                                "enabled": True}])
        lines, warns = rc.compile_mihomo(d, geo_ready=False)
        self.assertEqual(lines, [])
        self.assertTrue(any("未就绪" in w for w in warns))

    def test_04_v2rayn_prefix_mapping_and_merge(self):
        d = self._data(custom=[{"type": "DOMAIN", "value": "a.com", "policy": "PROXY",
                                "enabled": True},
                               {"type": "DOMAIN-KEYWORD", "value": "ads", "policy": "REJECT",
                                "enabled": True}],
                       presets={"ads": True, "cn": True, "lan": True})
        rules, warns = rc.compile_v2rayn(d)
        self.assertEqual(rules[0]["outboundTag"], "proxy")
        self.assertEqual(rules[0]["domain"], ["full:a.com"])
        self.assertEqual(rules[1]["outboundTag"], "block")
        self.assertEqual(rules[1]["domain"], ["keyword:ads"])
        cn = [r for r in rules if r.get("domain") == ["geosite:cn"]]
        self.assertEqual(len(cn), 1)
        self.assertEqual(cn[0]["ip"], ["geoip:cn"])
        self.assertEqual(warns, [])

    def test_05_v2rayn_port_and_process(self):
        d = self._data(custom=[{"type": "DST-PORT", "value": "443", "policy": "DIRECT",
                                "enabled": True},
                               {"type": "PROCESS-NAME", "value": "curl.exe",
                                "policy": "DIRECT", "enabled": True}],
                       presets=_off())
        rules, warns = rc.compile_v2rayn(d)
        self.assertEqual(rules[0]["port"], "443")
        self.assertEqual(len(rules), 1)
        self.assertTrue(any("进程" in w for w in warns))

    def test_06_singbox_known_category_maps_missing_skips(self):
        d = self._data(custom=[], presets={"ads": True, "cn": True, "lan": True,
                                           "gfw": True})
        out, warns = rc.compile_singbox(d)
        defined = set(out["rule_set"])
        self.assertIn("geosite-ads", defined)
        self.assertIn("geoip-private", defined)
        for r in out["rules"]:
            for t in r.get("rule_set", []):
                self.assertIn(t, defined)
        # gfw 类别（geolocation-!cn）无 .srs → 规则被剔除 + 警告
        self.assertNotIn("geosite-geolocation-!cn", defined)
        self.assertTrue(any("geolocation-!cn" in w for w in warns))

    def test_07_preset_ready(self):
        d = rc.load_unified()
        p = {x["key"]: x for x in (
            {"key": "ads", "geosite": ["category-ads-all"], "geoip": []},
            {"key": "gfw", "geosite": ["geolocation-!cn"], "geoip": []})}
        self.assertTrue(rc.preset_ready(p["ads"])["singbox"])    # geosite-ads.srs 存在
        self.assertFalse(rc.preset_ready(p["gfw"])["singbox"])   # 未下载


# =====================================================================
# 双写应用
# =====================================================================
class FakeClashMgr:
    def __init__(self, running=False):
        self.running = running
        self.reloaded = 0

    def reload(self):
        self.reloaded += 1
        return {}


class TestApply(RoutingTestBase):
    def _entries(self):
        return [{"type": "DOMAIN-SUFFIX", "value": "example.com", "policy": "REJECT",
                 "enabled": True, "remark": ""}]

    def test_01_dual_write_and_next_start(self):
        mgr = FakeClashMgr(running=False)
        r = rc.apply_unified(mgr, {"custom": self._entries(), "presets": _off()},
                             emit=lambda *_: None)
        self.assertEqual(r["applied"]["mihomo"], "next_start")
        self.assertEqual(r["applied"]["v2rayn"], "next_start")
        # unified.json 落盘
        self.assertTrue(os.path.isfile(rc.UNIFIED_FILE))
        # clash rules.json = 编译产物
        with open(cc.RULES_FILE, "r", encoding="utf-8") as f:
            lines = json.load(f)["rules"]
        self.assertEqual(lines, ["DOMAIN-SUFFIX,example.com,REJECT"])
        # advanced.json routing 为编译数组
        with open(v2c.ADVANCED_FILE, "r", encoding="utf-8") as f:
            adv = json.load(f)
        self.assertEqual(adv["routing"][0]["outboundTag"], "block")
        self.assertEqual(adv["routing"][0]["domain"], ["domain:example.com"])

    def test_02_hot_reload_when_running(self):
        mgr = FakeClashMgr(running=True)
        r = rc.apply_unified(mgr, {"custom": self._entries(), "presets": _off()},
                             emit=lambda *_: None)
        self.assertEqual(r["applied"]["mihomo"], "hot")
        self.assertEqual(mgr.reloaded, 1)

    def test_03_dns_preserved_and_list_replaced(self):
        os.makedirs(v2c.PROXY_DIR, exist_ok=True)
        with open(v2c.ADVANCED_FILE, "w", encoding="utf-8") as f:
            json.dump({"routing": [{"outboundTag": "direct", "domain": ["old.com"]}],
                       "dns": {"servers": ["1.1.1.1"]}}, f)
        rc.apply_unified(FakeClashMgr(), {"custom": self._entries(), "presets": _off()},
                         emit=lambda *_: None)
        with open(v2c.ADVANCED_FILE, "r", encoding="utf-8") as f:
            adv = json.load(f)
        self.assertEqual(adv["dns"], {"servers": ["1.1.1.1"]})
        self.assertEqual(adv["routing"][0]["domain"], ["domain:example.com"])

    def test_04_expert_dict_routing_not_overwritten(self):
        os.makedirs(v2c.PROXY_DIR, exist_ok=True)
        with open(v2c.ADVANCED_FILE, "w", encoding="utf-8") as f:
            json.dump({"routing": {"domainStrategy": "IPIfNonMatch", "rules": []},
                       "dns": {}}, f)
        r = rc.apply_unified(FakeClashMgr(), {"custom": self._entries(), "presets": _off()},
                             emit=lambda *_: None)
        self.assertEqual(r["applied"]["v2rayn"], "skipped_expert")
        with open(v2c.ADVANCED_FILE, "r", encoding="utf-8") as f:
            adv = json.load(f)
        self.assertEqual(adv["routing"]["domainStrategy"], "IPIfNonMatch")

    def test_05_empty_unified_restores_builtin(self):
        r = rc.apply_unified(FakeClashMgr(), {"custom": [], "presets": _off()},
                             emit=lambda *_: None)
        with open(v2c.ADVANCED_FILE, "r", encoding="utf-8") as f:
            adv = json.load(f)
        self.assertNotIn("routing", adv)
        self.assertEqual(r["applied"]["v2rayn"], "next_start")

    def test_06_clash_builtin_bypass_when_managed(self):
        node = {"type": "socks", "addr": "127.0.0.1", "port": 1080, "remark": "n0"}
        # 未接管：geo 缺失时保留内置静态降级行
        cfg, _ = cc.build_config([node], [], data_dir=os.path.join(self.clash_dir, "d1"))
        self.assertTrue(any(r.startswith("DOMAIN-SUFFIX,") for r in cfg["rules"]))
        # 接管后：内置降级行消失，规则来自统一规则（精选集 ads/cn/lan）+ MATCH 兜底
        rc.apply_unified(FakeClashMgr(), {"custom": [], "presets": {"ads": True}},
                         emit=lambda *_: None)
        cfg, _ = cc.build_config([node], [], data_dir=os.path.join(self.clash_dir, "d2"))
        self.assertFalse(any(r.startswith("DOMAIN-SUFFIX,") for r in cfg["rules"]))
        self.assertIn("GEOSITE,category-ads-all,REJECT", cfg["rules"])   # 来自精选集
        self.assertIn("MATCH,PROXY", cfg["rules"])


# =====================================================================
# 更新（备份源回退）
# =====================================================================
class TestUpdate(RoutingTestBase):
    def test_01_targets_follow_enabled_presets(self):
        items = rc.update_targets(dat_repo="metacubex")
        kinds = [i["kind"] for i in items]
        self.assertIn("clash-geoip", kinds)
        self.assertIn("clash-geosite", kinds)
        self.assertIn("geosite-category-ads-all.srs",
                      [i["label"] for i in items])
        # 默认只含 ads/cn/lan 的类别，未启用 telegram 不出现
        self.assertNotIn("geosite-telegram.srs", [i["label"] for i in items])

    def test_02_success_stamps_and_syncs(self):
        calls = []

        def fake_dl(kind, dest_dir=None, force=False, **kw):
            calls.append(kind)
            with open(os.path.join(dest_dir, kind.split("-", 1)[1] + ".dat"), "wb") as f:
                f.write(b"x" * 2048)
            return {"kind": kind, "cached": False}

        with mock.patch.object(bindl, "download_binary", side_effect=fake_dl), \
             mock.patch.object(bindl, "download_ruleset",
                               return_value={"cached": False}), \
             mock.patch.object(bindl, "download_srs",
                               return_value={"cached": False}):
            r = rc.update_all(force=True, dat_repo="metacubex")
        self.assertTrue(r["ok"])
        self.assertEqual(set(calls), {"clash-geoip", "clash-geosite"})
        self.assertGreater(rc.load_unified()["updated_ts"], 0)
        # dat 同步到 mihomo 目录
        self.assertTrue(os.path.isfile(os.path.join(cc.MIHOMO_DIR, "geoip.dat")))

    def test_03_dat_falls_back_to_backup_repo(self):
        calls = []

        def fake_dl(kind, dest_dir=None, force=False, **kw):
            calls.append(kind)
            if kind == "clash-geoip":
                raise bindl.BindlError("主源不可用")
            return {"kind": kind, "cached": False}

        with mock.patch.object(bindl, "download_binary", side_effect=fake_dl), \
             mock.patch.object(bindl, "download_ruleset",
                               return_value={"cached": False}), \
             mock.patch.object(bindl, "download_srs",
                               return_value={"cached": False}), \
             mock.patch.object(rc, "_sync_dat_to_mihomo", lambda: None):
            r = rc.update_all(force=True, dat_repo="metacubex")
        self.assertTrue(r["ok"])
        self.assertIn("clash-geoip", calls)
        self.assertIn("geoip", calls)          # 备份仓库被自动尝试

    def test_04_all_fail_reports_errs(self):
        with mock.patch.object(bindl, "download_binary",
                               side_effect=bindl.BindlError("网络不可用")), \
             mock.patch.object(bindl, "download_ruleset",
                               side_effect=bindl.BindlError("网络不可用")), \
             mock.patch.object(bindl, "download_srs",
                               side_effect=bindl.BindlError("网络不可用")):
            r = rc.update_all(force=True, dat_repo="metacubex")
        self.assertFalse(r["ok"])
        self.assertTrue(r["errs"])
        self.assertEqual(rc.load_unified()["updated_ts"], 0)

    def test_05_progress_events_emitted(self):
        events = []
        with mock.patch.object(bindl, "download_binary",
                               return_value={"cached": True}), \
             mock.patch.object(bindl, "download_ruleset",
                               return_value={"cached": True}), \
             mock.patch.object(bindl, "download_srs",
                               return_value={"cached": True}):
            rc.update_all(progress_cb=events.append, force=False, dat_repo="metacubex")
        self.assertTrue(any(e.get("status") == "running" for e in events))
        self.assertTrue(any(e.get("status") == "done" for e in events))


# =====================================================================
# Bridge API（fake 子类，不发网络）
# =====================================================================
class TestApi(RoutingTestBase):
    def _api(self, running=False):
        from app.bridge.routing_api import RoutingApi

        class B(RoutingApi):
            def __init__(self):
                self.emits = []
                self._routing_busy = False
                self._routing_stop = None
                self._proxy = None
                self._clash = FakeClashMgr(running=running)
                self._fcfg = FakeCfg()

            def emit(self, ev, m):
                self.emits.append((ev, m))

            def _cfg(self):
                return self._fcfg

            def _clash_publish(self):
                pass

            def _proxy_publish(self):
                pass

        class FakeCfg:
            def __init__(self):
                self.data = {}

            def get(self, k, d=None):
                return self.data.get(k, d)

            def set(self, k, v):
                self.data[k] = v

        return B()

    def test_01_get_state_shape(self):
        api = self._api()
        r = api.routing_get_state()
        self.assertTrue(r["ok"])
        d = r["data"]
        self.assertEqual([p["key"] for p in d["presets"]][:3],
                         ["ads", "cn", "lan"])
        self.assertIn("engines", d)
        self.assertIn("update", d)
        self.assertEqual(d["managed"]["v2rayn"], "default")

    def test_02_save_custom_applies(self):
        api = self._api()
        r = api.routing_save_custom([{"type": "DOMAIN", "value": "a.com",
                                      "policy": "DIRECT"}])
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["applied"]["mihomo"], "next_start")
        with open(cc.RULES_FILE, "r", encoding="utf-8") as f:
            lines = json.load(f)["rules"]
        # 自定义条目在前，默认启用的精选集（ads/cn/lan）随后
        self.assertEqual(lines, ["DOMAIN,a.com,DIRECT",
                                 "GEOSITE,category-ads-all,REJECT", "GEOSITE,cn,DIRECT",
                                 "GEOIP,cn,DIRECT", "GEOIP,private,DIRECT,no-resolve"])

    def test_03_save_custom_invalid_entry(self):
        api = self._api()
        r = api.routing_save_custom([{"type": "BAD", "value": "x"}])
        self.assertFalse(r["ok"])
        self.assertIn("规则类型", r["err"])

    def test_04_set_preset_toggle(self):
        api = self._api()
        r = api.routing_set_preset("gfw", True)
        self.assertTrue(r["ok"])
        st = api.routing_get_state()
        gfw = [p for p in st["data"]["presets"] if p["key"] == "gfw"][0]
        self.assertTrue(gfw["on"])

    def test_05_set_options_clamps(self):
        api = self._api()
        r = api.routing_set_options(auto=True, hours=1, dat_repo="bad")
        self.assertFalse(r["ok"])
        r = api.routing_set_options(auto=True, hours=1)
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["hours"], 6)      # 钳位下限
        r = api.routing_set_options(None, None, "loyalsoldier")
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["dat_repo"], "loyalsoldier")

    def test_06_preview(self):
        api = self._api()
        r = api.routing_preview()
        self.assertTrue(r["ok"])
        self.assertIn("mihomo", r["data"])
        self.assertIn("singbox", r["data"])


if __name__ == "__main__":
    unittest.main()
