import winreg

from . import logger as applog
from .privilege import is_admin
from .runner import (
    CommandResult, run, run_elevated, run_powershell,
    run_powershell_elevated, run_powershell_json,
)

log = applog.get_logger("network")

DISCOVERY_GROUP = "网络发现"
SHARING_GROUP = "文件和打印机共享"

_FW_PATH = (
    r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters"
    r"\FirewallPolicy\FirewallRules"
)
DISCOVERY_CTX = "@FirewallAPI.dll,-32752"
SHARING_CTX = "@FirewallAPI.dll,-28502"


def _sys_run(command, timeout=120):
    """系统命令：管理员直接运行（无弹窗），否则提权（弹一次 UAC）。"""
    if is_admin():
        return run(command, timeout=timeout)
    return run_elevated(command, timeout=timeout)


def _sys_ps(script, timeout=120):
    """PowerShell 脚本：管理员直接运行（无弹窗），否则提权（弹一次 UAC）。"""
    if is_admin():
        return run_powershell(script)
    return run_powershell_elevated(script, timeout=timeout)


def set_network_discovery(enable):
    log.info("%s网络发现", "启用" if enable else "关闭")
    steps = [("防火墙规则", set_group(DISCOVERY_GROUP, enable))]
    if enable:
        steps.append(("网络设为专用", _set_network_private()))
        steps.append(("启用发现服务", _start_discovery_services()))
    else:
        steps.append(("停止发现服务", _stop_discovery_services()))
    return _merge_steps(steps)


def set_file_sharing(enable):
    log.info("%s文件和打印机共享", "启用" if enable else "关闭")
    steps = [("防火墙规则", set_group(SHARING_GROUP, enable))]
    if enable:
        steps.append(("网络设为专用", _set_network_private()))
        steps.append(("启用共享服务", _ensure_server_service()))
    return _merge_steps(steps)


def set_group(group, enable):
    state = "yes" if enable else "no"
    return _sys_run(
        [
            "netsh",
            "advfirewall",
            "firewall",
            "set",
            "rule",
            f'group="{group}"',
            "new",
            f"enable={state}",
        ]
    )


def get_all_status():
    net = _read_network_info()
    category = net.get("category")

    is_private = None
    profile_str = None
    if isinstance(category, int) and category >= 0:
        is_private = category in (1, 2)
        profile_str = {0: "Public", 1: "Private", 2: "Domain"}.get(category)

    discovery_rules = _read_group_enabled(DISCOVERY_CTX, profile_str)
    sharing_rules = _read_group_enabled(SHARING_CTX, profile_str)

    discovery = None
    if discovery_rules is not None and is_private is not None:
        discovery = discovery_rules and is_private

    sharing = sharing_rules
    if sharing is None and is_private is False:
        sharing = False

    return {
        "discovery": discovery,
        "sharing": sharing,
        "profiles": net.get("profiles", []),
        "details": {
            "discovery_rules": discovery_rules,
            "sharing_rules": sharing_rules,
            "is_private": is_private,
            "fdrespub": net.get("fdrespub"),
        },
    }


def _merge_steps(steps):
    ok = all(r.ok for _, r in steps)
    summary = "；".join(f"{name}{'成功' if r.ok else '失败'}" for name, r in steps)
    return CommandResult(0 if ok else 1, summary, "")


def _set_network_private():
    return _sys_ps(
        "Get-NetConnectionProfile -ErrorAction SilentlyContinue | "
        "Set-NetConnectionProfile -NetworkCategory Private"
    )


def _start_discovery_services():
    script = (
        "Set-Service FDResPub -StartupType Automatic -ErrorAction SilentlyContinue;"
        "Set-Service FDPhost -StartupType Manual -ErrorAction SilentlyContinue;"
        "Set-Service SSDPSRV -StartupType Manual -ErrorAction SilentlyContinue;"
        "Set-Service upnphost -StartupType Manual -ErrorAction SilentlyContinue;"
        "Start-Service FDResPub -ErrorAction SilentlyContinue;"
        "Start-Service FDPhost -ErrorAction SilentlyContinue;"
        "Start-Service SSDPSRV -ErrorAction SilentlyContinue;"
        "Start-Service upnphost -ErrorAction SilentlyContinue;"
        "Write-Output 'ok'"
    )
    return _sys_ps(script)


def _stop_discovery_services():
    script = (
        "Stop-Service FDResPub -ErrorAction SilentlyContinue;"
        "Stop-Service FDPhost -ErrorAction SilentlyContinue;"
        "Set-Service FDResPub -StartupType Manual -ErrorAction SilentlyContinue;"
        "Write-Output 'ok'"
    )
    return _sys_ps(script)


def _ensure_server_service():
    script = (
        "Set-Service LanmanServer -StartupType Automatic -ErrorAction SilentlyContinue;"
        "Start-Service LanmanServer -ErrorAction SilentlyContinue;"
        "Write-Output 'ok'"
    )
    return _sys_ps(script)


def _read_group_enabled(ctx, profile):
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _FW_PATH)
    except OSError:
        return None
    try:
        count = winreg.QueryInfoKey(key)[1]
        matched = 0
        for i in range(count):
            _, value, _ = winreg.EnumValue(key, i)
            fields = _parse_rule(value)
            if fields.get("EmbedCtxt", "") != ctx:
                continue
            if profile is not None and fields.get("Profile", "") not in (profile, ""):
                continue
            matched += 1
            if fields.get("Active", "").upper() == "TRUE":
                return True
            if fields.get("Enable", "").upper() == "TRUE":
                return True
        return False if matched > 0 else None
    except OSError:
        return None
    finally:
        winreg.CloseKey(key)


def _parse_rule(value):
    fields = {}
    for part in value.split("|"):
        k, _, v = part.partition("=")
        fields[k] = v
    return fields


def _read_network_info():
    script = (
        "$prof = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | "
        "Select-Object Name, NetworkCategory);"
        "$cat = if ($prof.Count -gt 0) { $prof[0].NetworkCategory } else { -1 };"
        "$fd = $false;"
        "$s = Get-Service FDResPub -ErrorAction SilentlyContinue;"
        "if ($s -ne $null -and $s.Status -eq 'Running') { $fd = $true };"
        "[PSCustomObject]@{ Category = $cat; FDResPub = $fd; Profiles = $prof } "
        "| ConvertTo-Json"
    )
    data = run_powershell_json(script)
    if data is None:
        return {"category": None, "fdrespub": None, "profiles": []}

    profiles = []
    prof = data.get("Profiles")
    if isinstance(prof, dict):
        prof = [prof]
    for item in prof or []:
        if not item:
            continue
        cat = item.get("NetworkCategory", 0)
        cat_name = {0: "公用", 1: "专用", 2: "域"}.get(cat, str(cat))
        profiles.append(f"{item.get('Name', '')}（{cat_name}）")

    result = {
        "category": data.get("Category"),
        "fdrespub": data.get("FDResPub"),
        "profiles": profiles,
    }
    if result["fdrespub"] is not None and not isinstance(result["fdrespub"], bool):
        result["fdrespub"] = bool(result["fdrespub"])
    return result