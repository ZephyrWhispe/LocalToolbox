"""UI 布局量化审计：真实渲染窗口里逐页量测布局缺陷，输出可对比的 JSON 基线。

用法：
    python scripts/ui_layout_audit.py                     # 打印摘要 + 写 tests/baseline/layout_audit.json
    python scripts/ui_layout_audit.py --out /tmp/a.json   # 自定义输出路径

设计要点（与 tests/test_ui_smoke.py 的区别）：
1. **桩桥接**：不实例化 app.bridge.bridge.Bridge，避免启动设备发现广播、
   剪贴板监听、全局热键、防火墙放行等真实副作用。桩按真实 Bridge 的公开方法
   名单动态生成，全部返回 {ok:False, err:"stub"}，因此页面会走"无数据"路径，
   但**结构、控件、容器、行布局全部照常渲染**——这正是本脚本要量的对象。
2. **量测三类布局缺陷**（对应人工反馈的"输入框很小 / 按键提示不水平 / 功能区小留白大"）：
   - 控件宽度：宽型输入框（text/search/url/password）宽 / 所在卡片内容宽 的占比
   - 行对齐：同一 flex 行内子元素垂直中心的偏差（px）
   - 留白：卡片内容高度利用率、页面底部空白、无内容的大块容器
3. 输出 JSON 便于改造前后 diff（--compare 可对比两份结果）。

注意：会短暂弹出真实窗口（遍历完自动关闭），标题为"布局审计"。
"""

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import webview  # noqa: E402

from app.bridge.bridge import Bridge  # noqa: E402  仅用于取方法名单，不实例化
from main import ui_index  # noqa: E402


# ---------------------------------------------------------------- 桩桥接
def build_stub_api():
    """按真实 Bridge 的公开方法名生成桩：全部返回 {ok:False,err:'stub'}。

    不实例化 Bridge → 无任何真实副作用（发现广播/剪贴板/热键/托盘）。
    """
    names = [
        n for n in dir(Bridge)
        if not n.startswith("_") and callable(getattr(Bridge, n, None))
    ]

    def _mk(_name):
        def _stub(*_a, **_k):
            return {"ok": False, "err": "stub"}
        return _stub

    return type("StubBridge", (), {n: _mk(n) for n in names})()


