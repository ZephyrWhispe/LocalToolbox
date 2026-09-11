# OpenList 功能扩展（v3.2）实施方案

## Context（背景与目标）

用户要求补齐 OpenList 的完整功能面，共 9 项：服务管理、Rclone 本地挂载、实时监控（运行时间/性能指标）、系统托盘通知、服务控制（重启）、GUI 配置管理、日志监控、更新管理、自启动。当前已有：OpenList 启动/停止/健康监控/配置导入/WebDAV 文件操作、net use 盘符映射、bindl 二进制下载（含版本缓存）。需新增 Rclone 挂载模块、运行指标采样、托盘联动、更新检查流、双层级自启动（OpenList 随应用启动 + 应用开机自启），并对齐 v3.1 已建立的"内核可单测 → js_api → 前端 → 托盘/集成 → 回归"交付模式。

## 方案决策（用户已拍板）

1. **Rclone 挂载：盘符 + 文件夹双支持**（UI 可选目标类型；盘符用 `--network-mode` 在 WinFsp 下免管理员映射，仅字母盘传 `--volname`）
2. **WinFsp 缺失：自动下载 msi + 提权静默安装**（`msiexec /i <msi> /quiet /norestart`，ShellExecute runas 弹 UAC；失败给手动安装指引）

## 阶段一：内核能力（可单测）

**新建 `app/core/rclone_mount.py` — RcloneMountManager**
- 定位：`detect_bin(configured="")` 探测顺序 配置 `rclone_bin` → `DATA_HOME/rclone/bin/rclone.exe`
- `_ensure_config(user, pwd)`：生成 `DATA_HOME/openlist/rclone.conf`（tmp+os.replace 原子写）：remote `openlist` type=webdav url=`http://127.0.0.1:{openlist.port}/dav`（port 取 OpenListManager 当前端口），可选 user/pwd
- `mount(target_type, letter='', folder='', user='', pwd='')`：
  - 检查 `is_winfsp_installed()`，缺失抛"需安装 WinFsp"（带 trigger_install 标志）
  - target_type=letter：先探测盘符未占用（`os.path.exists("%s:\\" % letter)`）
  - `rclone mount openlist: <X: 或路径> --config <conf> --network-mode --volname OpenList --write-back-cache --vfs-cache-mode minimal --log-file DATA_HOME/openlist/rclone.log`（`--network-mode`/`--volname` 仅 letter 目标传）
  - 记录 proc（daemon 线程 Popen）+ `_mounts` 表 {target, proc, ts}
  - 启动后短暂探测挂载点；`--network-mode` 需要较新 WinFsp，失败时报"建议升级 WinFsp 或更换为文件夹挂载"
- `umount(target)`：`rclone umount openlist: <target>` → 超时兜底 `taskkill /PID`；从 `_mounts` 移除
- `list_mounted()`：遍历 `_mounts`，`os.path.lexists(target)` 验活，返回 [{target, pid, ts}]
- `start/stop/restart` 幂等；`state()` 供 get_state
- 依赖 OpenList 运行（url 需 port）；port 从 openlist.port 取

**新建 `app/core/winfsp.py`**
- `is_winfsp_installed()`：查注册表 `HKLM\SOFTWARE\WOW6432Node\WinFsp`（或文件 `C:\Program Files (x86)\WinFsp\bin\winfsp-x64.dll`）
- `install_winfsp(msi_path)`：非管理员 → `ShellExecuteW("runas")` 提权跑 `msiexec /i <msi> /quiet /norestart`，返回 ok/err；管理员直接 run
- 失败返回手动安装指引（WinFsp 官网下载链接）

**`app/core/bindl.py` 扩展**
- `_REPOS` 新增：
  - `"rclone": {"repo": "rclone/rclone", "match": re.compile(r"rclone-.*windows-amd64\.zip", re.I), "exe": "rclone.exe"}`
  - `"winfsp": {"repo": "winfsp/winfsp", "match": re.compile(r"winfsp-.*\.msi", re.I), "exe": "winfsp-*.msi", "raw": True}`（msi 为裸文件落盘）
- 新增 `latest(kind)`：复用 `_latest_release` 取 tag，不下载 → `{"version": tag}`，供"检查更新"对比 `_load_cache()[kind].version`

**`app/core/openlist.py` 增强**
- `restart(host, port, fw)`：stop → start（幂等，未运行仅 start）
- `start` 成功记录 `self._start_ts = time.time()`；`state()` 增 `uptime`（运行秒数）
- 性能采样：新增 `_metrics_loop` 线程（2s）：PowerShell `Get-Process -Id {pid}` 取 `CPU` 差速% 与 `WorkingSet64`（MB），存 `self.metrics = {"cpu":…, "mem_mb":…}`；首轮 null 幂等降级（参照 traffic.py 采样先例）；`state()` 并入
- `tail_log(lines=200)`：扫 `data_dir` 下 `*.log` / `rclone.log` 最新文件尾 N 行返回字符串（带文件路径），缺失返回空
- 安全网：monitor 检测到异常退出时 `log_callback` 带标记（供托盘通知去抖）

