"""外部核心程序自动下载：从 GitHub Releases 拉取 OpenList / v2ray / xray 等。

仅用标准库（urllib + zipfile），失败抛出中文 BindlError，并提供手动放置路径提示。
版本与文件哈希缓存于 DATA_HOME/bindl/cache.json，同版本重复下载直接跳过。

版本号与资产枚举不使用 GitHub API（匿名 API 有 60 次/小时限额，超限即 403，
会导致核心/规则库全部无法下载）：改为 releases/latest 重定向解析 tag +
releases/expanded_assets 页面解析资产文件名。直连 github.com 失败时按序尝试
镜像前缀（见 _GITHUB_MIRRORS，可通过环境变量 LOCALTOOLBOX_GH_MIRRORS 覆盖）。
"""

import hashlib
import json
import ctypes
import os
import platform
import re
import ssl
import threading
import time
import urllib.request
import zipfile

from .config import DATA_HOME

BINDL_DIR = os.path.join(DATA_HOME, "bindl")
CACHE_FILE = os.path.join(BINDL_DIR, "cache.json")

_USER_AGENT = "LocalToolbox/3.0 (smb-tool)"

# GitHub 下载加速镜像（实测可用的公开服务，按优先级排序；镜像失效时可自行增删，
# 或通过环境变量 LOCALTOOLBOX_GH_MIRRORS="https://a/ https://b/" 覆盖）。
_GITHUB_MIRRORS = [
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "https://gh.ddlc.top/",
]

# kind -> 来源仓库与资产匹配规则（资产名匹配失败时退化为任意 .zip 资产，由解压阶段定位 exe）
_REPOS = {
    "openlist": {
        "repo": "OpenListTeam/OpenList",
        "match": re.compile(r"openlist.*(?:win|windows).*amd64.*\.zip", re.I),
        "exe": "openlist.exe",
    },
    "v2ray": {
        "repo": "v2fly/v2ray-core",
        "match": re.compile(r"v2ray-windows(?:-64)?\.zip", re.I),
        "exe": "v2ray.exe",
    },
    "xray": {
        "repo": "XTLS/Xray-core",
        "match": re.compile(r"Xray-windows-64\.zip", re.I),
        "exe": "xray.exe",
    },
    "sing-box": {
        "repo": "SagerNet/sing-box",
        "match": re.compile(r"sing-box-.*windows-amd64\.zip", re.I),
        "exe": "sing-box.exe",
        # Windows 版压缩包内附 wintun.dll（TUN 模式依赖），随核心一并解出
        "companions": ("wintun.dll",),
    },
    # Clash 系常用内核（mihomo / Clash.Meta）：与 xray/sing-box 完全独立的引擎
    "mihomo": {
        "repo": "MetaCubeX/mihomo",
        "match": re.compile(r"^mihomo-windows-amd64-v1-go1(2[4-9]|3\d)-.*\.zip$", re.I),
        "exe": "mihomo.exe",
    },
    "clash-geoip": {
        "repo": "MetaCubeX/meta-rules-dat",
        "match": re.compile(r"^geoip\.dat$", re.I),
        "exe": "geoip.dat",
        "raw": True,
    },
    "clash-mmdb": {
        "repo": "MetaCubeX/meta-rules-dat",
        "match": re.compile(r"^country\.mmdb$", re.I),
        "exe": "country.mmdb",
        "raw": True,
    },
    "clash-geosite": {
        "repo": "MetaCubeX/meta-rules-dat",
        "match": re.compile(r"^geosite\.dat$", re.I),
        "exe": "geosite.dat",
        "raw": True,
    },
    "rclone": {
        "repo": "rclone/rclone",
        "match": re.compile(r"rclone-.*windows-amd64\.zip", re.I),
        "exe": "rclone.exe",
    },
    "winfsp": {
        "repo": "winfsp/winfsp",
        "match": re.compile(r"^winfsp-.*\.msi$", re.I),
        "exe": "winfsp.msi",  # 裸 msi 安装包（静默安装），落盘为固定名
        "raw": True,
    },
    "geoip": {
        "repo": "Loyalsoldier/v2ray-rules-dat",
        "match": re.compile(r"^geoip\.dat$", re.I),
        "exe": "geoip.dat",
        "raw": True,  # 规则文件为裸 .dat，非 zip
    },
    "geosite": {
        "repo": "Loyalsoldier/v2ray-rules-dat",
        "match": re.compile(r"^geosite\.dat$", re.I),
        "exe": "geosite.dat",
        "raw": True,
    },
    # sing-box 规则集（.srs，1.12+ 唯一受支持的本地规则格式；旧 .db 已在 1.12 移除）
    # 来源：SagerNet 官方规则集仓库（sing-geosite / sing-geoip）
    "geosite-cn": {
        "repo": "SagerNet/sing-geosite",
        "match": re.compile(r"^geosite-cn\.srs$", re.I),
        "exe": "geosite-cn.srs",
        "raw": True,
    },
    "geosite-ads": {
        "repo": "SagerNet/sing-geosite",
        "match": re.compile(r"^geosite-category-ads-all\.srs$", re.I),
        "exe": "geosite-category-ads-all.srs",
        "raw": True,
    },
    "geoip-cn": {
        "repo": "SagerNet/sing-geoip",
        "match": re.compile(r"^geoip-cn\.srs$", re.I),
        "exe": "geoip-cn.srs",
        "raw": True,
    },
    "geoip-private": {
        "repo": "SagerNet/sing-geoip",
        "match": re.compile(r"^geoip-private\.srs$", re.I),
        "exe": "geoip-private.srs",
        "raw": True,
    },
}

