# LocalToolbox

Windows 桌面效率工具箱：单机增强 + 局域网协作一体化。基于 Python + pywebview，全部功能优先使用 Windows 系统原生 API（WinRT / CIM-WMI / COM / netsh / 注册表），不捆绑第三方内核，本地优先、不后台上传数据。

## 功能总览

侧栏按场景分为 7 组：

### 互联与协作
- **剪贴板**：历史记录（热键呼出 / Win+V 弹窗接管）、多设备剪贴板同步、可选落盘加密、敏感信息模糊化
- **传输**：局域网文件/文件夹高速传输（TCP），自动接收、进度 HUD、传输历史
- **键鼠共享**：跨设备键鼠控制（被控端监听可自启，防火墙规则自动回收）
- **网络**：设备发现面板、备注别名、配对加密

### 共享服务
- **服务总览**：全部服务状态卡片 + 一键启停
- **FTP服务**：本地目录发布为 FTP 服务器（匿名/账号、写权限、防火墙自动放行）；亦可作为客户端连接远程 FTP 浏览传输
- **HTTP 共享 / WebDAV 服务**：把任意目录一键发布为下载站或 WebDAV
- **扫描**：局域网主机 / 端口扫描

### 笔记与安全
- **备忘录**：Markdown 笔记、分组管理、全局热键呼出弹窗速记
- **密码库**：PBKDF2 + AES-256-GCM 加密存储、自动锁定；支持 Chrome / Edge / Firefox / KeePass / 1Password / Bitwarden 等 CSV/JSON 导入，导出为 Chrome 兼容 CSV / 明文 JSON / 加密 `.lvt` 容器

### 网盘挂载
- **OpenList + rclone**：WebDAV 网盘挂载为本地盘符，账号密码管理、开机自启恢复

### 代理网络
- **V2rayN**：多核心（xray 等）、订阅管理、节点延迟测试、系统代理一键开关

### 工具与增效
- **工具箱**（Ctrl+K 快速直达，收藏 + 最近使用）：
  - 转换与编码：文本工具箱（JSON/XML/SQL 格式化、Base64 等编解码、文本对比）、时间戳、进制转换（BigInt 位运算）
  - 文件与校验：哈希计算、.md5 清单校验、目录快照、CSV 列表导出
  - 生成与安全：随机密码（熵评估）、UUID v4/v7、二维码、正则测试
  - 屏幕与图像：屏幕取色、调色板提取、图片编辑 / 合并 / 分割 / 批量处理、视频编辑
  - 网络工具：端口占用查询与释放、网卡 DNS 快速切换、Wi-Fi 密码查看、系统代理开关
- **截图标注**：区域圈选（自动识别窗口）/ 全屏 / 活动窗口 / 延迟截图 / 滚动长截图；ShareX 式标注工具（画笔、高亮、形状、箭头、步进编号、马赛克、裁剪等 15 种）；图像效果（圆角 / 灰度 / 反色 / 缩放）；MP4 / GIF 录制；贴图钉屏、多图床上传
- **OCR**：Windows.Media.Ocr 系统引擎离线识别
- **文件管理**：双栏管理器，F2 重命名、递归搜索、批量操作
- **右键菜单**：自定义 Windows 资源管理器右键菜单项

### 系统
- **系统优化**：隐私遥测 / 任务栏 / 服务等数十项优化，全部快照可逐项还原；应用管理（注册表 + UWP 多源检测、批量静默卸载 / 升级）；winget 软件安装；更新策略；垃圾清理；系统修复（SFC / DISM）
- **硬件信息**：CPU / 内存 / 显卡 / 磁盘 / 网卡 / 主板全景与实时使用率（原生 CIM/WMI，只读）
- **防火墙**：配置文件（域/专用/公用）开关、规则搜索 / 筛选 / 启停 / 删除、一键禁止应用联网、出站锁定模式、.wfw 备份恢复、操作审计（基于 INetFwPolicy2 COM）
- **显示器信息**：分辨率 / 缩放 / 多屏布局
- **无人值守安装**：生成微软官方 autounattend.xml，可联动预装系统优化
- **设置**：主题（深浅色 / 跟随系统）、强调色、界面缩放、全局热键、托盘行为、配置导出导入、WebDAV 整机备份

## 环境要求

- Windows 10 / 11（x64）
- Python 3.12

## 快速开始

```bat
git clone https://github.com/ZephyrWhispe/LocalToolbox.git
cd LocalToolbox
pip install -r requirements.txt
run.bat
```

管理员权限非必需；涉及防火墙写操作、UWP 卸载、系统优化等动作时，可点状态栏右下角「提权重启」一次性提权。

## 打包发布

```bat
build.bat
```

PyInstaller 打包输出至 `dist/`，并自动生成 SHA-256 清单（`dist/sha256sums.txt`）。exe 体积较大（约 100MB+），分发建议走 GitHub Release 附件。

## 测试

```bat
test.bat
```

