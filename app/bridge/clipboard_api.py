"""剪贴板同步页桥接：启动/停止、历史、手动设备、脱敏开关。"""

import os

from ..core import firewall
from ..core import screenshot
from ..core.clipboard_store import ClipboardStore, clip_store_key
from ..core.clipboard_sync import SYNC_PORT_DEFAULT, MAX_CLIP_BYTES, ClipboardSync
from ..core.config import DATA_HOME
from ..core.discovery import DEFAULT_DISCOVERY_PORT

FW_PREFIX = "ClipHelper-"


def _preview(text, limit=60):
    one_line = text.replace("\r", "").replace("\n", "⏎")
    return one_line if len(one_line) <= limit else one_line[:limit] + "…"


def _serialize_entry(entry):
    """历史条目 → 前端可消费结构。图片条目附带缩略图 data URL 与落盘路径。"""
    d = {
        "ts": entry["ts"],
        "device": entry["device"],
        "hash": entry["hash"],
        "kind": entry.get("kind", "text"),
        "remote": entry["remote"],
    }
    if d["kind"] == "image":
        d["img_path"] = entry.get("img_path") or ""
        if d["img_path"] and os.path.isfile(d["img_path"]):
            try:
                with open(d["img_path"], "rb") as f:
                    d["img"] = screenshot.thumbnail_data_url(f.read(), 160)
            except Exception:
                pass
    elif d["kind"] == "file":
        d["file_path"] = entry.get("file_path") or ""
        d["file_name"] = entry.get("file_name") or os.path.basename(d["file_path"] or "")
        d["file_size"] = entry.get("file_size") or 0
        if d["file_path"] and not os.path.isfile(d["file_path"]):
            d["missing"] = True
    else:
        d["text"] = entry["text"]
        d["enc_lost"] = bool(entry.get("enc_lost"))
        d["preview"] = _preview(entry["text"])
    return d


