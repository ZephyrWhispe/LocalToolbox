"""Clash 核心托管（mihomo / Clash.Meta）——与 xray / sing-box 完全独立的引擎。

设计要点（对齐 Clash Verge 的常用形态，但不与「V2rayN」页/引擎混淆）：
- 独立数据目录 DATA_HOME/clash（配置、分组、节点库、缓存、日志都在这里）；
- 独立节点库 clash/nodes.json（可自行导入订阅，也可从「V2rayN」复制一份）；
- 配置为 Clash 的 YAML（proxies / proxy-groups / rules），支持
  select / url-test / fallback / load-balance 四种策略组；
- 通过 mihomo 的 Clash API（external-controller）做连接监控、策略组运行时切换、
  内核级节点测速（/proxies/{name}/delay）；
- 端口默认 mixed 7891 / API 9091，避开 Clash Verge（7897/9090）与本应用其它引擎。

节点库与订阅解析复用 v2ray_core 的 NodeStore / import_sub_url（不同文件路径 =
数据独立），避免重复实现解析逻辑。
"""

import ctypes
import io
import json
import os
import random
import re
import socket
import string
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes

import yaml

from . import logger as applog
from .config import DATA_HOME
from .v2ray_core import (NodeStore, SMART_CIDRS, SMART_DIRECT_DOMAINS,
                         import_sub_url, parse_sub_text)

log = applog.get_logger("clash")

CLASH_DIR = os.path.join(DATA_HOME, "clash")
CONFIG_FILE = os.path.join(CLASH_DIR, "config.yaml")
NODES_FILE = os.path.join(CLASH_DIR, "nodes.json")
GROUPS_FILE = os.path.join(CLASH_DIR, "groups.json")
RULES_FILE = os.path.join(CLASH_DIR, "rules.json")
LOG_LEVELS = ("debug", "info", "warning", "error")
MIHOMO_DIR = os.path.join(DATA_HOME, "mihomo", "bin")
LOG_FILE = os.path.join(CLASH_DIR, "core.log")

DEFAULT_MIXED_PORT = 7891     # HTTP+SOCKS 混合端口（避开 Clash Verge 的 7897）
DEFAULT_API_PORT = 9091       # Clash API（避开 9090）
DEFAULT_MODE = "rule"         # rule | global | direct

GROUP_TYPES = ("select", "url-test", "fallback", "load-balance")

# 窗口化进程里启动控制台程序会弹黑窗（关窗即杀核心），必须禁用
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_SYS_PROXY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


# --------------------------------------------------------------------------
# 二进制定位
# --------------------------------------------------------------------------
def bin_candidates():
    return (os.path.join(MIHOMO_DIR, "mihomo.exe"),
            os.path.join(CLASH_DIR, "bin", "mihomo.exe"))


def detect_bin(configured=""):
    """探测 mihomo.exe：配置路径 → 下载目录 → PATH。"""
    for c in (str(configured or "").strip(),) + bin_candidates():
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    from shutil import which
    p = which("mihomo") or which("mihomo.exe")
    if p:
        return p
    raise ValueError("未找到 mihomo 内核。请点「下载内核」，或手动放置到 %s"
                     % bin_candidates()[0])


def geo_available():
    """Clash 规则库（geoip.dat / geosite.dat）是否就绪。"""
    return bool(_geo_dir())


def geo_ready_in(data_dir):
    """指定数据目录内 Clash 规则库是否齐备（mihomo 只在该目录查找）。"""
    if not data_dir:
        return False
    return all(os.path.isfile(os.path.join(data_dir, n))
               for n in ("geoip.dat", "geosite.dat"))


def _geo_dir():
    for d in (MIHOMO_DIR, os.path.join(CLASH_DIR, "bin")):
        if (os.path.isfile(os.path.join(d, "geoip.dat"))
                and os.path.isfile(os.path.join(d, "geosite.dat"))):
            return d
    return ""


# --------------------------------------------------------------------------
# 节点 → Clash proxies
# --------------------------------------------------------------------------
def _tls_fields(node):
    out = {}
    if str(node.get("tls") or "").lower() in ("tls", "1", "reality"):
        out["tls"] = True
        sni = node.get("sni") or node.get("host") or node.get("addr")
        if sni:
            out["servername"] = sni
            out["sni"] = sni
        if node.get("allow_insecure"):
            out["skip-cert-verify"] = True
        if node.get("alpn"):
            alpn = [a for a in re.split(r"[, ]", str(node["alpn"])) if a]
            if alpn:
                out["alpn"] = alpn
        fp = node.get("fp")
        if fp:
            out["client-fingerprint"] = fp
    if node.get("pbk"):      # REALITY
        out["reality-opts"] = {"public-key": node["pbk"]}
        if node.get("spx"):
            out["reality-opts"]["short-id"] = node["spx"]
    return out


