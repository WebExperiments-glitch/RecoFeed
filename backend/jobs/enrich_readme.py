"""README 补齐：给兜底 README（<200 字符）的仓库从 jsdelivr 拉真实 README，
并重提标签。seed_real.py 批量拉取时若遇 CDN 限流，用本脚本事后补。

用法：
    python backend/jobs/enrich_readme.py
"""
from __future__ import annotations

import json
import random
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

warnings.filterwarnings("ignore")  # verify=False 的告警静音

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from db.connection import get_conn  # noqa: E402
from tags.extractor import extract_readme_tags, init_jieba  # noqa: E402


def fetch(full_name: str, branch: str) -> str | None:
    for cand in (
        f"https://cdn.jsdelivr.net/gh/{full_name}@{branch}/README.md",
        f"https://cdn.jsdelivr.net/gh/{full_name}@{branch}/readme.md",
    ):
        try:
            r = requests.get(
                cand, timeout=8, verify=False,
                headers={"User-Agent": "recofeed/1.0"},
            )
            if r.status_code == 200 and r.text.strip():
                return r.text[:4000]
        except Exception:
            continue
    return None


def main() -> None:
    init_jieba()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, full_name, COALESCE(default_branch,'main') AS b, topics
               FROM repos WHERE readme_len < 200"""
        ).fetchall()
        todo = [
            (r["id"], r["full_name"], r["b"], json.loads(r["topics"] or "[]"))
            for r in rows
        ]
    print(f"待补齐: {len(todo)} 个仓库")

    def fetch_one(t):
        rid, full, branch, _ = t
        return rid, fetch(full, branch)

    with ThreadPoolExecutor(max_workers=4) as ex:
        fetched = list(ex.map(fetch_one, todo))

    rng = random.Random(42)
    ok = 0
    with get_conn() as conn:
        for (rid, _full, _b, topics), (_rid2, md) in zip(todo, fetched):
            if not md:
                continue
            tags = extract_readme_tags(md, topk=50)
            for t in topics[:8]:
                t = t.strip().lower()
                if t and len(t) > 1 and t not in tags:
                    tags[t] = round(0.5 + rng.random() * 0.3, 3)
            tags = dict(sorted(tags.items(), key=lambda kv: -kv[1])[:50])
            # ⭐ 拿到 README 的同时判定「是否已停更」：README 里写着"本项目停更/
            #    不再维护"的仓库不该被推荐（用户实测反馈），标记 is_dead 即被
            #    召回/补货/搜索全线排除。判定逻辑见 quality/lifecycle.py。
            from quality.lifecycle import detect_stopped
            note = detect_stopped(md)
            conn.execute(
                """UPDATE repos SET readme_md=?, readme_len=?,
                       tags_json=?, tags_updated_at=datetime('now'),
                       is_dead = CASE WHEN ? IS NULL THEN is_dead ELSE 1 END,
                       lifecycle_note = COALESCE(?, lifecycle_note)
                   WHERE id=?""",
                (md, len(md), json.dumps(tags, ensure_ascii=False), note, note, rid),
            )
            ok += 1
    print(f"✅ README 补齐 {ok}/{len(todo)}")


if __name__ == "__main__":
    main()
