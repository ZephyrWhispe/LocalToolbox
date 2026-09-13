"""备忘录页桥接：分组管理、markdown 备忘 CRUD、搜索与置顶。"""

import os

from ..core.config import DATA_HOME
from ..core.memo_store import MemoStore


class MemoApi:
    def _init_memo(self):
        self._memos = MemoStore(os.path.join(DATA_HOME, "memos.db"))

    def _memo_groups(self):
        return self._memos.groups()

    # -- API ---------------------------------------------------------------
    def memo_list(self, query="", group_id=None):
        try:
            return {"ok": True, "data": {
                "groups": self._memo_groups(),
                "memos": self._memos.list(query, group_id),
            }}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    def memo_get(self, mid):
        try:
            m = self._memos.get(int(mid or 0))
        except (TypeError, ValueError):
            return {"ok": False, "err": "备忘录 id 不合法"}
        if not m:
            return {"ok": False, "err": "备忘录不存在"}
        return {"ok": True, "data": m}

    def memo_save(self, mid, title, content, group_id=None, pinned=False):
        """mid=0/None 新建；title 为空时从内容首行推导。返回备忘 id。"""
        try:
            if mid:
                ok = self._memos.save(int(mid), title, content,
                                      group_id, pinned)
                if not ok:
                    return {"ok": False, "err": "备忘录不存在"}
                return {"ok": True, "data": {"id": int(mid)}}
            mid = self._memos.add(content, group_id, title, pinned)
            return {"ok": True, "data": {"id": mid}}
        except ValueError as e:
            return {"ok": False, "err": str(e)}
        except Exception as e:
            return {"ok": False, "err": "保存失败：%s" % e}

    def memo_delete(self, mid):
        try:
            return {"ok": True, "data": self._memos.delete(int(mid or 0))}
        except (TypeError, ValueError):
            return {"ok": False, "err": "备忘录 id 不合法"}

    def memo_pin(self, mid, pinned):
        try:
            return {"ok": True, "data": self._memos.set_pin(int(mid or 0),
                                                            bool(pinned))}
        except (TypeError, ValueError):
            return {"ok": False, "err": "备忘录 id 不合法"}

    def memo_groups(self):
        try:
            return {"ok": True, "data": self._memo_groups()}
        except Exception as e:
            # v5.1c：sqlite 异常（磁盘满/库被锁）转中文，不裸抛英文堆栈
            return {"ok": False, "err": "读取分组失败：%s" % e}

    def memo_group_add(self, name):
        try:
            gid = self._memos.group_add(name)
            return {"ok": True, "data": {"id": gid, "groups": self._memo_groups()}}
        except ValueError as e:
            return {"ok": False, "err": str(e)}

    def memo_group_rename(self, gid, name):
        try:
            self._memos.group_rename(int(gid or 0), name)
            return {"ok": True, "data": self._memo_groups()}
        except ValueError as e:
            return {"ok": False, "err": str(e)}

    def memo_group_delete(self, gid):
        """删除分组：组内备忘移入「未分组」。"""
        try:
            self._memos.group_delete(int(gid or 0))
        except (TypeError, ValueError):
            return {"ok": False, "err": "分组 id 不合法"}
        return {"ok": True, "data": self._memo_groups()}
