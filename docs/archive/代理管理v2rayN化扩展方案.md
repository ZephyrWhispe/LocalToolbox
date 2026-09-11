# 代理管理 v2rayN 化扩展方案（三核心 + 模式分流 + TUN + GeoIP + 高级编辑）

## Context

现状：v3.0 交付的「代理管理」页仅支持 xray/v2ray 两核心、全局代理单一模式。
用户要求向 v2rayN 对齐：**三核心切换（xray / sing-box / v2ray）**、
**代理模式（全局 / 智能分流 / 直连）**、**流量统计 + 批量测速**，并顺带填充
工具箱 grid-2 第 12 格空位。范围确认（用户二次补充）：**TUN 模式、GeoIP 规则库、
高级路由/DNS 编辑也要做**。不做：二维码导入、链式代理、NaiveProxy 等其余。

直连模式 = 停止核心 + 还原系统代理。
设计原则：不引入 grpcio 等新依赖；保持双端口（HTTP 10809 / SOCKS5 10808）语义；
新增高风险能力（TUN/GeoIP）带降级路径，真机验证并入 T-12 验收。

## 设计要点

### 1. 核心抽象（app/core/v2ray_core.py）
- 常量 `CORE_XRAY / CORE_SING / CORE_V2RAY = "xray" / "sing-box" / "v2ray"`。
- `build_core_config(node, http_port, socks_port, core_type, mode)` 分派：
  - xray/v2ray → 现有生成器（同构 JSON），路由由 mode 注入；
  - sing-box → 新 `build_singbox_config`：
    - outbound 映射：vless→`{type:"vless", server, server_port, uuid, tls, transport(ws/grpc/h2)}`；
      vmess→`{type:"vmess", uuid, alterId:0, security}`；trojan→`{type:"trojan", password}`；
      ss→`{type:"shadowsocks", method, password}`；
    - inbound：双端口（http 10809 + socks 10808），不用 mixed 以免破坏系统代理语义；
    - TUN 启用时附加 `{"type":"tun", "address":[172.19.0.1/30, fdf4::/64], "auto_route":true, "stack":"mixed"}`。
- `_start_core`：xray/v2ray = `[bin, "run", "-c", cfg]`；sing-box = `[bin, "run", "-D", PROXY_DIR, "-c", cfg]`。
- `detect_bin` 增加 sing-box.exe 候选（configured / V2rayN 目录 / `PROXY_DIR/bin`）。

### 2. 二进制自动下载（app/core/bindl.py）
- `_REPOS` 新增：
  - `sing-box` → `{repo:"SagerNet/sing-box", match:r"sing-box-.*windows-amd64\.zip", exe:"sing-box.exe"}`；
  - `geoip` → `{repo:"v2fly/geoip", match:r"geoip\.dat$"}`（解出 `geoip.dat` → `proxy/bin/`）；
  - `geosite` → `{repo:"v2fly/geosite", match:r"geosite\.dat$"}`；
  - `wintun` → `{repo:"Wintun-builds" 或 wintun.net zip, match: "wintun-.*/amd64/(wintun\.dll)"}`（解出 `wintun.dll` → `proxy/bin/`）。
- `proxy_download_bin(kind)` 支持 `sing-box/geoip/geosite/wintun`；`bins` 含三核心路径。

### 3. 模式与智能分流
- `mode ∈ global / smart / direct`：
  - `global`：全走代理；
  - `smart`（**xray/v2ray：GeoIP 规则**）：routing.rules 含
    `geoip:private`、`geoip:cn`、`geosite:cn`（geo 文件在 `proxy/bin/geoip.dat|geosite.dat`，
    未下载则 `bindl` 自动拉取；下载失败降级为内置静态规则并提示）；
    **sing-box：内置静态规则**（CIDR `10/8,172.16/12,192.168/16,127/8,169.254/16,224/4,
    114.114.114.114/32,223.5.5.5/32` + `SMART_DIRECT_RULES` 域名关键字 `.cn,gov.cn,baidu.com,
    taobao.com,alipay.com,qq.com,tencent.com,weixin,163.com,sina,bilibili,douyin,jd.com,
    mi.com,meituan,12306` 等 15–25 条），末位兜底走代理；同步注明"sing-box 智能分流暂用
    内置静态规则，GeoIP 规则库仅 xray/v2ray 生效"；
  - `direct`：`proxy_start` 短路为 `stop()+还原系统代理`。
- 高级配置（见 §6）可整体覆盖 routing/dns 段。

