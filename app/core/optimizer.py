# -*- coding: utf-8 -*-
"""系统优化引擎（v5.4）：声明式 Tweaks 清单 + 可逆执行 + 快照还原。

清单基底移植自 WinUtil `config/tweaks.json`（MIT，67 项甄选约 35 项），
键路径经 Winhance 源码交叉验证；执行框架为本项目独立实现。

TWEAKS 条目 schema（registry 对齐 winutil）：
  { id, name, desc, group, risk: low|mid|high, explorer_restart: bool,
    registry: [(ps_path, value_name, value, kind)],       # kind: DWord/SZ/ExpandSZ/QWord
    services: [(name, startup)],                          # startup: Disabled/Manual/Automatic
    invoke: [ps_lines], undo: [ps_lines] }                # 附加 PS 片段（可选）

可逆机制：
- apply 前对将写的每个键值动态读取原值 → tweak_snapshot.json；
- revert 读快照：原值存在 → 写回；原不存在（existed=false）→ 删除；
- 服务快照记录原启动类型；电源计划记录原活动 GUID。
"""

import ctypes
import json
import re
import os
import threading
import time

from . import logger as applog
from .platform import is_admin as platform_is_admin, has_winget
from .runner import run, run_elevated, run_powershell_json

log = applog.get_logger("optimizer")


def _arr(x):
    """PowerShell ConvertTo-Json 单元素数组折叠还原。"""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _hw_json(script, timeout=60):
    return run_powershell_json(script, timeout=timeout)

DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                        "LocalToolbox")
SNAPSHOT_PATH = os.path.join(DATA_DIR, "tweak_snapshot.json")

_lock = threading.Lock()
_snapshot_lock = threading.Lock()

_P = "HKCU:"  # 常用前缀简写
_HKLM = "HKLM:"


def _r(path, name, value, kind="DWord"):
    return {"path": path, "name": name, "value": value, "kind": kind}


# ---------------------------------------------------------------------------
# TWEAKS 清单（registry/services 移植自 WinUtil tweaks.json，值为「优化值」；
# 原值在 apply 时动态探测入快照——比 winutil 的静态 OriginalValue 更可靠）
# ---------------------------------------------------------------------------

T_TELEMETRY = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion"
T_POL = "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows"
T_ADV = T_TELEMETRY + "\\Explorer\\Advanced"

