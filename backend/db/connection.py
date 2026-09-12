"""SQLite 连接与查询辅助。

设计要点：
- 单文件数据库，零配置（本地优先架构）
- WAL 模式提升并发读性能
- row_factory = sqlite3.Row，返回类字典对象
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Sequence

from core.config import DB_PATH, SCHEMA_PATH


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """建立连接并设置 pragma。"""
    conn = sqlite3.connect(
        db_path or str(DB_PATH),
        check_same_thread=False,
        timeout=10.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    """上下文管理器，自动提交/回滚/关闭。"""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | None = None, drop: bool = False) -> None:
    """执行 schema.sql 建表。drop=True 时先清空（仅用于测试）。"""
    if drop and db_path:
        import os
        for suffix in ("", "-wal", "-shm"):
            p = str(db_path) + suffix
            if os.path.exists(p):
                os.remove(p)

    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"找不到 schema 文件: {SCHEMA_PATH}")

    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_conn(db_path) as conn:
        conn.executescript(sql)


# ------------------------------------------------------------------ 查询辅助
def query(conn: sqlite3.Connection, sql: str,
          params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def query_one(conn: sqlite3.Connection, sql: str,
              params: Sequence[Any] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def execute(conn: sqlite3.Connection, sql: str,
            params: Sequence[Any] = ()) -> sqlite3.Cursor:
    return conn.execute(sql, params)


def executemany(conn: sqlite3.Connection, sql: str,
                seq: Iterable[Sequence[Any]]) -> None:
    conn.executemany(sql, seq)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = query_one(
        conn,
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return row is not None
