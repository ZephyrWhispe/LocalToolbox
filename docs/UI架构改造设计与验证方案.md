# UI 架构改造设计与验证方案（Windows 10 为主 · Windows 11 适配）

> 目标：在不破坏现有功能的前提下，分批次偿还 UI 架构债（页面生命周期、任务中心、设计系统、Fluent 一致性）。
> 主平台：**Windows 10 22H2 (19045) 为第一优先级**；Windows 11 22H2+ 为适配增强路径。
> 宿主：pywebview 6.2（winforms 后端 + WebView2）+ Python 单进程 + 托盘常驻。
> 原则：**每一项改动都必须"可验证、可回退、可灰度"**。

---

## 0. 不破坏承诺（Invariants）

这些是硬约束，任何批次都不得违反。违反即回滚该提交。

| # | 不变量 | 依据 | 校验方式 |
|---|---|---|---|
| I1 | js_api 方法签名与返回结构不变：`{ok:True,data}` / `{ok:False,err}` 只增字段、不改字段、不删方法 | `app/bridge/base.py:1-11` 约定 | `scripts/check_contract.py` + `tests/test_*_api.py` |
| I2 | 页面 id 不变。`start_page` 配置、`PAGE_FOR_CLI` 右键菜单映射、托盘/CLI 深链都按 id 引用 | `webui/js/app.js:6`、`webui/js/app.js:463` | 回归矩阵用例 17 |
| I3 | 推送事件名冻结：`shot_pick`/`shot_capture_done`/`xfer_*`/`clip_*`/`ocr_*`/`update_progress`/`proxy_state`/`app_log`/`cli_action` 等只增不改 | `base.py:109` `emit()` + 前端 `App.on()` | `scripts/check_contract.py` 双向差集 |
| I4 | 页面注册契约向后兼容：`mount(el)` 旧签名必须继续可用 | `webui/js/app.js:168`、26 个页面 | L2 UI 冒烟遍历全部页面 |
| I5 | 配置键不可变。新增键写入 `DEFAULTS`，读取方必须容忍缺失 | `app/core/config.py:13`、`config.py:186-189` | `check_contract.py` 检查前端读取的 cfg 键 ∈ DEFAULTS |
| I6 | 原生行为不变：截图遮罩、剪贴板弹窗、录屏、全局热键、托盘、单实例、窗口几何记忆 | `main.py:197-296` | 回归矩阵用例 1-7、17 |
| I7 | 生产窗口配置与测试窗口配置**同源** | 见 §3.3（当前不一致，须先修） | L2 冒烟断言窗口参数 |

### 本阶段明确"不改"的东西（避免引入风险）

| 不改 | 原因 |
|---|---|
| 不引入前端框架（Vue/React/Preact），不引入构建链（esbuild/webpack） | 27 个文件 1.4 万行前端 + "零构建直跑源码"工作流（`run.bat`/`LocalToolbox.spec` 直接指向 `webui/`）。收益 vs 回归面不成比例，留待长期 |
| 不改 `document.body.style.zoom` 缩放机制（`app.js:27`） | 12 种 px 字号全靠 zoom 缩放，改 rem/变量牵动全部 CSS。**只验证、只文档化**，不改 |
| 不改 `region_capture.js` 的 overlay 布局（16 处 `style.cssText`） | 全屏 kiosk 窗口 + 与截图坐标强耦合（`region_capture.js:160-162`），收益小、回归代价大。只统一色值 |
| 不改任何页面 id、不改任何推送事件名、不删任何 bridge 方法 | I1-I3 |
| 不做 Win11 专属的 Snap Layouts（HTMAXBUTTON 子类化） | 需要 `SetWindowLongPtr` 子类化 WinForms 消息循环线程，与 pywebview 桥接线程模型冲突，风险高。列为长期项（§3.3.4） |

---

## 1. 总体设计：三层改造 + 能力协商

```
┌──────────────────── 前端（webui/） ────────────────────┐
│  ①PageCtx 页面上下文（生命周期/可见性治理）              │
│  ②设计 Token + 10 个组件原语（视觉一致性）              │
│  ③能力驱动的 UI（读 cap_get，缺能力即降级旧行为）        │
└────────────────────────┬───────────────────────────────┘
                         │  js_api（只增不改）/ emit（事件名冻结）
┌────────────────────────┴───────────────────────────────┐
│  ④ win_shell.py 窗口外壳（Win10 blur / Win11 Mica）     │
│  ⑤ cap_api.py 能力探测（前端降级的唯一依据）            │
│  ⑥ taskbus.py 任务聚合（旁路，不改旧事件）              │
│  ⑦ win_spec.py 窗口工厂（生产/测试同源）                │
└──────────────────── 后端（app/） ──────────────────────┘
```

**核心设计思想：能力协商（Capability Negotiation）。**
前端任何新 UI 都不是"无条件启用"，而是先问后端"这台机器支持什么"，然后**只启用支持的部分，其余走原有路径**。这是"调整后不影响功能使用"的技术底座——老系统/老 WebView2 上自动退回今天的表现，不需要分支代码。

---

## 2. 批次划分（每批独立可交付、独立可回滚）

| 批次 | 内容 | 工期 | 风险 | 触碰文件数 | 运行时开关 |
|---|---|---|---|---|---|
| **A** | 契约与反馈修复（零架构风险） | 0.5 天 | 极低 | 3 | 无 |
| **B** | 窗口工厂同源 + 验证体系落地 | 1 天 | 低 | 4 | 无 |
| **C** | PageCtx 页面上下文 + 可见性治理 | 2-3 天 | 中 | 12 | `ui_ctx_guard` |
| **D** | 设计 Token + 组件原语 + 字体/尺寸对齐 | 3-4 天 | 中 | 32 | `ui_token_v2` |
| **E** | 能力探测 + 窗口外壳（Win10 blur / Win11 Mica） | 3-4 天 | 中高 | 6 | `ui_material` |
| **F** | 任务中心 + 状态栏入口 | 3-4 天 | 中 | 10 | `ui_taskcenter` |
| **G** | 导航返回栈 + 分组收敛 + 焦点管理 | 2 天 | 低 | 5 | 无 |

**执行顺序：A → B → C → D → E → F → G。** A/B 是地基（B 的验证体系是后面所有批次的验收工具），C 消除最严重的资源泄漏，D 结束后视觉层稳定，E 依赖 B 的窗口工厂与 C 的确定性窗口行为，F 依赖 D 的原语。

---

## 3. 详细设计

### 3.1 批次 A：契约与反馈修复

**A1. Toast 语义别名（一处修 22 处）**

`webui/js/ui.js:89-98` 现在的类名是直拼：`class: "toast " + (kind === "info" ? "" : kind)`。全仓 22 处传 `"success"`，而 `app.css` 只定义 `.toast.ok/.error/.warn`，导致这些成功提示**完全无样式**（分布：`editor.js:184,210,284,299,323,351,361,697`、`batch.js:252`、`cliphist.js:213,221,233,242,279`、`combine.js:110,118`、`split.js:80,104,110,118`、`video.js:45,49,53`）。

```js
// ui.js 内新增，向后兼容：
const TOAST_ALIAS = { success: "ok", fail: "error", warning: "warn" };
function toast(msg, kind, ms) {
  kind = TOAST_ALIAS[kind] || kind || "info";
  ...
}
```
**双保险**：`app.css` 补 `.toast.success { /* 与 .ok 同规则 */ }`（防止其他代码路径直接拼类名）。

**A2. 设计 token 单一来源（为 D 铺路，不改任何视觉）**

把 `app.css:1-47` 的变量段与 `app.css:116-129` 的动效段原样抽出到 `webui/css/tokens.css`，`app.css` 顶部 `@import`。三个浮窗（`webui/overlay.html`、`clip_pop.html`、`memo_pop.html`）改为 `<link rel="stylesheet" href="css/tokens.css">`。
- **零视觉变化**：只搬家，不改值。
- 注意：`webui/css/tokens.css` 是新增文件，PyInstaller 打的是整个 `webui` 目录（`LocalToolbox.spec` 的 `datas=[('webui','webui')]`），无需改打包清单；但 `run.bat` 直跑源码时路径不变，同样无需改。

**A3. 消除 `.cliphist-item` 双定义覆盖事故**

`app.css:846-851` 与 `app.css:963-967` 对 `.cliphist-item`、`.cliphist-list` 定义冲突（padding 10/9、radius 9/10、背景 `--card2`/`--card` 两套），`.cliphist-text`（859）与 `.cliphist-item .ch-text`（978）是两套并行文本类。
- 处理：保留**后定义**（963-967，即当前实际生效的视觉），删除 846-851 的重复声明；把 `cliphist.js` 中对 `.cliphist-text` 的引用统一到 `.ch-text`。
- **风险控制**：改前先截图 `#/cliphist` 作为视觉基线，改后像素比对（见 §4.5）。

**A4. 设备轮询分档（`app.js:389-403`）**

保持"无变化不重绘"的现有优化（注释解释了必要性：hover 按钮会因 DOM 重建点不到），只把固定 3s 改为分档：有设备 3s、无设备 10s、窗口隐藏（`document.visibilityState === "hidden"` 或主窗口被托盘隐藏）时 30s。**语义不变，只降频。**

