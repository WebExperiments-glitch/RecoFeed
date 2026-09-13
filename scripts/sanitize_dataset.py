"""数据集脱敏：把不该进 git 的东西从 recofeed.db 里清掉。

⭐ 为什么需要
    `backend/data/recofeed.db` 是随仓库发布的数据集，但它同时是**运行中的数据库**，
    所以会自然包含运行期产生的机密：
      · users.github_token  —— 用户登录后写入的 GitHub 令牌（拿到即可读写他的仓库）
      · auth_sessions.token —— 登录会话令牌（拿到即可冒充该用户调用本地 API）

    这些一旦被 commit，就永久留在 git 历史里（即使后来删掉也能按 SHA 取回）。

用法：
    python scripts/sanitize_dataset.py          # 脱敏（清掉令牌与会话）
    python scripts/sanitize_dataset.py --check  # 只检查，不改动
    # 提交数据集前先跑一次，再 git add backend/data/recofeed.db

保留什么：仓库元数据、标签、流量池、画像（兴趣标签）、实证圈条目（仓库名/权重）、
         自定义关键词、补货日志 —— 这些是数据集的价值所在，且不含凭据。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from db.connection import get_conn  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="数据集脱敏（清除令牌与会话）")
    ap.add_argument("--check", action="store_true", help="只检查，不修改")
    args = ap.parse_args()

    with get_conn() as conn:
        # ① GitHub 令牌
        n_tok = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE github_token IS NOT NULL "
            "AND github_token != ''"
        ).fetchone()["n"]
        # ② 登录会话
        try:
            n_sess = conn.execute(
                "SELECT COUNT(*) AS n FROM auth_sessions"
            ).fetchone()["n"]
        except Exception:
            n_sess = 0

        print(f"发现：{n_tok} 个 GitHub 令牌、{n_sess} 条登录会话")
        if args.check:
            if n_tok or n_sess:
                print("⚠️ 数据集含凭据，提交前请先运行不带 --check 的本脚本")
            else:
                print("✓ 数据集干净，可以提交")
            return

        if n_tok:
            conn.execute(
                "UPDATE users SET github_token = NULL "
                "WHERE github_token IS NOT NULL AND github_token != ''"
            )
        if n_sess:
            conn.execute("DELETE FROM auth_sessions")
        print("✓ 已清除：users.github_token 置空、auth_sessions 清空")
        print("  提示：下次登录（OAuth 或令牌）会自动重新写入，不影响使用")
        print("  现在可以 git add backend/data/recofeed.db 并提交")


if __name__ == "__main__":
    main()
