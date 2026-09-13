"""导出数据集快照：把**运行库**脱敏后导出为可提交的 `recofeed.seed.db`。

⭐ 为什么用"快照"而不是直接提交运行库
    `backend/data/recofeed.db` 同时是运行中的数据库，会自然积累：
      · users.github_token  —— 用户登录后的 GitHub 令牌
      · auth_sessions.token —— 登录会话
      · 用户行为/画像        —— 属个人数据
    直接提交它就等于把凭据和个人数据写进 git 历史（一旦开源就无法收回）。

    正确做法（本项目采用）：
      运行库 recofeed.db      → gitignore，永不提交
      快照 recofeed.seed.db   → 脱敏后提交，供他人 clone 后开箱即用

用法：
    python scripts/export_dataset.py            # 导出快照（脱敏）
    python scripts/export_dataset.py --check    # 只检查运行库是否含凭据

产出：
    backend/data/recofeed.seed.db   ← 提交这个

⚠️ 导入注意：用 SQLite 官方 backup API 复制（WAL 模式下直接 copy 文件会丢数据）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = _ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from core.config import DATA_DIR, DB_PATH  # noqa: E402

SEED_PATH = DATA_DIR / "recofeed.seed.db"

# 这些表属于"本机数据"，不进快照
PRIVATE_TABLES = ("auth_sessions", "user_events", "feed_impressions", "feed_queue", "refill_log")


def _stats(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in ("repos", "repo_pools", "users", "github_footprint", "user_keywords"):
        try:
            out[t] = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            out[t] = -1
    return out


def _creds(conn: sqlite3.Connection) -> tuple[int, int]:
    tok = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE github_token IS NOT NULL "
        "AND github_token != ''"
    ).fetchone()[0]
    try:
        sess = conn.execute("SELECT COUNT(*) AS n FROM auth_sessions").fetchone()[0]
    except sqlite3.OperationalError:
        sess = 0
    return tok, sess


def main() -> None:
    ap = argparse.ArgumentParser(description="导出脱敏数据集快照")
    ap.add_argument("--check", action="store_true", help="只检查运行库是否含凭据")
    ap.add_argument("--keep-user-data", action="store_true",
                    help="保留用户行为/队列（默认清空，只留仓库元数据与实证圈）")
    args = ap.parse_args()

    if not Path(DB_PATH).exists():
        print(f"✗ 找不到运行库：{DB_PATH}")
        return

    src = sqlite3.connect(str(DB_PATH), timeout=20.0)
    tok, sess = _creds(src)
    print(f"运行库口径：{_stats(src)}")
    print(f"凭据检查：GitHub 令牌 {tok} 个、登录会话 {sess} 条")
    if args.check:
        print("→", "✓ 干净" if tok == 0 and sess == 0 else "⚠️ 含凭据，导出前会被清掉")
        src.close()
        return

    # 用 backup API 复制（WAL 下安全）
    if SEED_PATH.exists():
        SEED_PATH.unlink()
    dst = sqlite3.connect(str(SEED_PATH))
    src.backup(dst)
    src.close()

    # ── 脱敏 ──
    dst.execute(
        "UPDATE users SET github_token = NULL "
        "WHERE github_token IS NOT NULL AND github_token != ''"
    )
    dst.execute(
        "UPDATE users SET password_hash = 'seed' WHERE password_hash = 'github-oauth'"
    )
    dst.execute("DELETE FROM auth_sessions")
    if not args.keep_user_data:
        for t in ("user_events", "feed_impressions", "feed_queue", "refill_log"):
            try:
                dst.execute(f"DELETE FROM {t}")
            except sqlite3.OperationalError:
                pass
        # 画像里的"用户个人足迹"也清掉，但保留实证圈（仓库名+权重，公开信息）
        try:
            dst.execute("DELETE FROM user_profiles")
        except sqlite3.OperationalError:
            pass
    dst.commit()
    dst.execute("VACUUM")
    dst.commit()

    tok2, sess2 = _creds(dst)
    print(f"\n✓ 快照已导出：{SEED_PATH}")
    print(f"  体积 {SEED_PATH.stat().st_size / 1024 / 1024:.2f} MB")
    print(f"  快照口径：{_stats(dst)}")
    print(f"  残留凭据：令牌 {tok2} / 会话 {sess2}"
          f" → {'✓ 干净' if tok2 == 0 and sess2 == 0 else '✗ 仍有凭据'}")
    dst.close()
    print("\n下一步：git add backend/data/recofeed.seed.db 并提交（运行库不入库）")


if __name__ == "__main__":
    main()