TWEAKS = [
    # ---- 隐私与遥测 -------------------------------------------------------
    {
        "id": "telemetry", "name": "关闭遥测与数据收集",
        "desc": "停止向微软发送诊断数据（含广告 ID、个性化输入、反馈请求），并禁用遥测服务。",
        "group": "privacy", "risk": "high",
        "registry": [
            _r(T_TELEMETRY + "\\AdvertisingInfo", "Enabled", 0),
            _r(T_TELEMETRY + "\\Privacy", "TailoredExperiencesWithDiagnosticDataEnabled", 0),
            _r(T_TELEMETRY + "\\Speech_OneCore\\Settings\\OnlineSpeechPrivacy", "HasAccepted", 0),
            _r("HKCU:\\Software\\Microsoft\\Input\\TIPC", "Enabled", 0),
            _r("HKCU:\\Software\\Microsoft\\InputPersonalization", "RestrictImplicitInkCollection", 1),
            _r("HKCU:\\Software\\Microsoft\\InputPersonalization", "RestrictImplicitTextCollection", 1),
            _r("HKCU:\\Software\\Microsoft\\InputPersonalization\\TrainedDataStore", "HarvestContacts", 0),
            _r("HKCU:\\Software\\Microsoft\\Personalization\\Settings", "AcceptedPrivacyPolicy", 0),
            _r(T_POL + "\\DataCollection", "AllowTelemetry", 0),
            _r(T_ADV, "Start_TrackProgs", 0),
            _r("HKCU:\\Software\\Microsoft\\Siuf\\Rules", "NumberOfSIUFInPeriod", 0),
        ],
        "services": [("diagtrack", "Disabled")],
        "invoke": [
            "try { Set-MpPreference -SubmitSamplesConsent 2 -ErrorAction SilentlyContinue } catch {}",
            "Remove-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Siuf\\Rules' -Name PeriodInNanoSeconds -ErrorAction SilentlyContinue",
        ],
        "undo": [
            "try { Set-MpPreference -SubmitSamplesConsent 1 -ErrorAction SilentlyContinue } catch {}",
        ],
    },
    {
        "id": "consumer-features", "name": "关闭开始菜单推广与自动装应用",
        "desc": "停止推广应用自动安装与 Microsoft Store 内容推荐。",
        "group": "privacy", "risk": "low",
        "registry": [_r(T_POL + "\\CloudContent", "DisableWindowsConsumerFeatures", 1)],
    },
    {
        "id": "activity-history", "name": "关闭活动历史与时间线上传",
        "desc": "停止发布/上传用户活动（本地剪贴板历史不受影响）。",
        "group": "privacy", "risk": "low",
        "registry": [
            _r(T_POL + "\\System", "EnableActivityFeed", 1),
            _r(T_POL + "\\System", "PublishUserActivities", 0),
            _r(T_POL + "\\System", "UploadUserActivities", 0),
        ],
    },
    {
        "id": "location", "name": "关闭位置跟踪",
        "desc": "禁止应用获取位置并关闭位置服务。",
        "group": "privacy", "risk": "low",
        "registry": [
            _r("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\location", "Value", "Deny", "SZ"),
            _r("HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Sensor\\Overrides\\{BFA794E4-F964-4FDB-90F6-51056BFE4B44}", "SensorPermissionState", 0),
            _r("HKLM:\\SYSTEM\\Maps", "AutoUpdateEnabled", 0),
        ],
        "services": [("lfsvc", "Disabled")],
    },
    {
        "id": "copilot", "name": "禁用 Copilot 与 Windows AI",
        "desc": "卸载 Copilot 包、禁用 Recall 与记事本 AI、隐藏 AI 设置页。",
        "group": "privacy", "risk": "mid",
        "registry": [
            _r("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\Explorer", "SettingsPageVisibility", "hide:aicomponents", "SZ"),
            _r("HKLM:\\SOFTWARE\\Policies\\WindowsNotepad", "DisableAIFeatures", 1),
        ],
        "invoke": [
            "$Appx = (Get-AppxPackage MicrosoftWindows.Client.CoreAI -ErrorAction SilentlyContinue).PackageFullName",
            "if ($Appx) { Remove-AppxPackage $Appx -ErrorAction SilentlyContinue }",
            "Get-AppxPackage -AllUsers '*Copilot*' -ErrorAction SilentlyContinue | Remove-AppxPackage -AllUsers -ErrorAction SilentlyContinue",
            "try { Disable-WindowsOptionalFeature -FeatureName Recall -Online -NoRestart -ErrorAction SilentlyContinue } catch {}",
            "try { Set-Service -Name WSAIFabricSvc -StartupType Disabled -ErrorAction SilentlyContinue } catch {}",
        ],
    },
    {
        "id": "input-personalization", "name": "关闭输入个性化云同步",
        "desc": "停止输入习惯（词库/手写）与云端个性化同步。",
        "group": "privacy", "risk": "low",
        "registry": [
            _r("HKCU:\\Software\\Microsoft\\Personalization\\Settings", "AcceptedPrivacyPolicy", 0),
        ],
    },
    # ---- 任务栏与资源管理器 ----------------------------------------------
    {
        "id": "taskbar-left", "name": "任务栏图标左对齐（Win11）",
        "desc": "恢复 Win10 风格的任务栏左对齐布局。",
        "group": "explorer", "risk": "low", "explorer_restart": True,
        "registry": [_r(T_ADV, "TaskbarAl", 0)],
    },
    {
        "id": "taskbar-search-hide", "name": "隐藏任务栏搜索框",
        "desc": "移除任务栏上的搜索框/搜索图标。",
        "group": "explorer", "risk": "low", "explorer_restart": True,
        "registry": [_r(T_TELEMETRY + "\\Search", "SearchboxTaskbarMode", 0)],
    },
    {
        "id": "taskview-hide", "name": "隐藏任务视图按钮",
        "desc": "移除任务栏上的任务视图按钮。",
        "group": "explorer", "risk": "low", "explorer_restart": True,
        "registry": [_r(T_ADV, "ShowTaskViewButton", 0)],
    },
    {
        "id": "rightclick-classic", "name": "恢复 Win10 经典右键菜单（Win11）",
        "desc": "右键直接显示完整菜单（注销/重启资源管理器后生效）。",
        "group": "explorer", "risk": "low", "explorer_restart": True,
        "invoke": [
            "New-Item -Path 'HKCU:\\Software\\Classes\\CLSID\\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\\InprocServer32' -Value '' -Force | Out-Null",
        ],
        "undo": [
            "Remove-Item -Path 'HKCU:\\Software\\Classes\\CLSID\\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}' -Recurse -Force -ErrorAction SilentlyContinue",
        ],
    },
    {
        "id": "show-extensions", "name": "显示文件扩展名",
        "desc": "资源管理器显示 .exe/.png 等扩展名。",
        "group": "explorer", "risk": "low", "explorer_restart": True,
        "registry": [_r(T_ADV, "HideFileExt", 0)],
    },
    {
        "id": "hidden-files", "name": "显示隐藏文件",
        "desc": "资源管理器显示隐藏文件与系统文件。",
        "group": "explorer", "risk": "low", "explorer_restart": True,
        "registry": [_r(T_ADV, "Hidden", 1)],
    },
    {
        "id": "launch-this-pc", "name": "资源管理器默认打开「此电脑」",
        "desc": "打开资源管理器时显示此电脑而非快速访问。",
        "group": "explorer", "risk": "low",
        "registry": [_r(T_ADV, "LaunchTo", 1)],
    },
    {
        "id": "bing-disable", "name": "关闭开始菜单 Bing 网络搜索",
        "desc": "开始菜单搜索只显示本地结果，不再请求 Bing。",
        "group": "explorer", "risk": "low",
        "registry": [_r(T_TELEMETRY + "\\Search", "BingSearchEnabled", 0)],
    },
    {
        "id": "endtask-taskbar", "name": "任务栏右键「结束任务」",
        "desc": "右键任务栏程序时显示结束任务选项（Win11 22H2+）。",
        "group": "explorer", "risk": "low", "explorer_restart": True,
        "registry": [_r(T_ADV + "\\TaskbarDeveloperSettings", "TaskbarEndTask", 1)],
    },
    {
        "id": "numlock-on", "name": "开机默认开启 NumLock",
        "desc": "登录界面与系统内数字小键盘默认开启。",
        "group": "explorer", "risk": "low",
        "registry": [
            _r("HKCU:\\Control Panel\\Keyboard", "InitialKeyboardIndicators", "2", "SZ"),
        ],
        "invoke": [
            "Set-ItemProperty -Path 'HKU:\\.Default\\Control Panel\\Keyboard' -Name InitialKeyboardIndicators -Value '2' -ErrorAction SilentlyContinue",
        ],
    },
    {
        "id": "dark-theme", "name": "系统切换为深色主题",
        "desc": "系统与应用同时切换为深色模式。",
        "group": "explorer", "risk": "low",
        "registry": [
            _r("HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize", "AppsUseLightTheme", 0),
            _r("HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize", "SystemUsesLightTheme", 0),
        ],
    },
    # ---- 性能与视觉 -------------------------------------------------------
    {
        "id": "visual-performance", "name": "视觉效果调至最佳性能",
        "desc": "关闭窗口动画/菜单延迟/AeroPeek 等视觉特效（外观会变朴素）。",
        "group": "performance", "risk": "mid",
        "registry": [
            _r("HKCU:\\Control Panel\\Desktop", "DragFullWindows", "0", "SZ"),
            _r("HKCU:\\Control Panel\\Desktop", "MenuShowDelay", "200", "SZ"),
            _r("HKCU:\\Control Panel\\Desktop\\WindowMetrics", "MinAnimate", "0", "SZ"),
            _r("HKCU:\\Control Panel\\Keyboard", "KeyboardDelay", 0),
            _r(T_ADV, "ListviewAlphaSelect", 0),
            _r(T_ADV, "ListviewShadow", 0),
            _r(T_ADV, "TaskbarAnimations", 0),
            _r("HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\VisualEffects", "VisualFXSetting", 3),
            _r("HKCU:\\Software\\Microsoft\\Windows\\DWM", "EnableAeroPeek", 0),
        ],
        "invoke": [
            "Set-ItemProperty -Path 'HKCU:\\Control Panel\\Desktop' -Name UserPreferencesMask -Type Binary -Value ([byte[]](144,18,3,128,16,0,0,0))",
        ],
        "undo": [
            "Remove-ItemProperty -Path 'HKCU:\\Control Panel\\Desktop' -Name UserPreferencesMask -ErrorAction SilentlyContinue",
        ],
    },
    {
        "id": "power-plan", "name": "切换到高性能电源计划",
        "desc": "基于卓越性能/高性能复制一份电源计划并启用（原计划记录可还原）。",
        "group": "performance", "risk": "low",
        "invoke": [
            "$src = 'e9a42b02-d5df-448d-aa00-03f14749eb61';",
            "$cur = (powercfg /getactivescheme) -replace '.*GUID: ([a-f0-9-]+).*', '$1';",
            "Set-Content -Path 'HKCU:\\_lt_pp_guid' -Value $cur -ErrorAction SilentlyContinue;",
            "$new = (powercfg -duplicatescheme $src) -replace '.*GUID: ([a-f0-9-]+).*', '$1';",
            "powercfg -changename $new 'LocalToolbox 高性能';",
            "powercfg -setactive $new;",
            "Write-Output \"LT_PP_NEW=$new\"",
        ],
        "undo": [
            "powercfg -setactive $env:LT_PP_ORIG;",
        ],
    },
    {
        "id": "game-mode", "name": "启用游戏模式",
        "desc": "游戏时系统自动优先分配资源。",
        "group": "performance", "risk": "low",
        "registry": [
            _r("HKCU:\\Software\\Microsoft\\GameBar", "AllowAutoGameMode", 1),
            _r("HKCU:\\Software\\Microsoft\\GameBar", "AutoGameModeEnabled", 1),
        ],
    },
    {
        "id": "game-dvr", "name": "关闭游戏录制（GameDVR）",
        "desc": "关闭 Xbox Game Bar 后台录制，减少游戏性能损耗。",
        "group": "performance", "risk": "low",
        "registry": [
            _r("HKCU:\\System\\GameConfigStore", "GameDVR_Enabled", 0),
            _r("HKCU:\\SOFTWARE\\Policies\\Microsoft\\Windows\\GameDVR", "AllowGameDVR", 0),
        ],
    },
    {
        "id": "long-paths", "name": "启用长路径支持（>260 字符）",
        "desc": "解除 Win32 路径长度限制（需应用自身支持）。",
        "group": "performance", "risk": "low",
        "registry": [_r("HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem", "LongPathsEnabled", 1)],
    },
    # ---- 服务优化 ---------------------------------------------------------
    {
        "id": "svc-diagtrack", "name": "禁用诊断跟踪服务",
        "desc": "禁用 Connected User Experiences and Telemetry（DiagTrack）与设备管理 WAP 推送服务。",
        "group": "services", "risk": "low",
        "services": [("DiagTrack", "Disabled"), ("dmwappushservice", "Disabled")],
    },
    {
        "id": "svc-manual", "name": "非核心服务转手动 + SvcHost 优化",
        "desc": "离线文件/地图代理/共享访问等转手动，按内存调整 SvcHost 分裂阈值。",
        "group": "services", "risk": "mid",
        "services": [
            ("MapsBroker", "Manual"), ("StorSvc", "Manual"),
            ("CscService", "Disabled"), ("SharedAccess", "Disabled"),
        ],
        "invoke": [
            "$mem = (Get-CimInstance Win32_PhysicalMemory | Measure-Object Capacity -Sum).Sum / 1KB;",
            "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control' -Name SvcHostSplitThresholdInKB -Value $mem",
        ],
    },
    {
        "id": "sysmain", "name": "禁用 SysMain（Superfetch）",
        "desc": "关闭预读取服务。固态硬盘收益明显；机械硬盘可能变慢，请酌情。",
        "group": "services", "risk": "mid",
        "services": [("SysMain", "Disabled")],
    },
    {
        "id": "wsearch", "name": "禁用 Windows 搜索索引",
        "desc": "关闭文件内容索引服务（开始菜单搜索应用仍可用）。",
        "group": "services", "risk": "mid",
        "services": [("WSearch", "Disabled")],
    },
    # ---- 网络 --------------------------------------------------------------
    {
        "id": "delivery-opt", "name": "传递优化仅限本机",
        "desc": "停止用你的带宽向其他电脑上传更新。",
        "group": "network", "risk": "low",
        "registry": [_r(T_POL + "\\DeliveryOptimization", "DODownloadMode", 0)],
    },
    {
        "id": "ipv4-prefer", "name": "IPv4 优先于 IPv6",
        "desc": "未配置 IPv6 的内网中可降低解析延迟（前缀策略调整）。",
        "group": "network", "risk": "mid",
        "registry": [_r("HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip6\\Parameters", "DisabledComponents", 32)],
    },
    # ---- 高级（谨慎） -------------------------------------------------------
    {
        "id": "edge-debloat", "name": "Edge 去广告化（保留浏览器）",
        "desc": "关闭 Edge 遥测/购物助手/Rewards/首次运行体验等 17 项策略（不卸载 Edge）。",
        "group": "advanced", "risk": "mid",
        "registry": [
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\EdgeUpdate", "CreateDesktopShortcutDefault", 0),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "PersonalizationReportingEnabled", 0),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "ShowRecommendationsEnabled", 0),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "HideFirstRunExperience", 1),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "UserFeedbackAllowed", 0),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "ConfigureDoNotTrack", 1),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "AlternateErrorPagesEnabled", 0),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "EdgeCollectionsEnabled", 0),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "EdgeShoppingAssistantEnabled", 0),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "MicrosoftEdgeInsiderPromotionEnabled", 0),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "ShowMicrosoftRewards", 0),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "WebWidgetAllowed", 0),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "DiagnosticData", 0),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "EdgeAssetDeliveryServiceEnabled", 0),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "WalletDonationEnabled", 0),
            _r("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge", "DefaultBrowserSettingsCampaignEnabled", 0),
        ],
    },
    {
        "id": "hiber", "name": "关闭休眠（释放 hiberfil.sys）",
        "desc": "删除休眠文件释放数 GB 空间。笔记本如需休眠/快速启动请勿开启。",
        "group": "advanced", "risk": "mid",
        "registry": [
            _r("HKLM:\\System\\CurrentControlSet\\Control\\Session Manager\\Power", "HibernateEnabled", 0),
            _r("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FlyoutMenuSettings", "ShowHibernateOption", 0),
        ],
        "invoke": ["powercfg.exe /hibernate off"],
        "undo": ["powercfg.exe /hibernate on"],
    },
    {
        "id": "lockscreen", "name": "跳过锁定屏幕",
        "desc": "开机/唤醒直接进入登录界面。",
        "group": "advanced", "risk": "mid",
        "registry": [_r(T_POL + "\\Personalization", "NoLockScreen", 1)],
    },
    {
        "id": "bgapps", "name": "禁用 UWP 应用后台运行",
        "desc": "所有商店应用不再后台运行（Win11 需逐项的会统一关闭总开关）。",
        "group": "advanced", "risk": "mid",
        "registry": [_r("HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\BackgroundAccessApplications", "GlobalUserDisabled", 1)],
    },
    {
        "id": "storage-sense", "name": "关闭存储感知",
        "desc": "停止系统自动删除临时文件（如需自动清理请勿开启）。",
        "group": "advanced", "risk": "low",
        "registry": [_r("HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\StorageSense\\Parameters\\StoragePolicy", "01", 0)],
    },
    {
        "id": "utc-time", "name": "硬件时钟使用 UTC（双系统）",
        "desc": "Linux/Windows 双系统时间不一致时使用。",
        "group": "advanced", "risk": "mid",
        "registry": [_r("HKLM:\\SYSTEM\\CurrentControlSet\\Control\\TimeZoneInformation", "RealTimeIsUniversal", 1, "QWord")],
    },
    {
        "id": "teredo", "name": "禁用 Teredo 隧道",
        "desc": "关闭 IPv6 过渡隧道，可降低部分游戏延迟。",
        "group": "advanced", "risk": "mid",
        "registry": [_r("HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip6\\Parameters", "DisabledComponents", 1)],
        "invoke": ["netsh interface teredo set state disabled"],
        "undo": ["netsh interface teredo set state default"],
    },
    {
        "id": "bsod-verbose", "name": "蓝屏显示详细信息",
        "desc": "蓝屏时显示更多技术信息（替代 emoticon）。",
        "group": "advanced", "risk": "low",
        "registry": [
            _r("HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CrashControl", "DisplayParameters", 1),
            _r("HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CrashControl", "DisableEmoticon", 1),
        ],
    },
    {
        "id": "mouse-acc", "name": "关闭鼠标加速",
        "desc": "指针移动与物理移动 1:1（FPS 游戏常用）。",
        "group": "advanced", "risk": "low",
        "registry": [
            _r("HKCU:\\Control Panel\\Mouse", "MouseSpeed", "0", "SZ"),
            _r("HKCU:\\Control Panel\\Mouse", "MouseThreshold1", "0", "SZ"),
            _r("HKCU:\\Control Panel\\Mouse", "MouseThreshold2", "0", "SZ"),
        ],
    },
    {
        "id": "wpbt", "name": "禁用 WPBT 厂商开机执行",
        "desc": "阻止电脑厂商通过 WPBT 在开机时强制运行程序。",
        "group": "advanced", "risk": "low",
        "registry": [_r("HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager", "DisableWpbtExecution", 1)],
    },
    {
        "id": "device-meta", "name": "阻止设备连接时自动装软件",
        "desc": "插入设备不再从网络获取厂商配套软件。",
        "group": "advanced", "risk": "low",
        "registry": [_r(T_POL + "\\Device Metadata", "PreventDeviceMetadataFromNetwork", 1)],
    },
    {
        "id": "sticky-keys", "name": "关闭粘滞键",
        "desc": "连按 Shift 不再弹出粘滞键窗口。",
        "group": "advanced", "risk": "low",
        "registry": [_r("HKCU:\\Control Panel\\Accessibility\\StickyKeys", "Flags", "506", "SZ")],
    },
    {
        "id": "verbose-logon", "name": "开关机显示详细状态",
        "desc": "启动/关机/注销时显示详细消息。",
        "group": "advanced", "risk": "low",
        "registry": [_r("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System", "VerboseStatus", 1)],
    },
    {
        "id": "scrollbars", "name": "滚动条常显",
        "desc": "UWP 应用滚动条始终显示。",
        "group": "advanced", "risk": "low",
        "registry": [_r("HKCU:\\Control Panel\\Accessibility", "DynamicScrollbars", 0)],
    },
]