# sing-box 规则集（.srs）：1.12+ 的 rule_set 机制，旧 geoip/geosite 路由已移除。
# SagerNet 官方仓库只发旧 .db 格式（不可用），改取 MetaCubeX/meta-rules-dat 的 sing 分支。
_SRS_URLS = {
    "geosite-cn": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat"
                  "/sing/geo/geosite/cn.srs",
    "geosite-ads": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat"
                   "/sing/geo/geosite/category-ads-all.srs",
    "geoip-cn": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat"
                "/sing/geo/geoip/cn.srs",
    "geoip-private": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat"
                     "/sing/geo/geoip/private.srs",
}
_SRS_CACHE_DAYS = 7      # 同一规则集 7 天内复用本地文件（避免重复慢下载）

# --------------------------------------------------------------------------
# 架构识别（按操作系统位数，而非进程位数；含 x86-64 微架构等级）
# --------------------------------------------------------------------------
def arch_tag():
    """本机架构标识：amd64 / arm64 / 386。

    PROCESSOR_ARCHITEW6432 存在说明是 32 位进程跑在 64 位系统上——此时仍应
    下载 64 位内核（内核是独立进程，与宿主进程位数无关）。
    """
    arch = (os.environ.get("PROCESSOR_ARCHITEW6432")
            or os.environ.get("PROCESSOR_ARCHITECTURE")
            or platform.machine() or "").lower()
    if arch in ("amd64", "x86_64"):
        return "amd64"
    if arch in ("arm64", "aarch64"):
        return "arm64"
    if arch in ("x86", "i386", "i686", "i586"):
        return "386"
    return "amd64"


def cpu_level_amd64():
    """x86-64 微架构等级：3=AVX2（v3）/ 2=SSE4.2 或 AVX（v2）/ 1=基线（v1）。

    mihomo 等内核按该等级分发包，选高层级可拿到更好的性能。
    """
    if arch_tag() != "amd64":
        return 1
    try:
        k32 = ctypes.windll.kernel32
        # PF_AVX2_INSTRUCTIONS_AVAILABLE=40, PF_AVX_INSTRUCTIONS_AVAILABLE=39,
        # PF_SSE4_2_INSTRUCTIONS_AVAILABLE=38
        if k32.IsProcessorFeaturePresent(40):
            return 3
        if k32.IsProcessorFeaturePresent(39) or k32.IsProcessorFeaturePresent(38):
            return 2
    except Exception:
        pass
    return 1


def arch_label():
    """给界面显示的架构描述。"""
    a = arch_tag()
    if a == "amd64":
        return "x86-64 v%d" % cpu_level_amd64()
    return {"arm64": "ARM64", "386": "x86 (32 位)"}.get(a, a)


