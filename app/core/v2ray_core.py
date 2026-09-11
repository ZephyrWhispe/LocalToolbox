"""V2rayN 集成核心：节点解析 / 节点库持久化 / v2ray-xray 核心托管与自动重连。

支持节点来源：
- 订阅 URL（base64 或明文分享链接列表）
- V2rayN 安装目录（guiNConfig / configN / 含 outbounds 的 JSON）
- 剪贴板分享链接（vless:// vmess:// trojan:// ss:// socks:// http://
  hy2:// tuic:// wireguard:// anytls:// 及 v2rayn:// 内部格式，对齐 v2rayN 订阅说明）

完整代理控制：自跑 xray / v2ray / sing-box 三核心 → 探活 → 设置系统代理
（对齐 v2rayN：自动配置 / PAC / 清除 / 不改变 四策略）；失败自动切节点；
停止 / 异常 / 退出时按策略还原系统代理。仅用标准库（urllib/winreg/subprocess/base64）。
"""

import base64
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import uuid
import urllib.request
from urllib.parse import quote, unquote, urlparse, parse_qs

from .config import DATA_HOME

# 窗口化进程（console=False 的 exe）里启动控制台程序会弹出黑窗，
# 关掉窗口即杀掉核心进程——所有子进程必须禁用控制台窗口
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PROXY_DIR = os.path.join(DATA_HOME, "proxy")
NODES_FILE = os.path.join(PROXY_DIR, "nodes.json")
ADVANCED_FILE = os.path.join(PROXY_DIR, "advanced.json")
GROUPS_FILE = os.path.join(PROXY_DIR, "groups.json")
GEO_DIR = os.path.join(PROXY_DIR, "bin")

# 核心类型
CORE_XRAY = "xray"
CORE_SING = "sing-box"
CORE_V2RAY = "v2ray"
CORES = (CORE_XRAY, CORE_SING, CORE_V2RAY)

# 节点协议 → 支持它的核心（对齐 v2rayN wiki）：
# anytls / hysteria2 / tuic 仅 sing-box 支持——Xray 官方从未实现 anytls
# （配置里写 anytls 会报 unknown config id），v2ray-core 同样没有这三种。
_PROTO_CORES = {
    "vless": (CORE_XRAY, CORE_V2RAY, CORE_SING),
    "vmess": (CORE_XRAY, CORE_V2RAY, CORE_SING),
    "trojan": (CORE_XRAY, CORE_V2RAY, CORE_SING),
    "ss": (CORE_XRAY, CORE_V2RAY, CORE_SING),
    "socks": (CORE_XRAY, CORE_V2RAY, CORE_SING),
    "http": (CORE_XRAY, CORE_V2RAY, CORE_SING),
    "wireguard": (CORE_XRAY, CORE_SING),
    "hy2": (CORE_SING,),
    "tuic": (CORE_SING,),
    "anytls": (CORE_SING,),
}


def protocol_cores(proto):
    """节点协议 → 支持的核心元组（未知协议默认三核心均可尝试）。"""
    return _PROTO_CORES.get(str(proto or ""), CORES)


def protocol_supported(proto, core_type):
    return str(core_type) in protocol_cores(proto)


def _core_bin_candidates(core_type, configured="", v2rayn_dir=""):
    """核心可执行文件候选路径（按优先级）。

    覆盖三类落盘位置：用户指定 / V2rayN 安装目录 / 应用内下载目录。
    注意下载器（bindl）落在 %DATA_HOME%/<核心>/bin/<核心>.exe，与规则库目录
    （%DATA_HOME%/proxy/bin，GEO_DIR）不同——漏掉下载目录会导致「已下载但找不到核心」。
    """
    names = {CORE_XRAY: ("xray.exe",), CORE_V2RAY: ("v2ray.exe",),
             CORE_SING: ("sing-box.exe",)}[core_type]
    cands = []
    if configured:
        cands.append(configured)
    if v2rayn_dir:
        cands.extend(os.path.join(v2rayn_dir, nm) for nm in names)
    cands.append(os.path.join(DATA_HOME, core_type, "bin", names[0]))
    cands.append(os.path.join(DATA_HOME, core_type, names[0]))
    cands.append(os.path.join(GEO_DIR, names[0]))
    cands.append(os.path.join(PROXY_DIR, names[0]))
    return cands

# 代理模式
MODE_GLOBAL = "global"
MODE_SMART = "smart"
MODE_DIRECT = "direct"

# 系统代理策略（对齐 v2rayN：清除 / 自动配置 / 不改变 / PAC）
SYS_PROXY_AUTO = "auto"    # 自动配置系统代理：启动写入，停止还原
SYS_PROXY_PAC = "pac"      # 写入 Windows AutoConfigURL 指向本地 PAC 脚本
SYS_PROXY_NONE = "none"    # 不改变系统代理（保留其他软件设定）
SYS_PROXY_CLEAR = "clear"  # 启动/重启服务时强制清除系统代理
SYS_PROXY_MODES = (SYS_PROXY_AUTO, SYS_PROXY_PAC, SYS_PROXY_NONE, SYS_PROXY_CLEAR)

# 智能分流（sing-box 与 geo 缺失时的 xray 降级）：内网与常用国内地址直连
SMART_CIDRS = [
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    "169.254.0.0/16", "224.0.0.0/4", "114.114.114.114/32", "223.5.5.5/32",
]
SMART_DIRECT_DOMAINS = [
    ".cn", "baidu.com", "taobao.com", "tmall.com", "alipay.com", "qq.com",
    "weixin", "tencent.com", "163.com", "126.com", "sina", "weibo", "bilibili",
    "douyin", "kuaishou", "jd.com", "mi.com", "bytedance", "meituan", "12306",
    "pinduoduo", "ctrip.com", "cnzz", "alicdn", "qq", "wechat",
]

# 连通性探测源：第一项国际、后两项国内可达，取任意一个 204 即通
PROBE_URLS = (
    "http://www.gstatic.com/generate_204",
    "http://connect.rom.miui.com/generate_204",
    "https://cp.cloudflare.com/generate_204",
)

_WS_REALITY = re.compile(r"[A-Za-z0-9+/=]")


def _b64dec(s, url=False):
    """url-safe base64 解码为 str；失败返回 None。"""
    if not s:
        return None
    data = s
    pad = "=" * (-len(data) % 4)
    try:
        raw = base64.urlsafe_b64decode(data + pad) if url else base64.b64decode(data + pad)
        return raw.decode("utf-8", "replace")
    except Exception:
        return None


def _looks_base64(text):
    """整体为 base64 特征（长度合理且字符集受限）。"""
    t = str(text).strip()
    if not t or len(t) < 24:
        return False
    return bool(_WS_REALITY.match(t[:1])) and bool(re.fullmatch(r"[A-Za-z0-9+/=\r\n\t ]+", t))


# --------------------------------------------------------------------------
# 分享链接解析
# --------------------------------------------------------------------------
def parse_share_link(line):
    """解析单行分享链接，返回节点 dict；无法识别返回 None。"""
    line = str(line or "").strip()
    if not line or line.startswith("#") or line.startswith("//"):
        return None
    if line.lower().startswith("vmess://"):
        return _parse_vmess(line)
    if line.lower().startswith("vless://"):
        return _parse_uri("vless", line)
    if line.lower().startswith("trojan://"):
        return _parse_uri("trojan", line)
    if line.lower().startswith("ss://"):
        return _parse_ss(line)
    if line.lower().startswith(("socks5://", "socks://")):
        return _parse_uri("socks", line)
    if line.lower().startswith("tuic://"):
        return _parse_tuic(line)
    if line.lower().startswith("hy2://") or line.lower().startswith("hysteria2://"):
        return _parse_hy2(line)
    if line.lower().startswith("wireguard://"):
        return _parse_wireguard(line)
    if line.lower().startswith("anytls://"):
        return _parse_anytls(line)
    if line.lower().startswith("v2rayn://"):
        return _parse_v2rayn(line)
    if line.lower().startswith("http://") and "@" in line.split("#")[0]:
        # http://user:pass@host:port（用户信息型 http 代理节点）
        return _parse_uri("http", line)
    if line.lower().startswith("https://"):
        return None
    return None


def _vmess_node(d):
    return {
        "type": "vmess",
        "remark": str(d.get("ps") or d.get("remarks") or d.get("add") or "vmess"),
        "addr": str(d.get("add", "")),
        "port": int(d.get("port") or 0),
        "id": str(d.get("id", "")),
        "method": str(d.get("scy") or "auto"),
        "security": str(d.get("scy") or "auto"),
        "tls": ("tls" if str(d.get("tls", "")).lower() in ("tls", "1") else "none"),
        "sni": str(d.get("sni") or ""),
        "alpn": str(d.get("alpn") or ""),
        "fp": str(d.get("fp") or ""),
        "flow": str(d.get("flow") or ""),
        "network": str(d.get("net") or "tcp"),
        "host": str(d.get("host") or ""),
        "path": str(d.get("path") or ""),
        "header": str(d.get("type") or ""),
        "grpc_mode": str(d.get("grpcMode") or "gun"),
        "sv": str(d.get("sv") or ""),
    }


def _parse_vmess(line):
    payload = line[len("vmess://"):].split("#")[0]
    remark = line.split("#", 1)[1] if "#" in line else ""
    text = _b64dec(payload, url=True)
    if not text:
        return None
    try:
        d = json.loads(text)
    except ValueError:
        return None
    if not isinstance(d, dict) or not d.get("add") or not d.get("id"):
        return None
    node = _vmess_node(d)
    if remark:
        node["remark"] = unquote(remark)
    return node


def _parse_uri(proto, line):
    """vless / trojan / socks / http 通用解析：scheme://info?query#remark"""
    head, _, remark = line.partition("#")
    try:
        purl = urlparse(head)
    except ValueError:
        return None
    host = purl.hostname
    port = purl.port
    if not host or not port:
        return None
    if proto in ("vless", "trojan"):
        ident = unquote(purl.username or "") if "@" in head.split("?")[0] else None
        if ident is None:
            m = re.match(r"^[^@]*(?:://)([^@]+)@", head)
            ident = unquote(m.group(1)) if m else None
        if not ident:
            return None
    elif proto in ("socks", "http"):
        user = unquote(purl.username or "")
        pwd = unquote(purl.password or "") if purl.password else ""
    q = parse_qs(purl.query)
    def gv(k, default=""):
        return q.get(k, [default])[0]
    node = {
        "type": proto,
        "remark": unquote(remark) if remark else ("%s:%d" % (host, port)),
        "addr": host,
        "port": port,
        "id": ident if proto in ("vless", "trojan") else "",
        "method": "",
        "security": "",
        "tls": gv("security", "none"),
        "sni": gv("sni", gv("serverName")),
        "alpn": gv("alpn"),
        "fp": gv("fp", gv("fingerprint")),
        "flow": gv("flow"),
        "network": gv("type", "tcp"),
        "host": gv("host"),
        "path": gv("path"),
        "header": gv("headerType"),
        "grpc_mode": gv("serviceName"),
        "sv": gv("sv"),
        "user": user if proto in ("socks", "http") else "",
        "pass": pwd if proto in ("socks", "http") else "",
    }
    return node


def _parse_ss(line):
    head, _, remark = line.partition("#")
    payload = head[len("ss://"):]
    if "/" in payload:
        # ssr 风格 base64(method:password@host:port) 完整
        text = _b64dec(payload.split("/")[0], url=True)
        if text:
            n = _ss_from_userinfo(text)
        else:
            return None
    else:
        at = payload.rfind("@")
        if at < 0:
            return None
        left, right = payload[:at], payload[at + 1:]
        try:
            purl = urlparse("//" + right)
            host, port = purl.hostname, purl.port
        except ValueError:
            return None
        if not host or not port:
            return None
        m = re.match(r"^([^:]+):(.*)$", _b64dec(left, url=True) or left)
        if not m:
            return None
        method, password = m.group(1), m.group(2)
        n = {
            "addr": host, "port": port,
            "method": method, "password": _urlsafe_pass(password),
        }
    node = {
        "type": "ss",
        "remark": unquote(remark) if remark else n.get("remark") or ("%s:%d" % (n["addr"], n["port"])),
        "addr": n["addr"],
        "port": n["port"],
        "id": n.get("password") or n.get("id") or "",
        "method": n.get("method") or "aes-256-gcm",
        "security": "",
        "tls": "none",
        "sni": "", "alpn": "", "fp": "", "flow": "",
        "network": "tcp", "host": "", "path": "", "header": "",
        "grpc_mode": "", "sv": "", "user": "", "pass": "",
    }
    return node


def _ss_from_userinfo(text):
    """解析 method:password@host:port"""
    at = text.rfind("@")
    if at < 0:
        return {"addr": "", "port": 0, "method": "", "password": "", "remark": ""}
    left, right = text[:at], text[at + 1:]
    try:
        purl = urlparse("//" + right)
        host, port = purl.hostname, purl.port
    except ValueError:
        host, port = "", 0
    if ":" in left:
        method, password = left.split(":", 1)
    else:
        method, password = "aes-256-gcm", left
    return {"addr": host or "", "port": port or 0, "method": method,
            "password": password, "remark": ""}