class ClipboardApi:
    def _init_clipboard(self):
        enc_key = clip_store_key(DATA_HOME) if self.cfg.get("clip_encrypt", False) else None
        self.clip = ClipboardSync(
            history_limit=int(self.cfg.get("history_limit", 100)),
            discovery=self.discovery,
            name=getattr(self, "_custom_name", None),  # v3.5e：自定义设备名
            log=lambda m: self.emit("clip_log", str(m)),
            store=ClipboardStore(
                os.path.join(DATA_HOME, "clipboard_history.db"),
                limit=int(self.cfg.get("history_limit", 100)),
                days=int(self.cfg.get("clip_retain_days", 0) or 0),  # v3.5f
                key=enc_key,
            ),
        )

        def _on_history(entry):
            self.emit("clip_history", _serialize_entry(entry))

        self.clip.on_history = _on_history

    def _serialize_history(self):
        return [_serialize_entry(e) for e in self.clip.history]

    # -- API -----------------------------------------------------------
    def clip_get_state(self):
        return {
            "ok": True,
            "data": {
                "running": self.clip.running,
                "send": self.clip.send_enabled,
                "recv": self.clip.recv_enabled,
                "device_name": self.clip.device_name,
                "sync_port": self.clip.sync_port,
                "discovery_port": DEFAULT_DISCOVERY_PORT,
                "history": self._serialize_history(),
                "devices": self.clip.devices(),
                "desensitize": bool(self.cfg.get("desensitize", True)),
            },
        }

    def clip_start(self, send=True, recv=True, fw=True):
        try:
            if fw:
                firewall.add_ports(
                    FW_PREFIX,
                    [(self.clip.sync_port, "TCP"), (DEFAULT_DISCOVERY_PORT, "UDP")],
                )
            self.clip.start(send_enabled=bool(send), recv_enabled=bool(recv))
            self.emit(
                "clip_state",
                {"running": True, "send": bool(send), "recv": bool(recv)},
            )
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def clip_stop(self):
        try:
            self.clip.stop()
            self.emit("clip_state", {"running": False})
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def clip_add_manual(self, ip, port=None):
        try:
            ip = str(ip or "").strip()
            if not self._valid_ipv4(ip):
                return {"ok": False, "err": "请输入合法的 IPv4 地址。"}
            port = int(port or SYNC_PORT_DEFAULT)
            if not (1 <= port <= 65535):
                return {"ok": False, "err": "端口需在 1-65535 之间。"}
            self.clip.add_manual_peer(ip, port)
            return {"ok": True, "data": {"ip": ip, "port": port}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    @staticmethod
    def _valid_ipv4(ip):
        segs = ip.split(".")
        if len(segs) != 4:
            return False
        for s in segs:
            if not s.isdigit() or (len(s) > 1 and s[0] == "0"):
                return False
            if not (0 <= int(s) <= 255):
                return False
        return True

    def _find_entry(self, h):
        for e in self.clip.history:
            if e["hash"] == h:
                return e
        return None

    def clip_copy(self, h):
        entry = self._find_entry(str(h))
        if not entry:
            return {"ok": False, "err": "该条目已被删除或清空"}
        kind = entry.get("kind")
        if kind == "image":
            img_path = entry.get("img_path") or ""
            if not img_path or not os.path.isfile(img_path):
                return {"ok": False, "err": "图片文件不存在（可能已被移动或删除）"}
            try:
                with open(img_path, "rb") as f:
                    png = f.read()
            except OSError as e:
                return {"ok": False, "err": "读取图片失败：%s" % e}
            self.clip.copy_image_to_clipboard(png)
            self.emit("clip_log", "已复制选中图片到剪贴板。")
            return {"ok": True, "data": True}
        if kind == "file":
            file_path = entry.get("file_path") or ""
            if not file_path or not os.path.isfile(file_path):
                return {"ok": False, "err": "文件不存在（可能已被移动或删除）"}
            self.clip.copy_file_to_clipboard([file_path])
            self.emit("clip_log", "已复制选中文件到剪贴板。")
            return {"ok": True, "data": True}
        self.clip.copy_to_clipboard(entry["text"])
        self.emit("clip_log", "已复制选中历史到剪贴板。")
        return {"ok": True, "data": True}

    def clip_copy_path(self, img_path):
        """把历史图片文件内容重新写入剪贴板（前端缩略图直接回贴）。"""
        try:
            img_path = str(img_path or "")
            if not os.path.isfile(img_path):
                return {"ok": False, "err": "图片文件不存在"}
            with open(img_path, "rb") as f:
                png = f.read()
            self.clip.copy_image_to_clipboard(png)
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def clip_delete(self, h):
        self.clip.delete_entry(str(h))
        return {"ok": True, "data": True}

    def clip_reveal(self, file_path):
        """在资源管理器中打开文件所在目录（对齐 shot_reveal）。"""
        try:
            file_path = str(file_path or "")
            if not os.path.isfile(file_path):
                return {"ok": False, "err": "文件不存在（可能已被移动或删除）"}
            import subprocess
            subprocess.Popen(["explorer", "/select,", os.path.abspath(file_path)])
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def clip_clear(self):
        self.clip.clear_history()
        return {"ok": True, "data": True}

    def clip_copy_file(self, path):
        """右键菜单 --clip：读取文本文件内容进剪贴板（≤2MB），同步开启即推送。"""
        path = str(path)
        try:
            if not os.path.isfile(path):
                raise ValueError("文件不存在：%s" % path)
            size = os.path.getsize(path)
            if size > MAX_CLIP_BYTES:
                raise ValueError("文件超过 2MB（%d 字节），剪贴板同步不支持。" % size)
            with open(path, "rb") as f:
                raw = f.read()
            text = None
            for enc in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
                try:
                    text = raw.decode(enc)
                    break
                except (UnicodeDecodeError, ValueError):
                    continue
            if text is None:
                raise ValueError("该文件不是文本文件（或编码无法识别）。")
            self.clip.copy_to_clipboard(text)
            self.emit(
                "clip_log",
                "已复制「%s」的内容到剪贴板（%d 字符）。" % (os.path.basename(path), len(text)),
            )
            return {"ok": True, "data": {"chars": len(text)}}
        except Exception as e:
            return {"ok": False, "err": str(e)}
