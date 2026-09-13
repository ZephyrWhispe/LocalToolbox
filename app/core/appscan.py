# -*- coding: utf-8 -*-
"""全量已装应用检测工厂与静默卸载引擎（v5.4 架构级复刻一期）。

架构参照 UniGetUI（MIT，github.com/Devolutions/UniGetUI）与
Bulk Crap Uninstaller（Apache-2.0，github.com/Klocman/Bulk-Crap-Uninstaller）
的「多源检测工厂 + 统一实体模型 + 静默卸载」设计思想；
全部代码为本项目独立实现（Python 标准库），未复制任何参考项目源码。

设计要点：
- 检测源：注册表卸载条目（HKLM 64/32 位视图 + HKCU，单次 PowerShell JSON）
  + UWP Appx 包（复用 optimizer.uwp_list）。winget 安装的应用本就注册进
  卸载表，双源已覆盖，独立 winget 列表源列为二期。
- 统一实体 AppEntry：一处建模，含 quiet_possible / admin_required 计算属性。
- 安全：SystemComponent 与 KB 补丁双过滤、UWP_PROTECTED 保护名单沿用、
  仅 quiet_possible 条目可批量卸载、卸载前快照留痕（removed_apps.json）。
"""

import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field

from . import logger as applog
from .optimizer import (  # 复用既有检测/快照/保护名单
    DATA_DIR, UWP_PROTECTED, _arr, uwp_list,
)
from .platform import is_admin
from .runner import run, run_elevated, run_powershell_json

log = applog.get_logger("appscan")

APPS_SNAPSHOT_PATH = os.path.join(DATA_DIR, "removed_apps.json")

_CACHE_TTL = 300  # 扫描结果缓存秒数（仿 tools.py _hw_cache）
_cache = None       # (ts, entries)
_cache_lock = threading.Lock()

_MSI_GUID_RE = re.compile(r"\{[0-9A-Fa-f-]{36}\}")
_KB_RE = re.compile(r"^KB\d{5,}", re.IGNORECASE)
_KB_WORDS = ("安全更新", "累积更新", "Hotfix", "更新程序 (KB", "Update for Windows")

# 注册表卸载条目检测（三个视图一次会话取全，字段映射见 README 注释）
_REGISTRY_PS = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$paths='HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
    "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
    "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*';"
    "Get-ItemProperty $paths -ErrorAction SilentlyContinue"
    " | Where-Object { $_.DisplayName }"
    " | ForEach-Object { [pscustomobject]@{"
    "  name=[string]$_.DisplayName; ver=[string]$_.DisplayVersion;"
    "  pub=[string]$_.Publisher; date=[string]$_.InstallDate;"
    "  size=[int]($_.EstimatedSize); un=[string]$_.UninstallString;"
    "  quiet=[string]$_.QuietUninstallString;"
    "  sys=[int]([bool]$_.SystemComponent);"
    "  key=$_.PSChildName;"
    "  view= if ($_.PSPath -match 'WOW6432Node') {'x86'} else {'x64'};"
    "  scope= if ($_.PSPath -match 'HKEY_CURRENT_USER') {'user'} else {'machine'} } }"
    " | ConvertTo-Json -Compress -Depth 3")


@dataclass
class AppEntry:
    """已装应用统一实体（BCU ApplicationUninstallerEntry 的最小子集）。"""
    name: str
    source: str                     # registry | uwp
    key: str                        # registry=PSChildName；uwp=PackageFullName
    publisher: str = ""
    version: str = ""
    install_date: str = ""          # 原样保留（yyyyMMdd）
    size_kb: int = 0
    uninstall_cmd: str = ""
    quiet_cmd: str = ""
    scope: str = "machine"          # machine | user
    view: str = "x64"               # x64 | x86（registry 专用）
    extra: dict = field(default_factory=dict)

    @property
    def listkey(self) -> str:
        """对外主键（卸载/勾选均用它）。"""
        return f"{self.source}|{self.key}"

    @property
    def quiet_possible(self) -> bool:
        """能否静默卸载：有 QuietUninstallString，或 MSI 产品码可生成 msiexec。"""
        return bool(self.quiet_cmd) or bool(self._msi_quiet())

    def _msi_quiet(self) -> str:
        if not self.uninstall_cmd:
            return ""
        m = _MSI_GUID_RE.search(self.uninstall_cmd)
        return f"msiexec /x {m.group(0)} /qn /norestart" if m else ""

    @property
    def admin_required(self) -> bool:
        return self.scope == "machine" and not is_admin()

    def effective_quiet_cmd(self) -> str:
        return self.quiet_cmd or self._msi_quiet()

    def to_dict(self) -> dict:
        return {
            "listkey": self.listkey, "name": self.name,
            "publisher": self.publisher, "version": self.version,
            "install_date": self.install_date, "size_kb": self.size_kb,
            "source": self.source, "scope": self.scope, "view": self.view,
            "quiet_possible": self.quiet_possible,
            "admin_required": self.admin_required,
        }


