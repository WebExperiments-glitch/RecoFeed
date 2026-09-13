"""无 sentence-transformers 时的兜底向量化：TF-IDF + SVD（LSA）。

用途：保证整条链路在"没装 torch / 没下模型"的机器上也能跑通
（语义质量弱于 bge-small-zh，但足以验证架构与重排逻辑）。

依赖：仅 jieba（项目已有）+ numpy + scipy。
"""
from __future__ import annotations

import math
import re

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import svds

from tags.extractor import init_jieba

_TOKEN_RE = re.compile(r"[a-z0-9_+.#\-]{2,}|[\u4e00-\u9fff]{2,}")


def _tokenize(text: str) -> list[str]:
    import jieba
    init_jieba()
    out: list[str] = []
    for w in jieba.lcut(text.lower()):
        w = w.strip()
        if len(w) < 2:
            continue
        if not _TOKEN_RE.fullmatch(w) and not re.search(r"[\u4e00-\u9fff]", w):
            continue
        out.append(w)
    return out


def lsa_embed(texts: list[str], dim: int = 512) -> np.ndarray:
    """语料内 TF-IDF → 截断 SVD → L2 归一化稠密向量。"""
    docs = [_tokenize(t) for t in texts]
    vocab: dict[str, int] = {}
    for toks in docs:
        for t in toks:
            if t not in vocab:
                vocab[t] = len(vocab)
    n, v = len(docs), max(1, len(vocab))
    if v == 0 or n == 0:
        return np.zeros((n, dim), dtype=np.float32)

    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    for i, toks in enumerate(docs):
        tf: dict[int, int] = {}
        for t in toks:
            j = vocab[t]
            tf[j] = tf.get(j, 0) + 1
        for j, c in tf.items():
            rows.append(i)
            cols.append(j)
            vals.append(1.0 + math.log(c))
    x = csr_matrix((vals, (rows, cols)), shape=(n, v), dtype=np.float64)

    df = np.asarray((x > 0).sum(axis=0)).ravel()
    idf = np.log((1 + n) / (1 + df)) + 1.0
    x = x.multiply(idf).tocsr()

    k = int(min(dim, min(x.shape) - 1)) if min(x.shape) > 2 else 1
    if k < 1:
        return np.zeros((n, dim), dtype=np.float32)
    u, s, _ = svds(x, k=k)
    z = u * s
    out = np.zeros((n, dim), dtype=np.float32)
    out[:, :z.shape[1]] = z.astype(np.float32)
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (out / norms).astype(np.float32)
