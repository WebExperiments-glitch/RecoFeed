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


def _gh_token() -> str:
    """从 gh CLI 取令牌（api.github.com 走认证后限额 5000/小时）。"""
    try:
        import subprocess
        return subprocess.run(["gh", "auth", "token"], capture_output=True,
                              text=True, timeout=15).stdout.strip()
    except Exception:
        return ""


_GH_TOKEN = None
# 404 = 仓库在 GitHub 上已不存在（改名/删除），这类不该再被推荐 → 主循环里标记 is_dead
_GONE: set[str] = set()


def fetch(full_name: str, branch: str) -> str | None:
    """拉一个仓库的 README。

    ⚠️ 2026-09-18 改：原来只走 cdn.jsdelivr.net —— 本机网络下**该域名直接连不上**
    （raw.githubusercontent.com 也超时），任务只是空转等超时，1848 个仓库一个都补不到。
    改为以 **GitHub API** 为主（api.github.com 实测可达，带 token 限额 5000/小时），
    jsdelivr 仅作兜底。
    """
    global _GH_TOKEN
    if _GH_TOKEN is None:
        _GH_TOKEN = _gh_token()

    # ① GitHub API：/repos/{owner}/{repo}/readme，Accept: raw → 直接返回 README 正文
    try:
        r = requests.get(
            f"https://api.github.com/repos/{full_name}/readme",
            headers={
                "Accept": "application/vnd.github.raw",
                "User-Agent": "recofeed/1.0",
                **({"Authorization": f"Bearer {_GH_TOKEN}"} if _GH_TOKEN else {}),
            },
            timeout=15, verify=False,
        )
        if r.status_code == 200 and r.text.strip():
            return r.text[:4000]
        if r.status_code == 404:
            _GONE.add(full_name)          # 仓库已不存在 → 交给主循环标记 is_dead
            return None
        if r.status_code == 403:          # 触发限额：早点退出，别把时间浪费在 403 上
            print(f"  ⚠️ GitHub API 限额/被拒（{full_name}）—— 本轮提前结束")
            return None
    except Exception:
        pass

    # ② 兜底：jsdelivr（本机常不通，但别的网络环境下有用）
    for cand in (
        f"https://cdn.jsdelivr.net/gh/{full_name}@{branch}/README.md",
        f"https://cdn.jsdelivr.net/gh/{full_name}@{branch}/readme.md",
    ):
        try:
            r = requests.get(cand, timeout=6, verify=False,
                             headers={"User-Agent": "recofeed/1.0"})
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

    rng = random.Random(42)
    ok = 0
    tried = 0
    BATCH = 40          # 每批 40 个：拉完即写库，进度可见（原来"全拉完才写"，1848 个要等很久且中途崩溃全丢）

    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]

        def fetch_one(t):
            rid, full, branch, _ = t
            return rid, fetch(full, branch)

        with ThreadPoolExecutor(max_workers=8) as ex:
            fetched = list(ex.map(fetch_one, chunk))
        tried += len(chunk)

        with get_conn() as conn:
            for (rid, full, _b, topics), (_rid2, md) in zip(chunk, fetched):
                if not md:
                    # 仓库已从 GitHub 消失（404）→ 标记 is_dead，永不推荐
                    if full in _GONE:
                        conn.execute(
                            """UPDATE repos SET is_dead = 1,
                                   lifecycle_note = COALESCE(lifecycle_note, '仓库已从 GitHub 消失（404）')
                               WHERE id = ?""", (rid,))
                    continue
                # ⚠️ 这里保留带 topics/name 的闸门参数（白名单依据）
                tags = extract_readme_tags(md, topk=50, topics=topics)
                for t in topics[:8]:
                    t = t.strip().lower()
                    if t and len(t) > 1 and t not in tags:
                        tags[t] = round(0.5 + rng.random() * 0.3, 3)
                tags = dict(sorted(tags.items(), key=lambda kv: -kv[1])[:50])
            # ⭐ 拿到 README 的同时判定「是否已停更」：README 里写着"本项目停更/
            #    不再维护"的仓库不该被推荐（用户实测反馈），标记 is_dead 即被
            #    召回/补货/搜索全线排除。判定逻辑见 quality/lifecycle.py。
                from quality.lifecycle import detect_stopped
                from quality.redact import redact_secrets
                md = redact_secrets(md)      # 抹掉 README 里别人的密钥后再落库
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
        print(f"  进度 {tried}/{len(todo)} → 已补齐 {ok}", flush=True)
    print(f"✅ README 补齐 {ok}/{len(todo)}")


if __name__ == "__main__":
    main()
