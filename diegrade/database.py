from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .schema import SQL, TRIAL_SQL, VERSION


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction(write=True) as conn:
            version, = conn.execute('PRAGMA user_version').fetchone()
            if version == 0:
                for statement in SQL.split(';'):
                    if statement.strip():
                        conn.execute(statement)
                version = 1
            if version == 1:
                # 试评域是只读旁路：新表不触碰现场事件与封存件结构。
                for statement in TRIAL_SQL.split(';'):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute('PRAGMA user_version = 2')
                version = 2
            if version != VERSION:
                raise RuntimeError(f'不支持数据库结构版本 {version}')

    @contextmanager
    def transaction(self, write=False):
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            conn.execute('PRAGMA foreign_keys = ON')
            conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