def _urlsafe_pass(p):
    # 部分 ss 链接密码经 url 编码
    return unquote(p)


def _looks_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def _maybe_b64_remark(text):
    """备注尝试 base64 解码（部分订阅把备注单独编码）。

    仅当解码结果含中文、且原文不含中文时才替换——避免把 "test" 这类
    本身合法的 base64 字符串误解码成乱码。
    """
    t = str(text or "")
    if not t or _looks_cjk(t) or len(t) < 8:
        return t
    try:
        dec = _b64dec(t, url=True)
    except Exception:
        return t
    if dec and _looks_cjk(dec):
        return dec.strip()
    return t


def _common_uri_node(proto, host, port, q, remark):
    """按 query 组备份协议节点公共字段（对齐 vless 分享链接字段约定）。"""
    def gv(k, default=""):
        return q.get(k, [default])[0]
    return {
        "type": proto,
        "remark": _maybe_b64_remark(unquote(remark)) if remark else ("%s:%d" % (host, port)),
        "addr": host,
        "port": port,
        "id": "",
        "method": "",
        "security": gv("security", ""),
        "tls": "tls" if gv("security", "").lower() in ("tls", "1") or gv("insecure", "") == "1" else gv("security", "none"),
        "sni": gv("sni") or gv("serverName"),
        "alpn": gv("alpn"),
        "fp": gv("fp") or gv("fingerprint"),
        "flow": gv("flow"),
        "network": gv("type", "tcp"),
        "host": gv("host"),
        "path": gv("path"),
        "header": gv("headerType"),
        "grpc_mode": gv("serviceName"),
        "sv": gv("sv"),
        "user": "",
        "pass": "",
    }


def _parse_hy2(line):
    """hy2://password@host:port?sni=&insecure=&obfs=&obfs-password=&pinSHA256=&up=&down=#备注"""
    head, _, remark = line.partition("#")
    try:
        purl = urlparse(head)
    except ValueError:
        return None
    host, port = purl.hostname, purl.port
    password = unquote(purl.username or "") if "@" in head.split("?")[0] else ""
    if not host or not port or not password:
        return None
    q = parse_qs(purl.query)
    def gv(k, default=""):
        return q.get(k, [default])[0]
    node = _common_uri_node("hy2", host, port, q, remark)
    node["id"] = password
    node["tls"] = "tls"  # Hysteria2 本体即基于 QUIC+TLS
    node["obfs"] = gv("obfs")
    node["obfs_password"] = gv("obfs-password", gv("obfsPassword"))
    node["pin_sha256"] = gv("pinSHA256")
    node["up_mbps"] = _to_float(gv("up", gv("upMbps")))
    node["down_mbps"] = _to_float(gv("down", gv("downMbps")))
    node["allow_insecure"] = 1 if str(gv("insecure", "0")).lower() in ("1", "true") else 0
    return node


def _parse_tuic(line):
    """tuic://uuid:password@host:port?congestion_control=&alpn=&sni=&udp_relay_mode=#备注"""
    head, _, remark = line.partition("#")
    try:
        purl = urlparse(head)
    except ValueError:
        return None
    host, port = purl.hostname, purl.port
    if not host or not port:
        return None
    userinfo = unquote(purl.username or "") if "@" in head.split("?")[0] else ""
    if purl.password:
        userinfo += ":" + unquote(purl.password)
    uuid, _, pwd = userinfo.partition(":")

    if not uuid:
        return None
    q = parse_qs(purl.query)
    def gv(k, default=""):
        return q.get(k, [default])[0]
    node = _common_uri_node("tuic", host, port, q, remark)
    node["id"] = uuid
    node["pass"] = pwd
    node["congestion_control"] = gv("congestion_control", "bbr")
    node["udp_relay_mode"] = gv("udp_relay_mode", "native")
    node["zero_rtt"] = 1 if gv("zero_rtt_handshake", "0") == "1" else 0
    node["allow_insecure"] = 1 if gv("allow_insecure", gv("insecure", "0")) in ("1", "true") else 0
    return node


def _parse_anytls(line):
    """anytls://password@host:port?security=tls&sni=&pbk=&spx=#备注"""
    head, _, remark = line.partition("#")
    try:
        purl = urlparse(head)
    except ValueError:
        return None
    host, port = purl.hostname, purl.port
    password = unquote(purl.username or "") if "@" in head.split("?")[0] else ""
    if not host or not port or not password:
        return None
    q = parse_qs(purl.query)
    def gv(k, default=""):
        return q.get(k, [default])[0]
    node = _common_uri_node("anytls", host, port, q, remark)
    node["id"] = password
    node["tls"] = "tls" if gv("security", "").lower() in ("tls", "1", "") else "none"
    node["pbk"] = gv("pbk")
    node["spx"] = gv("spx")
    return node


def _parse_wireguard(line):
    """wireguard://<base64(配置JSON)>/?remarks= 或 wireguard://<私钥>@host:port#备注"""
    head, _, remark = line.partition("#")
    payload = head[len("wireguard://"):]
    plain = _b64dec(payload.split("/")[0].split("?")[0], url=True)
    node = {
        "type": "wireguard", "remark": unquote(remark) or "wireguard",
        "addr": "", "port": 0, "id": "", "method": "", "security": "",
        "tls": "none", "sni": "", "alpn": "", "fp": "", "flow": "",
        "network": "tcp", "host": "", "path": "", "header": "",
        "grpc_mode": "", "sv": "", "user": "", "pass": "",
        "wg_private": "", "wg_address": [], "wg_mtu": 1420, "wg_peers": [],
    }
    if plain and plain.lstrip().startswith("{"):
        # 仅当解密结果为 JSON 对象时按配置体解析；否则落到私钥@主机:端口形式
        try:
            d = json.loads(plain)
            if isinstance(d, dict):
                node["remark"] = unquote(remark) or d.get("remarks") or "wireguard"
                node["wg_private"] = str(d.get("private_key") or d.get("privateKey") or "")
                la = d.get("local_address") or d.get("address") or []
                node["wg_address"] = list(la) if isinstance(la, list) else [str(la)] if la else []
                node["wg_mtu"] = int(d.get("mtu") or 1420)
                for p in d.get("peers") or []:
                    if not isinstance(p, dict):
                        continue
                    ep = str(p.get("endpoint") or "")
                    node["addr"] = ep.split(":")[0] if ep else node["addr"]
                    try:
                        node["port"] = int(ep.rsplit(":", 1)[1]) if ep else node["port"]
                    except (IndexError, ValueError):
                        pass
                    node["wg_peers"].append({
                        "public_key": str(p.get("public_key") or p.get("publicKey") or ""),
                        "endpoint": ep,
                        "allowed_ips": str(p.get("allowed_ips") or "0.0.0.0/0,::/0"),
                        "keepalive": p.get("keepalive") or 25,
                    })
                if not node["addr"] and node["wg_peers"]:
                    ep = node["wg_peers"][0]["endpoint"]
                    node["addr"] = ep.split(":")[0] if ep else ""
                    try:
                        node["port"] = int(ep.rsplit(":", 1)[1]) if ep else 0
                    except (IndexError, ValueError):
                        pass
                return node if node["addr"] and node["port"] else (node if node["wg_peers"] else None)
        except ValueError:
            return None
        return None
    # 私钥@host:port 形式
    at = payload.find("@")
    if at < 0:
        return None
    host, _sep, _ = payload[at + 1:].partition("/")
    try:
        purl = urlparse("//" + host)
        h, p = purl.hostname, purl.port
    except ValueError:
        h, p = None, None
    if not h or not p:
        return None
    q = parse_qs(urlparse(head).query)
    def gv(k, default=""):
        return q.get(k, [default])[0]
    node["remark"] = unquote(remark) or ("%s:%d" % (h, p))
    node["addr"], node["port"] = h, p
    node["wg_private"] = payload[:at]
    node["wg_peers"] = [{
        "public_key": gv("pk", gv("publicKey")),
        "endpoint": "%s:%d" % (h, p),
        "allowed_ips": gv("allowed_ips", "0.0.0.0/0,::/0"),
        "keepalive": _to_float(gv("keepalive", "25")),
    }]
    node["wg_address"] = [a for a in re.split(r",\s*", gv("local_address", "")) if a]
    id_ = payload[:at]
    node["id"] = id_
    return node


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# v2rayn:// 内部格式：v2rayN 订阅/备份的唯一格式（wiki 订阅功能说明）
_V2RAYN_PROTO = {
    "vmess": "vmess", "vless": "vless", "ss": "ss", "shadowsocks": "ss",
    "socks": "socks", "socks5": "socks", "http": "http",
    "trojan": "trojan", "hy2": "hy2", "hysteria2": "hy2",
    "tuic": "tuic", "wireguard": "wireguard", "anytls": "anytls",
}
_V2RAYN_TYPE_BY_NUM = {
    1: "vmess", 3: "ss", 4: "socks", 5: "vless", 6: "trojan",
    7: "hy2", 8: "tuic", 9: "wireguard", 10: "http", 11: "anytls",
}


def _pi(d, *names):
    """ProfileItem 字段不区分大小写读取（v2rayn 内部格式字段名多样）。"""
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return ""


def _parse_v2rayn(line):
    """v2rayn://<协议token>/<urlsafe-base64(ProfileItemDtoJson)>"""
    rest = line[len("v2rayn://"):]
    token, _, b64 = rest.partition("/")
    token = (token or "").lower()
    text = _b64dec(b64, url=True)
    if not text:
        return None
    try:
        d = json.loads(text)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    proto = _V2RAYN_PROTO.get(token) or _V2RAYN_TYPE_BY_NUM.get(int(_pi(d, "ConfigType") or 0)) or token
    if proto not in ("vmess", "vless", "ss", "socks", "http", "trojan", "hy2", "tuic", "wireguard", "anytls"):
        return None  # 策略组/链式代理等跳过
    host = _pi(d, "Address")
    port_s = _pi(d, "Port")
    try:
        port = int(port_s)
    except (TypeError, ValueError):
        port = 0
    if not host or not port:
        return None
    remark = unquote(_pi(d, "Remarks")) or ("%s:%d" % (host, port))
    node = {
        "type": proto, "remark": remark, "addr": host, "port": port,
        "id": _pi(d, "Id", "UUID"),
        "method": _pi(d, "Security", "Method"),
        "security": _pi(d, "StreamSecurity", ""),
        "tls": "tls" if _pi(d, "StreamSecurity", "").lower() in ("tls", "1", "reality") else "none",
        "sni": _pi(d, "Sni", "ServerName"),
        "alpn": _pi(d, "Alpn"),
        "fp": _pi(d, "FingerPrint"),
        "flow": _pi(d, "Flow"),
        "network": _pi(d, "Network", "Type") or "tcp",
        "host": _pi(d, "Host"),
        "path": _pi(d, "Path"),
        "header": _pi(d, "HeaderType"),
        "grpc_mode": _pi(d, "ServiceName"),
        "sv": _pi(d, "Sid", "SpiderX"),
        "user": _pi(d, "Username"),
        "pass": _pi(d, "Password"),
    }
    if proto == "ss":
        node["id"] = _pi(d, "Password") or _pi(d, "Id")
    if proto in ("hy2", "tuic", "anytls") and not node["id"]:
        node["id"] = _pi(d, "Password")
    if proto == "hy2":
        node.setdefault("obfs", _pi(d, "Obfs"))
        node.setdefault("obfs_password", _pi(d, "ObfsPassword"))
        node.setdefault("pin_sha256", _pi(d, "PinSHA256"))
        node.setdefault("up_mbps", _to_float(_pi(d, "UpMbps")))
        node.setdefault("down_mbps", _to_float(_pi(d, "DownMbps")))
    if proto == "tuic":
        node["congestion_control"] = _pi(d, "CongestionControl") or "bbr"
        node["udp_relay_mode"] = _pi(d, "UdpRelayMode") or "native"
        node["zero_rtt"] = 1 if str(_pi(d, "ZeroRttHandshake")).lower() in ("1", "true") else 0
        node["allow_insecure"] = 1 if str(_pi(d, "AllowInsecure")).lower() in ("1", "true") else 0
    if proto == "wireguard":
        node["wg_private"] = _pi(d, "PrivateKey", "SecretKey")
        node["wg_mtu"] = int(_pi(d, "Mtu") or 1420)
        node["wg_address"] = [a for a in re.split(r",\s*", _pi(d, "LocalAddress")) if a]
        node["wg_peers"] = []
        peers = d.get("Peers") if isinstance(d.get("Peers"), list) else []
        for p in peers:
            if not isinstance(p, dict):
                continue
            node["wg_peers"].append({
                "public_key": _pi(p, "PublicKey"),
                "endpoint": _pi(p, "Endpoint"),
                "allowed_ips": _pi(p, "AllowedIPs") or "0.0.0.0/0,::/0",
                "keepalive": 25,
            })
        if node["wg_peers"] and not node["id"]:
            node["addr"] = node["wg_peers"][0]["endpoint"].split(":")[0]
            try:
                node["port"] = int(node["wg_peers"][0]["endpoint"].rsplit(":", 1)[1])
            except (IndexError, ValueError):
                pass
    if proto == "anytls":
        node["tls"] = "tls"
        node.setdefault("pbk", _pi(d, "PBK"))
        node.setdefault("spx", _pi(d, "Spx"))
    return node


