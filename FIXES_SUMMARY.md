# LocalToolbox 修复总结

## 本次会话完成的修复

### 1. MRO 冲突修复（关键）
**问题**: Bridge 类继承多个混入类时出现方法解析顺序（MRO）冲突，导致应用无法启动。

**原因**: Bridge 类同时直接继承 `BridgeBase` 和多个也继承自 `BridgeBase` 的混入类，造成菱形继承冲突。

**修复**: 
- 文件: `app/bridge/bridge.py`
- 移除 Bridge 类对 `BridgeBase` 的直接继承
- 使用 `super().__init__()` 替代显式调用 `BridgeBase.__init__(self)`
- 让 Python 的 MRO 机制自动处理初始化链

**验证**: ✓ 应用可正常启动

---

### 2. 窗口管理修复
**问题**: 
- 区域截图遮罩窗口关闭后无法完全销毁
- 主窗口恢复时位置异常（"跟随鼠标"现象）

**修复**:
- 文件: `app/bridge/screenshot_api.py`, `app/bridge/recorder_api.py`, `app/bridge/hotkey_api.py`
- 使用 Win32 API `PostMessageW(hwnd, WM_CLOSE, 0, 0)` 强制关闭窗口
- 添加 50-100ms 延迟确保消息处理完成
- 窗口恢复序列: `show()` → `restore()` → `SetWindowPos(HWND_TOP)` → `SetForegroundWindow()`

**验证**: ✓ 窗口正确关闭和激活

---

### 3. 滚动截图兼容性修复
**问题**: Electron/UWP/DirectX 应用滚动截图产生空白图像（GDI BitBlt 不支持 GPU 加速内容）

**修复**:
- 文件: `app/core/scroll_capture.py`
- 新增 `_capture_via_printwindow()` 函数，使用 `PrintWindow(PW_RENDERFULLCONTENT)` API
- 重构 `_capture_window()` 为双方法策略：
  1. 优先尝试 PrintWindow（支持 DirectX/GPU）
  2. 失败时回退到 GDI BitBlt（传统快速方法）

**验证**: ✓ PrintWindow API 可用

---

### 4. 视频播放器协议修复
**问题**: `<video src="file://...">` 在 pywebview 中无法播放视频

**修复**:
- 文件: `app/bridge/video_api.py`, `webui/js/pages/video.js`
- 新增 `VideoFileHandler` HTTP 服务器类
- 选择视频文件时自动启动本地 HTTP 服务器（端口 8765-8774）
- 后端返回 `video_url`（HTTP 链接），前端优先使用

**验证**: ✓ HTTP 服务器方法存在

---

### 5. 编辑器标注功能统一
**问题**: 独立图片编辑器缺少截图编辑器的标注工具

**修复**:
- 文件: `webui/js/pages/editor.js`
- 实现双模式架构：
  - **滤镜模式**: 后端特效（模糊、亮度、水印等）
  - **标注模式**: 前端 Canvas 绘制（9 种工具）
- 标注工具: 画笔、高亮、矩形、椭圆、箭头、文字、马赛克、裁剪、橡皮
- 支持快照预览、撤销重做、颜色/大小调节

**验证**: ✓ 所有标注功能代码存在

---

## 启动方式

### 方法 1: 批处理文件（推荐）
```bash
双击 run.bat
```

### 方法 2: 命令行
```bash
cd C:\Users\86151\Documents\Code\smb-tool
python main.py
```

### 方法 3: 验证修复
```bash
python verify_fixes.py
```

---

## 测试建议

1. **窗口管理**:
   - 打开区域截图，确认遮罩窗口能正常关闭
   - 截图完成后确认主窗口正确恢复且位置正常

2. **滚动截图**:
   - 在 Chrome/Edge 等浏览器上测试滚动截图
   - 在 Electron 应用（如 VS Code）上测试滚动截图

3. **视频播放**:
   - 打开视频文件，确认能正常播放
   - 测试裁剪、转 GIF、提取帧功能

4. **图片编辑**:
   - 打开图片，点击"切换到标注模式"
   - 测试所有 9 种标注工具
   - 切换回滤镜模式，确认标注保留

---

## 技术细节

### MRO 冲突原理
Python 使用 C3 线性化算法计算方法解析顺序。当类继承结构中某个基类出现多次且顺序不一致时，无法生成合法的 MRO，抛出 `TypeError`。

**错误示例**:
```python
class Base: pass
class Mixin1(Base): pass
class Mixin2(Base): pass
class Child(Base, Mixin1, Mixin2): pass  # MRO 冲突！
```

**正确做法**:
```python
class Child(Mixin1, Mixin2): pass  # 通过 Mixin1/2 间接继承 Base
```

### PrintWindow vs GDI BitBlt
| 特性 | PrintWindow | GDI BitBlt |
|------|-------------|------------|
| DirectX 支持 | ✓ | ✗ |
| GPU 加速内容 | ✓ | ✗ |
| UWP 应用 | ✓ | ✗ |
| 速度 | 较慢 | 快 |
| 兼容性 | Win8+ | 所有 Windows |

---

## 文件清单

修改的文件:
- `app/bridge/bridge.py` - MRO 修复
- `app/bridge/screenshot_api.py` - 窗口管理
- `app/bridge/recorder_api.py` - 窗口管理
- `app/bridge/hotkey_api.py` - 窗口管理
- `app/core/scroll_capture.py` - PrintWindow API
- `app/bridge/video_api.py` - HTTP 服务器
- `webui/js/pages/video.js` - HTTP URL 支持
- `webui/js/pages/editor.js` - 标注模式

新增的文件:
- `run.bat` - 启动脚本
- `test.bat` - 测试脚本
- `verify_fixes.py` - 验证脚本
- `FIXES_SUMMARY.md` - 本文档

---

## 区域截图（圈选）修复 —— 第二轮