def _is_kb_or_component_name(name: str) -> bool:
    if _KB_RE.match(name):
        return True
    return any(w in name for w in _KB_WORDS)


def scan_registry():
    """注册表卸载条目 → [AppEntry]（含过滤与 WOW64 去重）。"""
    data = run_powershell_json(_REGISTRY_PS, timeout=90)
    if data is None:
        return []
    raw = []
    for it in _arr(data):
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        un = str(it.get("un") or "").strip()
        quiet = str(it.get("quiet") or "").strip()
        # 过滤：空名 / 系统组件 / 无卸载串 / KB 补丁 / 受保护的 UWP 前缀
        if not name or (int(it.get("sys") or 0) == 1) or (not un and not quiet):
            continue
        if _is_kb_or_component_name(name):
            continue
        if any(name.startswith(p) for p in UWP_PROTECTED):
            continue
        raw.append(AppEntry(
            name=name, source="registry",
            key=str(it.get("key") or ""),
            publisher=str(it.get("pub") or "")[:80],
            version=str(it.get("ver") or ""),
            install_date=str(it.get("date") or ""),
            size_kb=int(it.get("size") or 0),
            uninstall_cmd=un, quiet_cmd=quiet,
            scope=str(it.get("scope") or "machine"),
            view=str(it.get("view") or "x64"),
        ))
    # 去重：WOW6432Node 与 64 位视图同名同版本 → 保留 x64
    best = {}
    for e in raw:
        k = (e.name.lower(), e.version)
        cur = best.get(k)
        if cur is None:
            best[k] = e
        elif cur.view != "x64" and e.view == "x64":
            best[k] = e
    out = list(best.values())
    out.sort(key=lambda x: x.name.lower())
    return out


def scan_uwp():
    """UWP Appx 包 → [AppEntry]（复用 optimizer.uwp_list，key=PackageFullName）。"""
    out = []
    for it in uwp_list():
        out.append(AppEntry(
            name=it["name"], source="uwp", key=it.get("full") or it["name"],
            publisher=it.get("publisher") or "", scope="user",
        ))
    return out


def _cli_available(cmd) -> bool:
    return shutil.which(cmd) is not None


def scan_scoop():
    """Scoop 包 → [AppEntry]（`scoop export` JSON；未装 scoop 时返回空）。"""
    if not _cli_available("scoop"):
        return []
    r = run("scoop export", timeout=60)
    try:
        data = json.loads((r.stdout or "").strip())
    except ValueError:
        return []
    out = []
    for it in (data or {}).get("apps") or []:
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        out.append(AppEntry(
            name=name, source="scoop", key=name,
            version=str(it.get("version") or ""),
            publisher=str(it.get("source") or ""),
            scope="user", uninstall_cmd=f"scoop uninstall {name}",
            quiet_cmd=f"scoop uninstall {name}",
        ))
    return out


def scan_choco():
    """Chocolatey 包 → [AppEntry]（`choco list -r`：每行 id|version）。"""
    if not _cli_available("choco"):
        return []
    r = run("choco list -r --no-progress", timeout=120)
    out = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith("chocolatey v"):
            continue
        parts = line.split("|")
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        if not name or name == "chocolatey":
            continue
        out.append(AppEntry(
            name=name, source="choco", key=name,
            version=parts[1].strip(), scope="machine",
            uninstall_cmd=f"choco uninstall {name} -y",
            quiet_cmd=f"choco uninstall {name} -y --no-progress",
        ))
    return out


def scan_apps(progress_cb=None):
    """多源检测工厂：注册表 + UWP + Scoop/Chocolatey（CLI 在才启用）→ 合并去重
    → [AppEntry]（带 TTL 缓存）。单源失败不阻断其它来源。"""
    global _cache
    with _cache_lock:
        if _cache and time.time() - _cache[0] < _CACHE_TTL:
            return _cache[1]
    steps = [
        ("registry", "注册表", scan_registry),
        ("uwp", "UWP 应用", scan_uwp),
        ("scoop", "Scoop", scan_scoop),
        ("choco", "Chocolatey", scan_choco),
    ]
    entries = []
    for i, (_, label, fn) in enumerate(steps):
        if progress_cb:
            progress_cb(i, len(steps) + 1, label)
        try:
            entries.extend(fn())
        except Exception as e:  # 单源失败不阻断其它来源
            log.warning("应用检测源 %s 失败：%s", label, e)
    if progress_cb:
        progress_cb(len(steps), len(steps) + 1, "合并去重")
    entries = _merge_dedupe(entries)
    with _cache_lock:
        _cache = (time.time(), entries)
    return entries