TWEAK_MAP = {t["id"]: t for t in TWEAKS}

PRESETS = {
    "lite": [t["id"] for t in TWEAKS if t["risk"] == "low"],
    "recommended": [t["id"] for t in TWEAKS if t["risk"] in ("low", "mid")
                    and t["id"] not in ("visual-performance", "dark-theme",
                                        "taskbar-left", "taskbar-search-hide",
                                        "taskview-hide", "hidden-files")],
    "deep": [t["id"] for t in TWEAKS if t["risk"] != "high"
             and t["id"] not in ("dark-theme",)],
}

GROUP_NAMES = {
    "privacy": "隐私与遥测", "explorer": "任务栏与资源管理器",
    "performance": "性能与游戏", "services": "服务优化",
    "network": "网络", "advanced": "高级（谨慎）",
}


def is_admin():
    """兼容别名：收敛至 platform 单点探测（v5.4 O12）。"""
    return platform_is_admin()


def _tweak_admin(t):
    """是否需要管理员：HKLM 键 / 服务 / invoke 含提权命令。"""
    if t.get("services"):
        return True
    for r in t.get("registry", []):
        if r["path"].upper().startswith("HKLM"):
            return True
    for s in t.get("invoke", []) + t.get("undo", []):
        if any(k in s for k in ("Set-MpPreference", "Set-Service", "powercfg",
                                "DISM", "netsh", "-AllUsers", "Disable-Windows")):
            return True
    return False


