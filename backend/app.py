"""RecoFeed 后端服务入口。

启动：
    python backend/app.py
    # 或
    uvicorn app:app --reload --port 8000        （在 backend/ 目录下）

设计说明：
- 这是"后端服务器"（前端服务器是 Vite dev server，端口 5173）
- 本地双服务器架构：前端 5173 只管界面，后端 8000 只管算法与数据
- 用户数据落 SQLite（backend/data/recofeed.db），WAL 模式
"""
from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

# 允许 `python backend/app.py` 与 `uvicorn app:app` 两种启动方式都能 import 到包
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from api.routes import router  # noqa: E402
from core.config import (CORS_ORIGINS, CORS_ORIGIN_REGEX, DATA_DIR,  # noqa: E402
                         DB_PATH, HOST, PORT)
from db.connection import init_db  # noqa: E402
from tags.extractor import init_jieba  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("recofeed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动前：建库 + 预热分词器；关闭时：无。"""
    t0 = time.perf_counter()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── 数据集引导 ──
    # 仓库里提交的是**脱敏快照** recofeed.seed.db（不含任何令牌/会话/用户行为），
    # 运行库 recofeed.db 属于本机数据、不入库。
    # 首次 clone 后没有运行库 → 自动从快照复制一份，开箱即用。
    seed_path = DB_PATH.parent / "recofeed.seed.db"
    if not DB_PATH.exists() and seed_path.exists():
        import shutil
        shutil.copy2(seed_path, DB_PATH)
        log.info("已从脱敏快照初始化运行库 → %s", DB_PATH)

    init_db()
    log.info("数据库就绪 → %s", DB_PATH)
    init_jieba()
    log.info("jieba 词典预热完成（%.2fs）", time.perf_counter() - t0)
    yield
    log.info("RecoFeed 后端已关闭")


app = FastAPI(
    title="RecoFeed API",
    description=(
        "仿抖音信息流的 GitHub 仓库推荐引擎。\n\n"
        "⚠️ 开发者内部版本（Beta），处于高速提交期，请勿用于有危险的地方。"
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    # 本地 dev server 端口可变（5173 可能落在 Windows 保留段）→ 正则兜住 localhost 任意端口。
    # 统一取自 core.config.CORS_ORIGIN_REGEX（单一来源，别在这里再写死一份 —— 曾经重复
    # 传入 allow_origin_regex 直接 SyntaxError 起不来）。
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    """给每个响应加耗时头，方便前端调试算法性能。"""
    t0 = time.perf_counter()
    response = await call_next(request)
    cost_ms = (time.perf_counter() - t0) * 1000
    response.headers["X-Process-Time-Ms"] = f"{cost_ms:.1f}"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """统一错误响应，避免前端拿到 HTML 报错页。

    ⭐ 带 error_id：把 8 位短 id 同时写进日志与响应体 ——
       用户报"某个页面失败"时，凭这个 id 就能在后端日志里一键定位到完整堆栈。
    """
    import uuid

    error_id = uuid.uuid4().hex[:8]
    log.exception("[%s] 未捕获异常 %s %s", error_id, request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误（后端日志可按 error_id 检索）",
                 "error_id": error_id, "path": request.url.path},
    )


app.include_router(router)


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "name": "RecoFeed API",
        "version": app.version,
        "docs": "/docs",
        "health": "/api/health",
        "notice": "开发者内部版本（Beta），请勿用于生产环境。",
    }


if __name__ == "__main__":
    import uvicorn

    log.info("RecoFeed 后端启动 → http://%s:%s", HOST, PORT)
    log.info("接口文档       → http://%s:%s/docs", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