def _transport_fields(node):
    """传输层（ws / grpc / h2）字段。"""
    net = str(node.get("network") or "tcp").lower()
    out = {}
    if net in ("ws", "websocket"):
        out["network"] = "ws"
        ws = {}
        if node.get("path"):
            ws["path"] = node["path"]
        if node.get("host"):
            ws["headers"] = {"Host": node["host"]}
        if ws:
            out["ws-opts"] = ws
    elif net == "grpc":
        out["network"] = "grpc"
        if node.get("serviceName"):
            out["grpc-opts"] = {"grpc-service-name": node["serviceName"]}
    elif net in ("h2", "http"):
        out["network"] = "h2"
        h2 = {}
        if node.get("host"):
            h2["host"] = [h for h in str(node["host"]).split(",") if h]
        if node.get("path"):
            h2["path"] = node["path"]
        if h2:
            out["h2-opts"] = h2
    return out


def node_to_clash_proxy(node, name):
    """把节点库条目转为 Clash proxy 字典；不支持的协议抛 ValueError。"""
    proto = str(node.get("type") or "")
    addr = node.get("addr")
    try:
        port = int(node.get("port"))
    except (TypeError, ValueError):
        raise ValueError("节点缺少端口")
    if not addr:
        raise ValueError("节点缺少服务器地址")
    base = {"name": name, "server": str(addr), "port": port}
    sec = str(node.get("tls") or "").lower()

    if proto == "vless":
        ob = {**base, "type": "vless", "uuid": node.get("id") or "",
              "udp": True}
        if node.get("flow"):
            ob["flow"] = node["flow"]
        # 无 TLS 的 vless 在 Clash 里需显式关闭
        if sec in ("tls", "1", "reality"):
            ob.update(_tls_fields(node))
        ob.update(_transport_fields(node))
        return ob
    if proto == "vmess":
        ob = {**base, "type": "vmess", "uuid": node.get("id") or "",
              "alterId": int(node.get("aid") or 0),
              "cipher": node.get("method") or "auto", "udp": True}
        ob.update(_tls_fields(node))
        ob.update(_transport_fields(node))
        return ob
    if proto == "trojan":
        ob = {**base, "type": "trojan", "password": node.get("id") or "",
              "udp": True}
        sni = node.get("sni") or node.get("host")
        if sni:
            ob["sni"] = sni
        if node.get("allow_insecure"):
            ob["skip-cert-verify"] = True
        if node.get("alpn"):
            alpn = [a for a in re.split(r"[, ]", str(node["alpn"])) if a]
            if alpn:
                ob["alpn"] = alpn
        ob.update(_transport_fields(node))
        return ob
    if proto == "ss":
        return {**base, "type": "ss", "cipher": node.get("method") or "aes-256-gcm",
                "password": node.get("id") or "", "udp": True}
    if proto in ("socks", "socks5", "http", "https"):
        t = "socks5" if proto.startswith("socks") else "http"
        ob = {**base, "type": t}
        if node.get("user"):
            ob["username"] = node["user"]
        if node.get("pass"):
            ob["password"] = node["pass"]
        if t == "http" and sec in ("tls", "1"):
            ob["tls"] = True
        return ob
    if proto == "anytls":
        ob = {**base, "type": "anytls", "password": node.get("id") or "",
              "udp": True}
        sni = node.get("sni") or node.get("host") or addr
        if sni:
            ob["sni"] = sni
        if node.get("allow_insecure"):
            ob["skip-cert-verify"] = True
        if node.get("fp"):
            ob["client-fingerprint"] = node["fp"]
        return ob
    if proto in ("hy2", "hysteria2"):
        ob = {**base, "type": "hysteria2", "password": node.get("id") or "",
              "sni": node.get("sni") or node.get("host") or addr}
        if node.get("allow_insecure"):
            ob["skip-cert-verify"] = True
        if node.get("obfs"):
            ob["obfs"] = node["obfs"]
        if node.get("obfs_password"):
            ob["obfs-password"] = node["obfs_password"]
        for k_src, k_dst in (("up_mbps", "up"), ("down_mbps", "down")):
            if node.get(k_src):
                ob[k_dst] = str(node[k_src]) + " Mbps"
        return ob
    if proto == "tuic":
        ob = {**base, "type": "tuic", "uuid": node.get("id") or "",
              "password": node.get("pass") or "",
              "sni": node.get("sni") or node.get("host") or addr,
              "udp-relay-mode": node.get("udp_relay_mode") or "native",
              "congestion-controller": node.get("congestion_control") or "bbr"}
        if node.get("allow_insecure"):
            ob["skip-cert-verify"] = True
        if node.get("alpn"):
            alpn = [a for a in re.split(r"[, ]", str(node["alpn"])) if a]
            if alpn:
                ob["alpn"] = alpn
        return ob
    if proto == "wireguard":
        peers = node.get("wg_peers") or []
        peer_pk, allowed = "", "0.0.0.0/0, ::/0"
        server, port2 = addr, port
        if peers:
            ep = peers[0].get("endpoint") or ""
            if ep:
                host, _, p2 = ep.rpartition(":")
                if host:
                    server = host.strip("[]")
                    try:
                        port2 = int(p2)
                    except ValueError:
                        pass
            peer_pk = peers[0].get("public_key", "")
            allowed = peers[0].get("allowed_ips") or allowed
        return {
            "name": name, "type": "wireguard", "server": server, "port": port2,
            "private-key": node.get("wg_private") or node.get("id") or "",
            "public-key": peer_pk,
            "ip": ",".join(node.get("wg_address") or []),
            "allowed-ips": [x.strip() for x in str(allowed).split(",") if x.strip()],
            "mtu": int(node.get("wg_mtu") or 1420), "udp": True,
        }
    raise ValueError("Clash 内核不支持节点类型：%s" % proto)


