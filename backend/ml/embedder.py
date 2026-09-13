"""物品塔（Item Tower）：用本地 Embedding 模型把每个仓库变成"语义指纹"。

模型选型（魔搭 ModelScope）：
    BAAI/bge-small-zh-v1.5 —— 中文场景最轻最好用的开源 embedding
    · 512 维、~100MB、CPU 上毫秒级，普通笔记本就能跑
    · 用户原方案说 384 维（all-MiniLM-L6-v2），但那个对中文支持差；
      本项目是中文优先 + 大量中英混排技术词，所以选同样轻量的 bge-small-zh

存储：
    repo_embeddings 表，向量用 BLOB 存 float32 —— 零外部依赖（不需要 chroma/faiss）
    258 个仓库 × 512 维 × 4B ≈ 0.5MB，SQLite 完全够用；
    未来 1 万个仓库 ≈ 20MB，也仍然不需要向量数据库。

降级策略：
    没装 sentence-transformers 时，用 numpy/scipy 的 TF-IDF + SVD（LSA）兜底，
    接口完全一致 —— 保证离线环境也能跑通整条链路（只是语义质量弱一些）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable

import numpy as np

_BACKEND_DIR = Path(__file__).resolve().parent.parent
# 模型缓存目录（随项目，便于打包与离线；已 gitignore）
MODEL_DIR = _BACKEND_DIR / "models" / "bge-small-zh-v1.5"

MODEL_ID_MS = "BAAI/bge-small-zh-v1.5"          # 魔搭官方 ID
MODEL_ID_MS_ALT = "AI-ModelScope/bge-small-zh-v1.5"   # 魔搭镜像 ID
MODEL_NAME = "bge-small-zh-v1.5"
EMBED_DIM = 512

# 送进 embedding 的文本长度上限（README 摘要，太长无益且拖慢 CPU）
MAX_TEXT_CHARS = 600

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS repo_embeddings (
  repo_id    INTEGER PRIMARY KEY REFERENCES repos(id) ON DELETE CASCADE,
  model      TEXT    NOT NULL,
  dim        INTEGER NOT NULL,
  vec        BLOB    NOT NULL,          -- float32 小端连续内存
  updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_embed_model ON repo_embeddings(model);
"""

_model = None          # 懒加载的 SentenceTransformer
_backend_kind = "unknown"   # "st" = sentence-transformers / "lsa" = numpy 兜底


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(_TABLE_SQL)


# ---------------------------------------------------------------- 模型加载

def model_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except Exception:
        return False


def download_model(force: bool = False) -> Path:
    """用魔搭下载模型到项目内 MODEL_DIR（HF 在国内不通，走魔搭）。"""
    if MODEL_DIR.exists() and any(MODEL_DIR.iterdir()) and not force:
        return MODEL_DIR
    from modelscope import snapshot_download  # type: ignore

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for mid in (MODEL_ID_MS, MODEL_ID_MS_ALT):
        try:
            snapshot_download(mid, local_dir=str(MODEL_DIR))
            return MODEL_DIR
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"魔搭下载失败：{last_err}")


def get_model():
    """懒加载 embedding 模型（只用本地目录，永不联网）。"""
    global _model, _backend_kind
    if _model is not None:
        return _model
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")   # 强制离线，避免误连 HF
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from sentence_transformers import SentenceTransformer  # type: ignore

    path = MODEL_DIR if MODEL_DIR.exists() else download_model()
    _model = SentenceTransformer(str(path), device="cpu")
    _backend_kind = "st"
    return _model


def backend_kind() -> str:
    if _backend_kind != "unknown":
        return _backend_kind
    return "st" if model_available() else "lsa"


def active_tag() -> str:
    """当前实际使用的向量标签。

    ⚠️ 必须区分标签：LSA 兜底向量与 bge 语义向量不可混用，
       否则换了模型后增量逻辑会误以为"已向量化"而跳过重建。
    """
    return MODEL_NAME if model_available() else "lsa-tfidf"


# ---------------------------------------------------------------- 文本构造

def build_item_text(row: dict[str, Any]) -> str:
    """仓库 → 送进 embedding 的文本。

    组成：名称 + 描述 + topics + 高权重标签 + README 摘要（截断）
    —— 与召回/标签用的是同一批信息，保证语义与结构化信号同源。
    """
    parts: list[str] = []
    name = row.get("full_name") or row.get("name") or ""
    if name:
        parts.append(str(name))
    desc = (row.get("description") or "").strip()
    if desc:
        parts.append(desc)
    topics = row.get("topics")
    if isinstance(topics, str):
        try:
            topics = json.loads(topics or "[]")
        except json.JSONDecodeError:
            topics = []
    if topics:
        parts.append(" ".join(str(t) for t in topics[:12]))

    tags = row.get("tags_json")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags or "{}")
        except json.JSONDecodeError:
            tags = {}
    if isinstance(tags, dict) and tags:
        top = sorted(tags.items(), key=lambda kv: -float(kv[1]))[:12]
        parts.append(" ".join(str(k) for k, _ in top))

    readme = (row.get("readme_md") or "").strip()
    if readme:
        parts.append(readme[:MAX_TEXT_CHARS])
    return "\n".join(parts)[:2000]