def _arch_patterns(kind):
    """按本机架构给出资产匹配优先级（正则字符串列表，依次尝试）。"""
    arch = arch_tag()
    if kind == "mihomo":
        if arch == "amd64":
            lvl = cpu_level_amd64()
            pats = []
            for lv in range(lvl, 0, -1):
                pats.append(r"^mihomo-windows-amd64-v%d-go\d+" % lv)
            pats += [r"^mihomo-windows-amd64-v\d+", r"^mihomo-windows-amd64-"]
            if lvl <= 1:
                # 基线 CPU / 老系统：官方 compatible 构建更稳
                pats.append(r"^mihomo-windows-amd64-compatible")
            return pats
        if arch == "arm64":
            return [r"^mihomo-windows-arm64-"]
        return [r"^mihomo-windows-386-"]
    if kind == "xray":
        return {"amd64": [r"^Xray-windows-64\.zip$"],
                "arm64": [r"^Xray-windows-arm64-v8a\.zip$"],
                "386": [r"^Xray-windows-32\.zip$"]}[arch]
    if kind == "sing-box":
        return {"amd64": [r"sing-box-.*windows-amd64\.zip$"],
                "arm64": [r"sing-box-.*windows-arm64\.zip$"],
                "386": [r"sing-box-.*windows-386\.zip$"]}[arch]
    if kind == "v2ray":
        return {"amd64": [r"v2ray-windows-64\.zip", r"v2ray-windows\.zip"],
                "arm64": [r"v2ray-windows-arm64-v8a\.zip"],
                "386": [r"v2ray-windows-32\.zip"]}[arch]
    if kind == "rclone":
        return {"amd64": [r"rclone-.*windows-amd64\.zip$"],
                "arm64": [r"rclone-.*windows-arm64\.zip$"],
                "386": [r"rclone-.*windows-386\.zip$"]}[arch]
    if kind == "openlist":
        return {"amd64": [r"openlist.*(?:win|windows).*amd64.*\.zip$"],
                "arm64": [r"openlist.*(?:win|windows).*arm64.*\.zip$"],
                "386": [r"openlist.*(?:win|windows).*386.*\.zip$"]}[arch]
    return []


_lock = threading.Lock()


class BindlError(RuntimeError):
    """下载失败的中文错误（网络 / 资产缺失 / 解压失败）。"""


def _load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cached_info(kind):
    """已下载组件的缓存信息（version/exe/asset）；无缓存返回 {}。"""
    return dict(_load_cache().get(kind) or {})


