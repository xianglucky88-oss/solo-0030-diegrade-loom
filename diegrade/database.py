from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .schema import MIGRATIONS, SQL, VERSION


def run(conn, script):
    for statement in script.split(';'):
        if statement.strip():
            conn.execute(statement)


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction(write=True) as conn:
            version, = conn.execute('PRAGMA user_version').fetchone()
            if version == 0:
                run(conn, SQL)
                version = VERSION
            while version != VERSION:
                if version not in MIGRATIONS:
                    raise RuntimeError(f'不支持数据库结构版本 {version}')
                run(conn, MIGRATIONS[version])
                version, = conn.execute('PRAGMA user_version').fetchone()

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