---

### 3.2 批次 B：窗口工厂同源 + 验证体系落地

这是整个方案的"可验证性"地基，必须先做。

**B1. 窗口参数工厂 `app/core/win_spec.py`（新增）**

现状问题：`main.py:232-246` 的生产窗口是 `frameless=True, shadow=False, easy_drag=True, background_color="#0f1419"`，而 `tests/test_ui_smoke.py:110-112` 建的测试窗口**这三个参数全没传**——也就是说**冒烟测试从来没有测过生产窗口形态**。窗口形态决定了标题栏高度、拖动行为、DPI 换算，是最容易出回归的地方。

```python
"""主窗口参数工厂：生产与测试共用同一份配置，防止"测试通过但生产不同"。

所有新增窗口能力（transparent / 材质）都必须经此处下发，保证
main.py 与 tests/test_ui_smoke.py 拿到的是同一套窗口形态。
"""

def window_kwargs(cfg, *, geo=None, overrides=None):
    """返回 webview.create_window(...) 的关键字参数。

    geo: _restore_geometry(cfg) 的结果（宽高/坐标）
    overrides: 测试用覆盖（如 {"transparent": False}）
    """
    kw = {
        "width": (geo or {}).get("width", 1320),
        "height": (geo or {}).get("height", 860),
        "x": (geo or {}).get("x"),
        "y": (geo or {}).get("y"),
        "frameless": True,
        "shadow": False,
        "easy_drag": True,
        "hidden": bool(cfg.get("start_minimized", False)),
        "on_top": bool(cfg.get("win_on_top", False)),
        "background_color": "#0f1419",
    }
    if overrides:
        kw.update(overrides)
    return kw
```
`main.py` 改为 `webview.create_window(APP_TITLE, ui_index(), js_api=bridge, **_restore_geo_and_kwargs())`；`tests/test_ui_smoke.py` 改为 `webview.create_window(..., **window_kwargs(cfg, overrides={"hidden": False}))`。

> 注意：`x=None`/`y=None` 传给 `create_window` 是合法的（现状即如此，`main.py:238-239`），保持原样。

**B2. 契约校验脚本 `scripts/check_contract.py`（新增）**

这是"前端与后端及时适配"的**自动化保障**，也是本方案最有价值的工程产物之一。

```python
"""前后端契约一致性校验（零依赖，纯正则 + ast）。

检查项：
1) 前端调用的 js_api 方法 ⊆ 后端公开方法
   前端： App.(call|tryCall|guardedCall)\(\s*["'](\w+)
   后端： ast 解析 app/bridge/*.py 的类方法名（前缀白名单见 base.py:20-27）+ bridge.py 的 MRO
2) 事件名双向差集
   后端 A：emit\(["'](\w+)  与 _push\(["'](\w+)
   前端 B：App\.on\(["'](\w+)
   → B\A 非空 = 错误（前端订阅了不存在的事件，通常是后端改名漏改前端）
   → A\B 非空 = 警告（无人订阅，白名单：app_log/cli_action 等由 app.js 订阅）
3) cfg 键一致性
   后端： config.DEFAULTS 的键集合
   前端： App.state.cfg.<key> 与 cfg_set\(["'](\w+)
   → 前端读取不存在的键 = 错误（拼写错误，运行时静默 undefined）
4) 退出码：有 ERROR 则 1，只 WARN 则 0
"""
```

产出示例：
```
[ERROR] 前端调用但后端不存在：pan_winfsp_status（webui/js/pages/pan.js:451）
[WARN]  后端发出但前端未订阅：proxy_traffic（1 处）
[ERROR] 前端读取未定义 cfg 键：ui_matrial（webui/js/app.js:31）→ 疑似 ui_material 拼写错误
[OK] 契约校验通过：js_api 138 个 / 事件 42 个 / cfg 键 46 个
```
接入 `test.bat` 作为第 0 步（在 pytest 之前跑，秒级）。

**B3. 基线快照机制（L2 的对照物）**

给 `tests/test_ui_smoke.py` 加两个命令行开关：
- `--baseline-out tests/baseline/ui_smoke.json`：把探针结果落盘
- `--baseline-in tests/baseline/ui_smoke.json`：与基线 diff，输出
  - 新增/消失的页面
  - 每页的 `toasts/errs/dangling/emptySel/overflow` 变化
  - `totalErrs` 变化
  - **diff 非空即 FAILED**（除白名单项，白名单在测试文件顶部显式列出并可注释原因）

**这就是"保证调整后不影响功能使用"的机器证明**：改造前后跑同一条命令，输出必须一致。

---

### 3.3 批次 C：PageCtx 页面上下文（核心设计）

#### 3.3.1 问题回顾与设计取舍

现状（`webui/js/app.js:147-183`）：`mount` 只跑一次，页面 DOM 永驻，**全仓 0 次 `App.off()`**，于是：
- `clash.js:116,122` 两个 `setInterval` 永久运行（2.5s/2s）
- `cliphist.js:70-86` 递归 `setTimeout` 轮询永不停止（注释 `cliphist.js:394` 与实际行为相反）
- `settings.js:83` 每个热键行一个 `window` keydown，捕获态切页后持续吞键
- `transfer.js` 8 处、`video.js` 3 处、`screenshot.js` 4 处在**模块顶层**订阅（页面没打开也在跑）

**关键设计取舍：不做"离开即销毁"（destroy-on-leave）。**

原因：26 个页面都把状态存在模块级 `state`/`refs` 里，契约里根本没有 `unmount`。销毁型改造要求每个页面把"挂载期资源"与"激活期资源"重新划分——这正是最可能引发大面积功能回归的做法。

**改用"三通道模型"**：把回调按语义分成三类，各自独立策略，**页面可见时的行为与今天完全一致**：

| 通道 | API | 不可见时 | 数据安全性 |
|---|---|---|---|
| 数据通道 | `ctx.on(event, fn)` | **照常执行**（绝不丢数据） | 保持现状，零风险 |
| 渲染通道 | `ctx.onView(event, fn)` | 跳过 + 置 dirty 标记，激活时补渲染一次 | 数据在 `state` 里，不丢 |
| 周期通道 | `ctx.every(ms, fn)` | 跳过 + 置 dirty | **仅限纯刷新回调** |
| 周期通道（数据型） | `ctx.everyAdaptive({active, idle}, fn)` | 降频（不停止） | 缓冲区不过量堆积 |
| 清理通道 | `ctx.onLeave(fn)` | 离开时执行一次 | 用于解除 armed/临时全局监听 |

#### 3.3.2 接口定义（`webui/js/app.js` 新增）

```js
/** 页面上下文：按语义分通道管理页面资源，可见性由框架统一判定。 */
App.createPageCtx = function (page, el) {
  const timers = [];    // 页面级定时器（离开页面才清，本方案中不主动清）
  const cleanups = [];  // onLeave 登记
  const subs = [];      // App.on 退订函数
  let dirty = false;
  const active = () => el.classList.contains("active");

  const ctx = {
    id: page.id, el: el, page: page,
    active: active,
    /** 纯刷新定时器：不可见时跳过并登记待补渲染 */
    every(ms, fn) {
      const t = setInterval(() => {
        if (!active()) { dirty = true; return; }
        try { fn(); } catch (e) { console.error("[ctx.every:" + page.id + "]", e); }
      }, ms);
      timers.push(t); return t;
    },
    /** 数据型定时器：不可见时降频，不停止（缓冲区类轮询用它） */
    everyAdaptive(opt, fn) {
      let cur = null;
      const schedule = (ms) => { cur = setTimeout(tick, ms); };
      const tick = () => {
        try { fn(); } catch (e) { console.error("[ctx.adaptive:" + page.id + "]", e); }
        schedule(active() ? (opt.active || 2000) : (opt.idle || 10000));
      };
      schedule(active() ? (opt.active || 2000) : (opt.idle || 10000));
      timers.push({ _cleared: false });
      return () => { clearTimeout(cur); };
    },
    /** 数据订阅：始终执行（保持现有语义） */
    on(name, fn) { const off = App.on(name, fn); subs.push(off); return off; },
    /** 渲染订阅：不可见时丢弃并置 dirty */
    onView(name, fn) {
      return ctx.on(name, (d) => {
        if (!active()) { dirty = true; return; }
        try { fn(d); } catch (e) { console.error("[ctx.onView:" + page.id + "]", e); }
      });
    },
    /** 全局监听：viewOnly 时不可见即跳过（键盘类监听必须用 viewOnly） */
    onWindow(type, fn, opt) {
      const h = (e) => {
        if (opt && opt.viewOnly && !ctx.armed()) return;
        fn(e);
      };
      window.addEventListener(type, h);
      cleanups.push(() => window.removeEventListener(type, h));
      return h;
    },
    onDoc(type, fn, opt) { /* 同 onWindow，作用在 document 上 */ },
    /** armed：页面可见 且（可选）页面内没有待处理的捕获态 */
    armed() { return active(); },
    onLeave(fn) { cleanups.push(fn); },
    /** 框架调用：离开页面 */
    _leave() { cleanups.splice(0).forEach((f) => { try { f(); } catch (e) {} }); },
    /** 框架调用：回到页面（先补渲染） */
    _enter() {
      if (dirty) { dirty = false; try { page.refresh && page.refresh(); } catch (e) {} }
    },
    /** 供 L2 泄漏探针断言 */
    _stats() { return { timers: timers.length, cleanups: cleanups.length, subs: subs.length, dirty }; },
  };
  return ctx;
};
```

