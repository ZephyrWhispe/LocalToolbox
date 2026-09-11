# ShareX 式截图能力增强（v3.3）

## Context

用户参考 GitHub ShareX（Windows 经典截图/录制/OCR 工具）完善本应用截图能力。经对照与确认，本轮实现 4 项能力：

1. **区域截图**（拖拽选区，ShareX 最高频能力）
2. **GIF 屏幕录制**（纯 Python + Pillow，无额外二进制）
3. **OCR 识别文字**（系统原生 Windows.Media.Ocr，经 winrt-Python；**依赖缺失自动禁用**，用户已确认）
4. **屏幕取色器 + 放大镜**（Tkinter 全屏覆盖窗口）

现状：已有全屏/窗口截图 + 画布标注编辑（[screenshot.js](webui/js/pages/screenshot.js)、[screenshot.py](app/core/screenshot.py)、[screenshot_api.py](app/bridge/screenshot_api.py)）；HUD 的 Tkinter 线程模式先例（[hud.py](app/core/hud.py)）；`--network-mode` 模式已工程实现，二进制自动下载有 bindl。

版本规划：v3.3（2026-09-09）。遵循三段式分层（core / bridge / page）+ js_api `页面_动作` 契约 + `{ok,data|err}`。

## 实施

### 1. 区域截图（前端遮罩层方案）

> 避开 pywebview 透明窗口跨显示器坑：后端截全屏 → pywebview 内固定全屏遮罩拖选 → 前端裁剪 → 进既有编辑器。

- **core**：无改动（复用 `screenshot.capture_full_png`）。
- **bridge**（[screenshot_api.py](app/bridge/screenshot_api.py)）：新增 `shot_capture_region()` → 调 `capture_full_png()` 返回 `{ok, data:{img, img_w, img_h}}`。
- **前端**：新建 `webui/js/pages/region_capture.js`，暴露 `window.RegionCapture.start({onDone})`；`index.html` 接入 script（置于 screenshot.js 前）。
  - DOM：独立 overlay 节点 append 到 body，`position:fixed; inset:0; z-index:1200`；内含背景 `<img>`（object-fit:contain，记录 scale=显示宽/原生宽）、四块半透明 rgba(0,0,0,.35) 遮罩、选区矩形 div、尺寸标签（W×H）、放大镜 canvas。
  - 交互：mousedown 起→move 更新 rect→mouseup 完成；确认（回车/双击/按钮）→ 原生坐标 `(x1,y1,x2,y2)×(1/scale)` → `canvas.drawImage` 裁剪 → `toDataURL` → 复用现有 `loadFromCapture({img,w,h,mode:"region"})` 进编辑器；ESC/取消移除节点。
  - 放大镜：指针周围 9×9 抠图 → CSS `transform:scale(16)` → 十字准线，`setInterval ~30ms` 跟随更新。
- **screenshot.js**：captureBar 新增「🔲 区域截图」按钮。

### 2. GIF 屏幕录制

- **core**：新建 `app/core/recorder.py` `RecorderManager(on_state, on_done)`：
  - `start(fps)`：fps ∈ {2,5,10} 白名单；daemon 线程 `while` 每 `1000/fps` ms `ImageGrab.grab()` 累积帧列表；`MAX_FRAMES=1200` 自动停。
  - `stop()`：先在采集线程退出，再于该线程编码（避免 jank）：帧数 <2 或图片超 1.4MB → 退化单 PNG；否则 `frames[0].save(path, save_all=True, append_images=frames[1:], duration=1000/fps, loop=0, optimize=True, disposal=[1]+[2]*n)`。
  - 保存：复用 `screenshot.py` 日期子目录/`_safe_label`/`_unique_path`，命名 `rec_日期_时间.gif/png`；新增 `screenshot.save_frames(shot_dir, frames, fps)`。
- **bridge**：新建 `app/bridge/recorder_api.py`（`RecorderApi`，`_init_rec` 实例化 manager，回调 `self.emit`）：
  - `rec_start(fps=None)`（默认 cfg `recorder_fps`）/ `rec_stop()`（立即返回 ok，编码在线程完成后 `rec_done` 事件）/ `rec_get_state()`（`_get_state` 风格，静默记日志）/ `rec_set_fps(fps)`。
  - 事件：`rec_state {running,elapsed,frames}`（节流 emit）、`rec_done {ok,path,frames,size,type}`。