# --------------------------------------------------------------------------
# 策略组
# --------------------------------------------------------------------------
def load_custom_rules():
    """自定义规则（Clash 规则语法，如 DOMAIN-SUFFIX,example.com,DIRECT）。

    生成配置时**前置**在自动规则之前，因此可覆盖内置分流。
    """
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    rules = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(rules, list):
        return []
    out = []
    for r in rules:
        r = str(r or "").strip()
        if r and not r.startswith("#"):
            out.append(r[:300])
    return out


def save_custom_rules(rules):
    """保存自定义规则（原子写）。"""
    if isinstance(rules, str):
        rules = [rules]
    os.makedirs(CLASH_DIR, exist_ok=True)
    clean = [str(r or "").strip()[:300] for r in (rules or [])
             if str(r or "").strip() and not str(r).strip().startswith("#")]
    tmp = RULES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"rules": clean}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RULES_FILE)
    return clean


def load_groups():
    """读取 Clash 策略组定义（独立于「V2rayN」页的分组文件）。

    结构：[{"name": "自动选择", "type": "select|url-test|fallback|load-balance",
           "filter": "香港,日本"（关键字，空或 * = 全部）,
           "interval": 300（url-test 间隔秒）, "default": true}]
    """
    try:
        with open(GROUPS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    groups = data.get("groups") if isinstance(data, dict) else data
    out = []
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        name = str(g.get("name") or "").strip()
        if not name:
            continue
        t = str(g.get("type") or "select")
        item = {"name": name[:40],
                "type": t if t in GROUP_TYPES else "select",
                "filter": str(g.get("filter") or "").strip()[:200]}
        try:
            item["interval"] = max(30, min(3600, int(g.get("interval") or 300)))
        except (TypeError, ValueError):
            item["interval"] = 300
        if g.get("default"):
            item["default"] = True
        out.append(item)
    return out


def save_groups(groups):
    """保存策略组定义（原子写）。"""
    os.makedirs(CLASH_DIR, exist_ok=True)
    clean = []
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        name = str(g.get("name") or "").strip()
        if not name:
            continue
        t = str(g.get("type") or "select")
        item = {"name": name[:40], "type": t if t in GROUP_TYPES else "select",
                "filter": str(g.get("filter") or "").strip()[:200]}
        try:
            item["interval"] = max(30, min(3600, int(g.get("interval") or 300)))
        except (TypeError, ValueError):
            item["interval"] = 300
        if g.get("default"):
            item["default"] = True
        clean.append(item)
    tmp = GROUPS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"groups": clean}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, GROUPS_FILE)
    return clean


def group_members(group, nodes):
    """按关键字解析组成员（返回索引列表；空/`*` = 全部）。"""
    kw = str((group or {}).get("filter") or "").strip()
    if not kw or kw == "*":
        return list(range(len(nodes or [])))
    keys = [k.strip().lower() for k in kw.split(",") if k.strip()]
    out = []
    for i, n in enumerate(nodes or []):
        hay = "%s %s %s" % (n.get("remark") or "", n.get("addr") or "",
                            n.get("type") or "")
        if any(k in hay.lower() for k in keys):
            out.append(i)
    return out


# --------------------------------------------------------------------------
# 配置生成
# --------------------------------------------------------------------------
def _proxy_name(index, node):
    """Clash proxy 名（组内展示用）：序号 + 备注，保证唯一。"""
    remark = str(node.get("remark") or node.get("addr") or ("节点%d" % index))
    return "#%02d %s" % (index + 1, remark[:48])


