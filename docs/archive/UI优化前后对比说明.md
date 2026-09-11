# UI 系统性优化：前后对比说明与 UI 规范

> 本文件记录对 Web 前端（`webui/`）的一次系统性优化：功能合并、风格统一、布局改进、命名规范、响应式适配。
> 涉及文件：`webui/js/ui.js`、`webui/css/app.css`、`webui/index.html`、`webui/js/app.js` 及 11 个页面。

---

## 一、功能合并（消除冗余）

| 冗余点 | 优化前 | 优化后 | 说明 |
|---|---|---|---|
| **日志框** | clipboard / km / transfer / web / ftp 5 个页面各写一份 `log()`（时间戳 + 限行数 + 滚动） | 统一为 `App.makeLog(el)`，页面一行创建 | ui.js 中约 20 行实现，替换 5 处共约 35 行重复代码 |
| **日期格式化** | file.js 的 `fmtMtime`、ftp.js 的 `fmtDate` 两份实现 | 统一为 `App.fmtDate(ts)` | 处理空值 / 非法时间戳，语义一致 |
| **服务端表单** | web / ftp 两个服务页的「监听地址/端口/共享目录/账号/开关 + 启动停止行 + 日志」结构高度雷同（各约 60 行） | 统一为 `App.svcCard(title, rows, actions, log)` 卡片组件 | 页面只需声明数据行，结构约 15 行 |
| **凭据弹窗** | scan.js 自造「用户名 + 密码」双输入模态框（约 27 行） | 扩展 `App.modal` 支持多输入 `inputs: [{label, type, placeholder}]`，scan 改为包装调用 | 全局唯一一套模态框实现 |
| **状态标签** | clipboard / transfer / web / ftp 用 `innerHTML` 字符串拼接 `<span class="tag ok">…</span>` | 统一为 `App.statusTag(text, kind, icon)` DOM 构建 | 天然转义，杜绝 XSS 隐患 |
| **字段前缀标签** | 混用 `.muted`（web/share/settings/transfer）与 `.hint`（ftp） | 统一为 `.field-label` | 样式一致：muted 色 + 不换行 |
| **行布局** | 全站 `App.h("div", { class: "row" }, …)` 冗长写法 | 统一为 `App.row(...)` | 语义化 + 少一层嵌套 |
| **内联样式** | `flex: "none"` / `marginLeft: "auto"` 等内联 style 遍布页面 | 新增 `.flex-none` / `.ml-auto` / `.text-right` / `.min-w-0` / `.text-clip` 工具类 | 样式下沉到 CSS，组件行为与表现分离 |

对应源码位置：
- 组件实现：`webui/js/ui.js`（`fmtDate` / `makeLog` / `statusTag` / `row` / `svcCard`）
- 服务表单重构：`webui/js/pages/web.js`、`webui/js/pages/ftp.js`
- 凭据复用：`webui/js/pages/scan.js`
- 日期复用：`webui/js/pages/file.js`、`webui/js/pages/ftp.js`
- 日志复用：`webui/js/pages/clipboard.js`、`km.js`、`transfer.js`、`web.js`、`ftp.js`

---

## 二、UI 规范（设计令牌）

### 2.1 设计令牌（Design Tokens）
全局色板收敛为 CSS 变量（`webui/css/app.css` 顶部），组件禁止写死色值：

| 分类 | 令牌 | 用途 |
|---|---|---|
| 背景层级 | `--bg` / `--bg2` / `--panel` / `--card` / `--card2` | 画布 / 侧栏 / 面板 / 卡片 / 卡片内层 |
| 文字层级 | `--text` / `--muted` / `--faint` | 主文字 / 次要 / 弱化提示 |
| 主色 | `--accent` / `--accent-soft` | 选中态、主按钮、链接 |
| 状态色 | `--ok` / `--warn` / `--danger`（含 `-soft` 底） | 成功 / 警告 / 错误 |
| 分隔 | `--border` / `--border2` | 细边 / 强调边 |
| 其他 | `--shadow` | 浮层投影 |

深浅主题：`:root, body.dark` 与 `body.light` 两套令牌，`App.setTheme()` 切换，无任何硬编码颜色。

### 2.2 组件命名与职责（`App.*` 命名空间）

| 组件 | 签名 | 职责 |
|---|---|---|
| `App.h(tag, attrs, …children)` | DOM 构建 | 已有，全站唯一入口 |
| `App.makeLog(el, maxLines?)` | 返回 `log(msg)` | 日志框（追加时间戳 / 限行 / 滚动） |
| `App.fmtDate(ts)` | `string` | `YYYY-MM-DD HH:mm` |
| `App.fmtTime(ts)` | `string` | `HH:mm:ss` |
| `App.statusTag(text, kind, icon?)` | DOM | 状态标签，kind ∈ ok/warn/danger/accent |
| `App.row(...children)` | DOM | `.row` 布局行 |
| `App.svcCard(title, rows, actions?, log?)` | DOM | 服务端表单卡片 |
| `App.modal({input|inputs, …})` | Promise | 单/多输入模态框 |

约定：**只有基础层（ui.js）可以定义通用组件；页面只声明业务数据与事件，禁止再自造 UI 组件。**