def _save_cache(cache):
    try:
        os.makedirs(BINDL_DIR, exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass


def _mirror_list():
    """镜像列表：环境变量覆盖 > 内置默认（按序直连优先于镜像）。"""
    env = os.environ.get("LOCALTOOLBOX_GH_MIRRORS", "").strip()
    if env:
        return [m if m.endswith("/") else m + "/"
                for m in env.replace(",", " ").split() if m.strip()]
    return list(_GITHUB_MIRRORS)


def _github_urls(url):
    """给定 github.com 链接 → [直连, 镜像1, 镜像2, ...]（按序尝试）。"""
    if not url.lower().startswith(("http://", "https://")):
        return [url]
    return [url] + [m + url for m in _mirror_list()]


def _open_http(url, timeout=30, extra_headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    if extra_headers:
        for k, v in extra_headers.items():
            req.add_header(k, v)
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    return opener.open(req, timeout=timeout)


def _resolve_latest_tag(repo):
    """解析最新 release 版本号：releases/latest 跟随重定向到 tag 页。

    返回 tag 字符串；多候选（直连 + 镜像）全部失败时抛 BindlError。
    """
    errs = []
    for u in _github_urls("https://github.com/%s/releases/latest" % repo):
        try:
            resp = _open_http(u, timeout=20)
            final = resp.geturl()
            m = re.search(r"/releases/tag/([^/?#]+)", final)
            if m:
                return m.group(1)
            errs.append("%s：无法解析重定向（%s）" % (u, final))
        except Exception as e:
            errs.append("%s：%s" % (u, e))
    raise BindlError(
        "无法访问 GitHub Releases 获取最新版本（%s）。"
        "可尝试设置环境变量 LOCALTOOLBOX_GH_MIRRORS 指定可用镜像，"
        "或手动下载 %s 放入应用数据目录。"
        % ("；".join(errs[-3:]), repo))


def _enum_release_assets(repo, tag):
    """从 releases/expanded_assets 页面枚举发行版资产文件名（不经 GitHub API）。

    返回规范化的资产 URL 列表（github.com 直链，下载阶段再套镜像）。
    """
    names = []
    page = "https://github.com/%s/releases/expanded_assets/%s" % (repo, tag)
    errs = []
    for u in _github_urls(page):
        try:
            html = _open_http(u, timeout=20).read().decode("utf-8", "replace")
            for href in re.findall(
                    r"/releases/download/[^\"'<>]+?/([^\"'<>/]+)", html):
                name = href.split("?")[0]
                if name not in names:
                    names.append(name)
            if names:
                break
            errs.append("%s：页面无资产链接" % u)
        except Exception as e:
            errs.append("%s：%s" % (u, e))
    if not names:
        raise BindlError(
            "无法枚举 %s %s 的发行文件（%s），"
            "请到 https://github.com/%s/releases 手动下载。"
            % (repo, tag, "；".join(errs[-2:]), repo))
    return [
        "https://github.com/%s/releases/download/%s/%s" % (repo, tag, name)
        for name in names
    ]


def _latest_release(kind):
    """返回 (tag, [资产下载URL, ...])。不使用 GitHub API（避免匿名限额 403）。"""
    repo = _REPOS[kind]["repo"]
    tag = _resolve_latest_tag(repo)
    return tag, _enum_release_assets(repo, tag)


def _asset_name(url):
    return url.split("?")[0].rsplit("/", 1)[-1]


def _pick_asset_by_arch(assets, kind):
    """按本机架构/微架构等级挑选资产（依次尝试各优先级正则）。

    例：amd64 + AVX2 → mihomo 的 v3 构建；ARM64 → arm64 构建；
    32 位系统 → 386 构建。全部不中返回 None（调用方回落通用规则）。
    """
    for pat in _arch_patterns(kind):
        try:
            rx = re.compile(pat, re.I)
        except re.error:
            continue
        hit = [a for a in assets if a and rx.search(_asset_name(a))]
        if not hit:
            continue
        # 同一架构可能有多份（go120..go125）：优先 Go 工具链版本更高的
        def _go_ver(url):
            m = re.search(r"-go(\d+)", _asset_name(url), re.I)
            return int(m.group(1)) if m else 0
        hit.sort(key=_go_ver, reverse=True)
        picked = _pick_asset(hit, rx)
        if picked:
            return picked
    return None


def _pick_asset(assets, match):
    """按规则挑选资产；优先完整版（排除 -lite 精简包），再放宽到 windows 包/任意 zip。"""
    def strict(no_lite):
        for a in assets:
            if not a or not match.search(_asset_name(a)):
                continue
            n = _asset_name(a)
            if not no_lite or "-lite" not in n.lower():
                return a
        return None

    a = strict(True) or strict(False)
    if a:
        return a
    for a in assets:
        n = _asset_name(a)
        if n and n.lower().endswith((".zip", ".msi")) and (
                "windows" in n.lower() or "win64" in n.lower() or n.lower().startswith("win")):
            return a
    for a in assets:
        if a and _asset_name(a).lower().endswith(".zip"):
            return a
    return None


def _find_exe_in_zip(zf, exe_name):
    """在压缩包中定位目标 .exe（不区分大小写）。

    匹配优先级：根目录精确名 → 任意层级精确名 → 以主干开头的名字
    （mihomo 打包为 mihomo-windows-amd64-v1-go125.exe 这类带平台后缀）
    → 包内唯一的 .exe。
    """
    stem = os.path.splitext(exe_name)[0].lower()
    exact = None
    prefixed = None
    exes = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        base = name.rsplit("/", 1)[-1].lower()
        if not base.endswith(".exe"):
            continue
        exes.append(info)
        if base == exe_name.lower():
            if "/" not in name:
                return info
            exact = info if exact is None else exact
        elif prefixed is None and base.startswith(stem + "-"):
            prefixed = info
    return exact or prefixed or (exes[0] if len(exes) == 1 else None)


def _download_file(exe_name, url, tmp_path, dest_dir, progress_cb, timeout,
                   validator=None):
    """流式下载资产 url 到 tmp_path：直连失败自动按序尝试镜像。

    validator(path) 可校验内容（大小 / zip 结构），校验不过按坏源处理换下一候选。
    全部失败抛 BindlError（附最后几个错误与手动放置提示）。
    """
    errs = []
    for u in _github_urls(url):
        try:
            resp = _open_http(u, timeout=timeout)
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)
            if validator and not validator(tmp_path):
                raise ValueError("内容不完整或非预期格式")
            return
        except Exception as e:
            errs.append("%s：%s" % (u, e))
            try:
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
    raise BindlError(
        "下载 %s 失败：%s（直连与镜像均不可用。请检查网络，"
        "或手动下载后把 %s 放到：%s）"
        % (exe_name, "；".join(errs[-3:]), exe_name, dest_dir))


def _download_raw(kind, spec, tag, cache, asset_url, exe_path, dest_dir,
                  progress_cb, timeout):
    """裸文件资源（geoip.dat / geosite.dat）的直接下载。"""
    tmp = os.path.join(dest_dir, ".dl_%s.bin" % kind)
    try:
        _download_file(spec["exe"], asset_url, tmp, dest_dir, progress_cb,
                       timeout, validator=_verify_size)
        os.replace(tmp, exe_path)
    except BindlError:
        raise
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
    asset_name = _asset_name(asset_url)
    with _lock:
        cache = _load_cache()
        cache[kind] = {"version": tag, "exe": exe_path, "sha": _sha256(exe_path),
                       "asset": asset_name}
        _save_cache(cache)
    return {"kind": kind, "version": tag, "exe": exe_path, "cached": False,
            "asset": asset_name}


def _verify_srs(path):
    """校验 sing-box 规则集格式：文件头必须是 SRS magic。

    不能只看体积——geoip:private 这类规则集只有百来字节。
    """
    try:
        with open(path, "rb") as f:
            return f.read(3) == b"SRS"
    except OSError:
        return False


def _verify_size(path, min_bytes=1024):
    try:
        return os.path.getsize(path) >= min_bytes
    except OSError:
        return False


def _extract_companion(zf, name, dest_dir):
    """从 zip 中解出伴随文件（如 sing-box 附带的 wintun.dll）。"""
    info = _find_exe_in_zip(zf, name)
    if not info:
        return False
    tmpc = os.path.join(dest_dir, ".dl_%s.bin" % name)
    try:
        with open(tmpc, "wb") as out:
            with zf.open(info) as src:
                while True:
                    buf = src.read(1024 * 1024)
                    if not buf:
                        break
                    out.write(buf)
        os.replace(tmpc, os.path.join(dest_dir, name))
        return True
    except OSError:
        return False
    finally:
        try:
            if os.path.isfile(tmpc):
                os.remove(tmpc)
        except OSError:
            pass


def _sha256(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def download_ruleset(kind, dest_dir=None, progress_cb=None, timeout=60, force=False):
    """下载 sing-box .srs 规则集（固定 URL + 镜像回退 + 7 天本地缓存）。

    force=True 跳过 7 天缓存强制重新下载。
    返回 {"kind","version","exe","cached"}（与 download_binary 对齐）。
    """
    if kind not in _SRS_URLS:
        raise BindlError("未知规则集：%s" % kind)
    if not dest_dir:
        dest_dir = os.path.join(DATA_HOME, "proxy", "bin")
    os.makedirs(dest_dir, exist_ok=True)
    exe_path = os.path.join(dest_dir, kind + ".srs")
    now = time.time()
    prev = _load_cache().get("srs:" + kind) or {}
    if (not force and prev.get("ts") and os.path.isfile(exe_path)
            and now - float(prev["ts"]) < _SRS_CACHE_DAYS * 86400
            and _verify_srs(exe_path)):
        return {"kind": kind, "version": prev.get("version", ""),
                "exe": exe_path, "cached": True}
    tmp = os.path.join(dest_dir, ".dl_%s.srs" % kind)
    try:
        _download_file(kind + ".srs", _SRS_URLS[kind], tmp, dest_dir,
                       progress_cb, timeout, validator=_verify_srs)
        os.replace(tmp, exe_path)
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
    stamp = time.strftime("%Y-%m-%d", time.localtime(now))
    with _lock:
        cache = _load_cache()
        cache["srs:" + kind] = {"ts": now, "version": stamp,
                                "exe": exe_path, "sha": _sha256(exe_path)}
        _save_cache(cache)
    return {"kind": kind, "version": stamp, "exe": exe_path, "cached": False}


# MetaCubeX/meta-rules-dat sing 分支的任意 v2fly 类别 .srs（geosite/geoip）。
# 主源 raw 直链（自动叠加镜像前缀），备源 jsdelivr CDN（@sing 分支）——
# 主源连镜像都不通时自动落到 CDN，构成「备份源」链。
_SING_RAW = "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing"
_SING_CDN = "https://cdn.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@sing"


def srs_url(rtype, category, cdn=False):
    """sing 分支类别规则集 URL：rtype ∈ geosite / geoip。"""
    base = _SING_CDN if cdn else _SING_RAW
    return "%s/geo/%s/%s.srs" % (base, rtype, category)


def download_srs(rtype, category, dest_dir=None, progress_cb=None,
                 timeout=60, force=False):
    """按类别下载 sing-box .srs 规则集（如 geosite/telegram.srs）。

    下载链：raw 直连 → raw 镜像 → jsdelivr CDN → CDN 镜像。7 天缓存，
    force=True 强制重新下载。返回 {"kind","version","exe","cached"}。
    """
    rtype = "geosite" if str(rtype) == "geosite" else "geoip"
    category = str(category or "").strip().lower()
    if not category or not re.fullmatch(r"[a-z0-9!_.\-]+", category):
        raise BindlError("非法的规则集类别：%s" % category)
    kind = "%s-%s" % (rtype, category)
    if not dest_dir:
        dest_dir = os.path.join(DATA_HOME, "proxy", "bin")
    os.makedirs(dest_dir, exist_ok=True)
    exe_path = os.path.join(dest_dir, kind + ".srs")
    now = time.time()
    prev = _load_cache().get("srs:" + kind) or {}
    if (not force and prev.get("ts") and os.path.isfile(exe_path)
            and now - float(prev["ts"]) < _SRS_CACHE_DAYS * 86400
            and _verify_srs(exe_path)):
        return {"kind": kind, "version": prev.get("version", ""),
                "exe": exe_path, "cached": True}
    tmp = os.path.join(dest_dir, ".dl_%s.srs" % kind)
    try:
        errs = []
        for cdn in (False, True):     # 主源 raw（含镜像） → 备源 jsdelivr CDN（含镜像）
            try:
                _download_file(kind + ".srs", srs_url(rtype, category, cdn=cdn),
                               tmp, dest_dir, progress_cb, timeout,
                               validator=_verify_srs)
                break
            except BindlError as e:
                errs.append(str(e))
        else:
            raise BindlError("；".join(errs[-1:]))
        os.replace(tmp, exe_path)
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
    stamp = time.strftime("%Y-%m-%d", time.localtime(now))
    with _lock:
        cache = _load_cache()
        cache["srs:" + kind] = {"ts": now, "version": stamp,
                                "exe": exe_path, "sha": _sha256(exe_path)}
        _save_cache(cache)
    return {"kind": kind, "version": stamp, "exe": exe_path, "cached": False}


def download_binary(kind, dest_dir=None, progress_cb=None, timeout=30, force=False):
    """下载指定核心程序到 dest_dir（默认 DATA_HOME/<kind>/bin）。

    progress_cb(done, total)：done 为已下载字节，total 为 Content-Length（未知为 0）。
    force=True 跳过版本缓存强制重新下载（MetaCubeX 等仓库用固定 tag「latest」
    滚动更新资产，版本号不变但内容会变——统一规则库「立即更新」需要它）。
    返回 {"kind","version","exe","cached"}。
    """
    if kind not in _REPOS:
        raise BindlError("未知组件类型：%s" % kind)
    spec = _REPOS[kind]
    if not dest_dir:
        dest_dir = os.path.join(DATA_HOME, kind, "bin")
    os.makedirs(dest_dir, exist_ok=True)
    exe_path = os.path.join(dest_dir, spec["exe"])

    tag, assets = _latest_release(kind)
    # 选资产要在缓存判断之前：同一版本可能有多种构建（如 mihomo 的 v1/v2/v3），
    # 换了架构构建必须重新下载，不能只看版本号
    asset_url = None
    if assets:
        # 先按本机架构（含 x86-64 微架构等级）选择，再回落到组件的通用规则
        asset_url = _pick_asset_by_arch(assets, kind) or _pick_asset(assets, spec["match"])
    asset_name = _asset_name(asset_url) if asset_url else ""

    cache = _load_cache()
    prev = cache.get(kind) or {}
    if (not force and prev.get("version") == tag and os.path.isfile(prev.get("exe", ""))
            and (prev.get("asset") or "") == asset_name):
        return {"kind": kind, "version": tag, "exe": prev["exe"], "cached": True,
                "asset": asset_name}
    if not asset_url:
        raise BindlError(
            "未在 %s 的最新发行版中找到 Windows 压缩包（%s）。"
            "请到 %s/releases 手动下载后解压，将 %s 放入目录：%s"
            % (spec["repo"], spec["exe"], "https://github.com/%s" % spec["repo"], spec["exe"], dest_dir)
        )

    if spec.get("raw"):
        # 裸 .dat 规则文件：直接下载（不经过 zip 解压）
        return _download_raw(kind, spec, tag, cache, asset_url, exe_path,
                             dest_dir, progress_cb, timeout)

    tmp_zip = os.path.join(dest_dir, ".dl_%s.zip" % kind)
    try:
        try:
            _download_file(spec["exe"], asset_url, tmp_zip, dest_dir,
                           progress_cb, timeout,
                           validator=lambda p: zipfile.is_zipfile(p))
            with zipfile.ZipFile(tmp_zip) as zf:
                found = _find_exe_in_zip(zf, spec["exe"])
                if not found:
                    raise BindlError("压缩包内未找到 %s，请手动解压并放入：%s" % (spec["exe"], dest_dir))
                tmp_exe = os.path.join(dest_dir, ".dl_%s.exe" % kind)
                with open(tmp_exe, "wb") as out:
                    with zf.open(found) as src:
                        while True:
                            buf = src.read(1024 * 1024)
                            if not buf:
                                break
                            out.write(buf)
                os.replace(tmp_exe, exe_path)
                for comp in spec.get("companions", ()):
                    if _extract_companion(zf, comp, dest_dir):
                        pass  # 伴随文件已解出到 dest_dir
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as e:
            raise BindlError("下载的压缩包损坏（%s），请重试或手动放置 %s 到 %s" % (e, spec["exe"], dest_dir))
        except BindlError:
            raise

        with _lock:
            cache = _load_cache()
            cache[kind] = {
                "version": tag,
                "exe": exe_path,
                "sha": _sha256(exe_path),
                # 记录实际下载的构建（如 mihomo 的 v1/v2/v3），
                # 否则同版本换架构构建会被误判为「已下载」而跳过
                "asset": asset_name,
            }
            _save_cache(cache)
        return {"kind": kind, "version": tag, "exe": exe_path, "cached": False,
            "asset": asset_name}
    finally:
        try:
            if os.path.isfile(tmp_zip):
                os.remove(tmp_zip)
        except OSError:
            pass


def latest(kind):
    """查询指定组件的 GitHub 最新版本 tag（不下载）。供「检查更新」使用。

    返回 {"kind","version"}；网络失败抛 BindlError。
    """
    if kind not in _REPOS:
        raise BindlError("未知组件类型：%s" % kind)
    tag, _ = _latest_release(kind)
    return {"kind": kind, "version": tag}


def latest_version(kind):
    """查询指定组件的最新发行版版本号（不下载）；失败抛 BindlError。

    「检查更新/刷新」按钮用：与 cached_version(kind) 对比即可判断是否有新版。
    """
    if kind not in _REPOS:
        raise BindlError("未知组件类型：%s" % kind)
    return _resolve_latest_tag(_REPOS[kind]["repo"])


def cached_version(kind):
    """本地缓存中的版本（可能为 None：未下载过 / 缓存失效）。"""
    try:
        c = _load_cache().get(kind) or {}
        v = c.get("version")
        return v if (v and os.path.isfile(c.get("exe", ""))) else None
    except Exception:
        return None