def build_config(nodes, groups, mixed_port=DEFAULT_MIXED_PORT,
                 api_port=DEFAULT_API_PORT, secret="", mode=DEFAULT_MODE,
                 tun=False, test_url="http://www.gstatic.com/generate_204",
                 data_dir="", custom_rules=None, log_level="warning"):
    """生成 mihomo 配置字典（YAML 来源）。

    nodes 为空或全部转换失败时抛 ValueError（避免生成空配置让核心空转）。
    """
    if mode not in ("rule", "global", "direct"):
        mode = DEFAULT_MODE
    proxies, name_of, skipped = [], {}, []
    for i, n in enumerate(nodes or []):
        try:
            p = node_to_clash_proxy(n, _proxy_name(i, n))
        except ValueError as e:
            skipped.append("节点[%d] %s：%s" % (i, n.get("remark") or n.get("addr"), e))
            continue
        proxies.append(p)
        name_of[i] = p["name"]
    if not proxies:
        raise ValueError("没有可用节点（%s）" % ("；".join(skipped[:3]) or "节点库为空"))

    # 策略组
    proxy_groups, group_names, primary = [], [], None
    for g in groups or []:
        members = [name_of[i] for i in group_members(g, nodes) if i in name_of]
        if not members:
            continue
        item = {"name": g["name"], "type": g.get("type") or "select",
                "proxies": list(group_names) + members}
        if item["type"] == "url-test":
            item["url"] = test_url
            item["interval"] = int(g.get("interval") or 300)
            item["tolerance"] = 50
        proxy_groups.append(item)
        group_names.append(g["name"])
        if g.get("default") and primary is None:
            primary = g["name"]
    if group_names and primary is None:
        primary = group_names[0]
    if not group_names:      # 无分组：自动生成一个全局自动测速组（Clash 风格）
        proxy_groups.append({"name": "PROXY", "type": "url-test",
                             "url": test_url, "interval": 300, "tolerance": 50,
                             "proxies": [p["name"] for p in proxies]})
        group_names.append("PROXY")
        primary = "PROXY"

    # 规则：自定义规则前置（可覆盖内置分流），其后是内置分流与兜底。
    # 统一分流规则中心（rules/unified.json）接管后，内置 GEOSITE 行由统一规则的
    # 精选集条目代替（避免重复命中），仅保留 GEOIP,private 与 MATCH 兜底。
    managed = False
    try:
        from .routing_core import is_managing
        managed = is_managing()
    except Exception:
        managed = False
    rules = list(custom_rules if custom_rules is not None else load_custom_rules())
    if geo_ready_in(data_dir) and not managed:
        rules += ["GEOSITE,category-ads-all,REJECT", "GEOSITE,cn,DIRECT",
                  "GEOIP,CN,DIRECT"]
    elif not managed:
        rules += ["IP-CIDR,%s,DIRECT,no-resolve" % c for c in SMART_CIDRS[:60]]
        rules += ["DOMAIN-SUFFIX,%s,DIRECT" % d.lstrip(".")
                  for d in SMART_DIRECT_DOMAINS[:80]]
    rules += ["GEOIP,private,DIRECT,no-resolve", "MATCH,%s" % primary]

    cfg = {
        "mixed-port": int(mixed_port),
        "allow-lan": False,
        "bind-address": "127.0.0.1",
        "mode": mode,
        "log-level": log_level if log_level in LOG_LEVELS else "warning",
        "ipv6": False,
        "unified-delay": True,
        "tcp-concurrent": True,
        "find-process-mode": "strict",
        "external-controller": "127.0.0.1:%d" % int(api_port),
        "secret": str(secret or ""),
        "profile": {"store-selected": True, "store-fake-ip": True},
        "dns": {
            "enable": True,
            "listen": "127.0.0.1:0",     # 仅内部使用，不开监听端口
            "ipv6": False,
            "enhanced-mode": "fake-ip",
            "fake-ip-range": "198.18.0.1/16",
            "fake-ip-filter": ["*.lan", "*.local", "+.msftconnecttest.com",
                               "+.msftncsi.com", "localhost.ptlogin2.qq.com"],
            "nameserver": ["223.5.5.5", "119.29.29.29"],
            "fallback": ["https://1.1.1.1/dns-query", "https://dns.google/dns-query"],
            # 不用 geoip 过滤：避免依赖 country.mmdb（缺失时核心会联网下载拖慢启动）
            "fallback-filter": {"geoip": False},
        },
        "proxies": proxies,
        "proxy-groups": proxy_groups,
        "rules": rules,
    }
    if tun:
        cfg["tun"] = {"enable": True, "stack": "mixed", "auto-route": True,
                      "auto-detect-interface": True,
                      "dns-hijack": ["any:53"]}
    return cfg, {"primary": primary, "groups": group_names,
                 "skipped": skipped, "count": len(proxies)}