# -- 快照 -------------------------------------------------------------------

def _snapshot_load():
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"entries": []}


def _snapshot_save(snap):
    os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)


def _snapshot_append(entries):
    with _snapshot_lock:
        snap = _snapshot_load()
        old = {e["key"] for e in snap.get("entries", [])}
        for e in entries:
            if e["key"] not in old:   # 首次修改才记录原值
                snap.setdefault("entries", []).append(e)
        _snapshot_save(snap)


# -- 探测 -------------------------------------------------------------------

def _detect_ps():
    """单条 PS 脚本读出全部条目当前值 + 服务启动类型 → JSON。"""
    lines = ["$ErrorActionPreference='SilentlyContinue';", "$out = @()"]
    for t in TWEAKS:
        for r in t.get("registry", []):
            p = r["path"].replace("'", "''")
            n = r["name"].replace("'", "''")
            lines.append(
                f"$v = (Get-ItemProperty -Path '{p}' -Name '{n}' -ErrorAction SilentlyContinue).'{n}';"
                f"$out += [pscustomobject]@{{ id='{t['id']}'; n='{n}'; v=$v }}")
        for s in t.get("services", []):
            lines.append(
                f"$sv = (Get-Service -Name '{s[0]}' -ErrorAction SilentlyContinue).StartType;"
                f"$out += [pscustomobject]@{{ id='{t['id']}'; n='{s[0]}'; v=[string]$sv }}")
    lines.append("$out | ConvertTo-Json -Compress")
    return "\n".join(lines)


def _entry_on(t, cur):  # cur: {键名或服务名: 当前值字符串}
    for r in t.get("registry", []):
        v = cur.get(r["name"])
        if v is None or str(v) != str(r["value"]):
            return False
    for s in t.get("services", []):
        if str(cur.get(s[0], "")).lower() != s[1].lower():
            return False
    return True


def statuses():
    """返回 {id: {on: bool, admin: bool}}；invoke 型项无法探测则 on=None 未知。"""
    admin = is_admin()
    result = {}
    need_detect = [t for t in TWEAKS if t.get("registry") or t.get("services")]
    cur = {}
    if need_detect:
        data = run(["powershell", "-NoProfile", "-Command", _detect_ps()],
                   timeout=90)
        if data.ok and data.stdout.strip():
            import json as _json
            try:
                parsed = _json.loads(data.stdout.strip(), strict=False)
                items = parsed if isinstance(parsed, list) else [parsed]
                for it in items:
                    if isinstance(it, dict) and it.get("n"):
                        cur[it["n"]] = it.get("v")
            except ValueError:
                log.warning("优化项状态解析失败")
    for t in TWEAKS:
        on = None
        if t.get("registry") or t.get("services"):
            on = _entry_on(t, cur)
        result[t["id"]] = {"on": on, "admin": _tweak_admin(t),
                           "risk": t["risk"], "group": t["group"]}
    return result


# -- 应用 / 还原 -------------------------------------------------------------

_KIND_PS = {"DWord": "DWord", "SZ": "String", "ExpandSZ": "ExpandString",
            "QWord": "QWord", "Binary": "Binary"}


def _build_apply_ps(t, ids_tag):
    """单项应用脚本（含快照读取原值输出 JSON 行 LT_SNAP:）。"""
    ps = [f"# tweak: {t['id']}"]
    snap_items = []
    for r in t.get("registry", []):
        p = r["path"].replace("'", "''")
        n = r["name"].replace("'", "''")
        kind = _KIND_PS.get(r["kind"], "DWord")
        val = r["value"]
        if isinstance(val, str):
            vexpr = f"'{val.replace(chr(39), chr(39) * 2)}'"
        else:
            vexpr = str(val)
        ps.append(f"$k = Get-Item -Path '{p}' -ErrorAction SilentlyContinue;")
        ps.append(f"$old = (Get-ItemProperty -Path '{p}' -Name '{n}'"
                  f" -ErrorAction SilentlyContinue).'{n}';")
        ps.append(f"$existed = [bool]($k -and (Get-ItemProperty -Path '{p}' -Name '{n}'"
                  f" -ErrorAction SilentlyContinue) -ne $null);")
        ps.append("Write-Output ('LT_SNAP:' + (ConvertTo-Json -Compress -InputObject"
                  f" @{{ key='{t['id']}|{n}'; id='{t['id']}'; path='{p}'; name='{n}';"
                  f" kind='{kind}'; existed=$existed;"
                  " old=$(if ($null -ne $old) { $old } else { $null }) }));")
        ps.append(f"if (!(Test-Path '{p}')) {{ New-Item -Path '{p}' -Force | Out-Null }};")
        ps.append(f"New-ItemProperty -Path '{p}' -Name '{n}' -Value {vexpr}"
                  f" -PropertyType {kind} -Force | Out-Null;")
        snap_items.append(n)
    for s in t.get("services", []):
        ps.append(f"$sv = Get-Service -Name '{s[0]}' -ErrorAction SilentlyContinue;")
        ps.append("if ($sv) { Write-Output ('LT_SNAP:' + (ConvertTo-Json -Compress"
                  f" -InputObject @{{ key='{t['id']}|svc:{s[0]}'; id='{t['id']}';"
                  f" svc='{s[0]}'; existed=$true; old=[string]$sv.StartType }}));"
                  f" Set-Service -Name '{s[0]}' -StartupType {s[1]} }}")
    for line in t.get("invoke", []):
        ps.append(line)
    if t.get("explorer_restart"):
        ps.append("Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue")
    return "\n".join(ps)