# ---------------------------------------------------------------- 探针
PROBE_JS = r"""
window.__layout = null;
(function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const WIDE_TYPES = ["text", "search", "url", "password", "email", "tel", ""];
  const NARROW_TYPES = ["number", "color", "range", "checkbox", "radio", "date",
                        "time", "file", "hidden", "button", "submit"];

  const txt = (el) => (el.textContent || "").trim();
  const cls = (el) => (el.className && String(el.className).slice(0, 48)) || "";

  /* 控件在页面里是否真正可见（跳过 subnav 未激活 pane、display:none 折叠区） */
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    if (el.offsetParent === null && getComputedStyle(el).position !== "fixed") return false;
    return true;
  };

  /* 找到控件的"内容宿主"：最近的 .card / .sec-body-inner / .subnav-pane / .page 自身 */
  const hostOf = (el) => {
    let n = el.parentElement, guard = 0;
    while (n && guard++ < 12) {
      if (n.classList && (n.classList.contains("card") || n.classList.contains("sec-body-inner")
          || n.classList.contains("subnav-pane") || n.classList.contains("tool-page")
          || n.classList.contains("editor-panel"))) return n;
      n = n.parentElement;
    }
    return null;
  };

  (async () => {
    const out = { pages: [], viewport: { w: window.innerWidth, h: window.innerHeight },
                  zoom: document.body.style.zoom || "1" };
    for (const p of App.pages) {
      const rec = { id: p.id, title: p.title || "", hidden: !!p.hidden,
                    controls: [], rows: [], cards: [], blocks: [], emptyBlocks: [] };
      try { await App.navigate(p.id); } catch (e) { rec.navErr = String(e && e.message || e); }
      await sleep(p.id.indexOf("tool-") === 0 ? 260 : 140);
      const root = document.getElementById("page-" + p.id);
      if (!root) { out.pages.push(Object.assign(rec, { missing: true })); continue; }

      /* 1) 控件：宽型输入框的宽度占比；所有控件的实测高度 */
      const controls = root.querySelectorAll(
        "input.input, select.input, textarea.input, button.btn, .chk, .switch, .field-label, .hint");
      for (const el of controls) {
        if (!shown(el)) continue;
        const r = el.getBoundingClientRect();
        const host = hostOf(el);
        const hr = host ? host.getBoundingClientRect() : null;
        const cs = getComputedStyle(host || root);
        const inner = hr ? hr.width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) : 0;
        const tag = el.tagName.toLowerCase();
        const type = tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : "";
        /* "宽型控件"只算长文本输入与多行文本域：<select> 按最长选项定宽是
           正确的 UI 行为（下拉框撑满整行反而难看），单列统计不进缺陷数。 */
        const isWide = (tag === "input" && WIDE_TYPES.indexOf(type) >= 0) || tag === "textarea";
        /* 诊断字段：控件不伸展时定位原因 —— 自身 flex 与父容器的 display/宽度。
           （常见两种：父容器按内容收缩、或父容器不是 flex 容器） */
        const ecs = getComputedStyle(el);
        const par = el.parentElement;
        const pcs = par ? getComputedStyle(par) : null;
        rec.controls.push({
          tag: tag, type: type, cls: cls(el), w: Math.round(r.width), h: Math.round(r.height),
          hostW: Math.round(inner), wide: isWide,
          ratio: inner > 40 ? +(r.width / inner).toFixed(3) : null,
          label: (el.getAttribute("placeholder") || txt(el) || "").slice(0, 24),
          flex: ecs.flexGrow + "/" + ecs.flexShrink + "/" + ecs.flexBasis,
          pDisp: pcs ? pcs.display : "-",
          pCls: par ? cls(par) : "-",
          pW: par ? Math.round(par.getBoundingClientRect().width) : 0,
        });
      }

      /* 2) 行对齐：flex 行内子元素垂直中心偏差 + 高度参差 */
      const rows = root.querySelectorAll(
        ".row, .field-row, .card-actions, .tool-out-bar, .editor-toolbar, .editor-annotate-toolbar, "
        + ".file-toolbar, .file-actionbar, .task-row, .tool-toolbar, .effect-row, .video-info");
      for (const row of rows) {
        if (!shown(row)) continue;
        const kids = Array.from(row.children).filter(shown);
        if (kids.length < 2) continue;
        /* 按"视觉行"聚类：纵向重叠超过较矮者一半的兄弟视为同一行，
           否则换行后跨行比较中心会产生假报警（如 17 控件自动换行的工具栏）。 */
        const box = kids.map((k) => k.getBoundingClientRect());
        const lines = [];
        box.forEach((b, i) => {
          const cy = b.top + b.height / 2;
          const hit = lines.find((L) => Math.abs(L.cy - cy) < Math.min(L.h, b.height) / 2 + 1);
          if (hit) { hit.cy = (hit.cy * hit.n + cy) / (hit.n + 1); hit.h = Math.max(hit.h, b.height); hit.n++; hit.idx.push(i); }
          else lines.push({ cy: cy, h: b.height, n: 1, idx: [i] });
        });
        let worstC = 0, worstS = 0, worstN = kids.length, worstKids = [];
        /* 只比"控件"，且高度差只在同类控件之间比：
           - 纯文本标签/提示比输入框矮是正常的（居中后并不难看）
           - .switch 的滑轨本身只有 19px，与 32px 输入框同行不算缺陷
           真正的"不水平"= 同一行里同类控件（按钮之间 / 输入框之间）
           高度不一致，或不同控件的垂直中心不在一条线上。 */
        const kindOf = (el) => {
          const t = el.tagName.toLowerCase();
          return (t === "input" || t === "select" || t === "textarea") ? "field" : "btn";
        };
        const isCtl = (el) => {
          const t = el.tagName.toLowerCase();
          if (t === "button" || t === "input" || t === "select" || t === "textarea") return true;
          return /(^|\s)(switch|chk|tag)(\s|$)/.test(cls(el));
        };
        for (const L of lines) {
          const ctlIdx = L.idx.filter((i) => isCtl(kids[i]));
          if (ctlIdx.length < 2) continue;
          const cs = ctlIdx.map((i) => box[i].top + box[i].height / 2);
          const cd = Math.max(...cs) - Math.min(...cs);
          let sp = 0;
          for (const kind of ["btn", "field"]) {
            const hs = ctlIdx.filter((i) => kindOf(kids[i]) === kind).map((i) => box[i].height);
            if (hs.length >= 2) sp = Math.max(sp, Math.max(...hs) - Math.min(...hs));
          }
          if (cd + sp > worstC + worstS) {
            worstC = cd; worstS = sp; worstN = ctlIdx.length;
            worstKids = ctlIdx.map((i) => cls(kids[i]) || kids[i].tagName.toLowerCase()).slice(0, 6);
          }
        }
        if (!worstKids.length) continue;   // 该行只有一个控件 → 不存在"不齐"
        rec.rows.push({
          cls: cls(row), n: worstN, lines: lines.length,
          cDelta: +worstC.toFixed(1), hSpread: +worstS.toFixed(1),
          minH: 0, maxH: 0, kids: worstKids,
        });
      }

      /* 3) 卡片：内容高度利用率（留白比） */
      const cards = root.querySelectorAll(".card, .sec, .editor-panel");
      for (const c of cards) {
        if (!shown(c)) continue;
        const r = c.getBoundingClientRect();
        const cs = getComputedStyle(c);
        const padV = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
        let contentH = 0;
        for (const k of c.children) {
          if (!shown(k)) continue;
          const kr = k.getBoundingClientRect();
          contentH = Math.max(contentH, kr.bottom - r.top);
        }
        rec.cards.push({
          cls: cls(c), w: Math.round(r.width), h: Math.round(r.height),
          contentH: Math.round(contentH), padV: Math.round(padV),
          fill: r.height > 40 ? +(Math.max(0, contentH) / r.height).toFixed(2) : null,
          title: txt(c.querySelector(".card-title, .sec-head") || c).slice(0, 20),
        });
      }

      /* 4) 无效空白块：高度大但文本极少（排除列表/画布/日志等本就该空高的容器） */
      /* 排除"本就该占据空间"的容器：日志/空态/画布/结果面板/列表滚动区 */
      const KEEP = ["log-box", "empty", "canvas", "progress", "list", "tasklist", "cliphist-list",
                    "file-table", "tool-out-list", "tool-out", "tree-drawer", "subnav-pane",
                    "file-body"];
      const blocks = root.querySelectorAll("div, section, aside");
      for (const b of blocks) {
        if (!shown(b)) continue;
        const r = b.getBoundingClientRect();
        if (r.height < 120 || r.width < 260) continue;
        const c = cls(b);
        if (KEEP.some((k) => c.indexOf(k) >= 0)) continue;
        if (b.querySelector("canvas, img, video, iframe, textarea, .log-box")) continue;
        const t = txt(b);
        if (t.length > 30) continue;
        rec.emptyBlocks.push({ cls: c, h: Math.round(r.height), w: Math.round(r.width),
                               text: t.slice(0, 24) });
      }

      /* 5) 页面级留白：内容底部到视口底部的距离 */
      const content = document.getElementById("content");
      const cRect = content.getBoundingClientRect();
      let last = cRect.top;
      for (const k of root.children) {
        if (!shown(k)) continue;
        last = Math.max(last, k.getBoundingClientRect().bottom);
      }
      rec.pageFill = {
        viewportH: Math.round(cRect.height),
        usedH: Math.round(last - cRect.top),
        tailH: Math.round(cRect.bottom - last),
      };
      out.pages.push(rec);
    }
    /* ⑥ v5.3 页面上下文：资源不增长（切换多轮后必须持平）+ 可见性守卫生效 */
    out.leak = null;
    try {
      const stats0 = App._ctxStats();
      for (let round = 0; round < 5; round++) {
        for (const p of App.pages) { try { await App.navigate(p.id); } catch (e) {} }
      }
      const stats1 = App._ctxStats();
      out.leak = { before: stats0, after: stats1,
                   grewTimers: stats1.timers - stats0.timers,
                   grewSubs: stats1.subs - stats0.subs,
                   grewCleanups: stats1.cleanups - stats0.cleanups };
      /* 离开 Clash 页后，其定时器回调应全部走"跳过"分支（skipped 增长） */
      const clashEl = document.getElementById("page-clash");
      if (clashEl && clashEl.__ctx) {
        await App.navigate("settings");
        const s1 = clashEl.__ctx._stats().skipped;
        await sleep(6000);
        const s2 = clashEl.__ctx._stats().skipped;
        out.guard = { page: "clash", waitedMs: 6000, skippedDelta: s2 - s1 };
      }
    } catch (e) { out.leakErr = String(e && e.message || e); }
    window.__layout = JSON.stringify(out);
  })();
})();
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "tests" / "baseline" / "layout_audit.json"))
    ap.add_argument("--compare", default="", help="与已有基线 JSON 对比")
    args = ap.parse_args()

    api = build_stub_api()
    window = webview.create_window(
        "布局审计", ui_index(), js_api=api,
        **_win_kwargs(),
    )
    result = {}

    def probe():
        time.sleep(4)
        try:
            # v5.3：顺手验证窗口材质链路（与生产共用 win_spec / win_shell）
            try:
                from app.core import win_shell, win_spec
                from app.core.config import AppConfig
                cfg = AppConfig()
                result["transparent"] = bool(getattr(window, "transparent", False))
                result["material"] = win_shell.apply(
                    win_shell.hwnd_of(window), win_spec.material_mode(cfg))
            except Exception as e:
                result["material_err"] = "%s: %s" % (type(e).__name__, e)
            window.evaluate_js(PROBE_JS)
            for _ in range(320):          # 最多等 80 秒
                time.sleep(0.25)
                if window.evaluate_js("window.__layout"):
                    break
            raw = window.evaluate_js("window.__layout")
            result["data"] = json.loads(raw) if raw else None
            if not raw:
                result["error"] = "探针未完成（页面数过多或某页挂载阻塞）"
        except Exception as e:
            result["error"] = "%s: %s" % (type(e).__name__, e)
        window.destroy()

    threading.Thread(target=probe, daemon=True).start()
    webview.start()

    data = result.get("data")
    if not data:
        print("审计失败：%s" % result.get("error"))
        return 1

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print_summary(data, result)
    if args.compare:
        compare(args.out, args.compare)
    return 0


def _win_kwargs():
    """窗口参数与生产一致（frameless/shadow/easy_drag），保证量测的是真实形态。"""
    try:
        sys.path.insert(0, str(ROOT))
        from app.core.config import AppConfig
        from app.core import win_spec
        return win_spec.window_kwargs(AppConfig(), overrides={"hidden": False})
    except Exception:
        # win_spec 尚未落地时的兜底：与 main.py 的生产参数一致
        return {"frameless": True, "shadow": False, "easy_drag": True,
                "background_color": "#0f1419"}


# ---------------------------------------------------------------- 摘要
def _rec_is_wide(c):
    """按记录本身重算"宽型控件"：<select> 不算（按最长选项定宽是正确的），
    textarea 与长文本 input 算。两份 JSON 用同一口径，保证前后可比。"""
    if c.get("tag") == "textarea":
        return True
    if c.get("tag") != "input":
        return False
    return (c.get("type") or "text") in ("text", "search", "url", "password", "email", "tel")


def print_summary(data, result=None):
    result = result or {}
    pages = data.get("pages", [])
    print("=" * 78)
    print("布局审计 · 视口 %sx%s · 缩放 %s · 页面 %d"
          % (data["viewport"]["w"], data["viewport"]["h"], data["zoom"], len(pages)))

    bad_small, bad_row, bad_fill, bad_tail, bad_block = [], [], [], [], []
    for p in pages:
        if p.get("missing"):
            print("  [缺失] %s 未渲染" % p["id"])
            continue
        for c in p.get("controls", []):
            # 宽型控件却只占宿主 < 40% 宽度 → "输入框很小"
            if _rec_is_wide(c) and c["ratio"] is not None and c["ratio"] < 0.40 and c["hostW"] >= 380:
                bad_small.append((c["ratio"], p["id"], c))
        for r in p.get("rows", []):
            if r["cDelta"] > 2 or r["hSpread"] > 5:
                bad_row.append((max(r["cDelta"], r["hSpread"]), p["id"], r))
        for c in p.get("cards", []):
            if c["fill"] is not None and c["fill"] < 0.45 and c["h"] >= 90:
                bad_fill.append((c["fill"], p["id"], c))
        pf = p.get("pageFill") or {}
        if pf.get("tailH", 0) > 220:
            bad_tail.append((pf["tailH"], p["id"], pf))
        for b in p.get("emptyBlocks", []):
            bad_block.append((b["h"], p["id"], b))

    def dump(title, items, fmt):
        print("\n-- %s（%d 处）" % (title, len(items)))
        for _, pid, item in sorted(items, key=lambda x: -x[0])[:14]:
            print("   %-14s %s" % (pid, fmt(item)))

    dump("输入框过窄（宽型控件占宿主 <40%）", bad_small,
         lambda c: "%-9s %-22s 宽 %4d / 宿主 %4d = %s  占位=%s"
                   % (c["tag"], c["cls"] or c["label"], c["w"], c["hostW"], c["ratio"], c["label"]))
    dump("行内元素不齐（中心偏差 >4px 或高度差 >8px）", bad_row,
         lambda r: "%-26s 子元素 %d 个 中心偏差 %.1f 高度差 %.1f (%d~%d)  %s"
                   % (r["cls"], r["n"], r["cDelta"], r["hSpread"], r["minH"], r["maxH"],
                      ",".join(r["kids"])))
    dump("卡片留白过大（内容填充 <45%）", bad_fill,
         lambda c: "%-22s %4dx%-4d 内容高 %4d 填充 %.2f  %s"
                   % (c["cls"] or c["title"], c["w"], c["h"], c["contentH"], c["fill"], c["title"]))
    dump("页面底部空白 >220px", bad_tail,
         lambda f: "视口高 %4d 已用 %4d 尾部空白 %4d" % (f["viewportH"], f["usedH"], f["tailH"]))
    dump("无内容大块（高 >120px 且几乎无文字）", bad_block,
         lambda b: "%-22s %4dx%-4d  文本=%s" % (b["cls"], b["w"], b["h"], b["text"]))

    tot_c = sum(len(p.get("controls", [])) for p in pages)
    tot_r = sum(len(p.get("rows", [])) for p in pages)
    print("\n-- v5.3 页面上下文（生命周期契约）")
    lk = data.get("leak") or {}
    if lk:
        print("   资源计数（切换 5 轮前后）：%s → %s"
              % (lk["before"], lk["after"]))
        print("   增长：定时器 %d / 订阅 %d / 清理登记 %d  %s"
              % (lk["grewTimers"], lk["grewSubs"], lk["grewCleanups"],
                 "OK 无泄漏" if max(lk["grewTimers"], lk["grewSubs"],
                                   lk["grewCleanups"]) <= 0 else "!! 有增长"))
    else:
        print("   未取得资源计数%s" % ("（%s）" % data.get("leakErr") if data.get("leakErr") else ""))
    gd = data.get("guard") or {}
    if gd:
        print("   离开 %s 页 %dms 内被跳过的定时器回调：%d 次  %s"
              % (gd["page"], gd["waitedMs"], gd["skippedDelta"],
                 "OK 守卫生效" if gd["skippedDelta"] > 0 else "!! 守卫未生效（定时器仍在空转）"))
    print("\n-- v5.3 窗口材质（与生产同一套 win_spec / win_shell）")
    print("   透明窗口：%s" % ("是" if result.get("transparent") else "否"))
    if result.get("material"):
        m = result["material"]
        print("   材质应用：%s（模式 %s，build %s）%s"
              % (m.get("applied"), m.get("mode"), m.get("build"),
                 "  原因：" + m["reason"] if m.get("reason") else ""))
    elif result.get("material_err"):
        print("   材质应用失败：%s" % result["material_err"])
    print("\n汇总：控件 %d · 行 %d · 过窄 %d · 不齐 %d · 留白卡 %d · 底部空白页 %d · 空块 %d"
          % (tot_c, tot_r, len(bad_small), len(bad_row), len(bad_fill), len(bad_tail), len(bad_block)))


def compare(new_path, old_path):
    with open(new_path, encoding="utf-8") as f:
        new = json.load(f)
    with open(old_path, encoding="utf-8") as f:
        old = json.load(f)

    def stat(d):
        s = {"small": 0, "row": 0, "fill": 0, "tail": 0, "block": 0, "controls": 0}
        for p in d.get("pages", []):
            s["controls"] += len(p.get("controls", []))
            s["small"] += sum(1 for c in p.get("controls", [])
                              if _rec_is_wide(c) and c["ratio"] is not None
                              and c["ratio"] < 0.40 and c["hostW"] >= 380)
            s["row"] += sum(1 for r in p.get("rows", [])
                            if r["cDelta"] > 2 or r["hSpread"] > 5)
            s["fill"] += sum(1 for c in p.get("cards", [])
                             if c["fill"] is not None and c["fill"] < 0.45 and c["h"] >= 90)
            s["tail"] += sum(1 for p2 in [p] if (p.get("pageFill") or {}).get("tailH", 0) > 220)
            s["block"] += len(p.get("emptyBlocks", []))
        return s

    a, b = stat(old), stat(new)
    print("\n" + "=" * 78)
    print("改造前 → 改造后")
    for k, label in (("small", "输入框过窄"), ("row", "行内不齐"), ("fill", "卡片留白过大"),
                     ("tail", "页面底部空白"), ("block", "无内容大块"), ("controls", "控件总数")):
        d = a[k] - b[k]
        print("  %-12s %4d → %-4d  %s" % (label, a[k], b[k],
                                          ("改善 %d" % d) if d > 0 else (("增加 %d" % -d) if d < 0 else "持平")))


if __name__ == "__main__":
    sys.exit(main())
