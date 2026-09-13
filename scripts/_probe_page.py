# -*- coding: utf-8 -*-
"""针对性探针：进入指定页面（默认 tool-optimize），抓取内容/错误/卡片匹配情况。

用法：python scripts/_probe_page.py tool-optimize [tool-unattend ...]
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

PAGES = sys.argv[1:] or ["tool-optimize"]

PROBE_JS_TEMPLATE = """
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
  window.__probeErrs = errs;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const pages = __PAGES__;

  (async () => {
    const out = {pages: []};
    const findMismatch = (root, cap) => {
      const res = [];
      for (const node of root.querySelectorAll('.card *')) {
        if (!node.offsetParent && node.tagName !== 'INPUT') continue;
        const card = node.closest('.card');
        if (!card) continue;
        const r = node.getBoundingClientRect();
        const c = card.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        if (r.right > c.right + 4 || r.left < c.left - 4) {
          res.push({
            tag: node.tagName.toLowerCase(),
            cls: String(node.className).slice(0, 60),
            text: (node.textContent || "").trim().slice(0, 30),
            overR: Math.round(r.right - c.right),
            overL: Math.round(c.left - r.left),
          });
          if (res.length >= cap) break;
        }
      }
      return res;
    };

    for (const id of pages) {
      const before = errs.length;
      let mountErr = null;
      try { await App.navigate(id); } catch (e) { mountErr = e.message; }
      await sleep(3000);
      let el = document.getElementById('page-' + id);
      if (!el) {
        /* 页面元素不存在（挂载失败等）：记录后跳过，避免探针自身中断 */
        out.pages.push({ id, mountErr, missing: true, errs: errs.slice(before) });
        continue;
      }
      /* 逐个点击页内 subnav 页签：每个页签测 内容量 + 卡片越界 */
      const tabsInfo = [];
      let mismatches = [];
      const tabs = Array.from(el.querySelectorAll('.subnav-tab'));
      for (let ti = 0; ti < tabs.length; ti++) {
        tabs[ti].click();
        await sleep(700);
        const pane = el.querySelector('.subnav-pane.active');
        tabsInfo.push({
          tab: tabs[ti].textContent.trim(),
          textLen: pane ? (pane.textContent || "").trim().length : 0,
        });
        mismatches = mismatches.concat(findMismatch(el, 6));
        document.getElementById('toasts').innerHTML = "";
      }
      if (tabs.length) { tabs[0].click(); await sleep(500); }
      await sleep(1200);
      el = document.getElementById('page-' + id);
      const info = {
        id, mountErr,
        active: (document.querySelector('.page.active') || {}).id,
        tabs: tabsInfo,
        textLen: el ? (el.textContent || "").trim().length : 0,
        cards: el ? el.querySelectorAll('.card').length : 0,
        secs: el ? el.querySelectorAll('.sec').length : 0,
        secHeads: el ? Array.from(el.querySelectorAll('.sec-head')).map(s => s.textContent.trim()) : [],
        empties: el ? Array.from(el.querySelectorAll('.empty')).map(e => (e.textContent || "").trim().slice(0, 60)) : [],
        mismatches: mismatches.slice(0, 12),
        toasts: Array.from(document.querySelectorAll('.toast')).map(t => t.textContent.slice(0, 80)),
        errs: errs.slice(before),
        snippet: el ? (el.textContent || "").trim().slice(0, 300) : "",
      };
      out.pages.push(info);
      document.getElementById('toasts').innerHTML = "";
    }
    out.totalErrs = errs.length;
    /* 右下角统一管理员入口（v5.4）：芯片存在性与状态 */
    const chip = document.querySelector("#status-right .admin-chip");
    out.adminChip = chip ? {
      present: true,
      state: (chip.querySelector(".tag") || {}).textContent || "",
      hasRelaunchBtn: !!Array.from(chip.querySelectorAll("button"))
        .find((b) => (b.textContent || "").indexOf("提权重启") >= 0),
      statusRight: (document.getElementById("status-right") || {}).textContent || "",
    } : { present: false };
    window.__probe = JSON.stringify(out);
  })();
})();
"""

probe_js = PROBE_JS_TEMPLATE.replace("__PAGES__", json.dumps(PAGES))


def main():
    bridge = Bridge()
    window = webview.create_window(
        "页面探针", ui_index(), js_api=bridge, width=1280, height=860, hidden=False)
    bridge.attach(window)
    result = {}

    def probe():
        time.sleep(4)
        try:
            window.evaluate_js(probe_js)
            for _ in range(600):
                time.sleep(0.25)
                if window.evaluate_js("window.__probe"):
                    break
            raw = window.evaluate_js("window.__probe")
            result["probe"] = json.loads(raw) if raw else None
            if not raw:
                result["stalled"] = window.evaluate_js(
                    "JSON.stringify(window.__probeErrs || [])")
        except Exception as e:
            result["error"] = str(e)
        window.destroy()

    threading.Thread(target=probe, daemon=True).start()
    webview.start()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
