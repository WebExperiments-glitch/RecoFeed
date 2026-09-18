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
    """建立连接并设置 pragma。

    ⚠️ isolation_level=None（自动提交）是刻意的，原因是一次真实的 500 事故：
        Python sqlite3 默认会隐式开启事务 —— 读了几行之后再做写操作，
        就变成「读事务升级为写事务」。而 SQLite 对**升级**场景有特殊处理：
        如果升级期间别的连接写过，它会**立刻**返回 SQLITE_BUSY，
        完全不理会 busy_timeout（这是为了避免死锁，属于设计行为）。
        表现就是：Feed 先 SELECT 队列、再 DELETE 出队，
        恰好撞上后台任务（翻译预热）刚提交的写 →
        刷卡几页后随机 500 database is locked，且怎么调大 busy_timeout 都没用。
        改成自动提交后每条语句独立成事务，不存在"升级"，问题消失。
    本项目是单用户本地库，没有跨语句原子性要求，自动提交完全够用。
    """
    conn = sqlite3.connect(
        db_path or str(DB_PATH),
        check_same_thread=False,
        timeout=10.0,
        isolation_level=None,          # ← 自动提交，规避读→写升级死锁
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    # 写锁等待（对"真·写-写"竞争有效；升级死锁由上面的自动提交规避）
    conn.execute("PRAGMA busy_timeout = 30000")
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


def _ensure_columns(conn) -> None:
    """轻量迁移：给老库补上新增列（幂等，失败不致命）。

    ⚠️ 为什么需要：schema.sql 只在首次建库时生效，已有的 recofeed.db 不会自动加列，
       而新代码会引用新列 —— 不补就会报 "no such column"。
    """
    wanted = {
        "repos": [("lifecycle_note", "TEXT")],
    }
    for table, cols in wanted.items():
        try:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except Exception:
            continue
        for name, sqltype in cols:
            if name not in have:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}")
                except Exception:
                    pass


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
        _ensure_columns(conn)          # 老库补列（schema.sql 只在首建时生效）


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