def dump_yaml(cfg, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------
# 核心托管
# --------------------------------------------------------------------------
def _free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class ClashCoreManager:
    """mihomo 进程托管 + Clash API 客户端。"""

    def __init__(self, log_callback=None, data_dir=None):
        self.data_dir = data_dir or CLASH_DIR
        self.store = NodeStore(os.path.join(self.data_dir, "nodes.json"))
        self.bin_path = ""
        self.proc = None
        self.running = False
        self.healthy = False
        self.mixed_port = DEFAULT_MIXED_PORT
        self.api_port = DEFAULT_API_PORT
        self.secret = ""
        self.mode = DEFAULT_MODE
        self.log_level = "warning"
        self.tun = False
        self.sys_proxy = False
        self._old_proxy = None
        self._ver_cache = {}
        self._ver_cache_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._log_fh = None
        self.log_callback = log_callback or (lambda m: None)

    # -- 日志 -------------------------------------------------------------
    def _log(self, msg):
        try:
            self.log_callback(str(msg))
        except Exception:
            pass

    def _log_path(self):
        return os.path.join(self.data_dir, "core.log")

    def log_tail(self, lines=30):
        try:
            with open(self._log_path(), "r", encoding="utf-8", errors="replace") as f:
                return [x.rstrip() for x in f.readlines()[-int(lines):] if x.strip()]
        except OSError:
            return []

    # -- 规则库 -----------------------------------------------------------
    GEO_FILES = ("geoip.dat", "geosite.dat")       # 必需
    GEO_OPTIONAL = ("country.mmdb",)               # 可选（有则复制）

    def ensure_geo(self):
        """把规则库就位到数据目录（mihomo 只在 -d 目录查找，缺失会联网下载）。

        下载目录（mihomo/bin）里有就从那里复制，避免重复下载 20MB+。
        """
        os.makedirs(self.data_dir, exist_ok=True)
        copied = []
        for name in self.GEO_FILES + self.GEO_OPTIONAL:
            dst = os.path.join(self.data_dir, name)
            if os.path.isfile(dst) and os.path.getsize(dst) > 1024:
                continue
            for src_dir in (MIHOMO_DIR, os.path.join(self.data_dir, "bin")):
                src = os.path.join(src_dir, name)
                if os.path.isfile(src) and os.path.getsize(src) > 1024:
                    try:
                        import shutil
                        shutil.copyfile(src, dst)
                        copied.append(name)
                    except OSError:
                        pass
                    break
        if copied:
            self._log("规则库已就位：" + "、".join(copied))
        return all(os.path.isfile(os.path.join(self.data_dir, n))
                   for n in self.GEO_FILES)

    def geo_ready(self):
        return all(os.path.isfile(os.path.join(self.data_dir, n))
                   for n in self.GEO_FILES)

    # -- 配置 -------------------------------------------------------------
    def _write_config(self):
        self.ensure_geo()
        nodes = self.store.nodes()
        groups = load_groups()
        cfg, info = build_config(
            nodes, groups, self.mixed_port, self.api_port, self.secret,
            self.mode, self.tun, data_dir=self.data_dir,
            log_level=self.log_level)
        dump_yaml(cfg, os.path.join(self.data_dir, "config.yaml"))
        if info.get("skipped"):
            self._log("已跳过不支持的节点：" + "；".join(info["skipped"][:3]))
        return info

    def config_path(self):
        return os.path.join(self.data_dir, "config.yaml")

    def reload(self):
        """热重载配置（重写 config.yaml 后 PUT /configs）。"""
        if not self.running:
            raise ValueError("Clash 未运行")
        self._write_config()
        return self.api("PUT", "/configs?force=true",
                        {"path": self.config_path()}, timeout=10)

    # -- 启停 -------------------------------------------------------------
    def start(self, mixed_port=None, api_port=None, mode=None, tun=None,
              sys_proxy=True):
        with self._lock:
            if self.running:
                return
            if mixed_port:
                self.mixed_port = int(mixed_port)
            if api_port:
                self.api_port = int(api_port)
            if mode:
                self.mode = str(mode)
            if tun is not None:
                self.tun = bool(tun)
            if not self.bin_path:
                self.bin_path = detect_bin("")
            if self.tun:
                if not _is_admin():
                    raise ValueError("TUN 模式需要管理员权限：请以管理员身份运行后重试")
                wintun = os.path.join(os.path.dirname(self.bin_path), "wintun.dll")
                if not os.path.isfile(wintun):
                    raise ValueError("TUN 需要 wintun.dll（缺失）。请先下载 wintun 后重试")
            if not self.api_port:
                self.api_port = DEFAULT_API_PORT
            self.secret = "".join(random.choices(string.ascii_letters + string.digits, k=16))
            info = self._write_config()
            cmd = [self.bin_path, "-d", self.data_dir,
                   "-f", os.path.join(self.data_dir, "config.yaml")]
            workdir = _geo_dir() or os.path.dirname(self.bin_path)
            self._log("Clash 启动：%s（混合端口 %d / API %d）"
                      % (os.path.basename(self.bin_path), self.mixed_port, self.api_port))
            try:
                os.makedirs(self.data_dir, exist_ok=True)
                self._log_fh = open(self._log_path(), "wb")
            except OSError:
                self._log_fh = None
            try:
                self.proc = subprocess.Popen(
                    cmd, stdout=self._log_fh or subprocess.DEVNULL,
                    stderr=subprocess.STDOUT, cwd=workdir, creationflags=_NOWIN)
            except Exception as e:
                self.running = False
                raise ValueError("Clash 内核启动失败：%s" % e)
            self.running = True
            self._stop_event.clear()
        # 就绪等待（端口 + 规则集）
        self._activate(info)
        if sys_proxy:
            self.set_sys_proxy(True)

    def _activate(self, info):
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                tail = " | ".join(self.log_tail(5))
                self.stop()
                raise ValueError("Clash 内核启动后立即退出：%s" % (tail or "无输出"))
            if self._port_open(self.mixed_port) and self._port_open(self.api_port):
                self.healthy = True
                self._log("Clash 已就绪：混合端口 %d，API %d，%d 个节点，%d 个策略组"
                          % (self.mixed_port, self.api_port, info["count"],
                             len(info["groups"])))
                return
            time.sleep(0.4)
        self.stop()
        raise ValueError("Clash 内核 20 秒内未就绪（端口 %d/%d 未监听）"
                         % (self.mixed_port, self.api_port))

    @staticmethod
    def _port_open(port, timeout=0.4):
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
                return True
        except OSError:
            return False

    def stop(self):
        with self._lock:
            proc, self.proc = self.proc, None
            self.running = False
            self.healthy = False
        if self.sys_proxy:
            try:
                self.set_sys_proxy(False)
            except Exception:
                pass
        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            except OSError:
                pass
            self._log("Clash 已停止。")
        fh, self._log_fh = self._log_fh, None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    def restart(self, **kw):
        self.stop()
        self.start(**kw)

    # -- 系统代理 ---------------------------------------------------------
    def set_sys_proxy(self, on):
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _SYS_PROXY_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as k:
            def _get(name_, def_):
                try:
                    return winreg.QueryValueEx(k, name_)[0]
                except OSError:
                    return def_

            if on:
                if self._old_proxy is None:
                    self._old_proxy = {
                        "enable": _get("ProxyEnable", 0),
                        "server": _get("ProxyServer", ""),
                        "override": _get("ProxyOverride", ""),
                        "pac": _get("AutoConfigURL", ""),
                    }
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ,
                                  "127.0.0.1:%d" % self.mixed_port)
                try:
                    winreg.SetValueEx(k, "AutoConfigURL", 0, winreg.REG_SZ, "")
                except OSError:
                    pass
            elif self._old_proxy:
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD,
                                  self._old_proxy["enable"])
                winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ,
                                  self._old_proxy["server"])
                winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ,
                                  self._old_proxy["override"])
                try:
                    winreg.SetValueEx(k, "AutoConfigURL", 0, winreg.REG_SZ,
                                      self._old_proxy.get("pac", ""))
                except OSError:
                    pass
                self._old_proxy = None
        self.sys_proxy = bool(on)

    # -- Clash API --------------------------------------------------------
    def api(self, method, path, body=None, timeout=6, retries=2):
        if not self.running:
            raise ValueError("Clash 未运行")
        url = "http://127.0.0.1:%d%s" % (self.api_port, path)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, method=str(method), data=data, headers={
            "Authorization": "Bearer %s" % self.secret,
            "Content-Type": "application/json"})
        last = None
        for _ in range(max(1, retries + 1)):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                return json.loads(raw) if raw else {}
            except urllib.error.HTTPError:
                raise
            except Exception as e:
                last = e
                time.sleep(0.35)
        raise ValueError("Clash API 请求失败：%s" % last)

    def proxies(self):
        """策略组 + 节点状态（含当前选中）。"""
        data = (self.api("GET", "/proxies") or {}).get("proxies") or {}
        nodes = self.store.nodes()
        name_of = {}
        for i, n in enumerate(nodes):
            name_of[_proxy_name(i, n)] = {"index": i,
                                          "remark": n.get("remark") or n.get("addr")}
        groups = []
        for g in load_groups() or []:
            info = data.get(g["name"]) or {}
            members = []
            for m in (info.get("all") or []):
                meta = name_of.get(m) or {}
                members.append({"name": m, "index": meta.get("index"),
                                "remark": meta.get("remark") or m,
                                "latency": _latency_of(data.get(m))})
            groups.append({"name": g["name"], "type": info.get("type") or g["type"],
                           "now": info.get("now"),
                           "filter": g.get("filter", ""), "members": members})
        # 无自定义分组时，核心里仍有自动生成的 PROXY 组
        if not groups and "PROXY" in data:
            info = data["PROXY"]
            groups.append({"name": "PROXY", "type": info.get("type") or "URLTest",
                           "now": info.get("now"), "filter": "",
                           "members": [{"name": m, "index": (name_of.get(m) or {}).get("index"),
                                        "remark": (name_of.get(m) or {}).get("remark") or m,
                                        "latency": _latency_of(data.get(m))}
                                       for m in (info.get("all") or [])]})
        return {"groups": groups, "custom": bool(load_groups()),
                "node_count": len(nodes)}

    def select(self, group, name):
        self.api("PUT", "/proxies/" + urllib.parse.quote(str(group), safe=""),
                 {"name": str(name)})
        return True

    def rules(self):
        """当前生效规则列表（内核 API）+ 自定义规则。"""
        out = []
        try:
            data = self.api("GET", "/rules") or {}
            for r in (data.get("rules") or []):
                out.append({"type": r.get("type") or "", "payload": r.get("payload") or "",
                            "proxy": r.get("proxy") or "", "custom": False})
        except Exception:
            pass
        # 内核下发的类型是规范化写法（DomainSuffix/DOMAIN-SUFFIX 都可能出现），
        # 不一致则永远标不上「自定义」→ 去掉连字符后大写比较
        def _norm(*parts):
            return ",".join(re.sub(r"[-_\s]", "", str(p or "")).upper() for p in parts)

        custom = set()
        for line in load_custom_rules():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 3:
                custom.add(_norm(parts[0], parts[1], parts[2]))
        for i, r in enumerate(out):
            if _norm(r["type"], r["payload"], r["proxy"]) in custom:
                out[i]["custom"] = True
        return {"rules": out, "custom": load_custom_rules(),
                "geo_ok": geo_ready_in(self.data_dir)}

    def set_log_level(self, level):
        level = str(level or "").lower()
        if level not in LOG_LEVELS:
            raise ValueError("日志级别无效（debug/info/warning/error）")
        self.log_level = level
        if self.running:
            self.api("PATCH", "/configs", {"log-level": level})
        return level

    def start_log_stream(self, level, on_line, on_error=None):
        """订阅内核日志流（/logs，分块 JSON）：后台线程回调每一行。

        返回停止函数；内核未运行时抛 ValueError。
        """
        if not self.running:
            raise ValueError("Clash 未运行")
        level = str(level or "info").lower()
        if level not in LOG_LEVELS:
            level = "info"
        # 内核自身的 log-level 会压制推送：订阅什么级别就把内核调到什么级别，
        # 否则 warning 配置下订阅 info 会一条都收不到
        self.set_log_level(level)
        stop = threading.Event()
        holder = {}

        def _run():
            url = ("http://127.0.0.1:%d/logs?level=%s"
                   % (self.api_port, level))
            req = urllib.request.Request(url, headers={
                "Authorization": "Bearer %s" % self.secret})
            try:
                resp = urllib.request.urlopen(req, timeout=None)
                holder["resp"] = resp
                # 逐行读：read(n) 在 socket 上会阻塞到攒满 n 字节，
                # 日志量小时会一条都收不到
                while not stop.is_set():
                    raw = resp.readline()
                    if not raw:
                        break
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        item = json.loads(raw.decode("utf-8", "replace"))
                        on_line(item.get("type") or "info",
                                item.get("payload") or "")
                    except ValueError:
                        continue
            except Exception as e:
                if not stop.is_set() and on_error:
                    try:
                        on_error(str(e))
                    except Exception:
                        pass
            finally:
                try:
                    if holder.get("resp"):
                        holder["resp"].close()
                except Exception:
                    pass

        t = threading.Thread(target=_run, daemon=True, name="clash-logs")
        t.start()

        def _stop():
            # 关闭底层 socket 让阻塞中的 readline 立即返回（不能直接 resp.close()：
            # close 要等读线程释放缓冲区锁，而读线程正阻塞在 socket 上 → 死锁）
            stop.set()
            resp = holder.get("resp")
            if resp is None:
                return
            for getter in (lambda r: r.fp.raw._sock, lambda r: r.fp.raw.fp._sock):
                try:
                    getter(resp).shutdown(socket.SHUT_RDWR)
                    return
                except (AttributeError, OSError):
                    continue
        return _stop

    def connections(self, limit=200):
        data = self.api("GET", "/connections")
        out = []
        for c in (data.get("connections") or [])[:int(limit)]:
            meta = c.get("metadata") or {}
            out.append({
                "id": c.get("id") or "",
                "host": meta.get("host") or meta.get("destinationIP") or "",
                "dest": "%s:%s" % (meta.get("destinationIP") or "",
                                   meta.get("destinationPort") or ""),
                "network": meta.get("network") or "",
                "rule": c.get("rule") or "",
                "payload": c.get("rulePayload") or "",
                "chains": c.get("chains") or [],
                "upload": int(c.get("upload") or 0),
                "download": int(c.get("download") or 0),
                "start": c.get("start") or "",
                "process": os.path.basename(str(meta.get("processPath") or "")),
            })
        return {"connections": out,
                "upload_total": int(data.get("uploadTotal") or 0),
                "download_total": int(data.get("downloadTotal") or 0),
                "memory": int(data.get("memory") or 0)}

    def close_connection(self, conn_id):
        self.api("DELETE", "/connections/" + urllib.parse.quote(str(conn_id), safe=""))
        return True

    def close_all_connections(self):
        self.api("DELETE", "/connections")
        return True

    def test_node(self, index, timeout_ms=5000):
        """经内核测速（/proxies/{name}/delay），返回毫秒延迟。"""
        nodes = self.store.nodes()
        if not (0 <= index < len(nodes)):
            raise ValueError("节点索引无效")
        name = _proxy_name(index, nodes[index])
        path = ("/proxies/%s/delay?timeout=%d&url=%s"
                % (urllib.parse.quote(name, safe=""), int(timeout_ms),
                   urllib.parse.quote("http://www.gstatic.com/generate_204", safe="")))
        data = self.api("GET", path, timeout=int(timeout_ms / 1000) + 6)
        lat = data.get("delay")
        if lat is None:
            raise ValueError(data.get("message") or "测速失败")
        nodes[index]["latency"] = int(lat)
        self.store.save()
        return int(lat)

    def test_group(self, group, timeout_ms=5000):
        """整组测速：对组内每个成员调内核 delay 接口。"""
        data = (self.api("GET", "/proxies") or {}).get("proxies") or {}
        info = data.get(group) or {}
        out = []
        for name in (info.get("all") or []):
            try:
                r = self.api("GET",
                             "/proxies/%s/delay?timeout=%d&url=%s"
                             % (urllib.parse.quote(name, safe=""), int(timeout_ms),
                                urllib.parse.quote(
                                    "http://www.gstatic.com/generate_204", safe="")),
                             timeout=int(timeout_ms / 1000) + 6)
                out.append({"name": name, "ok": r.get("delay") is not None,
                            "latency": r.get("delay")})
            except Exception as e:
                out.append({"name": name, "ok": False, "err": str(e)[:80]})
        return out

    def burst_test(self, timeout_ms=5000):
        """全部节点测速（按延迟排序返回）。"""
        nodes = self.store.nodes()
        results = []
        for i, n in enumerate(nodes):
            try:
                lat = self.test_node(i, timeout_ms)
                results.append({"index": i, "remark": n.get("remark"), "ok": True,
                                "latency": lat})
            except Exception as e:
                results.append({"index": i, "remark": n.get("remark"), "ok": False,
                                "err": str(e)[:80]})
        results.sort(key=lambda x: (0, x["latency"]) if x.get("ok") else (1, 0))
        return results

    # -- 状态 -------------------------------------------------------------
    def bin_version(self):
        """内核自报版本（mihomo -v）：可直观看出用的是 v1/v2/v3 哪个构建。

        带缓存（按 exe 路径 + mtime），失败返回空串。
        """
        path = self.bin_path
        if not path or not os.path.isfile(path):
            return ""
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            key = (path, 0)
        with self._ver_cache_lock:
            if self._ver_cache.get("key") == key:
                return self._ver_cache.get("value", "")
        value = ""
        try:
            out = subprocess.run([path, "-v"], capture_output=True, timeout=6,
                                 creationflags=_NOWIN)
            text = (out.stdout or out.stderr or b"").decode("utf-8", "replace")
            value = (text.splitlines() or [""])[0].strip()[:160]
        except Exception:
            value = ""
        with self._ver_cache_lock:
            self._ver_cache = {"key": key, "value": value}
        return value

    def state(self):
        return {
            "running": self.running,
            "healthy": self.healthy and self.running,
            "bin": self.bin_path,
            "build": self.bin_version(),
            "core_ok": bool(self.bin_path and os.path.isfile(self.bin_path)),
            "mixed_port": self.mixed_port,
            "api_port": self.api_port,
            "mode": self.mode,
            "tun": self.tun,
            "sys_proxy": self.sys_proxy,
            "admin": _is_admin(),
            "geo_ok": geo_available(),
            "node_count": len(self.store.nodes()),
            "data_dir": self.data_dir,
        }


def _latency_of(entry):
    """从 Clash API 的 proxy 条目里取最近一次测速延迟。"""
    try:
        hist = (entry or {}).get("history") or []
        if hist:
            return hist[-1].get("delay")
    except Exception:
        pass
    return None