**用户现象**：可以划定区域，但后续无法操作（点「确认」没反应、ESC/「取消」退不出去），
且完全不知道截图是否成功。运行日志（`%APPDATA%\LocalToolbox\logs\app.log`）确认了三个根因。

### 1. 「确认」按钮点击无效（前端，关键）
**问题**: 拖选进入调整模式后，点工具条「确认 (Enter)」没有任何反应，选区还会消失。

**原因**: 工具条位于遮罩（overlay）内部，按钮的 `mousedown` 会冒泡到遮罩的
`onDown` 处理器；调整模式下点击点落在选区之外 → 走「选区外重新圈选」分支，
先把 `selRect` 清空并重建为零尺寸拖选，随后 mouseup 又按「误点」回到空闲。
等按钮的 `click` 事件到达 `finish()` 时 `selRect` 已为空 → 静默 return。

**修复**: `webui/js/pages/region_capture.js` 的 `onDown` 增加
`if (bar.contains(e.target)) return;` —— 工具条上的按下不参与圈选逻辑。

### 2. ESC / 「取消」按钮抛 TypeError（前端 + 后端）
**问题**: 按 ESC 或点「取消」后遮罩不关闭，界面卡死。日志：
`shot_overlay_cancel() takes from 1 to 2 positional arguments but 7 were given`。

**原因**: `overlay.html` 的 `call()` 固定按 6 个实参调用后端（未给出的补 `undefined`），
而 pywebview 会把 `undefined` 序列化成 `null` 一并转给 Python，
`shot_overlay_cancel(token)` 收到 6 个位置参数直接抛异常。

**修复**: `call()` 改为 `Array.prototype.slice.call(arguments, 1)` 只转发实际参数；
后端 `shot_overlay_cancel` / `shot_overlay_done` 增加 `*_extra` 容忍多余实参
（兼容旧打包版本的前端）。

### 3. 遮罩启动两次（前端）
**问题**: 日志中每次 ESC 都出现两条相差 1ms 的取消调用 —— 有两层遮罩叠放，
交互、键盘监听全部重复。

**原因**: `pywebviewready` 事件与 1500ms 兜底定时器都会调用 `start()`。

**修复**: `overlay.html` 增加 `started` 守卫，只允许启动一次。

### 4. 事件投递顺序（后端）
**问题**: 先销毁遮罩窗口再 emit `shot_pick`，遮罩窗口销毁可能与 js_api 调用线程
相互干扰导致事件偶发丢失。

**修复**: `shot_overlay_done` / `shot_overlay_cancel` 调整为先投递事件（并先唤回
主窗口），遮罩窗口销毁移入 `finally` 兜底。

### 5. 无反馈（体验）
- 确认后遮罩立刻变黑且无任何提示 → 新增「正在生成截图…」状态层（提交后仍可按 ESC 退出）；
- 后端 `ok:false` / 通信失败 / 15s 超时 → 遮罩顶部红色错误条；
- 后端处理异常时同样唤回主窗口并投递取消事件（不再出现「窗口消失且无提示」）。

### 验证
- `node test_region_ui.js`（新增）：DOM 桩回归 —— 拖选→确认/取消/Enter、
  引导脚本单次启动、后端调用参数个数、canvas 裁剪降级（17 项）；
  已验证该测试能捕获历史缺陷（临时还原旧代码即失败）。
- `python test_screenshot.py`：32 项通过（新增 4 项：事件顺序、失败恢复、
  多余实参容忍）。
- `python test_region_capture.py`：语法 + 交互回归全部通过。

---

## 编辑画布拖拽与窗口拖动 —— 最终方案

**用户现象（先后两次）**：
1. 画布上无法划线/标注，一按下拖动整个窗口跟着鼠标跑；
2. 按 1 的方案把 easy_drag 关掉后，整个窗口又完全拖不动了。

**根因分析**：
- pywebview 的 `easy_drag`（无边框窗口默认开启）在页面 `window` 上挂全局
  `mousedown`：任意位置按住拖动都会移动整窗——先前的表现正是它在工作，
  但它同时劫持了画布的 `mousedown/mousemove` 绘制序列；
- 自绘标题栏的 `win_begin_drag`（`ReleaseCapture` + `SendMessage`
  `WM_NCLBUTTONDOWN/HTCAPTION`）在 WebView2 上不生效：鼠标捕获属于
  `msedgewebview2` 进程，本进程既无法释放它、`SetCapture` 也会失败，
  系统移动循环拿不到捕获即退出。关掉 easy_drag 后唯一的拖动通道就断了，
  窗口自然拖不动。

**最终修复（两项必须同时在位）**：
1. 主窗口保持 `easy_drag=True`（`main.py` 显式写出并在注释中说明）——
   拖动整窗由 pywebview 提供，恢复「按住任意区域拖窗」的体验；
2. 新增 `webui/js/window_drag.js`（`index.html` 加载）：在 **document 冒泡
   阶段** 拦截交互元素（canvas/输入框/按钮/链接/可编辑内容/.no-drag…）的
   `mousedown`（`stopPropagation`），easy_drag 收不到事件就不会拖窗；
   元素自身的绘制/选择/点击处理器在更早的阶段已执行，不受影响。拦截挂在
   document（而非 body）以避开 pywebview 在 body 上的
   `.pywebview-drag-region` 显式拖拽区监听。
3. `app.js` 移除标题栏冲突的 `win_begin_drag` 调用（否则与 easy_drag 双写）。

**边界**：标题栏、卡片空白、页面背景 = 拖动整窗；画布、输入框、按钮、
下拉、链接、图片 = 各自交互。新增「按下后要自行拖拽」的元素：加 `no-drag` 类。

**验证**:
- `node test_region_ui.js` 新增 10 项拖动守卫单测（画布/按钮/输入框/嵌套
  元素被拦截，卡片空白/标题栏/卡片内文字放行）；