# ---------------------------------------------------------------- 向量化

def embed_texts(texts: list[str], batch_size: int = 32) -> np.ndarray:
    """文本 → L2 归一化向量矩阵 (n, dim)。"""
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)

    if model_available():
        model = get_model()
        vecs = model.encode(
            texts, batch_size=batch_size, normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vecs, dtype=np.float32)

    # ── 兜底：TF-IDF + SVD（LSA），deps 只有 scipy/numpy ──
    from ml.sklearn_lite import lsa_embed
    return lsa_embed(texts, dim=EMBED_DIM)


def embed_one(text: str) -> np.ndarray:
    return embed_texts([text])[0]


# ---------------------------------------------------------------- 存取

def _pack(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def _unpack(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32, count=dim).copy()


def count_embedded(conn: sqlite3.Connection) -> int:
    ensure_table(conn)
    return conn.execute(
        "SELECT COUNT(*) AS n FROM repo_embeddings WHERE model = ?", (active_tag(),)
    ).fetchone()["n"]


def build_all(
    conn: sqlite3.Connection,
    *,
    force: bool = False,
    limit: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """给全部仓库生成 embedding（增量：默认跳过已有的）。"""
    ensure_table(conn)
    tag = active_tag()
    if force:
        conn.execute("DELETE FROM repo_embeddings WHERE model = ?", (tag,))
    done = {
        r["repo_id"] for r in conn.execute(
            "SELECT repo_id FROM repo_embeddings WHERE model = ?", (tag,)
        )
    }
    rows = conn.execute(
        """SELECT id, full_name, name, description, topics, tags_json, readme_md
           FROM repos ORDER BY id"""
    ).fetchall()
    todo = [r for r in rows if r["id"] not in done]
    if limit:
        todo = todo[:limit]
    if not todo:
        return {"embedded": 0, "total": len(rows), "skipped": len(rows), "dim": EMBED_DIM}

    texts = [build_item_text(dict(r)) for r in todo]
    n_done = 0
    step = 64
    for i in range(0, len(texts), step):
        chunk_rows = todo[i:i + step]
        chunk_texts = texts[i:i + step]
        vecs = embed_texts(chunk_texts)
        for r, v in zip(chunk_rows, vecs):
            conn.execute(
                """INSERT INTO repo_embeddings (repo_id, model, dim, vec, updated_at)
                   VALUES (?,?,?,?, datetime('now'))
                   ON CONFLICT(repo_id) DO UPDATE SET
                       model = excluded.model, dim = excluded.dim,
                       vec = excluded.vec, updated_at = excluded.updated_at""",
                (r["id"], tag, EMBED_DIM, _pack(v)),
            )
        conn.commit()
        n_done += len(chunk_rows)
        if progress:
            progress(n_done, len(todo))
    return {"embedded": n_done, "total": len(rows), "skipped": len(rows) - len(todo),
            "dim": EMBED_DIM, "backend": backend_kind()}


def load_all(conn: sqlite3.Connection) -> tuple[list[int], np.ndarray]:
    """读全部向量 → (repo_ids, matrix)。"""
    ensure_table(conn)
    rows = conn.execute(
        "SELECT repo_id, dim, vec FROM repo_embeddings WHERE model = ?", (active_tag(),)
    ).fetchall()
    if not rows:
        return [], np.zeros((0, EMBED_DIM), dtype=np.float32)
    ids = [int(r["repo_id"]) for r in rows]
    mat = np.vstack([_unpack(r["vec"], int(r["dim"])) for r in rows])
    return ids, mat


def load_one(conn: sqlite3.Connection, repo_id: int) -> np.ndarray | None:
    ensure_table(conn)
    row = conn.execute(
        "SELECT dim, vec FROM repo_embeddings WHERE repo_id = ? AND model = ?",
        (repo_id, active_tag()),
    ).fetchone()
    if not row:
        return None
    return _unpack(row["vec"], int(row["dim"]))


def cosine_topk(conn: sqlite3.Connection, query_vec: np.ndarray,
                k: int = 50) -> list[tuple[int, float]]:
    """余弦召回（向量已归一化 → 点积即余弦）。"""
    ids, mat = load_all(conn)
    if not ids:
        return []
    sims = mat @ np.asarray(query_vec, dtype=np.float32)
    order = np.argsort(-sims)[:k]
    return [(ids[i], float(sims[i])) for i in order]
