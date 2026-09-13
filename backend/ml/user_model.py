"""用户塔（User Tower）+ 个人微调（Personal MLP）。

⭐ 这就是"为你一个人训练一个微型 AI 模型"的实现。

   ① 用户塔（免训练，秒级）
      把用户的三类证据按权重聚合成一个向量：
        自建仓库 0.5（用户亲手在做）> 点星仓库 0.4（明确认可）> 深读/点赞 0.3（注意力）
      做法：证据仓库的 embedding 加权平均 → L2 归一化 = 用户语义向量 u

   ② 个人模型（本地微调，CPU 100 epoch < 3 秒）
      MLP：输入 = item_vec ⊙ u（512 维语义交互）+ 5 维标量特征 → 隐藏 128 → 输出 1（sigmoid）
      特征里的标量：语义相似度、星数、遗珠分、新鲜度、证据权重
      标签：正 = 自建/点星/深读/点赞；负 = 划过/不感兴趣（按来源加权）
      产出：backend/models/personal_model_u{user_id}.pth（几百 KB，"你的兴趣灵魂"）

   ③ 为什么这样就够了
      只有一个人、几百到几万条数据 —— 不需要大模型，不需要 GPU，
      一个 512→128→1 的 MLP 就能把"你到底喜欢什么"编码进去，
      而且每次交互都能在几秒内重训，永远跟得上你的兴趣漂移。
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ml import embedder

_BACKEND_DIR = Path(__file__).resolve().parent.parent
MODEL_DIR = _BACKEND_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# 证据权重（用户拍定）
EVIDENCE_WEIGHTS = {"owned": 0.50, "starred": 0.40, "read": 0.30, "like": 0.20}
HIDDEN_DIM = 128
SCALAR_DIM = 5
EPOCHS_DEFAULT = 100
LR_DEFAULT = 3e-3
SEED = 42


def model_path(user_id: int) -> Path:
    return MODEL_DIR / f"personal_model_u{user_id}.pth"


# ---------------------------------------------------------------- 用户塔

def collect_evidence(conn: sqlite3.Connection, user_id: int) -> list[tuple[int, float, str]]:
    """收集用户证据 → [(repo_id, weight, source)]。

    来源：
      · github_footprint（自建 0.5 / 点星 0.4，权重由 auth_service 按规则算好）
      · user_events 的 deep_read → 0.3，like → 0.2
    """
    out: dict[int, tuple[float, str]] = {}

    # ① GitHub 实证圈（最强）：仓库名 → repos.id 匹配
    try:
        rows = conn.execute(
            """SELECT f.source, f.weight, r.id AS repo_id
               FROM github_footprint f
               JOIN repos r ON r.full_name = f.full_name
               WHERE f.user_id = ?""",
            (user_id,),
        ).fetchall()
        for r in rows:
            rid = int(r["repo_id"])
            w = float(r["weight"])
            if rid not in out or w > out[rid][0]:
                out[rid] = (w, r["source"])
    except sqlite3.OperationalError:
        pass

    # ② 本地行为：深读 / 点赞
    rows = conn.execute(
        """SELECT repo_id, event_type, COUNT(*) AS n
           FROM user_events
           WHERE user_id = ? AND event_type IN ('deep_read', 'like')
           GROUP BY repo_id, event_type""",
        (user_id,),
    ).fetchall()
    for r in rows:
        rid = int(r["repo_id"])
        base = EVIDENCE_WEIGHTS["read"] if r["event_type"] == "deep_read" else EVIDENCE_WEIGHTS["like"]
        # 多次深读最多放大到 1.5 倍
        w = round(min(base * (1 + 0.1 * (int(r["n"]) - 1)), base * 1.5), 3)
        if rid not in out or w > out[rid][0]:
            out[rid] = (w, r["event_type"])

    return [(rid, w, src) for rid, (w, src) in out.items()]


def build_user_vector(conn: sqlite3.Connection,
                      user_id: int) -> tuple[np.ndarray | None, dict[str, Any]]:
    """用户塔：证据仓库 embedding 的加权平均 → 用户语义向量。"""
    evidence = collect_evidence(conn, user_id)
    ids = [rid for rid, _, _ in evidence]
    if not ids:
        return None, {"evidence": 0, "note": "没有证据（先登录同步 GitHub 或刷几条）"}

    emb = {rid: embedder.load_one(conn, rid) for rid in ids}
    usable = [(rid, w) for rid, w, _ in evidence if emb.get(rid) is not None]
    if not usable:
        return None, {"evidence": len(ids), "usable": 0,
                      "note": "证据仓库还没有向量，请先构建 embedding"}

    dim = len(next(v for v in emb.values() if v is not None))
    acc = np.zeros(dim, dtype=np.float32)
    wsum = 0.0
    for rid, w in usable:
        acc += w * np.asarray(emb[rid], dtype=np.float32)
        wsum += w
    acc /= max(wsum, 1e-6)
    n = float(np.linalg.norm(acc))
    if n > 0:
        acc /= n

    by_src: dict[str, int] = {}
    for _, _, src in evidence:
        by_src[src] = by_src.get(src, 0) + 1
    return acc, {
        "evidence": len(ids), "usable": len(usable), "dim": dim,
        "by_source": by_src,
        "weight_sum": round(wsum, 2),
    }


# ---------------------------------------------------------------- 训练数据

def _scalars(conn: sqlite3.Connection, rid: int, cos: float,
             evidence_map: dict[int, float]) -> np.ndarray:
    row = conn.execute(
        """SELECT stars, forgotten_score, freshness_score, quality_score
           FROM repos WHERE id = ?""",
        (rid,),
    ).fetchone()
    if not row:
        return np.zeros(SCALAR_DIM, dtype=np.float32)
    stars_norm = min(1.0, np.log10(int(row["stars"] or 0) + 1) / 5.3)
    return np.array([
        cos,
        stars_norm,
        float(row["forgotten_score"] or 0.0),
        float(row["freshness_score"] or 0.0),
        float(evidence_map.get(rid, 0.0)),
    ], dtype=np.float32)


def collect_training_data(conn: sqlite3.Connection, user_id: int,
                          user_vec: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """构造 (X, y, sample_weight)。

    正例：实证圈 + 深读 + 点赞；负例：划过(skip) + 不感兴趣(dislike)
    没有互动的仓库不进训练集（避免把"没看过"当"不喜欢"）。
    """
    evidence = {rid: w for rid, w, _ in collect_evidence(conn, user_id)}

    rows = conn.execute(
        """SELECT repo_id, event_type, COUNT(*) AS n FROM user_events
           WHERE user_id = ? AND event_type IN ('deep_read','like','skip','dislike')
           GROUP BY repo_id, event_type""",
        (user_id,),
    ).fetchall()
    labels: dict[int, float] = {}
    weights: dict[int, float] = {}
    for r in rows:
        rid, et = int(r["repo_id"]), r["event_type"]
        if et in ("deep_read", "like"):
            labels[rid], weights[rid] = 1.0, 0.3
        elif et == "dislike":
            labels[rid], weights[rid] = 0.0, 0.6      # 明确负反馈权重更高
        else:  # skip
            labels.setdefault(rid, 0.0)
            weights.setdefault(rid, 0.15)             # 划过是弱负例

    for rid, w in evidence.items():
        labels[rid] = 1.0
        weights[rid] = max(weights.get(rid, 0.0), w)   # 实证圈是强正例

    # ⚠️ 隐式负例采样：新登录用户往往只有正样本（全 1 的标签会让 MLP 退化成常数输出）。
    #    做法：从「没进证据、也没互动过」的仓库里随机采样，标 0、低权重（0.1）——
    #    这是隐式反馈推荐的标准做法（类似 ALS 的置信度加权）。
    import random as _random
    rng = _random.Random(SEED)
    all_ids = [int(r["id"]) for r in conn.execute("SELECT id FROM repos")]
    seen = set(labels)
    pool = [rid for rid in all_ids if rid not in seen]
    n_neg = min(len(pool), max(30, 2 * len(labels)))
    for rid in rng.sample(pool, n_neg) if pool else []:
        labels[rid] = 0.0
        weights[rid] = 0.1

    ids = [rid for rid in labels if embedder.load_one(conn, rid) is not None]
    in_dim = embedder.EMBED_DIM + SCALAR_DIM
    if not ids:
        return (np.zeros((0, in_dim), dtype=np.float32),
                np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32), {})

    feats = []
    for rid in ids:
        v = np.asarray(embedder.load_one(conn, rid), dtype=np.float32)
        cos = float(np.dot(v, user_vec))
        feats.append(np.concatenate([v * user_vec, _scalars(conn, rid, cos, evidence)]))
    X = np.vstack(feats).astype(np.float32)
    y = np.array([labels[i] for i in ids], dtype=np.float32)
    sw = np.array([weights[i] for i in ids], dtype=np.float32)
    meta = {
        "n": len(ids),
        "pos": int((y > 0.5).sum()),
        "neg": int((y <= 0.5).sum()),
        "from_evidence": len(evidence),
    }
    return X, y, sw, meta


# ---------------------------------------------------------------- 个人 MLP

def _torch():
    import torch  # type: ignore
    return torch


def build_mlp(in_dim: int):
    torch = _torch()
    torch.manual_seed(SEED)
    return torch.nn.Sequential(
        torch.nn.Linear(in_dim, HIDDEN_DIM),
        torch.nn.ReLU(),
        torch.nn.Linear(HIDDEN_DIM, 1),
    )


def train(conn: sqlite3.Connection, user_id: int, *, epochs: int = EPOCHS_DEFAULT,
          lr: float = LR_DEFAULT, verbose: bool = True) -> dict[str, Any]:
    """在本地 CPU 上微调个人模型并保存 .pth。"""
    torch = _torch()
    torch.manual_seed(SEED)

    user_vec, vinfo = build_user_vector(conn, user_id)
    if user_vec is None:
        return {"ok": False, "reason": vinfo.get("note", "无法构建用户向量"), "vector": vinfo}

    X, y, sw, dmeta = collect_training_data(conn, user_id, user_vec)
    if len(X) < 10:
        return {"ok": False, "reason": f"训练样本不足（{len(X)} 条，至少 10 条）",
                "vector": vinfo, "data": dmeta}

    # 标准化（保存 mu/sd，推理时要一致）
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd < 1e-6] = 1.0
    Xn = (X - mu) / sd

    xt = torch.from_numpy(Xn)
    yt = torch.from_numpy(y).unsqueeze(1)
    wt = torch.from_numpy(sw).unsqueeze(1)
    model = build_mlp(X.shape[1])
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    lossfn = torch.nn.BCEWithLogitsLoss(reduction="none")

    # 留出 20% 评估（固定划分，保证可比）
    rng = np.random.default_rng(SEED)
    idx = rng.permutation(len(X))
    split = max(1, int(len(X) * 0.8))
    tr, te = idx[:split], idx[split:]
    xt_tr, yt_tr, wt_tr = xt[tr], yt[tr], wt[tr]

    t0 = time.perf_counter()
    model.train()
    for _ in range(epochs):
        opt.zero_grad()
        logits = model(xt_tr)
        loss = (lossfn(logits, yt_tr) * wt_tr).mean()
        loss.backward()
        opt.step()
    seconds = time.perf_counter() - t0

    # 评估
    model.eval()
    with torch.no_grad():
        p_all = torch.sigmoid(model(xt)).squeeze(1).numpy()
    metrics: dict[str, Any] = {"seconds": round(seconds, 2), "epochs": epochs, "n": len(X),
                               "pos_rate": round(float(y.mean()), 3), **dmeta}

    if len(te) >= 4 and len(set(y[te].tolist())) > 1:
        p_te, y_te = p_all[te], y[te]
        pos, neg = p_te[y_te > 0.5], p_te[y_te <= 0.5]
        auc = float(np.mean([(a > b) + 0.5 * (a == b) for a in pos for b in neg])) if len(pos) and len(neg) else float("nan")
        acc = float(((p_te > 0.5).astype(float) == y_te).mean())
        metrics.update({"auc_holdout": round(auc, 3), "acc_holdout": round(acc, 3),
                        "n_holdout": int(len(te))})

    path = model_path(user_id)
    torch.save({
        "state_dict": model.state_dict(),
        "in_dim": int(X.shape[1]),
        "mu": mu.astype(np.float32),
        "sd": sd.astype(np.float32),
        "user_vec": user_vec.astype(np.float32),
        "meta": {
            "user_id": user_id, "model": embedder.active_tag(),
            "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "epochs": epochs, "metrics": metrics, "vector_info": vinfo,
        },
    }, str(path))
    size_kb = path.stat().st_size / 1024
    metrics.update({"model_path": str(path), "size_kb": round(size_kb, 1)})
    if verbose:
        print(f"  ✓ {path.name} 已保存（{size_kb:.0f} KB，训练 {seconds:.2f}s）")
    return {"ok": True, "metrics": metrics, "vector": vinfo}


def load(conn: sqlite3.Connection, user_id: int):
    """加载个人模型（没有则 None）。"""
    path = model_path(user_id)
    if not path.exists():
        return None
    torch = _torch()
    blob = torch.load(str(path), map_location="cpu", weights_only=False)
    model = build_mlp(int(blob["in_dim"]))
    model.load_state_dict(blob["state_dict"])
    model.eval()
    return {"model": model, "mu": blob["mu"], "sd": blob["sd"],
            "user_vec": blob["user_vec"], "meta": blob.get("meta", {})}


def score_repos(conn: sqlite3.Connection, user_id: int,
                repo_ids: list[int]) -> dict[int, float]:
    """用个人模型给仓库打分（0~1 的"这个用户会喜欢"概率）。"""
    packed = load(conn, user_id)
    if not packed or not repo_ids:
        return {}
    torch = _torch()
    u = np.asarray(packed["user_vec"], dtype=np.float32)
    evidence = {rid: w for rid, w, _ in collect_evidence(conn, user_id)}
    rows: list[np.ndarray] = []
    keep: list[int] = []
    for rid in repo_ids:
        v = embedder.load_one(conn, rid)
        if v is None:
            continue
        v = np.asarray(v, dtype=np.float32)
        cos = float(np.dot(v, u))
        rows.append(np.concatenate([v * u, _scalars(conn, rid, cos, evidence)]))
        keep.append(rid)
    if not rows:
        return {}
    X = np.vstack(rows).astype(np.float32)
    Xn = (X - packed["mu"]) / packed["sd"]
    with torch.no_grad():
        p = torch.sigmoid(packed["model"](torch.from_numpy(Xn))).squeeze(1).numpy()
    return {rid: float(pi) for rid, pi in zip(keep, p)}


def status(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    path = model_path(user_id)
    out: dict[str, Any] = {
        "embeddings": {"count": embedder.count_embedded(conn),
                       "dim": embedder.EMBED_DIM,
                       "model": embedder.active_tag(),
                       "backend": embedder.backend_kind()},
        "user_model": {"exists": path.exists(), "path": str(path)},
        "evidence": {"count": len(collect_evidence(conn, user_id))},
    }
    if path.exists():
        try:
            torch = _torch()
            blob = torch.load(str(path), map_location="cpu", weights_only=False)
            out["user_model"].update({
                "size_kb": round(path.stat().st_size / 1024, 1),
                **{k: v for k, v in (blob.get("meta") or {}).items() if k != "vector_info"},
            })
        except Exception as e:  # noqa: BLE001
            out["user_model"]["error"] = str(e)
    return out