- `test_region_capture.py` 第 5 步静态校验：主窗口 `easy_drag=True`、
  `window_drag.js` 存在且被 `index.html` 加载、遮罩窗口 `easy_drag=False`；
- 渲染 pywebview 注入 JS 确认 `easy_drag=True` 时拖动分支生效。

---

## OpenList / V2rayN 启动失败 —— 修复

### OpenList：命令行用法错误（启动必然失败）
**现象**: 点击启动后立刻失败，提示进程「启动后立即退出」。

**原因**: 启动命令写成 `openlist.exe --data <目录> start --addr host:port`：
- `--addr` 参数根本不存在（openlist.exe 只有 `--config/--data/--debug/--dev/--force-bin-dir/--log-std/--no-prefix`），
  CLI 报 unknown flag 后退码非 0 立即退出；
- `start` 子命令是「静默后台启动」（Silent start），前台进程立刻返回，
  进程托管、健康轮询、停止/监控全部失效。

**修复**（`app/core/openlist.py`）:
- 改用 `openlist server --data <目录>` 前台运行；
- 监听地址/端口写进 `<data>/config.json` 的 `scheme` 段（该程序无命令行端口参数）；
- 子进程 stdout/stderr 转发到应用日志——首次运行会在 stdout 打印
  「初始管理员密码」，此前被 DEVNULL 丢弃导致无法登录 Web 管理界面。

**实测**: `server` 子命令启动 0.5s 就绪、端口监听、停止后端口释放；
首启日志出现 `Successfully created the admin user and the initial password is: …`。

### V2rayN：三层问题叠加（核心找不到 / 规则库找不到 / 协议不被支持）
**现象**: 「自动下载核心」后仍无法启动，或启动即失败。

**原因**:
1. **核心探测路径不含下载目录**：下载器落在 `%APPDATA%/LocalToolbox/xray/bin/xray.exe`，
   而 `detect_bin` 只找 `proxy/bin`、`proxy/` → 报「未找到 xray 核心程序」；
2. **规则库路径不匹配**：geoip.dat/geosite.dat 在 `proxy/bin`，而 xray 从
   `xray/bin` 启动时只在自身目录查找 → `failed to open file: geosite.dat` 启动失败；
   必须用 `XRAY_LOCATION_ASSET`（v2ray 为 `V2RAY_LOCATION_ASSET`）指向规则库目录；
3. **节点协议与核心不匹配**：AnyTLS 节点 + 默认 xray 核心 → `unknown config id: anytls`。
   Xray 官方从未实现 AnyTLS（仅 sing-box 1.12+/mihomo 支持），v2ray-core 同；
4. sing-box 的 anytls 出站字段写错（`"padding": true`）→ sing-box
   `FATAL … unknown field "padding"`。

**修复**（`app/core/v2ray_core.py`）:
- `_core_bin_candidates` 增加下载目录 `<DATA_HOME>/<核心>/bin`（含无 bin 的兜底）；
- 启动时按核心设置 `XRAY_LOCATION_ASSET` / `V2RAY_LOCATION_ASSET` 指向规则库目录；
- 新增协议↔核心支持矩阵与 `_ensure_core_for_node`：节点协议不被当前核心支持时，
  若其他核心可用则**自动切换**并记录日志；否则给出中文指引（切换 sing-box + 下载）；
- 修正 sing-box anytls 出站（去掉非法 `padding` 字段）；
- 核心 stdout/stderr 落盘 `proxy/core.log`，启动失败时把末尾几行附进错误信息
  （此前只有「退出码 1」，无从排查）；
- 自动切换节点 `_switch` 改为真正向后递进（原实现每轮都取同一节点，切换形同虚设），
  并在监控里加上限（最多轮换 min(节点数,16) 次后停止并还原系统设置）。

**实测**: 76 节点订阅（anytls）→ 自动切到 sing-box → 启动成功、延迟 202ms、停止还原正常。

### 节点列表：信息行污染 + 展示设计
- **信息行清理**: 「剩余流量/套餐到期」一类订阅信息不是可代理节点，此前被当普通节点
  导入，默认选中常落在信息行上（启动后节点不可达）。现在导入时跳过、存量数据加载时
  清理并顺延选中项（实测 76 → 75 个）；
- **UI 重做**（`webui/js/pages/v2ray.js`）: 顶部搜索框（备注/地址/协议）+ 排序
  （默认/延迟升降序/名称）+ 显示计数；行内为 协议徽标 + 备注 + 等宽地址 +
  延迟色阶徽标（<300ms 绿 / <800ms 黄 / 其余红 / 未测灰）+ 当前/运行中标记；
  双击行=应用；批量测速后自动按延迟排序；列表高度由 300px 放宽到 420px。

**验证**: `test_v2ray.py` 66 项（新增 10 项：协议矩阵、自动切换、下载目录候选、
信息行识别/清理、切换递进、anytls 出站字段）、`test_openlist.py` 16 项
（新增 4 项：server 子命令、config.json 写入/保留、输出转发）全部通过。

---

## 控制台黑窗 / 测速弹窗 / 下载项刷新 —— 修复

### 1. 后台进程弹黑窗（OpenList「要开着窗口才运行」）
**现象**: 启动 OpenList 后多出一个黑色控制台窗口，关掉它服务就停；
代理核心、批量测速同样会闪黑窗。

**原因**: 应用是窗口化进程（`console=False`），其 `subprocess` 启动的控制台程序
（openlist.exe / xray.exe / sing-box.exe / rclone.exe / powershell / net use / ffmpeg）
默认会新建控制台窗口；用户关窗即等于杀掉子进程。