# --------------------------------------------------------------------------
# 节点库
# --------------------------------------------------------------------------
# 订阅信息行特征（v2rayN 把这类条目单列为「信息」，不当作可代理节点）：
# 它们的 addr/port 通常指向入口线路，被当作节点选中会直接连不上
_INFO_REMARK_KEYS = ("剩余流量", "套餐", "到期", "重置", "官网", "订阅",
                     "流量", "有效期", "距离下次")


def is_info_node(n):
    """是否为订阅信息行（剩余流量/套餐到期一类，不参与选择与自动切换）。"""
    remark = str((n or {}).get("remark") or "")
    if not remark:
        return False
    return any(k in remark for k in _INFO_REMARK_KEYS)


class NodeStore:
    def __init__(self, path=NODES_FILE):
        self.path = path
        self.data = {"sub_url": "", "updated_at": "", "selected": 0, "nodes": []}
        self._lock = threading.Lock()
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("nodes"), list):
                self.data = data
        except (OSError, ValueError):
            pass
        if not isinstance(self.data.get("nodes"), list):
            self.data["nodes"] = []
        self._purge_info_nodes()

    def _purge_info_nodes(self):
        """清理订阅信息行（剩余流量/套餐到期一类）。

        这类条目不是可代理节点（v2rayN 也单独归纳为「信息」），早期版本按普通
        节点导入后，默认选中常落在信息行上——表现为启动后节点不可达。清理时把
        selected 顺延到下一个真实节点并立即落盘，保证后续索引一致。
        """
        nodes = self.data.get("nodes") or []
        clean = [n for n in nodes if not is_info_node(n)]
        if len(clean) == len(nodes):
            return
        sel = int(self.data.get("selected") or 0)
        removed_before = sum(1 for n in nodes[:max(0, sel)] if is_info_node(n))
        self.data["nodes"] = clean
        self.data["selected"] = max(0, min(sel - removed_before,
                                           len(clean) - 1)) if clean else 0
        self.save()

    def save(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            except OSError:
                pass

    @staticmethod
    def _key(n):
        return "%s|%s|%s|%s" % (n.get("type"), n.get("addr"), n.get("port"), n.get("id"))

    def merge(self, nodes, sub_url=""):
        """去重合并节点，返回新增数量与总数量。"""
        existing = {self._key(n) for n in self.data["nodes"]}
        added = 0
        for n in nodes:
            if self._key(n) in existing:
                continue
            self.data["nodes"].append(n)
            existing.add(self._key(n))
            added += 1
        if sub_url:
            self.data["sub_url"] = sub_url
        self.data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save()
        return added, len(self.data["nodes"])

    def set_selected(self, index):
        if 0 <= index < len(self.data["nodes"]):
            self.data["selected"] = index
            self.save()
            return True
        return False

    def delete(self, index):
        if not (0 <= index < len(self.data["nodes"])):
            return False
        self.data["nodes"].pop(index)
        sel = self.data.get("selected", 0)
        if sel >= len(self.data["nodes"]):
            self.data["selected"] = max(0, len(self.data["nodes"]) - 1)
        self.save()
        return True

    def nodes(self):
        return list(self.data["nodes"])

    def replace_all(self, nodes, sub_url=""):
        """订阅更新：整体替换节点列表（保留未变化者）"""
        keep = {self._key(n): n for n in self.data["nodes"]}
        merged = []
        seen = set()
        for n in nodes:
            k = self._key(n)
            if k in seen or not n.get("addr"):
                continue
            seen.add(k)
            merged.append(keep.get(k, n))
        self.data["nodes"] = merged
        self.data["sub_url"] = sub_url
        self.data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if self.data.get("selected", 0) >= len(merged):
            self.data["selected"] = max(0, len(merged) - 1)
        self.save()
        return len(merged)


# --------------------------------------------------------------------------
# 导入
# --------------------------------------------------------------------------
def parse_sub_text(text):
    """订阅/剪贴板文本 -> 节点列表（整体 base64 解码或逐行解析）。"""
    text = str(text or "")
    if _looks_base64(text):
        dec = _b64dec(re.sub(r"[ \r\n\t]+", "", text), url=True)
        if dec and any(l.lower().startswith(
            ("vless://", "vmess://", "trojan://", "ss://",
             "hy2://", "hysteria2://", "tuic://", "wireguard://", "anytls://", "v2rayn://")
        ) for l in dec.splitlines()):
            text = dec
    nodes = []
    for line in text.splitlines():
        n = parse_share_link(line)
        if n and n.get("addr") and not is_info_node(n):
            nodes.append(n)
    return nodes


def _parse_sub_userinfo(value):
    """解析 subscription-userinfo 响应头（Clash 客户端用它显示流量与到期）。

    形如 ``upload=123; download=456; total=789; expire=1700000000``。
    返回 {upload/download/total/expire: int}，解析不出的键直接省略。
    """
    out = {}
    for part in str(value or "").split(";"):
        key, _, val = part.partition("=")
        key = key.strip().lower()
        val = val.strip()
        if key not in ("upload", "download", "total", "expire") or not val:
            continue
        try:
            out[key] = int(float(val))
        except ValueError:
            continue
    return out


def import_sub_url(url, store, timeout=20):
    """下载订阅并解析合并。返回 (added, total, nodes)。

    同时把响应头中的订阅流量 / 到期（subscription-userinfo）与提供方建议的
    更新周期（profile-update-interval）写入 store.data，供界面展示与自动更新。
    """
    url = str(url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("订阅地址需以 http(s):// 开头")
    req = urllib.request.Request(url, headers={"User-Agent": "LocalToolbox/3.0"})
    headers = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            try:
                headers = resp.headers
            except Exception:
                headers = None
    except Exception as e:
        raise ValueError("订阅下载失败：%s" % e)
    if headers is not None:
        try:
            userinfo = _parse_sub_userinfo(headers.get("subscription-userinfo"))
            if userinfo:
                store.data["sub_userinfo"] = userinfo
            interval = str(headers.get("profile-update-interval") or "").strip()
            if interval.isdigit():
                store.data["sub_interval_hours"] = max(1, min(168, int(interval)))
        except Exception:
            pass
    for codec in ("utf-8", "gbk", "latin-1"):
        try:
            text = data.decode(codec)
            break
        except (UnicodeDecodeError, LookupError):
            text = data.decode("utf-8", "replace")
    nodes = parse_sub_text(text)
    if not nodes:
        raise ValueError("订阅中没有解析到有效节点")
    added, total = store.merge(nodes, sub_url=url)
    return added, total, nodes


def import_v2rayn_dir(path, store):
    """导入 V2rayN 目录内的节点（扫描 *.json 的 outbounds/servers）。"""
    path = str(path or "").strip()
    if not os.path.isdir(path):
        raise ValueError("目录不存在：%s" % path)
    nodes = []
    files = [f for f in os.listdir(path) if f.lower().endswith(".json")]
    if not files:
        raise ValueError("目录中没有找到 JSON 配置文件"
                         "（请选择 V2rayN 的安装目录，如 C:\\Program Files\\V2rayN）")
    for fn in files:
        try:
            with open(os.path.join(path, fn), "r", encoding="utf-8-sig", errors="replace") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        nodes.extend(_extract_from_conf(data))
    nodes = [n for n in nodes if n.get("addr")]
    dedup = {}
    for n in nodes:
        dedup.setdefault(NodeStore._key(n), n)
    nodes = list(dedup.values())
    if not nodes:
        raise ValueError("配置文件中没有识别到 vless/vmess/trojan/ss 节点")
    added, total = store.merge(nodes)
    return added, total, nodes


def _extract_from_conf(data):
    out = []
    for ob in data.get("outbounds", []) or []:
        if not isinstance(ob, dict):
            continue
        proto = str(ob.get("protocol", "")).lower()
        ss = ob.get("streamSettings") or {}
        net = str(ss.get("network", "tcp"))
        sec = "tls" if str(ss.get("security", "none")).lower() == "tls" else "none"
        wss = ss.get("wsSettings") or {}
        tls = ss.get("tlsSettings") or {}
        settings = ob.get("settings") or {}
        if proto in ("vless", "vmess", "trojan"):
            vnext = (settings.get("vnext") or [{}])[0]
            users = vnext.get("users") or [{}]
            u = users[0] if users else {}
            addr = vnext.get("address") or ""
            port = vnext.get("port") or 0
            if addr and port:
                out.append({
                    "type": proto, "remark": ob.get("tag") or ("%s:%s" % (addr, port)),
                    "addr": addr, "port": int(port),
                    "id": u.get("id") or u.get("password") or "",
                    "method": u.get("security", "auto") if proto == "vmess" else "",
                    "security": sec, "tls": sec,
                    "sni": (tls.get("serverName") or ""), "alpn": str(tls.get("alpn") or "").lstrip("[").rstrip("]"),
                    "fp": str(tls.get("fingerprint") or ""), "flow": str(u.get("flow") or vnext.get("flow") or ""),
                    "network": net, "host": (wss.get("headers") or {}).get("Host", ""),
                    "path": wss.get("path", ""), "header": "",
                    "grpc_mode": str((ss.get("grpcSettings") or {}).get("serviceName") or ""),
                    "sv": "", "user": "", "pass": "",
                })
        elif proto == "shadowsocks":
            for sv in settings.get("servers") or []:
                addr = sv.get("address") or ""
                port = sv.get("port") or 0
                if addr and port:
                    out.append({
                        "type": "ss", "remark": ob.get("tag") or ("%s:%s" % (addr, port)),
                        "addr": addr, "port": int(port),
                        "id": sv.get("password") or "", "method": sv.get("method") or "aes-256-gcm",
                        "security": "", "tls": "none", "sni": "", "alpn": "", "fp": "", "flow": "",
                        "network": "tcp", "host": "", "path": "", "header": "",
                        "grpc_mode": "", "sv": "", "user": "", "pass": "",
                    })
        elif proto == "socks":
            for sv in settings.get("servers") or []:
                addr = sv.get("address") or ""
                port = sv.get("port") or 0
                if addr and port:
                    out.append({
                        "type": "socks", "remark": ob.get("tag") or ("%s:%s" % (addr, port)),
                        "addr": addr, "port": int(port), "id": "",
                        "method": "", "security": "", "tls": "none", "sni": "", "alpn": "", "fp": "", "flow": "",
                        "network": "tcp", "host": "", "path": "", "header": "",
                        "grpc_mode": "", "sv": "",
                        "user": ((sv.get("users") or [{}])[0].get("user", "") if sv.get("users") else ""),
                        "pass": ((sv.get("users") or [{}])[0].get("pass", "") if sv.get("users") else ""),
                    })
    # guiNConfig: legacy 字段 servers[]
    for sv in data.get("servers") or []:
        if not isinstance(sv, dict):
            continue
        addr = sv.get("address") or sv.get("server") or ""
        port = sv.get("port") or 0
        if addr and port:
            out.append({
                "type": "ss", "remark": sv.get("remarks") or ("%s:%s" % (addr, port)),
                "addr": addr, "port": int(port),
                "id": sv.get("password") or "", "method": sv.get("method") or "aes-256-cfb",
                "security": "", "tls": "none", "sni": "", "alpn": "", "fp": "", "flow": "",
                "network": "tcp", "host": "", "path": "", "header": "",
                "grpc_mode": "", "sv": "", "user": "", "pass": "",
            })
    return out


# --------------------------------------------------------------------------
# 配置生成（xray / v2ray / sing-box）
# --------------------------------------------------------------------------
def _geo_available():
    return (os.path.isfile(os.path.join(GEO_DIR, "geoip.dat"))
            and os.path.isfile(os.path.join(GEO_DIR, "geosite.dat")))


# sing-box 1.12+ 的本地规则集（.srs）：旧 geoip/geosite 路由与 .db 已被官方移除，
# 仍按旧格式生成配置会让核心直接 FATAL（geoip database is deprecated ... removed）
SING_RULE_SETS = ("geosite-cn.srs", "geosite-ads.srs",
                  "geoip-cn.srs", "geoip-private.srs")
# 下载用的 kind 名（bindl._SRS_URLS 的键）
SING_RULE_SETS_KINDS = ("geosite-cn", "geosite-ads", "geoip-cn", "geoip-private")


def _geo_sing_available():
    """sing-box 规则集是否就绪（.srs，缺任一即退回内置静态分流）。"""
    return all(os.path.isfile(os.path.join(GEO_DIR, f)) for f in SING_RULE_SETS)


def geo_available(core_type=CORE_XRAY):
    """指定核心的规则库是否就绪（xray/v2ray 用 .dat，sing-box 用 .db）。"""
    return _geo_sing_available() if core_type == CORE_SING else _geo_available()


def build_core_config(node, http_port, socks_port, core_type=CORE_XRAY,
                      mode=MODE_SMART, tun=False, advanced=None, stats_port=None,
                      nodes=None, groups=None, clash_api=None):
    """按节点与核心类型生成运行配置 JSON 文本。

    nodes + groups + clash_api 仅 sing-box 使用（策略组与 Clash API 管理接口）；
    xray / v2ray 忽略这些参数，保持单节点配置。
    """
    if core_type == CORE_SING:
        return build_singbox_config(node, http_port, socks_port, mode=mode,
                                    tun=tun, advanced=advanced, nodes=nodes,
                                    groups=groups, clash_api=clash_api)
    return build_xray_config(node, http_port, socks_port, mode=mode,
                             advanced=advanced, stats_port=stats_port)


# 广告拦截（对齐 v2rayN 自定义路由规则示例：geosite:category-ads-all -> block）
AD_RULE = {"type": "field", "outboundTag": "block", "domain": ["geosite:category-ads-all"]}


def _xray_routing_rules(mode):
    """xray/v2ray 的路由规则（按代理模式；smart = 绕过大陆 + 广告拦截）。"""
    base = [{"type": "field", "inboundTag": ["socks-in", "http-in"],
             "outboundTag": "proxy-main"}]
    if mode == MODE_GLOBAL:
        return base
    if _geo_available():
        return [AD_RULE,
                {"type": "field", "outboundTag": "direct", "ip": ["geoip:private", "geoip:cn"]},
                {"type": "field", "outboundTag": "direct", "domain": ["geosite:cn"]}] + base
    # geo 规则库缺失时的降级：内置静态分流
    return [
        {"type": "field", "outboundTag": "direct", "ip": list(SMART_CIDRS)},
        {"type": "field", "outboundTag": "direct",
         "domain": ["keyword:" + d.lstrip(".") for d in SMART_DIRECT_DOMAINS]},
    ] + base


def _xray_rules_from_v2rayn(rules):
    """把 v2rayN 自定义路由规则数组（wiki 格式）转换为 xray routing.rules。

    [{"port","network","outboundTag":"proxy|direct|block","enabled","remarks"}, ...]
    """
    tag_map = {"proxy": "proxy-main", "direct": "direct", "block": "block"}
    out = []
    for r in rules or []:
        if not isinstance(r, dict) or r.get("enabled") is False:
            continue
        if not (r.get("port") or r.get("network") or r.get("ip") or r.get("domain")
                or r.get("inboundTag")):
            continue  # 空规则/仅备注项跳过
        tag = str(r.get("outboundTag") or "")
        tag = tag_map.get(tag, tag) or "proxy-main"
        rule = {"type": "field", "outboundTag": tag}
        if r.get("port"):
            rule["port"] = r["port"]
        if r.get("network"):
            rule["network"] = r["network"]
        if r.get("ip"):
            rule["ip"] = list(r["ip"]) if isinstance(r["ip"], list) else [str(r["ip"])]
        if r.get("domain"):
            rule["domain"] = list(r["domain"]) if isinstance(r["domain"], list) else [str(r["domain"])]
        if r.get("inboundTag"):
            rule["inboundTag"] = (list(r["inboundTag"]) if isinstance(r["inboundTag"], list)
                                  else [str(r["inboundTag"])])
        out.append(rule)
    return out


# v2rayN 自定义规则里的 geo 前缀 → 本应用下载的 .srs 规则集标签（1.12+ 只能用 rule_set）。
# 类别 → 标签动态解析（_sing_rule_tag）：GEO_DIR 内存在 <kind>-<cat>.srs 即可用，
# 内置 4 件（cn/ads/private）保留历史短名作别名。
_SING_GEO_ALIASES = {
    "geosite": {"category-ads-all": ("geosite-ads", "geosite-category-ads-all")},
    "geoip": {},
}


def _sing_rule_tag(kind, cat):
    """geo 类别 → 本地 .srs 规则集标签；GEO_DIR 无对应文件返回 ""（不可引用）。"""
    cat = str(cat or "").strip().lower()
    if not cat:
        return ""
    cands = _SING_GEO_ALIASES.get(kind, {}).get(cat) or ("%s-%s" % (kind, cat),)
    for stem in cands:
        if os.path.isfile(os.path.join(GEO_DIR, stem + ".srs")):
            return stem
    return ""


def _singbox_rules_from_v2rayn(rules):
    """把 v2rayN 自定义路由规则数组转换为 sing-box route.rules（尽力映射）。

    geoip:/geosite: 前缀必须转成 rule_set 引用——sing-box 1.12 起已移除
    geoip/geosite 字段，继续输出旧字段会让核心 FATAL。按 _sing_rule_tag 动态
    解析本地已下载的 .srs（GEO_DIR 内存在才引用），其余前缀无法表达，忽略并
    由调用方提示（避免生成非法配置）。
    """
    out = []
    for r in rules or []:
        if not isinstance(r, dict) or r.get("enabled") is False:
            continue
        if not (r.get("port") or r.get("network") or r.get("ip") or r.get("domain")):
            continue  # 空规则/仅备注项跳过
        tag = str(r.get("outboundTag") or "")
        tag = {"proxy": "proxy-main", "direct": "direct", "block": "block"}.get(tag, tag)
        if not tag:
            continue
        rule = {"outbound": tag}
        if r.get("port"):
            p = str(r["port"]).strip()
            if "-" in p:
                a, _, b = p.partition("-")
                if b:
                    rule["port_range"] = "%s-%s" % (a, b)
            else:
                try:
                    rule["port"] = int(p)
                except ValueError:
                    pass
        if r.get("network"):
            rule["network"] = r["network"]
        rule_sets = []
        if r.get("ip"):
            vals = list(r["ip"]) if isinstance(r["ip"], list) else [str(r["ip"])]
            cidr = []
            for v in vals:
                v = str(v)
                if v.startswith("geoip:"):
                    mapped = _sing_rule_tag("geoip", v[6:])
                    if mapped:
                        rule_sets.append(mapped)
                else:
                    cidr.append(v)
            if cidr:
                rule["ip_cidr"] = cidr
        if r.get("domain"):
            vals = list(r["domain"]) if isinstance(r["domain"], list) else [str(r["domain"])]
            domain = []
            for v in vals:
                v = str(v)
                if v.startswith("geosite:"):
                    mapped = _sing_rule_tag("geosite", v[8:])
                    if mapped:
                        rule_sets.append(mapped)
                else:
                    domain.append(v)
            if domain:
                rule["domain"] = domain
        if rule_sets:
            rule["rule_set"] = rule_sets
        if len(rule) > 1:      # 除 outbound 外还有有效条件才保留
            out.append(rule)
    return out


def build_xray_config(node, http_port, socks_port, mode=MODE_SMART,
                      advanced=None, stats_port=None):
    """生成 v2ray/xray 兼容 JSON 配置文本。"""
    t = node.get("type")
    ss = _stream_settings(node)
    if t == "vless":
        out = {"tag": "proxy-main", "protocol": "vless",
               "settings": {"vnext": [{"address": node["addr"], "port": node["port"],
                                       "users": [{"id": node["id"], "encryption": "none",
                                                  "flow": node.get("flow") or ""}]}]},
               "streamSettings": ss}
    elif t == "vmess":
        out = {"tag": "proxy-main", "protocol": "vmess",
               "settings": {"vnext": [{"address": node["addr"], "port": node["port"],
                                       "users": [{"id": node["id"], "alterId": 0,
                                                  "security": node.get("method") or "auto"}]}]},
               "streamSettings": ss}
    elif t == "trojan":
        out = {"tag": "proxy-main", "protocol": "trojan",
               "settings": {"servers": [{"address": node["addr"], "port": node["port"],
                                         "password": node["id"], "flow": node.get("flow") or ""}]},
               "streamSettings": ss}
    elif t == "ss":
        out = {"tag": "proxy-main", "protocol": "shadowsocks",
               "settings": {"servers": [{"address": node["addr"], "port": node["port"],
                                         "method": node.get("method") or "aes-256-gcm",
                                         "password": node.get("id") or ""}]},
               "streamSettings": ss}
    elif t == "socks":
        users = ([{"user": node.get("user", ""), "pass": node.get("pass", "")}]
                 if (node.get("user") or node.get("pass")) else None)
        out = {"tag": "proxy-main", "protocol": "socks",
               "settings": {"servers": [{"address": node["addr"], "port": node["port"]}],
                            "outbounds": []}}
        if users:
            out["settings"]["servers"][0]["users"] = users
        out["streamSettings"] = ss
    elif t == "http":
        users = ([{"user": node.get("user", ""), "pass": node.get("pass", "")}]
                 if (node.get("user") or node.get("pass")) else None)
        out = {"tag": "proxy-main", "protocol": "http",
               "settings": {"servers": [{"address": node["addr"], "port": node["port"]}]}}
        if users:
            out["settings"]["servers"][0]["users"] = users
        out["streamSettings"] = ss
    elif t == "hy2":
        st = {"addr": node["addr"], "port": node["port"],
              "password": node.get("id") or "",
              "insecure": bool(node.get("allow_insecure")),
              "sni": node.get("sni") or node.get("host") or "",
              "up_mbps": node.get("up_mbps") or 0,
              "down_mbps": node.get("down_mbps") or 0}
        if node.get("obfs"):
            st["obfs"] = node["obfs"]
        if node.get("obfs_password"):
            st["obfs-password"] = node["obfs_password"]
        if node.get("pin_sha256"):
            st["pinSHA256"] = node["pin_sha256"]
        out = {"tag": "proxy-main", "protocol": "hysteria2", "settings": st}
    elif t == "tuic":
        st = {"address": node["addr"], "port": node["port"],
              "uuid": node.get("id") or "",
              "password": node.get("pass") or "",
              "congestion_control": node.get("congestion_control") or "bbr",
              "udp_relay_mode": node.get("udp_relay_mode") or "native",
              "zero_rtt_handshake": bool(node.get("zero_rtt")),
              "allow_insecure": bool(node.get("allow_insecure"))}
        if node.get("sni"):
            st["sni"] = node["sni"]
        if node.get("alpn"):
            st["alpn"] = [a for a in re.split(r"[, ]", node["alpn"]) if a]
        out = {"tag": "proxy-main", "protocol": "tuic", "settings": st}
    elif t == "wireguard":
        peers = node.get("wg_peers") or []
        out = {"tag": "proxy-main", "protocol": "wireguard",
               "settings": {
                   "secretKey": node.get("wg_private") or node.get("id") or "",
                   "address": node.get("wg_address") or [],
                   "mtu": int(node.get("wg_mtu") or 1420),
                   "peers": [{
                       "publicKey": p.get("public_key", ""),
                       "endpoint": p.get("endpoint", ""),
                       "allowedIPs": [a.strip() for a in
                                      str(p.get("allowed_ips", "0.0.0.0/0")).split(",") if a.strip()],
                   } for p in peers]}}
    elif t == "anytls":
        out = {"tag": "proxy-main", "protocol": "anytls",
               "settings": {"users": [{"password": node.get("id") or ""}]},
               "streamSettings": {"network": "tcp", "security": "tls",
                                  "tlsSettings": {
                                      "serverName": node.get("sni") or node.get("host") or node["addr"],
                                      "allowInsecure": False}}}
    else:
        raise ValueError("不支持的节点类型：%s" % t)

    inbounds = [
        {"tag": "socks-in", "port": int(socks_port), "listen": "127.0.0.1",
         "protocol": "socks", "settings": {"udp": True}},
        {"tag": "http-in", "port": int(http_port), "listen": "127.0.0.1",
         "protocol": "http", "settings": {}},
    ]
    if stats_port:
        inbounds.append({"tag": "api-in", "listen": "127.0.0.1", "port": int(stats_port),
                         "protocol": "dokodemo-door",
                         "settings": {"address": "127.0.0.1"}})
    rules = _xray_routing_rules(mode)
    if advanced and isinstance(advanced.get("routing"), list):
        # 高级配置内为 v2rayN 自定义路由规则数组时整体替换
        vr = _xray_rules_from_v2rayn(advanced["routing"])
        if vr:
            rules = vr
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": [out, {"tag": "direct", "protocol": "freedom"}],
        "routing": {"domainStrategy": "AsIs", "rules": rules},
    }
    if stats_port:
        config["api"] = {"tag": "api", "services": ["StatsService"]}
        config["policy"] = {"system": {"statsInboundUplink": True,
                                       "statsInboundDownlink": True}}
        config["stats"] = {}
    config = _merge_advanced(config, advanced)
    # 路由含 block 规则时补 blackhole 出站（对齐 v2rayN 广告拦截/自定义规则）
    if any(r.get("outboundTag") == "block" for r in (config.get("routing") or {}).get("rules", [])):
        config["outbounds"].append({"tag": "block", "protocol": "blackhole"})
    return json.dumps(config, ensure_ascii=False, indent=2)


def build_singbox_config(node, http_port, socks_port, mode=MODE_SMART,
                         tun=False, advanced=None, nodes=None, groups=None,
                         clash_api=None):
    """生成 sing-box JSON 配置文本（v1.x 格式）。

    nodes + groups 传入时构建「策略组」配置（Clash Verge 风格）：
    每个节点一个出站（node-<索引>）+ selector / urltest 组，route.final 指向主组，
    配合 clash_api 可在运行时切换选择（无需重启核心）。
    """
    group_out, group_tags, primary = [], [], None
    if nodes and groups:
        group_out, group_tags, primary = build_group_outbounds(nodes, groups)
    out = _singbox_outbound(node)
    inbounds = [
        {"type": "socks", "tag": "socks-in", "listen": "127.0.0.1",
         "listen_port": int(socks_port), "users": []},
        {"type": "http", "tag": "http-in", "listen": "127.0.0.1",
         "listen_port": int(http_port)},
    ]
    for ib in inbounds:
        if not ib.get("users"):
            ib.pop("users", None)
    if tun:
        inbounds.insert(0, {
            "type": "tun", "tag": "tun-in",
            "address": ["172.19.0.1/30", "fdf4:d57e:1d9d:100::1/64"],
            "auto_route": True, "strict_route": True, "stack": "mixed",
        })
    route = _singbox_route(mode)
    if advanced and isinstance(advanced.get("routing"), list):
        # 高级配置内为 v2rayN 自定义路由规则数组时整体替换规则，
        # 但保留基础 route 的 rule_set 定义与 default_domain_resolver
        # （丢掉它们会让规则里的 rule_set 引用与解析器标签失效 → FATAL）
        sr = _singbox_rules_from_v2rayn(advanced["routing"])
        if sr:
            defined = {rs.get("tag") for rs in route.get("rule_set", [])}
            sr = [r for r in sr
                  if not r.get("rule_set")
                  or all(t in defined for t in r["rule_set"])]
        if sr:
            base = dict(route)
            base["final"] = base.get("final") or "proxy-main"
            base["rules"] = sr
            route = base
    if primary:
        # 主组接管所有「走代理」的出口（规则里的 proxy-main 一并改名）
        route = dict(route)
        if route.get("final") in (None, "proxy-main"):
            route["final"] = primary
        route["rules"] = [
            ({**r, "outbound": primary} if r.get("outbound") == "proxy-main" else r)
            for r in (route.get("rules") or [])
        ]
    config = {
        "log": {"level": "warn"},
        "dns": _singbox_dns(),
        "inbounds": inbounds,
        "outbounds": (group_out or [out]) + [{"type": "direct", "tag": "direct"}],
        "route": route,
    }
    if clash_api:
        # Clash API：连接列表 / 策略组运行时切换（Clash Verge 同款接口）
        config["experimental"] = {
            "clash_api": clash_api,
            "cache_file": {"enabled": True,
                           "path": os.path.join(PROXY_DIR, "core_cache.db")},
        }
    config = _merge_advanced(config, advanced, singbox=True)
    if any(r.get("outbound") == "block" for r in (config.get("route") or {}).get("rules", [])):
        config["outbounds"].append({"type": "block", "tag": "block"})
    return json.dumps(config, ensure_ascii=False, indent=2)


def _singbox_dns():
    """sing-box DNS 段：用于路由判定的域名解析。

    必须显式指定解析器并只走 IPv4：
    - 系统解析器（local 类型）在本机实测每次冷解析约 5 秒（IPv6/AAAA 等待），
      表现为「每个新网站第一次打开都要等好几秒」；
    - 换成国内公共 DNS（223.5.5.5）+ ipv4_only 后实测首连 0.11 秒。
    解析结果仅用于分流判定（域名仍原样交给代理远端解析），无污染风险。
    """
    return {
        "servers": [{"type": "udp", "tag": "local", "server": "223.5.5.5"}],
        "final": "local",
        "strategy": "ipv4_only",
    }


def load_groups():
    """读取策略组定义（Clash Verge 风格）。

    结构：[{"name": "自动选择", "type": "urltest"|"selector",
           "filter": "香港,日本"（关键字，空或 * = 全部节点）,
           "interval": "5m"（urltest 测速间隔）}]
    """
    try:
        with open(GROUPS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    groups = data.get("groups") if isinstance(data, dict) else data
    if not isinstance(groups, list):
        return []
    out = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        name = str(g.get("name") or "").strip()
        if not name:
            continue
        item = {
            "name": name,
            "type": "urltest" if str(g.get("type")) == "urltest" else "selector",
            "filter": str(g.get("filter") or "").strip(),
            "interval": str(g.get("interval") or "5m"),
        }
        if g.get("default"):
            item["default"] = True
        out.append(item)
    return out


def save_groups(groups):
    """保存策略组定义（原子写）。"""
    os.makedirs(PROXY_DIR, exist_ok=True)
    clean = []
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        name = str(g.get("name") or "").strip()
        if not name:
            continue
        item = {
            "name": name[:40],
            "type": "urltest" if str(g.get("type")) == "urltest" else "selector",
            "filter": str(g.get("filter") or "").strip()[:200],
            "interval": str(g.get("interval") or "5m")[:12],
        }
        if g.get("default"):
            item["default"] = True
        clean.append(item)
    tmp = GROUPS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"groups": clean}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, GROUPS_FILE)
    return clean


