# -*- coding: utf-8 -*-
"""本地 IPC 只读接口（v5.4 二期，UniGetUI IPC 复刻要点之子集）。

- 默认关闭；开启后仅监听 127.0.0.1 回环地址（不暴露局域网、不进防火墙）；
- 只读端点：/api/v1/ping、/api/v1/apps（已装应用缓存）、/api/v1/removed（卸载留痕）；
- 标准库 http.server 实现，无新增依赖；任何本地进程均可读取（仅含应用清单
  类信息，不含敏感数据），风险已在设置项文案中明示。

架构参照 UniGetUI（MIT）IPC 设计思想（本地优先、TCP 仅回环），独立实现。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import logger as applog

log = applog.get_logger("ipc")

DEFAULT_PORT = 17258
_server = None
_lock = threading.Lock()


def _apps_payload():
    # 延迟导入避免桥接层与核心层的循环依赖
    from . import appscan
    entries = appscan.cache_get()
    if entries is None:
        return {"cached": False, "items": []}
    return {"cached": True,
            "items": [e.to_dict() for e in entries]}


def make_handler():
    class _Handler(BaseHTTPRequestHandler):
        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type",
                             "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802（http.server 约定）
            path = self.path.split("?", 1)[0]
            if path == "/api/v1/ping":
                self._json({"ok": True, "app": "LocalToolbox"})
            elif path == "/api/v1/apps":
                self._json({"ok": True, **_apps_payload()})
            elif path == "/api/v1/removed":
                from . import appscan
                self._json({"ok": True,
                            "removed": appscan.removed_list()})
            else:
                self._json({"ok": False,
                            "err": "未知端点（可用：/api/v1/ping、/apps、/removed）"},
                           code=404)

        def do_POST(self):  # noqa: N802
            self._json({"ok": False, "err": "IPC 接口为只读"}, code=405)

        def log_message(self, *a):  # 静默（避免刷日志）
            pass

    return _Handler


def start(port=None):
    """启动 IPC 服务（已运行则忽略）。返回实际端口；失败抛 OSError。"""
    global _server
    with _lock:
        if _server is not None:
            return _server.server_address[1]
        srv = ThreadingHTTPServer(
            ("127.0.0.1", int(port or DEFAULT_PORT)), make_handler())
        srv.daemon_threads = True
        t = threading.Thread(target=srv.serve_forever, daemon=True,
                             name="ipc-server")
        t.start()
        _server = srv
        log.info("本地 IPC 接口已启动：http://127.0.0.1:%d/api/v1/ping",
                 srv.server_address[1])
        return srv.server_address[1]


def stop():
    global _server
    with _lock:
        srv, _server = _server, None
    if srv is not None:
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:
            pass
        log.info("本地 IPC 接口已停止")


def running():
    return _server is not None
