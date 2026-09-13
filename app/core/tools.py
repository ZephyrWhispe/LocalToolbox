"""工具箱核心：哈希校验与清单校验、目录结构快照、文件列表导出、端口查询、二维码生成。

纯标准库 + Pillow/qrcode（可选）。所有函数保持阻塞简单风格 —— pywebview
每个 js_api 调用自带独立线程，无需异步；大数据量操作提供 progress/cancel 回调。
"""

import base64
import csv
import datetime
import hashlib
import io
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import logger as applog
from .runner import run, run_powershell_json

log = applog.get_logger("tools")

HASH_ALGOS = {"md5": hashlib.md5, "sha1": hashlib.sha1, "sha256": hashlib.sha256}
_CHUNK = 1 << 20  # 1MiB


def _collect_files(paths):
    """把文件/目录展开为文件全路径列表（目录递归），返回 (files, missing)。"""
    files = []
    missing = []
    for p in paths:
        p = os.path.abspath(str(p).strip().strip('"'))
        if os.path.isfile(p):
            files.append(p)
        elif os.path.isdir(p):
            for root, dirs, names in os.walk(p, onerror=lambda e: None):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in sorted(names):
                    files.append(os.path.join(root, name))
        else:
            missing.append(p)
    return files, missing


def hash_file(path, algo="sha256"):
    h = HASH_ALGOS[algo]()
    with open(path, "rb") as f:
        while True:
            block = f.read(_CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def hash_paths(paths, algo="sha256", progress=None, cancel=None, workers=4):
    """计算文件/目录（递归）的哈希，返回 [{path, size, digest}] 与失败项 [{path, err}]。

    :param progress: 回调 (done, total)，total 为文件总数
    :param cancel:   回调 () -> bool，返回 True 时停止并抛出中断
    """
    files, missing = _collect_files(paths)
    results = []
    fails = [{"path": m, "err": "路径不存在"} for m in missing]
    done = 0

    def work(path):
        try:
            size = os.path.getsize(path)
            digest = hash_file(path, algo)
            return (path, size, digest, None)
        except OSError as e:
            return (path, 0, "", str(e))

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(work, f) for f in files]
        for fut in futures:
            if cancel and cancel():
                raise RuntimeError("已取消")
            path, size, digest, err = fut.result()
            done += 1
            if err:
                fails.append({"path": path, "err": err})
            else:
                results.append({"path": path, "size": size, "digest": digest})
            if progress:
                progress(done, len(files))
    finally:
        # 取消时立即返回：未启动的 future 一并取消，不等待已入队任务跑完
        pool.shutdown(wait=False, cancel_futures=True)
    return {"files": len(results), "fails": fails, "results": results}


def build_manifest(paths, algo="md5"):
    """生成 .md5 清单文本：每行 ``<hash> *<路径>``。"""
    out = hash_paths(paths, algo=algo)
    lines = []
    for r in out["results"]:
        lines.append("%s *%s" % (r["digest"], r["path"]))
    return "\n".join(lines) + ("\n" if lines else "")


_MANIFEST_LINE = re.compile(r"^([0-9a-fA-F]{32,64})\s+(\*?)(.*)$")


def verify_manifest(manifest_path):
    """校验 md5 清单：逐行解析 ``hash  path`` 或 ``hash *path``，路径相对清单所在目录。"""
    mdir = os.path.dirname(os.path.abspath(manifest_path))
    total = ok = 0
    fails = []
    try:
        raw = open(manifest_path, "r", encoding="utf-8-sig", errors="replace").read()
    except OSError as e:
        return {"ok": False, "err": str(e)}
    for lineno, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        m = _MANIFEST_LINE.match(line)
        if not m:
            fails.append({"line": lineno, "reason": "格式无法解析"})
            continue
        digest = m.group(1).lower()  # 大小写归一，兼容 fciv 等大写清单
        rel = m.group(3).strip()
        target = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(mdir, rel))
        total += 1
        if not os.path.exists(target):
            fails.append({"path": rel, "reason": "缺失", "line": lineno})
            continue
        if os.path.isdir(target):
            fails.append({"path": rel, "reason": "是目录而非文件", "line": lineno})
            continue
        try:
            actual = hash_file(target, algo="md5")
        except OSError as e:
            fails.append({"path": rel, "reason": str(e), "line": lineno})
            continue
        if actual == digest:
            ok += 1
        else:
            fails.append({"path": rel, "reason": "哈希不匹配", "line": lineno})
    return {"ok": ok == total and total > 0, "total": total, "ok_count": ok, "fails": fails}


