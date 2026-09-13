# -*- coding: utf-8 -*-
"""Windows 防火墙管理核心（v5.5 合并端口放行与管理功能）。

技术路线（全部系统原生，无第三方依赖）：
- 读取/枚举/写规则：PowerShell + INetFwPolicy2 COM（HNetCfg.FwPolicy2），
  一次进程内 COM 调用即可取到规则名 / 程序路径 / 端口等全量字段
- 备份/恢复：netsh advfirewall export/restore（微软官方 .wfw 格式）
- 端口放行（v3.2 起）：netsh advfirewall + 提权执行器，供 FTP/HTTP/OpenList 等使用
- 写操作需要管理员令牌；未提权时返回中文错误并指向右下角「提权重启」
功能设计仅参考公开文档与同类软件的功能定位，独立实现，未复制任何第三方代码。
"""

import json
import os
import re
import time

from . import logger as applog
from .config import DATA_HOME
from .platform import is_admin
from .runner import run_powershell, run_powershell_elevated, run_powershell_json

log = applog.get_logger("firewall")

# 本工具创建的规则显示名统一前缀（识别 / 一键清理依据）
RULE_PREFIX = "LocalToolbox"
# 一键拦截规则的显示名前缀（比 RULE_PREFIX 更精确，避免误删高级自定义规则）
BLOCK_PREFIX = "LocalToolbox 拦截"
MAX_BACKUPS = 10

PROFILE_NAMES = {1: "domain", 2: "private", 4: "public"}
# INetFwRule.Protocol 常量（NET_FW_IP_PROTOCOL_*）
PROTO_NAMES = {1: "ICMPv4", 6: "TCP", 17: "UDP", 58: "ICMPv6", 256: "Any"}
PROTO_IDS = {v: k for k, v in PROTO_NAMES.items()}
DIRECTION_NAMES = {1: "in", 2: "out"}
ACTION_NAMES = {0: "block", 1: "allow"}

BACKUP_DIR = os.path.join(DATA_HOME, "firewall_backups")
AUDIT_PATH = os.path.join(DATA_HOME, "firewall_audit.jsonl")


def _esc(s):
    """PowerShell 单引号字符串转义。"""
    return str(s or "").replace("'", "''")


def _as_bool(v):
    return v is True or v == 1