`navigate()` 的改动（**完全向后兼容**）：

```js
App.navigate = async function (id) {
  const p = App.pages.find((x) => x.id === id);
  if (!p) return false;
  closeDrawers();
  // 离开上一页：执行其清理通道
  const prev = App.pages.find((x) => x.id === App.state.page);
  const prevEl = prev && document.getElementById("page-" + prev.id);
  if (prevEl && prevEl.__ctx && prevEl.__ctx !== (prevEl.__ctx)) prevEl.__ctx._leave();
  ...
  if (!el.__mounted) {
    el.__ctx = App.createPageCtx(p, el);   // 新增：上下文
    await p.mount(el, el.__ctx);           // 旧页面 mount(el) 忽略第二个参数 → 零改动兼容
    el.__mounted = true;
  } else {
    if (el.__ctx) el.__ctx._enter();       // 补渲染（仅在声明了 refresh 且 dirty 时生效）
    if (p.show) { try { p.show(); } catch (e) { console.error(e); } }
  }
  ...
};
```
- **I4 证明**：`mount(el, ctx)` 对签名 `mount(el)` 的函数是合法的额外实参，26 个页面一行不改照样工作。
- `ctx` 缺省不存在时（未迁移页面），一切照旧。

#### 3.3.3 逐页迁移清单（只改这 6 处，最小充分）

| 页面 | 位置 | 现状 | 迁移动作 |
|---|---|---|---|
| Clash | `clash.js:116`（2.5s 连接列表） | 永久 `setInterval` | `ctx.every` + `refresh()` 重取连接 |
| Clash | `clash.js:122`（2s 内核日志） | 永久 `setInterval`，**后端环形缓冲会被消费** | `ctx.everyAdaptive({active:2000, idle:10000}, ...)`（降频而非停跑，避免日志断档太多） |
| 剪贴板历史 | `cliphist.js:70-86` | 递归 `setTimeout` 永不停止，注释与实际不符 | `ctx.every` + `refresh()` 重取列表；**同步修正 `cliphist.js:394` 的误导注释** |
| 设置 | `settings.js:83` | 每个热键行一个全局 keydown，armed 后切页仍吞键 | `ctx.onWindow("keydown", h, { viewOnly: true })` + 捕获结束时 `ctx.onLeave` 解绑 + 捕获完成/取消立即解绑 |
| 设置/更新 | `settings.js` 自动更新订阅 | 已在 v4 改为 mount 注册一次 | 改用 `ctx.onView("update_progress", ...)`（进度条是纯渲染） |
| 截图 | `screenshot.js:1092` | `window` resize 监听未移除 | `ctx.onWindow("resize", fitCanvas)` |
| 图片编辑 | `editor.js:111` | `window` mouseup 未移除 | `ctx.onWindow("mouseup", onCanvasMouseUp)` |

**判定规则（写进迁移文档，避免误用）**：
> 回调里除了"改 DOM"还"改 `state`/累加数据"的，**必须**用 `ctx.on`（数据通道）或 `ctx.everyAdaptive`；**只有**"拉取后直接渲染、数据不落 `state`"的才允许用 `ctx.every` / `ctx.onView`。
> 例：`scan.js:254` 的 `state.results.push` 属于数据落状态 → 用 `ctx.on`，不可用 `ctx.onView`。

#### 3.3.4 紧急开关

`cfg.ui_ctx_guard`（默认 `true`）：置 `false` 时 `ctx.every` / `ctx.onView` 退化为"始终执行"（即完全回到今天的语义），`ctx.onLeave` 不触发。用于线上出问题时**不发版**即刻恢复旧行为（设置页写入 + 重启生效）。

**风险与兼容性**
- 风险 1：某页面的刷新回调里隐含了"必须在不可见时也执行"的副作用 → 逐页迁移清单把范围压到 6 处，且数据落状态的场景一律不用 `every`，风险封闭。
- 风险 2：`_leave()` 里的 `onLeave` 回调若抛异常会中断后续清理 → 每个回调独立 try/catch。
- 风险 3：`everyAdaptive` 用 `setTimeout` 链实现，页面返回时可能有一段 idle 间隔的延迟 → 在 `_enter()` 里提供 `flushNow()`（`everyAdaptive` 可返回一个"立即触发"函数）供 `refresh` 调用。
- 兼容性：WebView2 支持 `classList.contains`/`setInterval` 等全部特性，无版本顾虑。

---

### 3.4 批次 D：设计 Token 与组件原语

#### 3.4.1 Token 层补全（**只增不改**）

在 A2 抽出的 `tokens.css` 中**追加**（不删除、不改名任何现有变量，保证既有 `var()` 全部继续有效）：

```css
:root {
  /* 间距（4px 基准，覆盖现有 6/8/10/12/14/16 用法） */
  --sp-1: 4px; --sp-2: 6px; --sp-3: 8px; --sp-4: 12px; --sp-5: 16px; --sp-6: 24px;
  /* 圆角（收敛现有 15 种原始值） */
  --r-xs: 4px; --r-sm: 6px; --r-md: 8px; --r-lg: 10px; --r-xl: 12px; --r-pill: 999px;
  /* 字号（Fluent type ramp 近似，覆盖现有 12 种） */
  --fs-caption: 11px; --fs-body-sm: 12px; --fs-body: 13px;
  --fs-subtitle: 15px; --fs-title: 18px; --fs-large: 24px;
  /* 层级 */
  --z-base: 1; --z-sticky: 20; --z-drawer: 300; --z-menu: 1000; --z-modal: 1100; --z-toast: 1200;
  /* 高程（现有 4 处散落阴影） */
  --elev-1: 0 1px 2px rgba(0,0,0,.16);
  --elev-2: 0 8px 24px rgba(0,0,0,.28);
  --elev-3: 0 18px 50px rgba(0,0,0,.35);
  /* 遮罩（替代 3 处不随主题翻转的黑色常量） */
  --scrim: rgba(0,0,0,.45);
  /* 外壳尺寸（替代 46/26px 在 4 处的重复硬编码） */
  --titlebar-h: 48px; --statusbar-h: 26px;
  --shadow: var(--elev-2);   /* 兼容既有 --shadow 引用 */
}
body.light { --scrim: rgba(15,20,25,.28); }
@media (forced-colors: active) { /* 高对比度：几何用边框替代颜色 */ }
```

**关键点**：`--shadow` 保留并指向 `--elev-2`，`--card/--bg` 等 21 个既有色变量一个不动——这样 D 批次的 CSS 改动是"新增可选变量"，任何页面不改也不会有视觉变化。

#### 3.4.2 原语清单与迁移策略

新增 10 个原语类（前缀 `u-`，避免与既有类名冲突）：`u-card / u-row / u-progress / u-pill / u-tabs / u-crumb / u-empty / u-btn / u-field / u-dialog`。

**迁移采用"三步法"**（关键：中间态允许新旧并存，任一步都可停）：
1. **新增**：`u-*` 原语 + 对应的 `App.ui.*` JS 构造器（内部复用现有 `App.h`）。
2. **套用**：按页面逐页替换（每页一个提交），被替换的旧类**先不删**，只在 CSS 里标注 `/* @deprecated 被 u-card 取代 */`。
3. **清理**：全部页面迁移完成后，用一个独立提交删除旧类。若某页迁移出问题，只回滚那一页的提交。

**视觉基线是硬要求**：每个页面迁移前截图存 `tests/baseline/visual/<page>.png`，迁移后像素比对，差异阈值内才允许合并（见 §4.5）。

#### 3.4.3 字体与 Windows 尺寸对齐（Win10 为主）

```css
/* Win11 走 Segoe UI Variable；Win10 自动回落到 Segoe UI（现有行为） */
font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI",
             "PingFang SC", sans-serif;
```
- **不要用 `system-ui`**：在 WebView2/Chromium 下的映射曾随版本变化，显式栈更可控。
- `--titlebar-h` 从 46px 调到 48px（Fluent 标准），同步改 `app.css:76`（grid 行高）、`app.css:802`（`top: 46px`）、`app.css:814`（`inset: 46px 0 26px`）、`app.css:694`（toast `top: 56px` 是独立猜的，改为 `calc(var(--titlebar-h) + 8px)`）。
- **这是本批次唯一会改变观感的项**，放在 D 批次最后一步，便于单独回滚。

#### 3.4.4 accent 硬编码修正

`app.css:181`（`rgba(123,92,255,.12)`）、`189`（`#7b5cff`）、`510`（`#6a4dff`）、`512`（`rgba(76,141,255,.25)`）、`412-417`（`.tool-item-icon.tint-0..5` 里 `tint-0` 就是 `#4c8dff`）改为 `var(--accent)` / `color-mix(in srgb, var(--accent) 70%, #000)`。
- **兼容性**：`color-mix` 需要 Chromium 111+（WebView2 2023 年起满足）。**降级方案**：`@supports not (color: color-mix(in srgb, red, blue))` 内保留现有十六进制值。**需核实**：目标用户机器的 WebView2 Runtime 版本（前端可用 `navigator.userAgent` 里的 `Edg/xxx` 探测并上报，见 E 批次能力探测）。

