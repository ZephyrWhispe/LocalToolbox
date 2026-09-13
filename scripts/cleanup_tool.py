"""项目定期清理工具：扫描废弃/过期文件 → 备份 → 删除 → 生成报告。

用法（项目根目录下）：
    python cleanup_tool.py --dry-run          # 仅扫描并输出候选清单（默认）
    python cleanup_tool.py --execute          # 备份 + 删除 + 生成报告

识别类别（按优先级内置，防误删源码/文档/测试）：
    C1  __pycache__ / *.pyc        Python 缓存（运行自动重建）
    C2  *.tmp|*.bak|*.old|*.orig  临时/备份残留
    C3  *.part / *.log             传输断点残留 / 运行日志（数据目录）
    C4  *.whl|*.egg-info|*.pdf     意外下载或杂项（需人工核验，默认列出)
    C5  修改时间超过 --max-age-days 天 且 未被源码/文档规则排除
    C6  build/ dist/ 目录           构建中间产物（整目录）
安全边界：
    - 只操作 --target 内（默认项目根）；绝不触碰系统目录
    - 源码/文档/测试/配置类后缀（.py .js .md .html .css .spec .bat .txt .json .gitignore）
      永不删除，仅 C5 例外不适用
    - 删除前先复制到 <target>/_cleanup_backup/<日期>/，报告备份位置
    - --execute 前必须 --dry-run 可见清单
"""

import argparse
import datetime
import os
import shutil
import sys

# 永不删除的源码/文档类后缀
_SAFE_EXT = {
    ".py", ".js", ".md", ".html", ".css", ".spec", ".bat", ".txt", ".json",
    ".gitignore", ".pyproject", ".toml", ".ini", ".cfg", ".db", ".png", ".ico",
}

# C1 目录名与文件后缀
_CACHE_DIRS = {"__pycache__"}
_CACHE_EXTS = {".pyc", ".pyo"}
# C2/C3 临时类后缀
_TEMP_EXTS = {".tmp", ".bak", ".old", ".orig", ".part", ".log", ".whl"}
# C6 构建产物目录
_BUILD_DIRS = {"build", "dist"}
# 临时/构建产物可能附加包目标
_EXTRA_RISK_DIRS = {"node_modules", "venv", ".venv", ".tox"}


def _is_safe_ext(path):
    return os.path.splitext(path)[1].lower() in _SAFE_EXT


def scan(target, max_age_days=30, classify_by_age=True):
    """返回候选列表：[{path, size, mtime, category, reason}]（按类别排序）。"""
    candidates = []
    now = datetime.datetime.now()
    for root, dirs, files in os.walk(target):
        # 跳过备份目录与虚拟环境
        dirs[:] = [d for d in dirs
                   if d not in _EXTRA_RISK_DIRS and not d.endswith("_cleanup_backup")]
        rel = os.path.relpath(root, target)
        for d in list(dirs):
            full = os.path.join(root, d)
            if d in _BUILD_DIRS:
                size = sum(
                    os.path.getsize(os.path.join(r, f))
                    for r, _, fs in os.walk(full) for f in fs
                )
                candidates.append({
                    "path": full.replace(target, "", 1) or os.sep,
                    "size": size, "category": "C6",
                    "reason": "构建产物目录，可重新打包生成",
                })
                dirs.remove(d)
            elif d in _CACHE_DIRS:
                candidates.append({
                    "path": full.replace(target, "", 1),
                    "size": _dir_size(full), "category": "C1",
                    "reason": "Python 缓存，运行时自动重建",
                })
                dirs.remove(d)
        for f in files:
            full = os.path.join(root, f)
            ext = os.path.splitext(f)[1].lower()
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(full))
            if f.endswith(tuple(_CACHE_EXTS)):
                candidates.append({
                    "path": full.replace(target, "", 1),
                    "size": _dir_size(full), "category": "C1",
                    "reason": "Python 字节码缓存",
                })
                continue
            if ext in _TEMP_EXTS:
                candidates.append({
                    "path": full.replace(target, "", 1),
                    "size": os.path.getsize(full), "category": "C2/C3",
                    "reason": f"临时/日志类文件（{ext}）",
                })
                continue
            if classify_by_age and (now - mtime).days > max_age_days and not _is_safe_ext(full):
                candidates.append({
                    "path": full.replace(target, "", 1),
                    "size": os.path.getsize(full), "category": "C5",
                    "reason": f"超过 {max_age_days} 天未修改且非源码/文档",
                })
    return candidates