## 阶段二：js_api 桥接（bridge/pan_api.py / bridge.py / config.py）

**config.py DEFAULTS 新增**（cfg_set 白名单同步补）：
`openlist_host`("127.0.0.1")、`openlist_port`(15244)、`openlist_fw`(True)、`openlist_autostart`(False)、`rclone_bin`("")、`rclone_autostart`(False)、`rclone_user`("")、`rclone_pwd`("")、`rclone_mount_type`("letter")、`rclone_mount_target`("V")、`app_autostart`(False)

**pan_api.py**
- `_init_pan` 建 `self._rclone = RcloneMountManager(...)`、`_metrics_last`
- 新方法（均 `{ok,data}`/`{ok,err}`，长操作走 `_pan_pool`）：
  - `pan_restart(host, port, fw)`：OpenList restart
  - `pan_mount_letter(letter, user, pwd)` / `pan_mount_folder(path, user, pwd)`：rclone 挂载
  - `pan_umount(target)`
  - `pan_rclone_mounted()`
  - `pan_winfsp_status()` → {installed, msi_ready}; `pan_install_winfsp()` → 触发提权安装（对话框按钮触发，绝不后台静默装）
  - `pan_check_update()` → [{kind, local, latest, need}]（openlist + rclone）
  - `pan_update_bin(kind)` → bindl 下载新版本（进度走 `pan_task`）
  - `pan_set_app_autostart(on)` / `pan_get_app_autostart`：HKCU Run 键读写（写 `sys.executable`/exe 路径）
- `pan_get_state` 并入：uptime/cpu/mem、rclone state（installed/mounted）、update 摘要
- 事件新增 `pan_metrics`（{cpu, mem_mb, uptime}，2s 节流推送），沿用 `pan_log`/`pan_task`
- `_stop_pan` 补 `self._rclone.stop()`
- 托盘：Bridge 挂 `self.tray = None`；openlist 异常退出/重启/挂载完成时 `try: self.tray.notify(...)`，去抖避免重复

**bridge.py**：`__init__` 末尾若 `openlist_autostart` 则 `threading.Timer(3, pan_start(host,port,fw))` 自动启服务

## 阶段三：前端（webui/js/pages/pan.js）

**卡片 1 OpenList 服务**：增 运行时间/CPU/内存 展示（`pan_metrics` 订阅）、重启按钮、数据目录行（只读 + 打开目录）、自启动开关（openlist_autostart）
**卡片 2 Rclone 挂载**（新卡）：
- WinFsp 状态行（已安装/未安装 + 「安装 WinFsp」按钮 → pan_install_winfsp，成功后刷新）
- 目标类型单选（盘符/文件夹）+ 盘符 input 或 路径 input + 账号/密码 + [挂载] [卸载]
- 已挂载列表（target + pid + 时间 + 卸载按钮，刷新）
- rclone 程序行（rclone_bin 显示 + 自动下载）
**卡片 3 更新管理**：显示 openlist / rclone 本地版本 vs 最新版本 + [检查更新] [更新]（进度 toast）
**卡片 4 盘符映射（net use，保留）**、**卡片 5 文件浏览（保留）**——日志查看并入卡片 1 增"日志查看"区（tail_log + 手动/自动刷新 + 清空按钮）

## 阶段四：托盘与集成（main.py）

- main 创建 TrayController 后 `bridge.tray = tray`
- OpenList 异常退出 → `try: tray.notify("OpenList 已停止（异常退出）")`；自动重启成功/挂载完成 → notify
- app_autostart 开关在「设置」页新增一行（HKCU Run）

## 阶段五：测试与文档

**单测**：test_openlist.py 增补（restart 幂等 / uptime / tail_log（临时 .log）/ metrics 降级）；新建 test_rclone.py（config 生成、mount 命令组装、umount 兜底、list_mounted、winfsp 检测 mock）；test_bindl.py 增补（rclone/winfsp spec、`latest()` 假 `_latest_release`）
**回归**：全量单测 + 7 场景脚本 + `node --check`（pan.js）+ `test_ui_smoke.py`（15 页）
**文档**：技术文档 v3.2（CHANGELOG、js_api 计数、配置键表、事件表、数据目录）、功能设计文档 §17、使用文档（Rclone 挂载/WinFsp/更新/自启动章节）

## 风险与注意

1. `--network-mode`/`--volname` 仅字母盘传；老版 WinFsp 挂字母盘失败 → 明确报错并建议文件夹挂载
2. WinFsp msi 静默安装必弹 UAC，仅由用户点击「安装 WinFsp」触发
3. 盘符冲突：mount 前 `os.path.exists("X:\\")` 探测；net use 与 rclone 同盘符共存冲突由用户规避
4. pywebview js_api 非 async：长操作全走线程池 + 事件回推
5. CPU 差速首轮 null 降级不抛；托盘通知去抖避免连续弹