def _build_revert_ps(t, snap_map):
    """单项还原脚本：优先快照原值；无快照时用清单 undo 片段。"""
    ps = [f"# revert: {t['id']}"]
    has_snapshot = any(k.startswith(t["id"] + "|") for k in snap_map)
    for r in t.get("registry", []):
        key = f"{t['id']}|{r['name']}"
        p = r["path"].replace("'", "''")
        n = r["name"].replace("'", "''")
        e = snap_map.get(key)
        if e:
            kind = _KIND_PS.get(e.get("kind"), "DWord")
            if not e.get("existed"):
                ps.append(f"Remove-ItemProperty -Path '{p}' -Name '{n}'"
                          " -ErrorAction SilentlyContinue;")
            else:
                old = e.get("old")
                vexpr = f"'{old}'" if isinstance(old, str) else str(old)
                ps.append(f"if (Test-Path '{p}') {{ New-ItemProperty -Path '{p}'"
                          f" -Name '{n}' -Value {vexpr} -PropertyType {kind}"
                          " -Force | Out-Null };")
        else:
            ps.append(f"# 无快照，回退默认（原 winutil 值不可知，跳过 {n}）")
    for s in t.get("services", []):
        key = f"{t['id']}|svc:{s[0]}"
        e = snap_map.get(key)
        if e and e.get("old"):
            ps.append(f"try {{ Set-Service -Name '{s[0]}' -StartupType {e['old']}"
                      " } catch {}")
        else:
            ps.append(f"try {{ Set-Service -Name '{s[0]}' -StartupType Manual"
                      " } catch {}")
    if not has_snapshot:
        for line in t.get("undo", []):
            ps.append(line)
    if t.get("explorer_restart"):
        ps.append("Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue")
    return "\n".join(ps)


def _parse_snap_output(stdout):
    out = []
    for line in (stdout or "").splitlines():
        if line.startswith("LT_SNAP:"):
            try:
                e = json.loads(line[8:], strict=False)
                if e.get("key"):
                    out.append(e)
            except ValueError:
                pass
    return out


def apply_tweaks(ids, progress_cb=None):
    """应用所选优化项。返回 (ok, results, err)。"""
    tweaks = [TWEAK_MAP[i] for i in ids if i in TWEAK_MAP]
    if not tweaks:
        return False, [], "请先选择优化项"
    results, all_snap = [], []
    total = len(tweaks)
    # 先统一跑快照探测（普通权限可读 HKCU，HKLM 读也需要读权限——Get-ItemProperty 读 HKLM 一般允许）
    snap_ps_parts = []
    for t in tweaks:
        for r in t.get("registry", []):
            p = r["path"].replace("'", "''")
            n = r["name"].replace("'", "''")
            snap_ps_parts.append(
                f"$old = (Get-ItemProperty -Path '{p}' -Name '{n}'"
                f" -ErrorAction SilentlyContinue).'{n}';"
                f"$existed = $null -ne $old;"
                "Write-Output ('LT_SNAP:' + (ConvertTo-Json -Compress -InputObject"
                f" @{{ key='{t['id']}|{n}'; id='{t['id']}'; path='{p}'; name='{n}';"
                f" kind='{_KIND_PS.get(r['kind'], 'DWord')}'; existed=$existed;"
                " old=$(if ($null -ne $old) { $old } else { $null }) }));")
        for s in t.get("services", []):
            snap_ps_parts.append(
                f"$sv = Get-Service -Name '{s[0]}' -ErrorAction SilentlyContinue;"
                "if ($sv) { Write-Output ('LT_SNAP:' + (ConvertTo-Json -Compress"
                f" -InputObject @{{ key='{t['id']}|svc:{s[0]}'; id='{t['id']}';"
                f" svc='{s[0]}'; existed=$true; old=[string]$sv.StartType }})) }}")
    if snap_ps_parts:
        r = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-Command", "\n".join(snap_ps_parts)], timeout=120)
        all_snap = _parse_snap_output(r.stdout)
    _snapshot_append(all_snap)
    snap_map = {e["key"]: e for e in all_snap}

    done = 0
    for t in tweaks:
        admin = _tweak_admin(t)
        ps = _build_apply_ps(t, t["id"])
        if admin:
            if not is_admin():
                results.append({"id": t["id"], "name": t["name"], "ok": False,
                                "err": "需要管理员权限"})
                done += 1
                if progress_cb:
                    progress_cb(done, total, t["name"])
                continue
            r = run_elevated(["powershell", "-NoProfile", "-ExecutionPolicy",
                              "Bypass", "-Command", ps], timeout=600)
            ok = r.returncode == 0
            err = "" if ok else (r.output or r.stderr or "").strip()[:300] \
                or "提权执行失败（可能拒绝 UAC）"
        else:
            r = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-Command", ps], timeout=300)
            ok = r.ok
            err = "" if ok else r.output.strip()[:300] or "执行失败"
        # 从应用输出补抓快照（apply 脚本也会输出 LT_SNAP）
        for e in _parse_snap_output(r.stdout):
            all_snap.append(e)
        results.append({"id": t["id"], "name": t["name"], "ok": ok,
                        "err": err})
        done += 1
        if progress_cb:
            progress_cb(done, total, t["name"])
    if all_snap:
        _snapshot_append(all_snap)
    log.info("应用优化项 %s：成功 %d / 失败 %d",
             ids, sum(1 for x in results if x["ok"]),
             sum(1 for x in results if not x["ok"]))
    return True, results, ""


def revert_tweaks(ids=None, progress_cb=None):
    """还原（不传 ids = 还原快照中的全部）。"""
    snap = _snapshot_load()
    entries = snap.get("entries", [])
    if ids:
        idset = set(ids)
        known_ids = {e["id"] for e in entries if e["id"] in idset}
        known_ids.update(i for i in (ids or []) if i in TWEAK_MAP)
    else:
        known_ids = {e["id"] for e in entries}
    tweaks = [TWEAK_MAP[i] for i in known_ids if i in TWEAK_MAP]
    if not tweaks:
        return False, [], "没有可还原的记录"
    snap_map = {e["key"]: e for e in entries}
    results = []
    total = len(tweaks)
    done = 0
    for t in tweaks:
        admin = _tweak_admin(t)
        ps = _build_revert_ps(t, snap_map)
        if admin:
            if not is_admin():
                results.append({"id": t["id"], "name": t["name"], "ok": False,
                                "err": "需要管理员权限"})
            else:
                r = run_elevated(["powershell", "-NoProfile", "-ExecutionPolicy",
                                  "Bypass", "-Command", ps], timeout=600)
                results.append({"id": t["id"], "name": t["name"],
                                "ok": r.returncode == 0,
                                "err": "" if r.returncode == 0
                                else (r.output or "").strip()[:300]})
        else:
            r = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-Command", ps], timeout=300)
            results.append({"id": t["id"], "name": t["name"], "ok": r.ok,
                            "err": "" if r.ok else r.output.strip()[:300]})
        done += 1
        if progress_cb:
            progress_cb(done, total, t["name"])
    log.info("还原优化项：成功 %d / 失败 %d",
             sum(1 for x in results if x["ok"]),
             sum(1 for x in results if not x["ok"]))
    return True, results, ""