**风险与兼容性（批次 D 整体）**
- 最大风险是"迁移过程中新旧类混用导致局部视觉错乱"→ 用"三步法 + 视觉基线 + 每页一提交"封死。
- 12 种字号的统一会轻微改变行高与换行 → 强约束：**D 批次不改任何字号的实际数值**，只建立"新代码用 token"的约定，字号收敛放到后续独立批次。

---

### 3.5 批次 E：能力探测与窗口外壳（Win10 为主，Win11 适配）

#### 3.5.1 能力探测 `app/bridge/cap_api.py` + `app/core/win_shell.py`

```python
# app/core/win_shell.py
"""窗口外壳能力：材质 / 圆角 / 系统暗色 / 窗口几何。

Windows 10 优先路径：build < 22621 → SetWindowCompositionAttribute 的
ACCENT_ENABLE_BLURBEHIND（Win7+ 均支持，Win10 表现稳定）。
Windows 11 增强路径：build >= 22621 → DWMWA_SYSTEMBACKDROP_TYPE。

所有调用失败都降级为「不透明」，并通过返回值报告可显示的原因字符串，
前端设置页据此显示「当前材质：Mica / 模糊 / 无（原因）」。
"""
MATERIAL_AUTO, MATERIAL_OFF, MATERIAL_BLUR, MATERIAL_MICA = "auto", "off", "blur", "mica"

# DWM 属性
DWMWA_USE_IMMERSIVE_DARK_MODE   = 20
DWMWA_WINDOW_CORNER_PREFERENCE  = 33
DWMWA_SYSTEMBACKDROP_TYPE       = 38
DWMSBT_NONE, DWMSBT_MAINWINDOW, DWMSBT_TRANSIENTWINDOW = 1, 2, 3
DWMWCP_ROUND = 2

def hwnd_of(window):
    """取主窗口 HWND。取值范式与 app/bridge/win_api.py:80-82 保持一致。"""
    native = getattr(window, "native", None)
    h = getattr(native, "Handle", None)
    if not h:
        return 0
    return h if isinstance(h, int) else int(h.ToInt64())

def is_remote_session():
    """远程桌面/虚拟机场景下不启用模糊：Win10 上会明显掉帧且观感无意义。"""
    return bool(ctypes.windll.user32.GetSystemMetrics(0x1000))  # SM_REMOTESESSION

def probe(hwnd=0) -> dict:
    """返回前端可消费的能力字典（也供 cap_get 桥接方法直接返回）。"""
    build = sys.getwindowsversion().build
    out = {"build": build, "mica": False, "blur": False,
           "round_corner": False, "reason": "", "remote": is_remote_session()}
    if out["remote"]:
        out["reason"] = "远程会话"
        return out
    if build >= 22621:
        out["round_corner"] = True
        # 用 DwmSetWindowAttribute 的返回码探测，比版本号更可靠
        out["mica"] = _dwm_attr_supported(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_MAINWINDOW)
    if build >= 10240:
        out["blur"] = True   # Win10 的 BlurBehind 全版本可用
    return out

def apply(hwnd, mode="auto", dark=True) -> dict:
    """幂等应用材质。返回 {"applied": "mica|blur|none", "reason": "..."}。"""
    cap = probe(hwnd)
    want = mode
    if want == MATERIAL_AUTO:
        want = MATERIAL_MICA if cap["mica"] else (MATERIAL_BLUR if cap["blur"] else MATERIAL_OFF)
    if want == MATERIAL_MICA and not cap["mica"]:
        want, reason = MATERIAL_BLUR if cap["blur"] else MATERIAL_OFF, "系统不支持 Mica"
    if want == MATERIAL_BLUR and not cap["blur"]:
        want, reason = MATERIAL_OFF, "系统不支持模糊"
    if want == MATERIAL_MICA:
        _dwm_set(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_MAINWINDOW)
        _dwm_set(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)
        _dwm_set(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
        return {"applied": "mica", "reason": ""}
    if want == MATERIAL_BLUR:
        _set_blur_behind(hwnd, dark)      # ACCENT_ENABLE_BLURBEHIND + ABGR tint
        return {"applied": "blur", "reason": reason}
    _clear_blur(hwnd)                     # ACCENT_DISABLED
    return {"applied": "none", "reason": reason}
```

**Win10 模糊的关键细节**（`_set_blur_behind`）：
```python
class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]
# AccentState: 3 = ACCENT_ENABLE_BLURBEHIND（推荐，拖动不卡）
#              4 = ACCENT_ENABLE_ACRYLICBLURBEHIND（更"材质"，但 Win10 拖动掉帧）
# GradientColor 是 0xAABBGGRR（**注意是 ABGR，不是 ARGB**）
# 深色底色 #0f1419 → 0xCC_19_14_0F；亮色 #f2f5f9 → 0xCC_F9_F5_F2
```
三个必须写进代码注释的 Win10 已知限制：
1. **Win10 的 BlurBehind 在窗口最大化时失效**（DWM 行为）→ 最大化/还原时重调 `apply()`，并让前端把 `--app-bg-alpha` 切到 1.0（不透明），避免最大化时观感突变。
2. **Win10 的 Acrylic(4) 拖动掉帧** → 默认用 BlurBehind(3)；设置页提供"增强材质（可能掉帧）"选项供用户自选。
3. **窗口透明是模糊的前提** → Win10 上必须 `transparent=True` 才能看到模糊；若 `transparent=True` 但材质未生效（比如探测失败），窗口会透出桌面 → **因此 `transparent` 只在 `apply()` 确认返回 `mica|blur` 时才允许启用**，见 3.5.3 的启动时序。

#### 3.5.2 能力桥接方法 `app/bridge/cap_api.py`

```python
class CapApi:
    def cap_get(self):
        """返回本机 UI 能力（前端降级的唯一依据，必须在 boot 早期调用）。"""
        return {"ok": True, "data": {
            "os": {"build": ..., "win11": bool},
            "material": {"supported": bool, "applied": "mica|blur|none", "reason": str},
            "webview": {"ua_edg": "Edg/xxx"},        # 由前端补充上报
            "font_variable": _has_segoe_variable(),   # 是否装了 Segoe UI Variable
            "reduce_motion": bool,                    # 系统"减少动画"
            "high_contrast": bool,                    # 系统高对比度
            "deps": {"ffmpeg": bool, "ocr": bool, "admin": bool},  # 复用现有探测（main.py:98-190）
        }}

    def cap_report_webview(self, ua):
        """前端上报 WebView2 版本（UA 里的 Edg/xxx），后端据此决定是否启用 color-mix 等。"""
    def material_set(self, mode):   # auto|off|blur|mica，写入 cfg 并立即应用
    def material_state(self):       # 供设置页显示"当前材质 + 原因"
```
- `deps` 直接复用 `main.py:98-190` 已有的探测函数（`_ocr_probe`、`arch_pick` 模式），不重复造。
- **兼容性**：全部为**新增**方法，`_JS_API_PREFIXES`（`base.py:20-27`）需加 `"cap_"` 前缀才会被自动日志与包装覆盖（这是唯一需要动 `base.py` 的地方，且是纯新增）。

#### 3.5.3 启动时序（后端适配前端的关键一处）

```python
# main.py 内
kw = win_spec.window_kwargs(cfg, geo=geo_kw)
material = (cfg.get("ui_material") or "auto")
cap = win_shell.probe()                       # 先探测（不需要 hwnd）
if material != "off" and (cap["mica"] or cap["blur"]):
    kw["transparent"] = True                  # 只有确认有能力才透明
    kw["hidden"] = True                        # 透明窗口先隐藏，避免 HTML 加载前透出桌面
kw["background_color"] = "#00000000" if kw.get("transparent") else "#0f1419"
window = webview.create_window(APP_TITLE, ui_index(), js_api=bridge, **kw)
bridge.attach(window)

def _on_loaded_material():
    """HTML 加载完成 → 应用材质 → 显示窗口（顺序不可颠倒）。"""
    hwnd = win_shell.hwnd_of(window)
    state = win_shell.apply(hwnd, material, dark=(cfg.get("theme") != "light"))
    bridge.emit("material_state", state)
    if kw.get("transparent") and not cfg.get("start_minimized"):
        window.show()
window.events.loaded += _on_loaded_material
```
**这是"通过后端调整适配前端"的典型**：前端只声明"我要材质"，透明窗口的开关、启动闪烁的规避、失败降级都在后端完成。
若 `apply()` 返回 `none`（探测失败），后端**保持窗口不透明**并推送 `material_state`，前端 CSS 变量 `--app-bg-alpha` 保持 1.0，观感与今天完全一致。

#### 3.5.4 前端材质消费（Win10 直角、Win11 圆角）