- **bridge.py**：Bridge 继承增 `RecorderApi`，`__init__` 调 `_init_rec()`，`shutdown()` 停录。
- **base.py**：`_JS_API_PREFIXES` 增 `"rec_"`。
- **config.py**：DEFAULTS 增 `"recorder_fps": 5`；`cfg_set` 白名单增 `recorder_fps`（{2,5,10}）。
- **screenshot.js**：加「● 录制 / ■ 停止」按钮 + fps 选择；`App.on("rec_state"/"rec_done")` 更新按钮态与 toast；启动时提示「录制会包含本窗口，建议先最小化」。

### 3. OCR（Windows.Media.Ocr）

- **core**：新建 `app/core/ocr.py`：
  - `is_available()`：惰性 import winrt 包，失败 → False。
  - `recognize_bytes(png_bytes, lang=None) → {text, lines:[{text, rect:{x,y,w,h}}]}`：PIL → RGBA bytes → `SoftwareBitmap.create_copy_from_buffer`（RGBA8）→ OcrEngine：优先 `available_recognizer_languages` 探测 zh-CN，缺失回退 user-profile 语言 → `recognize_async(...).get()` 阻塞（pywebview 6 js_api 独立线程安全）。
  - 不可用统一文案：「缺少 winrt 依赖或系统 OCR 语言包（Win10 1803+ 自带引擎）」。
- **bridge**：`shot_ocr(data_url)`（截图页当前画布图）放 [screenshot_api.py](app/bridge/screenshot_api.py)；`tool_ocr_file()`（工具箱：`create_file_dialog` 选图 → 识别 → 结果文本 + 逐行坐标）放 [tools_api.py](app/bridge/tools_api.py)。
- **requirements.txt**：增 `winrt-Windows.Media.Ocr==3.2.0`、`winrt-Windows.Graphics.Imaging==3.2.0`（本机 Python 3.12 x64 有 cp312 wheel）。
- **前端**：截图页「OCR 识别」按钮 → 结果弹窗（可复制全文/逐行）；工具箱「识别图片」卡。

### 4. 屏幕取色器 + 放大镜

- **core**：新建 `app/core/pickcolor.py`：
  - `rgb_to_hex(r,g,b)` 纯函数。
  - `PickColor`。`run_blocking() → (r,g,b,hex) | None`：Tk 全屏无边框 topmost 窗口（仿 hud 模式），`after(30, _sample)`：`winfo_pointerx/y` → `ImageGrab.grab(bbox=(x-8,…,y+8))` → `resize((144,144), NEAREST)` 像素放大 → PhotoImage 显示 + 十字准线；通道值/HEX 实时显示；空格/回车/点按 → `pyperclip.copy(hex)`（项目已有 pyperclip）返回；ESC/右键 → None。
- **bridge**：[tools_api.py](app/bridge/tools_api.py) 新增 `tool_pick_color()`（js_api 独立线程内阻塞运行，OK）。

### js_api 全量新增（8 个）

`shot_capture_region` / `shot_ocr` / `rec_start` / `rec_stop` / `rec_get_state` / `rec_set_fps` / `tool_ocr_file` / `tool_pick_color`（168 → 176）。

## 测试

- 新建 `test_recorder.py`：mock `ImageGrab.grab` 断言帧数/间隔/≤N 退化 PNG/保存路径/类型。
- 新建 `test_ocr.py`：`is_available=False` 路径；mock OcrEngine 断言 `recognize_bytes` lines 输出。
- 新建 `test_pickcolor.py`：`rgb_to_hex` 断言。
- `test_screenshot.py` 增补：`shot_capture_region` 返回非空 img/w/h。
- 区域拖选裁剪、取色采样、真实 WinRT OCR 属实机项（记录待 T-12 验收）。

## 文档（v3.3 同步）

- [技术文档.md](技术文档.md)：CHANGELOG v3.3、js_api 168→176、配置表 recorder_fps、事件表 rec_state/rec_done、架构新增 3 个 core 模块与 recorder_api。
- [功能设计文档.md](功能设计文档.md)：新章节「截图增强（v3.3）」。
- [使用文档.md](使用文档.md)：截图页（区域截图/录制/OCR）、工具箱（取色器/识别图片）操作说明 + FAQ（winrt 缺失提示）。

## 验证

1. `python test_recorder.py && python test_ocr.py && python test_pickcolor.py && python test_screenshot.py`
2. 全量回归：单测 + `node --check` 前端 JS + UI 冒烟遍历页面无 toast 错误。
3. 实机验证（记录，非本机必需）：区域截图选区内标注、GIF 录制回放、OCR 识别截图文字、取色器锁定复制。