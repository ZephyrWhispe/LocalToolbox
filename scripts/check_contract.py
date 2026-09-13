"""前后端契约校验（零依赖，正则 + ast）。

为什么需要：前端调一个后端不存在的方法、订阅一个已改名的事件、读一个拼错的
配置键，**都不会报错**，只会静默失效 —— `App.tryCall` 失败只弹个 toast、
`cfg.xxx` 是 undefined、事件永远不来。这类问题靠人眼 review 抓不住，
脚本一秒就能抓出来。

用法：
    python scripts/check_contract.py              # 输出报告；有 ERROR 时退出码 1
    python scripts/check_contract.py --warn-only  # 只警告不失败（逃生开关）

检查项与口径：
  1. 前端调用的 js_api 方法 ⊆ 后端公开方法                      ERROR
     （后端集合取 `dir(Bridge)`，即 pywebview 真正暴露给前端的方法；
       前端集合含 App.call/tryCall/guardedCall 与旧页面的本地 call() 兼容层）
  2. 前端订阅的事件名 ⊆ 后端 emit/_push 的事件 ∪ 前端 dispatch 的事件   ERROR
     白名单见 EVENT_OK（少数事件由非 App.on 通道消费）
  3. 前端读写的 cfg 键 ⊆ config.DEFAULTS                        WARN
     （历史上存在非 DEFAULTS 的动态键，故为警告级；见 CFG_OK）
  4. 后端发出但前端无人订阅的事件                                 WARN（信息性）
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 白名单：这些键/事件不进检查（各有历史原因，加注释说明为什么）
CFG_OK = {
    "tool_favs", "tool_recent",   # 工具箱收藏/最近使用：前端 cfg_set 动态写入，未登记进 DEFAULTS
}
EVENT_OK = {
    "app_log",        # 由 app.js 的状态栏订阅（在 boot 内），扫描能覆盖；留作白名单防误报
    "material_state", # 由 ui.js 的工具库订阅
}
# 后端专属事件（无人订阅属正常，列出以便发现"前端漏订阅"）的白名单
EVENT_BACKEND_ONLY_OK = {
    "cli_action",
}


def _js_files():
    return [p for p in (ROOT / "webui" / "js").rglob("*.js")
            if "vendor" not in p.parts]


def _py_files():
    return [p for p in (ROOT / "app").rglob("*.py")] + [ROOT / "main.py"]


def backend_methods():
    """后端暴露给前端的 js_api 方法名（用 pywebview 同口径：dir(Bridge)）。"""
    from app.bridge.bridge import Bridge
    return {n for n in dir(Bridge)
            if not n.startswith("_") and callable(getattr(Bridge, n, None))}


def backend_events():
    """后端发出的事件名：emit/_emit/_push 的字面量。

    注意两类别名要靠 `backend_literals()` 兜底，正则本身抓不到：
      - `self._emit("xfer_log", ...)`（transfer.py 的薄包装，前面是下划线）
      - `self.emit(self._overlay_cancel_event(mode), None)`（名字由 helper 返回）
    """
    ev = set()
    pat = re.compile(r'\b_?(?:emit|_push)\(\s*["\']([A-Za-z_]\w*)["\']')
    for p in _py_files():
        try:
            src = p.read_text(encoding="utf-8")
        except Exception:
            continue
        ev |= set(pat.findall(src))
        if "emit_log(" in src:
            ev.add("app_log")
    return ev


def backend_literals():
    """后端出现过的全部字符串字面量（用 ast，排除注释与文档串的干扰最小化）。

    作为事件名的兜底来源：动态拼出来的事件名（由 helper 返回字面量）在这里能找到，
    而**前端拼错的名字在整个后端不会以字面量形式出现** —— 仍能被抓出来。
    """
    lits = set()
    for p in _py_files():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                lits.add(node.value)
    return lits


def frontend_scan():
    """返回 (调用的 js_api 方法, 订阅的事件, 前端 dispatch 的事件, 读写的 cfg 键)。"""
    calls, subs, dispatches, cfgs = {}, {}, set(), {}
    call_re = re.compile(r'App\.(?:call|tryCall|guardedCall)\(\s*["\']([A-Za-z_]\w*)["\']')
    alias_re = re.compile(r'(?<![\w.$])call\(\s*["\']([A-Za-z_]\w*)["\']')
    sub_re = re.compile(r'App\.on\(\s*["\']([A-Za-z_]\w*)["\']')
    disp_re = re.compile(r'App\.dispatch\(\s*["\']([A-Za-z_]\w*)["\']')
    cfg_re = re.compile(r'App\.state\.cfg\.([a-z_][a-z0-9_]*)')
    set_re = re.compile(r'cfg_set\(\s*["\']([A-Za-z_]\w*)["\']')
    for p in _js_files():
        try:
            src = p.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = p.relative_to(ROOT).as_posix()
        for m in call_re.finditer(src):
            calls.setdefault(m.group(1), []).append(rel)
        for m in alias_re.finditer(src):
            calls.setdefault(m.group(1), []).append(rel)
        for m in sub_re.finditer(src):
            subs.setdefault(m.group(1), []).append(rel)
        dispatches |= set(disp_re.findall(src))
        for m in cfg_re.finditer(src):
            cfgs.setdefault(m.group(1), []).append(rel)
        for m in set_re.finditer(src):
            cfgs.setdefault(m.group(1), []).append(rel)
    # 过滤掉 JS 侧明显的非 cfg 用法（如 cfg.get / cfg.data）
    for k in ("get", "set", "data", "then", "length", "map", "value"):
        cfgs.pop(k, None)
    return calls, subs, dispatches, cfgs


def main():
    warn_only = "--warn-only" in sys.argv

    try:
        be_methods = backend_methods()
    except Exception as e:
        print("[ERROR] 无法导入 Bridge（%s: %s）—— 契约校验跳过方法检查" % (type(e).__name__, e))
        be_methods = set()

    be_events = backend_events()
    be_lits = backend_literals()
    calls, subs, dispatches, cfgs = frontend_scan()
    from app.core.config import DEFAULTS
    cfg_keys = set(DEFAULTS)

    errors, warns = [], []

    for name, where in sorted(calls.items()):
        if name not in be_methods:
            errors.append("前端调用但后端不存在：%s（%s）" % (name, ", ".join(sorted(set(where)))))

    ok_events = be_events | dispatches | EVENT_OK
    for name, where in sorted(subs.items()):
        if name in ok_events:
            continue
        # 兜底：事件名由 helper 动态返回时，只看它是否作为字面量出现在后端
        if name in be_lits:
            warns.append("前端订阅的事件名在后端仅以动态方式发出（字面量兜底命中）：%s" % name)
            continue
        errors.append("前端订阅但后端从不发出：%s（%s）" % (name, ", ".join(sorted(set(where)))))

    for name, where in sorted(cfgs.items()):
        if name not in cfg_keys and name not in CFG_OK:
            warns.append("前端读写但 DEFAULTS 未定义：%s（%s）" % (name, ", ".join(sorted(set(where)))))

    no_sub = sorted(e for e in be_events
                    if e not in subs and e not in EVENT_BACKEND_ONLY_OK)

    print("=" * 78)
    print("前后端契约校验 · js_api %d 个 · 事件 %d 个 · cfg 键 %d 个 · 前端脚本 %d 个"
          % (len(be_methods), len(be_events), len(cfg_keys), len(_js_files())))
    print("前端调用 js_api %d 个 · 订阅事件 %d 个 · 读写 cfg 键 %d 个"
          % (len(calls), len(subs), len(cfgs)))

    for e in errors:
        print("  [ERROR] %s" % e)
    for w in warns:
        print("  [WARN]  %s" % w)
    if no_sub:
        print("  [WARN]  后端发出但前端未订阅（%d 个）：%s" % (len(no_sub), ", ".join(no_sub[:12])))

    if errors:
        print("\n契约校验失败：%d 个错误 / %d 个警告%s"
              % (len(errors), len(warns), "（--warn-only 模式不阻塞）" if warn_only else ""))
    else:
        print("\n契约校验通过：无错误 / %d 个警告" % len(warns))
    return 0 if (not errors or warn_only) else 1


if __name__ == "__main__":
    sys.exit(main())