### 2.3 页面结构模板
```
page-head（标题 + 副标题）
  └─ svcCard / card / list / log-box
```
卡片内行为 `App.row(...)`；字段前缀用 `class="field-label"`；状态用 `App.statusTag()`；日志用 `App.makeLog()`。

---

## 三、布局结构优化

- 保持「标题栏 + 左导航 + 内容 + 右设备面板 + 状态栏」的 3 栏 grid 骨架（`#app`），信息层级不变：
  - 标题栏：品牌 + 抽屉开关（小屏）+ 主题切换
  - 左侧导航：按分组渲染，激活态高亮
  - 中部内容：页面卡片化，卡片内字段行清晰分隔
  - 右侧设备面板：设备卡片 + 能力标签（剪贴板 / 文件 / 已配对）+ 悬停操作
  - 状态栏：全局消息 + 本机信息
- 字段/操作/状态在功能行内同层排布，减少嵌套层级；
- 模态框增加 `max-height + 滚动`，内容过长时不再溢出屏幕。

## 四、响应式显示

| 断点 | 行为 |
|---|---|
| `> 1100px` | 固定三栏 grid（200px + 1fr + 284px） |
| `≤ 1100px` | 导航与设备面板转为左右**抽屉**（`position: fixed` + 滑入动画），标题栏出现汉堡/列表切换按钮，点击内容区/切换项自动关闭，抽屉带半透明遮罩 |
| `≤ 640px` | 内容与卡片内边距收缩，长按钮 `btn.row-wide` 可整行换行，避免溢出 |
| 任意尺寸 | 模态框 `max-width: 90vw`；支持 `prefers-reduced-motion` 关闭动画 |

实现位置：`webui/css/app.css`（`@media` 断点 + `.drawer-toggle`）、`webui/index.html`（两个抽屉按钮）、`webui/js/app.js`（`initDrawers()` / `closeDrawers()`）。

## 五、代码组织

```
webui/
├── index.html        # 骨架：标题栏（含抽屉按钮）、三栏容器、toasts、modal-root
├── css/app.css       # 设计令牌 + 组件样式 + 工具类 + 响应式断点
├── js/
│   ├── ui.js         # 基础层：DOM/图标/Toast/Modal/格式化/脱敏 + 通用组件
│   ├── bridge.js     # pywebview 桥接、事件分发、页面注册表
│   ├── app.js        # 应用壳：导航/路由/设备面板/主题/抽屉
│   └── pages/*.js    # 业务页面：11 个，仅业务逻辑，复用 App.* 组件
```

## 六、回归验证

| 测试 | 结果 |
|---|---|
| `node --check`（全部 10 个 JS 文件） | 通过 |
| `test_ui_smoke.py`（真实窗口遍历 11 页） | 11 页全部挂载，无 mountErr / toast 错误 |
| `test_transfer.py`（传输引擎） | 9/9 通过 |
| `test_pairing.py`（配对加密） | 9/9 通过 |

## 七、实现代码摘要

### ui.js 新增通用组件
```js
/** 日志框：包装容器元素，返回 log(msg) 函数 */
function makeLog(el, maxLines) {
  maxLines = maxLines || 200;
  return (msg) => {
    for (const line of String(msg).split("\n")) {
      el.appendChild(h("div", null,
        `[${new Date().toLocaleTimeString("zh-CN", { hour12: false })}] ${line}`));
    }
    while (el.children.length > maxLines) el.firstChild.remove();
    el.scrollTop = el.scrollHeight;
  };
}

/** 状态标签：kind ∈ ok | warn | danger | accent */
function statusTag(text, kind, iconName) {
  const k = kind && kind !== "default" ? " " + kind : "";
  return h("span", { class: "tag" + k },
    iconName ? [icon(iconName, 11), String(text)] : String(text));
}

/** 服务卡片：统一服务端表单结构 */
function svcCard(title, rows, actions, log) {
  return h("div", { class: "card" },
    h("div", { class: "card-title" }, title),
    ...rows,
    actions && actions.length ? h("div", { class: "row" }, ...actions) : null,
    log ? h("div", { style: { marginTop: "10px" } }, log) : null,
  );
}
```

### web.js 使用 svcCard（同 ftp.js）
```js
el.appendChild(App.svcCard(
  "Web 服务器（HTTP 浏览 + WebDAV 挂载…）",
  [
    App.row(App.h("span", { class: "field-label" }, "监听地址："), refs.host, /* … */),
    App.row(/* 账号 / 开关行 */),
    /* 提示与认证修复行 */
  ],
  [refs.start, refs.stop, refs.fwRm, refs.state, refs.url],
  refs.log,
));
```

### app.css 响应式核心
```css
@media (max-width: 1100px) {
  .drawer-toggle { display: inline-flex; }
  #app { grid-template-columns: 1fr; grid-template-areas: "header" "content" "status"; }
  #nav { position: fixed; left: 0; top: 46px; bottom: 26px; transform: translateX(-105%); transition: transform .22s ease; }
  #devices { position: fixed; right: 0; top: 46px; bottom: 26px; transform: translateX(105%); transition: transform .22s ease; }
  #app.nav-open #nav { transform: translateX(0); }
  #app.devices-open #devices { transform: translateX(0); }
}
```