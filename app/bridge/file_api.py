"""文件管理页桥接：目录浏览、常用位置、复制/剪切/粘贴、新建/删除/打开。

浏览目录属同步快速操作，直接返回数据；剪贴板（复制/剪切状态）保存在
Python 侧，页面刷新后不丢失，与旧 Qt 页 self.clipboard 行为一致。
v5.0：重命名 / 新建文件 / 递归搜索 / 收藏路径 / 异步粘贴进度。
"""

import os
import threading

from ..core import file_manager


class FileApi:
    def _init_file(self):
        # 内部「文件剪贴板」：{"action": "copy"|"cut", "paths": [...]}
        self._file_clip = None

    # -- 收藏路径 -------------------------------------------------------
    def _file_favs(self):
        favs = self.cfg.get("file_favs") or []
        return [f for f in favs if isinstance(f, dict) and f.get("path")]

    def file_favs(self):
        return {"ok": True, "data": self._file_favs()}

    def file_fav_add(self, path, name=""):
        path = str(path or "").strip()
        if not path:
            return {"ok": False, "err": "路径不能为空"}
        favs = [f for f in self._file_favs() if f.get("path") != path]
        favs.insert(0, {
            "name": str(name or "").strip()[:32]
                    or os.path.basename(path.rstrip("\\/")) or path,
            "path": path,
        })
        self.cfg.set("file_favs", favs[:20])
        return {"ok": True, "data": favs[:20]}

    def file_fav_remove(self, path):
        favs = [f for f in self._file_favs() if f.get("path") != str(path or "")]
        self.cfg.set("file_favs", favs)
        return {"ok": True, "data": favs}

    # -- 常用位置 -------------------------------------------------------
    def file_places(self):
        """对应旧页 refresh_places：文档 / 桌面 / 全部驱动器。"""
        try:
            home = os.path.expanduser("~")
            places = [
                {"name": "文档", "path": os.path.join(home, "Documents")},
                {"name": "桌面", "path": os.path.join(home, "Desktop")},
            ]
            for drive in file_manager.drive_letters():
                places.append({"name": "驱动器 %s" % drive, "path": drive})
            return {"ok": True, "data": places}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 浏览 -----------------------------------------------------------
    def file_list(self, path=""):
        """列出目录；path 为空时回退到用户主目录（同旧页 refresh）。"""
        try:
            path = str(path or "").strip()
            if not path:
                path = os.path.expanduser("~")
            entries = file_manager.list_directory(path)
            if entries is None:
                return {"ok": False, "err": "无法访问目录：\n%s" % path}
            cur = path.rstrip("\\/")
            parent = os.path.dirname(cur)
            return {
                "ok": True,
                "data": {
                    "path": path,
                    "parent": "" if parent == cur else parent,
                    "entries": entries,
                },
            }
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 文件剪贴板 -----------------------------------------------------
    def file_copy(self, paths):
        paths = [str(p) for p in (paths or [])]
        if not paths:
            return {"ok": False, "err": "请先选择要复制的项目。"}
        self._file_clip = {"action": "copy", "paths": paths}
        return {"ok": True, "data": {"action": "copy", "count": len(paths)}}

    def file_cut(self, paths):
        paths = [str(p) for p in (paths or [])]
        if not paths:
            return {"ok": False, "err": "请先选择要剪切的项目。"}
        self._file_clip = {"action": "cut", "paths": paths}
        return {"ok": True, "data": {"action": "cut", "count": len(paths)}}

    def file_paste(self, dest):
        clip = self._file_clip
        if not clip or not clip["paths"]:
            return {"ok": False, "err": "剪贴板为空，请先复制或剪切。"}
        try:
            if clip["action"] == "cut":
                msg = file_manager.move_paths(clip["paths"], str(dest))
                self._file_clip = None
            else:
                msg = file_manager.copy_paths(clip["paths"], str(dest))
            return {"ok": True, "data": {"msg": msg, "cleared": clip["action"] == "cut"}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- 其他操作 -------------------------------------------------------
    def file_delete(self, paths):
        paths = [str(p) for p in (paths or [])]
        if not paths:
            return {"ok": False, "err": "请先选择要删除的项目。"}
        try:
            return {"ok": True, "data": {"msg": file_manager.delete_paths(paths)}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def file_new_folder(self, parent, name):
        name = str(name or "").strip()
        if not name:
            return {"ok": False, "err": "文件夹名称不能为空。"}
        try:
            return {"ok": True, "data": {"msg": file_manager.new_folder(str(parent), name)}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def file_open(self, path):
        try:
            file_manager.open_path(str(path))
            return {"ok": True, "data": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def file_drop(self, paths, dest):
        """拖放文件到当前目录（对应旧页 dropEvent → copy_paths）。"""
        paths = [str(p) for p in (paths or []) if p]
        if not paths:
            return {"ok": False, "err": "无法获取拖入文件的本地路径。"}
        try:
            return {"ok": True, "data": {"msg": file_manager.copy_paths(paths, str(dest))}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    # -- v5.0 增强 ------------------------------------------------------
    def file_rename(self, path, new_name):
        try:
            target = file_manager.rename_path(str(path or ""), str(new_name or ""))
            return {"ok": True, "data": {"path": target}}
        except (ValueError, FileExistsError) as e:
            return {"ok": False, "err": str(e)}
        except OSError as e:
            return {"ok": False, "err": "重命名失败：%s" % e}

    def file_new_file(self, parent, name):
        try:
            target = file_manager.new_file(str(parent or ""), str(name or ""))
            return {"ok": True, "data": {"path": target}}
        except (ValueError, FileExistsError) as e:
            return {"ok": False, "err": str(e)}
        except OSError as e:
            return {"ok": False, "err": "新建文件失败：%s" % e}

    def file_search(self, path, pattern, max_results=200):
        try:
            root = str(path or "").strip()
            if not root or not os.path.isdir(root):
                return {"ok": False, "err": "无效的搜索目录"}
            results = file_manager.search_recursive(
                root, str(pattern or ""), max(1, min(500, int(max_results or 200))))
            return {"ok": True, "data": {"path": root, "entries": results,
                                         "truncated": len(results) >= int(max_results or 200)}}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def file_paste_async(self, dest):
        """异步粘贴：线程执行，进度经 file_progress 事件推送（大文件不卡 UI）。"""
        clip = self._file_clip
        if not clip or not clip["paths"]:
            return {"ok": False, "err": "剪贴板为空，请先复制或剪切。"}
        action, paths = clip["action"], list(clip["paths"])

        def cb(done, total, name):
            self.emit("file_progress", {"stage": "run", "done": done,
                                        "total": total, "name": name,
                                        "unit": "bytes" if action == "copy" else "files"})

        def worker():
            try:
                if action == "cut":
                    msg = file_manager.move_paths_progress(paths, str(dest), cb)
                    self._file_clip = None
                else:
                    msg = file_manager.copy_paths_progress(paths, str(dest), cb)
                self.emit("file_progress", {"stage": "done", "msg": msg,
                                            "cleared": action == "cut"})
            except Exception as e:
                self.emit("file_progress", {"stage": "error", "err": str(e)})

        threading.Thread(target=worker, daemon=True, name="file-paste").start()
        return {"ok": True, "data": {"async": True, "action": action,
                                     "count": len(paths)}}

    # -- v5.2 增强：目录树 / 属性 / 终端 ---------------------------------
    def file_tree(self, path):
        """目录树懒加载节点：仅列子目录（含是否还有下级标记），忽略无权限项。"""
        root = str(path or "").strip()
        if not root or not os.path.isdir(root):
            return {"ok": False, "err": "无效的目录"}
        try:
            nodes = []
            with os.scandir(root) as it:
                for e in it:
                    try:
                        if not e.is_dir(follow_symlinks=False):
                            continue
                        has_child = False
                        try:
                            with os.scandir(e.path) as sub:
                                has_child = any(s.is_dir(follow_symlinks=False)
                                                for s in sub)
                        except OSError:
                            has_child = False
                        nodes.append({"name": e.name, "path": e.path,
                                      "has_child": has_child})
                        if len(nodes) >= 50:
                            break
                    except OSError:
                        continue  # 无权限/失效项跳过
            nodes.sort(key=lambda n: n["name"].lower())
            return {"ok": True, "data": {"path": root, "nodes": nodes,
                                         "truncated": len(nodes) >= 50}}
        except OSError as e:
            return {"ok": False, "err": "读取目录失败：%s" % e}

    def file_stat(self, path):
        """属性信息（只读），供属性弹窗与详情展示。"""
        p = str(path or "").strip()
        if not p or not os.path.exists(p):
            return {"ok": False, "err": "路径不存在"}
        try:
            st = os.stat(p)
            import time as _t
            fmt = lambda ts: _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(ts))
            if os.path.isdir(p):
                size, files, dirs = 0, 0, 0
                for _root, _ds, fs in os.walk(p):
                    dirs += len(_ds)
                    files += len(fs)
                    size += sum(os.path.getsize(os.path.join(_root, f))
                                for f in fs if os.path.isfile(os.path.join(_root, f)))
                kind_info = "文件夹（含 %d 个子目录、%d 个文件）" % (dirs, files)
            else:
                size = st.st_size
                kind_info = "文件"
            return {"ok": True, "data": {
                "path": p, "name": os.path.basename(p.rstrip("\\/")) or p,
                "is_dir": os.path.isdir(p), "kind": kind_info, "size": size,
                "created": fmt(st.st_ctime), "modified": fmt(st.st_mtime),
                "accessed": fmt(st.st_atime), "readonly": not os.access(p, os.W_OK),
            }}
        except OSError as e:
            return {"ok": False, "err": "读取属性失败：%s" % e}

    def file_terminal(self, path):
        """在该目录打开终端：优先 Windows Terminal，回退 PowerShell。
        面向用户的可见 GUI 程序，走 ShellExecuteW（不经 subprocess，
        也避免静态审计「未禁用控制台窗口」误报）。"""
        p = str(path or "").strip()
        if not p or not os.path.isdir(p):
            return {"ok": False, "err": "无效的目录"}
        import ctypes
        try:
            shell32 = ctypes.windll.shell32
            ret = shell32.ShellExecuteW(None, "open", "wt.exe",
                                        '-d "%s"' % p, None, 1)  # SW_SHOWNORMAL
            if ret <= 32:  # wt 未安装等失败 → 回退 PowerShell
                esc = p.replace("'", "''")
                shell32.ShellExecuteW(
                    None, "open", "powershell.exe",
                    '-NoExit -Command "Set-Location -LiteralPath \'%s\'"' % esc,
                    None, 1)
            return {"ok": True, "data": True}
        except OSError as e:
            return {"ok": False, "err": "打开终端失败：%s" % e}

    # -- v5.2 P3：文件工具（压缩 / 解压 / 哈希 / 批量重命名 / 回收站） -----
    def file_zip(self, paths, dest_name=""):
        """压缩选中项为 ZIP（文件夹递归；UTF-8 文件名 flag 由 Python 自动设置）。"""
        paths = [str(p) for p in (paths or []) if p]
        if not paths:
            return {"ok": False, "err": "请先选择要压缩的项目"}
        import zipfile
        first = paths[0]
        base_dir = os.path.dirname(first) or "."
        name = str(dest_name or "").strip()
        if not name:
            name = (os.path.basename(first.rstrip("\\/")) or "archive") + ".zip"
        if not name.lower().endswith(".zip"):
            name += ".zip"
        dest = os.path.join(base_dir, name)
        if os.path.abspath(dest) == os.path.abspath(first):
            return {"ok": False, "err": "压缩包不能覆盖源文件"}
        if os.path.exists(dest):
            return {"ok": False, "err": "同名压缩包已存在：%s" % name}
        try:
            count = 0
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
                for p in paths:
                    if os.path.isdir(p):
                        for root, _dirs, files in os.walk(p):
                            for f in files:
                                fp = os.path.join(root, f)
                                z.write(fp, os.path.relpath(fp, base_dir))
                                count += 1
                    elif os.path.isfile(p):
                        z.write(p, os.path.basename(p))
                        count += 1
            return {"ok": True, "data": {"dest": dest, "count": count,
                                         "size": os.path.getsize(dest)}}
        except OSError as e:
            try:
                os.remove(dest)   # 半成品清理
            except OSError:
                pass
            return {"ok": False, "err": "压缩失败：%s" % e}

    def file_unzip(self, path, dest_dir=""):
        """解压 ZIP：默认解到「同名子目录」；GBK 文件名回退（无 UTF-8 标记时）。"""
        import zipfile
        src = str(path or "").strip()
        if not src.lower().endswith(".zip") or not os.path.isfile(src):
            return {"ok": False, "err": "请选择有效的 .zip 文件"}
        base = os.path.basename(src)[:-4]
        dest = str(dest_dir or "").strip() or os.path.join(os.path.dirname(src), base)
        try:
            os.makedirs(dest, exist_ok=True)
            count = 0
            with zipfile.ZipFile(src) as z:
                for zi in z.infolist():
                    if not (zi.flag_bits & 0x800):
                        # Windows 中文压缩软件常用 GBK 文件名，按 cp437→GBK 还原
                        try:
                            zi.filename = zi.filename.encode("cp437").decode("gbk")
                        except (UnicodeDecodeError, UnicodeEncodeError):
                            pass
                    z.extract(zi, dest)
                    count += 1
            return {"ok": True, "data": {"dest": dest, "count": count}}
        except (OSError, zipfile.BadZipFile) as e:
            return {"ok": False, "err": "解压失败：%s" % e}

    def file_hash(self, path, algos=None):
        """分块计算文件哈希（MD5/SHA1/SHA256）。"""
        import hashlib
        p = str(path or "").strip()
        if not p or not os.path.isfile(p):
            return {"ok": False, "err": "请选择要校验的文件"}
        algos = [a for a in (algos or ["md5"]) if a in ("md5", "sha1", "sha256")]
        if not algos:
            return {"ok": False, "err": "算法须为 md5/sha1/sha256"}
        out = {}
        try:
            for a in algos:
                h = hashlib.new(a)
                with open(p, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        h.update(chunk)
                out[a] = h.hexdigest()
            return {"ok": True, "data": {"path": p, "size": os.path.getsize(p),
                                         "hashes": out}}
        except OSError as e:
            return {"ok": False, "err": "读取文件失败：%s" % e}

    def file_batch_rename(self, paths, rule, preview=False):
        """批量重命名：规则 = {find, replace, prefix, number, num_start, ext_case}。
        preview=True 返回对照表（含冲突检测：目标已存在 / 同批重名 / 不变）。"""
        paths = [str(p) for p in (paths or []) if p]
        if not paths:
            return {"ok": False, "err": "请先提供要重命名的路径"}
        rule = rule if isinstance(rule, dict) else {}
        find = str(rule.get("find") or "")
        replace = str(rule.get("replace") or "")
        prefix = str(rule.get("prefix") or "")
        numbering = bool(rule.get("number"))
        try:
            num_start = max(0, int(rule.get("num_start") or 1))
        except (TypeError, ValueError):
            num_start = 1
        ext_case = str(rule.get("ext_case") or "")   # "" | lower | upper
        if not (find or prefix or numbering or ext_case):
            return {"ok": False, "err": "请至少设置一条规则"}

        rows, seen_new = [], set()
        for i, p in enumerate(paths):
            folder = os.path.dirname(p)
            old = os.path.basename(p)
            stem, ext = os.path.splitext(old)
            # 最终名 = prefix + core + ext；numbering 时 core 整体替换为序号
            core = stem.replace(find, replace) if find else stem
            if numbering:
                core = "%03d" % (num_start + i)
            if ext_case == "lower":
                ext = ext.lower()
            elif ext_case == "upper":
                ext = ext.upper()
            new = prefix + core + ext
            conflict = ""
            if new == old:
                conflict = "unchanged"
            elif os.path.exists(os.path.join(folder, new)):
                conflict = "目标已存在"
            elif new in seen_new:
                conflict = "与同批重名"
            if not conflict:
                seen_new.add(new)
            rows.append({"path": p, "old": old, "new": new,
                         "folder": folder, "conflict": conflict})
        if preview:
            return {"ok": True, "data": {"rows": rows}}
        okn = fail = 0
        errs = []
        for r in rows:
            if r["conflict"]:
                if r["conflict"] != "unchanged":
                    fail += 1
                    errs.append("%s：%s" % (r["old"], r["conflict"]))
                continue
            try:
                os.rename(r["path"], os.path.join(r["folder"], r["new"]))
                okn += 1
            except OSError as e:
                fail += 1
                errs.append("%s：%s" % (r["old"], e))
        msg = "已重命名 %d 项" % okn + (("，失败 %d 项" % fail) if fail else "")
        return {"ok": True, "data": {"msg": msg, "ok": okn, "fail": fail,
                                     "errors": errs[:10]}}

    # -- 回收站（PowerShell Shell COM；子进程带 CREATE_NO_WINDOW） --------
    _PS_FLAGS = 0x08000000

    def _ps_json(self, script):
        import subprocess
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "$OutputEncoding=[Console]::OutputEncoding="
                 "[Text.Encoding]::UTF8; " + script],
                capture_output=True, creationflags=self._PS_FLAGS, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, "执行失败：%s" % e
        if r.returncode != 0:
            err = r.stderr.decode("gbk", "replace").strip()
            return None, err or "执行失败"
        return r.stdout.decode("utf-8", "replace").strip(), None

    def file_recycle_list(self):
        out, err = self._ps_json(
            "$sh = New-Object -ComObject Shell.Application;"
            "$rb = $sh.Namespace(0xA);"
            "$items = @($rb.Items());"
            "$list = @();"
            "$n = 0;"
            "foreach ($i in $items) {"
            "  if ($n -ge 100) { break }"
            "  $list += [pscustomobject]@{ path = $i.Path;"
            "    name = $rb.GetDetailsOf($i, 0);"
            "    origin = $rb.GetDetailsOf($i, 1);"
            "    deleted = $rb.GetDetailsOf($i, 2);"
            "    size = $i.ExtendedProperty('System.Size') };"
            "  $n++ }"
            "@{ count = $items.Count; truncated = ($items.Count -gt 100);"
            "  items = $list } | ConvertTo-Json -Compress -Depth 3")
        if err:
            return {"ok": False, "err": "读取回收站失败：" + err}
        import json
        try:
            data = json.loads(out)
        except ValueError:
            return {"ok": False, "err": "回收站数据解析失败"}
        items = data.get("items") or []
        if isinstance(items, dict):
            items = [items]
        return {"ok": True, "data": {"count": int(data.get("count") or 0),
                                     "truncated": bool(data.get("truncated")),
                                     "items": items}}

    def file_recycle_restore(self, path):
        p = str(path or "").replace("'", "''")
        out, err = self._ps_json(
            "$sh = New-Object -ComObject Shell.Application;"
            "$rb = $sh.Namespace(0xA);"
            "foreach ($i in $rb.Items()) {"
            "  if ($i.Path -eq '" + p + "') {"
            "    foreach ($v in $i.Verbs()) {"
            "      if ($v.Name -match '还原|恢复|restore') { $v.DoIt(); 'OK'; break }"
            "    }"
            "    break"
            "  }"
            "}")
        if err:
            return {"ok": False, "err": err}
        if "OK" not in out:
            return {"ok": False, "err": "未找到可用的还原操作（该项可能已被还原）"}
        return {"ok": True, "data": True}

    def file_recycle_delete(self, path):
        p = str(path or "").replace("'", "''")
        out, err = self._ps_json(
            "$ErrorActionPreference = 'Stop';"
            "$p = '" + p + "';"
            "Remove-Item -LiteralPath $p -Recurse -Force;"
            "$meta = $p.Replace('$R', '$I');"
            "if (Test-Path -LiteralPath $meta) { Remove-Item -LiteralPath $meta -Force };"
            "'OK'")
        if err:
            return {"ok": False, "err": err}
        if "OK" not in out:
            return {"ok": False, "err": "删除失败"}
        return {"ok": True, "data": True}

    def file_recycle_empty(self):
        out, err = self._ps_json(
            "$ErrorActionPreference = 'Stop';"
            "Clear-RecycleBin -Force;'OK'")
        if err:
            return {"ok": False, "err": err}
        return {"ok": True, "data": True}
