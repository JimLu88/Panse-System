"""幂等中间件测试: 同 key 重复 → 409 且业务只执行一次; 无 key / 不同 key 正常放行。"""
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.idempotency import IdempotencyMiddleware


def _app():
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware)
    calls = {"n": 0}

    @app.post("/do")
    def do():
        calls["n"] += 1
        return {"ok": True, "n": calls["n"]}

    @app.post("/fail")
    def fail():
        raise HTTPException(400, "boom")

    return app, calls


def test_duplicate_key_blocked_and_runs_once():
    app, calls = _app()
    c = TestClient(app)
    assert c.post("/do", headers={"Idempotency-Key": "k1"}).status_code == 200
    r2 = c.post("/do", headers={"Idempotency-Key": "k1"})
    assert r2.status_code == 409
    assert r2.json().get("idempotent") is True
    assert calls["n"] == 1   # 第二次没真正执行


def test_no_key_and_different_keys_pass():
    app, calls = _app()
    c = TestClient(app)
    assert c.post("/do").status_code == 200
    assert c.post("/do").status_code == 200
    assert c.post("/do", headers={"Idempotency-Key": "a"}).status_code == 200
    assert c.post("/do", headers={"Idempotency-Key": "b"}).status_code == 200
    assert calls["n"] == 4


def test_failed_first_request_releases_key():
    app, _ = _app()
    c = TestClient(app)
    # 首次失败 (4xx) → key 释放, 同 key 可重试
    assert c.post("/fail", headers={"Idempotency-Key": "kf"}).status_code == 400
    assert c.post("/fail", headers={"Idempotency-Key": "kf"}).status_code == 400