### 4. 状态持久化
- config.py DEFAULTS 新增：`v2ray_core:"xray"`、`v2ray_mode:"smart"`、`v2ray_tun:false`；
- 批量测速延迟写 NodeStore node dict `latency` 字段；高级配置段存 `DATA_HOME/proxy/advanced.json`（仅允许 routing/dns 键）。

### 5. 流量统计（零新依赖）
- 新增 `app/core/traffic.py`：`TrafficMonitor` daemon（1.5s 采样）：
  - 仅 **xray**：配置含 api inbound(dokodemo-door) + policy.stats + stats:{}，
    采样 `[bin, "api", "stats", "--server", 127.0.0.1:APIPORT]` 解析 up/down 计数，
    `speed = Δcounter/Δt`；
  - **v2ray / sing-box：标注「该核心暂不支持实时统计」**（避免 grpcio）；
  - 经 `proxy_traffic` 事件推送（≥1s 节流）。

### 6. TUN 模式（仅 sing-box）
- 入口：卡片「TUN 模式」开关（核心非 sing-box 时禁用并提示"仅 sing-box 支持"）；
  运行时同时启用=系统代理 + TUN 双通道（v2rayN 行为）。
- **提权运行**：TUN 需管理员——`Popen` 前检测 `is_admin()`；非管理员弹 UAC
  （`ShellExecuteW("runas")` 以管理员启动 sing-box 子进程，配置/日志/探活沿用；
  UAC 拒绝或提权失败 → 中文报错提示，不静默）；
- **wintun 依赖**：启动检查 `proxy/bin/wintun.dll`，缺失自动下载（bindl）；
- 兜底：sing-box 退出时 auto_route 自动还原路由表；监控线程发现 TUN 进程异常 → 状态复位并提示；
- 单测覆盖配置生成与权限检测分支；**真机验证并入 T-12**（需管理员环境）。

### 7. 桥接与前端
- proxy_api 新增：`proxy_set_core(kind)`、`proxy_set_mode(mode)`、`proxy_set_tun(on)`、
  `proxy_burst_test()`（独立临时端口/配置并发测延迟，回写 store.latency，emit 进度；
  `finally` 清理，不动运行中核心）、`proxy_traffic`、`proxy_get_advanced()` /
  `proxy_set_advanced(json_str)`（校验仅 routing/dns，落盘 advanced.json）；
  `proxy_start` 读取 core/mode/tun/advanced；`proxy_get_state` 返回对应字段 + 二进制路径 + geo 文件状态。
- v2ray.js 卡片1：核心类型 select（xray/sing-box/v2ray）、代理模式 select（全局/智能分流/直连）、
  TUN 开关（sing-box 才可点）、顶部速度条（up/down）、高级配置入口按钮；
  节点卡片：批量测速按钮 + 延迟列；高级编辑模态框（routing/dns JSON 文本 + 保存）。
- tools.js 第 12 格：`mountProxyQuickSwitch()`（proxy_get_state → 运行状态/模式 + 开启/停止按钮，
  复用 grid-2 card）。

### 8. 测试与验证
- test_v2ray.py 复用 `make_manager`（临时 NodeStore + mock PROXY_DIR + FakeProc）新增：
  sing-box 配置生成快照（vless/vmess/trojan/ss + TUN 开关）、三核心 detect_bin、
  mode→路由断言（global 无直连规则 / smart 含 geoip 或静态规则）、set_core/set_mode/set_tun 持久化、
  TrafficMonitor mock 换算、burst_test 聚合、advanced.json 合并与注入校验、bindl 四类 spec 匹配；
- 验证链：`python test_v2ray.py` → 全量 `test_*.py` 回归 → UI 冒烟 15 页 → 18 个 JS
  `node --check` → 重新打包 exe 冒烟；真机项（TUN 提权/GeoIP 分流效果）入 T-12 验收。

## 改动文件清单
- 改：`app/core/v2ray_core.py`、`app/core/bindl.py`、`app/core/config.py`、
  `app/bridge/proxy_api.py`、`webui/js/pages/v2ray.js`、`webui/js/pages/tools.js`、`test_v2ray.py`；
  文档（技术/功能设计/使用文档 CHANGELOG v3.1）
- 新增：`app/core/traffic.py`

## 实施顺序
1. v2ray_core.py：常量/分派/sing-box 生成器/TUN 段/mode 路由/detect/advanced 合并（单测先行）
2. traffic.py + xray stats 配置生成
3. config.py 三键；bindl.py 四类 spec
4. proxy_api.py 新方法 + burst_test + advanced
5. v2ray.js + tools.js 前端
6. 全量回归 + JS 检查 + 打包冒烟 → 文档同步（v3.1）