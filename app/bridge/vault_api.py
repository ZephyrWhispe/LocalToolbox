"""密码库页桥接：建库/解锁/条目 CRUD/复制密码/生成器/自动锁定/导入导出。

安全语义：未解锁时 vault_entries/vault_entry 一律拒绝；vault_entries 对
前端脱敏（不含 password 明文，仅长度/是否非空）；复制密码后按
vault_clip_clear_sec 定时清剪贴板（清除前校验剪贴板仍为本密码）。
导入导出（v5.1）：多来源解析（浏览器/密码管理器 CSV·JSON）+ 去重校验 +
完整报告；导出支持明文 CSV/JSON 与独立口令加密容器。
"""

import os
import threading
import time

import webview

from ..core.config import DATA_HOME
from ..core.vault import Vault, VaultError, generate_password


class VaultApi:
    def _init_vault(self):
        import os
        self._vault = Vault(os.path.join(DATA_HOME, "vault.dat"))
        threading.Thread(target=self._vault_autolock_loop, daemon=True,
                         name="vault-autolock").start()

    def _vault_autolock_loop(self):
        """空闲自动锁定：每 20s 检查，超时（cfg vault_autolock_min 分钟，0=不锁）清内存态。"""
        while True:
            time.sleep(20)
            try:
                minutes = int(self.cfg.get("vault_autolock_min", 15) or 0)
                if minutes <= 0 or not self._vault.unlocked:
                    continue
                if time.time() - self._vault.last_used >= minutes * 60:
                    self._vault.lock()
                    self.emit_log("密码库已自动锁定（空闲 %d 分钟）。" % minutes)
            except Exception:
                continue

    # -- API ---------------------------------------------------------------
    def vault_status(self):
        return {"ok": True, "data": self._vault.status()}

    def vault_setup(self, password):
        try:
            self._vault.create(password)
            self.emit_log("密码库已创建。")
            return {"ok": True, "data": self._vault.status()}
        except VaultError as e:
            return {"ok": False, "err": str(e)}

    def vault_unlock(self, password):
        try:
            self._vault.unlock(password)
            return {"ok": True, "data": self._vault.status()}
        except VaultError as e:
            return {"ok": False, "err": str(e)}

    def vault_lock(self):
        self._vault.lock()
        return {"ok": True, "data": self._vault.status()}

    def vault_change_password(self, old, new):
        try:
            self._vault.change_password(old, new)
            self.emit_log("密码库主口令已更改。")
            return {"ok": True, "data": self._vault.status()}
        except VaultError as e:
            return {"ok": False, "err": str(e)}

    def vault_entries(self, query=""):
        """脱敏列表：不含 password 明文（仅 has_pwd），未解锁拒绝。"""
        try:
            q = str(query or "").strip().lower()
            out = []
            for e in self._vault.entries():
                if q and q not in ((e.get("title") or "") + " "
                                   + (e.get("username") or "") + " "
                                   + (e.get("url") or "")).lower():
                    continue
                out.append({
                    "id": e["id"], "title": e["title"],
                    "username": e["username"], "url": e["url"],
                    "group": e["group"], "updated": e["updated"],
                    "has_pwd": bool(e.get("password")),
                })
            return {"ok": True, "data": {
                "entries": out, "groups": self._vault.groups(),
                "autolock": int(self.cfg.get("vault_autolock_min", 15) or 0),
            }}
        except VaultError as e:
            return {"ok": False, "err": str(e)}

    def vault_entry(self, eid):
        """单条全文（解锁才可；用于查看/编辑表单回填）。"""
        try:
            e = self._vault.entry(int(eid or 0))
            if not e:
                return {"ok": False, "err": "条目不存在"}
            return {"ok": True, "data": e}
        except VaultError as ex:
            return {"ok": False, "err": str(ex)}

    def vault_save(self, eid, title, username, password, url, note, group):
        try:
            mid = self._vault.upsert({
                "id": eid or 0, "title": title, "username": username,
                "password": password, "url": url, "note": note, "group": group,
            })
            return {"ok": True, "data": {"id": mid}}
        except VaultError as e:
            return {"ok": False, "err": str(e)}

    def vault_delete(self, eid):
        try:
            return {"ok": True, "data": self._vault.delete(int(eid or 0))}
        except VaultError as e:
            return {"ok": False, "err": str(e)}

    def vault_groups(self):
        try:
            return {"ok": True, "data": self._vault.groups()}
        except VaultError as e:
            return {"ok": False, "err": str(e)}

    def vault_copy_password(self, eid):
        """复制密码到剪贴板 + 定时清除（清除前校验剪贴板仍为本密码）。"""
        try:
            e = self._vault.entry(int(eid or 0))
            if not e:
                return {"ok": False, "err": "条目不存在"}
            pwd = e.get("password") or ""
            if not pwd:
                return {"ok": False, "err": "该条目没有密码"}
            import pyperclip
            pyperclip.copy(pwd)
            sec = int(self.cfg.get("vault_clip_clear_sec", 30) or 0)
            if sec > 0:
                threading.Timer(sec, self._vault_clip_clear, args=(pwd,),
                                daemon=True).start()
            self.emit_log("已复制条目「%s」的密码%s。" % (
                e.get("title") or "", "，%ds 后自动清除剪贴板" % sec if sec else ""))
            return {"ok": True, "data": {"clear_sec": sec}}
        except VaultError as ex:
            return {"ok": False, "err": str(ex)}

    def _vault_clip_clear(self, pwd):
        """定时清剪贴板：仅当剪贴板仍是本密码时清（避免误清用户后续复制）。"""
        try:
            import pyperclip
            if (pyperclip.paste() or "") == pwd:
                pyperclip.copy("")
        except Exception:
            pass

    def vault_copy_username(self, eid):
        try:
            e = self._vault.entry(int(eid or 0))
            if not e:
                return {"ok": False, "err": "条目不存在"}
            import pyperclip
            pyperclip.copy(e.get("username") or "")
            return {"ok": True}
        except VaultError as ex:
            return {"ok": False, "err": str(ex)}

    def vault_generate(self, length=16, symbols=True):
        return {"ok": True, "data": {"password": generate_password(
            length, bool(symbols))}}

    # -- 导入 / 导出（v5.1） -----------------------------------------------
    def vault_import_preview(self, path=None):
        """选择导入文件并解析预览（格式 / 条数 / 错误摘要）。path 为空弹对话框。"""
        try:
            from ..core import vault_io
            if not path:
                if self._window is None:
                    return {"ok": False, "err": "窗口尚未就绪。"}
                result = self._window.create_file_dialog(
                    webview.OPEN_DIALOG, directory="",
                    file_types=("密码文件 (*.csv;*.json;*.lvt)",),
                )
                paths = [str(p) for p in (result or []) if p]
                if not paths:
                    return {"ok": False, "err": "未选择文件。"}
                path = paths[0]
            path = str(path)
            if not os.path.isfile(path):
                return {"ok": False, "err": "文件不存在：%s" % path}
            if vault_io.sniff(path) == "encrypted":
                return {"ok": True, "data": {"path": path, "format": "encrypted",
                                             "need_password": True}}
            rep = vault_io.parse_file(path)
            return {"ok": True, "data": {
                "path": path,
                "format": rep["format"],
                "count": len(rep["entries"]),
                "error_count": len(rep["errors"]),
                "errors": rep["errors"][:10],
                "sample": [e["title"] for e in rep["entries"][:5]],
            }}
        except Exception as e:
            return {"ok": False, "err": "预览失败：%s" % e}

    def vault_import_exec(self, path, mode="merge", password=None):
        """执行导入：解析 → 去重/校验 → 批量写库（仅落盘一次）→ 完整报告。

        mode: merge=合并（同 标题+用户名+密码 跳过）/ replace=清空后导入。
        """
        try:
            from ..core import vault_io
            if not self._vault.unlocked:
                return {"ok": False, "err": "请先解锁密码库"}
            path = str(path or "")
            if not os.path.isfile(path):
                return {"ok": False, "err": "文件不存在：%s" % path}
            mode = str(mode or "merge")
            if mode not in ("merge", "replace"):
                return {"ok": False, "err": "mode 须为 merge/replace"}
            fmt = vault_io.sniff(path)
            if fmt == "encrypted":
                with open(path, "r", encoding="utf-8") as f:
                    line = f.read().strip()
                rep = vault_io.parse_encrypted_line(line, password)
            else:
                rep = vault_io.parse_file(path)
            errors = list(rep["errors"])
            report = {
                "format": rep["format"], "mode": mode,
                "total_rows": rep["total_rows"],
                "imported": 0, "skipped_dup": 0, "skipped_invalid": len(errors),
                "errors": errors[:20],
            }
            if mode == "replace":
                report["imported"] = self._vault.replace_entries(rep["entries"])
            else:
                existing = {(e.get("title") or "", e.get("username") or "",
                             e.get("password") or "")
                            for e in self._vault.entries()}
                fresh = []
                for e in rep["entries"]:
                    key = (e["title"], e["username"], e["password"])
                    if key in existing:
                        report["skipped_dup"] += 1
                        continue
                    existing.add(key)
                    fresh.append(e)
                report["imported"] = self._vault.add_many(fresh)
            self.emit_log("密码导入完成（%s/%s）：导入 %d，重复跳过 %d，无效跳过 %d。"
                          % (report["format"], mode, report["imported"],
                             report["skipped_dup"], report["skipped_invalid"]))
            return {"ok": True, "data": report}
        except Exception as e:
            return {"ok": False, "err": "导入失败：%s" % e}

    def vault_export(self, fmt="csv", password=None, path=None):
        """导出：fmt=csv|json|enc（独立口令加密容器）。path 为空弹保存对话框。"""
        try:
            from ..core import vault_io
            if not self._vault.unlocked:
                return {"ok": False, "err": "请先解锁密码库"}
            fmt = str(fmt or "csv")
            if fmt not in ("csv", "json", "enc"):
                return {"ok": False, "err": "不支持的导出格式"}
            if fmt == "enc" and not str(password or ""):
                return {"ok": False, "err": "加密导出需要设置导出口令"}
            entries = self._vault.entries()
            if not entries:
                return {"ok": False, "err": "密码库为空，没有可导出的条目"}
            ext = {"csv": "csv", "json": "json", "enc": "lvt"}[fmt]
            if not path:
                if self._window is None:
                    return {"ok": False, "err": "窗口尚未就绪。"}
                result = self._window.create_file_dialog(
                    webview.SAVE_DIALOG, directory="",
                    save_filename="密码导出_%s.%s" % (
                        time.strftime("%Y%m%d_%H%M%S"), ext))
                path = str(result) if result else ""
                if not path:
                    return {"ok": False, "err": "已取消导出。"}
                if not os.path.splitext(path)[1]:
                    path += "." + ext
            if fmt == "csv":
                vault_io.write_csv(entries, path)
            elif fmt == "json":
                vault_io.write_json(entries, path)
            else:
                vault_io.write_encrypted(entries, path, password)
            report = {
                "path": path, "format": fmt, "count": len(entries),
                "size": os.path.getsize(path), "encrypted": fmt == "enc",
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.emit_log("密码导出完成：%s（%d 条，%s）" % (
                path, len(entries), "加密容器" if fmt == "enc" else "明文文件"))
            return {"ok": True, "data": report}
        except Exception as e:
            return {"ok": False, "err": "导出失败：%s" % e}
