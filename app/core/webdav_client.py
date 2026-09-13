"""纯标准库 WebDAV 客户端：浏览 / 上传 / 下载 / 重命名 / 删除。

对 OpenList 的 /dav/ 接口工作；同时兼容任意标准 WebDAV 服务端。
错误统一为 WebDavError（携带 HTTP 状态码与中文消息），由桥接层转为 {ok:False, err}。
"""

import base64
import http.client
import os
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urlsplit

CHUNK = 64 * 1024

# 错误码 -> 中文提示
_STATUS_MSG = {
    400: "请求无效（400）",
    401: "认证失败：请检查网盘驱动凭据（401）",
    403: "无权限执行该操作（403）",
    404: "路径不存在或已被删除（404）",
    405: "目标位置不允许该方法（405）",
    409: "目标已存在或冲突（409）",
    412: "操作前提条件失败（412）",
    423: "目标被锁定，请稍后重试（423）",
    507: "网盘空间不足或配额超限（507）",
}

_D = "DAV:"


def _prop_body(extra=False):
    props = [
        "<d:displayname/>",
        "<d:getcontentlength/>",
        "<d:getlastmodified/>",
        "<d:getcontenttype/>",
        "<d:resourcetype/>",
    ]
    if extra:
        props.extend(
            [
                "<d:quota-used-bytes/>",
                "<d:quota-available-bytes/>",
            ]
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<d:propfind xmlns:d="DAV:">'
        "<d:prop>%s</d:prop></d:propfind>" % "".join(props)
    ).encode("utf-8")


class WebDavError(RuntimeError):
    def __init__(self, status, detail=""):
        self.status = status
        self.detail = detail or ""
        msg = _STATUS_MSG.get(status, "操作失败（HTTP %s）" % status)
        if self.detail:
            msg += "：%s" % self.detail
        super().__init__(msg)


def norm_segments(path):
    """把可读路径规范化为路径段列表；拒绝 ``..`` 逃逸。"""
    if not path:
        return []
    segs = []
    for raw in str(path).replace("\\", "/").split("/"):
        seg = unquote(raw).strip()
        if not seg or seg == ".":
            continue
        if seg == "..":
            raise WebDavError(400, "非法路径（不允许 ..）")
        segs.append(seg)
    return segs


def check_local_safe(dest):
    """本地路径合法性：非空、绝对、不指向已存在目录。"""
    if not dest or not os.path.isabs(dest):
        raise WebDavError(400, "本地路径必须为绝对路径")