def _as_int(v, default=-1):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def audit_add(action, detail=""):
    """追加一条操作记录（JSONL，失败静默——审计不阻塞主操作）。"""
    try:
        os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), "action": action,
                                "detail": str(detail or "")}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def audit_list(limit=100):
    try:
        with open(AUDIT_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out


def _require_admin():
    """未提权时返回中文错误（文案统一指向右下角「提权重启」），否则 None。"""
    if not is_admin():
        return {"ok": False,
                "err": "此操作需要管理员权限：请先点状态栏右下角「提权重启」"}
    return None


# ---------------------------------------------------------------- 枚举
_ENUM_SCRIPT = """
$ErrorActionPreference='Stop'
$pol = New-Object -ComObject HNetCfg.FwPolicy2
$prof = @(1,2,4) | ForEach-Object {
  $p = $_
  [pscustomobject]@{ id=$p; enabled=($pol.FirewallEnabled($p) -ne 0);
    inbound=$pol.DefaultInboundAction($p); outbound=$pol.DefaultOutboundAction($p) }
}
$rules = @($pol.Rules | ForEach-Object {
  [pscustomobject]@{ name=$_.Name; app=$_.ApplicationName; svc=$_.ServiceName;
    en=$_.Enabled; dir=$_.Direction; act=$_.Action; proto=$_.Protocol;
    lp=$_.LocalPorts; rp=$_.RemotePorts; ra=$_.RemoteAddresses; prof=$_.Profiles }
})
[pscustomobject]@{ profiles=$prof; rules=$rules } | ConvertTo-Json -Compress -Depth 3
"""


def _normalize_profiles(raw):
    out = []
    for p in raw or []:
        pid = _as_int(p.get("id"), 0)
        if pid not in PROFILE_NAMES:
            continue
        out.append({
            "id": pid,
            "name": PROFILE_NAMES[pid],
            "enabled": _as_bool(p.get("enabled")),
            "inbound": ACTION_NAMES.get(_as_int(p.get("inbound"), -1), "allow"),
            "outbound": ACTION_NAMES.get(_as_int(p.get("outbound"), -1), "allow"),
        })
    return out


def _normalize_profiles_mask(mask):
    mask = _as_int(mask, 0)
    if mask <= 0:
        return sorted(PROFILE_NAMES.values())
    return [PROFILE_NAMES[b] for b in (1, 2, 4) if mask & b]


def _normalize_rules(raw):
    out = []
    for r in raw or []:
        proto = _as_int(r.get("proto"), -1)
        out.append({
            "name": str(r.get("name") or ""),
            "app": str(r.get("app") or ""),
            "svc": str(r.get("svc") or ""),
            "enabled": _as_bool(r.get("en")),
            "dir": DIRECTION_NAMES.get(_as_int(r.get("dir"), -1), "in"),
            "action": ACTION_NAMES.get(_as_int(r.get("act"), -1), "allow"),
            "proto": PROTO_NAMES.get(proto, str(proto) if proto >= 0 else "Any"),
            "lports": str(r.get("lp") or ""),
            "rports": str(r.get("rp") or ""),
            "raddrs": str(r.get("ra") or ""),
            "profiles": _normalize_profiles_mask(r.get("prof")),
            "ours": str(r.get("name") or "").startswith(RULE_PREFIX),
        })
    return out


def enum_state():
    """全量枚举（无需管理员）。返回 {profiles, rules, admin}；失败抛异常。"""
    raw = run_powershell_json(_ENUM_SCRIPT, timeout=60)
    if not isinstance(raw, dict):
        raise OSError("读取防火墙状态失败（PowerShell 返回为空）")
    return {
        "admin": is_admin(),
        "profiles": _normalize_profiles(raw.get("profiles")),
        "rules": _normalize_rules(raw.get("rules")),
    }


def is_ours(name):
    return str(name or "").startswith(RULE_PREFIX)


# ---------------------------------------------------------------- 写操作
def _ps_write(script, timeout=60):
    """执行写脚本：$ErrorActionPreference=Stop 下失败时 powershell 退出码非 0。"""
    result = run_powershell(script, timeout=timeout)
    if not result.ok:
        raise OSError((result.output or "").strip()[:200] or "防火墙写入失败")
    return True


def _write_ok(script, timeout=60):
    """_ps_write 的结果版：成功返回 None，失败返回 {"ok": False, "err": ...}。"""
    try:
        _ps_write(script, timeout=timeout)
        return None
    except OSError as e:
        return {"ok": False, "err": str(e)}


def set_profile_enabled(profile_id, enabled):
    err = _require_admin()
    if err:
        return err
    pid = _as_int(profile_id, 0)
    if pid not in PROFILE_NAMES:
        return {"ok": False, "err": "配置文件标识无效（应为 1/2/4）"}
    val = "1" if enabled else "0"
    r = _write_ok(
        "$ErrorActionPreference='Stop'\n"
        "$pol = New-Object -ComObject HNetCfg.FwPolicy2\n"
        "$pol.FirewallEnabled(%d) = %s\n" % (pid, val))
    if r:
        return r
    audit_add("profile", "%s %s" % (PROFILE_NAMES[pid], "启用" if enabled else "关闭"))
    return {"ok": True}


def set_default_action(profile_id, direction, action):
    """direction: in/out；action: allow/block。"""
    err = _require_admin()
    if err:
        return err
    pid = _as_int(profile_id, 0)
    if pid not in PROFILE_NAMES:
        return {"ok": False, "err": "配置文件标识无效（应为 1/2/4）"}
    prop = "DefaultInboundAction" if direction == "in" else \
        "DefaultOutboundAction" if direction == "out" else None
    if not prop:
        return {"ok": False, "err": "方向无效（in/out）"}
    act_id = {"allow": 1, "block": 0}.get(action)
    if act_id is None:
        return {"ok": False, "err": "默认策略无效（allow/block）"}
    r = _write_ok(
        "$ErrorActionPreference='Stop'\n"
        "$pol = New-Object -ComObject HNetCfg.FwPolicy2\n"
        "$pol.%s(%d) = %d\n" % (prop, pid, act_id))
    if r:
        return r
    audit_add("default", "%s %s→%s" % (PROFILE_NAMES[pid], direction, action))
    return {"ok": True}


def toggle_rule(name, enabled):
    err = _require_admin()
    if err:
        return err
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "err": "规则名不能为空"}
    r = _write_ok(
        "$ErrorActionPreference='Stop'\n"
        "$pol = New-Object -ComObject HNetCfg.FwPolicy2\n"
        "$r = $pol.Rules.Item('%s')\n"
        "$r.Enabled = %d\n" % (_esc(name), 1 if enabled else 0))
    if r:
        return r
    audit_add("toggle", "%s → %s" % (name, "启用" if enabled else "停用"))
    return {"ok": True}


