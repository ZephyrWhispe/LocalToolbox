# -*- coding: utf-8 -*-
"""发布产物 SHA-256 清单生成（v5.4 O10，v2rayN Release 流程对齐）。

扫描发布目录（默认 dist/）中的发布产物（.exe/.zip/.7z），生成
sha256sums.txt（BSD/GNU coreutils 标准格式：<sha256>  <filename>），
并附核验说明。清单与产物一一对应，可随 Release 一并上传。

用法：
    python scripts/release_manifest.py            # 扫描 dist/，写 dist/sha256sums.txt
    python scripts/release_manifest.py <目录>     # 指定目录
    python scripts/release_manifest.py --verify <目录>   # 核验现有清单
"""

import hashlib
import sys
from pathlib import Path

ARTIFACT_EXTS = {".exe", ".zip", ".7z"}
MANIFEST_NAME = "sha256sums.txt"

VERIFY_NOTE = (
    "# 核验说明（Windows PowerShell）：\n"
    "#   Get-FileHash .\\<文件名> -Algorithm SHA256\n"
    "# 与本清单逐行比对；或用 git bash：sha256sum -c sha256sums.txt\n"
)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(directory: Path):
    files = sorted(
        (p for p in directory.iterdir()
         if p.is_file() and p.suffix.lower() in ARTIFACT_EXTS),
        key=lambda p: p.name.lower(),
    )
    if not files:
        raise SystemExit("目录中没有发布产物（%s）：%s"
                         % ("/".join(sorted(ARTIFACT_EXTS)), directory))
    return files


def write_manifest(directory: Path) -> Path:
    files = collect(directory)
    lines = [VERIFY_NOTE]
    for p in files:
        lines.append("%s  %s\n" % (sha256_of(p), p.name))
    out = directory / MANIFEST_NAME
    out.write_text("".join(lines), encoding="utf-8", newline="\n")
    print("已生成 %s（%d 个产物）" % (out, len(files)))
    for p in files:
        print("  %s → %s" % (p.name, sha256_of(p)[:16] + "…"))
    return out


def verify_manifest(directory: Path) -> int:
    manifest = directory / MANIFEST_NAME
    if not manifest.is_file():
        raise SystemExit("未找到清单：%s（先运行不带 --verify 的命令生成）" % manifest)
    bad = 0
    seen = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition("  ")
        target = directory / name.strip()
        if not target.is_file():
            print("缺失：%s" % name)
            bad += 1
            continue
        actual = sha256_of(target)
        if actual != digest.strip():
            print("不一致：%s（清单 %s… / 实际 %s…）"
                  % (name, digest[:16], actual[:16]))
            bad += 1
        seen += 1
    extra = [p.name for p in collect(directory)
             if p.name not in
             [l.partition("  ")[2].strip()
              for l in manifest.read_text(encoding="utf-8").splitlines()
              if "  " in l and not l.startswith("#")]]
    for name in extra:
        print("清单外产物：%s（未登记）" % name)
        bad += 1
    print("核验完成：%d 个一致，%d 个问题" % (seen, bad))
    return 1 if bad else 0


def main():
    args = [a for a in sys.argv[1:]]
    verify = "--verify" in args
    if verify:
        args.remove("--verify")
    directory = Path(args[0]) if args else Path("dist")
    if not directory.is_dir():
        raise SystemExit("目录不存在：%s" % directory)
    if verify:
        sys.exit(verify_manifest(directory))
    write_manifest(directory)


if __name__ == "__main__":
    main()
