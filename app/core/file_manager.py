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