def delete_rule(name):
    err = _require_admin()
    if err:
        return err
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "err": "规则名不能为空"}
    r = _write_ok(
        "$ErrorActionPreference='Stop'\n"
        "$pol = New-Object -ComObject HNetCfg.FwPolicy2\n"
        "$null = $pol.Rules.Item('%s')\n"
        "$pol.Rules.Remove('%s')\n" % (_esc(name), _esc(name)))
    if r:
        return r
    audit_add("delete", name)
    return {"ok": True}


# ---------------------------------------------------------------- 一键拦截
def blocked_rules(rules):
    """从枚举结果中筛出本工具创建的「一键拦截」规则（按 BLOCK_PREFIX 精确匹配）。"""
    return [r for r in rules or [] if str(r.get("name") or "").startswith(BLOCK_PREFIX)]


def _exe_display(path):
    return os.path.splitext(os.path.basename(path))[0]


def block_app(program):
    """一键禁止程序联网：创建 入+出 两条 Block 规则（需管理员）。"""
    err = _require_admin()
    if err:
        return err
    program = str(program or "").strip()
    if not program or not os.path.isfile(program):
        return {"ok": False, "err": "程序文件不存在：%s" % (program or "（空）")}
    # 已拦截检查（同程序的入+出 Block 规则已存在时不重复创建）
    try:
        st = enum_state()
    except OSError as e:
        return {"ok": False, "err": str(e)}
    already = [r["name"] for r in blocked_rules(st["rules"])
               if r["app"].lower() == program.lower()]
    if len(already) >= 2:
        return {"ok": True, "data": {"already": True, "names": already}}
    disp_in = "%s 拦截 %s（入站）" % (RULE_PREFIX, _exe_display(program))
    disp_out = "%s 拦截 %s（出站）" % (RULE_PREFIX, _exe_display(program))
    script = (
        "$ErrorActionPreference='Stop'\n"
        "$pol = New-Object -ComObject HNetCfg.FwPolicy2\n"
        "foreach ($d in @(1,2)) {\n"
        "  $r = New-Object -ComObject HNetCfg.FWRule\n"
        "  if ($d -eq 1) { $r.Name = '%s' } else { $r.Name = '%s' }\n"
        "  $r.Description = '由 LocalToolbox 创建的联网拦截规则'\n"
        "  $r.ApplicationName = '%s'\n"
        "  $r.Direction = $d\n"
        "  $r.Action = 0\n"
        "  $r.Enabled = 1\n"
        "  $r.Protocol = 256\n"
        "  $pol.Rules.Add($r)\n"
        "}\n" % (_esc(disp_in), _esc(disp_out), _esc(program)))
    try:
        _ps_write(script)
    except OSError as e:
        return {"ok": False, "err": "创建拦截规则失败：%s" % e}
    audit_add("block", program)
    return {"ok": True, "data": {"already": False,
                                 "names": [disp_in, disp_out]}}


def unblock_all():
    """移除全部本工具创建的拦截规则（操作前自动备份）。"""
    err = _require_admin()
    if err:
        return err
    try:
        st = enum_state()
    except OSError as e:
        return {"ok": False, "err": str(e)}
    ours = blocked_rules(st["rules"])
    if not ours:
        return {"ok": True, "data": {"removed": 0}}
    bk = backup_now()
    lines = "\n".join("$pol.Rules.Remove('%s')" % _esc(r["name"]) for r in ours)
    script = ("$ErrorActionPreference='Stop'\n"
              "$pol = New-Object -ComObject HNetCfg.FwPolicy2\n" + lines + "\n")
    try:
        _ps_write(script)
    except OSError as e:
        return {"ok": False, "err": "批量移除失败：%s" % e}
    audit_add("unblock_all", "%d 条（备份：%s）" % (len(ours), (bk or {}).get("name", "")))
    return {"ok": True, "data": {"removed": len(ours), "backup": bk}}


