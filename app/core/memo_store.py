"""备忘录 SQLite 落盘：markdown 笔记 + 自定义分组收纳。

设计（仿 clipboard_store.py 四件套）：单连接 check_same_thread=False +
内部锁；_SCHEMA CREATE IF NOT EXISTS；删分组只把组内备忘置为「未分组」，
不级联删除。列表排序 pinned DESC, updated DESC。搜索用 LIKE（标题+内容），
中文无分词需求，不引 FTS5。

export()/replace_all()/merge_in() 供 WebDAV 备份与恢复使用。
"""

import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memo_groups (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT UNIQUE,
    sort  INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS memos (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    title    TEXT,
    content  TEXT,
    group_id INTEGER,
    pinned   INTEGER DEFAULT 0,
    created  REAL,
    updated  REAL
);
"""

_PREVIEW_LEN = 120


def derive_title(content):
    """取内容首个非空行作为标题：剥掉 markdown 标记，≤40 字。"""
    import re
    for line in str(content or "").splitlines():
        t = line.strip()
        if not t:
            continue
        t = re.sub(r"^#{1,6}\s*", "", t)          # 标题
        t = re.sub(r"^[-*+]\s+\[[ xX]\]\s*", "", t)  # 任务列表
        t = re.sub(r"^[-*+]\s+", "", t)           # 无序列表
        t = re.sub(r"^>\s*", "", t)               # 引用
        t = t.strip("[]`*_")
        if not t:
            continue
        return t[:40]
    return "无标题"


class MemoStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=5)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- 分组 --------------------------------------------------------------
    def groups(self):
        """分组列表 + 各组备忘计数（含未分组 group_id=0）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, sort FROM memo_groups ORDER BY sort, id"
            ).fetchall()
            counts = dict(self._conn.execute(
                "SELECT group_id, COUNT(*) FROM memos GROUP BY group_id"
            ).fetchall())
        out = [{"id": 0, "name": "未分组", "count": counts.get(0, 0) or counts.get(None, 0)}]
        for gid, name, sort in rows:
            out.append({"id": gid, "name": name, "sort": sort,
                        "count": counts.get(gid, 0)})
        return out

    def group_add(self, name):
        name = str(name or "").strip()[:32]
        if not name:
            raise ValueError("分组名不能为空")
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO memo_groups(name) VALUES (?)", (name,))
                self._conn.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                raise ValueError("分组已存在：%s" % name)

    def group_rename(self, gid, name):
        name = str(name or "").strip()[:32]
        if not name:
            raise ValueError("分组名不能为空")
        with self._lock:
            try:
                self._conn.execute(
                    "UPDATE memo_groups SET name=? WHERE id=?", (name, int(gid)))
                self._conn.commit()
            except sqlite3.IntegrityError:
                raise ValueError("分组已存在：%s" % name)

    def group_delete(self, gid):
        """删除分组：组内备忘置为未分组（group_id NULL），不删除备忘。"""
        with self._lock:
            self._conn.execute(
                "UPDATE memos SET group_id=NULL WHERE group_id=?", (int(gid),))
            self._conn.execute("DELETE FROM memo_groups WHERE id=?", (int(gid),))
            self._conn.commit()

    # -- 备忘 --------------------------------------------------------------
    def add(self, content, group_id=None, title=None, pinned=False):
        """新增备忘；title 为空时从内容首行推导。返回新 id。"""
        content = str(content or "")
        now = time.time()
        if not title:
            title = derive_title(content)
        gid = int(group_id) if group_id else None
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO memos(title, content, group_id, pinned, created, updated)"
                " VALUES (?,?,?,?,?,?)",
                (str(title)[:120], content, gid, 1 if pinned else 0, now, now))
            self._conn.commit()
            return cur.lastrowid

    def save(self, mid, title, content, group_id=None, pinned=False):
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memos SET title=?, content=?, group_id=?, pinned=?,"
                " updated=? WHERE id=?",
                (str(title or "")[:120], str(content or ""),
                 int(group_id) if group_id else None,
                 1 if pinned else 0, time.time(), int(mid)))
            self._conn.commit()
            return cur.rowcount > 0

    def set_pin(self, mid, pinned):
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memos SET pinned=?, updated=updated WHERE id=?",
                (1 if pinned else 0, int(mid)))
            self._conn.commit()
            return cur.rowcount > 0

    def delete(self, mid):
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM memos WHERE id=?", (int(mid),))
            self._conn.commit()
            return cur.rowcount > 0

    def get(self, mid):
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, content, group_id, pinned, created, updated"
                " FROM memos WHERE id=?", (int(mid),)).fetchone()
        if not row:
            return None
        return {"id": row[0], "title": row[1], "content": row[2],
                "group_id": row[3], "pinned": bool(row[4]),
                "created": row[5], "updated": row[6]}

    def list(self, query="", group_id=None):
        """列表（不含正文，仅预览）；query 匹配标题+内容；group_id=0 → 未分组。"""
        q = str(query or "").strip()
        sql = ("SELECT m.id, m.title, m.content, m.group_id, m.pinned,"
               " m.updated, g.name FROM memos m"
               " LEFT JOIN memo_groups g ON g.id = m.group_id WHERE 1=1")
        args = []
        if q:
            # v5.1b：SQLite LIKE 默认无转义符，必须显式 ESCAPE '\' 才能匹配
            # 含 % / _ 的关键词
            sql += (" AND (m.title LIKE ? ESCAPE '\\' OR"
                    " m.content LIKE ? ESCAPE '\\')")
            kw = "%" + q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
            args += [kw, kw]
        if group_id is not None and str(group_id) != "":
            if int(group_id) == 0:
                sql += " AND m.group_id IS NULL"
            else:
                sql += " AND m.group_id = ?"
                args.append(int(group_id))
        sql += " ORDER BY m.pinned DESC, m.updated DESC, m.id DESC LIMIT 500"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        out = []
        for mid, title, content, gid, pinned, updated, gname in rows:
            preview = (content or "").replace("\r", "").replace("\n", " ")[:_PREVIEW_LEN]
            out.append({"id": mid, "title": title or "", "preview": preview,
                        "group_id": gid, "group_name": gname or "",
                        "pinned": bool(pinned), "updated": updated})
        return out

    # -- 备份 / 恢复 -------------------------------------------------------
    def export(self):
        """整库导出（备份上传用）。"""
        with self._lock:
            groups = [{"id": r[0], "name": r[1], "sort": r[2]} for r in
                      self._conn.execute(
                          "SELECT id, name, sort FROM memo_groups ORDER BY id")]
            memos = [{"id": r[0], "title": r[1], "content": r[2],
                      "group_id": r[3], "pinned": r[4],
                      "created": r[5], "updated": r[6]} for r in
                     self._conn.execute(
                         "SELECT id, title, content, group_id, pinned,"
                         " created, updated FROM memos ORDER BY id")]
        return {"groups": groups, "memos": memos}

    def replace_all(self, data):
        """恢复（替换模式）：清空后按备份内容重建，返回导入条数。

        v5.1b：条目缺 id / id 冲突 / 非 int 时自动分配新 id（不再整批回滚）；
        数据库异常转 ValueError（中文），由桥接层直显。
        """
        groups = list((data or {}).get("groups") or [])
        memos = list((data or {}).get("memos") or [])
        with self._lock:
            try:
                self._conn.execute("DELETE FROM memos")
                self._conn.execute("DELETE FROM memo_groups")
                for g in groups:
                    self._conn.execute(
                        "INSERT INTO memo_groups(id, name, sort) VALUES (?,?,?)",
                        (int(g.get("id") or 0), str(g.get("name") or "")[:32],
                         int(g.get("sort") or 0)))
                used = set()
                rows = []
                for m in memos:
                    try:
                        mid = int(m.get("id") or 0)
                    except (TypeError, ValueError):
                        mid = 0
                    if mid <= 0 or mid in used:
                        mid = None  # 缺失/冲突 → 稍后自动分配
                    else:
                        used.add(mid)
                    rows.append((mid, m))
                nxt = 1
                for mid, m in rows:
                    if mid is None:
                        while nxt in used:
                            nxt += 1
                        mid = nxt
                        used.add(mid)
                    self._conn.execute(
                        "INSERT INTO memos(id, title, content, group_id, pinned,"
                        " created, updated) VALUES (?,?,?,?,?,?,?)",
                        (mid, str(m.get("title") or "")[:120],
                         str(m.get("content") or ""),
                         int(m["group_id"]) if m.get("group_id") else None,
                         1 if m.get("pinned") else 0,
                         float(m.get("created") or time.time()),
                         float(m.get("updated") or time.time())))
                # 修正自增起点
                self._conn.execute(
                    "UPDATE sqlite_sequence SET seq=(SELECT COALESCE(MAX(id),0)"
                    " FROM memo_groups) WHERE name='memo_groups'")
                self._conn.execute(
                    "UPDATE sqlite_sequence SET seq=(SELECT COALESCE(MAX(id),0)"
                    " FROM memos) WHERE name='memos'")
                self._conn.commit()
                return len(rows)
            except (sqlite3.Error, TypeError, ValueError):
                self._conn.rollback()
                raise ValueError("恢复失败：备份数据不完整或格式异常")

    def merge_in(self, data):
        """恢复（合并模式）：全部作为新备忘追加，按分组名映射到现有分组
        （缺失则新建），返回导入条数。"""
        groups = list((data or {}).get("groups") or [])
        memos = list((data or {}).get("memos") or [])
        name2id = {g["name"]: g["id"] for g in self.groups() if g["id"]}
        gid_map = {}
        with self._lock:
            try:
                for g in groups:
                    name = str(g.get("name") or "").strip()
                    if not name:
                        continue
                    if name in name2id:
                        gid_map[g.get("id")] = name2id[name]
                        continue
                    cur = self._conn.execute(
                        "INSERT INTO memo_groups(name) VALUES (?)", (name,))
                    gid_map[g.get("id")] = cur.lastrowid
                    name2id[name] = cur.lastrowid
                now = time.time()
                for m in memos:
                    self._conn.execute(
                        "INSERT INTO memos(title, content, group_id, pinned,"
                        " created, updated) VALUES (?,?,?,?,?,?)",
                        (str(m.get("title") or "")[:120],
                         str(m.get("content") or ""),
                         gid_map.get(m.get("group_id")),
                         1 if m.get("pinned") else 0,
                         float(m.get("created") or now),
                         float(m.get("updated") or now)))
                self._conn.commit()
                return len(memos)
            except (sqlite3.Error, TypeError, ValueError):
                self._conn.rollback()
                raise

    def close(self):
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
