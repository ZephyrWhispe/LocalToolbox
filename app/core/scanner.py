import socket
import ipaddress

from . import logger as applog
from .runner import run

log = applog.get_logger("scanner")


def _port_open(ip, port, timeout):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def _list_local_computers():
    result = run(["net", "view"])
    computers = []
    if not result.ok:
        return computers
    started = False
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "---" in stripped:
            started = True
            continue
        if started and "命令" not in stripped and "成功" not in stripped:
            token = stripped.split()[0]
            computers.append(token.replace("\\\\", ""))
    return computers


def list_computer_shares(host):
    result = run(["net", "view", "\\\\" + host])
    if not result.ok:
        if _is_denied(result.output):
            return [], "denied"
        return [], "unavailable"
    shares = []
    started = False
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "---" in stripped:
            started = True
            continue
        if started and "命令" not in stripped and "共享资源" not in stripped:
            token = stripped.split()[0]
            if token:
                shares.append(token)
    if not shares:
        return [], "empty"
    return shares, None


def connect_host(host, user, password):
    args = ["net", "use", "\\\\" + host + "\\IPC$", password, "/user:" + user]
    result = run(args, timeout=30)
    if result.ok:
        return True, "连接成功"
    lines = [l.strip() for l in result.output.splitlines() if l.strip()]
    msg = lines[-1] if lines else "连接失败"
    return False, msg


def _is_denied(text):
    t = text.lower()
    keywords = [
        "拒绝访问",
        "access is denied",
        "logon failure",
        "登录失败",
        "信任关系",
        "trust",
        "用户名或密码",
        "密码",
        "password",
        "credential",
        "凭据",
        "multiple connections",
        "多次连接",
        "没有权限",
        "权限",
        "找不到网络名称",
    ]
    return any(k in t for k in keywords)


def get_local_network():
    script = (
        "if ($c = Get-NetIPConfiguration | Where-Object { "
        "$_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up' } | "
        "Select-Object -First 1) { "
        '$a = $c.IPv4Address | Select-Object -First 1; '
        '[PSCustomObject]@{ IPAddress = $a.IPAddress; PrefixLength = $a.PrefixLength } | ConvertTo-Json }'
    )
    result = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])
    if not result.ok:
        return None
    try:
        import json

        data = json.loads(result.stdout.strip())
    except Exception:
        return None
    if not data:
        return None
    return data.get("IPAddress"), data.get("PrefixLength")


def scan_network(progress=None, host_callback=None):
    if progress:
        progress(0, 0, "正在获取本机网络信息...")
    info = get_local_network()
    if not info:
        log.info("无法获取本地网络，回退到 net view 扫描")
        if progress:
            progress(0, 0, "无法获取本地网络，回退到 net view ...")
        computers = _list_local_computers()
        results = []
        for c in computers:
            shares, error = list_computer_shares(c)
            results.append({"host": c, "ip": "", "shares": shares, "error": error})
        if progress:
            progress(1, 1, "扫描完成")
        log.info("net view 扫描完成，共 %d 台主机", len(results))
        return results

    ip, prefix = info
    log.info("开始扫描局域网 %s/%s", ip, prefix)
    try:
        network = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    except ValueError:
        return []
    hosts = [str(h) for h in network.hosts()]

    if progress:
        progress(0, len(hosts), f"正在扫描 {len(hosts)} 个地址...")
    open_hosts = []
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=64) as ex:
        futures = {ex.submit(_port_open, h, 445, 0.4): h for h in hosts}
        done = 0
        for f in as_completed(futures):
            done += 1
            host = futures[f]
            if f.result():
                open_hosts.append(host)
            if progress and done % 16 == 0:
                progress(done, len(hosts), f"正在扫描 {host} ...")
    if progress:
        progress(len(hosts), len(hosts), "发现主机，正在枚举共享...")

    results = []
    for idx, host in enumerate(open_hosts):
        shares, error = list_computer_shares(host)
        name = _resolve_hostname(host)
        results.append({"host": name or host, "ip": host, "shares": shares, "error": error})
        if progress:
            progress(len(hosts), len(hosts), f"枚举 {host} 的共享...")
            if host_callback:
                host_callback(results[-1])
    if progress:
        progress(len(hosts), len(hosts), f"扫描完成，共发现 {len(open_hosts)} 台主机。")
    log.info("扫描完成，共发现 %d/%d 台主机", len(open_hosts), len(hosts))
    return results


def _resolve_hostname(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""