包含 570+ 项 pytest 用例（后端逻辑 / 桥接层契约）与 UI 冒烟测试（真实窗口遍历全部页面、断言 0 JS 报错）。

## 项目结构

```
├── main.py                # 入口：窗口 / 托盘 / 单实例
├── app/
│   ├── bridge/            # js_api 桥接层（按页面混入，命名 页面_动作）
│   └── core/              # 业务核心（防火墙、剪贴板、优化器、密码库…）
├── webui/                 # 前端（原生 JS + 组件化 App.*，无框架）
├── tests/                 # pytest 用例 + UI 冒烟 + 基线
├── scripts/               # 探针与辅助脚本
├── run.bat / test.bat / build.bat
└── LocalToolbox.spec      # PyInstaller 配置
```

## 安全说明

- 剪贴板历史与密码库支持加密落盘，密钥经 Windows DPAPI 保护
- 设备配对传输可强制加密；关闭配对要求时传输明文需逐次确认
- 防火墙、系统优化等危险操作均带二次确认与自动备份 / 快照还原
- 所有数据保存在本机 `%APPDATA%/LocalToolbox`，不内置任何上报通道

## 参考与致谢（复刻/借鉴项目全表）

本工具在功能设计过程中系统性地参考了以下开源项目——仅借鉴**功能定位、交互形态与架构思想**，全部实现均为独立重写：未复制、未改写、未打包任何参考项目的源码。GPL/AGPL 系项目仅作思想与事实性技术细节参考（许可证合规）；MIT 系项目可参考其实现思路。

| # | 项目 | 许可证 | 借鉴方向 | 对应本工具功能 |
|---|---|---|---|---|
| 1 | [ShareX](https://github.com/ShareX/ShareX) | GPL-3.0 | 截图/录屏/标注工具集、After Capture 任务链、自定义上传器（HTTP 模板 + 响应提取 URL） | 截图标注页全部能力、上传目标管理 |
| 2 | [Ditto](https://github.com/sabrogden/Ditto) | GPL-3.0 | 剪贴板历史弹窗（Win+V 接管）、分组/粘性条目、本地优先无遥测 | 剪贴板弹窗、历史加密、剪贴板同步 |
| 3 | [CopyQ](https://github.com/hluk/CopyQ) | GPL-3.0 | 剪贴板条目自定义命令扩展点 | 剪贴板自定义命令（受限版，脚本 API 主动裁剪） |
| 4 | [WinUtil](https://github.com/ChrisTitusTech/winutil) | MIT | 系统优化清单（隐私遥测/服务/更新策略）、可还原思路 | 系统优化页（清单经本地化改造，全部快照可还原） |
| 5 | [Winhance](https://github.com/memstechtips/Winhance) | GPL-3.0 | 系统配置/应用管理的三合一体裁（UWP + 可选功能 + 旧版能力） | 系统优化 · 应用管理（交叉验证参照） |
| 6 | [UniGetUI](https://github.com/marticliment/UniGetUI) | GPL-3.0 | winget 包管理交互：静默安装/升级检测、多源检测工厂 | 软件安装、全量已装应用中心、可升级应用 |
| 7 | [simplewall](https://github.com/henrypp/simplewall) | GPL-3.0 | 按应用管理防火墙、默认拒绝的交互思想（不碰 WFP 的边界论证） | 防火墙页 · 一键拦截 / 锁定模式 |
| 8 | [Minimal Firewall](https://github.com/deminimis/minimalfirewall) | AGPL-3.0 | INetFwPolicy2 管理路线、"前端不碰 WFP 以免与系统规则冲突"的论证 | 防火墙页技术选型依据 |
| 9 | [Portmaster](https://github.com/safing/portmaster) | GPL-3.0 | 应用级防火墙的能力形态（驱动路线经评估后主动裁剪） | 防火墙方案调研（未采纳其路线） |
| 10 | [OpenList](https://github.com/OpenListTeam/openlist) | AGPL-3.0 | 多存储后端统一管理、多协议服务架构（本工具以独立进程 + HTTP API 边界集成） | 网盘挂载、服务总览 |
| 11 | [OpenList Desktop](https://github.com/OpenListTeam/openlist-desktop) | 见仓库 | 桌面端服务实时监控 + 托盘集成形态 | 服务总览页、托盘集成 |
| 12 | [v2rayN](https://github.com/2dust/v2rayN) | GPL-3.0 | 代理核心管理形态、发布完整性（SHA-256 清单）流程 | V2rayN 页、Release 校验清单 |
| 13 | [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev) | GPL-3.0 | 分层配置管理、配置原子性更新（写前备份 + 启动回滚独立实现） | 配置导入导出、分流规则管理 |

> 合规声明：上表所有项目的实现代码零复制、零改写、零打包；仅功能域、交互流程与不受版权保护的事实性技术细节（键路径、API 端点、字段名等）被借鉴。各项目许可以其仓库为准。

## 许可证

本项目暂未附带开源许可证，默认保留所有权利。如需引用代码请先开 issue 沟通。