```css
/* 默认（不材质）：与今天完全一致 */
#app { background: var(--bg); }
/* 材质启用时由 body.material-blur / body.material-mica 切换 */
body.material-blur #app  { background: color-mix(in srgb, var(--bg) 92%, transparent); }
body.material-mica #app  { background: color-mix(in srgb, var(--bg) 85%, transparent); }
body.material-blur #titlebar, body.material-mica #titlebar {
  background: color-mix(in srgb, var(--panel) 80%, transparent);
  backdrop-filter: blur(20px) saturate(180%);
}
```
- **Win10 不做窗口圆角**（无 DWM 圆角属性，且不透明窗口下 CSS 圆角会露底）。这符合 Win10 原生观感，也是"以 Win10 为主"的体现。
- **Win11 用 DWM 圆角**（`DWMWA_WINDOW_CORNER_PREFERENCE`）。**已核实**：pywebview 对属性 33/34 的写入只出现在 `toggle_fullscreen()` 内（`webview/platforms/winforms.py:572-574` 写 `33=1`(DONOTROUND)/`34=0xFFFFFFFE`，还原时写 `33=0`/`34=0xFFFFFFFF`），**本应用从不调用 `window.toggle_fullscreen()`**（最大化走 `w.restore()/w.maximize()`，`app/bridge/win_api.py:39-52`）→ 圆角属性不会被 pywebview 抢写。且 `33=0` 是 `DWMWCP_DEFAULT`（交给 DWM 决定），Win11 对普通顶层窗口默认就是圆角，因此 **Win11 上很可能无需任何额外设置就已圆角**；仅当真机显示为直角时才显式写 `33=2`(ROUND)。唯一要记的坑：若未来新增"全屏"功能，注意它会写 `33=1` 关掉圆角。
- 前端在收到 `material_state` 事件后才加 `body.material-*` 类，**事件没到就不加**（默认不透明路径），保证最坏情况等于今天的观感。

#### 3.5.5 窗口缩放边框（Win10 的独立价值项）

现状（**已从源码层面确认**）：`frameless=True` → `FormBorderStyle.None`（`webview/platforms/winforms.py:271`），而 `resizable` 分支（`winforms.py:231`）先执行、随后被 271 行覆盖为 `None`；`winforms.py` 全文没有任何 `WS_THICKFRAME`/`0x40000` 的写入。WinForms 的 `FormBorderStyle.None` 窗口不提供 resize 边框，`WM_NCHITTEST` 全区域返回 `HTCLIENT`。

→ 结论：**当前无边框主窗口用户无法拖边缩放**。这已经不是"体验增强"而是**功能缺口**（用户只能最大化或改配置里的几何记忆），因此本项优先级应从"体验项"提升为 **P1 功能补齐**，不依赖材质批次，可与批次 B/C 并行。建议真机确认一次并记录为已知问题。

**方案（复用已验证的原生调用范式，避免 WndProc 子类化）**：
```python
# app/bridge/win_api.py 新增（与既有 win_begin_drag 同族，win_api.py:104-129）
def win_resize_begin(self, edge):
    """边缘缩放：把事件交给系统非客户区逻辑（HTLEFT/HTRIGHT/HTTOP...）。

    edge ∈ left|right|top|bottom|topleft|topright|bottomleft|bottomright
    与 win_begin_drag 完全同源（ReleaseCapture + WM_NCLBUTTONDOWN + HTxxx），
    由系统完成缩放与坐标换算，不做自算增量。
    """
```
```js
// webui/js/window_drag.js：在 mousedown 捕获阶段判定 6px 边缘命中
// 命中则 App.call("win_resize_begin", edge) 并 stopPropagation（阻断 easy_drag 的拖窗）
```
- 为什么不用 `SetWindowLongPtr` 子类化：pywebview 的 js_api 运行在独立线程（`base.py:6-7` 明确说明），而 WndProc 回调在 WinForms UI 线程；跨线程 ctypes 回调有死锁/崩溃风险。上面这条路径复用 `win_begin_drag` 已验证的 `SendMessageW` 模式（该函数目前在 `app.js:262-265` 被注释停用，但代码本身是好的），风险低得多。
- **DPI**：边缘宽度按 `GetDpiForWindow()/96` 缩放（现有 `main.py:43-60` 已声明 Per-Monitor DPI Aware）。
- Win11 的 Snap Layouts（最大化按钮悬停菜单）需要真实 `HTMAXBUTTON`，**不做**（列为长期项，理由见 §0）。

---

### 3.6 批次 F：任务中心（旁路聚合，不改旧事件）

#### 3.6.1 后端 `app/core/taskbus.py`（薄注册表，不重构任何现有任务）

```python
"""任务聚合总线：把分散的进度回调适配成统一事件，供前端任务中心消费。

设计约束（关键）：
- **旁路**：只新增 task_update 事件，旧的 update_progress / xfer_progress /
  pan_task 等事件继续照发，前端旧代码零改动。
- **无状态持有**：只在内存保留最近 N 条任务快照（供页面重进时补渲染），
  不接管任何任务的生命周期、不做取消调度。
"""
class TaskBus:
    def start(self, kind, title, *, total=None, cancellable=False) -> str:  # 返回 task_id
    def update(self, task_id, *, done=None, total=None, text=None)
    def finish(self, task_id, *, ok=True, err=None)
    def snapshot(self) -> list            # 供 task_snapshot 桥接方法
```
事件载荷（新 schema，冻结前先与前端一起定）：
```json
{"id":"t-17","kind":"upload|download|transfer|encode|mount|update|scan",
 "title":"上传 report.pdf","state":"running|done|fail|cancelled",
 "done":7340032,"total":10485760,"text":"6.9/10.0 MB","speed_bps":524288,
 "cancellable":true,"ts":1699999999}
```
接入点（**逐处单独提交**，每处都能独立回滚）：`app/bridge/pan_api.py`、`transfer_api.py`、`update_api.py`、`recorder_api.py`、`routing_api.py`、`rclone_mount.py`。接入方式：在**已有的进度回调旁边**加一行 `taskbus.update(...)`，不改动原有 emit。

#### 3.6.2 前端任务中心

- 状态栏（`webui/index.html:60-63`，26px 高）右侧新增"进行中 N"按钮，点击弹出任务列表（复用 `App.modal` 或新的轻量浮层）。
- **切页后任务可见**是这一项的核心价值。
- 进度条统一用 D 批次的 `u-progress` 原语（替换 8 处自造实现之一即可，无需一次全换）。
- 可选（需真机验证）：Windows 任务栏进度 `ITaskbarList3::SetProgressValue`。**风险提示**：js_api 在独立线程执行（`base.py:6-7`），COM 调用需每线程 `CoInitializeEx`，且 ITaskbarList3 应在窗口线程使用 → **若验证不通过则只保留状态栏入口**，不影响主功能。`comtypes` 已在依赖中（`requirements.txt:13`）。

**风险与兼容性**
- 后端接入点是**追加式**的：每处只加一行，删掉即回退。
- 前端订阅 `task_update` 用 `ctx.onView`（纯渲染）→ 页面不可见时不做 DOM 工作，但 `TaskBus` 的 `snapshot()` 保证切回时数据完整。
- 旧事件不动 → **现有页面（含未迁移的）行为完全不变**。

---

### 3.7 批次 G：导航、分组与焦点

**G1. 返回栈（`app.js:147-183`）**
```js
let backStack = [];
// navigate() 内：记录来源（排除热键中转与自身重复）
if (App.state.page && App.state.page !== id) backStack.push(App.state.page);
if (backStack.length > 20) backStack.shift();
App.back = () => { const prev = backStack.pop(); if (prev) App.navigate(prev, { fromBack: true }); };
// 二级页返回按钮：栈为空时回退到 backTo（保持现有行为）
```
绑定 `Alt+←`（在 `app.js:444` 的 Ctrl+K 监听旁新增一个）。**注意**：`hotkeyAction`（`app.js:188-220`）触发的跳转不记栈。

**G2. 分组收敛（只改 `group` 常量，绝不改 `id`）**

现状：7 组，其中"网盘挂载"1 项、"系统"1 项、"笔记与安全"2 项，而"工具与增效"承载 4 可见 + 19 隐藏。
- 第 1 步（零风险）：`pan.js` 的 `group` 并入"共享服务" → 分组数 7→6。
- 第 2 步：把 `network.js`（网络发现/文件共享）与 `scan.js`（扫描与映射）的语义在标题上明确区分（目前一个是"网络发现/文件共享"、一个是"扫描与映射"，用户难以判断该点哪个）。
- **不做**语义无关的大重组（改 id 会破坏 `start_page` 与右键菜单，收益不足）。
- `NAV_ITEM_PRI`（`app.js:9`）与 `GROUP_ORDER`（`app.js:5`）保持不变。

**G3. 焦点管理（`:focus-visible` 是纯新增 CSS，零风险）**
```css
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
:focus:not(:focus-visible) { outline: none; }
.switch input { /* 从 display:none 改为 appearance:none + 绝对定位，保留 Tab 序 */ }
```
`navigate()` 末尾：`if (p.focusTarget !== false) { const t = el.querySelector("h2, .card-title"); t && (t.tabIndex = -1, t.focus({preventScroll:true})); }`
- `modal`（`ui.js:167-190`）补焦点陷阱：Tab 循环限制在对话框内（`focusin` 监听 + 首尾按钮回绕）。
- `toast`（`ui.js:93`）补 `role="status" aria-live="polite"`；`modal` 补 `role="dialog" aria-modal="true"`。
- **需核实**：焦点环会不会与 `user-select: none`（`app.css:64`）或 `body{zoom}` 产生视觉偏移 → L2 探针 + 手工回归。

