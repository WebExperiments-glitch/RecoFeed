"""数据集快照的完整性闸门。

seed.db 是随仓库分发的脱敏快照，必须满足：
    · SQLite 完整性检查通过
    · 语料规模足够（不能让"空库"混过去）
    · 不含任何登录令牌 / 会话
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SEED_DB = Path(__file__).resolve().parents[1] / "data" / "recofeed.seed.db"

REQUIRED_COLUMNS = {
    "full_name", "owner", "name", "stars", "topics", "tags_json",
    "readme_md", "quality_score", "forgotten_score",
}


def test_seed_db_exists_and_is_healthy() -> None:
    assert SEED_DB.exists(), f"缺少数据集快照：{SEED_DB}"
    conn = sqlite3.connect(str(SEED_DB))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        repos = conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
        assert repos >= 3000, f"语料库只有 {repos} 个仓库，疑似快照损坏"
        embeddings = conn.execute("SELECT COUNT(*) FROM repo_embeddings").fetchone()[0]
        assert embeddings >= repos * 0.5, "向量覆盖率过低（快照可能没带向量）"
    finally:
        conn.close()


def test_seed_db_schema_has_algorithm_columns() -> None:
    conn = sqlite3.connect(str(SEED_DB))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(repos)")}
    finally:
        conn.close()
    missing = REQUIRED_COLUMNS - cols
    assert not missing, f"repos 表缺少推荐链路必需的列：{sorted(missing)}"


def test_seed_db_is_sanitized() -> None:
    conn = sqlite3.connect(str(SEED_DB))
    try:
        tokens = conn.execute(
            "SELECT COUNT(*) FROM users WHERE COALESCE(github_token, '') <> ''"
        ).fetchone()[0]
        sessions = conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0]
    finally:
        conn.close()
    assert tokens == 0, f"快照里有 {tokens} 个用户的 GitHub 令牌未清除"
    assert sessions == 0, f"快照里有 {sessions} 条登录会话未清除"