def group_members(group, nodes):
    """按过滤关键字解析组成员（返回节点索引列表；空/`*` = 全部）。"""
    kw = str((group or {}).get("filter") or "").strip()
    if not kw or kw == "*":
        return list(range(len(nodes or [])))
    keys = [k.strip().lower() for k in kw.split(",") if k.strip()]
    out = []
    for i, n in enumerate(nodes or []):
        hay = "%s %s %s" % (n.get("remark") or "", n.get("addr") or "",
                            n.get("type") or "")
        hay = hay.lower()
        if any(k in hay for k in keys):
            out.append(i)
    return out


def build_group_outbounds(nodes, groups):
    """生成「每节点一个出站 + 策略组出站」。返回 (outbounds, 组标签列表, 主组标签)。

    - selector：手动选择（Clash 的 Selector）
    - urltest：自动测速选最快（Clash 的 URL-Test）
    节点出站标签固定为 node-<索引>，索引与节点列表一致（前端据此映射备注）。
    """
    out, tag_of = [], {}
    for i, n in enumerate(nodes or []):
        ob = _singbox_outbound(n)
        ob["tag"] = "node-%d" % i
        out.append(ob)
        tag_of[i] = ob["tag"]
    group_tags, primary, default_tag = [], None, None
    for g in groups or []:
        members = [tag_of[i] for i in group_members(g, nodes)]
        if not members:
            continue
        tag = str(g.get("name"))
        if g.get("type") == "urltest":
            out.append({
                "type": "urltest", "tag": tag, "outbounds": members,
                "url": "http://www.gstatic.com/generate_204",
                "interval": g.get("interval") or "5m",
                "tolerance": 50,
            })
        else:
            # selector 可嵌套：先放已定义的组（Clash 的「节点选择」含「自动选择」），
            # 再列节点本身，用户可在运行时就地切换
            out.append({"type": "selector", "tag": tag,
                        "outbounds": list(group_tags) + members,
                        "default": list(group_tags)[0] if group_tags else members[0]})
        group_tags.append(tag)
        if g.get("default"):
            default_tag = tag
    primary = default_tag or (group_tags[0] if group_tags else None)
    # 主组排到最后生成（含其它组作为成员时先有组、后有嵌套）——保持声明顺序即可
    if default_tag:
        primary = default_tag
    if group_tags and not default_tag:
        primary = group_tags[0]
    return out, group_tags, primary


