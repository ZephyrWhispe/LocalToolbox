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