**修复**: 全局给所有子进程加 `creationflags=CREATE_NO_WINDOW`
（openlist、v2ray_core 主核心与测速、traffic 采样、rclone 挂载/卸载、
dns_changer、winfsp 安装、video_edit、pan_api 的 net use）。
**实测**: OpenList 进程 `MainWindowHandle = 0`（无任何可见窗口），端口正常监听。

### 2. v2ray 批量测速「一直弹窗」
**原因**: 两个来源——① 每个节点测速都会拉起一次核心进程（控制台黑窗，见上）；
② 测速结束弹结果模态框罗列全部节点。

**修复**: 黑窗由 CREATE_NO_WINDOW 消除；结果模态框改为**直接回写节点列表**
（延迟徽标 + 自动按延迟排序），只用一条 toast 汇报
「N 个可用 / M 个不可用；最快 xx ms，已按延迟排序」。

### 3. 下载项缺「刷新/检查更新」
**修复**:
- `bindl.latest_version(kind)`：查询最新发行版版本号（不下载）；
- 新接口 `proxy_refresh_bins` / `pan_refresh_bins`：**立即**重新探测程序
  （含手动放置位置）并回报本地状态，版本检查在**后台线程**完成、经
  `proxy_bins` / `pan_bins` 事件推送（GitHub 不可达时单次查询可达数十秒，
  不能阻塞按钮——实测同步返回 0.01s，版本结果稍后 toast 提示）；
- 界面：代理页「核心与运行控制」、网盘挂载页 OpenList/rclone 处各加
  「检查更新」按钮；有新版提示「本地 vX → 最新 vY，点自动下载更新」。

**验证**: `test_v2ray.py` 69 项（新增：刷新接口即时返回 + 事件推送、
调用台窗口静态扫描守卫）、`test_openlist.py` 16 项、`test_screenshot.py` 32 项、
Node UI 34 项全部通过。

---

## 代理很慢 / 分流失效 —— 修复

### 根因（实测定位）
1. **sing-box 路由规则已废弃**：应用生成 `{"geoip": [...], "geosite": [...]}` +
   `route.geoip.path`（sing-box 1.8 起废弃、**1.12 起移除**），而下载的
   `geoip.db/geosite.db`（SagerNet 官方只发旧 .db 格式）同样不被 1.12+ 支持。
   用户下载规则库后核心直接 FATAL（`geoip database is deprecated ... removed
   in sing-box 1.12.0`）→ 代理完全不可用，表现为「什么都很慢」。
2. **域名冷解析每次约 5 秒**：未配 DNS 段时 sing-box 用系统解析器，
   本机 IPv6/AAAA 等待明显（实测百度/淘宝/知乎首连均 ~5.1s）。改用公共 DNS
   （223.5.5.5）+ `strategy: ipv4_only` 后首连降到 **0.11s**（46 倍）。
3. `geoip` 规则在 `geosite` 规则之前，使国内域名也要先解析 IP 才能直连。

### 修复
- **规则集换格式**：改用 sing-box 1.12+ 的 `rule_set` + `.srs` 文件
  （MetaCubeX/meta-rules-dat 的 sing 分支；SagerNet 官方仓库只发旧 .db）。
  新增 `bindl.download_ruleset()`（固定 URL + 镜像回退 + 7 天缓存 +
  SRS magic 校验——`geoip-private.srs` 仅 144 字节，不能按体积校验）。
- **DNS 段**：`{"servers": [{"type":"udp","server":"223.5.5.5"}], "final":"local",
  "strategy":"ipv4_only"}` + `route.default_domain_resolver`（缺标签会 FATAL）。
- **规则顺序**：`sniff` → 广告拦截（`action: "reject"`）→ `geosite-cn` 直连
  → `geoip-*` 直连 —— 国内域名无需先解析即可直连。
- **自定义规则兼容**：v2rayN 规则的 `geoip:cn / geosite:cn / category-ads-all /
  private` 前缀映射到对应 rule_set；无法映射的 geo 前缀直接忽略（原样输出会
  生成非法配置）。
- 界面文案与下载逻辑同步（sing-box 走 `download_ruleset`，xray/v2ray 仍用 .dat）。

### 实测（真机）
| 指标 | 修复前 | 修复后 |
|---|---|---|
| 核心启动 | FATAL 起不来 | 1.3s，探测 219ms |
| 百度/淘宝/微博 首连 | ~5.1s / 失败 | 0.11 / 0.05 / 0.14s |
| github / google 首连 | 失败 | 0.52s / 1.31s |
| 分流正确性 | — | 国内出口＝本地 ISP，国外＝节点 |

### 验证
`test_v2ray.py` 71 项（新增：rule_set 结构断言、DNS 段与解析器标签、
v2rayN 规则 rule_set 映射、SRS magic 校验）；其余测试全绿。

---

## 其他核心审计（xray / v2ray / sing-box）—— 修复

对三个核心逐条实测（启动、语法、连通、分流、延迟），发现并修复 3 个真实缺陷：

### 1. 单节点测速 / 批量测速对 xray、v2ray 必然失败
`test_node` / `burst_test` 启动的临时核心**没有传规则库环境变量**：
配置里含 `geoip:`/`geosite:` 规则，而 xray/v2ray 只在自身目录找不到
`geoip.dat/geosite.dat` → 启动即失败（`failed to open file: geosite.dat`）。
启动主路径（`_start_core`）有环境变量，测速路径漏了。
**修复**：抽出 `_core_env()`，启动、单测、批量测速三条路径统一使用。

### 2. 连通性探测可能绕过代理（假成功 → 健康检查/测速误判）
`_probe_http_port` 的 `ProxyHandler` 只给了 `http`，第 3 个探测 URL 是 https，
urllib 找不到 https 代理就会**直连** —— 本地代理已死也能探测"成功"。
实测表现为 xray 测速返回「成功，延迟 12703ms」这种假数据。
**修复**：http/https 都映射到本地代理端口，并在探测期间禁用
`no_proxy/proxy_bypass`（作用域内保存恢复，不影响其它下载逻辑）。

