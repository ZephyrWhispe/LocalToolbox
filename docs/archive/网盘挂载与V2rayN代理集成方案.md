# 网盘挂载（OpenList）与 V2rayN 代理集成方案

> **状态：已实施**（2026-09-09，v3.0 一体交付：网盘挂载 + 代理管理 + UI 导航重构 + 文档。
> 原三阶段 v3.0/v3.1/v3.2 合并为应用版本 v3.0，全部测试与文档门禁已通过；
> 最终交付记录见[技术文档.md CHANGELOG v3.0](../../技术文档.md) 与
> [使用文档.md](../../使用文档.md)。）

## 1. 概要

为 smb-tool 新增两大能力并重构导航 UI，分三阶段独立交付、每阶段测试闭环：

- **阶段一（v3.0）网盘挂载**：集成 OpenList 统一后端（自动下载/探测二进制、进程托管、健康监控），应用内置纯 stdlib 的 WebDAV 客户端实现文件浏览/上传/下载/重命名/删除，支持一键映射本机盘符（`net use`）。
- **阶段二（v3.1）V2rayN 代理管理**：完整代理控制——支持订阅 URL / V2rayN 配置目录 / 剪贴板分享链接三种方式导入节点，自跑 v2ray/xray 核心，系统代理托管与还原，连通性监测 + 失败自动切节点重连。两个核心组件（v2ray/xray、OpenList）均支持 GitHub Releases 自动拉取下载（用户已确认）。
- **阶段三（v3.2）UI 导航重构 + 文档**：按功能逻辑分组定稿导航（协作/共享服务/网盘挂载/代理加速/工具/系统），微调样式，补齐使用文档与设计/技术文档 CHANGELOG。

已确认的技术路线：OpenList 统一后端 + 应用内 WebDAV 客户端；V2rayN 完整代理控制（自跑 v2ray/xray 核，非仅读配置）；UI 渐进式重构；分三阶段交付。全程零新增第三方 Python 依赖（stdlib 实现 HTTP/WebDAV/代理/注册表），对齐现有「服务型模块 + 桥接 Mixin + emit 事件」架构。

## 2. 现状分析（探索结论）

