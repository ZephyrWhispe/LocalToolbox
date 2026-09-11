/* App 核心：页面注册表、事件分发、Python 桥接调用。 */
(function () {
  "use strict";

  const listeners = {};

  const readyP = new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) return resolve();
    window.addEventListener("pywebviewready", () => resolve());
    const timer = setInterval(() => {
      if (window.pywebview && window.pywebview.api) { clearInterval(timer); resolve(); }
    }, 120);
    setTimeout(() => clearInterval(timer), 15000);
  });

  const App = (window.App = window.App || {});
  App.pages = App.pages || [];
  App.state = App.state || {
    cfg: null, info: null, devices: [], page: null, warnedSensitive: false,
  };

  Object.assign(App, {
    on(name, fn) {
      (listeners[name] = listeners[name] || []).push(fn);
      return () => App.off(name, fn);
    },
    off(name, fn) {
      const arr = listeners[name];
      if (arr) { const i = arr.indexOf(fn); if (i >= 0) arr.splice(i, 1); }
    },
    dispatch(name, data) {
      (listeners[name] || []).slice().forEach((fn) => {
        try { fn(data); } catch (e) { console.error("[event:" + name + "]", e); }
      });
    },
    registerPage(page) { App.pages.push(page); },

    /** 调用 Python 桥接方法；失败（ok:false / 异常）时 reject Error。 */
    async call(name, ...args) {
      await readyP;
      const r = await window.pywebview.api[name](...args);
      if (!r || r.ok === false) throw new Error((r && r.err) || "调用 " + name + " 失败");
      return r.data;
    },

    /** 调用但把失败转成 {ok:false, err}，不抛异常。 */
    async tryCall(name, ...args) {
      try { return { ok: true, data: await App.call(name, ...args) }; }
      catch (e) { return { ok: false, err: e.message }; }
    },

    /** 调用并把失败转成错误 toast 的便捷封装。 */
    async guardedCall(name, ...args) {
      try { return await App.call(name, ...args); }
      catch (e) { App.toast(e.message, "error"); throw e; }
    },
  });

  /* 接管 push 通道：回放加载前缓冲的事件 */
  const queued = window.__q || [];
  window.__push = (m) => App.dispatch(m.event, m.data);
  document.addEventListener("DOMContentLoaded", () => {
    queued.forEach((m) => App.dispatch(m.event, m.data));
  });
})();