def export_cfg(ids):
    return {"version": 1, "ids": list(ids)}


def import_cfg(data):
    if not isinstance(data, dict) or not isinstance(data.get("ids"), list):
        raise ValueError("配置格式不正确")
    valid = [i for i in data["ids"] if i in TWEAK_MAP]
    return valid


# -- 更新策略 ---------------------------------------------------------------

def update_policy_get():
    ps = (
        "$p = 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU';"
        "$no = (Get-ItemProperty -Path $p -Name NoAutoUpdate -ErrorAction"
        " SilentlyContinue).NoAutoUpdate;"
        "$def = (Get-ItemProperty -Path $p -Name DeferFeatureUpdatesPeriodInDays"
        " -ErrorAction SilentlyContinue).DeferFeatureUpdatesPeriodInDays;"
        "[pscustomobject]@{ no = $no; defer = $def } | ConvertTo-Json -Compress")
    r = run(["powershell", "-NoProfile", "-Command", ps], timeout=30)
    if not r.ok or not r.stdout.strip():
        return "unknown"
    try:
        d = json.loads(r.stdout.strip(), strict=False)
        if d.get("no") == 1:
            return "paused"
        if (d.get("defer") or 0) >= 300:
            return "security"
        return "default"
    except ValueError:
        return "unknown"


def update_policy_set(mode):
    """mode: default | security | paused。需要管理员。"""
    base = (
        "$p = 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU';"
        "if (!(Test-Path $p)) { New-Item -Path $p -Force | Out-Null };")
    if mode == "default":
        script = base + (
            "Set-ItemProperty -Path $p -Name NoAutoUpdate -Value 0;"
            "Remove-ItemProperty -Path $p -Name DeferFeatureUpdatesPeriodInDays"
            " -ErrorAction SilentlyContinue;"
            "Remove-ItemProperty -Path $p -Name DeferQualityUpdatesPeriodInDays"
            " -ErrorAction SilentlyContinue;")
    elif mode == "security":
        script = base + (
            "Set-ItemProperty -Path $p -Name NoAutoUpdate -Value 0;"
            "Set-ItemProperty -Path $p -Name DeferFeatureUpdatesPeriodInDays -Value 365;"
            "Set-ItemProperty -Path $p -Name DeferQualityUpdatesPeriodInDays -Value 0;"
            "Set-ItemProperty -Path $p -Name DeferFeatureUpdates -Value 1;")
    elif mode == "paused":
        script = base + (
            "Set-ItemProperty -Path $p -Name NoAutoUpdate -Value 1;")
    else:
        raise ValueError("mode 须为 default/security/paused")
    r = run_elevated(["powershell", "-NoProfile", "-Command", script], timeout=120)
    if r.returncode != 0:
        raise OSError((r.output or "").strip()[:200] or "提权执行失败（可能拒绝 UAC）")
    return True


# -- 垃圾清理 ----------------------------------------------------------------

CLEAN_ITEMS = [
    {"id": "user-temp", "name": "用户临时文件", "path": os.path.expandvars("%TEMP%"), "admin": False},
    {"id": "win-temp", "name": "Windows 临时文件", "path": os.path.expandvars("%SystemRoot%\\Temp"), "admin": True},
    {"id": "update-cache", "name": "Windows 更新缓存", "path": os.path.expandvars("%SystemRoot%\\SoftwareDistribution\\Download"), "admin": True},
    {"id": "wer", "name": "错误报告队列", "path": os.path.expandvars("%SystemRoot%\\ServiceProfiles\\LocalService\\AppData\\Local\\Temp"), "admin": True},
]


def _dir_size(path):
    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$s = (Get-ChildItem -LiteralPath '{path}' -Recurse -Force"
        " -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum;"
        "if ($null -eq $s) { 0 } else { $s }")
    r = run(["powershell", "-NoProfile", "-Command", ps], timeout=120)
    try:
        return int(r.stdout.strip() or 0)
    except ValueError:
        return 0


def clean_scan():
    out = []
    for item in CLEAN_ITEMS:
        out.append({"id": item["id"], "name": item["name"],
                    "size": _dir_size(item["path"]), "admin": item["admin"]})
    out.append({"id": "recycle", "name": "回收站", "size": None, "admin": False})
    out.append({"id": "dns", "name": "DNS 缓存", "size": 0, "admin": False})
    return out


def clean_run(ids, progress_cb=None):
    """执行清理。系统路径项合并进一次提权会话。"""
    idset = set(ids or [])
    normal, admin = [], []
    total = len(idset)
    done = 0

    def cb(name):
        nonlocal done
        done += 1
        if progress_cb:
            progress_cb(done, total, name)

    if "user-temp" in idset:
        normal.append(
            "Remove-Item -Path $Env:Temp\\* -Recurse -Force"
            " -ErrorAction SilentlyContinue")
        cb("用户临时文件")
    if {"win-temp", "update-cache", "wer"} & idset:
        admin.append("$ErrorActionPreference='SilentlyContinue';")
        if "win-temp" in idset:
            admin.append("Remove-Item -Path $Env:SystemRoot\\Temp\\* -Recurse -Force"
                         " -ErrorAction SilentlyContinue")
        if "update-cache" in idset:
            admin.append("Stop-Service -Name wuauserv,bits -Force"
                         " -ErrorAction SilentlyContinue;")
            admin.append("Remove-Item -Path"
                         " $Env:SystemRoot\\SoftwareDistribution\\Download\\*"
                         " -Recurse -Force -ErrorAction SilentlyContinue;")
            admin.append("Start-Service -Name wuauserv,bits"
                         " -ErrorAction SilentlyContinue")
        if "wer" in idset:
            admin.append("Remove-Item -Path"
                         " $Env:SystemRoot\\ServiceProfiles\\LocalService"
                         "\\AppData\\Local\\Temp\\* -Recurse -Force"
                         " -ErrorAction SilentlyContinue")
    if "recycle" in idset:
        normal.append("Clear-RecycleBin -Force -ErrorAction SilentlyContinue")
    if "dns" in idset:
        normal.append("ipconfig /flushdns | Out-Null")

    errs = []
    if normal:
        r = run(["powershell", "-NoProfile", "-Command",
                 "\n".join(normal)], timeout=600)
        if not r.ok:
            errs.append(r.output.strip()[:200] or "清理失败")
    if admin:
        if not is_admin():
            return False, "部分清理项需要管理员权限，请点状态栏右下角「提权重启」后重试"
        r = run_elevated(["powershell", "-NoProfile", "-Command",
                          "\n".join(admin)], timeout=600)
        if r.returncode != 0:
            errs.append((r.output or "").strip()[:200] or "提权清理失败")
    log.info("垃圾清理完成：ids=%s", ids)
    return (not errs), "；".join(errs)


# -- 系统修复 ----------------------------------------------------------------