def _singbox_rule_sets():
    """本地 .srs 规则集定义（扫描 GEO_DIR 全部 .srs，供内置分流与统一规则共同引用）。

    标签 = 文件名去扩展名（geosite-cn.srs → geosite-cn）。扫描而非固定清单，
    统一分流规则按需下载的类别 .srs（如 geosite-telegram.srs）自动可用。
    """
    try:
        names = sorted(f for f in os.listdir(GEO_DIR) if f.endswith(".srs"))
    except OSError:
        names = []
    return [{"type": "local", "tag": f[:-4], "format": "binary",
             "path": os.path.join(GEO_DIR, f)} for f in names]


def _singbox_route(mode):
    if mode == MODE_GLOBAL:
        # 全局模式不加内置分流，但保留规则集定义：
        # 用户自定义规则里的 rule_set 引用（geoip:cn / geosite:cn …）需要它们存在
        route = {"final": "proxy-main", "rules": []}
        if _geo_sing_available():
            route["rule_set"] = _singbox_rule_sets()
            route["default_domain_resolver"] = "local"
        return route
    if _geo_sing_available():
        # 规则集就绪：绕过大陆 + 广告拦截（action reject 为新版写法，无需 block 出站）
        return {
            "final": "proxy-main",
            "rule_set": _singbox_rule_sets(),
            "rules": [
                {"action": "sniff"},
                {"rule_set": ["geosite-ads"], "action": "reject"},
                {"rule_set": ["geosite-cn"], "outbound": "direct"},
                {"rule_set": ["geoip-private", "geoip-cn"], "outbound": "direct"},
            ],
            "default_domain_resolver": "local",
        }
    # 规则集缺失：内置静态分流（仅覆盖常用国内网段/域名，会漏不少国内站点）
    return {"final": "proxy-main", "rules": [
        {"action": "sniff"},
        {"domain_keyword": [d.lstrip(".") for d in SMART_DIRECT_DOMAINS], "outbound": "direct"},
        {"ip_cidr": list(SMART_CIDRS), "outbound": "direct"},
    ]}


def _singbox_outbound(node):
    proto = node.get("type")
    tls = _singbox_tls(node)
    tr = _singbox_transport(node)
    base = {"tag": "proxy-main", "server": node["addr"], "server_port": node["port"]}
    if proto == "vless":
        ob = {"type": "vless", **base, "uuid": node["id"], "flow": node.get("flow") or ""}
        ob["flow"] = ob["flow"] if ob["flow"] else None
        if tls:
            ob["tls"] = tls
        if tr:
            ob["transport"] = tr
        _drop_none(ob)
        return ob
    if proto == "vmess":
        ob = {"type": "vmess", **base, "uuid": node["id"],
              "security": node.get("method") or "auto"}
        if tls:
            ob["tls"] = tls
        if tr:
            ob["transport"] = tr
        return ob
    if proto == "trojan":
        ob = {"type": "trojan", **base, "password": node["id"]}
        if tls:
            ob["tls"] = tls
        if tr:
            ob["transport"] = tr
        return ob
    if proto == "ss":
        return {"type": "shadowsocks", **base,
                "method": node.get("method") or "aes-256-gcm",
                "password": node.get("id") or ""}
    if proto in ("socks", "http"):
        ob = {"type": proto, **base}
        user, pwd = node.get("user", ""), node.get("pass", "")
        if user:
            ob["username"] = user
            ob["password"] = pwd
        return ob
    if proto == "hy2":
        ob = {"type": "hysteria2", **base, "password": node.get("id") or ""}
        if node.get("up_mbps"):
            ob["up_mbps"] = node["up_mbps"]
        if node.get("down_mbps"):
            ob["down_mbps"] = node["down_mbps"]
        if node.get("obfs"):
            ob["obfs"] = {"type": "salamander", "password": node.get("obfs_password") or ""}
        elif node.get("obfs_password"):
            ob["obfs"] = {"type": "salamander", "password": node["obfs_password"]}
        tls = {"enabled": True,
               "server_name": node.get("sni") or node.get("host") or node["addr"]}
        if node.get("allow_insecure"):
            tls["insecure"] = True
        ob["tls"] = tls
        return ob
    if proto == "tuic":
        ob = {"type": "tuic", **base,
              "uuid": node.get("id") or "",
              "password": node.get("pass") or "",
              "congestion_control": node.get("congestion_control") or "bbr",
              "udp_relay_mode": node.get("udp_relay_mode") or "native",
              "zero_rtt_handshake": bool(node.get("zero_rtt"))}
        tls = {"enabled": True,
               "server_name": node.get("sni") or node.get("host") or node["addr"]}
        if node.get("allow_insecure"):
            tls["insecure"] = True
        if node.get("alpn"):
            alpn = [a for a in re.split(r"[, ]", node["alpn"]) if a]
            if alpn:
                tls["alpn"] = alpn
        ob["tls"] = tls
        return ob
    if proto == "wireguard":
        peers = node.get("wg_peers") or []
        server, server_port = node.get("addr"), node.get("port")
        peer_pk, allowed = "", "0.0.0.0/0,::/0"
        if peers:
            ep = peers[0].get("endpoint") or ""
            if not server:
                parts = ep.rsplit(":", 1)
                server = parts[0] if parts else ""
                try:
                    server_port = int(parts[1]) if len(parts) == 2 else 0
                except ValueError:
                    server_port = 0
            peer_pk = peers[0].get("public_key", "")
            allowed = peers[0].get("allowed_ips") or allowed
        if not server or not server_port:
            raise ValueError("WireGuard 节点缺少服务器地址/端口")
        return {
            "type": "wireguard",
            "server": server, "server_port": int(server_port),
            "local_address": list(node.get("wg_address") or []),
            "private_key": node.get("wg_private") or node.get("id") or "",
            "peer_public_key": peer_pk,
            "allowed_ips": [a.strip() for a in str(allowed).split(",") if a.strip()],
            "mtu": int(node.get("wg_mtu") or 1420),
        }
    if proto == "anytls":
        # sing-box 的 anytls 出站字段：password + tls（padding 由默认 padding_scheme
        # 承担；写 "padding": true 会因 unknown field 直接 FATAL 启动失败）
        ob = {"type": "anytls", **base, "password": node.get("id") or ""}
        ob["tls"] = {"enabled": True,
                     "server_name": node.get("sni") or node.get("host") or node["addr"],
                     "insecure": bool(node.get("allow_insecure"))}
        return ob
    raise ValueError("该核心不支持节点类型：%s" % proto)


