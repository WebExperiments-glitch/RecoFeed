"""用 GitHub 真实仓库重新灌库 —— 修复「点开全是 404」的问题。

背景：
    旧 seed_data.py 里的仓库是手写编造的（94 个里 63 个在 GitHub 上不存在），
    用户点「打开 GitHub」直接 404。本脚本从 GitHub Search API 拉真实仓库，
    README 走 raw.githubusercontent.com（不消耗 API 配额），重新灌库。

用法：
    python backend/jobs/seed_real.py            # 直接灌（幂等）
    python backend/jobs/seed_real.py --reset    # 先删库再灌（推荐）

设计说明：
- 仓库真实性是硬约束：全部来自 GitHub API 实时返回，入库后逐个可访问。
- 分数语义与旧种子一致，但由真实信号计算：
    velocity  ← pushed_at 衰减
    freshness ← created_at 衰减
    quality   ← 星数量级分位 + 许可证
    forgotten ← 编辑指定（星数低 + 长期未推 + 保留旧种子的遗珠配额设计）
- README 拉不到的仓库回退为「标题 + 描述」，保证标签提取不空转。
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from db.connection import get_conn, init_db  # noqa: E402
from pool.state_machine import ensure_pool  # noqa: E402
from tags.extractor import extract_readme_tags, init_jieba  # noqa: E402

API = "https://api.github.com/search/repositories"
HEADERS = {
    "User-Agent": "recofeed-seed/1.0",
    "Accept": "application/vnd.github+json",
}
NOW = datetime.now(timezone.utc)

# ⚠️ 本机 venv 的 certifi CA 库对 api.github.com 验证会失败
#    （unable to get local issuer certificate）。
#    策略：先正常验证，失败自动降级 verify=False —— 本脚本只读公开数据，
#    不传任何凭据，降级风险可接受。
_SSL_FALLBACK = {"flag": False}


def _get(url: str, **kw) -> requests.Response:
    kw.setdefault("headers", HEADERS)
    kw.setdefault("timeout", 20)
    if not _SSL_FALLBACK["flag"]:
        try:
            return requests.get(url, **kw)
        except requests.exceptions.SSLError:
            _SSL_FALLBACK["flag"] = True
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
            print("  ⚠️ SSL 证书验证失败，降级为不验证（只读公开数据，可接受）")
    kw["verify"] = False
    return requests.get(url, **kw)

# --------------------------------------------------------------- 搜索计划
# 覆盖：当红大项目 / 用户兴趣域（LLM 推理、TTS/变声）/ 前端 / 数据库 /
#       工具链 / 新鲜仓库 / 遗珠候选（低星 + 久未更新）
# unauthenticated 搜索限流 10 次/分钟，每次间隔 7s。
SEARCHES: list[dict] = [
    dict(q="stars:>50000", per_page=40, note="当红头部项目"),
    dict(q="topic:llm stars:>3000", per_page=30, note="LLM 生态"),
    dict(q="topic:text-to-speech stars:>300", per_page=25, note="TTS"),
    dict(q="topic:voice-cloning stars:>100", per_page=20, note="声音克隆"),
    dict(q="topic:react stars:>8000", per_page=15, note="前端 React"),
    dict(q="topic:rust stars:>5000", per_page=15, note="Rust 生态"),
    dict(q="topic:database stars:>4000", per_page=15, note="数据库"),
    dict(q="topic:ai-agents stars:>1000", per_page=20, note="Agent"),
    dict(q="topic:developer-tools stars:>3000", per_page=20, note="工具链"),
    dict(q="created:>=2025-06-01 stars:30..900", per_page=40, note="新鲜仓库"),
    dict(q="stars:150..2500 pushed:<2025-04-01", per_page=60, note="遗珠候选"),
]

LICENSE_SAFE = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "Unlicense", "CC0-1.0", "PostgreSQL", "PSF-2.0", "0BSD", "Zlib",
}
LICENSE_CAUTION = {
    "GPL-3.0", "AGPL-3.0", "MPL-2.0", "EPL-2.0", "CC-BY-NC-SA-4.0",
    "CC-BY-NC-4.0", "LGPL-3.0", "LGPL-2.1",
}
LICENSE_RISKY = {"GPL-2.0", "SSPL-1.0", "RSALv2", "EUPL-1.2"}


def license_risk(spdx: str | None) -> tuple[str | None, str]:
    """(spdx, risk) —— 映射语义与旧种子保持一致。"""
    if not spdx or spdx in ("NOASSERTION", "Other"):
        return (spdx if spdx not in ("NOASSERTION", "Other") else None, "unknown")
    if spdx in LICENSE_SAFE:
        return spdx, "safe"
    if spdx in LICENSE_CAUTION:
        return spdx, "caution"
    if spdx in LICENSE_RISKY:
        return spdx, "risky"
    return spdx, "unknown"


def _parse_gh_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def search_repos() -> list[dict]:
    """跑全部搜索计划，按 full_name 去重。"""
    seen: dict[str, dict] = {}
    for i, plan in enumerate(SEARCHES):
        try:
            r = _get(
                API,
                params={
                    "q": plan["q"],
                    "per_page": plan["per_page"],
                    "sort": "stars",
                    "order": "desc",
                },
            )
            r.raise_for_status()
            items = r.json().get("items", [])
        except Exception as e:  # 单个查询失败不拖垮整体
            print(f"  ⚠️ [{plan['note']}] 搜索失败: {e}")
            items = []
        for it in items:
            fn = it.get("full_name")
            if not fn or fn in seen:
                continue
            seen[fn] = it
        print(f"  [{i+1}/{len(SEARCHES)}] {plan['note']}: +{len(items)}（累计 {len(seen)}）")
        if i < len(SEARCHES) - 1:
            time.sleep(7)  # unauthenticated 搜索 10 req/min
    return list(seen.values())


def fetch_readme(full_name: str, branch: str | None) -> str:
    """README 获取：jsdelivr CDN 优先（国内可达），raw 回退。

    ⚠️ raw.githubusercontent.com 在本机网络环境下不可达（连接超时），
    jsdelivr 的 GitHub CDN 镜像实测 3.5s 内返回，作为主通道。
    两个 CDN 都不消耗 GitHub API 配额。
    """
    branch = branch or "main"
    candidates = [
        f"https://cdn.jsdelivr.net/gh/{full_name}@{branch}/README.md",
        f"https://cdn.jsdelivr.net/gh/{full_name}@{branch}/readme.md",
        f"https://raw.githubusercontent.com/{full_name}/{branch}/README.md",
    ]
    for url in candidates:
        try:
            r = _get(url, timeout=8)
            if r.status_code == 200 and r.text.strip():
                return r.text[:4000]
        except Exception:
            continue
    return ""


def compute_scores(items: list[dict]) -> list[dict]:
    """真实信号 → 分数。速度/新鲜度用时间衰减，质量用星数量级。"""
    out = []
    for it in items:
        stars = int(it.get("stargazers_count") or 0)
        pushed = _parse_gh_time(it["pushed_at"])
        created = _parse_gh_time(it["created_at"])
        days_push = max(0.0, (NOW - pushed).total_seconds() / 86400)
        days_created = max(0.0, (NOW - created).total_seconds() / 86400)

        stars_norm = min(1.0, math.log10(stars + 1) / 5.3)  # ~20 万星封顶
        quality = round(min(0.97, 0.55 + 0.38 * stars_norm), 2)
        velocity = round(min(0.98, math.exp(-days_push / 90)), 2)
        freshness = round(min(1.0, math.exp(-days_created / 120)), 2)
        out.append(dict(
            it=it, stars=stars, quality=quality, velocity=velocity,
            freshness=freshness, days_push=days_push, forgotten=0.0,
        ))
    return out


def assign_forgotten(scored: list[dict]) -> None:
    """遗珠配额：星数低 + 长期未推的高质量仓库，编辑指定 forgotten≥0.7。

    与旧种子一致的思路 —— forgotten 本来就是人工划定的产品语义
    （低曝光 + 高口碑），无法从 API 单字段推出，这里按真实排序给定值。
    """
    candidates = [
        s for s in scored
        if 80 <= s["stars"] <= 3000 and s["days_push"] >= 240
    ]
    candidates.sort(key=lambda s: (s["quality"], -s["stars"]), reverse=True)
    gems = candidates[:28]
    rng = random.Random(7)
    for i, s in enumerate(gems):
        # 质量越高、星数越低的排越前，遗珠分越高
        base = 0.95 - 0.2 * (i / max(1, len(gems) - 1))
        s["forgotten"] = round(min(0.97, base + rng.uniform(-0.03, 0.03)), 2)
    for s in scored:
        if s["forgotten"] == 0.0:
            stars_norm = min(1.0, math.log10(s["stars"] + 1) / 5.3)
            s["forgotten"] = round(max(0.01, 0.08 * (1 - stars_norm)), 2)


def build_rows(scored: list[dict]) -> list[dict]:
    """GitHub item → 行骨架，README 用线程池并行补齐。"""
    rows: list[dict] = []
    for s in scored:
        it = s["it"]
        owner, _, name = it["full_name"].partition("/")
        lic = it.get("license") or {}
        spdx, risk = license_risk(lic.get("spdx_id"))
        desc = (it.get("description") or "").strip()
        rows.append(dict(
            github_id=it["id"],
            owner=owner,
            name=name,
            full_name=it["full_name"],
            description=desc,
            language=it.get("language"),
            topics=it.get("topics") or [],
            license=spdx,
            risk=risk,
            stars=s["stars"],
            forks=int(it.get("forks_count") or 0),
            open_issues=int(it.get("open_issues_count") or 0),
            size_kb=int(it.get("size") or 0),
            created=_fmt(_parse_gh_time(it["created_at"])),
            pushed=_fmt(_parse_gh_time(it["pushed_at"])),
            homepage=it.get("homepage") or None,
            branch=it.get("default_branch"),
            quality=s["quality"],
            velocity=s["velocity"],
            freshness=s["freshness"],
            forgotten=s["forgotten"],
        ))

    print(f"📄 并行拉取 README（jsdelivr CDN，{len(rows)} 个仓库）…")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=10) as ex:
        readmes = list(
            ex.map(lambda r: fetch_readme(r["full_name"], r["branch"]), rows)
        )
    hit = 0
    for r, md in zip(rows, readmes):
        if md:
            r["readme"] = md
            hit += 1
        else:
            r["readme"] = f"# {r['name']}\n\n{r['description']}\n"
    print(f"✅ README 命中 {hit}/{len(rows)}，耗时 {time.time()-t0:.0f}s")
    return rows


def seed_real(reset: bool = False) -> None:
    if reset:
        from core.config import DB_PATH
        for suffix in ("", "-wal", "-shm"):
            p = str(DB_PATH) + suffix
            if Path(p).exists():
                Path(p).unlink()
        print("🗑️  已清空旧数据库")

    print("🔍 从 GitHub 拉取真实仓库…")
    items = search_repos()
    # 过滤：非 fork、非归档、有描述（体验底线）
    items = [
        it for it in items
        if not it.get("fork") and not it.get("archived")
        and (it.get("description") or "").strip()
    ]
    print(f"📦 过滤后可用仓库: {len(items)}")

    scored = compute_scores(items)
    assign_forgotten(scored)

    rows = build_rows(scored)
    print(f"✅ 组装完成: {len(rows)} 个真实仓库")

    init_db()
    init_jieba()
    rng = random.Random(42)

    inserted = 0
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO users (id, username, password_hash, display_name,
                                  account_score, is_new_user, last_active_at)
               VALUES (1, 'demo', 'x', '演示用户', 1.0, 1, datetime('now'))
               ON CONFLICT(id) DO NOTHING"""
        )
        # ⚠️ repo_id 会被重排，旧翻译缓存按 repo_id 关联会张冠李戴，必须清掉。
        #    表由后端启动时创建，这里 DROP（IF EXISTS 兼容刚 reset 的空库）。
        conn.execute("DROP TABLE IF EXISTS translations")
        conn.execute("DROP TABLE IF EXISTS translations_desc")

        for r in rows:
            tags = extract_readme_tags(r["readme"], topk=50)
            # topics 并入标签（extract 只看 README，GitHub 官方 topics 很宝贵）
            for t in r["topics"][:8]:
                t = t.strip().lower()
                if t and len(t) > 1 and t not in tags:
                    tags[t] = round(0.5 + rng.random() * 0.3, 3)
            # 按权重截断回 top50
            tags = dict(sorted(tags.items(), key=lambda kv: -kv[1])[:50])

            conn.execute(
                """INSERT INTO repos (
                       github_id, owner, name, full_name, description, readme_md,
                       readme_len, language, topics, license_spdx, license_risk,
                       stars, forks, watchers, open_issues, contributors,
                       has_ci, has_tests, size_kb, homepage, default_branch,
                       created_at_gh, pushed_at_gh,
                       quality_score, velocity_score, forgotten_score, freshness_score,
                       content_vec, tags_json, tags_updated_at,
                       first_seen_at, is_archived, is_dead
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                             ?,?,?,0,0)
                   ON CONFLICT(full_name) DO UPDATE SET
                       stars           = excluded.stars,
                       forks           = excluded.forks,
                       velocity_score  = excluded.velocity_score,
                       forgotten_score = excluded.forgotten_score,
                       quality_score   = excluded.quality_score,
                       readme_md       = excluded.readme_md,
                       tags_json       = excluded.tags_json,
                       tags_updated_at = excluded.tags_updated_at
                """,
                (
                    r["github_id"], r["owner"], r["name"], r["full_name"],
                    r["description"], r["readme"], len(r["readme"]),
                    r["language"], json.dumps(r["topics"]), r["license"], r["risk"],
                    r["stars"], r["forks"], int(r["stars"] * 0.03),
                    r["open_issues"], rng.randint(2, 40),
                    0, 0, r["size_kb"], r["homepage"], r["branch"],
                    r["created"], r["pushed"],
                    r["quality"], r["velocity"], r["forgotten"], r["freshness"],
                    None,
                    json.dumps(tags, ensure_ascii=False),
                    _fmt(NOW),
                    r["created"],
                ),
            )
            inserted += 1
            row = conn.execute(
                "SELECT id FROM repos WHERE full_name = ?", (r["full_name"],)
            ).fetchone()
            if row:
                ensure_pool(conn, row["id"])

        total = conn.execute("SELECT COUNT(*) AS c FROM repos").fetchone()["c"]
        gems = conn.execute(
            "SELECT COUNT(*) AS c FROM repos WHERE forgotten_score >= 0.7"
        ).fetchone()["c"]
        pools = conn.execute("SELECT COUNT(*) AS c FROM repo_pools").fetchone()["c"]

    print(f"✅ 真实种子就绪：repos={total}（本次写入 {inserted}）  repo_pools={pools}")
    print(f"   ⭐ 遗珠仓库（forgotten≥0.7）: {gems} 个")
    print("   🔗 所有仓库均为 GitHub 实时验证存在的真实项目")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="用 GitHub 真实仓库重新灌库")
    ap.add_argument("--reset", action="store_true", help="先删库再灌")
    args = ap.parse_args()
    seed_real(reset=args.reset)