---

## 4. 验证方案

### 4.1 五层验证体系

| 层 | 内容 | 耗时 | 运行方式 | 门禁 |
|---|---|---|---|---|
| **L0 静态** | `py_compile` 全部改动 py；`node --check` 全部改动 js；`check_contract.py` 契约校验；CSS 变量白名单（禁止新增裸十六进制） | < 10s | `test.bat` 前置 | 硬门禁 |
| **L1 单测** | `python -m pytest tests/ -v` 全量（现有 37 个测试文件，含 `test_settings_api.py`/`test_transfer.py`/`test_screenshot.py` 等） | 1-3 min | `test.bat` | 硬门禁：**必须全绿** |
| **L2 UI 冒烟** | 真实窗口遍历全部页面（扩展 `tests/test_ui_smoke.py`）+ 基线 diff | 2-4 min | 独立脚本 | 硬门禁：**diff 为空** |
| **L3 手工回归** | §4.4 矩阵 | 每批次 30-60 min | 人工 | 硬门禁：抽样通过 |
| **L4 性能基线** | `scripts/perf_probe.py`（新增）采样 300s | 5 min | 手动/夜间 | 阈值门禁：§4.6 |

### 4.2 L2 冒烟扩展（`tests/test_ui_smoke.py`）

**先修同源问题（B1）**，再新增四个探针：

```javascript
/* ① 资源泄漏探针：切 20 轮后资源数不得增长 */
const stat0 = App._ctxStats();                  // 新增 API：汇总所有页面 ctx._stats()
for (let i = 0; i < 20; i++) { for (const p of App.pages) await App.navigate(p.id); }
const stat1 = App._ctxStats();
out.leak = { before: stat0, after: stat1,
             grew: stat1.timers - stat0.timers || stat1.subs - stat0.subs };

/* ② 主题矩阵：4 种组合各遍历一遍，收集错误 */
for (const th of ["dark", "light", "auto"]) { /* cfg_set + App.setTheme 后重跑遍历 */ }

/* ③ 焦点探针：navigate 后 activeElement 不得为 body */
await App.navigate("settings");
out.focusOk = document.activeElement !== document.body;

/* ④ 材质一致性探针：材质状态与 body 背景透明度必须自洽 */
out.material = { state: window.__materialState,          // 来自 material_state 事件
                 bodyAlpha: getComputedStyle(document.body).backgroundColor };

/* ⑤ 定时器可见性探针：切走 clash 页后 3 秒内不得有 clash_* 桥接调用 */
// 由后端配合：bridge 侧已自动记录全部 js_api 调用（base.py:53-70），
// 测试直接读日志区间计数，无需前端埋点
```
**关键**：`⑤` 不需要新埋点——`app/bridge/base.py` 的 `__getattribute__` **已经自动记录每一次 js_api 调用**（含方法名与耗时）。这给了我们一个天然的、零成本的"空转度量"。

### 4.3 L0 契约校验（`scripts/check_contract.py`）

见 §3.2 B2。**这是"前端与后端及时适配"的强制机制**：任何人改了后端方法名/事件名/cfg 键而没同步前端，L0 立刻报 ERROR 并阻塞提交。

### 4.4 L3 手工回归矩阵

**平台矩阵**（Win10 为主）：

| 系统 | 优先级 | 材质预期 |
|---|---|---|
| Windows 10 22H2 (19045) | **必须** | BlurBehind（若开关开启）/ 不透明 |
| Windows 10 21H2 / 1809 | 抽样 | 同上 |
| Windows 11 22H2 / 23H2 | **必须**（适配） | Mica + 圆角 |
| 远程桌面会话 | 抽样 | **不启用材质**（`is_remote_session`） |

**缩放 × 显示器**：100% / 125% / 150% × 单屏 / 双屏（不同 DPI）。重点验截图与遮罩坐标（`body{zoom}` 与 `devicePixelRatio` 的交互是已知风险区，`tools.js:990,1015` 是唯一处理过 DPR 的地方）。

**核心用例清单**（每批次跑一遍，★ = 该批次重点）：

| # | 用例 | 覆盖批次 | 可自动化 |
|---|---|---|---|
| 1 | 截图：区域(Ctrl+Alt+A)/窗口/全屏(Ctrl+Alt+F)/延迟/滚动 ★ | C,E | 部分 |
| 2 | 截图任务链：保存/复制/进编辑器/复制路径/定位文件 | C | 否 |
| 3 | 录屏：MP4(含声音)/GIF/区域录/编码提示/隐藏窗口 ★ | C | 否 |
| 4 | OCR：热键圈选→弹窗→复制全文/单行/查看原始 ★ | C,E | 否 |
| 5 | 剪贴板：文本/图片/文件三类收发 + 回贴 + 加密历史失败提示 ★ | C,E | 否 |
| 6 | 剪贴板历史页：搜索/置顶/删除/轮询更新 ★ | C | 部分 |
| 7 | 剪贴板弹窗(Win+V)：呼出/键盘选择/自动粘贴/主题跟随 ★ | D,E | 否 |
| 8 | 备忘录 + 浮窗：搜索/主题跟随/置顶 | D | 否 |
| 9 | 文件传输：拖拽发送/选择发送/接收确认/自动接收/配对加密/HUD/失败明细 ★ | C,F | 否 |
| 10 | 共享：创建/刷新(PS)/映射盘符/防火墙放行 | F | 否 |
| 11 | FTP：服务启停/客户端连接/目录/上传下载/远程管理 | F | 否 |
| 12 | Web 服务器：启停/端口/打开浏览器(open_external) | — | 否 |
| 13 | 网盘挂载：OpenList 启停/WebDAV 上传下载/rclone 挂载卸载/自动下载 ★ | F | 否 |
| 14 | 代理：Clash 启停/导入/订阅/测速/连接监控/内核日志(切页后)★ | C,F | 否 |
| 15 | V2rayN：核心管理/导入/系统代理/自动重连 | F | 否 |
| 16 | 分流规则：规则库下载/编辑/应用 | F | 否 |
| 17 | 工具箱子页：14 个内置工具逐个执行 ★ | D | 部分 |
| 18 | 系统集成：托盘/单实例唤起/关窗行为/自启/置顶/几何记忆/CLI 右键菜单/热键捕获态切页 ★ | C,E,G | 部分 |
| 19 | 窗口：拖边缩放（新增）/拖动/最大化还原/持久化 | E | 否 |
| 20 | 界面：主题 4 组合/缩放 0.85-1.4/减少动画/强调色/导航折叠/抽屉(<1100px)★ | D,E,G | 部分 |

### 4.5 视觉回归（辅助证据，非硬门禁）

用现有能力落地：`app/core/screenshot.py` 已能截屏 → 新增 `scripts/visual_snapshot.py`：
- 固定窗口尺寸（1320×860）+ 固定 DPI + 固定主题 + 关闭动画（`ui_reduce_motion`）
- 逐页截图 → `tests/baseline/visual/<page>-<theme>.png`
- 比对用"结构差异"而非严格像素：允许阈值内的抗锯齿/字体渲染差异（建议先只比 `--faint`/边框/间距区域的差异面积）
- **定位为人工复核的辅助证据**，不进硬门禁（像素 diff 天然不稳定，做成门禁会变成噪声源）

### 4.6 L4 性能与资源基线（`scripts/perf_probe.py`，新增）

采样 300 秒，输出 CSV：
| 指标 | 采集方式 | 改造前基线（待测） | 改造后目标 |
|---|---|---|---|
| 主进程 CPU（空闲） | `Get-Process` / `psutil` 不可用则用 `ctypes` + `GetProcessTimes` | 测一次记录 | 不高于基线 |
| 主进程内存 + 句柄数 | 同上 | 测一次记录 | 句柄不随时间增长 |
| WebView2 子进程合计内存 | `tasklist` 汇总 `msedgewebview2.exe` | 测一次记录 | 不高于基线 |
| **空闲 js_api 调用次数** | 解析应用日志（`base.py:53-70` 自动记录，无需埋点）：`devices_list`、`clash_log_poll` 等 | 预计 `devices_list` ~100 次/5min | `devices_list` ≤ 40 次（A4）；Clash 页挂载后 `clash_*` 轮询 0 次（C） |

**这是对 P0-1/P0-2 修复效果最直接的量化证据**：日志里的 js_api 调用次数就是"空转"的精确度量。

---

## 5. 风险登记与回滚预案