### 3. sing-box 自定义 DNS / 路由（高级配置）会让核心 FATAL
- 旧格式 `{"dns": {"servers": ["223.5.5.5"]}}` → 1.12+ 要求对象形式
  （`cannot unmarshal string into option._DNSServerOptions`）；
- 域名形式的 DNS 服务器（如 `https://dns.google/dns-query`）需要引导解析器
  （`missing domain resolver for domain server address`）；
- 自定义路由列表原先整段替换 route，丢掉 `rule_set` 定义与解析器标签 →
  规则引用失效。
**修复**：新增 `_migrate_singbox_dns()`（字符串→对象、按地址引用的 rules 换 tag、
域名服务器自动补引导解析器）；DNS 合并在用户配置之上保留 `strategy/final` 并保证
`final`/`default_domain_resolver` 指向存在的标签；自定义路由改为"替换规则但保留
规则集定义与解析器"，并剔除引用未定义规则集的规则（规则库缺失时不生成非法配置）。

### 实测结果（真机，三核心）
| 核心 | 启动 | 探测延迟 | 国内站点首连 | 分流 |
|---|---|---|---|---|
| xray | 3.9s | 172ms | 0.50s | 国内＝本地 ISP ✓ |
| v2ray | 4.4s | 233ms | 0.47s | 国内＝本地 ISP ✓ |
| sing-box | 2.0s | 218ms | 0.09s | 国内＝本地 ISP ✓ |

配置变体校验（smart/global/direct/无规则库降级/自定义 DNS/自定义路由/TUN，
xray 与 v2ray 用实跑存活判断、sing-box 用 `check`）全部通过。
注：那个香港 trojan 节点访问 github 失败——同一节点换 sing-box 核心同样失败，
属线路问题，非配置问题。

### 验证
`test_v2ray.py` 79 项（新增 8 项：核心环境变量、测速传参、探测不绕过代理、
DNS 迁移与引导解析器、合并后引用有效性、规则集定义保留与剔除）。

---

## 复刻 Clash Verge Rev 特性（策略组 / 连接监控 / 订阅自动更新）

对照 clash-verge-rev 的功能清单评估后，复刻了其中与运行体验最相关、且适配
本项目架构（Python + pywebview + xray/sing-box）的三项；底层复用 **sing-box
自带的 Clash API**（与 Clash Verge 同一套管理接口，`experimental.clash_api`）。

### 1. 策略组（Clash 的 Selector / URL-Test）
- 定义存 `proxy/groups.json`：`{name, type: selector|urltest, filter, interval, default}`；
  成员按**关键字**匹配节点备注 / 地址 / 协议（空或 `*` = 全部节点）。
- 配置生成：每个节点一个出站（`node-<索引>`）+ 每组一个
  `selector`（手动选择，可嵌套其它组，与 Clash 的「节点选择 → 自动选择」一致）
  或 `urltest`（自动测速，`interval` 可配）；`route.final` 指向 `default` 标记的组。
- **运行时切换**：走 Clash API `PUT /proxies/{组}` —— 即时生效、无需重启核心
  （实测：切换 节点选择 → node-2 → 再切回嵌套的自动组，均立即生效）。
- **向后兼容**：没有分组定义时配置与原来完全一致（单 `proxy-main`），零风险。

### 2. 连接监控（Clash 的 Connections 页）
- Clash API `GET /connections` → 活动连接列表（主机、命中规则、链路、上下行、
  进程名）+ 累计流量/内存；单条关闭 `DELETE /connections/{id}`、
  全部关闭 `DELETE /connections`。
- 前端新增「连接」页签：2.5 秒轮询（仅页签可见且代理运行时拉取），
  一键关闭单条 / 全部，显示累计流量。
- 启动竞态修复：Clash API 端口比代理端口略晚监听，新增就绪等待（≤3s）
  与连接类错误自动重试，避免启动瞬间查询被拒。

### 3. 订阅自动更新
- 后台线程按配置周期（6/12/24/72 小时，可关）重拉订阅地址，
  记录上次更新时间；失败只记日志不刷屏。前端「节点导入」页可设周期、
  显示订阅地址与上次更新时间、支持「立即更新订阅」。

### 未复刻 / 评估结论
- **Merge / Script 配置增强、CSS 注入、图标自定义**：属于 mihomo 配置文件生态的
  特性，本项目用节点库 + 高级配置（路由/DNS JSON）承担，价值有限，未做。
- **WebDAV 配置备份同步**：项目已有 WebDAV 客户端（网盘挂载页），
  可作为后续独立特性复刻（备份 nodes.json / groups.json / advanced.json）。
- **Service 模式（免管理员 TUN）**：需要安装系统服务，改动面大，未做。
- **可视化规则编辑**：已有「高级配置」JSON 编辑，可视化编辑列为后续项。

### 验证
`test_v2ray.py` 87 项（新增 8 项：策略组存取/成员过滤/出站生成/配置集成、
Clash API 仅 sing-box 生效，以及**前端调用的桥接方法必须存在**的静态校验）；
真机实测：策略组创建与切换、连接接口、订阅自动更新设置全部通过。

---

## Clash 独立页面组（mihomo 内核，与「代理管理」分离）

**需求**：Clash 放到「网盘与网络」分组；使用 Clash 常用内核（mihomo）；
不与 v2ray 混淆；不要把所有内容塞进一个页面。

### 1. 独立引擎：mihomo（Clash.Meta）
- 新增 `app/core/clash_core.py` + `app/bridge/clash_api.py`（25 个 `clash_*` 方法）；
  **独立数据目录** `%APPDATA%/LocalToolbox/clash/`（配置、节点库、分组、缓存、日志），
  不读写「代理管理」的任何文件（节点库可一键复制一份，复制后各自独立）。
