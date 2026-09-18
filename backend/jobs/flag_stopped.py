"""回填：把「已停更 / 不再维护」的仓库标记为 is_dead，避免被推荐。

用法
    python backend/jobs/flag_stopped.py            # 扫描并写标记
    python backend/jobs/flag_stopped.py --dry-run  # 只看命中，不写库
    python backend/jobs/flag_stopped.py --unflag    # 撤销标记（误判时用）

落库
    repos.is_dead = 1        ← 召回 / 补货 / 搜索的 SQL 都已在过滤这一列，标记即生效
    repos.lifecycle_note     ← 命中的原句，便于追溯与人工复核

判定逻辑见 quality/lifecycle.py（只认项目级措辞，刻意不收 deprecated/archived 这类
局部语义词，避免误杀）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from db.connection import get_conn  # noqa: E402
from quality.lifecycle import detect_stopped  # noqa: E402


def _ensure_column(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
    if "lifecycle_note" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN lifecycle_note TEXT")
        print("  ＋ 已新增列 repos.lifecycle_note")


def main() -> None:
    ap = argparse.ArgumentParser(description="标记已停更仓库")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    ap.add_argument("--unflag", action="store_true", help="清除所有停更标记")
    args = ap.parse_args()

    with get_conn() as conn:
        _ensure_column(conn)

        if args.unflag:
            n = conn.execute(
                "UPDATE repos SET is_dead = 0, lifecycle_note = NULL WHERE is_dead = 1"
            ).rowcount
            print(f"✓ 已清除 {n} 个停更标记")
            return

        rows = conn.execute(
            """SELECT id, full_name, description, readme_md
               FROM repos WHERE is_archived = 0"""
        ).fetchall()

        hits: list[tuple[int, str, str]] = []
        for r in rows:
            note = detect_stopped(r["readme_md"], r["description"])
            if note:
                hits.append((int(r["id"]), r["full_name"], note))

        print(f"扫描 {len(rows)} 个仓库 → 命中 {len(hits)} 个"
              f"（{len(hits)/max(1,len(rows))*100:.1f}%）")
        for _rid, name, note in hits[:25]:
            print(f"   · {name:<46} ← {note}")
        if len(hits) > 25:
            print(f"   … 其余 {len(hits)-25} 个见数据库")

        if args.dry_run:
            print("\n（--dry-run：未写库）")
            return

        for rid, _name, note in hits:
            conn.execute(
                "UPDATE repos SET is_dead = 1, lifecycle_note = ? WHERE id = ?",
                (note, rid),
            )
        print(f"\n✓ 已标记 {len(hits)} 个仓库为 is_dead=1（召回/补货/搜索会自动排除它们）")


if __name__ == "__main__":
    main()
