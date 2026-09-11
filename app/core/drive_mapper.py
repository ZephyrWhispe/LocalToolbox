from . import logger as applog
from .runner import run

log = applog.get_logger("drive")


def map_drive(letter, path, user="", password="", persistent=True):
    clean_letter = letter.replace(":", "").strip().upper()
    clean_path = path.strip()
    if not clean_letter or not clean_path:
        return (False, "盘符和共享路径不能为空。")
    if not clean_path.startswith(r"\\"):
        return (False, "共享路径需以 \\\\ 开头，例如 \\\\电脑名\\共享名")
    cmd = ["net", "use", f"{clean_letter}:", clean_path]
    if user:
        cmd += ["/user:" + user, password]
    if persistent:
        cmd.append("/persistent:yes")
    result = run(cmd, timeout=40)
    if result.ok:
        log.info("映射网络驱动器 %s: → %s（持久=%s）", clean_letter, clean_path, persistent)
        return (True, f"已将 {clean_path} 映射为 {clean_letter}: 盘")
    log.warning("映射网络驱动器 %s: → %s 失败：%s", clean_letter, clean_path, _clean_error(result))
    return (False, _clean_error(result))


def unmap_drive(letter):
    clean_letter = letter.replace(":", "").strip().upper()
    result = run(["net", "use", f"{clean_letter}:", "/delete"])
    if result.ok:
        log.info("断开网络驱动器 %s:", clean_letter)
        return (True, f"已断开 {clean_letter}: 盘")
    log.warning("断开网络驱动器 %s: 失败：%s", clean_letter, _clean_error(result))
    return (False, _clean_error(result))


def list_mapped_drives():
    result = run(["net", "use"])
    drives = []
    if not result.ok:
        return drives
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        if "命令成功完成" in line or "列表" in line or "---" in line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        letter = ""
        remote = ""
        for p in parts:
            if re_full(p):
                letter = p.rstrip(":")
                break
        for p in parts:
            if p.startswith("\\\\"):
                remote = p
                break
        if letter and remote:
            drives.append({"letter": letter, "remote": remote})
    return drives


def re_full(token):
    return len(token) == 2 and token[1] == ":" and token[0].isalpha()


def _clean_error(result):
    lines = [l.strip() for l in result.output.splitlines() if l.strip()]
    meaningful = [
        l for l in lines if "命令成功完成" not in l and "命令失败" not in l
    ]
    if not meaningful:
        return "操作失败。"
    return meaningful[-1]