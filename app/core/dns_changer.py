"""DNS 修改器核心：通过 WMI 修改网络适配器 DNS。"""

import ctypes
import subprocess
# 窗口化进程里跑控制台程序会弹黑窗，必须禁用
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def list_adapters():
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
             "Select-Object Name, InterfaceIndex, MacAddress | "
             "ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=10,
            creationflags=_NOWIN)
        import json
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            data = [data]
        return [{"name": a.get("Name", ""),
                 "index": a.get("InterfaceIndex", 0),
                 "mac": a.get("MacAddress", "")} for a in data]
    except Exception:
        return []


def get_dns(adapter_name):
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-DnsClientServerAddress -InterfaceAlias '%s' -AddressFamily IPv4 | "
             "Select-Object -ExpandProperty ServerAddresses | "
             "ConvertTo-Json -Compress" % adapter_name.replace("'", "''")],
            capture_output=True, text=True, timeout=10,
            creationflags=_NOWIN)
        import json
        data = json.loads(result.stdout)
        if isinstance(data, list):
            return {"primary": data[0] if len(data) > 0 else "",
                    "secondary": data[1] if len(data) > 1 else ""}
        return {"primary": data or "", "secondary": ""}
    except Exception:
        return {"primary": "", "secondary": ""}


def set_dns(adapter_name, primary, secondary=""):
    if not is_admin():
        return {"ok": False, "err": "修改 DNS 需要管理员权限"}
    cmd = "Set-DnsClientServerAddress -InterfaceAlias '%s' " % adapter_name.replace("'", "''")
    servers = primary
    if secondary:
        servers = "%s,%s" % (primary, secondary)
    cmd += "-ServerAddresses ('%s')" % servers.replace(",", "','")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=10,
            creationflags=_NOWIN)
        if result.returncode == 0:
            return {"ok": True}
        return {"ok": False, "err": result.stderr.strip()[:200]}
    except Exception as e:
        return {"ok": False, "err": str(e)}


def reset_dns(adapter_name):
    if not is_admin():
        return {"ok": False, "err": "修改 DNS 需要管理员权限"}
    cmd = ("Set-DnsClientServerAddress -InterfaceAlias '%s' -ResetServerAddresses"
           % adapter_name.replace("'", "''"))
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=10,
            creationflags=_NOWIN)
        if result.returncode == 0:
            return {"ok": True}
        return {"ok": False, "err": result.stderr.strip()[:200]}
    except Exception as e:
        return {"ok": False, "err": str(e)}
