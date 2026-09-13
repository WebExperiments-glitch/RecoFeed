"""ML 任务的进程内进度状态（单机单用户，不需要队列系统）。

用途：向量化 / 训练这类慢操作放到后台线程跑，前端轮询问进度。
"""
from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_state: dict[str, Any] = {
    "embed": {"running": False, "done": 0, "total": 0, "error": None, "finished_at": None},
    "train": {"running": False, "error": None, "result": None, "finished_at": None},
}


def set_embed(**kw: Any) -> None:
    with _lock:
        _state["embed"].update(kw)


def set_train(**kw: Any) -> None:
    with _lock:
        _state["train"].update(kw)


def snapshot() -> dict[str, Any]:
    with _lock:
        return {k: dict(v) for k, v in _state.items()}