FIXES = {
    "sfc": {"name": "系统文件检查（SFC）",
            "desc": "扫描并修复受保护的系统文件。耗时 5-15 分钟。",
            "cmd": "sfc /scannow"},
    "dism": {"name": "组件存储修复（DISM）",
             "desc": "修复 Windows 组件存储，SFC 无法修复时先跑这个。耗时 10-20 分钟。",
             "cmd": "DISM /Online /Cleanup-Image /RestoreHealth"},
    "netstack": {"name": "网络栈重置",
                 "desc": "重置 Winsock 与 TCP/IP（完成后需要重启电脑）。",
                 "cmd": "netsh winsock reset; netsh int ip reset; ipconfig /flushdns"},
    "wu-reset": {"name": "Windows 更新组件重置",
                 "desc": "停止更新服务并清空更新缓存后重启服务。",
                 "cmd": ("Stop-Service wuauserv,bits,cryptsvc -Force"
                         " -ErrorAction SilentlyContinue;"
                         " Remove-Item $Env:SystemRoot\\SoftwareDistribution\\*"
                         " -Recurse -Force -ErrorAction SilentlyContinue;"
                         " Start-Service bits,wuauserv,cryptsvc"
                         " -ErrorAction SilentlyContinue")},
    "winget": {"name": "WinGet 源修复",
               "desc": "重置并更新 WinGet 包源（需已安装 winget）。",
               "cmd": "winget source reset --force; winget source update"},
}


def fix_run(kind):
    if kind not in FIXES:
        raise ValueError("未知的修复项")
    cmd = FIXES[kind]["cmd"]
    if kind == "winget":
        probe = run(["where", "winget"], timeout=10)
        if not probe.ok:
            raise OSError("未检测到 winget，请先安装应用安装程序（应用安装程序/WinGet）")
    r = run_elevated(["powershell", "-NoProfile", "-Command",
                      f"$ErrorActionPreference='Continue'; {cmd};"
                      " Write-Output 'LT_FIX_DONE'"], timeout=3600)
    out = (r.stdout or "").strip()
    ok = r.returncode == 0 or "LT_FIX_DONE" in out
    return ok, out[-4000:]


# -- 应用管理（v5.4 二期：UWP + 可选功能 + 旧版能力，Winhance 三合一模式） --

# 框架/系统关键包保护名单（卸载会导致系统组件损坏，一律隐藏）
UWP_PROTECTED = (
    "Microsoft.WindowsStore", "Microsoft.VCLibs", "Microsoft.UI.Xaml",
    "Microsoft.NET.Native", "Microsoft.Services.Store",
    "Microsoft.StorePurchaseApp", "Microsoft.WindowsAppRuntime",
    "Microsoft.DesktopAppInstaller",   # 即 winget 本体
    "Microsoft.WindowsAppLifeCycle", "Microsoft.WebImageExtension",
)

UWP_SNAPSHOT_PATH = os.path.join(DATA_DIR, "uwp_snapshot.json")

_UWP_LIST_PS = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$out = Get-AppxPackage | Where-Object { -not $_.IsFramework } |"
    " ForEach-Object { [pscustomobject]@{ name = $_.Name;"
    "  full = $_.PackageFullName; loc = $_.InstallLocation;"
    "  publisher = $_.Publisher } };"
    "$out | ConvertTo-Json -Compress -Depth 3")


def _uwp_snapshot_load():
    try:
        with open(UWP_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"removed": []}


def _uwp_snapshot_save(snap):
    os.makedirs(os.path.dirname(UWP_SNAPSHOT_PATH), exist_ok=True)
    with open(UWP_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)


def uwp_list():
    """当前用户 Appx 包（过滤框架与保护名单）。"""
    data = _hw_json(_UWP_LIST_PS, timeout=60)
    if data is None:
        return []
    items = _arr(data)
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name") or ""
        if any(name.startswith(p) for p in UWP_PROTECTED):
            continue
        out.append({"name": name, "full": it.get("full") or "",
                    "publisher": (it.get("publisher") or "")[:60]})
    out.sort(key=lambda x: x["name"].lower())
    return out


def uwp_remove(names, progress_cb=None):
    """卸载 UWP：deprovision 先行（规避 Win10 0x80070002）→ -AllUsers。
    卸载前记录 manifest 位置到快照以便恢复。"""
    if not is_admin():
        raise OSError("卸载 UWP 应用需要管理员权限，请点状态栏右下角「提权重启」后重试")
    names = [str(n) for n in (names or []) if n]
    if not names:
        raise ValueError("请先选择要卸载的应用")
    snap = _uwp_snapshot_load()
    known = {r["name"] for r in snap.get("removed", [])}
    total = len(names)
    results = []
    done = 0
    # 一次提权会话完成全部：先探测 manifest，再 deprovision，再卸载
    ps = ["$ErrorActionPreference='Continue';"]
    for n in names:
        safe = n.replace("'", "''")
        ps.append(f"$pkg = Get-AppxPackage -AllUsers '{safe}'"
                  " -ErrorAction SilentlyContinue | Select-Object -First 1;")
        ps.append("if ($pkg) { Write-Output ('LT_UWP:' +"
                  " (ConvertTo-Json -Compress -InputObject"
                  f" @{{ name='{safe}'; full=$pkg.PackageFullName;"
                  " loc=$pkg.InstallLocation })) };"
                  f" Get-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue"
                  f" | Where-Object DisplayName -eq '{safe}'"
                  " | Remove-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue | Out-Null;")
        ps.append(f"Get-AppxPackage -AllUsers '{safe}'"
                  " | Remove-AppxPackage -AllUsers -ErrorAction SilentlyContinue")
    r = run_elevated(["powershell", "-NoProfile", "-Command",
                      "\n".join(ps)], timeout=1800)
    for line in (r.stdout or "").splitlines():
        if line.startswith("LT_UWP:"):
            try:
                e = json.loads(line[7:], strict=False)
                if e.get("name"):
                    results.append({"name": e["name"], "ok": True})
                    if e["name"] not in known:
                        snap["removed"].append({
                            "name": e["name"], "full": e.get("full") or "",
                            "loc": e.get("loc") or "",
                            "ts": time.time()})
                    done += 1
                    if progress_cb:
                        progress_cb(done, total, e["name"])
            except ValueError:
                pass
    _uwp_snapshot_save(snap)
    missed = [n for n in names if n not in {x["name"] for x in results}]
    for n in missed:
        results.append({"name": n, "ok": False, "err": "未找到包或卸载失败"})
    return results


def uwp_removed_list():
    """快照中已卸载的应用（可恢复）。"""
    snap = _uwp_snapshot_load()
    return snap.get("removed", [])


def uwp_restore(name):
    """从快照恢复已卸载的 UWP（Register manifest）。"""
    snap = _uwp_snapshot_load()
    rec = next((r for r in snap.get("removed", []) if r["name"] == str(name)), None)
    if not rec:
        raise ValueError("快照中没有该应用的记录")
    if not rec.get("loc") or not os.path.isdir(rec["loc"]):
        raise OSError("应用的安装目录已不存在，请从 Microsoft Store 重新安装")
    manifest = os.path.join(rec["loc"], "AppxManifest.xml")
    r = run(["powershell", "-NoProfile", "-Command",
             f"Add-AppxPackage -Register '{manifest}'"
             " -DisableDevelopmentMode -ErrorAction Stop"], timeout=300)
    if not r.ok:
        raise OSError("恢复失败：" + (r.output or "").strip()[:200])
    snap["removed"] = [x for x in snap.get("removed", []) if x["name"] != name]
    _uwp_snapshot_save(snap)
    return True