| # | 风险 | 触发条件 | 缓解措施 | 回滚动作 |
|---|---|---|---|---|
| R1 | PageCtx 导致某页刷新逻辑丢失 | 某页 `ctx.every` 的回调里隐含数据副作用 | 迁移白名单只有 6 处；数据落 `state` 的场景强制用 `ctx.on`/`everyAdaptive`；L2 基线 diff | `cfg.ui_ctx_guard=false`（不发版）+ 回滚该页提交 |
| R2 | 设计 Token 迁移造成局部视觉错乱 | 新旧类混用 | 三步法 + 每页一提交 + 视觉基线；旧类标注 deprecated 不立即删 | 回滚该页提交（旧类仍在） |
| R3 | Win10 透明窗口无材质 → 透出桌面 | `apply()` 探测成功但 DWM 调用失败 | **只有 `apply()` 确认返回 mica/blur 才允许 `transparent=True`**；失败即保持不透明；`material_state` 推送原因 | `cfg.ui_material="off"` 重启 |
| R4 | 透明窗口下启动闪烁/穿透 | HTML 加载前窗口已可见 | `transparent` 时强制 `hidden=True`，`loaded` 事件后再 `apply()+show()`；顺序不可颠倒 | `ui_material=off` |
| R5 | 材质开启后拖动/缩放掉帧 | Win10 Acrylic 状态 | 默认用 BlurBehind(3) 而非 Acrylic(4)；增强材质做成用户可选项 | 设置页切回"标准模糊" |
| R6 | 生产窗口参数变更影响几何记忆 | B1 重构 `main.py` 窗口创建 | `win_geometry` 读写逻辑（`main.py:18-40`、`269-278`）不动；L3 用例 18 专项验证 | 回滚 B1 提交 |
| R7 | 任务中心双份状态不一致 | 页面本地进度与 TaskBus 快照冲突 | 明确"页面权威、TaskBus 只读快照"；`task_snapshot` 只用于页面重进补渲染 | `cfg.ui_taskcenter=false`（前端入口隐藏，事件仍在） |
| R8 | 焦点环与 zoom/布局冲突 | 125%/150% 下 outline 偏移 | L2 焦点探针 + L3 用例 20 | 回滚 G3 的 CSS 提交 |
| R9 | 契约校验误报阻塞提交 | 白名单外的动态方法名 | `check_contract.py` 支持显式白名单 + `--warn-only` 逃生参数 | 使用逃生参数 |
| R10 | 右键菜单/CLI 深链断裂 | 改 id 或改 `PAGE_FOR_CLI` | 本方案不改 id；`PAGE_FOR_CLI`（`app.js:6`）不动 | 回滚提交 |

**统一回滚原则**：
1. 每批次独立提交（可 `git revert` 单批）。
2. 用户可感知的行为变化必须配 `cfg` 开关，默认值 = 新行为，出问题可**不发版**即刻恢复旧行为。
3. 涉及窗口形态的改动（E 批次）单独提交 + 单独开关，与其他批次无耦合。

---

## 6. 新增/修改文件清单

**新增**
| 文件 | 用途 | 批次 |
|---|---|---|
| `webui/css/tokens.css` | 设计 token 单一来源 | A |
| `app/core/win_spec.py` | 窗口参数工厂（生产/测试同源） | B |
| `scripts/check_contract.py` | 前后端契约校验（L0 门禁） | B |
| `tests/baseline/ui_smoke.json` | 冒烟基线快照 | B |
| `app/core/win_shell.py` | 材质/圆角/系统集成 | E |
| `app/bridge/cap_api.py` | 能力探测桥接（`cap_*`） | E |
| `app/core/taskbus.py` | 任务聚合总线 | F |
| `scripts/perf_probe.py` | 性能与空转度量 | B（先建基线）/ F（验收） |
| `scripts/visual_snapshot.py` | 视觉基线（辅助） | D |

**修改（按批次）**
| 批次 | 文件 |
|---|---|
| A | `webui/js/ui.js`、`webui/css/app.css`、`webui/overlay.html`、`webui/clip_pop.html`、`webui/memo_pop.html`、`webui/js/app.js` |
| B | `main.py`、`tests/test_ui_smoke.py`、`test.bat` |
| C | `webui/js/app.js`、`webui/js/pages/{clash,cliphist,settings,screenshot,editor}.js` |
| D | `webui/css/{app,tokens}.css` + 逐页迁移（每页一提交） |
| E | `main.py`、`webui/js/app.js`、`webui/css/app.css`、`app/bridge/win_api.py`、`webui/js/window_drag.js`、`webui/js/pages/settings.js` |
| F | `app/core/taskbus.py` + 6 个 `*_api.py`（逐处提交）、`webui/index.html`、`webui/js/ui.js` |
| G | `webui/js/app.js`、`webui/css/app.css`、`webui/js/pages/pan.js` |

**配置项新增（写入 `app/core/config.py` 的 `DEFAULTS`，全部可回退）**
```python
"ui_material": "auto",     # auto|off|blur|mica —— 窗口材质
"ui_ctx_guard": True,      # 页面可见性守卫（false = 完全回到旧行为）
"ui_taskcenter": True,     # 状态栏任务中心入口
"ui_font_ramp": True,      # Fluent 字阶（false = 保留现有字号）
```

---

## 7. 实施前必须核实的 4 项（不确认不动手）· 另有 2 项已核实

**已核实（本轮完成，无需再查）**

| # | 结论 | 依据 | 对方案的影响 |
|---|---|---|---|
| ✅1 | **无边框主窗口用户无法拖边缩放** | `winforms.py:231` 的 `resizable` 分支被 `:271` 的 `FormBorderStyle.None` 覆盖；全文无 `WS_THICKFRAME` 写入 | §3.5.5 由"体验增强"升级为 **P1 功能补齐**，可与 B/C 批次并行 |
| ✅5 | pywebview 不会抢写 `DWMWA_WINDOW_CORNER_PREFERENCE` | 属性 33/34 仅出现在 `toggle_fullscreen()`（`winforms.py:572-574`），本应用不调用该方法 | Win11 圆角路径安全；很可能无需显式设置（DWMWCP_DEFAULT 已圆角） |

**仍需核实**

| # | 待核实 | 为什么必须核实 | 核实方式 |
|---|---|---|---|
| 2 | 目标用户 WebView2 Runtime 版本分布 | `color-mix`（Chromium 111+）与 `backdrop-filter` 的降级路径 | 前端上报 `navigator.userAgent` 的 `Edg/xxx`，收集一轮 |
| 3 | 浮窗（clip_pop/memo_pop）**当前打开时**切换主窗主题的实际表现 | 决定主题广播是"补功能"还是"修 bug" | 真机：开着剪贴板弹窗切主题 |
| 4 | `body{zoom}` 在 125%/150% 下与截图遮罩坐标的一致性 | 已知风险区（`tools.js:990,1015` 是唯一处理 DPR 处） | L3 用例 1/4 × 三种缩放实测 |
| 6 | `screenshot.js` 内联标注画布与 `editor.js` 的能力重叠度 | 决定是否新增一项"图像能力合并"（本方案未含，可能是下一个重复建设点） | 逐行对比两块 canvas 逻辑 |

---

## 8. 每批次验收标准（DoD）

一个批次**只有同时满足**以下四条才算完成：

1. **L0 全绿**：`check_contract.py` 无 ERROR，改动文件语法检查通过。
2. **L1 全绿**：`pytest tests/` 与改造前基线**完全一致**（通过数不减、失败数为 0）。
3. **L2 diff 为空**：`test_ui_smoke.py --baseline-in` 输出无差异（除显式白名单项），且新增探针（泄漏/焦点/材质/主题矩阵）全部通过。
4. **L3 抽样通过**：本批次相关用例（表格中标 ★ 者）在 Win10 22H2 上手工验证通过；涉及窗口形态的（E）必须在 Win11 上再验一遍。

**全局完成定义**：四个批次全部完成时，应能观察到三项可量化改善——
- 空闲 5 分钟 `js_api` 调用次数下降 ≥ 50%（L4）
- 页面切换 20 轮后资源计数零增长（L2 泄漏探针）
- 长任务在任意页面切换下都可见、可追踪（L3 用例 9/13/14 手工确认）

---

## 附：v5.3 布局规范化实施记录（已实施并验证）

> 对应人工反馈的四类具体问题：**输入框过小 / 按键与提示不水平 / 功能区小留白大 / 元素摆放不规范**。
> 量化工具：`scripts/ui_layout_audit.py`（桩桥接，不启动设备发现广播、剪贴板监听、全局热键、托盘，
> 不会干扰正在运行的程序；遍历 40 个页面量测控件宽度占比、行内中心偏差、卡片填充率、页面尾部空白）。

### 量化结果（前后同一口径）

| 指标 | 前 | 后 |
|---|---|---|
| 宽型输入框过窄（占宿主 <40%） | 19 | **12** |
| 行内元素不齐（同类控件高度差/中心偏差） | 76 | **5** |
| 页面尾部空白 >220px | 33 页 | **15 页** |
| 卡片"大卡装小内容" | 0 | 1 |

### 改动清单

