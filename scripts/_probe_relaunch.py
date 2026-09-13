# -*- coding: utf-8 -*-
"""一次性探针：验证点击右下角「提权重启」不再弹出确认框（mock 掉真实提权）。"""

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
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  (async () => {
    const out = {};
    await App.navigate("settings");
    await sleep(2000);
    // mock 提权调用（不真触发 UAC）
    const calls = [];
    const origTry = App.tryCall;
    App.tryCall = function (name) {
      calls.push(name);
      if (name === "network_relaunch_admin") {
        return Promise.resolve({ok: true, data: "mock-relaunch"});
      }
      return origTry.apply(App, arguments);
    };
    // 点击右下角提权按钮
    const btn = document.querySelector("#status-right .admin-relaunch");
    if (!btn) { out.err = "no relaunch btn"; window.__probe = JSON.stringify(out); return; }
    btn.click();
    await sleep(600);
    out.modalShown = !!document.querySelector("#modal-root .modal");
    out.calls = calls;
    out.btnText = btn.textContent.trim();
    window.__probe = JSON.stringify(out);
  })();
})();
"""


def main():
    bridge = Bridge()
    window = webview.create_window(
        "提权弹窗探针", ui_index(), js_api=bridge, width=1280, height=860, hidden=False)
    bridge.attach(window)
    result = {}

    def probe():
        time.sleep(4)
        try:
            window.evaluate_js(PROBE_JS)
            for _ in range(120):
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
