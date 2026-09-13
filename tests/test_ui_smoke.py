"""UI 冒烟测试：真实启动 pywebview 窗口，遍历全部页面并收集错误。

每个页面都会检查：挂载成功 / 挂载异常 / 弹窗提示 / JS 报错（含未捕获 Promise）/
渲染异常文本（[object Object]、undefined、NaN）/ 空下拉框 / 横向溢出。
会短暂打开一个真实窗口（遍历完成后自动关闭）。
"""

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webview

from app.bridge.bridge import Bridge
from main import ui_index

PROBE_JS = """
window.__probe = null;
(function () {
  /* JS 报错收集：error / 未捕获 Promise / console.error */
  const errs = [];
  window.addEventListener("error", function (e) {
    errs.push("error: " + String(e.message || e));
  });
  window.addEventListener("unhandledrejection", function (e) {
    const r = e.reason;
    errs.push("rejection: " + String((r && r.message) || r));
  });
  const origErr = console.error;
  console.error = function () {
    try {
      errs.push("console.error: " + Array.prototype.map.call(arguments, function (x) {
        return typeof x === "string" ? x : (x && x.message) || String(x);
      }).join(" "));
    } catch (e) {}
    return origErr.apply(console, arguments);
  };
  window.__probeErrs = errs;

  const DANGLING = function (t) {
    return t.indexOf('[object Object]') >= 0 ||
           t.indexOf('[object HTML') >= 0 ||   /* DOM 元素被 String()（v5.4 transfer 实测） */
           t.indexOf('undefined') >= 0 ||
           t.indexOf('NaN') >= 0;
  };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  (async () => {
    const out = {pages: [], nav: document.querySelectorAll('.nav-item').length};
    const clearToasts = () => { document.getElementById('toasts').innerHTML = ''; };
    for (const p of App.pages) {
      clearToasts();
      window.__probeProg = p.id;      /* 进度：便于定位卡在哪个页面 */
      const before = errs.length;
      let mountErr = null;
      try { await App.navigate(p.id); } catch (e) { mountErr = e.message; }
      window.__probeProg = p.id + " (ok)";
      /* 工具类页面内嵌卡片，数据（网卡/端口等）来自后端，多等一会儿 */
      await sleep(p.id.indexOf("tool-") === 0 ? 900 : 220);
      const el = document.getElementById('page-' + p.id);
      const active = (document.querySelector('.page.active') || {}).id;
      const toasts = Array.from(document.querySelectorAll('.toast')).map(t => t.textContent);
      /* 渲染异常文本 */
      const dangling = [];
      if (el) {
        for (const node of el.querySelectorAll("div,span,td,li,p,label,option")) {
          if (node.children.length) continue;
          const t = (node.textContent || "").trim();
          if (t && DANGLING(t) && t.length < 120) {
            dangling.push(t.slice(0, 60));
            if (dangling.length >= 4) break;
          }
        }
      }
      /* 空下拉框（通常是数据没填进去）：后端可能较慢，最多再等 3 秒再判定 */
      let emptyList = [];
      for (let i = 0; i < 12; i++) {
        emptyList = Array.from(document.querySelectorAll('#page-' + p.id + ' select'))
          .filter((s) => s.offsetParent !== null && s.options.length === 0);
        if (!emptyList.length) break;
        await sleep(260);
      }
      const emptySel = emptyList.length;
      /* 横向溢出 */
      const vw = document.documentElement.clientWidth;
      let overflow = 0;
      if (el) {
        for (const node of el.querySelectorAll("*")) {
          const r = node.getBoundingClientRect();
          if (r.width > 0 && r.height > 0 && (r.right > vw + 2 || r.left < -2)) overflow++;
        }
      }
      out.pages.push({id: p.id, mounted: active === 'page-' + p.id, mountErr, toasts,
                      errs: errs.slice(before), dangling, emptySel, overflow});
    }
    clearToasts();
    out.devCards = document.querySelectorAll('#dev-list .dev-card').length;
    out.totalErrs = errs.length;
    window.__probe = JSON.stringify(out);
  })();
})();
"""


def main():
    bridge = Bridge()
    # v5.3：窗口形态与生产同源（app/core/win_spec.py）——此前这里只传 width/height，
    # 等于**从未测过生产窗口的形态**（frameless / 材质透明 / easy_drag）
    from app.core import win_spec
    window = webview.create_window(
        "UI 冒烟测试", ui_index(), js_api=bridge,
        **win_spec.window_kwargs(bridge.cfg, {}, {"hidden": False}),
    )
    bridge.attach(window)

    def probe():
        time.sleep(4)  # 等待页面加载与前端 boot 完成
        try:
            window.evaluate_js(PROBE_JS)
            for _ in range(700):  # 最多等 175 秒（44 个页面，工具页含后端慢调用）
                time.sleep(0.25)
                if window.evaluate_js("window.__probe"):
                    break
            raw = window.evaluate_js("window.__probe")
            result["probe"] = json.loads(raw) if raw else None
            if not raw:
                result["partial"] = window.evaluate_js(
                    "JSON.stringify(window.__probeErrs || [])")
                result["stalled_at"] = window.evaluate_js("window.__probeProg")
        except Exception as e:
            result["error"] = str(e)
        window.destroy()

    result = {}
    threading.Thread(target=probe, daemon=True).start()
    webview.start()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    probe_data = result.get("probe") or {}
    pages = probe_data.get("pages", [])
    bad = [p for p in pages if not p.get("mounted") or p.get("mountErr") or p.get("toasts")
           or p.get("errs") or p.get("dangling") or p.get("emptySel") or p.get("overflow")]
    ok = probe_data.get("nav", 0) >= 10 and pages and not bad
    print("页面 %d · 导航 %d · JS 报错合计 %s" % (
        len(pages), probe_data.get("nav", 0), probe_data.get("totalErrs", 0)))
    if bad:
        for p in bad:
            print("问题页面 %s：挂载=%s 异常=%s 提示=%s JS报错=%s 异常文本=%s 空下拉=%s 溢出=%s"
                  % (p["id"], p.get("mounted"), p.get("mountErr"), p.get("toasts"),
                     p.get("errs"), p.get("dangling"), p.get("emptySel"), p.get("overflow")))
    print("UI SMOKE TEST %s" % ("PASSED" if ok else "FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