# ---------------------------------------------------------------- 高级规则
_PORTS_RE = re.compile(r"^[\d,\-]+$")
_ADDRESS_RE = re.compile(r"^[0-9a-fA-F:.\-/,\s]+$")   # IPv4/IPv6/CIDR/逗号


def _validate_spec(spec):
    name = str(spec.get("name") or "").strip()
    if not name:
        return None, "规则名不能为空"
    if len(name) > 180:
        return None, "规则名过长（≤180 字符）"
    direction = spec.get("direction")
    if direction not in ("in", "out"):
        return None, "方向无效（in/out）"
    action = spec.get("action")
    if action not in ("allow", "block"):
        return None, "动作无效（allow/block）"
    proto = spec.get("proto", "Any")
    if proto not in PROTO_IDS:
        return None, "协议无效（Any/TCP/UDP/ICMPv4/ICMPv6）"
    program = str(spec.get("program") or "").strip()
    if program and not os.path.isfile(program):
        return None, "程序文件不存在：%s" % program
    for key in ("lports", "rports"):
        ports = str(spec.get(key) or "").strip().replace(" ", "")
        if not ports:
            continue
        if proto not in ("TCP", "UDP"):
            return None, "仅 TCP/UDP 可指定端口（%s）" % key
        if not _PORTS_RE.match(ports):
            return None, "端口格式无效（应为数字/逗号/连字符）"
        for part in ports.split(","):
            for p in part.split("-"):
                if not p.isdigit() or not 0 <= int(p) <= 65535:
                    return None, "端口超出范围（0-65535）：%s" % p
    raddrs = str(spec.get("raddrs") or "").strip()
    if raddrs and not _ADDRESS_RE.match(raddrs):
        return None, "远程地址格式无效（IPv4/IPv6/CIDR，逗号分隔）"
    profiles = spec.get("profiles")
    mask = 0
    for p in profiles or []:
        if p not in ("domain", "private", "public"):
            return None, "配置文件无效（domain/private/public）"
        mask |= {"domain": 1, "private": 2, "public": 4}[p]
    return {
        "name": "%s·%s" % (RULE_PREFIX, name),
        "direction": direction, "action": action, "proto": proto,
        "program": program,
        "lports": str(spec.get("lports") or "").strip().replace(" ", ""),
        "rports": str(spec.get("rports") or "").strip().replace(" ", ""),
        "raddrs": raddrs,
        "mask": mask,
        "enabled": True if spec.get("enabled") is None else bool(spec.get("enabled")),
    }, None


def create_rule(spec):
    err = _require_admin()
    if err:
        return err
    if not isinstance(spec, dict):
        return {"ok": False, "err": "参数需为对象"}
    norm, verr = _validate_spec(spec)
    if verr:
        return {"ok": False, "err": verr}
    ps = [
        "$ErrorActionPreference='Stop'",
        "$pol = New-Object -ComObject HNetCfg.FwPolicy2",
        "$r = New-Object -ComObject HNetCfg.FWRule",
        "$r.Name = '%s'" % _esc(norm["name"]),
        "$r.Description = '由 LocalToolbox 创建'",
    ]
    if norm["program"]:
        ps.append("$r.ApplicationName = '%s'" % _esc(norm["program"]))
    ps.append("$r.Direction = %d" % {"in": 1, "out": 2}[norm["direction"]])
    ps.append("$r.Action = %d" % {"allow": 1, "block": 0}[norm["action"]])
    ps.append("$r.Enabled = %d" % (1 if norm["enabled"] else 0))
    if norm["proto"] != "Any":
        ps.append("$r.Protocol = %d" % PROTO_IDS[norm["proto"]])
    if norm["lports"]:
        ps.append("$r.LocalPorts = '%s'" % _esc(norm["lports"]))
    if norm["rports"]:
        ps.append("$r.RemotePorts = '%s'" % _esc(norm["rports"]))
    if norm["raddrs"]:
        ps.append("$r.RemoteAddresses = '%s'" % _esc(norm["raddrs"]))
    if norm["mask"]:
        ps.append("$r.Profiles = %d" % norm["mask"])
    ps.append("$pol.Rules.Add($r)")
    try:
        _ps_write("\n".join(ps) + "\n")
    except OSError as e:
        return {"ok": False, "err": "创建规则失败：%s" % e}
    audit_add("create", norm["name"])
    return {"ok": True, "data": {"name": norm["name"]}}