class WebDavClient:
    """base_url 形如 http://127.0.0.1:15244/dav"""

    def __init__(self, base_url, user="", password="", timeout=10):
        parsed = urlsplit(str(base_url))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("无效的 WebDAV 地址：%s" % base_url)
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        if parsed.port:
            self.port = parsed.port
        elif self.scheme == "https":
            self.port = 443
        else:
            self.port = 80
        self.base_path = (parsed.path or "").rstrip("/")
        self.timeout = timeout
        self.headers = {"User-Agent": "LocalToolbox/3.0 WebDAV"}
        if user or password:
            raw = "%s:%s" % (user, password)
            self.headers["Authorization"] = "Basic " + base64.b64encode(
                raw.encode("utf-8")
            ).decode("ascii")

    # -- URL 组装 ----------------------------------------------------------
    def _url(self, segments, is_dir=False):
        path = self.base_path
        for s in segments:
            path += "/" + quote(str(s), safe="")
        if not path:
            path = "/"
        elif is_dir:
            path += "/"
        return path

    def _abs_url(self, segments, is_dir=False):
        return "%s://%s:%d%s" % (
            self.scheme,
            self.host,
            self.port,
            self._url(segments, is_dir),
        )

    def _conn(self):
        if self.scheme == "https":
            return http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout)
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def _request(self, method, url, body=None, headers=None):
        hdrs = dict(self.headers)
        if headers:
            hdrs.update(headers)
        conn = self._conn()
        try:
            conn.request(method, url, body=body, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            status = resp.status
        except (http.client.HTTPException, OSError) as e:
            raise WebDavError(0, "网络错误：%s" % e)
        finally:
            conn.close()
        if status not in (200, 201, 204, 207):
            raise WebDavError(status)
        return status, data

    # -- 目录浏览 ----------------------------------------------------------
    def listdir(self, path, extra=False):
        segs = norm_segments(path)
        url = self._url(segs, is_dir=True)
        status, data = self._request(
            "PROPFIND",
            url,
            body=_prop_body(extra),
            headers={"Depth": "1", "Content-Type": "application/xml"},
        )
        self_url = self._url(segs)  # 不含尾部斜杠，用于剔除自身
        entries, used, avail = [], None, None
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            raise WebDavError(0, "服务器返回了无法解析的列表（%s）" % e)
        for resp_el in root.findall("{%s}response" % _D):
            href_el = resp_el.find("{%s}href" % _D)
            if href_el is None or not href_el.text:
                continue
            href = unquote(href_el.text.strip())
            is_self = href.rstrip("/") == self_url.rstrip("/")
            prop = resp_el.find("{%s}propstat" % _D)
            if prop is None:
                prop = resp_el.find("{%s}prop" % _D)
                props = resp_el
            else:
                props = prop
            if is_self:
                # 自身的配额信息
                u = _first_text(props, "quota-used-bytes")
                a = _first_text(props, "quota-available-bytes")
                used = int(u) if u else None
                avail = int(a) if a else None
                continue
            name = href.rstrip("/").rsplit("/", 1)[-1] if href.strip("/") else ""
            if not name:
                continue
            rtype = props.find(".//{DAV:}collection")
            is_dir = rtype is not None
            size = _first_text(props, "getcontentlength")
            mtime = _first_text(props, "getlastmodified")
            entries.append(
                {
                    "name": name,
                    "path": "/" + "/".join([*segs, name]) if segs else "/" + name,
                    "is_dir": is_dir,
                    "size": int(size) if size and size.isdigit() else 0,
                    "mtime": mtime or "",
                }
            )
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        return {"entries": entries, "used": used, "avail": avail}

    # -- 基本操作 ----------------------------------------------------------
    def mkdir(self, path):
        segs = norm_segments(path)
        if not segs:
            raise WebDavError(400, "不能对根目录执行新建")
        self._request("MKCOL", self._url(segs, is_dir=True))
        return True

    def delete(self, path):
        segs = norm_segments(path)
        if not segs:
            raise WebDavError(400, "不能删除根目录")
        self._request("DELETE", self._url(segs))
        return True

    def rename(self, src, dst):
        s_segs, d_segs = norm_segments(src), norm_segments(dst)
        if not s_segs or not d_segs:
            raise WebDavError(400, "重命名目标无效")
        dest_url = self._abs_url(d_segs)
        conn = self._conn()
        try:
            hdrs = dict(self.headers)
            hdrs["Destination"] = dest_url
            conn.request(
                "MOVE",
                self._url(s_segs),
                headers=hdrs,
            )
            resp = conn.getresponse()
            resp.read()
            status = resp.status
        except (http.client.HTTPException, OSError) as e:
            raise WebDavError(0, "网络错误：%s" % e)
        finally:
            conn.close()
        if status not in (200, 201, 204):
            raise WebDavError(status)
        return True

    # -- 上传 / 下载 ----------------------------------------------------------
    def upload(self, local_path, remote_dir=None, remote_path=None, progress_cb=None):
        """PUT 流式上传；local_path 与 remote_dir 均需传入，或直接给 remote_path。"""
        if not os.path.isfile(local_path):
            raise WebDavError(400, "本地文件不存在：%s" % local_path)
        if remote_path:
            segs = norm_segments(remote_path)
        else:
            d_segs = norm_segments(remote_dir)
            segs = d_segs + [os.path.basename(local_path)]
        if not segs:
            raise WebDavError(400, "上传目标无效")
        url = self._url(segs, is_dir=True)
        total = os.path.getsize(local_path)
        content_type = "application/octet-stream"
        done = 0
        conn = self._conn()
        try:
            conn.putrequest("PUT", url, skip_accept_encoding=True)
            for k, v in self.headers.items():
                conn.putheader(k, v)
            conn.putheader("Content-Type", content_type)
            conn.putheader("Content-Length", str(total))
            conn.endheaders()
            with open(local_path, "rb") as f:
                while True:
                    buf = f.read(CHUNK)
                    if not buf:
                        break
                    conn.send(buf)
                    done += len(buf)
                    if progress_cb:
                        progress_cb(done, total)
            resp = conn.getresponse()
            resp.read()
            status = resp.status
        except (http.client.HTTPException, OSError) as e:
            raise WebDavError(0, "上传中断：%s" % e)
        finally:
            conn.close()
        if status not in (200, 201, 204):
            raise WebDavError(status)
        return True

    def download(self, remote_path, local_path, progress_cb=None):
        """GET 流式下载；local_path 为绝对路径。"""
        segs = norm_segments(remote_path)
        if not segs:
            raise WebDavError(400, "不能下载根目录（请打开某个文件）")
        check_local_safe(local_path)
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        conn = self._conn()
        try:
            conn.request("GET", self._url(segs), headers=dict(self.headers))
            resp = conn.getresponse()
            status = resp.status
            if status not in (200, 206):
                resp.read()
                raise WebDavError(status)
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            tmp = local_path + ".part"
            with open(tmp, "wb") as f:
                while True:
                    buf = resp.read(CHUNK)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    if progress_cb:
                        progress_cb(done, total)
            if os.path.isfile(local_path):
                os.remove(local_path)  # 覆盖，先在临时文件完成后替换
            os.replace(tmp, local_path)
        except WebDavError:
            # v5.1c：失败清理 .part 残片，避免反复失败在临时目录堆积
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise
        except (http.client.HTTPException, OSError) as e:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise WebDavError(0, "下载中断：%s" % e)
        finally:
            conn.close()
        return local_path

    def free_space(self, root="/"):
        info = self.listdir(root, extra=True)
        return {"used": info.get("used"), "avail": info.get("avail")}


def _first_text(container, tag):
    el = container.find(".//{%s}%s" % ("DAV:", tag))
    return el.text if el is not None and el.text else None