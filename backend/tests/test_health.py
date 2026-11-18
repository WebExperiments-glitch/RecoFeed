"""应用装配的集成闸门：FastAPI 能起来、健康检查与 OpenAPI 正常。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app


@pytest.fixture(scope="module")
def client():
    # with 语句会触发 lifespan：建库 / 从 seed.db 初始化 / jieba 预热
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["repos"] > 0


def test_openapi_contains_feed(client: TestClient) -> None:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert "/api/feed" in resp.json()["paths"]