def _merge_dedupe(entries):
    """跨源去重：WOW64 同名同版本保留 x64；Scoop/Chocolatey 条目若已被
    注册表/UWP 覆盖（同名同版本）则丢弃（优先保留带原生卸载串的来源）。"""
    best = {}
    for e in entries:
        if e.source == "registry":
            k = (e.name.lower(), e.version)
            cur = best.get(("reg", k))
            if cur is None:
                best[("reg", k)] = e
            elif cur.view != "x64" and e.view == "x64":
                best[("reg", k)] = e
    for e in entries:
        if e.source == "registry":
            continue
        # CLI 包管理器应用若注册表已有同名同版本条目 → 跳过（避免重复展示）
        if ("reg", (e.name.lower(), e.version)) in best:
            continue
        lk = e.listkey
        cur = best.get(("src", lk))
        if cur is None:
            best[("src", lk)] = e
    out = sorted(best.values(), key=lambda x: (x.source, x.name.lower()))
    return out


def apps_cache_clear():
    global _cache
    with _cache_lock:
        _cache = None


def cache_get():
    """只读缓存（不触发扫描）；过期/未扫描返回 None。"""
    with _cache_lock:
        if _cache and time.time() - _cache[0] < _CACHE_TTL:
            return _cache[1]
    return None


def find_by_listkeys(keys):
    """按 listkey 集合取缓存中的条目；缓存过期时自动重扫。"""
    want = {str(k) for k in (keys or [])}
    entries = scan_apps()
    return [e for e in entries if e.listkey in want]


# -- 卸载引擎 ----------------------------------------------------------------

def _snapshot_load():
    try:
        with open(APPS_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"removed": []}


def _snapshot_save(snap):
    os.makedirs(os.path.dirname(APPS_SNAPSHOT_PATH), exist_ok=True)
    with open(APPS_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)


def removed_list():
    """本工具静默卸载过的应用（快照留痕，一期仅展示）。"""
    return _snapshot_load().get("removed", [])


def uninstall_apps(keys, progress_cb=None):
    """批量静默卸载：单次提权会话逐条执行，输出 LT_UN: 行解析结果。

    仅接受 quiet_possible 条目；返回 [{listkey,name,ok,err?}]，
    成功项写入快照（removed_apps.json）。
    """
    entries = find_by_listkeys(keys)
    if not entries:
        raise ValueError("所选应用不在已扫描列表中，请先重新扫描")
    targets = []
    for k in {str(k) for k in (keys or [])}:
        e = next((x for x in entries if x.listkey == k), None)
        if e is None:
            continue
        if not e.quiet_possible:
            raise ValueError(f"「{e.name}」不支持静默卸载，请手动卸载")
        targets.append(e)
    if not targets:
        raise ValueError("所选应用不支持静默卸载，请手动卸载")

    # 单次提权会话：逐条 Start-Process 等待退出码，LT_UN: 行上报
    ps = ["$ErrorActionPreference='SilentlyContinue';"]
    for e in targets:
        cmd = e.effective_quiet_cmd().replace("'", "''")
        lk = e.listkey.replace("'", "''")
        ps.append(
            f"$p = Start-Process -FilePath cmd.exe"
            f" -ArgumentList '/c', '{cmd}' -Wait -PassThru"
            " -WindowStyle Hidden;")
        ps.append("Write-Output ('LT_UN:' + (ConvertTo-Json -Compress"
                  f" -InputObject @{{ key='{lk}'; code=$p.ExitCode }}));")
    r = run_elevated(["powershell", "-NoProfile", "-Command",
                      "\n".join(ps)], timeout=1800)

    snap = _snapshot_load()
    results = []
    seen = set()
    for line in (r.stdout or "").splitlines():
        if not line.startswith("LT_UN:"):
            continue
        try:
            e = json.loads(line[6:], strict=False)
        except ValueError:
            continue
        key = str(e.get("key") or "")
        code = e.get("code")
        ent = next((x for x in targets if x.listkey == key), None)
        if ent is None:
            continue
        ok = code == 0
        results.append({"listkey": key, "name": ent.name, "ok": ok,
                        "err": "" if ok else f"退出码 {code}"})
        if ok:
            seen.add(key)
            if not any(x["listkey"] == key for x in snap.get("removed", [])):
                snap["removed"].append({
                    "listkey": key, "name": ent.name,
                    "quiet_cmd": ent.effective_quiet_cmd(), "ts": time.time()})
        if progress_cb:
            progress_cb(len(results), len(targets), ent.name)
    _snapshot_save(snap)
    for e in targets:
        if e.listkey not in seen:
            results.append({"listkey": e.listkey, "name": e.name,
                            "ok": False, "err": "卸载失败或被取消"})
    apps_cache_clear()  # 卸载后强制下次重扫
    log.info("批量静默卸载完成：%d/%d 成功",
             len(seen), len(targets))
    return results
