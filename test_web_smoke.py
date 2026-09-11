import base64
import http.client
import os
import shutil
import tempfile
import time
from urllib.parse import quote

from app.core import web_server

root = tempfile.mkdtemp(prefix="web_smoke_")
port = 21220
ok = False


def req(method, path, body=None, headers=None, user=None, pwd=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    h = dict(headers or {})
    if user is not None:
        token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        h["Authorization"] = "Basic " + token
    conn.request(method, path, body=body, headers=h)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


# 1) 匿名可写 + 中文文件名
logs = []
srv = web_server.WebServer()
logs = []
srv.start("127.0.0.1", port, root, "", "", True, log_callback=logs.append)
time.sleep(0.5)
try:
    st, body = req("GET", "/")
    assert st == 200, st
    up = "/hi.txt"
    st, _ = req("PUT", up, b"x")
    assert st in (201, 204), st
    st, _ = req("PROPFIND", "/", headers={"Depth": "1"})
    assert st == 207, st

    up = "/中文文件.txt"
    CH_UP = "uploaded-中文".encode("utf-8")
    st, body = req("PUT", quote(up), CH_UP, {"Content-Type": "application/octet-stream"})
    assert st in (201, 204), (st, body[:200])
    st, body = req("GET", quote(up))
    assert st == 200 and body == CH_UP, (st, body[:200])

    st, body = req("PROPFIND", "/", headers={"Depth": "1"})
    assert st == 207, (st, body[:200])
    assert "中文文件.txt".encode("utf-8") in body, body[:500]

    st, body = req("MKCOL", quote("/新目录"))
    assert st in (201, 204), (st, body[:200])
finally:
    srv.stop()
assert logs, "no logs received"
print("1) anonymous write mode OK, logs:", len(logs))

# 2) 账号模式：无凭据 401，有凭据 OK
with open(os.path.join(root, "hello.txt"), "wb") as f:
    f.write(b"webdav-content")
srv = web_server.WebServer()
srv.start("127.0.0.1", port, root, "alice", "secret", True)
time.sleep(0.5)
try:
    st, body = req("GET", "/")
    assert st == 401, st
    st, body = req("GET", "/", user="alice", pwd="secret")
    assert st == 200, st
    st, _ = req("PUT", "/alice.txt", b"data", user="alice", pwd="secret")
    assert st in (201, 204), st
finally:
    srv.stop()
print("2) account mode OK")

# 3) 只读模式：PUT 被拒
srv = web_server.WebServer()
srv.start("127.0.0.1", port, root, "bob", "pw", allow_write=False)
time.sleep(0.5)
try:
    st, _ = req("GET", "/", user="bob", pwd="pw")
    assert st == 200, st
    st, _ = req("PUT", "/nope.txt", b"x", user="bob", pwd="pw")
    assert st in (403, 405), st
finally:
    srv.stop()
print("3) readonly mode OK")

# 4) 匿名只读
srv = web_server.WebServer()
srv.start("127.0.0.1", port, root, "", "", allow_write=False)
time.sleep(0.5)
try:
    st, _ = req("PUT", "/anon-no.txt", b"x")
    assert st in (403, 405), st
finally:
    srv.stop()
print("4) anonymous readonly OK")

print("WEB SMOKE TEST PASSED")
shutil.rmtree(root, ignore_errors=True)