import os
import ctypes
import shutil

from .runner import run_powershell


def list_directory(path):
    path = path.strip().rstrip("\\/")
    if not path:
        return None
    if not os.path.isdir(path):
        return None
    entries = []
    try:
        names = os.listdir(path)
    except (OSError, PermissionError):
        return None
    for name in names:
        full = os.path.join(path, name)
        try:
            is_dir = os.path.isdir(full)
            size = os.path.getsize(full) if not is_dir else 0
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        entries.append(
            {
                "name": name,
                "path": full,
                "is_dir": is_dir,
                "size": size,
                "mtime": mtime,
            }
        )
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


def copy_paths(sources, dest_dir):
    count = 0
    for src in sources:
        name = os.path.basename(src.rstrip("\\/"))
        target = os.path.join(dest_dir, name)
        if os.path.normcase(src) == os.path.normcase(dest_dir):
            continue
        if os.path.isdir(src):
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, dest_dir)
        count += 1
    return f"已复制 {count} 个项目到 {dest_dir}"


def move_paths(sources, dest_dir):
    count = 0
    for src in sources:
        shutil.move(src, dest_dir)
        count += 1
    return f"已移动 {count} 个项目到 {dest_dir}"


def delete_paths(paths):
    count = 0
    permanent = []
    for p in paths:
        try:
            _delete_recycle(p)
        except Exception:
            _delete_permanent(p)
            permanent.append(os.path.basename(p.rstrip("\\/")))
        count += 1
    msg = f"已删除 {count} 个项目"
    if permanent:
        msg += "（部分项目不受回收站支持，已永久删除）"
    return msg


def new_folder(parent, name):
    target = os.path.join(parent, name)
    os.makedirs(target, exist_ok=False)
    return f"已创建文件夹：{name}"


def rename_path(path, new_name):
    new_name = str(new_name or "").strip()
    if not new_name or any(c in new_name for c in '\\/:*?"<>|'):
        raise ValueError("名称不能为空且不能包含 \\/:*?\"<>| 字符")
    parent = os.path.dirname(path)
    target = os.path.join(parent, new_name)
    if os.path.exists(target):
        raise FileExistsError(f"同名项目已存在：{new_name}")
    os.rename(path, target)
    return target


def new_file(parent, name):
    name = str(name or "").strip()
    if not name or any(c in name for c in '\\/:*?"<>|'):
        raise ValueError("名称不能为空且不能包含 \\/:*?\"<>| 字符")
    target = os.path.join(parent, name)
    if os.path.exists(target):
        raise FileExistsError(f"同名项目已存在：{name}")
    with open(target, "x", encoding="utf-8"):
        pass
    return target


def search_recursive(root, pattern, max_results=200):
    """当前目录递归搜索（名称子串匹配，忽略大小写），上限 max_results 条。"""
    pat = str(pattern or "").strip().lower()
    if not pat:
        return []
    out = []

    def add_entry(full, is_dir):
        try:
            out.append({
                "name": os.path.basename(full),
                "path": full,
                "is_dir": is_dir,
                "size": 0 if is_dir else os.path.getsize(full),
                "mtime": os.path.getmtime(full),
                "rel_dir": os.path.dirname(full)[len(root):].lstrip("\\/"),
            })
        except OSError:
            pass

    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            if pat in name.lower():
                add_entry(os.path.join(dirpath, name), True)
                if len(out) >= max_results:
                    return out
        for name in filenames:
            if pat in name.lower():
                add_entry(os.path.join(dirpath, name), False)
                if len(out) >= max_results:
                    return out
    return out


def _iter_files(src):
    """展开为一个 (文件, 相对名) 作业列表（目录递归；目录本身负责创建）。"""
    jobs = []
    if os.path.isdir(src):
        base = os.path.basename(src.rstrip("\\/"))
        for dirpath, _dirnames, filenames in os.walk(src):
            rel = os.path.relpath(dirpath, src)
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                relname = fn if rel == "." else os.path.join(base, rel, fn)
                jobs.append((full, relname))
    else:
        jobs.append((src, os.path.basename(src)))
    return jobs


def _copy_file_progress(src, dst, cb, done_bytes, total_bytes):
    """分块复制单文件并按字节回报进度；返回新 done_bytes。"""
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    chunk = 256 * 1024
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            buf = fsrc.read(chunk)
            if not buf:
                break
            fdst.write(buf)
            done_bytes += len(buf)
            if cb:
                cb(done_bytes, total_bytes, os.path.basename(src))
    return done_bytes


def copy_paths_progress(sources, dest_dir, progress_cb=None):
    """带进度复制：先统计总字节，逐文件分块复制并回报 (done, total, name)。"""
    jobs = []
    total_bytes = 0
    for src in sources:
        for full, relname in _iter_files(src):
            try:
                total_bytes += os.path.getsize(full)
            except OSError:
                pass
            jobs.append((src, full, os.path.join(dest_dir, relname)))
    done = 0
    for i, (src, full, dst) in enumerate(jobs):
        if os.path.normcase(os.path.dirname(full)) == os.path.normcase(dest_dir) \
                and os.path.isfile(full):
            continue  # 源即目标目录内文件：跳过自复制
        done = _copy_file_progress(full, dst, progress_cb, done, total_bytes)
    if progress_cb and jobs:
        progress_cb(total_bytes, total_bytes, "")
    return f"已复制 {len(jobs)} 个文件到 {dest_dir}"


def move_paths_progress(sources, dest_dir, progress_cb=None):
    """带进度移动：跨盘走复制+删源（shutil.move 自适应），同盘 rename 秒完成；
    按文件计数回报 (done, total, name)。"""
    jobs = []
    for src in sources:
        for full, relname in _iter_files(src):
            jobs.append((full, os.path.join(dest_dir, relname)))
    total = len(jobs)
    for i, (full, dst) in enumerate(jobs):
        if progress_cb:
            progress_cb(i, total, os.path.basename(full))
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        shutil.move(full, dst)
    if progress_cb and total:
        progress_cb(total, total, "")
    # 移动后尝试清理空的源目录
    for src in sources:
        if os.path.isdir(src):
            try:
                if not any(os.scandir(src)):
                    os.rmdir(src)
            except OSError:
                pass
    return f"已移动 {total} 个文件到 {dest_dir}"


def open_path(path):
    os.startfile(path)


def drive_letters():
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        mask = 0
    return [f"{chr(65 + i)}:\\" for i in range(26) if mask & (1 << i)]


def _delete_recycle(path):
    escaped = path.replace("'", "''")
    if os.path.isdir(path):
        method = "DeleteDirectory"
    else:
        method = "DeleteFile"
    script = (
        "Add-Type -AssemblyName Microsoft.VisualBasic;"
        f"[Microsoft.VisualBasic.FileIO.FileSystem]::{method}('{escaped}',"
        "'OnlyErrorDialogs','SendToRecycleBin')"
    )
    result = run_powershell(script)
    if not result.ok:
        raise RuntimeError(result.output.strip() or "删除失败")


def _delete_permanent(path):
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=_on_rm_error)
    else:
        os.remove(path)


def _on_rm_error(func, path, exc_info):
    try:
        os.chmod(path, 0o777)
        func(path)
    except Exception:
        raise