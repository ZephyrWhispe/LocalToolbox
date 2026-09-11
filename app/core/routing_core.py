"""统一分流规则中心：V2rayN（xray/sing-box/v2ray）与 Clash（mihomo）共用。

中立格式存储于 DATA_HOME/rules/unified.json（唯一真源），保存时编译双写：
- mihomo：规则行数组 → clash rules.json（内核运行中可热重载）；
- xray/sing-box/v2ray：v2rayN 路由数组 → advanced.json routing 段（下次启动生效）。
精选规则集基于 GitHub 开源数据（MetaCubeX/meta-rules-dat，每日构建），按需下载。
"""

import json
import os
import threading
import time

from . import bindl
from . import clash_core as cc
from . import v2ray_core as v2c
from .config import DATA_HOME

RULES_DIR = os.path.join(DATA_HOME, "rules")
UNIFIED_FILE = os.path.join(RULES_DIR, "unified.json")

# 规则类型与策略白名单（行格式与 clash 对齐：TYPE,VALUE[,POLICY]）
RULE_TYPES = ("DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "GEOSITE",
              "IP-CIDR", "IP-CIDR6", "GEOIP", "DST-PORT", "PROCESS-NAME")
POLICIES = ("DIRECT", "PROXY", "REJECT")
_MAX_VALUE = 200      # 单条值上限（整行 ≤300 由 clash save_custom_rules 再截断）
_MAX_REMARK = 60

# GEO 文件引用（mihomo GEOSITE/GEOIP 与 xray geosite:/geoip: 前缀）所依赖的 dat
_GEO_DAT = ("geoip.dat", "geosite.dat")

# 精选规则集（数据源 MetaCubeX/meta-rules-dat，geosite.dat 覆盖全部 v2fly 类别；
# sing-box 需对应类别的 .srs，启用时按需下载）。默认启用前三项，与内置智能分流对齐。
PRESETS = (
    {"key": "ads", "name": "广告拦截", "desc": "广告与跟踪域名（geosite:category-ads-all）",
     "policy": "REJECT", "geosite": ["category-ads-all"], "geoip": []},
    {"key": "cn", "name": "国内直连", "desc": "中国大陆域名与 IP 直连（geosite:cn + geoip:cn）",
     "policy": "DIRECT", "geosite": ["cn"], "geoip": [("cn", False)]},
    {"key": "lan", "name": "局域网直连", "desc": "私有网段直连（geoip:private）",
     "policy": "DIRECT", "geosite": [], "geoip": [("private", True)]},
    {"key": "gfw", "name": "国外网站", "desc": "被干扰域名走代理（geosite:geolocation-!cn）",
     "policy": "PROXY", "geosite": ["geolocation-!cn"], "geoip": []},
    {"key": "telegram", "name": "Telegram", "desc": "TG 域名与 IP 段走代理",
     "policy": "PROXY", "geosite": ["telegram"], "geoip": [("telegram", False)]},
    {"key": "openai", "name": "OpenAI", "desc": "ChatGPT / OpenAI 域名走代理",
     "policy": "PROXY", "geosite": ["openai"], "geoip": []},
    {"key": "netflix", "name": "Netflix", "desc": "奈飞域名走代理",
     "policy": "PROXY", "geosite": ["netflix"], "geoip": []},
    {"key": "apple", "name": "Apple 直连", "desc": "苹果国内可直连域名",
     "policy": "DIRECT", "geosite": ["apple"], "geoip": []},
    {"key": "microsoft", "name": "Microsoft 直连", "desc": "微软国内可直连域名",
     "policy": "DIRECT", "geosite": ["microsoft"], "geoip": []},
    {"key": "youtube", "name": "YouTube", "desc": "油管域名走代理",
     "policy": "PROXY", "geosite": ["youtube"], "geoip": []},
)
DEFAULT_PRESETS = {"ads": True, "cn": True, "lan": True}
PRESET_KEYS = tuple(p["key"] for p in PRESETS)

