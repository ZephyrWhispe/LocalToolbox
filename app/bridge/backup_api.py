"""备份桥接：多目标并行上传 / 恢复 / 连接测试 / 自动定期上传 daemon。

备份内容：
- memo：MemoStore.export() 的 JSON（明文 markdown，恢复支持替换/合并）
- vault：vault.dat 密文原样（任何机器输主口令即可解——备份即兜底）
"""

import json
import os
import threading
import time

from ..core.cloud_backup import BackupTargetError, CloudBackup
from ..core.config import DATA_HOME
from ..core.vault import MAGIC, VaultError


class BackupApi:
    def _init_backup(self):
        self._backup = CloudBackup(self.cfg, log=self.emit_log)
        self._backup_run_id = 0
        self._backup_lock = threading.Lock()  # v5.1c：防「检查+置位」竞态
        self._backup_running = False
        threading.Thread(target=self._backup_autoupload_loop, daemon=True,
                         name="backup-autoupload").start()

    # -- 备份源 ------------------------------------------------------------
    def _memo_export_file(self):
        import tempfile
        fd, path = tempfile.mkstemp(prefix="memo_bak_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._memos.export(), f, ensure_ascii=False)
        except Exception:
            # v5.1c：写失败时把半成品临时文件删掉，不残留 %TEMP%
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        return path

    def _vault_path(self):
        return os.path.join(DATA_HOME, "vault.dat")

    # -- API ---------------------------------------------------------------
    def backup_run(self, kind="all"):
        """并行上传到全部启用目标；异步执行，进度经 backup_progress 事件推送。"""
        kind = str(kind or "all")
        if kind not in ("all", "memo", "vault"):
            return {"ok": False, "err": "kind 须为 all/memo/vault"}
        # v5.1c：pywebview 每次调用在独立线程，检查+置位必须原子
        with self._backup_lock:
            if self._backup_running:
                return {"ok": False, "err": "备份正在进行中，请稍候"}
            targets = self._backup.targets(enabled_only=True)
            if not targets:
                return {"ok": False, "err": "没有已启用的备份目标，请先在设置中配置"}
            kinds = ["memo", "vault"] if kind == "all" else [kind]
            self._backup_running = True
        threading.Thread(target=self._backup_worker, args=(targets, kinds),
                         daemon=True, name="backup-run").start()
        return {"ok": True, "data": {"targets": len(targets), "kinds": kinds}}

    def _backup_worker(self, targets, kinds):
        try:
            self._backup_worker_inner(targets, kinds)
        finally:
            self._backup_running = False

    def _backup_worker_inner(self, targets, kinds):
        results = []
        threads = []

        def run_target(target):
            per = []
            for kind in kinds:
                local = None
                try:
                    if kind == "memo":
                        local = self._memo_export_file()
                    else:
                        local = self._vault_path()
                        if not os.path.isfile(local):
                            continue  # 未建库跳过
                    name = self._backup.backup_one(target, kind, local)
                    per.append({"kind": kind, "ok": True, "file": name})
                except Exception as e:
                    per.append({"kind": kind, "ok": False, "err": str(e)})
                finally:
                    # v5.1b：备忘录明文临时文件任何路径都必须删除
                    if kind == "memo" and local and os.path.isfile(local) \
                            and "memo_bak_" in local:
                        try:
                            os.remove(local)
                        except OSError:
                            pass
            self.emit("backup_progress", {
                "target": target.get("name"), "results": per})
            results.append({"target": target.get("name"), "results": per})

        for t in targets:
            th = threading.Thread(target=run_target, args=(t,), daemon=True,
                                  name="backup-target")
            th.start()
            threads.append(th)
        for th in threads:
            th.join(timeout=600)
        ok = sum(1 for r in results for x in r["results"] if x["ok"])
        fail = sum(1 for r in results for x in r["results"] if not x["ok"])
        self.emit("backup_done", {"ok": fail == 0, "ok_count": ok,
                                  "fail_count": fail, "results": results})
        self.emit_log("云备份完成：成功 %d 项，失败 %d 项。" % (ok, fail))

    def backup_list(self, kind, target_index):
        """列出目标上可恢复的备份文件。"""
        kind = str(kind or "")
        if kind not in ("memo", "vault"):
            return {"ok": False, "err": "kind 须为 memo/vault"}
        targets = self._backup.targets()
        if not (0 <= int(target_index or 0) < len(targets)):
            return {"ok": False, "err": "备份目标不存在"}
        try:
            files = self._backup.list_remote(targets[int(target_index)], kind)
            return {"ok": True, "data": {"target": targets[int(target_index)].get("name"),
                                         "files": files}}
        except BackupTargetError as e:
            return {"ok": False, "err": str(e)}
        except Exception as e:
            return {"ok": False, "err": "列出备份失败：%s" % e}

    def backup_restore(self, kind, target_index, remote_name, mode="replace"):
        """恢复：下载 → 校验 → 导入（memo replace/merge；vault 覆盖后强制锁定）。"""
        kind = str(kind or "")
        if kind not in ("memo", "vault"):
            return {"ok": False, "err": "kind 须为 memo/vault"}
        targets = self._backup.targets()
        if not (0 <= int(target_index or 0) < len(targets)):
            return {"ok": False, "err": "备份目标不存在"}
        import tempfile
        suffix = ".json" if kind == "memo" else ".dat"
        fd, tmp = tempfile.mkstemp(prefix="restore_", suffix=suffix)
        os.close(fd)
        try:
            self._backup.download_one(targets[int(target_index)], kind,
                                      remote_name, tmp)
            if kind == "memo":
                with open(tmp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict) or "memos" not in data:
                    return {"ok": False, "err": "备份文件格式不正确"}
                if mode == "merge":
                    n = self._memos.merge_in(data)
                else:
                    n = self._memos.replace_all(data)
                return {"ok": True, "data": {"count": n, "mode": mode}}
            # vault：校验头部为加密容器后原样覆盖
            with open(tmp, "r", encoding="utf-8") as f:
                line = f.read().strip()
            parts = line.split("|")
            if len(parts) != 5 or parts[0] != MAGIC:
                return {"ok": False, "err": "该文件不是有效的密码库备份"}
            # v5.1b：先锁定清空内存态，阻断并发保存把旧条目写回刚恢复的文件
            self._vault.lock()
            dest = self._vault_path()
            os.replace(tmp, dest)
            self.emit_log("密码库已从备份恢复（%s），请用备份时的主口令解锁。" % remote_name)
            return {"ok": True, "data": {"restored": True}}
        except VaultError as e:
            return {"ok": False, "err": str(e)}
        except ValueError as e:
            return {"ok": False, "err": str(e) or "备份文件解析失败"}
        except json.JSONDecodeError:
            return {"ok": False, "err": "备份文件解析失败"}
        except Exception as e:
            return {"ok": False, "err": "恢复失败：%s" % e}
        finally:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def backup_test(self, target_index):
        """单目标连接测试（listdir 探活）。"""
        targets = self._backup.targets()
        idx = int(target_index or 0)
        if not (0 <= idx < len(targets)):
            return {"ok": False, "err": "备份目标不存在"}
        t = targets[idx]
        try:
            client, segs = self._backup._resolve(t)
            client.listdir("/".join(segs) if segs else "/")
            return {"ok": True, "data": {"name": t.get("name"), "reachable": True}}
        except BackupTargetError as e:
            return {"ok": False, "err": str(e)}
        except Exception as e:
            return {"ok": False, "err": "连接失败：%s" % e}

    # -- 自动定期 ----------------------------------------------------------
    def _backup_autoupload_loop(self):
        last = 0.0
        while True:
            time.sleep(1800)
            try:
                if not self.cfg.get("backup_autoupload", False):
                    continue
                hours = max(6.0, float(self.cfg.get("backup_autoupload_hours", 24) or 24))
                if time.time() - last < hours * 3600:
                    continue
                if not self._backup.targets(enabled_only=True):
                    continue
                last = time.time()
                r = self.backup_run("all")
                # v5.1c：被重入防护拒绝（手动备份进行中）时不再误报「已触发」
                if r.get("ok"):
                    self.emit_log("自动云备份已触发。")
            except Exception:
                continue
