# -*- coding: utf-8 -*-
"""一次性探针：进入 tool-optimize 应用管理页签，点击「扫描本机应用」，抓取报错。"""

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
  const errs = [];
  window.addEventListener("error", (e) => errs.push("error: " + (e.message || e)));
  window.addEventListener("unhandledrejection", (e) =>
    errs.push("rejection: " + String((e.reason && e.reason.message) || e.reason)));
  const origErr = console.error;
  console.error = function () {
    errs.push("console.error: " + Array.prototype.map.call(arguments, (x) =>
      typeof x === "string" ? x : (x && x.message) || String(x)).join(" "));
    return origErr.apply(console, arguments);
  };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  (async () => {
    const out = {steps: [], errs};
    await App.navigate("tool-optimize");
    await sleep(2500);
    // 切到应用管理页签
    const tabs = Array.from(document.querySelectorAll('#page-tool-optimize .subnav-tab'));
    const appsTab = tabs.find(t => t.textContent.trim() === "应用管理");
    if (!appsTab) { out.steps.push("no apps tab"); window.__probe = JSON.stringify(out); return; }
    appsTab.click();
    await sleep(800);
    // 点击扫描按钮
    const btns = Array.from(document.querySelectorAll('#page-tool-optimize button'));
    const scanBtn = btns.find(b => b.textContent.indexOf("扫描本机应用") >= 0);
    if (!scanBtn) { out.steps.push("no scan btn"); window.__probe = JSON.stringify(out); return; }
    out.steps.push({btnFound: true, disabled: scanBtn.disabled,
                    cls: scanBtn.className});
    /* 诊断 1：直接调 js_api，验证方法是否存在 */
    try {
      const r1 = await App.tryCall("opt_apps_scan");
      out.steps.push({directCall: r1});
    } catch (e) {
      out.steps.push({directCallErr: String(e && e.message || e)});
    }
    await sleep(4000);
    out.steps.push({afterDirectCall: {
      rows: document.querySelectorAll('#page-tool-optimize .list-item').length,
      toasts: Array.from(document.querySelectorAll('.toast')).map(t => t.textContent.slice(0, 100)),
    }});
    /* 诊断 2：再点按钮本体 */
    scanBtn.click();
    out.steps.push("clicked scan");
    // 等待扫描完成（真机约 2-5s，保守等 30s）
    for (let i = 0; i < 60; i++) {
      await sleep(500);
      const rows = document.querySelectorAll('#page-tool-optimize .list-item').length;
      const toasts = Array.from(document.querySelectorAll('.toast')).map(t => t.textContent.slice(0, 120));
      const empty = (document.querySelector('#page-tool-optimize .empty') || {}).textContent || "";
      if (errs.length) break;
      if (rows > 0 || /失败|错误|错误|请先/.test(toasts.join(""))) {
        out.steps.push({rows, toasts, empty: empty.trim().slice(0, 80)});
        break;
      }
      if (i === 59) out.steps.push({timeout: true, rows, toasts, empty: empty.trim().slice(0, 80)});
    }
    out.errList = errs.slice();
    out.toasts = Array.from(document.querySelectorAll('.toast')).map(t => t.textContent.slice(0, 120));
    const box = document.querySelectorAll('#page-tool-optimize .list-item').length;
    out.rowsTotal = box;
    out.sample = Array.from(document.querySelectorAll('#page-tool-optimize .li-title'))
      .slice(0, 5).map(x => x.textContent.trim());
    window.__probe = JSON.stringify(out);
  })();
})();
"""


def main():
    bridge = Bridge()
    window = webview.create_window(
        "扫描探针", ui_index(), js_api=bridge, width=1280, height=860, hidden=False)
    bridge.attach(window)
    result = {}

    def probe():
        time.sleep(4)
        try:
            window.evaluate_js(PROBE_JS)
            for _ in range(300):
                time.sleep(0.25)
                if window.evaluate_js("window.__probe"):
                    break
            raw = window.evaluate_js("window.__probe")
            result["probe"] = json.loads(raw) if raw else None
        except Exception as e:
            result["error"] = str(e)
        window.destroy()

    threading.Thread(target=probe, daemon=True).start()
    webview.start()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