- 内核 `mihomo.exe` 由 bindl 下载（MetaCubeX/mihomo）；规则库
  `geoip.dat` / `geosite.dat` / `country.mmdb` 来自 MetaCubeX/meta-rules-dat，
  启动前自动就位到数据目录（缺失时 mihomo 会自行联网下载，拖慢首启 12s+）。
- 端口默认 **混合 7891 / API 9091**（避开 Clash Verge 的 7897/9090 与
  本应用 xray/sing-box 的 10809/10808）。
- 配置为 Clash YAML（PyYAML 生成）：proxies（vless/vmess/trojan/ss/socks/http/
  anytls/hysteria2/tuic/wireguard + REALITY/ws/grpc/h2）、proxy-groups
  （select / url-test / fallback / load-balance）、rules（GEOSITE/GEOIP +
  无规则库时的内置静态降级）、DNS（fake-ip + 国内 DNS + 国外 fallback，
  不依赖 MMDB）、可选 TUN。
- 实测：启动 0.9s；策略组自动/手动均正常；内核级测速（/proxies/{name}/delay）
  147ms；运行时切换即时生效；百度直连 0.11s / github 经节点 2.09s；
  连接列表与累计流量可用；停止后端口释放、系统代理还原。

### 2. 拆成 4 个独立页面（同一文件内注册，共享状态）
全部归入 **「网盘与网络」** 分组（与「代理管理」同组，互不干扰）：
- **Clash 代理**：运行控制（启动/停止/模式/系统代理）、策略组（卡片 + 成员
  就地切换 + 组测速）、节点列表（搜索 / 内核测速 / 删除 / 批量测速）；
- **Clash 连接**：活动连接实时列表（2.5 秒刷新，仅本页前台时拉取）+ 单条/全部关闭；
- **Clash 订阅**：订阅导入、自动更新周期、立即更新、粘贴分享链接、
  「从代理管理复制节点」（一键迁移，之后各自独立）；
- **Clash 设置**：内核 / 规则库下载、端口、TUN、日志、打开数据目录。

### 验证
新增 `test_clash.py` 24 项（节点→Clash 转换含 REALITY/ws/hy2、策略组类型与
间隔夹取、配置生成含主组/嵌套/自动 PROXY/规则降级/DNS+TUN、YAML 往返、
规则库就位、状态字段、内核缺失报错、bindl 规格与压缩包内 exe 定位）；
页面注册自检更新为断言 clash.js 注册 4 个页面且都在「网盘与网络」；
其余测试全绿。

---

## 架构自适应下载 + Clash 功能对齐（规则 / 日志 / 速率）

**需求**：自动识别架构并下载相应核心；功能尽量与 Clash 对齐。

### 1. 自动识别 CPU 架构并选择内核构建
- `app/core/bindl.py` 新增 `arch_tag()`（`PROCESSOR_ARCHITEW6432` →
  `PROCESSOR_ARCHITECTURE` → `platform.machine()`，32 位进程跑在 64 位系统上
  仍按 64 位下载，因为内核是独立进程）、`cpu_level_amd64()`
  （`IsProcessorFeaturePresent`：40=AVX2→v3、39=AVX / 38=SSE4.2→v2、否则 v1）、
  `arch_label()`、`_arch_patterns(kind)`、`_pick_asset_by_arch()`。
- 选包顺序按架构回落：本机 `x86-64 v3` → `mihomo-...-amd64-v3-go*.zip`，
  v3 缺失退 v2、再退 v1，最后才退通用（`compatible` 只在 v1 兜底时用）；
  ARM64 / 386 各自只取对应架构，绝不会拿到 amd64 包。同一层级有多个 Go
  工具链构建时取 `-go` 版本较新的。
- 缓存键加入 `asset`：同版本但不同构建（v1 → v3）不会误判为「已下载」而跳过。
  新增 `cached_info(kind)` 供界面展示。
- 设置页显示 **本机架构** 与 **实际内核构建**：`x86-64 v3` +
  `Mihomo Meta v1.19.30 windows amd64 with go1.25.13`（`bin_version()` 调
  `mihomo -v`，按 exe 路径 + mtime 缓存，0.06s 首次 / 0ms 之后），
  下载后能一眼看出用的是哪个变体。

### 2. Clash 规则页（`clash_rules`）
- 自定义规则（`clash/rules.json`，一行一条，支持 `DOMAIN-SUFFIX,...`、
  `IP-CIDR,...`、`GEOIP,...`）生成配置时 **置顶**，可覆盖内置分流；
  保存即 **热重载**（重写 config.yaml + `PUT /configs?force=true`，无需重启内核）。
- 生效规则列表来自内核 `GET /rules`，支持搜索、显示命中策略，
  可按条「复制」到自定义规则；与自定义规则一致的条目打「自定义」标
  （内核下发的是 `DomainSuffix` 规范化写法，比较时去连字符 + 大写归一）。

### 3. Clash 日志页（`clash_logs`）
- 订阅内核 `GET /logs` 流式接口，按行解析后经 `clash_kernel_log` 事件推送到界面；
  级别（debug/info/warning/error）+ 前端展示过滤 + 自动滚动 + 清空。
- **订阅级别会同时下发内核** `PATCH /configs {"log-level": ...}`：
  内核自身 `log-level` 会压制推送，否则 warning 配置下订阅 info 收不到任何日志
  （实测 0 行 → 3 行）；级别持久化到配置（`clash_log_level`）。
- 两个实现坑已修：`resp.read(n)` 在 socket 上会阻塞到攒满 n 字节（小日志量下
  一条都收不到）→ 改为 `readline()` 逐行读；停止时直接 `resp.close()` 会与
  阻塞中的读线程死锁（close 等缓冲区锁）→ 改为从外部 `socket.shutdown()`
  唤醒读线程，再由读线程自己关闭。