def _drop_none(d):
    for k in [k for k, v in d.items() if v is None]:
        del d[k]


def _singbox_tls(node):
    sec = str(node.get("tls") or "").lower()
    if sec not in ("tls", "1", "reality"):
        return None
    tls = {"enabled": True,
           "server_name": node.get("sni") or node.get("host") or node["addr"],
           "insecure": False}
    if node.get("alpn"):
        alpn = [a for a in re.split(r"[, ]", node.get("alpn", "")) if a]
        if alpn:
            tls["alpn"] = alpn
    fp = node.get("fp") or ""
    if fp and not fp.isdigit():
        tls["utls"] = {"enabled": True, "fingerprint": fp}
    return tls


def _singbox_transport(node):
    net = node.get("network") or "tcp"
    if net == "ws":
        tr = {"type": "ws", "path": node.get("path") or "/",
              "max_early_data": node.get("max_early_data") or 0}
        if node.get("host"):
            tr["headers"] = {"Host": node["host"]}
        return tr
    if net == "grpc":
        return {"type": "grpc",
                "service_name": node.get("path") or node.get("grpc_mode") or "",
                "mode": "gun"}
    if net == "httpupgrade":
        tr = {"type": "httpupgrade", "path": node.get("path") or "/"}
        if node.get("host"):
            tr["host"] = node["host"]
        return tr
    if net == "quic":
        return {"type": "quic", "security": "none"}
    if net == "h2":
        tr = {"type": "http", "path": node.get("path") or "/"}
        if node.get("host"):
            tr["host"] = [node["host"]]
        return tr
    return None


def _migrate_singbox_dns(dns):
    """把旧格式 DNS（v2rayN / sing-box 1.11 风格）迁移到 1.12+ 形式。

    旧：{"servers": ["223.5.5.5", "https://dns.google/dns-query"]}
    新：{"servers": [{"type": "udp", "tag": "srv0", "server": "223.5.5.5"}, ...]}
    1.12 起字符串形式的 servers 会直接 FATAL（cannot unmarshal string into
    option._DNSServerOptions）；rules 里按地址引用服务器的一并换成迁移后的 tag。
    """
    if not isinstance(dns, dict):
        return dns
    servers = dns.get("servers")
    if not isinstance(servers, list):
        return dns
    tag_map = {}
    out = []
    for i, srv in enumerate(servers):
        if isinstance(srv, dict):      # 已是新格式
            out.append(srv)
            continue
        raw = str(srv or "").strip()
        if not raw:
            continue
        tag = "srv%d" % i
        tag_map[raw] = tag
        low = raw.lower()
        if low in ("local", "localhost"):
            out.append({"type": "local", "tag": tag})
        elif low.startswith("https://"):
            host, _, path = raw[8:].partition("/")
            item = {"type": "https", "tag": tag, "server": host}
            if path:
                item["path"] = "/" + path
            out.append(item)
        elif low.startswith("tls://"):
            out.append({"type": "tls", "tag": tag, "server": raw[6:]})
        elif low.startswith("quic://"):
            out.append({"type": "quic", "tag": tag, "server": raw[7:]})
        elif low.startswith(("udp://", "tcp://")):
            out.append({"type": low[:3], "tag": tag, "server": raw[6:]})
        else:
            out.append({"type": "udp", "tag": tag, "server": raw})
    # 地址写成域名的服务器（如 https://dns.google/dns-query）需要引导解析器：
    # 1.12+ 不指定 domain_resolver 会 FATAL（missing domain resolver for domain
    # server address）。优先复用列表里 IP 形式/本地解析器，都没有则补 223.5.5.5。
    def _is_ip(host):
        try:
            ipaddress.ip_address(str(host).strip("[]"))
            return True
        except ValueError:
            return False

    bootstrap = None
    for item in out:
        addr = str(item.get("server") or "")
        if item.get("type") == "local" or (addr and _is_ip(addr)):
            bootstrap = item.get("tag")
            break
    needs = [it for it in out
             if it.get("type") in ("https", "tls", "quic", "h3")
             and it.get("server") and not _is_ip(it["server"])]
    if needs and not bootstrap:
        out.append({"type": "udp", "tag": "bootstrap", "server": "223.5.5.5"})
        bootstrap = "bootstrap"
    for item in needs:
        item.setdefault("domain_resolver", bootstrap)

    migrated = dict(dns)
    migrated["servers"] = out
    rules = migrated.get("rules")
    if isinstance(rules, list):
        new_rules = []
        for r in rules:
            if not isinstance(r, dict):
                continue
            r = dict(r)
            srv = r.get("server")
            if isinstance(srv, str) and srv in tag_map:
                r["server"] = tag_map[srv]
            new_rules.append(r)
        migrated["rules"] = new_rules
    return migrated


def _merge_advanced(config, advanced, singbox=False):
    """把高级配置（routing/dns 段）合并进生成配置；用户段优先。"""
    if not advanced:
        return config
    if singbox:
        if isinstance(advanced.get("routing"), dict):
            config["route"] = advanced["routing"]
        if isinstance(advanced.get("dns"), dict):
            # 用户 DNS 覆盖在我们的默认（strategy/final）之上，
            # 并保证 final / default_domain_resolver 指向存在的服务器标签，
            # 否则 sing-box 直接 FATAL（resolver/标签不存在）
            merged = dict(_singbox_dns())
            merged.update(_migrate_singbox_dns(advanced["dns"]))
            tags = [srv.get("tag") for srv in (merged.get("servers") or [])
                    if isinstance(srv, dict) and srv.get("tag")]
            if tags:
                if merged.get("final") not in tags:
                    merged["final"] = tags[0]
                route = config.get("route")
                if isinstance(route, dict):
                    res = route.get("default_domain_resolver")
                    if isinstance(res, dict):
                        tagged = set(tags)
                        if res.get("server") not in tagged:
                            res = dict(res)
                            res["server"] = merged["final"]
                            route["default_domain_resolver"] = res
                    elif res not in tags:
                        route["default_domain_resolver"] = merged["final"]
            config["dns"] = merged
    else:
        if isinstance(advanced.get("routing"), dict):
            config["routing"] = advanced["routing"]
        if isinstance(advanced.get("dns"), dict):
            config["dns"] = advanced["dns"]
    return config


def _stream_settings(node):
    sec = "tls" if str(node.get("tls") or "").lower() in ("tls", "1", "reality") else "none"
    tls_cfg = {}
    if sec != "none":
        tls_cfg = {
            "serverName": node.get("sni") or node.get("host") or node["addr"],
            "allowInsecure": False,
        }
        fp = node.get("fp") or ""
        if fp:
            tls_cfg["fingerprint"] = fp
        en = ""
        if node.get("alpn"):
            tls_cfg["alpn"] = [a for a in re.split(r"[, ]", node["alpn"]) if a]
        if node.get("sv") and sec == "reality":
            tls_cfg["show"] = False
    base = {"network": node.get("network") or "tcp", "security": sec}
    if sec != "none":
        base["tlsSettings"] = tls_cfg if node.get("type") in ("vless", "vmess", "trojan") else tls_cfg
    net = node.get("network") or "tcp"
    if net == "ws":
        base["wsSettings"] = {
            "path": node.get("path") or "/",
            "headers": {"Host": node.get("host") or ""} if node.get("host") else {},
        }
    elif net == "grpc":
        base["grpcSettings"] = {"serviceName": node.get("path") or node.get("grpc_mode") or ""}
    elif net == "httpupgrade":
        base["httpupgradeSettings"] = {"path": node.get("path") or "/"}
    elif net == "h2":
        base["httpSettings"] = {"path": node.get("path") or "/", "host": [node.get("host")] if node.get("host") else []}
    elif net == "quic":
        base["quicSettings"] = {"security": "none"}
    return base