# ---------------------------------------------------------------- 备份/恢复
def backup_now():
    """netsh advfirewall export（需管理员）。返回 {"name","path","ts"}。"""
    err = _require_admin()
    if err:
        return err
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BACKUP_DIR, "fw_backup_%s.wfw" % ts)
    result = run_powershell(
        "netsh advfirewall export '%s'" % _esc(path), timeout=120)
    if not result.ok or not os.path.isfile(path):
        return {"ok": False, "err": (result.output or "").strip()[:200]
                or "备份失败（未生成文件）"}
    _prune_backups()
    audit_add("backup", os.path.basename(path))
    return {"ok": True, "data": {"name": os.path.basename(path), "path": path,
                                 "ts": int(time.time())}}


def _prune_backups():
    try:
        files = sorted(
            (f for f in os.listdir(BACKUP_DIR) if f.endswith(".wfw")),
            reverse=True)
        for f in files[MAX_BACKUPS:]:
            try:
                os.remove(os.path.join(BACKUP_DIR, f))
            except OSError:
                pass
    except OSError:
        pass


def backup_list():
    out = []
    try:
        for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
            if not f.endswith(".wfw"):
                continue
            p = os.path.join(BACKUP_DIR, f)
            try:
                out.append({"name": f, "path": p,
                            "size": os.path.getsize(p),
                            "ts": int(os.path.getmtime(p))})
            except OSError:
                continue
    except OSError:
        pass
    return out


def restore_backup(path):
    err = _require_admin()
    if err:
        return err
    path = str(path or "").strip()
    if not path or not os.path.isfile(path) or not path.endswith(".wfw"):
        return {"ok": False, "err": "备份文件不存在或格式无效（.wfw）"}
    result = run_powershell(
        "netsh advfirewall import '%s'" % _esc(path), timeout=180)
    if not result.ok:
        return {"ok": False, "err": (result.output or "").strip()[:200] or "恢复失败"}
    audit_add("restore", os.path.basename(path))
    return {"ok": True}


# ---------------------------------------------------------------- 端口放行（v3.2 起原有能力）
def rule_name(prefix, port):
    return f"{prefix}{port}"


def add_ports(prefix, ports):
    """一次提权添加多条防火墙入站规则。

    ports: [(端口, "TCP"|"UDP"), ...]
    """
    parts = []
    for port, proto in ports:
        name = rule_name(prefix, port)
        parts.append(
            f'netsh advfirewall firewall delete rule name="{name}" | Out-Null; '
            f'netsh advfirewall firewall add rule name="{name}" dir=in action=allow '
            f"protocol={proto} localport={port} profile=any"
        )
    script = "; ".join(parts)
    # 脚本里用 | Out-Null（PowerShell 语法）：run_elevated 生成的是 .bat（cmd），
    # 会被 cmd 当成未知命令 → 必须走 PowerShell 版提权执行器
    result = run_powershell_elevated(script)
    if result.ok:
        log.info("防火墙入站规则 %s 已放行 %s 端口", prefix,
                 ",".join(str(p) for p, _ in ports))
    else:
        log.warning("防火墙放行 %s 端口失败：%s", prefix, result.output.strip()[:200])
    return result.ok, result.output


def add(prefix, port, protocol="TCP"):
    return add_ports(prefix, [(port, protocol)])


def remove(prefix, port):
    name = rule_name(prefix, port)
    script = f'netsh advfirewall firewall delete rule name="{name}"'
    result = run_powershell_elevated(script)
    if result.ok:
        log.info("防火墙规则 %s 已删除", name)
    else:
        log.warning("删除防火墙规则 %s 失败：%s", name, result.output.strip()[:200])
    return result.ok, result.output
