"""接口冒烟测试：把所有 GET 接口跑一遍，并校验关键响应字段。

为什么需要
    2026-09-18 事故：我给 feed 富化加了「摘要过滤」，代码里读了 `r["name"]`，
    但那条 SQL 忘了 SELECT name → sqlite3.Row 抛 "No item with that key"
    → **整块富化被 except 吞掉** → 所有卡片的 stats / README 摘要全为空。
    后端只留了一行 WARNING，前端表现为"数据全空"，看起来像 UI 坏了。
    是用户截图才发现的 —— 这类问题必须由自动化冒烟测试兜住。

用法
    python scripts/smoke_test.py                # 全量
    python scripts/smoke_test.py --user 3       # 指定用户
    python scripts/smoke_test.py --only feed    # 只跑名字含 feed 的接口
退出码：0 全通过 / 1 有失败
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Callable

import requests

BASE = "http://127.0.0.1:8000/api"

# 关键字段校验：命中即检查（避免面面俱到但空洞的测试）
CHECKS: dict[str, Callable[[dict], list[str]]] = {}


def check(name: str):
    def deco(fn: Callable[[dict], list[str]]):
        CHECKS[name] = fn
        return fn
    return deco


@check("/feed")
def _check_feed(d: dict) -> list[str]:
    errs: list[str] = []
    items = d.get("items") or []
    if not items:
        errs.append("items 为空")
        return errs
    need = ("repo_id", "full_name", "tags", "topics", "score", "reason")
    for i, it in enumerate(items[:5]):
        for k in need:
            if k not in it:
                errs.append(f"item[{i}] 缺字段 {k}")
        if not it.get("tags"):
            errs.append(f"item[{i}] tags 为空（展示层过滤不该把标签清空）")
        # 富化字段：stats 必须存在（2026-09-18 就是它全丢）
        if not it.get("stats"):
            errs.append(f"item[{i}] stats 缺失（富化失败？看 meta.enrich_error）")
        elif not (it["stats"].get("forks") is not None):
            errs.append(f"item[{i}].stats.forks 缺失")
    if d.get("meta", {}).get("enrich_error"):
        errs.append(f"meta.enrich_error = {d['meta']['enrich_error']}")
    return errs


@check("/repos/{id}")
def _check_repo(d: dict) -> list[str]:
    errs: list[str] = []
    for k in ("id", "full_name", "stars", "topics"):
        if k not in d:
            errs.append(f"缺字段 {k}")
    if "readme_md" not in d:
        errs.append("缺字段 readme_md（with_readme=true 时应下发）")
    return errs


@check("/translate/status")
def _check_translate(d: dict) -> list[str]:
    errs: list[str] = []
    chain = d.get("chain") or []
    if not chain:
        errs.append("chain 为空（模型链没配？）")
    if not any(m.get("configured") for m in chain):
        errs.append("链上没有任何可用模型（key 没配？）")
    return errs


@check("/ml/status")
def _check_ml(d: dict) -> list[str]:
    errs: list[str] = []
    if not d.get("embeddings"):
        errs.append("embeddings 缺失")
    elif not d["embeddings"].get("count"):
        errs.append("向量库为空（跑 jobs/train_personal_model.py 或 /ml/embed）")
    return errs


@check("/user/profile")
def _check_profile(d: dict) -> list[str]:
    # ⚠️ 只校验 /user/profile（不要匹配到 /user/profile/summary —— 后者返回 {insight:…}）
    errs: list[str] = []
    if "interests" not in d:
        errs.append("缺 interests")
    for it in (d.get("interests") or []):
        if "tag" not in it or "weight" not in it:
            errs.append("interests 条目缺 tag/weight")
            break
    return errs


# 这些接口天生"不是 200"或需要外部条件，冒烟测试不算失败
_EXPECTED_NON200 = {
    "/api/auth/me": "需要会话",
    "/api/auth/github/callback": "需要 GitHub 回调参数 code",
    "/api/auth/github/login": "跳转 GitHub（本机网络可能不通）",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="后端接口冒烟测试")
    ap.add_argument("--user", type=int, default=3)
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--only", default=None, help="只跑路径包含该子串的接口")
    args = ap.parse_args()
    base, uid = args.base.rstrip("/"), args.user
    # ⚠️ OpenAPI 里的 path 已包含 /api 前缀（router 带 prefix），
    #    所以调用要用「服务根」+ path，不能用 base 再拼一次 → 否则 /api/api/feed 全 404
    root = base[:-4] if base.endswith("/api") else base

    try:
        # ⚠️ OpenAPI schema 在服务**根路径**（/openapi.json），不在 /api 前缀下
        spec_url = base[:-4] + "/openapi.json" if base.endswith("/api") else f"{base}/openapi.json"
        spec = requests.get(spec_url, timeout=15).json()
        if "paths" not in spec:
            print(f"✗ openapi.json 结构异常（{spec_url}）：{str(spec)[:120]}")
            return 1
    except Exception as e:  # noqa: BLE001
        print(f"✗ 拿不到 openapi.json（后端没起？）：{type(e).__name__}")
        return 1

    # 需要真实参数的接口 → 先取一个可用 repo_id
    repo_id = None
    try:
        f = requests.get(f"{base}/feed", params={"user_id": uid, "limit": 1}, timeout=60).json()
        repo_id = (f.get("items") or [{}])[0].get("repo_id")
    except Exception:
        pass

    # 参数表：路径 → query/替换值
    def params_for(path: str) -> tuple[str, dict]:
        p = path.replace("/repos/{repo_id}", f"/repos/{repo_id or 1}")
        q: dict[str, Any] = {}
        if "{repo_id}" in path and not repo_id:
            q = {}
        if "user_id" in str(spec["paths"][path].get("get", {}).get("parameters", "")):
            q["user_id"] = uid
        if path in ("/feed",):
            q = {"user_id": uid, "limit": 3}
        if path.startswith("/repos/{repo_id}") and "with_readme" in str(spec["paths"][path]):
            q["user_id"] = uid
            q["with_readme"] = "true"
        if path == "/search":
            q = {"q": "tts", "limit": 3}
        if path == "/search/suggest":
            q = {"prefix": "rvc"}
        return p, q

    rows, failed = [], 0
    for path in sorted(spec["paths"]):
        if args.only and args.only not in path:
            continue
        op = spec["paths"][path].get("get")
        if not op:                     # 只测 GET
            continue
        p, q = params_for(path)
        url = f"{root}{p}"            # 用服务根拼接（path 自带 /api）
        name = path.lstrip("/")
        try:
            r = requests.get(url, params=q, timeout=90)
            status = r.status_code
            note = ""
            if status == 200:
                try:
                    body = r.json()
                except Exception:
                    body = None
                # /user/profile/summary 不能被 /user/profile 的校验命中
                if path.endswith("/summary"):
                    fn = None
                else:
                    fn = next((v for k, v in CHECKS.items() if k in path), None)
                if fn and isinstance(body, dict):
                    errs = fn(body)
                    note = "；".join(errs) if errs else "字段 ✓"
                    if errs:
                        failed += 1
                else:
                    note = "（无字段校验）"
            elif path in _EXPECTED_NON200:
                # 这些接口天生给非 200（未登录 / 缺参数 / 要跳转外网），不算失败
                note = f"（预期非 200：{_EXPECTED_NON200[path]}）"
            else:
                note = (r.text or "")[:70].replace("\n", " ")
                failed += 1
            rows.append((name, status, note))
        except Exception as e:  # noqa: BLE001
            if path in _EXPECTED_NON200:
                rows.append((name, 0, f"（预期失败：{_EXPECTED_NON200[path]}）"))
            else:
                rows.append((name, 0, f"{type(e).__name__}: {e}"))
                failed += 1

    print(f"{'接口':<34} {'状态':<6} 说明")
    print("-" * 96)
    for name, status, note in rows:
        mark = "✓" if status == 200 and "✗" not in note and "缺失" not in note and "为空" not in note else "✗"
        print(f"{mark} {name:<32} {status:<6} {note[:70]}")

    print("-" * 96)
    print(f"共 {len(rows)} 个 GET 接口，{len(rows)-failed} 通过 / {failed} 有问题")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
