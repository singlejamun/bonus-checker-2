"""Minimal stand-in for aiosqlite, only for running tests without the real package."""
import sqlite3

Row = sqlite3.Row


class _Cursor:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = cur.rowcount
        self.lastrowid = cur.lastrowid

    async def fetchone(self):
        return self._cur.fetchone()

    async def fetchall(self):
        return self._cur.fetchall()

    async def close(self):
        self._cur.close()


class Connection:
    def __init__(self, path):
        self._db = sqlite3.connect(path)
        self.row_factory = None

    def __await__(self):
        async def _self():
            return self
        return _self().__await__()

    async def execute(self, sql, params=()):
        self._db.row_factory = self.row_factory
        return _Cursor(self._db.execute(sql, params))

    async def executemany(self, sql, seq):
        return _Cursor(self._db.executemany(sql, seq))

    async def executescript(self, script):
        self._db.executescript(script)

    async def commit(self):
        self._db.commit()

    async def rollback(self):
        self._db.rollback()

    async def close(self):
        self._db.close()


def connect(path):
    return Connection(path)