def _dir_size(path):
    return sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(path) for f in fs)


def backup_and_delete(candidates, target, backup_root):
    """复制到备份目录后删除。返回 (备份数, 删除数, 释放字节)。"""
    freed = 0
    for c in candidates:
        src = target + c["path"]
        dst = os.path.join(backup_root, c["path"].lstrip("\\/"))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
            if os.path.isdir(src):
                shutil.rmtree(src)
            else:
                os.remove(src)
            freed += c["size"]
        except OSError as e:
            print(f"  [跳过] {src}: {e}")
    return len(candidates), freed


def report(candidates, backup_dir, deleted, freed, elapsed_sec):
    lines = [
        "# 定期文件清理报告",
        "",
        f"- 执行时间：{datetime.datetime.now():%Y-%m-%d %H:%M}",
        f"- 删除文件数：{deleted}",
        f"- 释放空间：{freed / 1024:.1f} KB",
        f"- 备份位置：{backup_dir}",
        f"- 耗时：{elapsed_sec:.1f}s",
        "",
        "| 路径 | 大小(KB) | 最后修改 | 类别 | 原因 |",
        "|------|---------|---------|------|------|",
    ]
    for c in candidates:
        size_kb = c["size"] / 1024
        lines.append(
            f"| `{c['path']}` | {size_kb:.1f} | - | {c['category']} | {c['reason']} |"
        )
    lines.append("")
    lines.append("> 机制：运行 `python cleanup_tool.py --execute`（先 `--dry-run` 查看清单）。")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="项目定期清理工具")
    ap.add_argument("--target", default=".", help="扫描目标目录（默认项目根）")
    ap.add_argument("--max-age-days", type=int, default=30, help="C5 时间阈值（天）")
    ap.add_argument("--dry-run", action="store_true", help="仅扫描输出清单")
    ap.add_argument("--execute", action="store_true", help="备份+删除+报告")
    args = ap.parse_args()

    target = os.path.abspath(args.target)
    if not os.path.isdir(target):
        print(f"目标目录不存在：{target}")
        sys.exit(2)

    t0 = datetime.datetime.now()
    cands = scan(target, args.max_age_days)
    total = sum(c["size"] for c in cands)
    print(f"候选 {len(cands)} 项，合计 {total/1024:.1f} KB")
    for c in cands:
        print(f"  [{c['category']}] {c['path']}  ({c['size']/1024:.1f} KB)  {c['reason']}")

    if not cands:
        print("无待清理项。")
        return

    if args.dry_run or not args.execute:
        print("\n（请先确认清单，再以 --execute 执行；前一步已 --dry-run 的请直接执行）")
        return

    backup_root = os.path.join(target, f"_cleanup_backup/{datetime.date.today():%Y-%m-%d}")
    deleted, freed = backup_and_delete(cands, target, backup_root)
    elapsed = (datetime.datetime.now() - t0).total_seconds()
    text = report(cands, backup_root, deleted, freed, elapsed)
    out = os.path.join(target, f"_cleanup_reports/{datetime.date.today():%Y-%m-%d}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\n完成：删除 {deleted} 项，释放 {freed/1024:.1f} KB，报告：{out}")
    print(f"提示：确认没问题后可删除备份目录 {backup_root}")


if __name__ == "__main__":
    main()