### 4. 实时速率
- 代理页显示 ↓/↑ 实时速率：对 `/connections` 的累计字节做差分（每 2 秒，
  仅本页在前台时请求，不做无谓轮询）。

### 验证
- 真机（真实 mihomo 内核、项目本地数据目录）：启动 healthy；
  自定义规则热重载后出现在内核规则列表首位且标 custom；
  日志流收到 3 行真实内核日志；速率差分 ↓111 B/s ↑100 B/s；
  运行时改日志级别立即生效；停止后系统代理未改动。
- 真实 WebView2 窗口探针 9 项全过：6 个 Clash 页面全部挂载在「网盘与网络」下、
  规则页保存 2 条规则（后端确认落盘 + toast）、内核未运行时点「开始接收」
  给出「Clash 未运行」提示而非静默、架构提示 `x86-64 v3`、日志级别下拉 4 档、
  速率占位渲染。
- `test_clash.py` 24 → **43 项**（新增自定义规则存取/置顶/过长截断、
  规则 custom 标记、日志级别校验与下发、日志流逐行解析/停止不死锁/错误回调、
  桥接 clash_rules/save_rules/log_start/log_stop/cfg 白名单、架构选择
  v1/v2/v3/arm64/386 + Go 工具链择优 + 缓存 info、bin_version 解析与缓存）；
  页面注册自检更新为 6 个页面；全量回归 + UI 冒烟（44 页面 0 挂载错误）全绿。

### 5. 回归中发现并修掉的两个下载器 bug（构建前拦住）
- **缓存缺 `asset`**：zip 分支只写了 `version/exe/sha`，架构构建切换判断
  （`prev.asset == asset_name`）永远不成立 → 每次都重新下载。
  已补写 `asset`，并加测试断言缓存落盘内容。
- **raw 分支变量名错**：`_download_raw`（winfsp / geoip.dat / geosite.dat 走这条路）
  里引用了不存在的 `asset_name` → `NameError`，即「下载规则库/winfsp」必崩。
  改为就地 `_asset_name(asset_url)`。
- 两个 bug 都是 `test_bindl.py` 抓出来的（该文件此前未在本轮跑过）：
  9 → **11 项**，新增「同版本换架构构建必须重下」「缓存必须记录 asset」。

### 6. 订阅流量 / 到期（与 Clash 客户端对齐）
- `import_sub_url` 现在同时读取响应头：`subscription-userinfo`
  （upload/download/total/expire）写入 `store.data["sub_userinfo"]`，
  `profile-update-interval` 写入 `sub_interval_hours`。
- Clash 订阅页新增一行：`已用 X · 共 Y（剩余 Z）· 到期 …`；未提供该头的机场
  显示「订阅未提供流量信息」，不显示假数据。
- 机场在头里建议了更新周期、而用户尚未设置自动更新时自动采用
  （Clash 客户端同款行为，会写日志说明）；用户已设置的值优先，不被覆盖。
- 新增 4 项测试（头解析含 0 / 空 / 科学计数 / 大小写、桥接展示、
  建议周期采用、用户设置优先、无建议不动作）→ `test_clash.py` **47 项**。

### 7. 顺手修掉的时间显示 bug（订阅「上次更新」/ 到期）
- `App.fmtDate()` 收的是**秒**（内部 `new Date(ts * 1000)`），但 Clash 订阅页与
  代理管理页都写成 `App.fmtDate(ts * 1000)` —— 秒再乘 1000 会被当成天数级时间戳，
  显示成 5 万多年后的日期（或空）。改为直接传秒；订阅「到期」同理。
- 前端回归加了静态拦截：`webui/js/pages/*.js` 里出现 `fmtDate(... * 1000)`
  直接判失败（`node test_region_ui.js`）。

---

## 全功能体检（逐模块实测 + UI 冲突排查）

**需求**：逐一测试软件全部功能并修复；检查 UI 冲突/不合理的地方。

### 1. 前端「调用约定错位」——6 个页面整体失效（最严重）
`App.call()` 现在直接返回 `data` 并在失败时抛错，但 `combine / editor / batch /
cliphist / split / video` 这 6 个页面仍按早期 `{ok, data}` 包装写：
`r.data.data_url`、`r.ok` 全是 undefined → **图片合并、图片分割、批量处理、视频编辑、
剪贴板历史、编辑器另存为/格式转换全部静默失效**（既不报错也不干活）。
- 修法：在每个页面加一层兼容层（`call()` 包装成 `{ok, data}`，失败弹 toast 不抛异常），
  一处改动同时修好全部调用点，且不改变其它页面的写法。
- 校验：真实窗口遍历后 `unhandledrejection` 归零；`test_capability` 与真机链路均通过。

### 2. 后端返回结构与前端期望不符（3 处）
- `dns_list_adapters` 返回 `[{name,index,mac}]`，DNS 工具页当成字符串数组渲染 →
  `appendChild ... not of type Node`，整页不可用。改为按对象取 name/mac，
  下拉项显示「网卡（MAC）」。
- `tools.js` 调 `editor_extract_palette(path, n)`，但该接口要 **data_url** 且
  `editor_open()` 不接参数（多传 `null` → `TypeError`）→ 调色板提取工具失效。
- 全应用静态核对「前端实参个数 vs 后端签名」（301 个方法）后只此一处不匹配，已修。

### 3. WebView2 下不可用的浏览器 API（11 处）
- `prompt()` 在 WebView2 里被禁用（静默返回 null）：`batch.js` 8 处
  （目标宽度/特效参数/水印文字/目标格式/输出目录）+ `editor.js` 2 处
  （另存格式/转换格式）→ 这些按钮点了没反应。统一改为应用自带的 `App.modal`
  （含校验与错误提示）；批量处理的输出目录改用系统目录选择器。