# 内置 4 件 .srs（历史短名）→ bindl._SRS_URLS 的 kind；其余类别走 download_srs 全名
_BUILTIN_SRS = {"geosite-cn": "geosite-cn", "geosite-category-ads-all": "geosite-ads",
                "geoip-cn": "geoip-cn", "geoip-private": "geoip-private"}

_lock = threading.Lock()


# --------------------------------------------------------------------------
# 统一模型存取
# --------------------------------------------------------------------------
def _default_unified():
    return {"version": 1, "updated_ts": 0, "custom": [],
            "presets": dict(DEFAULT_PRESETS)}


def load_unified():
    data = _default_unified()
    try:
        with open(UNIFIED_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return data
    if not isinstance(saved, dict):
        return data
    custom = _clean_entries(saved.get("custom"))
    presets = data["presets"]
    for k, v in (saved.get("presets") or {}).items():
        if k in PRESET_KEYS:
            presets[k] = bool(v)
    return {"version": 1, "updated_ts": float(saved.get("updated_ts") or 0),
            "custom": custom, "presets": presets}


def save_unified(data):
    os.makedirs(os.path.dirname(UNIFIED_FILE), exist_ok=True)
    clean = {"version": 1, "updated_ts": float(data.get("updated_ts") or 0),
             "custom": _clean_entries(data.get("custom")),
             "presets": {k: bool((data.get("presets") or {}).get(k, DEFAULT_PRESETS.get(k, False)))
                         for k in PRESET_KEYS}}
    tmp = UNIFIED_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    os.replace(tmp, UNIFIED_FILE)
    return clean


def _clean_entries(entries):
    out = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        try:
            out.append(normalize_entry(e))
        except ValueError:
            continue
    return out


def normalize_entry(e):
    """校验/规整单条规则，非法抛 ValueError（中文提示）。"""
    t = str(e.get("type") or "").strip().upper()
    if t not in RULE_TYPES:
        raise ValueError("不支持的规则类型：%s" % (t or "空"))
    value = str(e.get("value") or "").strip()
    if not value:
        raise ValueError("规则值不能为空")
    value = value[:_MAX_VALUE]
    policy = str(e.get("policy") or "PROXY").strip().upper()
    if policy not in POLICIES:
        raise ValueError("不支持的策略：%s（可选 DIRECT / PROXY / REJECT）" % policy)
    return {"type": t, "value": value, "policy": policy,
            "enabled": bool(e.get("enabled", True)),
            "remark": str(e.get("remark") or "").strip()[:_MAX_REMARK]}


def parse_line(line):
    """"TYPE,VALUE[,POLICY[,no-resolve]]" 行 → 条目（与 clash 规则行格式对齐）。"""
    s = str(line or "").strip()
    if not s or s.startswith("#"):
        raise ValueError("空行或注释")
    parts = [p.strip() for p in s.split(",")]
    if len(parts) < 2:
        raise ValueError("规则格式：TYPE,VALUE[,POLICY]")
    policy, no_resolve = "PROXY", False
    if parts[-1].upper() in POLICIES:
        policy = parts[-1].upper()
        parts = parts[:-1]
    elif len(parts) >= 4 and parts[-1].lower() == "no-resolve" \
            and parts[-2].upper() in POLICIES:
        policy = parts[-2].upper()
        no_resolve = True
        parts = parts[:-2]
    elif len(parts) >= 3:
        raise ValueError("规则策略必须是 DIRECT / PROXY / REJECT（或带 no-resolve 后缀）")
    value = ",".join(parts[1:]) + (",no-resolve" if no_resolve else "")
    return normalize_entry({"type": parts[0], "value": value, "policy": policy})


def to_line(entry, primary="PROXY"):
    """条目 → clash 规则行（PROXY 映射为主策略组名）。"""
    e = normalize_entry(entry)
    return "%s,%s,%s" % (e["type"], e["value"], primary if e["policy"] == "PROXY" else e["policy"])


def is_managing():
    """统一规则中心是否已接管（unified.json 存在且有内容：自定义条目或启用了精选集）。"""
    if not has_unified_file():
        return False
    d = load_unified()
    return bool(d["custom"]) or any(d["presets"].values())


def has_unified_file():
    return os.path.isfile(UNIFIED_FILE)


# --------------------------------------------------------------------------
# 就绪探测
# --------------------------------------------------------------------------
def geo_dat_ready():
    """geosite.dat / geoip.dat 是否就绪（xray/v2ray/mihomo 的 GEOSITE/GEOIP 依赖）。"""
    return all(os.path.isfile(os.path.join(v2c.GEO_DIR, n)) for n in _GEO_DAT)


def preset_ready(preset):
    """精选集在三内核的就绪状态 {xray, singbox, mihomo}。

    xray/mihomo 依赖 geosite.dat/geoip.dat；sing-box 逐类别看 .srs。
    """
    dat = geo_dat_ready()
    sing = True
    for cat in preset.get("geosite") or []:
        if not v2c._sing_rule_tag("geosite", cat):
            sing = False
    for item in preset.get("geoip") or []:
        cat = item[0] if isinstance(item, tuple) else item
        if not v2c._sing_rule_tag("geoip", cat):
            sing = False
    return {"xray": bool(dat), "singbox": bool(sing), "mihomo": bool(dat)}


def srs_needed(preset):
    """精选集在 sing-box 侧缺失的 (rtype, category) 列表（供按需下载）。"""
    out = []
    for cat in preset.get("geosite") or []:
        if not v2c._sing_rule_tag("geosite", cat):
            out.append(("geosite", cat))
    for item in preset.get("geoip") or []:
        cat = item[0] if isinstance(item, tuple) else item
        if not v2c._sing_rule_tag("geoip", cat):
            out.append(("geoip", cat))
    return out


# --------------------------------------------------------------------------
# 编译器：统一条目 → 各内核格式
# --------------------------------------------------------------------------
def _enabled_custom(data):
    return [e for e in data.get("custom") or [] if e.get("enabled", True)]


def _enabled_presets(data):
    on = data.get("presets") or {}
    return [p for p in PRESETS if on.get(p["key"])]


def compile_mihomo(data, primary=None, geo_ready=None):
    """统一条目 → mihomo rules 行数组（自定义在前、精选集在后）。

    PROXY 映射为主策略组名（与 build_config 的 primary 判定一致：
    default 组 → 第一个组 → 自动造的 PROXY 组）。geo 未就绪时 GEOSITE/GEOIP
    条目跳过并给 warning（MATCH 兜底保证可用）。
    """
    if primary is None:
        primary = mihomo_primary()
    if geo_ready is None:
        geo_ready = geo_dat_ready()
    lines, warns = [], []
    if not geo_ready:
        warns.append("geosite.dat / geoip.dat 未就绪：GEOSITE/GEOIP 规则已跳过"
                     "（请在「更新维护」下载规则库）")
    for e in _enabled_custom(data):
        if not geo_ready and e["type"] in ("GEOSITE", "GEOIP"):
            warns.append("自定义规则已跳过（geo 未就绪）：%s,%s" % (e["type"], e["value"]))
            continue
        lines.append(to_line(e, primary=primary))
    for p in _enabled_presets(data):
        if not geo_ready:
            warns.append("精选集「%s」已跳过（geo 未就绪）" % p["name"])
            continue
        for cat in p["geosite"]:
            lines.append("GEOSITE,%s,%s" % (cat, p["policy"]))
        for item in p["geoip"]:
            cat, no_resolve = (item if isinstance(item, tuple) else (item, False))
            lines.append("GEOIP,%s,%s%s" % (cat, p["policy"], ",no-resolve" if no_resolve else ""))
    return lines, warns


def compile_v2rayn(data, geo_ready=None):
    """统一条目 → v2rayN 自定义路由规则数组（wiki 格式）。

    每条自定义规则一个条目（保持用户优先级粒度）；精选集每集合并为一个条目。
    REJECT→block、PROXY→proxy、DIRECT→direct（xray 侧再映射 proxy-main）。
    """
    if geo_ready is None:
        geo_ready = geo_dat_ready()
    rules, warns = [], []
    tag_of = {"PROXY": "proxy", "DIRECT": "direct", "REJECT": "block"}

    def _new(outbound):
        return {"outboundTag": tag_of.get(outbound, outbound), "enabled": True}

    for e in _enabled_custom(data):
        t = e["type"]
        if t == "PROCESS-NAME":
            warns.append("xray/v2ray 路由不支持进程规则，已跳过：%s" % e["value"])
            continue
        r = _new(e["policy"])
        if t == "DOMAIN":
            r["domain"] = ["full:" + e["value"].lstrip(".")]
        elif t == "DOMAIN-SUFFIX":
            r["domain"] = ["domain:" + e["value"].lstrip(".")]
        elif t == "DOMAIN-KEYWORD":
            r["domain"] = ["keyword:" + e["value"]]
        elif t == "GEOSITE":
            if not geo_ready:
                warns.append("自定义规则已跳过（geo 未就绪）：GEOSITE,%s" % e["value"])
                continue
            r["domain"] = ["geosite:" + e["value"]]
        elif t in ("IP-CIDR", "IP-CIDR6"):
            r["ip"] = [e["value"].split(",")[0]]
        elif t == "GEOIP":
            if not geo_ready:
                warns.append("自定义规则已跳过（geo 未就绪）：GEOIP,%s" % e["value"])
                continue
            r["ip"] = ["geoip:" + e["value"].split(",")[0]]
        elif t == "DST-PORT":
            r["port"] = e["value"]
        rules.append(r)
    for p in _enabled_presets(data):
        r = _new(p["policy"])
        if p["geosite"]:
            if not geo_ready:
                warns.append("精选集「%s」已跳过（geo 未就绪）" % p["name"])
                continue
            r["domain"] = ["geosite:%s" % c for c in p["geosite"]]
        for item in p["geoip"]:
            cat = item[0] if isinstance(item, tuple) else item
            if not geo_ready:
                warns.append("精选集「%s」已跳过（geo 未就绪）" % p["name"])
                continue
            r.setdefault("ip", []).append("geoip:%s" % cat)
        rules.append(r)
    return rules, warns


def compile_singbox(data, geo_ready=None):
    """统一条目 → sing-box route.rules（复用 v2rayN 数组转换器 + 动态 .srs 映射）。

    引用的 rule_set 标签 = GEO_DIR 实际存在的 .srs；类别缺失时该前缀跳过 + warning
    （绝不生成引用不存在 .srs 的配置——sing-box 会直接 FATAL）。
    """
    v2rules, warns = compile_v2rayn(data, geo_ready=geo_ready)
    rules = v2c._singbox_rules_from_v2rayn(v2rules)
    defined = {rs["tag"] for rs in v2c._singbox_rule_sets()}
    for cat in _missing_srs(data):
        warns.append("sing-box 规则集缺失：%s（在「规则集」页启用后自动下载）" % cat)
    rules = [r for r in rules
             if not r.get("rule_set") or all(t in defined for t in r["rule_set"])]
    return {"rules": rules, "rule_set": sorted(defined)}, warns


def _missing_srs(data):
    """启用内容（自定义 geo 条目 + 精选集）在 sing-box 侧缺失的类别标签。"""
    out = []
    for e in _enabled_custom(data):
        if e["type"] == "GEOSITE" and not v2c._sing_rule_tag("geosite", e["value"].split(",")[0]):
            out.append("geosite-" + e["value"].split(",")[0])
        if e["type"] == "GEOIP" and not v2c._sing_rule_tag("geoip", e["value"].split(",")[0]):
            out.append("geoip-" + e["value"].split(",")[0])
    for p in _enabled_presets(data):
        for kind, cats in (("geosite", p["geosite"]),
                           ("geoip", [i[0] if isinstance(i, tuple) else i for i in p["geoip"]])):
            for cat in cats:
                if not v2c._sing_rule_tag(kind, cat):
                    tag = v2c._SING_GEO_ALIASES.get(kind, {}).get(cat, ("%s-%s" % (kind, cat),))[0]
                    out.append(tag)
    return sorted(set(out))


def mihomo_primary():
    """mihomo 主策略组名（与 build_config 判定一致：default → 第一个 → PROXY）。"""
    groups = cc.load_groups()
    for g in groups:
        if g.get("default"):
            return g["name"]
    return groups[0]["name"] if groups else "PROXY"


def compile_preview(data=None):
    """三内核编译产物（不落盘），供「生效预览」页签。"""
    data = data or load_unified()
    mihomo_lines, warns = compile_mihomo(data)
    v2rules, warns2 = compile_v2rayn(data)
    sing, warns3 = compile_singbox(data)
    return {"mihomo": {"lines": mihomo_lines, "primary": mihomo_primary()},
            "v2rayn": v2rules, "singbox": sing,
            "warnings": warns + warns2 + warns3}


# --------------------------------------------------------------------------
# 应用：双写（mihomo 热重载 / advanced.json 下次启动生效）
# --------------------------------------------------------------------------
def apply_unified(clash_mgr, data=None, emit=None):
    """保存统一规则并应用到两个引擎。

    - mihomo：编译行数组 → save_custom_rules；运行中 reload 热生效（失败降级提示）
    - xray/sing-box：编译 v2rayN 数组 → advanced.json routing 段（仅 list 形式；
      dict=专家模式不动并提示），下次启动核心生效
    返回 {"applied": {"mihomo": "hot"|"next_start", "v2rayn": "next_start"|"skipped_expert"},
          "warnings": [...]}。
    """
    data = save_unified(data or load_unified())
    warns = []
    applied = {"mihomo": "next_start", "v2rayn": "next_start"}

    # -- mihomo ---------------------------------------------------------------
    lines, w1 = compile_mihomo(data)
    warns.extend(w1)
    cc.save_custom_rules(lines)
    if clash_mgr is not None and getattr(clash_mgr, "running", False):
        try:
            clash_mgr.reload()
            applied["mihomo"] = "hot"
        except Exception as e:
            warns.append("Clash 热重载失败（重启 Clash 后生效）：%s" % e)
    if emit:
        emit("routing_log", "mihomo 规则已写入（%d 条）" % len(lines))

    # -- xray / sing-box（advanced.json） -------------------------------------
    v2rules, w2 = compile_v2rayn(data)
    warns.extend(w2)
    adv = {}
    try:
        with open(v2c.ADVANCED_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            adv = loaded
    except (OSError, ValueError):
        adv = {}
    if isinstance(adv.get("routing"), dict):
        applied["v2rayn"] = "skipped_expert"
        warns.append("高级配置使用专家模式（routing 为对象）已接管路由，"
                     "统一规则不写入 V2rayN 侧")
    else:
        os.makedirs(v2c.PROXY_DIR, exist_ok=True)
        if v2rules:
            adv["routing"] = v2rules
        else:
            adv.pop("routing", None)     # 无内容 → 恢复内置智能分流
        tmp = v2c.ADVANCED_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(adv, f, ensure_ascii=False, indent=2)
        os.replace(tmp, v2c.ADVANCED_FILE)
        if emit:
            emit("routing_log", "V2rayN 路由已写入 advanced.json（下次启动核心生效，%d 条）"
                 % len(v2rules))
    return {"applied": applied, "warnings": warns}


# --------------------------------------------------------------------------
# 自动更新（GitHub 开源规则数据，主源失败自动切换备份源）
# --------------------------------------------------------------------------
def _dat_kinds(repo):
    """dat 主源 → bindl kind（geoip/geosite 两个）；备份源 = 另一个仓库。"""
    return ("clash-geoip", "clash-geosite") if repo == "metacubex" else ("geoip", "geosite")


def update_targets(data=None, dat_repo="metacubex"):
    """按启用内容收集需下载的文件清单。

    返回 [{"kind", "type": "dat"|"srs", "label", "args"}]；dat 主源失败时由
    执行方自动切换到另一仓库（备份源）。
    """
    data = data or load_unified()
    primary, _backup = _dat_kinds(dat_repo), _dat_kinds(
        "loyalsoldier" if dat_repo == "metacubex" else "metacubex")
    items = [{"kind": primary[0], "type": "dat", "label": "geoip.dat（%s）" % _repo_label(dat_repo)},
             {"kind": primary[1], "type": "dat", "label": "geosite.dat（%s）" % _repo_label(dat_repo)}]
    seen = set()
    for p in _enabled_presets(data):
        for kind, cats in (("geosite", p["geosite"]),
                           ("geoip", [i[0] if isinstance(i, tuple) else i for i in p["geoip"]])):
            for cat in cats:
                key = "%s-%s" % (kind, cat)
                if key in seen:
                    continue
                seen.add(key)
                if key in _BUILTIN_SRS:
                    items.append({"kind": _BUILTIN_SRS[key], "type": "srs_builtin",
                                  "label": key + ".srs"})
                else:
                    items.append({"kind": key, "type": "srs",
                                  "label": key + ".srs",
                                  "args": (kind, cat)})
    return items


def _repo_label(repo):
    return {"metacubex": "MetaCubeX/meta-rules-dat",
            "loyalsoldier": "Loyalsoldier/v2ray-rules-dat"}.get(repo, repo)


def update_all(progress_cb=None, force=False, dat_repo="metacubex"):
    """更新全部所需规则文件（dat + 启用类别的 .srs）。

    备份源链：dat 主仓库失败自动换另一仓库（MetaCubeX ↔ Loyalsoldier）；
    srs raw 直连/镜像失败自动换 jsdelivr CDN。单文件失败不影响其他。
    返回 {"ok", "results": [...], "errs": [...]}。
    """
    items = update_targets(dat_repo=dat_repo)
    backup = "loyalsoldier" if dat_repo == "metacubex" else "metacubex"
    results, errs = [], []
    for i, it in enumerate(items):
        name = it["label"]
        if progress_cb:
            progress_cb({"name": name, "done": 0, "total": 0,
                         "status": "running", "index": i, "total_items": len(items)})
        try:
            if it["type"] == "dat":
                try:
                    r = bindl.download_binary(it["kind"], dest_dir=v2c.GEO_DIR,
                                              force=force)
                except Exception:
                    # 备份仓库：clash-geoip ↔ geoip、clash-geosite ↔ geosite（同序）
                    bk = {"clash-geoip": "geoip", "geoip": "clash-geoip",
                          "clash-geosite": "geosite", "geosite": "clash-geosite"}[it["kind"]]
                    r = bindl.download_binary(bk, dest_dir=v2c.GEO_DIR, force=force)
            elif it["type"] == "srs_builtin":
                r = bindl.download_ruleset(it["kind"], dest_dir=v2c.GEO_DIR,
                                           force=force)
            else:
                rtype, cat = it["args"]
                r = bindl.download_srs(rtype, cat, dest_dir=v2c.GEO_DIR, force=force)
            results.append({"name": name, "ok": True, "cached": bool(r.get("cached"))})
            if progress_cb:
                progress_cb({"name": name, "done": 1, "total": 1,
                             "status": "done", "index": i, "total_items": len(items)})
        except Exception as e:
            results.append({"name": name, "ok": False, "err": str(e)[:160]})
            errs.append("%s：%s" % (name, str(e)[:120]))
    # dat 落在 GEO_DIR（proxy/bin）；mihomo 需复制到自身目录（ensure_geo 再进数据目录）
    _sync_dat_to_mihomo()
    if results and not errs:
        with _lock:
            d = load_unified()
            d["updated_ts"] = time.time()
            save_unified(d)
    return {"ok": not errs, "results": results, "errs": errs}


def _sync_dat_to_mihomo():
    """把 GEO_DIR 的 dat 复制到 mihomo/bin（clash ensure_geo 的上游目录）。"""
    import shutil
    for name in _GEO_DAT:
        src = os.path.join(v2c.GEO_DIR, name)
        if not os.path.isfile(src):
            continue
        for dst_dir in (cc.MIHOMO_DIR, os.path.join(cc.CLASH_DIR, "bin")):
            try:
                os.makedirs(dst_dir, exist_ok=True)
                dst = os.path.join(dst_dir, name)
                if (not os.path.isfile(dst)
                        or os.path.getsize(dst) != os.path.getsize(src)):
                    shutil.copyfile(src, dst)
            except OSError:
                continue
