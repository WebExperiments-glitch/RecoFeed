"""重刷全部仓库标签（离线）：从库里已存的 README 重新提取，清掉历史噪声标签。

⭐ 为什么需要
   早期版本把 README 套话当标签写进了 repos.tags_json（实测出现过
   support 0.33 / install 0.17 / https 0.12 / easily 0.11 / practices 0.11 …），
   这些噪声会：
     ① 灌进用户画像（"你的兴趣是 support"）
     ② 被当成爬虫查询词（去 GitHub 搜 support，白烧额度）
     ③ 影响召回与排序
   字典修好后，必须把**库里已存的历史标签**重刷一遍，否则旧噪声会一直留存。

用法：
    python backend/jobs/retag.py            # 全量重刷
    python backend/jobs/retag.py --limit 50 # 调试用
    python backend/jobs/retag.py --dry-run  # 只看会剔掉什么，不写库
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from db.connection import get_conn  # noqa: E402
from tags.extractor import extract_readme_tags, init_jieba, is_noise_tag  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="重刷全部仓库标签（清噪声）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    init_jieba()

    # ⭐ 先生成/刷新「语料库 topics 词表」——它是白名单闸门的依据之一。
    #    GitHub topics 是仓库作者自己打的标签，是**真实英文技术词表**；
    #    只靠人工技术词典（391 词）太窄，会导致大量仓库一个标签都留不下（实测 25%）。
    if not args.dry_run:
        with get_conn() as _c:
            vocab: set[str] = set()
            for r in _c.execute("SELECT topics FROM repos WHERE topics IS NOT NULL AND topics != ''"):
                try:
                    for t in json.loads(r["topics"]):
                        tt = str(t).strip().lower()
                        if 2 <= len(tt) <= 40:
                            vocab.add(tt)
                except Exception:
                    continue
        vp = _BACKEND_DIR / "dict" / "topics_vocab.txt"
        header = (
            "# 由 jobs/retag.py 自动生成：语料库全部仓库的 GitHub topics 并集\n"
            "# 用途：英文标签的技术性白名单依据（topics 是仓库作者自打的真实技术词）\n"
        )
        vp.write_text(header + "\n".join(sorted(vocab)) + "\n", encoding="utf-8")
        print(f"✓ 已刷新 topics 词表：{len(vocab)} 个技术词 → {vp.name}")

    removed: Counter[str] = Counter()
    changed = total = 0

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, full_name, name, owner, topics, readme_md, tags_json FROM repos ORDER BY id"
        ).fetchall()
        if args.limit:
            rows = rows[: args.limit]
        total = len(rows)

        for r in rows:
            readme = r["readme_md"] or ""
            if not readme.strip():
                continue
            import json as _json
            try:
                _topics = _json.loads(r["topics"] or "[]")
            except Exception:
                _topics = []
            new_tags = extract_readme_tags(readme, topk=50,
                                           topics=_topics, name=r["name"] or "")

            # 统计被剔除的旧噪声（用于报告，说明改动效果）
            try:
                old = json.loads(r["tags_json"] or "{}")
            except json.JSONDecodeError:
                old = {}
            for t in old:
                if is_noise_tag(t):
                    removed[t.lower()] += 1

            new_json = json.dumps(new_tags, ensure_ascii=False)
            if new_json != (r["tags_json"] or ""):
                changed += 1
                if not args.dry_run:
                    conn.execute(
                        "UPDATE repos SET tags_json = ?, tags_updated_at = datetime('now') "
                        "WHERE id = ?",
                        (new_json, r["id"]),
                    )

    print(f"{'[dry-run] ' if args.dry_run else ''}重刷完成：{changed}/{total} 个仓库标签有变化")
    if removed:
        print(f"剔除的噪声标签 Top15（出现在多少仓库里）：")
        for tag, n in removed.most_common(15):
            print(f"   {tag:<18} {n} 个仓库")
    if not args.dry_run:
        print("\n下一步：重建画像 →")
        print("   python -c \"import sys; sys.path.insert(0,'backend'); "
              "from db.connection import get_conn; from user_profile.service import rebuild_profile; "
              "c=get_conn(); [rebuild_profile(c,u) for u in (1,2,3)]\"")


if __name__ == "__main__":
    main()