- `window.open` 被宿主拦截：挂载页「打开 Web 界面」点了没反应 →
  新增桥接 `open_external(url)`（限 http/https，走系统默认浏览器）。

### 4. 缺失样式：几个页面从来没被真正看过（48 个类名未定义）
剪贴板历史/合并/分割/批处理/视频这几个页面的结构类此前**没有任何 CSS**：
列表项没有卡片外观，剪贴板缩略图甚至按原图尺寸撑破布局。
- 新增 `.btn.xs`（12+ 处「btn xs」此前与 sm 同尺寸）+ `.cliphist-*`、`.combine-*`、
  `.batch-*`、`.progress-bar`、`.video-info`、`.log-line`，全部沿用同一套主题变量
  （深浅色主题都正确）。
- 批量进度条去掉硬编码 `#333/#4CAF50`（浅色主题下很突兀），改用主题色；
  剪贴板状态的 `.status-tag` 换成统一的 `App.statusTag`。
- 顺带清理 3 处同层重复 CSS（`.brand .brand-sub` 残缺重复、`.nav-item` 拆成两条）。

### 5. 录制帧数显示为 0
视频录制把帧数记在局部变量里，`get_state()` 报的是只有 GIF 路径才累积的
`_frames` → 界面整个录制过程都显示「0 帧」。改为共用计数器（实测 2 秒录到 28 帧）。

### 6. 新增两个可复跑的体检入口
- `test_capability.py`（新）：**63 项真实链路能力测试**——图片编辑/合并/分割/调色板/
  截屏/OCR、文件与校验和、批处理流水线、视频（真实 ffmpeg 裁剪/转 GIF/抽帧）、
  网络与系统只读查询、端到端加密配对（含 EncryptedChannel 往返）、
  代理核心解析与 Clash 配置生成、架构识别。9 秒跑完，OCR/ffmpeg 缺失自动跳过。
- `test_ui_smoke.py`（增强）：除挂载外，现在还会抓 JS 报错（含未捕获 Promise）、
  渲染异常文本（`[object Object]`/`undefined`/`NaN`）、空下拉框、横向溢出，
  并给出「卡在哪个页面」的诊断。当前：**44 页 / 0 报错 / 0 溢出**。

### 7. 真机功能性验证（此前未测过的窗口类功能）
贴图（创建/置顶/透明度/批量关闭）、录屏（rec_start → rec_done 落盘 845KB mp4 → 清理）、
窗口控制（最大/还原/置顶/最小化）、流量采样、配置读写、OCR 真实识别 → 全部通过。

### 8. 排查后确认「不是 bug」的项（避免误改）
- `batch_api` 的 `self._batch.running` 看似漏括号，实为 `@property` ✓
  （全项目 12 个同类 `running/active/available` 都是 property）。
- `rec_stop` 只返回「已请求停止」，文件名由 `rec_done` 事件带回 ✓
- 硬编码颜色多集中在画布绘制/圈选遮罩（编辑器标注色、区域截图遮罩）→ 有意为之 ✓
- 侧栏隐藏页（22 个）全部有入口（工具箱卡片 / backTo）✓

### 9. 提权执行器与脚本语法不匹配（防火墙/WebDAV 静默失败）
从应用日志里翻出来的真问题：`app.log` 里有
`防火墙放行 LocalToolboxOpenList 端口失败：'Out-Null' 不是内部或外部命令`。
- 根因：`run_elevated()` 把命令写进 **.bat** 交给 cmd 执行（UAC 提权），
  而 `firewall.add_ports/remove`、`web_server.set_webclient_basic_auth`
  传的是 **PowerShell 语法**（`| Out-Null`、`Set-ItemProperty`）→ cmd 解析失败，
  防火墙规则实际没加上（局域网访问 FTP / Web 共享 / OpenList 会不通），
  Web 服务器页的「修复认证」也点了没反应。
- 修法：这两处改用 `run_powershell_elevated()`（包一层 powershell）；
  清理因此不再使用的导入。
- 新增 `test_elevated_scripts.py`（8 项）：
  ① 断言这两个功能走 PowerShell 提权执行器；
  ② 用 **PowerShell 自己的解析器**离线校验生成的脚本语法（不提权、不改系统）；
  ③ 拦截 `ShellExecuteExW` 检查生成的 .bat 内容（PS 版包 powershell、cmd 版不包）；
  ④ **全项目静态扫描**：`run_elevated(...)` 实参里出现 PowerShell 专有语法即判失败
  （防止以后再把 PS 脚本塞回 cmd 通道）。

### 10. 本轮新发现并确认已修的问题汇总
| # | 问题 | 影响 |
|---|------|------|
| 1 | 6 个页面用旧 `{ok,data}` 约定调 `App.call` | 合并/分割/批处理/视频/剪贴板历史/编辑器另存 全部静默失效 |
| 2 | DNS 适配器返回结构与前端不符 | DNS 工具页抛错不可用 |
| 3 | `editor_open` 多传参 + 调色板传的是路径不是 data_url | 调色板提取工具失效 |
| 4 | 原生 `prompt()` 10 处（WebView2 禁用） | 批量处理/编辑器格式类按钮点了没反应 |
| 5 | `window.open` 被宿主拦截 | 网盘页「打开 Web 界面」没反应 |
| 6 | 剪贴板/合并/分割/批处理/视频缺 CSS（48 个类名） | 列表无卡片样式；剪贴板缩略图撑破布局 |
| 7 | 批量进度条硬编码 `#333/#4CAF50` | 浅色主题下突兀 |
| 8 | 录制帧数恒显示 0 | 状态误导 |
| 9 | 提权脚本语法与执行器不匹配 | 防火墙放行/WebDAV 认证失败 |
| 10 | 3 处同层重复 CSS | 维护隐患（已合并） |
