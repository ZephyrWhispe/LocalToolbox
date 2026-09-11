import os

from . import logger as applog
from .runner import run, run_elevated, run_powershell_json

log = applog.get_logger("share")


def list_shares():
    script = (
        "Get-CimInstance Win32_Share | Where-Object { $_.Type -eq 0 } | "
        "Select-Object Name,Path,Description | ConvertTo-Json"
    )
    data = run_powershell_json(script)
    shares = []
    if data is None:
        return shares
    if isinstance(data, dict):
        data = [data]
    for item in data:
        shares.append(
            {
                "name": item.get("Name", ""),
                "path": item.get("Path", ""),
                "description": item.get("Description", ""),
            }
        )
    return shares


def create_share(name, path):
    if not name or not path:
        return (False, "共享名和文件夹路径不能为空。")
    if not os.path.isdir(path):
        return (False, "指定的文件夹不存在，请重新选择。")
    result = run_elevated(["net", "share", f"{name}={path}", "/grant:everyone,full"])
    if result.ok:
        log.info("创建共享 %s → %s（everyone 完全控制）", name, path)
        return (True, f"已成功共享文件夹：{name}")
    log.warning("创建共享 %s 失败：%s", name, _clean_net_error(result.output))
    return (False, _clean_net_error(result.output))


def delete_share(name):
    result = run_elevated(["net", "share", name, "/delete"])
    if result.ok:
        log.info("删除共享 %s", name)
        return (True, f"已取消共享：{name}")
    log.warning("删除共享 %s 失败：%s", name, _clean_net_error(result.output))
    return (False, _clean_net_error(result.output))


def _clean_net_error(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    meaningful = [
        l for l in lines if "命令成功完成" not in l and "命令失败" not in l
    ]
    if not meaningful:
        return "操作失败，可能拒绝了权限提升请求。"
    return meaningful[-1]