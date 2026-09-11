from . import logger as applog
from .runner import run_powershell_elevated

log = applog.get_logger("firewall")


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