# --------------------------------------------------------------------------
# 核心进程托管
# --------------------------------------------------------------------------
class V2rayCoreManager:
    def __init__(self, log_callback=None, store=None):
        self.store = store or NodeStore()
        self.bin_path = ""
        self.http_port = 10809
        self.socks_port = 10808
        self.running = False
        self.healthy = False
        self.proc = None
        self.config_file = ""
        self.current = -1
        self.latency = None
        self.sys_proxy = False
        self.sys_proxy_mode = SYS_PROXY_AUTO
        self.auto_switch = True
        self.core_type = CORE_XRAY
        self.mode = MODE_SMART
        self.tun = False
        self.stats_api_port = 0
        self._monitor = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._log_fh = None          # 核心输出文件句柄（启动时打开，停止时关闭）
        self.clash_port = 0          # Clash API 端口（sing-box；连接/策略组管理）
        self.clash_secret = ""
        self._old_proxy = None
        self._old_pac = None
        self._advanced = None
        self.log_callback = log_callback or (lambda m: None)

    def _log(self, msg):
        try:
            self.log_callback(str(msg))
        except Exception:
            pass

    # -- 核心定位 -----------------------------------------------------------
    def detect_bin(self, configured="", v2rayn_dir=""):
        names = {CORE_XRAY: ("xray.exe",), CORE_V2RAY: ("v2ray.exe",),
                 CORE_SING: ("sing-box.exe",)}[self.core_type]
        for c in _core_bin_candidates(self.core_type, configured, v2rayn_dir):
            if os.path.isfile(c):
                self.bin_path = os.path.abspath(c)
                return self.bin_path
        for name in names:
            p = _which(name)
            if p:
                self.bin_path = p
                return p
        raise ValueError(
            "未找到 %s 核心程序。请点击「自动下载」，或选择 V2rayN 安装目录 / 手动放置到 %s"
            % (self.core_type, os.path.join(DATA_HOME, self.core_type, "bin"))
        )

    def _ensure_core_for_node(self, node):
        """节点协议与当前核心不匹配时自动切换（或给出明确指引）。

        Xray / V2Ray 内核不支持 anytls / hysteria2 / tuic（anytls 官方从未实现），
        这些节点必须用 sing-box。此前默认核心 xray + anytls 节点会直接抛核心的
        英文启动错误，用户完全无从下手。
        """
        proto = str(node.get("type") or "")
        if protocol_supported(proto, self.core_type):
            return
        for cand in protocol_cores(proto):
            if cand == self.core_type:
                continue
            for c in _core_bin_candidates(cand, ""):
                if os.path.isfile(c):
                    self._log("节点为 %s 协议，当前核心 %s 不支持，已自动切换为 %s"
                              % (proto, self.core_type, cand))
                    self.core_type = cand
                    self.bin_path = os.path.abspath(c)
                    return
        need = " / ".join(protocol_cores(proto))
        raise ValueError(
            "节点为 %s 协议，当前核心 %s 不支持该协议（anytls / hysteria2 / tuic 仅 %s 支持）。"
            "请在「运行控制」把核心切换为 %s 并点击「自动下载核心」，然后重新启动。"
            % (proto, self.core_type, need, need)
        )

    def _resolve_nodes(self):
        return self.store.nodes()

    def _node(self, index):
        nodes = self._resolve_nodes()
        if not (0 <= index < len(nodes)):
            raise ValueError("节点索引无效")
        return nodes[index]

    # -- 高级配置 -----------------------------------------------------------
    def load_advanced(self):
        """读取高级配置段（routing/dns），解析失败视为空。"""
        try:
            with open(ADVANCED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._advanced = data if isinstance(data, dict) else None
        except (OSError, ValueError):
            self._advanced = None
        return self._advanced

    # -- 配置与启动 ----------------------------------------------------------
    def _write_config(self, index, http_port, socks_port):
        node = self._node(index)
        nodes = groups = clash_api = None
        if self.core_type == CORE_SING:
            groups = load_groups()
            if groups:
                try:
                    nodes = self._resolve_nodes()
                except Exception:
                    nodes = None
            if self.clash_port:
                clash_api = {"external_controller": "127.0.0.1:%d" % self.clash_port,
                             "secret": self.clash_secret}
        conf = build_core_config(
            node, http_port, socks_port,
            core_type=self.core_type, mode=self.mode, tun=self.tun,
            advanced=self._advanced,
            stats_port=self.stats_api_port if self.core_type == CORE_XRAY else None,
            nodes=nodes, groups=groups, clash_api=clash_api,
        )
        os.makedirs(PROXY_DIR, exist_ok=True)
        path = os.path.join(PROXY_DIR, "config_%d.json" % index)
        with open(path, "w", encoding="utf-8") as f:
            f.write(conf)
        return path

    def start(self, http_port=10809, socks_port=10808, index=None):
        with self._lock:
            if self.running:
                return
            self._start_prepare(http_port, socks_port, index)
        # 探活/系统代理/监控线程在锁外执行：其失败路径会再次加锁（stop_core）
        self._activate()

    def _start_prepare(self, http_port, socks_port, index):
        if not self.bin_path:
            raise ValueError("未设置核心程序路径")
        try:
            self._cleanup_stale_proxy()
        except Exception:
            pass
        http_port = int(http_port)
        socks_port = int(socks_port)
        if not (1 <= http_port <= 65535 and 1 <= socks_port <= 65535):
            raise ValueError("端口无效")
        nodes = self._resolve_nodes()
        if not nodes:
            raise ValueError("节点库为空，请先导入节点或订阅")
        if index is None:
            index = self.store.data.get("selected", 0)
        if not (0 <= index < len(nodes)):
            index = 0
        self.store.set_selected(index)
        self.http_port = http_port
        self.socks_port = socks_port
        # 协议↔核心兼容性（早于 TUN 校验：自动切换后按新核心校验）
        self._ensure_core_for_node(nodes[index])
        if self.core_type == CORE_SING:
            # 分配 Clash API 端口（连接列表 / 策略组运行时切换）
            self.clash_port = int(_free_ports(1)[0])
            self.clash_secret = uuid.uuid4().hex[:16]
        if self.tun and not _is_admin():
            raise ValueError(
                "TUN 模式需要管理员权限：请以管理员身份运行本程序后重新启动代理"
            )
        if self.tun and not os.path.isfile(os.path.join(GEO_DIR, "wintun.dll")):
            raise ValueError(
                "启用 TUN 需要 wintun.dll（缺失）。请点击「自动下载 wintun」补齐后重试"
            )
        # 流量统计走系统级采样（见 app/core/traffic.py），无需核心 api 入站
        self.stats_api_port = 0
        self._start_core(index)

    def _start_core(self, index):
        node = self._node(index)
        self.config_file = self._write_config(index, self.http_port, self.socks_port)
        self._log("核心：%s [%s] | 节点[%d] %s %s:%d" %
                  (os.path.basename(self.bin_path), self.core_type, index,
                   node.get("remark"), node.get("addr"), node.get("port")))
        if self.tun:
            self._log("TUN 模式已启用（需要管理员权限）")
        cmd = [self.bin_path, "run"]
        workdir = os.path.dirname(self.bin_path) or PROXY_DIR
        if self.core_type == CORE_SING:
            # sing-box：-D 指定工作目录（geoip/geosite 与 wintun.dll 所在目录）
            cmd += ["-D", workdir]
        cmd += ["-c", self.config_file]
        env = self._core_env()
        try:
            os.makedirs(workdir, exist_ok=True)
            # 核心输出落盘（每次启动截断）：配置错误只有退出码时无从排查，
            # 失败路径会把末尾几行附在错误信息里给用户看
            try:
                self._log_fh = open(self._core_log_path(), "wb")
            except OSError:
                self._log_fh = None
            self.proc = subprocess.Popen(
                cmd,
                stdout=self._log_fh or subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                cwd=workdir, env=env, creationflags=_NOWIN,
            )
        except Exception as e:
            self.running = False
            raise ValueError("核心启动失败：%s" % e)
        self.current = index
        self.running = True
        self._stop_event.clear()

    def _core_log_path(self):
        return os.path.join(PROXY_DIR, "core.log")

    def _core_log_tail(self, lines=5):
        """核心输出末尾若干行（启动失败时附到错误信息里）。"""
        try:
            with open(self._core_log_path(), "r", encoding="utf-8",
                      errors="replace") as f:
                tail = [x.strip() for x in f.readlines()[-lines:]]
            return " | ".join(x for x in tail if x)[:600]
        except OSError:
            return ""

    # -- Clash API（sing-box）：连接列表 / 策略组运行时切换 ---------------------
    def _wait_clash_ready(self, timeout=3.0):
        """等待 Clash API 端口就绪（避免启动瞬间查询被拒）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.clash_port), timeout=0.4):
                    return True
            except OSError:
                time.sleep(0.2)
        self._log("警告：Clash API（端口 %d）未就绪，连接/策略组功能暂不可用" % self.clash_port)
        return False

    def clash_request(self, method, path, body=None, timeout=6, retries=2):
        """调用核心的 Clash API。仅 sing-box 且在运行中可用。

        连接类错误自动重试（启动瞬间端口可能尚未监听）。
        """
        if self.core_type != CORE_SING or not self.clash_port:
            raise ValueError("当前核心不支持连接/策略组管理（需 sing-box 核心运行中）")
        url = "http://127.0.0.1:%d%s" % (self.clash_port, path)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, method=str(method), data=data, headers={
            "Authorization": "Bearer %s" % self.clash_secret,
            "Content-Type": "application/json",
        })
        last = None
        for attempt in range(max(1, retries + 1)):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                return json.loads(raw) if raw else {}
            except urllib.error.HTTPError:
                raise
            except Exception as e:      # 连接被拒/超时：稍候重试
                last = e
                time.sleep(0.35)
        raise ValueError("Clash API 请求失败：%s" % last)

    def groups_state(self):
        """策略组状态：定义 + 成员（节点索引/备注/延迟）+ 当前选择。"""
        groups = load_groups()
        nodes = self._resolve_nodes()
        proxies = {}
        if self.running and self.clash_port:
            try:
                proxies = (self.clash_request("GET", "/proxies") or {}).get("proxies") or {}
            except Exception:
                proxies = {}
        out = []
        for g in groups:
            idxs = group_members(g, nodes)
            info = proxies.get(g["name"]) or {}
            out.append({
                "name": g["name"], "type": g["type"], "filter": g["filter"],
                "interval": g["interval"], "now": info.get("now"),
                "members": [{
                    "index": i, "tag": "node-%d" % i,
                    "remark": nodes[i].get("remark") or nodes[i].get("addr"),
                    "latency": nodes[i].get("latency"),
                } for i in idxs],
            })
        return {"enabled": bool(groups), "groups": out,
                "clash": bool(self.running and self.clash_port),
                "singbox": self.core_type == CORE_SING, "running": self.running}

    def group_select(self, name, member_tag):
        """运行时切换策略组选择（Clash API，无需重启核心；仅 selector 组可切）。"""
        if not (self.running and self.clash_port):
            raise ValueError("代理未运行，无法切换策略组")
        self.clash_request("PUT", "/proxies/%s" % quote(str(name), safe=""),
                           {"name": str(member_tag)})
        return True

    def connections(self, limit=200):
        """活动连接列表（Clash API）。"""
        data = self.clash_request("GET", "/connections")
        conns = []
        for c in (data.get("connections") or [])[:int(limit)]:
            meta = c.get("metadata") or {}
            conns.append({
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
        return {"connections": conns,
                "upload_total": int(data.get("uploadTotal") or 0),
                "download_total": int(data.get("downloadTotal") or 0),
                "memory": int(data.get("memory") or 0)}

    def close_connection(self, conn_id):
        """关闭单条连接。"""
        self.clash_request("DELETE", "/connections/%s" % quote(str(conn_id), safe=""))
        return True

    def close_all_connections(self):
        """关闭全部连接。"""
        self.clash_request("DELETE", "/connections")
        return True

    def _probe_latency(self, timeout=4.0):
        """经本地 http 代理探测真实外网连通性，返回毫秒延迟；失败返回 None。"""
        return _probe_http_port(self.http_port, timeout)

    # -- 系统代理 -----------------------------------------------------------
    def set_sys_proxy(self, on):
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
            if on:
                if self._old_proxy is None:
                    def _get(name_, def_):
                        try:
                            v, _ = winreg.QueryValueEx(k, name_)
                            return v
                        except OSError:
                            return def_

                    self._old_proxy = {
                        "enable": _get("ProxyEnable", 0),
                        "server": _get("ProxyServer", ""),
                        "override": _get("ProxyOverride", ""),
                    }
                try:
                    winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                except OSError:
                    pass
                winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, "127.0.0.1:%d" % self.http_port)
            else:
                if self._old_proxy:
                    winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, self._old_proxy["enable"])
                    winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, self._old_proxy["server"])
                    winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, self._old_proxy["override"])
                    self._old_proxy = None
        self.sys_proxy = on

    # -- 系统代理四策略（对齐 v2rayN：清除 / 自动配置 / 不改变 / PAC） ------
    def _write_pac(self, port):
        """生成 PAC 脚本：本机/局域网直连，其余走核心（wiki 系统代理说明）。"""
        os.makedirs(PROXY_DIR, exist_ok=True)
        path = os.path.join(PROXY_DIR, "pac.js")
        content = (
            "function FindProxyForURL(url, host) {\n"
            "  if (isPlainHostName(host) || host === 'localhost' ||\n"
            "      /^(10\\.|127\\.|192\\.168\\.|169\\.254\\.|"
            "172\\.(1[6-9]|2\\d|3[01])\\.)/.test(host) ||\n"
            "      dnsDomainIs(host, '.local') || dnsDomainIs(host, '.lan')) {\n"
            "    return 'DIRECT';\n"
            "  }\n"
            "  return 'PROXY 127.0.0.1:%d';\n"
            "}\n"
        ) % int(port)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def set_sys_proxy_pac(self, on):
        """PAC 策略：写/还原 Windows AutoConfigURL（file:// 本地脚本）。"""
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as k:
            def _q(name_, def_):
                try:
                    v, _ = winreg.QueryValueEx(k, name_)
                    return v
                except OSError:
                    return def_
            if on:
                if self._old_pac is None:
                    self._old_pac = {
                        "enable": _q("ProxyEnable", 0),
                        "autoconfig": _q("AutoConfigURL", ""),
                    }
                pac = self._write_pac(self.http_port)
                try:
                    winreg.SetValueEx(k, "AutoConfigURL", 0, winreg.REG_SZ,
                                      "file:///" + pac.replace("\\", "/"))
                except OSError:
                    pass
                try:
                    winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                except OSError:
                    pass
            else:
                if self._old_pac:
                    if self._old_pac["autoconfig"]:
                        try:
                            winreg.SetValueEx(k, "AutoConfigURL", 0, winreg.REG_SZ,
                                              self._old_pac["autoconfig"])
                        except OSError:
                            pass
                    else:
                        try:
                            winreg.DeleteValue(k, "AutoConfigURL")
                        except OSError:
                            pass
                    try:
                        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD,
                                          self._old_pac["enable"])
                    except OSError:
                        pass
                    self._old_pac = None
        self.sys_proxy = on

    def _clear_sys_proxy(self):
        """清除系统代理（对齐 v2rayN「清除系统代理」策略）。"""
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as k:
            try:
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            except OSError:
                pass
            try:
                winreg.DeleteValue(k, "AutoConfigURL")
            except OSError:
                pass
        self.sys_proxy = False

    def _apply_sysproxy_start(self):
        """启动核心后按当前策略设置系统代理。"""
        mode = self.sys_proxy_mode
        if mode == SYS_PROXY_NONE:
            self.sys_proxy = False
            self._log("系统代理策略：不改变（保留其他软件设定）")
            return
        if mode == SYS_PROXY_CLEAR:
            try:
                self._clear_sys_proxy()
                self._log("系统代理策略：清除（已强制清除，本机端口需手动配置）")
            except Exception as e:
                self._log("清除系统代理失败：%s" % e)
            return
        if mode == SYS_PROXY_PAC:
            try:
                self.set_sys_proxy_pac(True)
                self._log("系统代理策略：PAC（本地网络直连，其余走核心）")
            except Exception as e:
                self._log("设置 PAC 系统代理失败：%s（不影响手动代理使用）" % e)
            return
        try:
            self.set_sys_proxy(True)
        except Exception as e:
            self._log("设置系统代理失败：%s（不影响手动代理使用）" % e)

    def _apply_sysproxy_stop(self):
        """停止核心后按当前策略还原系统代理。"""
        mode = self.sys_proxy_mode
        try:
            if mode == SYS_PROXY_NONE or mode == SYS_PROXY_CLEAR:
                return
            if mode == SYS_PROXY_PAC:
                if self.sys_proxy or self._old_pac:
                    self.set_sys_proxy_pac(False)
                return
            if self.sys_proxy or self._old_proxy:
                self.set_sys_proxy(False)
        except Exception as e:
            self._log("还原系统代理失败：%s" % e)

    def set_sys_proxy_mode(self, mode):
        """切换系统代理策略；运行中时立即重设。"""
        if mode not in SYS_PROXY_MODES:
            raise ValueError("不支持的系统代理策略：%s" % mode)
        self.sys_proxy_mode = mode
        if self.running:
            for fn in (self.set_sys_proxy, self.set_sys_proxy_pac):
                try:
                    if self._old_proxy or self._old_pac:
                        fn(False)
                except Exception:
                    pass
            self._apply_sysproxy_start()
        return mode

    def _cleanup_stale_proxy(self):
        """启动时清理上次异常退出残留的本地代理设置。"""
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
                try:
                    enable, _ = winreg.QueryValueEx(k, "ProxyEnable")
                    server, _ = winreg.QueryValueEx(k, "ProxyServer")
                except OSError:
                    enable, server = 0, ""
                if enable == 1 and str(server).startswith("127.0.0.1:"):
                    winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
                    self._log("已清理残留的系统代理设置（%s）" % server)
        except OSError:
            pass

    # -- 监控与自动切换 -------------------------------------------------------
    def _switch(self):
        """依次尝试下一个节点并重启核心；全部失败返回 False。

        注意用局部步进（而非改写 self.current）：失败也要继续往后试，
        否则每轮都取 (current+1) 原地重试同一节点，自动切换形同虚设。
        """
        nodes = self._resolve_nodes()
        count = len(nodes)
        if count <= 1:
            return False
        for step in range(1, count):
            nxt = (self.current + step) % count
            if is_info_node(nodes[nxt]):
                continue
            try:
                self._start_core(nxt)
                self._log("已自动切换至节点[%d] %s" % (nxt, nodes[nxt].get("remark")))
                return True
            except Exception as e:
                self._log("切换节点失败：%s" % e)
        return False

    def _monitor_loop(self):
        fails = 0
        switch_rounds = 0
        while not self._stop_event.wait(5.0):
            if not self.running or self.proc is None:
                return
            if self.proc.poll() is not None:
                with self._lock:
                    self.running = False
                self._log("核心进程已退出。")
                self._apply_sysproxy_stop()
                return
            lat = self._probe_latency()
            if lat is not None:
                self.latency = lat
                self.healthy = True
                fails = 0
                switch_rounds = 0
                continue
            fails += 1
            self.latency = None
            self.healthy = False
            if fails >= 2:
                self._log("节点[%d] 连续 %d 次探测失败" % (self.current, fails))
                try:
                    count = len(self._resolve_nodes())
                except Exception:
                    count = 0
                switch_rounds += 1
                # 上限：把系统代理指向一个连不通的出口时不能无限轮换下去
                if switch_rounds > max(1, min(count, 16)):
                    self._log("已尝试多个节点均不可用，停止代理并还原系统设置。")
                    try:
                        self.stop()
                    except Exception:
                        pass
                    return
                if self.auto_switch and self._switch():
                    fails = 0
                    continue
                self._log("全部节点不可用，停止代理并还原系统设置。")
                try:
                    self.stop()
                except Exception:
                    pass
                return

    def _activate(self):
        """启动核心 + 探活 + 设置系统代理 + 监控线程。"""
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                code = self.proc.returncode
                tail = self._core_log_tail()
                self.stop_core()
                raise ValueError(
                    "核心进程启动后立即退出（退出码 %s）%s"
                    % (code, ("：" + tail) if tail else "")
                )
            if self._probe_latency() is not None:
                break
            time.sleep(0.5)
        else:
            node = {}
            try:
                nodes = self._resolve_nodes()
                if 0 <= self.current < len(nodes):
                    node = nodes[self.current]
            except Exception:
                pass
            self.stop_core()
            raise ValueError(
                "核心已启动，但节点[%d]「%s」在 20 秒内未通过连通性探测："
                "该节点可能不可用或线路受限。请在节点列表点「测试」确认后改用可用节点重试。"
                % (self.current, str(node.get("remark") or node.get("addr") or "?"))
            )
        self.latency = self._probe_latency() or 0
        self.healthy = True
        if self.core_type == CORE_SING and self.clash_port:
            # Clash API 就绪等待：其监听比代理端口略晚（实测竞态会导致
            # 启动后立刻查连接/策略组被拒），最多等 3 秒
            self._wait_clash_ready()
        self._apply_sysproxy_start()
        self._stop_event.clear()
        self._monitor = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor.start()

    def stop_core(self):
        with self._lock:
            proc, self.proc = self.proc, None
            self.running = False
            self.healthy = False
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
        fh, self._log_fh = self._log_fh, None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    def stop(self):
        try:
            self._apply_sysproxy_stop()
            self._stop_event.set()
            self.stop_core()
            self.current = -1
            self.latency = None
            self._log("代理已停止，系统设置已还原。")
        except Exception as e:
            self._log("停止代理时出错：%s" % e)

    # -- 状态 ---------------------------------------------------------------
    def state(self):
        nodes = self._resolve_nodes()
        cur = None
        if 0 <= self.current < len(nodes):
            n = nodes[self.current]
            cur = {"index": self.current, "remark": n.get("remark"), "addr": n.get("addr"),
                   "port": n.get("port"), "type": n.get("type")}
        return {
            "running": self.running,
            "healthy": self.healthy and self.running,
            "proc_alive": bool(self.proc and self.proc.poll() is None),
            "core_bin": self.bin_path or "",
            "core_ok": bool(self.bin_path and os.path.isfile(self.bin_path)),
            "core_type": self.core_type,
            "mode": self.mode,
            "tun": self.tun,
            "http_port": self.http_port,
            "socks_port": self.socks_port,
            "sys_proxy": self.sys_proxy,
            "sys_proxy_mode": self.sys_proxy_mode,
            "node_count": len(nodes),
            "selected": self.store.data.get("selected", 0),
            "current": cur,
            "latency": self.latency,
            "auto_switch": self.auto_switch,
            "sub_url": self.store.data.get("sub_url", ""),
            "stats_supported": True,  # 流量统计由 traffic.py 系统级采样提供，核心无关
            "geo_ok": _geo_available(),
            "geo_sing_ok": _geo_sing_available(),
            "admin": _is_admin(),
            "advanced": self._advanced,
        }

    # -- 手动操作 ----------------------------------------------------------
    def select(self, index):
        """手动切到指定节点（重启核心）。"""
        if not self.running:
            self.store.set_selected(index)
            return {"ok": True, "note": "not-running"}
        node = self._node(index)
        try:
            self._start_core(index)
        except Exception as e:
            return {"ok": False, "err": str(e)}
        # 快速探活，失败立即回退上一节点
        deadline = time.time() + 12
        while time.time() < deadline:
            if self.proc.poll() is not None:
                break
            if self._probe_latency() is not None:
                self.store.set_selected(index)
                self._log("已切换到节点[%d] %s" % (index, node.get("remark")))
                return {"ok": True, "data": {"index": index}}
            time.sleep(0.5)
        self._log("节点[%d] 不可达，保持当前节点。" % index)
        self._start_core(self.current)
        return {"ok": False, "err": "节点不可达，已回退到原节点"}

    def _test_cmd(self, cfg, tmp_http, tmp_socks):
        """按核心类型组装 test/测速命令。"""
        workdir = self._core_workdir()
        cmd = [self.bin_path, "run"]
        if self.core_type == CORE_SING:
            cmd += ["-D", workdir]
        cmd += ["-c", cfg]
        return cmd

    def _core_workdir(self):
        return os.path.dirname(self.bin_path) or PROXY_DIR

    def _core_env(self):
        """核心子进程环境：xray / v2ray 需用环境变量指向规则库目录。

        规则库在 GEO_DIR（proxy/bin），核心默认只在自己可执行文件目录找
        geoip.dat/geosite.dat，不指过去会 "failed to open file: geosite.dat"
        启动失败——启动、单节点测试、批量测速三条路径都必须带上。
        """
        if self.core_type in (CORE_XRAY, CORE_V2RAY) and os.path.isdir(GEO_DIR):
            key = ("XRAY_LOCATION_ASSET" if self.core_type == CORE_XRAY
                   else "V2RAY_LOCATION_ASSET")
            env = dict(os.environ)
            env[key] = GEO_DIR
            return env
        return None

    def test_node(self, index):
        """单节点连通性测试（毫秒），不改变运行状态。"""
        node = self._node(index)
        if not self.bin_path:
            raise ValueError("未设置核心程序路径")
        tmp_http, tmp_socks = _free_ports(2)
        conf = build_core_config(node, tmp_http, tmp_socks, core_type=self.core_type)
        cfg = os.path.join(PROXY_DIR, "test-%d.json" % os.getpid())
        os.makedirs(PROXY_DIR, exist_ok=True)
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(conf)
        proc = None
        try:
            proc = subprocess.Popen(
                self._test_cmd(cfg, tmp_http, tmp_socks),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=PROXY_DIR, creationflags=_NOWIN, env=self._core_env(),
            )
            t0 = time.monotonic()
            deadline = time.time() + 12
            while time.time() < deadline:
                if proc.poll() is not None:
                    return {"ok": False,
                            "err": "核心启动失败（退出码 %s）" % proc.returncode}
                lat = _probe_http_port(tmp_http)
                if lat is not None:
                    return {"ok": True, "latency": lat}
                time.sleep(0.4)
            return {"ok": False, "err": "节点在 12 秒内未通过连通性测试"}
        finally:
            try:
                if proc and proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=3)
            except Exception:
                pass
            try:
                os.remove(cfg)
            except OSError:
                pass

    def burst_test(self, indexes=None, timeout=12):
        """批量测速：并发测试多个节点延迟并回写 store.latency。返回结果列表。"""
        nodes = self._resolve_nodes()
        if indexes is None:
            indexes = list(range(len(nodes)))
        indexes = [i for i in indexes if 0 <= i < len(nodes)]
        results = []
        limit = 3  # 并发上限，避免瞬时拉起过多核心
        threads = []

        def work(i):
            try:
                r = self.test_node(i)
                if r.get("ok"):
                    nodes[i]["latency"] = r.get("latency")
                    results.append({"index": i, "remark": nodes[i].get("remark"),
                                    "ok": True, "latency": r.get("latency")})
                    self._log("测速 节点[%d] %s = %dms" %
                              (i, nodes[i].get("remark"), r.get("latency")))
                else:
                    nodes[i]["latency"] = None
                    results.append({"index": i, "remark": nodes[i].get("remark"),
                                    "ok": False, "err": r.get("err")})
                    self._log("测速 节点[%d] %s 失败：%s" %
                              (i, nodes[i].get("remark"), r.get("err")))
            except Exception as e:
                results.append({"index": i, "remark": _safe_remark(nodes, i),
                                "ok": False, "err": str(e)})

        for i in indexes:
            while len([t for t in threads if t.is_alive()]) >= limit:
                time.sleep(0.2)
            t = threading.Thread(target=work, args=(i,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout + 4)

        def _key(r):
            return (0 if r.get("ok") else 1, r.get("latency") or 10 ** 9)
        results.sort(key=_key)
        self.store.save()
        return results

    # -- 关闭 ---------------------------------------------------------------
    def shutdown(self):
        self.stop()


def _safe_remark(nodes, i):
    try:
        return nodes[i].get("remark")
    except Exception:
        return ""


def _is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _free_ports(n):
    """获取 n 个空闲本地端口。"""
    socks = []
    sockets = []
    try:
        for _ in range(n):
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            socks.append(s)
            sockets.append(s.getsockname()[1])
    finally:
        for s_ in socks:
            s_.close()
    return sockets


def _probe_http_port(port, timeout=4.0):
    """经本地 http 代理端口探测外网连通性，返回毫秒延迟；失败返回 None。

    两个必须点（都曾导致「代理已死却探测成功」的误判）：
    - https 代理也要显式给出：urllib 对 https URL 找不到代理会**直连**，
      于是本地代理没起来也能返回 204；
    - 忽略 no_proxy/NO_PROXY：命中绕过规则时同样会直连探测。
    """
    t0 = time.monotonic()
    proxy = "http://127.0.0.1:%d" % port
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    except Exception:
        return None
    saved_bypass = urllib.request.proxy_bypass
    urllib.request.proxy_bypass = lambda host: False
    try:
        for url in PROBE_URLS:
            try:
                with opener.open(
                    urllib.request.Request(url, headers={"User-Agent": "lan/3.0"}),
                    timeout=timeout,
                ) as resp:
                    if resp.status != 204:
                        continue
                    return int((time.monotonic() - t0) * 1000)
            except Exception:
                continue
        return None
    finally:
        urllib.request.proxy_bypass = saved_bypass

def _which(name):
    import shutil

    return shutil.which(name) or ""