1. **控件高度三档令牌**（`--ctl-h` 32 / `--ctl-h-sm` 27 / `--ctl-h-xs` 23）：此前 `.btn`/`.btn.sm`/`.btn.xs`/`.input`/`select`/`.switch` 有 6 种高度，这是"按键与提示不水平"的直接原因；顺带删除了作者当年为 select/input 高度差打的 34px 补丁。
2. **`.input.grow-in` 弹性输入框**：占据行内剩余宽度，替换 11 处写死的 150~220px 小宽度（剪贴板搜索、文件页过滤、Umi 地址、设备名、Clash 节点搜索/订阅地址、Web 目录、FTP 共享目录、V2rayN 订阅地址、密码库口令、网盘保存目录、分流规则快速输入）。
3. **拆分拥挤行**：ftp/web（6~7 元素一行 → 按语义拆行）、pan（9 元素 + 8 元素两行 → 四行）、routing（7 元素挤在一条 list-item → 两行，"值"输入独占宽度）、settings 备份卡（5 元素一行 → 两行）。
4. **工具页工作区**（14 个内置工具页）：结果面板常驻 + 空态提示 + 吸收剩余高度，把"顶部一个小卡 + 下方 400~670px 空白"变成"参数区 + 结果区"。
5. **画布型页面统一壳**（`combine` / `split` / `video` / `batch`）：此前没有 `page-head` 也没有卡片容器，工具栏裸铺；现在统一为 `page-head` + 卡片容器 + `.card.fill` 吸收剩余高度。
6. **顺带修掉的真实视觉 bug**：`combine`/`split` 的下拉框与数字输入框**没有 `.input` 类**，在深色主题下渲染为系统原生白底控件；补 `.input` 并把裸 `<label>` 换成 `.field-label`。
7. **`network` 页三卡并排**（`.card-cols`，说明卡跨整行）。
8. 零风险配套：`:focus-visible` 焦点环（此前全仓 0 处）、`.toast.success` 别名（此前 22 处成功提示渲染为无样式）、行内 `.hint` 不再沿用段落级 1.7 行高。

### 验证证据

- `python -m pytest tests/ -q` → **463 passed**（与改造前基线一致，失败数 0）
- `python tests/test_ui_smoke.py`（真实后端遍历 40 页）→ **PASSED，JS 报错 0**
- `node --check` 全部改动 JS + CSS 括号平衡
- `scripts/ui_layout_audit.py --compare` 前后对比见上表

### 由度量发现、读代码看不出的两个根因

- 剪贴板搜索框外层是**按内容收缩的 flex 容器**，里面再怎么 `flex:1` 也无空间可长；
- `toolShell` 返回的外层 `div` **没有 class**，直系子选择器匹配不到 → 工具页铺满规则失效。
  两者都是先按预期改完、审计显示"没生效"才定位到的。

### 不宜继续的部分（收益递减）

- 剩余 12 处"过窄"中约 8 处是 `<select>`/mono 数字框（按内容定宽或本应窄，属正确行为）。
- 剩余 15 页尾部空白多为内容本就不满一屏（如 ocr 用 434/751px），继续填充属于"为填而填"，不建议。
- "无内容大块"计数上升的部分是画布/预览/结果区在未载入文件时的应有空白。

**建议**：把 `scripts/ui_layout_audit.py` 加入 `test.bat`（与 `test_ui_smoke.py` 并列），作为后续批次的门禁之一。

---

## 附：v5.3 批次 C 实施记录（页面生命周期契约，已实施并验证）

> 对应 §3.3 的设计。目标：消除"页面离开后定时器与全局监听仍在跑"的架构债，
> 且**不要求现有 26 个页面做任何改造**。

### 落地方式

1. **`App.createPageCtx(page, el)`**（`webui/js/app.js`）：按语义分通道的页面上下文 ——
   `on`（数据，始终执行）/ `onView`（渲染，不可见跳过+登记待渲染）/ `every`（纯刷新定时器，不可见跳过）/
   `everyAdaptive`（数据型轮询，不可见降频）/ `onWindow`·`onDoc`（全局监听，viewOnly 且离开自动解绑）/
   `onLeave`（清理通道）。
2. **路由集成**：`mount(el, ctx)` —— 旧签名 `mount(el)` 只是多余实参，**未迁移页面零改动**；
   离开上一页时执行 `ctx._leave()`；回到已挂载页面时先 `ctx._enter()` 补渲染再走原有 `show()`。
3. **紧急开关**：`cfg.ui_ctx_guard`（默认开）。置 false 时各守卫失效，完全回到改造前行为，免发版回退。
4. **迁移范围（只动高价值 4 处，不做 27 页全量改造）**：
   - `clash.js`：连接面板轮询 `setInterval` → `ctx.every`；速率差分 → `ctx.everyAdaptive({active:2000, idle:10000})`
     （差分样本不能整段跳过，故降频而非停止）。
   - `cliphist.js`：递归 `setTimeout` 轮询（**改前永不停止**，且原注释写着"离开页面停止轮询"，与行为相反）→ `ctx.every`，并订正该注释。
   - `settings.js`：热键捕获监听加"已离开设置页则解除并放行"守卫 —— 修掉**切页后整个键盘被静默吞掉**的问题。
   - `screenshot.js`：窗口 resize 时不再对隐藏页面重算画布尺寸。
   - `editor.js` 的 `window.mouseup` 有意保留：它需要为"隐藏时中断的描边"收尾，跳过反而会留下绘制状态。

### 验证证据（`scripts/ui_layout_audit.py` 新增探针）

```
资源计数（切换 5 轮前后，共 200 次导航）：pages 40 / timers 3 → 完全持平
增长：定时器 0 / 订阅 0 / 清理登记 0    OK 无泄漏
离开 clash 页 6000ms 内被跳过的定时器回调：2 次    OK 守卫生效
```

- `pytest tests/ -q` → **463 passed**
- `tests/test_ui_smoke.py`（真实后端 40 页）→ **PASSED，JS 报错 0**
- 另：`ctx` 内置 `skipped` 计数器，使"切页后是否真的停止空转"成为可断言的事实。

### 一个测量教训（值得保留）

首次跑探针时"无泄漏"结论是**假的**：探针脚本里残留了一句 `root.__ctx = null`（早期草稿），
把被遍历页面的上下文对象清空，导致资源计数恒为 0。修掉该行后才得到上面 40/3 的真实基数。
**验证脚本本身也需要被验证** —— 计数全 0、全持平这类"太干净"的结果应当先怀疑探针。

---

## 附：v5.3 批次 B + E 实施记录（窗口同源 / 能力探测 / 材质 / 缩放边框）

### 批次 B：窗口形态同源

- **`app/core/win_spec.py`（新增）**：`webview.create_window(...)` 参数的唯一来源，
  生产（`main.py`）、UI 冒烟（`tests/test_ui_smoke.py`）、布局审计（`scripts/ui_layout_audit.py`）
  三处共用。此前冒烟测试只传 `width/height/js_api`，**从未测过生产窗口形态**
  （frameless / 透明 / easy_drag）—— 这是最容易出回归的地方。
- **透明决策只在一处**：`win_spec.window_kwargs()` 内部按能力探测决定是否透明，
  并顺带把 `hidden` 置真（避免内容就绪前显示一个透明窗口）。

### 批次 E：能力探测 + 材质 + 缩放边框

| 组件 | 作用 |
|---|---|
| `app/core/win_shell.py`（新增） | 材质能力探测与应用：Win10 走 `SetWindowCompositionAttribute` + `ACCENT_ENABLE_BLURBEHIND`；Win11 22621+ 走 `DWMWA_SYSTEMBACKDROP_TYPE`(Mica)。失败一律降级不透明并回报原因 |
| `app/bridge/cap_api.py`（新增，`cap_*`） | `cap_get`（前端 boot 早期调用，能力驱动）/ `material_set` / `material_state` |
| `main.py` | 窗口按 `win_spec` 创建；`loaded` 后应用材质 → 回报状态 → 再 `show()`（顺序不可颠倒：透明窗口在内容就绪前可见会直接透出桌面） |
| `webui/css/app.css` | `body.win-transparent` 96% 兜底底色 + `material-blur/mica` 外壳毛玻璃。**兜底是关键**：即使 DWM 材质未生效，画面也只是"接近不透明"，不会破相 |
| `app/bridge/win_api.py` | `win_resize_by(dw, dh)`：补上**此前无法拖边缩放窗口**的功能缺口 |
| `webui/js/window_drag.js` | 5px 边缘命中 → 逐帧调 `win_move_by` / `win_resize_by` |

**为什么缩放不用 `WM_NCLBUTTONDOWN`**：该方案在 `win_begin_drag` 的注释里已被记录为
"在 WebView2 上因鼠标捕获不在本进程而拖不动"。改用与 pywebview 自带 `easy_drag`
相同的路径（前端逐帧 mousemove → 桥接 → `window.move/resize`），**这条路径在本应用已被拖动功能长期验证**。
注意：`win_resize_by` 按帧调用，故**不要**把 `win_` 加入 `base.py` 的自动日志白名单，否则刷屏（已写在方法注释里）。

### 验证证据（Win10 19045 真机）

```
-- v5.3 窗口材质（与生产同一套 win_spec / win_shell）
   透明窗口：是
   材质应用：blur（模式 auto，build 19045）
```

- `pytest tests/ -q` → **463 passed**
- `tests/test_ui_smoke.py`（**已切换为生产窗口形态**：frameless + 透明 + 材质）→ **40 页 PASSED，JS 报错 0**
- 布局与生命周期探针不受影响（40 个页面上下文 / 3 个定时器 / 无增长 / 守卫生效）
- 回退方式：`cfg.ui_material = "off"`（重启生效），窗口回到不透明