### 架构与代码位置
- 入口 `main.py` → `app/bridge/bridge.py` 的 `Bridge` 类（聚合各 `*Api` Mixin 作为 pywebview js_api）→ `webui/`（原生 JS，`App.*` 全局命名空间）。
- 服务型模块范式（`app/core/web_server.py`、`ftp_server.py`）：`__init__` 字段（`_server/_thread/running`）→ `start()` 参数校验 + daemon 线程启动 + `firewall.add_ports()` 放行端口 → `stop()` 优雅关闭 → `state()` 状态结构 → `log_callback` 回调推日志。
- 桥接层契约（`app/bridge/base.py`）：js_api 方法名 `页面_动作`，返回 `{"ok":True,"data":...}` / `{"ok":False,"err":"中文"}`，通过 `self.emit("事件名", payload)` 推送前端；`web_api.py` / `ftp_api.py` 为完整范式样例：参数校验 → 核心调用 → `emit` 事件 → 返回状态。
- Mixin 聚合点：[bridge.py](file:///c:/Users/86151/Documents/Code/smb-tool/app/bridge/bridge.py) 第 54-90 行——import + `Bridge(...)` 继承列表 + `__init__` 内 `_init_xxx()`；`shutdown()` 第 91-95 行收束线程——新增服务的停止/还原必须挂到这里。
- 配置持久化：`app/core/config.py` — `DEFAULTS` + `%APPDATA%/LocalToolbox/config.json`，`AppConfig.get/set`；`cfg_set` 白名单在 [bridge.py](file:///c:/Users/86151/Documents/Code/smb-tool/app/bridge/bridge.py#L132-L171)。

### 前端
- 导航分组：`webui/js/app.js` 第 5 行 `GROUP_ORDER = ["协作", "共享服务", "工具", "系统"]`；`buildNav()` 按 `p.group` 聚合渲染 `nav-group/nav-caption/nav-item`，未列出的组自动追加在尾部。
- 页面注册：`App.registerPage({id,title,icon,group})` + `mount(el)/show()/onCli` 生命周期；页面脚本在 `webui/index.html` 第 58-70 行加载。
- UI 辅助：`App.h / App.row / App.svcCard(标题, 配置行[], 操作行[], 日志框) / App.icon / App.toast / App.modal / App.confirm / App.statusTag / App.makeLog / App.tryCall / App.on`（`js/ui.js`、`js/bridge.js`）。
- 现有 13 页分组：协作=clipboard/km/transfer；共享服务=share/ftp/web；工具=network/file/scan/shellmenu/tools/screenshot；系统=settings。
- 图标：`js/ui.js` ICONS 含 `wifi/shield/check/refresh/toolbox/folder/globe/server/cursor/radar/keyboard/send/clipboard/monitor/settings/share`；**无 cloud/bolt**，新页面复用 `folder`/`shield`，不新增图标（避免样式泄漏风险）。
- 样式主文件 `webui/css/app.css`，导航样式在 110-147 行。

### 依赖与测试
- `requirements.txt`：无 requests；`cryptography` 已存在。新模块全部用 stdlib（`urllib`/`http.client`/`winreg`/`subprocess`/`base64`/`xml.etree`/`zipfile`）。
- 测试：项目根 `test_*.py`（pytest 风格，约 80 单测 + `test_ui_smoke.py` 13 页冒烟），新增测试放置根目录同风格。

## 3. 总体设计决策

| # | 决策 | 说明 |
|---|------|------|
| D1 | 核心组件自动拉取下载 | 新增 `app/core/bindl.py`：从 GitHub Releases 自动下载并解压 OpenList / v2ray / xray 二进制，带进度回调与本地缓存（版本校验），失败给出中文提示 + 手动放置入口。 |
| D2 | OpenList 集成边界 | 应用负责：二进制托管、进程启停/健康监控、WebDAV 客户端（浏览/上传/下载/改删）、盘符映射。**不直接写 OpenList SQLite**（版本脆弱）；网的驱配置经「打开官方管理界面」与「导入配置文件（openlist.json）」完成。 |
| D3 | V2rayN 集成边界 | 完整代理控制：自跑 v2ray/xray 核心。节点来源=订阅 URL（base64）/ V2rayN 目录（guiNConfig.json、configN.json）/ 剪贴板分享链接；节点库落盘 `DATA_HOME/proxy/nodes.json`。 |
| D4 | 数据目录 | 全部归 `%APPDATA%/LocalToolbox/` 下：`openlist/`（二进制+数据）、`proxy/`（节点库、核心二进制、生成配置）、`bindl/cache.json`（下载缓存）。 |
| D5 | 代理托管与还原 | 系统代理仅在核心运行期间设置（winreg HKCU Internet Settings），stop/shutdown/异常均还原原值；启动时检测并清理指向 127.0.0.1 的残留。 |
| D6 | 零新增依赖 | 全部 stdlib，规避打包与兼容风险。 |
| D7 | 每阶段独立门禁 | 单测 + 场景 + UI 冒烟通过才进入下一阶段。 |

## 4. 阶段一：网盘挂载（OpenList 对接，v3.0）

### 4.1 新增 `app/core/bindl.py` — 二进制自动下载器（被阶段一/二共用）
- `download_binary(kind, dest_dir, progress_cb)`：`kind ∈ {"openlist","v2ray","xray"}` → 组装 GitHub Releases 最新版资产 ZIP 地址（`.../releases/latest` 跟随重定向解析版本 tag 与资产名；资产名按 `openlist.*(win|windows).*amd64.*zip` / `v2ray-windows-64.zip` / `Xray-windows-64.zip` 模糊匹配，保留「用户可改 URL」兜底）。
- `urllib.request` 下载（自定义 User-Agent、30s 超时、写临时文件），`zipfile` 解压提取目标 `.exe`，落 `dest_dir/`；版本与 sha256 记 `bindl/cache.json`，同版本重复下载直接跳过。
- 失败抛带中文的 `BindlError`（提示网络/路径/手动放置位置）。
- 回调 `progress_cb(downloaded_bytes, total)` 供前端进度展示。

### 4.2 新增 `app/core/openlist.py` — OpenList 进程托管
`class OpenListManager`（对齐 WebServer 模式）：
- 字段：`proc / bin_path / data_dir(DATA_HOME/openlist/) / host / port / running / fw_open / healthy / log_callback`。
- `detect_bin()`：探测顺序 = 配置 `openlist_bin` → `DATA_HOME/openlist/bin/openlist.exe` → 应用同目录 `runtime/openlist.exe`；均缺则抛错并提示「自动下载或手动放置」。
- `start(host, port, fw)`：校验二进制与端口 → **`_build_cmd()`** 封装 CLI 启动参数（`--data <data_dir> start`；CLI 真实参数在首次真机联调时以 `openlist.exe --help` 校准，`_build_cmd` 便于单测）→ `subprocess.Popen` → 健康轮询：对 `http://127.0.0.1:{port}/dav` 发 PROPFIND，最多 30s → 成功则 `firewall.add_ports('LocalToolbox OpenList', [port], 'tcp')` → 启动监控线程。
- `stop()`：`terminate()` → 等待 → `kill()` 兜底 → 移除防火墙 → 清引用。
- `state()`：`{running, pid, bin, host, port, fw_open, healthy, drives}`（`drives`=根目录条目数，表示已配置的网盘驱动数）。
- `_monitor_loop()`：每 3s `proc.poll()` + 可选探活；状态从 running→down 时回调 `log_callback("OpenList 已退出...")` 并复位 `running/healthy`。
- `import_config(path)`：读取用户导入的 openlist.json，校验必备字段（`version` 与 `initBase`/驱动数组），校验通过后写入 `data_dir/openlist.json`（OpenList 启动时加载），返回提示「重启后生效」；同时提供 `open_web()` 返回官方管理界面地址。

### 4.3 新增 `app/core/webdav_client.py` — 纯 stdlib WebDAV 客户端
`class WebDavClient(base_url, user, password, timeout=10)`，`base_url` 形如 `http://127.0.0.1:15244/dav`：
- `listdir(path)`：`PROPFIND` Depth:1 → `xml.etree` 解析 207 → `[{name, path, is_dir, size, mtime}]`（目录优先排序）；对根路径额外读 `quota-used-bytes / quota-available-bytes` 返回空间信息。
- `mkdir(path)`=MKCOL；`delete(path)`=DELETE；`rename(src,dst)`=MOVE（`Destination` 头）。
- `upload(local_path, remote_dir, progress_cb)`：`http.client` PUT，64KB 分块流式发送 + 进度回调。
- `download(remote_path, local_path, progress_cb)`：GET 流式写文件 + 进度回调；`Content-Length` 为空时进度显示已传输字节。
- 错误映射：401→「认证失败（请检查驱动凭据）」、403→「无权限」、404→「路径不存在」、409→「目标已存在/冲突」，统一中文。
- 路径安全：URL 编码组件（空格/中文/`#`）、解码后规范化并拒绝 `..` 逃逸；每操作独立连接（urllib 非线程安全），线程安全由调用方保证。

### 4.4 新增 `app/bridge/pan_api.py` — 网盘桥接 `class PanApi`
- `_init_pan()`：`self._pan = OpenListManager(log_callback=lambda m: self.emit("pan_log", m))`；`self._pan_tasks = {}`；`self._pan_pool = ThreadPoolExecutor(max_workers=2)`。
- js_api（全部遵循契约，参数首行校验）：
  - `pan_get_state()` → OpenListManager.state() + 已映射盘符列表 + 下载缓存状态；
  - `pan_start(host, port, fw)` / `pan_stop()`；
  - `pan_set_bin(path)`（保存配置）+ `pan_download_bin()`（异步下载，经 `pan_task` 推进度）；
  - `pan_import_config(path)`；
  - `pan_open_web()` → 返回地址（前端 `window.open`）；
  - `pan_browse(path)` / `pan_mkdir(name)` / `pan_delete(path)` / `pan_rename(src, dst)`；
  - `pan_upload(local_path, remote_dir)`、`pan_download(remote_path, save_dir)` —— 提交流程池执行，任务 id 关联 `pan_task` 事件：`{task_id, kind, name, done, total, speed, status}`（进度事件节流 ≥200ms）；
  - `pan_tasks()`：任务列表；`pan_cancel(task_id)`：正在传输的任务置取消标记（下个分块中断）；
  - `pan_map_drive(letter, user, pwd)`：校验单个字母 → `net use X: http://127.0.0.1:{port}/dav /persistent:no`（用户级映射，无需管理员）→ 解析输出；
  - `pan_unmap_drive(letter)`：`net use X: /delete`；`pan_mapped()`：`net use` 输出解析为列表。
- 事件：`pan_log`、`pan_task`。

### 4.5 新增前端 `webui/js/pages/pan.js`（id:`pan`，title:网盘挂载，icon:`folder`，group:网盘挂载）
三张卡片（`App.svcCard` 结构，仿 `web.js`）：
1. **OpenList 服务**：二进制路径只读框 + `选择..`（`pan_set_bin`）/ `自动下载`按钮；主机/端口输入；启动/停止/防火墙开关；`打开管理界面`、`导入配置`按钮；状态 `App.statusTag`（运行中/健康、未运行）；日志框 `App.makeLog` 订阅 `pan_log`。
2. **盘符映射**：盘符输入 + `映射` / `断开` + 已映射列表（盘符→地址）。
3. **文件浏览**：面包屑路径 + 条目表（名称/类型/大小/修改时间，仿 `file.js` 排序渲染）+ 工具行（上传到当前目录、下载、新建文件夹、删除、重命名、刷新）+ 当前任务进度条区（订阅 `pan_task`）。
- `show()` → `pan_get_state` 刷新；上传/下载本地路径复用后端/前端目录选择（`web_pick_folder` 类似能力的 `pan_pick_local()`，用 `create_file_dialog`）。

### 4.6 注册与配置改动
- `app/bridge/bridge.py`：`from .pan_api import PanApi` + `Bridge(...)` 继承列表加 `PanApi`；`__init__` 加 `self._init_pan()`；`shutdown()` 加 `self._pan.stop()` 兜底（try/except）。
- `app/core/config.py`：`DEFAULTS` 增 `"openlist_bin": ""`。
- `webui/index.html`：追加 `<script src="js/pages/pan.js"></script>`。
- `webui/js/app.js`：`GROUP_ORDER` → `["协作","共享服务","网盘挂载","工具","系统"]`。
- `app/bridge/bridge.py`：`APP_VERSION` → `"3.0"`。

### 4.7 阶段一测试
- `test_webdav_client.py`：用现有 `web_server.py`（WsgiDAV，含读写）在测试起本地假 WebDAV，全链路断言 listdir/mkdir/upload/download(校验内容)/rename/delete + 401(错密码)/404 错误映射。
- `test_openlist.py`：monkeypatch `subprocess.Popen` / 探活请求 → start/stop/监控状态机/`import_config` 校验/端口与防火墙参数断言。
- `test_bindl.py`：monkeypatch `urllib` → 下载/解压/缓存命中/同版本跳过。
- 冒烟：`test_ui_smoke.py` 兼容 + 手动 `pan_get_state` 调用冒烟。
- 回归：全部既有单测 + 场景脚本 + 13 页冒烟必须通过。
- 可选真机：存在 `openlist.exe` 时手工联调浏览/上传/映射盘符并记两机验收。

## 5. 阶段二：V2rayN 代理管理（v3.1）

### 5.1 节点模型与解析（`app/core/v2ray_core.py`）
- NODE 模型：`{type: vless|vmess|trojan|ss|socks|http, remark, addr, port, id, password, security, tls, sni, host, path, flow}`。
- `parse_share_link(line)`：解析 `vless://`、`trojan://`、`ss://`（含 base64 与明文两种）、`vmess://`（base64-JSON 元信息）；解析 `flow/sni/host/path/security/tls` 参数。
- `import_sub(url)`：`urllib` 下载 → 优先 base64 decode（SM=A-Za-z0-9+/= 纯字符判定），再逐行 parse；合并去重（addr+port+id+remark），持久化 `DATA_HOME/proxy/nodes.json`：`{sub_url, updated_at, selected_index, nodes[]}`。
- `import_v2rayn_dir(dir)`：探测 V2rayN 安装目录内 `guiNConfig.json`（主配置，含子进程序列化路径）与 `configN.json` / 其它含 `outbounds[].settings.{vless,vmess,trojan}` 或 `servers` 的 JSON，抽取节点转 NODE 模型；无则返回明确错误。
- `import_clipboard(text)`：`parse_share_link` 逐行追加。

### 5.2 核心进程托管 `class V2rayCoreManager`
- `detect_bin()`：配置 `v2ray_bin` → 用户选择的 V2rayN 目录（优先 `xray.exe`，其次 `v2ray.exe`）→ PATH 搜索 → `DATA_HOME/proxy/bin/`。
- `start(port_http=10809, port_socks=10808, node_index)`：用 NODE 生成 v2ray/xray 兼容 JSON 配置（inbound：socks + http；outbound：按节点类型生成；`streamSettings` 用 xray 兼容子集，v2ray 同样可读）→ 写 `DATA_HOME/proxy/config_{node_id}.json` → `subprocess.Popen([bin, "run", "-c", cfg])` → 探活：经本机 socks 代理请求 `http://www.gstatic.com/generate_204`（超时 3s）→ 成功 → `_set_system_proxy(True)`（winreg：`ProxyEnable=1`、`ProxyServer=127.0.0.1:{http_port}`，备份 `ProxyEnable/ProxyServer/ProxyOverride` 原值）→ 启动监控线程。
- `_monitor_loop()`：每 5s 探活当前节点，记延迟；连续失败 2 次 → 切下一节点（跳过同 addr）重启核心（重写配置 + 重启子进程），emit `v2ray_status`；切换列表耗尽 → 停止核心并还原系统代理，进入「未连接」。
- `_set_system_proxy(on)`：仅本进程存活期间生效；`stop()` 时还原原值；`shutdown()` 挂接 `proxy_stop`。
- `state()`：`{running, core_bin, http_port, socks_port, sys_proxy, node_count, current:{index,remark,addr}, latency_last, healthy}`。
- 幂等清理：`start()` 时读取注册表，若残留 `ProxyServer` 指向 `127.0.0.1:*` 且本进程无核心在跑 → 还原为系统原值（避免崩溃残留）。

### 5.3 新增 `app/bridge/proxy_api.py` — `class ProxyApi`
- `_init_proxy()`：`self._px = V2rayCoreManager(log_callback=lambda m: self.emit("v2ray_log", m))`。
- js_api：`proxy_get_state()` / `proxy_import_sub(url)` / `proxy_import_v2rayn_dir(path)` / `proxy_import_clipboard()` / `proxy_nodes()` / `proxy_select(index)` / `proxy_test(index)`（单点延迟，返回 ms）/ `proxy_delete(index)` / `proxy_set_bin(path)` / `proxy_download_bin(kind)` / `proxy_start(http_port, socks_port)` / `proxy_stop()` / `proxy_set_sys(on)`。
- 事件：`v2ray_log`、`v2ray_status`（透传 state 变更）。
- `shutdown()` 挂接：先 `proxy_stop()` 还原系统代理再收其它线程。

### 5.4 新增前端 `webui/js/pages/v2ray.js`（id:`v2ray`，title:代理管理，icon:`shield`，group:代理加速）
1. **核心与服务控制**：核心路径 + `选择`/`自动下载(xray)`；HTTP/SOCKS 端口；`启动`/`停止`；系统代理开关；状态徽标（运行中+当前节点+延迟）；日志框订阅 `v2ray_log`。
2. **节点导入**：订阅 URL 输入 + `导入订阅`；`选择 V2rayN 目录`；`从剪贴板导入`；显示导入数量结果 toast。
3. **节点列表**：表格 名称/地址/端口/协议/延迟/操作（`测试`、`切换`、`删除`）；当前选中行高亮；手动切换即 `proxy_select` 并重启核心。
- 订阅 `v2ray_status` 实时刷新状态；`show()` 时 `proxy_get_state`。

### 5.5 注册与配置改动
- `config.py` DEFAULTS 增：`v2ray_bin:""`、`proxy_http_port:10809`、`proxy_socks_port:10808`、`proxy_auto_switch:True`。
- `bridge.py`：`ProxyApi` 进 Mixin 列表 + `_init_proxy()` + `shutdown()` 挂 `proxy_stop`；`APP_VERSION` → `"3.1"`。
- `index.html`：追加 `v2ray.js`；`app.js`：`GROUP_ORDER` 插入 `"代理加速"`（协作 之后）。
- 说明：`bindl.py` 复用阶段一，`proxy_download_bin` 下载 xray-core（v2ray 探不到时兜底 PATH/v2rayN 目录即可）。

### 5.6 阶段二测试
- `test_v2ray_parse.py`：vless/vmess(base64-JSON)/trojan/ss（base64+明文）样例断言解析结果；订阅 base64 整体解码；`guiNConfig.json` 样例抽取节点。
- `test_v2ray_core.py`：monkeypatch `subprocess.Popen`/探活/`winreg` → 启动、探活成功设代理、失败切节点（伪探针序列驱动）、耗尽还原、stop 还原、残留清理幂等。
- 回归全量单测 + 冒烟 + 13 页。

## 6. 阶段三：UI 导航定稿 + 文档（v3.2）

### 6.1 导航结构定稿（`app.js` `GROUP_ORDER`）
```
["协作", "共享服务", "网盘挂载", "代理加速", "工具", "系统"]
```
页面归属：
- 协作：剪贴板同步 / 键鼠共享 / 文件传输
- 共享服务：共享文件夹 / FTP / Web
- 网盘挂载：网盘挂载
- 代理加速：代理管理
- 工具：网络发现/文件共享 / 文件管理 / 扫描与映射 / 右键菜单 / 工具箱 / 截图标注
- 系统：设置

### 6.2 交互与样式微调（低风险渐进式，不改组件体系）
- 仅可选调整：`app.css` 导航分组间距/分隔线（`nav-group`/`nav-caption` 110-147 行）、新页面统一 `page-head` 结构；不改既有 13 页逻辑，避免回归。

### 6.3 文档交付
- `功能设计文档.md`：v3.0/3.1/3.2 CHANGELOG；新增「网盘挂载」「代理管理」章节（功能作用/使用场景/关键操作流程/技术要点/与现有模块关系）。
- `技术文档.md`：js_api 清单补 `pan_*`/`proxy_*`（全量更新 count）；新增核心模块（bindl/openlist/webdav_client/v2ray_core）说明；配置项表补丁；`net use` 映射示例与「自动下载机制」说明；CHANGELOG v3.0-3.2。
- `使用文档.md`（新建，零基础向）：两核心组件自动下载/手动放置；网盘挂载 4 步（启动 OpenList → 添加驱动（官方界面或导入配置）→ 应用内浏览/上传/下载 → 映射盘符）；代理管理（导入订阅/节点 → 启动 → 延时与自动切换 → 关闭还原系统代理）；FAQ（下载失败、没网盘目录、切换不生效、系统代理残留）。
- 项目记忆同步：工程约定、踩坑记录。

## 7. 兼容性与稳定性保障

1. **零新增第三方依赖**：`http.client`/`urllib`/`xml.etree`/`winreg`/`subprocess`/`zipfile`/`base64`，打包差异面最小。
2. **外部二进制自动拉取**（用户确认）：GitHub Releases 资产模糊匹配 + 版本缓存；任一步失败给中文错误与手动放置路径；v2ray 核心同时支持复用已装 V2rayN 目录。
3. **系统代理托管**：仅运行期间设置；stop/shutdown/切干自动还原；启动时清理残留（幂等）。
4. **OpenList 驱动配置边界**：不写其 SQLite，驱动增删改走官方界面/文件导入，规避版本兼容破裂。
5. **线程与事件**：新增线程全部 daemon；`shutdown()` 统一收束；进度事件节流（≥200ms）避免刷爆前端。
6. **WebDAV 路径安全**：组件级 URL 编码 + 规范化，拒绝 `..` 逃逸；上传/下载断点由任务取消标志实现。
7. **UI 渐进式**：阶段一只加一个新组与新页，不改既有页面；最终分组在阶段三定稿，全过程 13 页冒烟不回归。

## 8. 验证清单（每阶段门禁）

| 阶段 | 新增测试 | 回归 | 冒烟 |
|------|----------|------|------|
| 一 | test_bindl.py / test_openlist.py / test_webdav_client.py | 既有全部单测 + 场景脚本 | 13 页 + pan 页 |
| 二 | test_v2ray_parse.py / test_v2ray_core.py | 阶段一全量 + 既有 | 14 页 + v2ray 页 |
| 三 | — | 全量 | 15 页（node --check 全部 js） |

交付门禁：单测全绿 + 场景脚本通过 + UI 冒烟无 toast 错误 + 文档（功能设计/技术/使用文档）就绪。

## 9. 文件清单汇总

**新增（14）**
- `app/core/bindl.py`、`app/core/openlist.py`、`app/core/webdav_client.py`、`app/core/v2ray_core.py`
- `app/bridge/pan_api.py`、`app/bridge/proxy_api.py`
- `webui/js/pages/pan.js`、`webui/js/pages/v2ray.js`
- `tests/`：`test_bindl.py`、`test_openlist.py`、`test_webdav_client.py`、`test_v2ray_parse.py`、`test_v2ray_core.py`
- `使用文档.md`

**修改（7）**
- `app/bridge/bridge.py`（Mixin、`_init_pan/_init_proxy`、shutdown、APP_VERSION）
- `app/core/config.py`（DEFAULTS 增 5 项）
- `webui/index.html`（2 个 script）
- `webui/js/app.js`（GROUP_ORDER）
- `webui/css/app.css`（可选导航微调）
- `功能设计文档.md`、`技术文档.md`

## 10. 执行顺序与依赖
1. bindl.py（阶段一/二公用）→ 2. openlist.py + webdav_client.py → 3. pan_api.py + pan.js + 注册 → 4. 测试 → 阶段门禁。
5. v2ray_core.py + proxy_api.py + v2ray.js + 注册 → 6. 测试 → 门禁。
7. GROUP_ORDER 定稿 + 样式微调 → 8. 三份文档 → 9. 全量回归 + 门禁。