def snapshot_tree(source, dest):
    """复制源目录的完整空目录骨架（不含文件）到目标目录，返回创建的目录数。"""
    source = os.path.abspath(source)
    dest = os.path.abspath(dest)
    if not os.path.isdir(source):
        raise ValueError("源目录不存在：%s" % source)
    if os.path.normcase(source) == os.path.normcase(dest):
        raise ValueError("源目录与目标目录相同：%s" % source)
    created = 0
    for root, dirs, names in os.walk(source):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        rel = os.path.relpath(root, source)
        target = dest if rel == "." else os.path.join(dest, rel)
        os.makedirs(target, exist_ok=True)
        created += 1
    return created


def export_file_list(path, include_sub=True, with_hash=False, algo="sha256", progress=None, cancel=None):
    """导出目录文件清单为 CSV（utf-8-sig，Excel 直接打开），保存到 Downloads。

    列：相对路径, 文件名, 大小(字节), 修改时间, [哈希]。返回 (导出文件路径, 行数)。
    遍历收集时即时回调 progress/cancel；哈希列并行计算，行结构统一为 5 列。
    """
    source = os.path.abspath(str(path).strip().strip('"'))
    if not os.path.isdir(source):
        raise ValueError("目录不存在：%s" % source)
    entries = []  # [rel, name, size, mtime]（size/mtime 失败时为空串，列数仍统一）
    done = 0

    def add(rel, name):
        nonlocal done
        try:
            st = os.stat(os.path.join(source, rel))
            size, mtime = st.st_size, datetime.datetime.fromtimestamp(
                st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            size, mtime = "", ""
        entries.append([rel, name, size, mtime])
        done += 1
        if progress:
            progress(done, None)
        if cancel and cancel():
            raise RuntimeError("已取消")

    if include_sub:
        for root, dirs, names in os.walk(source, onerror=lambda e: None):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(names):
                rel = os.path.relpath(os.path.join(root, name), source)
                add(rel, name)
    else:
        for name in sorted(n for n in os.listdir(source)
                           if os.path.isfile(os.path.join(source, n))):
            add(name, name)

    digests = [""] * len(entries)
    if with_hash and entries:
        with ThreadPoolExecutor(max_workers=4) as pool:
            digests = list(pool.map(
                lambda e: hash_file(os.path.join(source, e[0]), algo)
                if os.path.isfile(os.path.join(source, e[0])) else "",
                entries))
    header = ["相对路径", "文件名", "大小(字节)", "修改时间"] + ([algo.upper()] if with_hash else ["哈希"])
    out = os.path.join(os.path.expanduser("~"), "Downloads",
                       "lan_file_list_%s.csv" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for e, d in zip(entries, digests):
            writer.writerow(e + [d])
    return out, len(entries)


# -- 端口占用 ---------------------------------------------------------


def port_owner(port):
    """查询监听端口的进程。返回 [{port, pid, name}]；无占用时返回空列表。"""
    port = int(port)
    script = (
        "$c = Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue;"
        "$c | ForEach-Object { $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue;"
        "[pscustomobject]@{Port=$_.LocalPort; Pid=$p.Id; Name=$p.ProcessName} } | ConvertTo-Json -Compress"
        % port
    )
    data = run_powershell_json(script)
    if data is None:
        log.warning("查询端口 %d 占用失败或无数据", port)
        return []
    if not data:
        return []
    items = data if isinstance(data, list) else [data]
    out = []
    for it in items:
        if isinstance(it, dict):
            out.append({"port": it.get("Port"), "pid": it.get("Pid"), "name": it.get("Name") or ""})
    return out


def kill_pid(pid):
    """结束进程（先普通权限，失败提示需管理员）。返回 (ok, msg)。"""
    result = run(["taskkill", "/F", "/PID", str(pid)], timeout=15)
    if result.ok:
        log.info("已结束进程 PID=%s", pid)
        return True, "已结束进程 PID=%s" % pid
    return False, result.output.strip()[:200] or "结束进程失败（可能需管理员权限）"


# -- Wi-Fi 密码查看（netsh，本机已保存的 WLAN 配置文件） -----------------


def wifi_profiles():
    """列出本机已保存的 WLAN 配置文件名。
    兼容中文 / 英文系统输出；无无线网卡或无保存网络时返回空列表。"""
    result = run(["netsh", "wlan", "show", "profiles"], timeout=20)
    if not result.ok:
        return []
    names = []
    for line in result.stdout.splitlines():
        m = re.match(
            r"^\s*(?:所有用户配置文件|All User Profile)\s*:\s*(.+?)\s*$", line)
        if m:
            names.append(m.group(1))
    return names


def wifi_password(name):
    """读取指定 WLAN 配置文件的明文密码与认证方式。
    返回 (password_or_None, auth)；开放网络 / 未保存密钥时 password 为 None。"""
    result = run(
        ["netsh", "wlan", "show", "profile", "name=%s" % name, "key=clear"],
        timeout=20)
    if not result.ok:
        return None, ""
    pwd = ""
    auth = ""
    for line in result.stdout.splitlines():
        s = line.strip()
        m = re.match(r"^(?:关键内容|Key Content)\s*:\s*(.*?)\s*$", s)
        if m:
            pwd = m.group(1)
        m2 = re.match(r"^(?:身份验证|验证|Authentication)\s*:\s*(.+?)\s*$", s)
        if m2:
            auth = m2.group(1)
    return (pwd or None), auth


# -- 硬件信息（Windows CIM/WMI，零第三方依赖；只读，无需管理员） ----------

_HW_MEM_TYPES = {
    20: "DDR", 21: "DDR2", 22: "DDR2 FB-DIMM", 24: "DDR3",
    26: "DDR4", 34: "DDR5", 28: "LPDDR", 29: "LPDDR2",
    30: "LPDDR3", 31: "LPDDR4", 32: "LPDDR4X",
}

_hw_cache = {}
_hw_cache_lock = threading.Lock()


def _hw_cache_get(key, ttl):
    with _hw_cache_lock:
        hit = _hw_cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    return None


def _hw_cache_put(key, data):
    with _hw_cache_lock:
        _hw_cache[key] = (time.time(), data)


def _hw_json(script, timeout=40):
    """单进程跑一段综合 CIM 查询脚本并解析 JSON；失败返回 None。
    PS5 控制台输出编码（中文系统 GBK）与 runner.run 的 gbk 解码天然匹配。"""
    return run_powershell_json(script, timeout=timeout)


def _arr(x):
    """PowerShell ConvertTo-Json 单元素数组会被折叠成对象 → 统一还原为列表。"""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _gb(n):
    try:
        return round(float(n) / (1 << 30), 1)
    except (TypeError, ValueError):
        return None


def hw_summary():
    """概览：电脑/系统/CPU/内存/显卡/磁盘 摘要（缓存 60s）。"""
    cached = _hw_cache_get("summary", 60)
    if cached is not None:
        return cached
    data = _hw_json(
        "$ErrorActionPreference='SilentlyContinue';"
        "$cs = Get-CimInstance Win32_ComputerSystem;"
        "$csp = Get-CimInstance Win32_ComputerSystemProduct | Select-Object -First 1;"
        "$os = Get-CimInstance Win32_OperatingSystem;"
        "$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1;"
        "$mems = @(Get-CimInstance Win32_PhysicalMemory);"
        "$gpus = @(Get-CimInstance Win32_VideoController);"
        "$disks = @(Get-CimInstance Win32_DiskDrive);"
        "$ld = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3');"
        "$memSum = ($mems | Measure-Object Capacity -Sum).Sum;"
        "$diskSum = ($disks | Measure-Object Size -Sum).Sum;"
        "$up = 0;"
        "if ($os.LastBootUpTime) { $up = [int]((Get-Date) - $os.LastBootUpTime).TotalSeconds };"
        "$ldUsed = 0; $ldTotal = 0;"
        "foreach ($d in $ld) { $ldUsed += ($d.Size - $d.FreeSpace); $ldTotal += $d.Size };"
        "[pscustomobject]@{"
        " computer = $cs.Name; model = $csp.Name; vendor = $cs.Manufacturer;"
        " sys = $os.Caption; build = $os.BuildNumber;"
        " uptime_h = [math]::Round($up / 3600.0, 1);"
        " cpu = $cpu.Name; cpu_cores = $cpu.NumberOfCores; cpu_threads = $cpu.NumberOfLogicalProcessors;"
        " cpu_ghz = [math]::Round($cpu.MaxClockSpeed / 1000.0, 2);"
        " mem_gb = [math]::Round($memSum / 1GB, 1); mem_bars = $mems.Count;"
        " gpus = @($gpus | ForEach-Object Name);"
        " disk_total_tb = [math]::Round($diskSum / 1TB, 2);"
        " ld_total_gb = [math]::Round($ldTotal / 1GB, 1); ld_used_gb = [math]::Round($ldUsed / 1GB, 1)"
        "} | ConvertTo-Json -Compress -Depth 3")
    if not isinstance(data, dict):
        log.warning("硬件概览查询失败或解析失败")
        return {}
    out = {
        "computer": data.get("computer") or "",
        "model": data.get("model") or "",
        "vendor": data.get("vendor") or "",
        "sys": data.get("sys") or "",
        "build": str(data.get("build") or ""),
        "uptime_h": data.get("uptime_h"),
        "cpu": data.get("cpu") or "",
        "cpu_cores": data.get("cpu_cores"),
        "cpu_threads": data.get("cpu_threads"),
        "cpu_ghz": data.get("cpu_ghz"),
        "mem_gb": data.get("mem_gb"),
        "mem_bars": data.get("mem_bars"),
        "gpus": [g for g in _arr(data.get("gpus")) if g],
        "disk_total_tb": data.get("disk_total_tb"),
        "ld_total_gb": data.get("ld_total_gb"),
        "ld_used_gb": data.get("ld_used_gb"),
    }
    _hw_cache_put("summary", out)
    return out


def hw_live():
    """实时使用率：CPU / 内存 / 各逻辑盘（不缓存）。"""
    data = _hw_json(
        "$ErrorActionPreference='SilentlyContinue';"
        "$cpu = (Get-CimInstance Win32_Processor |"
        "  Measure-Object -Property LoadPercentage -Average).Average;"
        "$os = Get-CimInstance Win32_OperatingSystem;"
        "$memPct = 0; $memUsed = 0; $memTotal = 0;"
        "if ($os.TotalVisibleMemorySize) {"
        "  $memTotal = [double]$os.TotalVisibleMemorySize * 1KB;"
        "  $memUsed = ([double]$os.TotalVisibleMemorySize - [double]$os.FreePhysicalMemory) * 1KB;"
        "  $memPct = [math]::Round($memUsed / $memTotal * 100, 0) };"
        "$disks = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' |"
        "  ForEach-Object { [pscustomobject]@{ drive = $_.DeviceID;"
        "    total = $_.Size; free = $_.FreeSpace;"
        "    pct = $(if ($_.Size) { [math]::Round(($_.Size - $_.FreeSpace) / $_.Size * 100, 0) } else { 0 }) } });"
        "[pscustomobject]@{ cpu = $cpu; mem_pct = $memPct;"
        "  mem_used_gb = [math]::Round($memUsed / 1GB, 1);"
        "  mem_total_gb = [math]::Round($memTotal / 1GB, 1);"
        "  disks = $disks } | ConvertTo-Json -Compress -Depth 3")
    if not isinstance(data, dict):
        return {}
    return {
        "cpu": data.get("cpu"),
        "mem_pct": data.get("mem_pct"),
        "mem_used_gb": data.get("mem_used_gb"),
        "mem_total_gb": data.get("mem_total_gb"),
        "disks": [
            {"drive": d.get("drive"), "total": d.get("total"),
             "free": d.get("free"), "pct": d.get("pct")}
            for d in _arr(data.get("disks")) if isinstance(d, dict)
        ],
    }


def hw_detail():
    """硬件明细：CPU / 内存条 / 显卡 / 主板 / BIOS（静态，缓存 300s）。"""
    cached = _hw_cache_get("detail", 300)
    if cached is not None:
        return cached
    data = _hw_json(
        "$ErrorActionPreference='SilentlyContinue';"
        "$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1;"
        "$mems = @(Get-CimInstance Win32_PhysicalMemory);"
        "$gpus = @(Get-CimInstance Win32_VideoController);"
        "$bb = Get-CimInstance Win32_BaseBoard | Select-Object -First 1;"
        "$bios = Get-CimInstance Win32_BIOS | Select-Object -First 1;"
        "[pscustomobject]@{"
        " cpu = @{ name = $cpu.Name; cores = $cpu.NumberOfCores;"
        "   threads = $cpu.NumberOfLogicalProcessors;"
        "   base_ghz = [math]::Round($cpu.MaxClockSpeed / 1000.0, 2);"
        "   cache_l2 = $cpu.L2CacheSize; cache_l3 = $cpu.L3CacheSize;"
        "   socket = $cpu.SocketDesignation; voltage = $cpu.CurrentVoltage };"
        " mems = @($mems | ForEach-Object { [pscustomobject]@{"
        "   gb = [math]::Round($_.Capacity / 1GB, 1); type = $_.SMBIOSMemoryType;"
        "   speed = $_.Speed; mfr = $_.Manufacturer; slot = $_.DeviceLocator } });"
        " gpus = @($gpus | ForEach-Object { [pscustomobject]@{ name = $_.Name;"
        "   ram = $_.AdapterRAM; driver = $_.DriverVersion;"
        "   driver_date = $(if ($_.DriverDate)"
        "     { $_.DriverDate.ToString('yyyy-MM-dd') } else { '' });"
        "   mode = $(if ($_.CurrentHorizontalResolution)"
        "     { \"$($_.CurrentHorizontalResolution) x $($_.CurrentVerticalResolution)\" } else { '' }) } });"
        " board = @{ mfr = $bb.Manufacturer; product = $bb.Product };"
        " bios = @{ mfr = $bios.Manufacturer; ver = $bios.SMBIOSBIOSVersion;"
        "   date = $(if ($bios.ReleaseDate)"
        "     { $bios.ReleaseDate.ToString('yyyy-MM-dd') } else { '' }) }"
        "} | ConvertTo-Json -Compress -Depth 4")
    if not isinstance(data, dict):
        log.warning("硬件明细查询失败或解析失败")
        return {}
    cpu = data.get("cpu") or {}
    board = data.get("board") or {}
    bios = data.get("bios") or {}
    out = {
        "cpu": {
            "name": cpu.get("name") or "",
            "cores": cpu.get("cores"), "threads": cpu.get("threads"),
            "base_ghz": cpu.get("base_ghz"),
            "cache_l2": cpu.get("cache_l2"), "cache_l3": cpu.get("cache_l3"),
            "socket": cpu.get("socket") or "",
        },
        "mems": [
            {"gb": m.get("gb"),
             "type": _HW_MEM_TYPES.get(m.get("type"), str(m.get("type") or "未知")),
             "speed": m.get("speed"), "mfr": (m.get("mfr") or "").strip(),
             "slot": m.get("slot") or ""}
            for m in _arr(data.get("mems")) if isinstance(m, dict)
        ],
        "gpus": [
            {"name": g.get("name") or "",
             "ram_gb": _gb(g.get("ram")),
             "driver": str(g.get("driver") or ""),
             "driver_date": (g.get("driver_date") or "")[:10],
             "mode": g.get("mode") or ""}
            for g in _arr(data.get("gpus")) if isinstance(g, dict)
        ],
        "board": {"mfr": board.get("mfr") or "", "product": board.get("product") or ""},
        "bios": {"mfr": bios.get("mfr") or "", "ver": bios.get("ver") or "",
                 "date": (bios.get("date") or "")[:10]},
    }
    _hw_cache_put("detail", out)
    return out


def hw_disks():
    """存储：物理盘逐条 + 逻辑分区使用率（不缓存，使用率实时）。"""
    data = _hw_json(
        "$ErrorActionPreference='SilentlyContinue';"
        "$phys = @(Get-CimInstance Win32_DiskDrive);"
        "$medias = @{};"
        "try { Get-CimInstance -Namespace root\\microsoft\\windows\\storage"
        "  -ClassName MSFT_PhysicalDisk | ForEach-Object {"
        "    $medias[$_.SerialNumber.Trim()] = $_.MediaType } } catch {};"
        "$drives = @($phys | ForEach-Object { [pscustomobject]@{"
        "  model = $_.Model; size_gb = [math]::Round($_.Size / 1GB, 1);"
        "  iface = $_.InterfaceType; serial = $_.SerialNumber;"
        "  media = $medias[$_.SerialNumber.Trim()] } });"
        "$parts = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' |"
        "  ForEach-Object { [pscustomobject]@{ drive = $_.DeviceID;"
        "    label = $_.VolumeName; fs = $_.FileSystem;"
        "    total = $_.Size; free = $_.FreeSpace;"
        "    pct = $(if ($_.Size) { [math]::Round(($_.Size - $_.FreeSpace) / $_.Size * 100, 0) } else { 0 }) } });"
        "[pscustomobject]@{ drives = $drives; parts = $parts }"
        " | ConvertTo-Json -Compress -Depth 3")
    if not isinstance(data, dict):
        return {"drives": [], "parts": []}
    # MSFT_PhysicalDisk.MediaType: 3=HDD 4=SSD 5=SCM 0=未指定
    media_map = {3: "HDD", 4: "SSD", 5: "SCM"}
    return {
        "drives": [
            {"model": d.get("model") or "",
             "size_gb": d.get("size_gb"),
             "iface": d.get("iface") or "",
             "serial": (d.get("serial") or "").strip(),
             "media": media_map.get(d.get("media"), "")}
            for d in _arr(data.get("drives")) if isinstance(d, dict)
        ],
        "parts": [
            {"drive": p.get("drive"), "label": p.get("label") or "",
             "fs": p.get("fs") or "", "total": p.get("total"),
             "free": p.get("free"), "pct": p.get("pct")}
            for p in _arr(data.get("parts")) if isinstance(p, dict)
        ],
    }


def hw_network():
    """网络适配器（物理网卡 + IP 配置；缓存 60s）。"""
    cached = _hw_cache_get("network", 60)
    if cached is not None:
        return cached
    data = _hw_json(
        "$ErrorActionPreference='SilentlyContinue';"
        "$cfgs = @{};"
        "Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True'"
        " | ForEach-Object { $cfgs[$_.Index] = $_ };"
        "$adapters = @(Get-CimInstance Win32_NetworkAdapter"
        "  -Filter 'PhysicalAdapter=True');"
        "$list = @($adapters | ForEach-Object {"
        "  $c = $cfgs[$_.Index];"
        "  [pscustomobject]@{ name = $_.NetConnectionID; desc = $_.Name;"
        "    mac = $_.MACAddress; speed = $_.Speed; netok = $_.NetConnected;"
        "    ips = $(if ($c) { @($c.IPAddress | Where-Object { $_ -notmatch ':' }) } else { @() });"
        "    gw = $(if ($c) { @($c.DefaultIPGateway) } else { @() }) } });"
        "[pscustomobject]@{ adapters = $list } | ConvertTo-Json -Compress -Depth 4")
    if not isinstance(data, dict):
        return {"adapters": []}
    out = {"adapters": [
        {"name": a.get("name") or "", "desc": a.get("desc") or "",
         "mac": (a.get("mac") or ""), "speed": a.get("speed"),
         "netok": bool(a.get("netok")),
         "ips": [x for x in _arr(a.get("ips")) if x],
         "gw": [x for x in _arr(a.get("gw")) if x]}
        for a in _arr(data.get("adapters")) if isinstance(a, dict)
    ]}
    _hw_cache_put("network", out)
    return out


# -- 二维码 ------------------------------------------------------------


def qr_png_data_url(text, size=280):
    """生成二维码 PNG 的 data URL（qrcode 不可用时返回 None）。"""
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except Exception:
        log.warning("qrcode 库未安装，二维码功能不可用")
        return None
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=5, border=2)
    qr.add_data(str(text))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    img = img.resize((size, size))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")