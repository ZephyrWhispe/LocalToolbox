"""临时清理：删除探针测试条目。用完即删。"""
import os
import sqlite3

from app.core.config import DATA_HOME

db = os.path.join(DATA_HOME, "clip_history.db")
conn = sqlite3.connect(db)
n = conn.execute(
    "SELECT COUNT(*) FROM clip_history WHERE text LIKE 'WINV-TEST%'"
).fetchone()[0]
conn.execute("DELETE FROM clip_history WHERE text LIKE 'WINV-TEST%'")
conn.commit()
conn.close()
print("removed test rows:", n)