def opt_features_list():
    """Windows 可选功能（管理员；DISM 查询）。"""
    if not is_admin():
        raise OSError("查询可选功能需要管理员权限")
    data = _hw_json(
        "$ErrorActionPreference='SilentlyContinue';"
        "$out = Get-WindowsOptionalFeature -Online"
        " | Where-Object { $_.FeatureName -and $_.State -ne 'DisabledWithPayloadRemoved' }"
        " | ForEach-Object { [pscustomobject]@{ name = $_.FeatureName;"
        "  state = [string]$_.State } };"
        "$out | ConvertTo-Json -Compress -Depth 3", timeout=120)
    if data is None:
        raise OSError("可选功能查询失败")
    return [{"name": f.get("name") or "", "state": f.get("state") or ""}
            for f in _arr(data) if isinstance(f, dict)]


def opt_feature_set(name, enable):
    """启用/禁用可选功能（管理员）。"""
    verb = "Enable" if enable else "Disable"
    safe = str(name or "").replace("'", "''")
    r = run_elevated(["powershell", "-NoProfile", "-Command",
                      f"{verb}-WindowsOptionalFeature -Online"
                      f" -FeatureName '{safe}' -NoRestart"
                      " -ErrorAction Stop; Write-Output 'LT_OK'"], timeout=900)
    if "LT_OK" not in (r.stdout or ""):
        raise OSError((r.output or "").strip()[:200] or "操作失败（可能需重启生效）")
    return True


def opt_caps_list():
    """旧版能力 / 按需组件（管理员）。"""
    if not is_admin():
        raise OSError("查询旧版能力需要管理员权限")
    data = _hw_json(
        "$ErrorActionPreference='SilentlyContinue';"
        "$out = Get-WindowsCapability -Online"
        " | Where-Object { $_.Name }"
        " | ForEach-Object { [pscustomobject]@{ name = $_.Name;"
        "  state = [string]$_.State } };"
        "$out | ConvertTo-Json -Compress -Depth 3", timeout=120)
    if data is None:
        raise OSError("旧版能力查询失败")
    return [{"name": c.get("name") or "", "state": c.get("state") or ""}
            for c in _arr(data) if isinstance(c, dict)]


def opt_cap_set(name, add):
    """添加/移除旧版能力（管理员）。"""
    verb = "Add" if add else "Remove"
    safe = str(name or "").replace("'", "''")
    r = run_elevated(["powershell", "-NoProfile", "-Command",
                      f"{verb}-WindowsCapability -Online"
                      f" -Name '{safe}' -ErrorAction Stop;"
                      " Write-Output 'LT_OK'"], timeout=1800)
    if "LT_OK" not in (r.stdout or ""):
        raise OSError((r.output or "").strip()[:200] or "操作失败")
    return True


# -- winget 软件一键安装（Winhance 分类思想） --------------------------------

WINGET_APPS = {
    "浏览器": [
        {"id": "Google.Chrome", "name": "Google Chrome"},
        {"id": "Mozilla.Firefox", "name": "Firefox"},
        {"id": "Brave.Brave", "name": "Brave"},
    ],
    "多媒体": [
        {"id": "VideoLAN.VLC", "name": "VLC 播放器"},
        {"id": "Spotify.Spotify", "name": "Spotify"},
        {"id": "Obsidian.Obsidian", "name": "Obsidian 笔记"},
    ],
    "文档与工具": [
        {"id": "SumatraPDF.SumatraPDF", "name": "SumatraPDF"},
        {"id": "7zip.7zip", "name": "7-Zip"},
        {"id": "Notepad++.Notepad++", "name": "Notepad++"},
        {"id": "Git.Git", "name": "Git"},
        {"id": "Microsoft.PowerShell", "name": "PowerShell 7"},
        {"id": "Microsoft.PowerToys", "name": "PowerToys"},
    ],
    "通讯": [
        {"id": "TelegramMessenger.TelegramDesktop", "name": "Telegram"},
        {"id": "Discord.Discord", "name": "Discord"},
    ],
}


def winget_available():
    """收敛至 platform 单点探测（v5.4 O12）。"""
    return has_winget()


def winget_upgrade_list():
    """可升级应用列表（解析 `winget upgrade` 表格输出；winget 无 JSON 能力）。

    返回 [{id, name, version, available}]；解析失败/无 winget 返回空并记录日志。
    """
    if not winget_available():
        return []
    # winget 重定向输出为 UTF-8（GBK 解码会出替换符乱码，v5.4 二期实测）
    r = run(["winget", "upgrade", "--disable-interactivity",
             "--accept-source-agreements"], timeout=300, encoding="utf-8")
    rows = []
    started = False
    for line in (r.stdout or "").splitlines():
        line = line.rstrip()
        if not started:
            # 表头行（含升级/Version 字样）的下一行为分隔线，其后才是数据
            if line and set(line) <= {"-", " ", "\t"} and len(line) > 10:
                started = True
            continue
        if not line.strip():
            break
        cols = re.split(r"\s{2,}", line.strip())
        if len(cols) < 3:
            continue
        # 兼容尾列缺失 Source 的情况：Name... Id Version Available [Source]
        if len(cols) >= 4:
            available = cols[-2]
            version = cols[-3]
            pkg_id = cols[-4]
            name = " ".join(cols[:-4]).strip()
        else:
            available = cols[-1]
            version = cols[-2]
            pkg_id = cols[-3]
            name = " ".join(cols[:-3]).strip()
        if not pkg_id or available in ("...", "N/A"):
            continue
        rows.append({"id": pkg_id, "name": name or pkg_id,
                     "version": version, "available": available})
    if not rows:
        log.debug("winget upgrade 解析为空（无升级或输出格式变化）")
    return rows


def winget_upgrade(app_ids, progress_cb=None):
    """逐个 winget 静默升级（复用 winget_install 的执行与结果结构）。"""
    if not winget_available():
        raise OSError("未检测到 winget，请先安装应用安装程序")
    ids = [str(i) for i in (app_ids or []) if i]
    if not ids:
        raise ValueError("请选择要升级的软件")
    total = len(ids)
    done = 0
    results = []
    for app_id in ids:
        r = run(["winget", "upgrade", "--id", app_id, "-e", "--silent",
                 "--accept-package-agreements", "--accept-source-agreements",
                 "--disable-interactivity"], timeout=1800, encoding="utf-8")
        ok = r.ok
        results.append({"id": app_id, "name": app_id, "ok": ok,
                        "err": "" if ok else (r.output or "").strip()[-200:]})
        done += 1
        if progress_cb:
            progress_cb(done, total, app_id)
    return results


def winget_install(app_ids, progress_cb=None):
    """逐个 winget 安装（普通权限，包级安装器自请求提升）。返回结果列表。"""
    if not winget_available():
        raise OSError("未检测到 winget，请先安装应用安装程序")
    ids = [str(i) for i in (app_ids or []) if i]
    if not ids:
        raise ValueError("请选择要安装的软件")
    total = len(ids)
    done = 0
    results = []
    for app_id in ids:
        name = next((a["name"] for cat in WINGET_APPS.values()
                     for a in cat if a["id"] == app_id), app_id)
        r = run(["winget", "install", "--id", app_id, "-e", "--silent",
                 "--accept-package-agreements", "--accept-source-agreements"],
                timeout=1800, encoding="utf-8")
        ok = r.ok
        results.append({"id": app_id, "name": name, "ok": ok,
                        "err": "" if ok else (r.output or "").strip()[-200:]})
        done += 1
        if progress_cb:
            progress_cb(done, total, name)
    return results
