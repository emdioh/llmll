from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.auth import COOKIE_NAME
from app.config import Settings
from app.main import create_app

from .conftest import Clock

TOKEN = "s3cret-token-value"


def make_client(settings: Settings, clock: Clock, token: str | None = TOKEN, **extra):
    configured = settings.model_copy(
        update={"access_token": SecretStr(token) if token is not None else None, **extra}
    )
    return TestClient(create_app(configured, now=clock))


@pytest.fixture
def secured(migrated_settings: Settings, clock: Clock):
    with make_client(migrated_settings, clock) as c:
        yield c


def login(c: TestClient, token: str = TOKEN):
    return c.post("/api/auth/login", json={"token": token})


def test_open_mode(client: TestClient) -> None:
    assert client.get("/api/health").json()["auth"] == "disabled"
    assert client.get("/api/auth/status").json() == {"auth": "disabled", "authenticated": True}
    assert client.get("/api/queue").status_code != 401
    assert client.get("/api/learner").status_code == 404  # reaches the handler


def test_enabled_blocks_api_but_not_health_or_static(
    migrated_settings: Settings, clock: Clock, tmp_path: Path
) -> None:
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("<html>app</html>")
    with make_client(migrated_settings, clock, frontend_dist=tmp_path / "dist") as c:
        health = c.get("/api/health")
        assert health.status_code == 200
        assert health.json()["auth"] == "enabled"
        assert c.get("/api/auth/status").json() == {"auth": "enabled", "authenticated": False}
        for path in ("/api/learner", "/api/queue", "/api/stats", "/api/settings", "/api/items"):
            resp = c.get(path)
            assert resp.status_code == 401, path
        assert c.post("/api/sessions").status_code == 401
        assert c.get("/").status_code == 200  # static assets stay public


def test_bearer_token(secured: TestClient) -> None:
    ok = secured.get("/api/learner", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 404  # authenticated; no learner yet
    bad = secured.get("/api/learner", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    assert secured.get("/api/learner", headers={"Authorization": TOKEN}).status_code == 401


def test_login_sets_cookie_that_authenticates(secured: TestClient) -> None:
    resp = login(secured)
    assert resp.status_code == 200
    assert resp.json() == {"auth": "enabled", "authenticated": True}
    header = resp.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=strict" in header
    assert f"max-age={90 * 24 * 3600}" in header
    assert "secure" not in header.replace("samesite", "")  # plain http
    cookie = secured.cookies.get(COOKIE_NAME)
    assert cookie and TOKEN not in cookie
    assert secured.get("/api/auth/status").json()["authenticated"] is True
    assert secured.get("/api/learner").status_code == 404


def test_cookie_secure_flag(migrated_settings: Settings, clock: Clock) -> None:
    with make_client(migrated_settings, clock, cookie_secure=True) as c:
        assert "secure" in login(c).headers["set-cookie"].lower().replace("samesite", "")
    with make_client(migrated_settings, clock) as c:
        resp = c.post(
            "https://testserver/api/auth/login",
            json={"token": TOKEN},
        )
        assert "secure" in resp.headers["set-cookie"].lower().replace("samesite", "")


def test_wrong_token(secured: TestClient) -> None:
    resp = login(secured, "wrong")
    assert resp.status_code == 401
    assert COOKIE_NAME not in secured.cookies
    assert secured.get("/api/learner").status_code == 401


def test_forged_cookie_rejected(secured: TestClient) -> None:
    secured.cookies.set(COOKIE_NAME, "deadbeef")
    assert secured.get("/api/learner").status_code == 401
    secured.cookies.set(COOKIE_NAME, TOKEN)  # the raw token is not a valid cookie value
    assert secured.get("/api/learner").status_code == 401


def test_logout(secured: TestClient) -> None:
    login(secured)
    assert secured.get("/api/learner").status_code == 404
    resp = secured.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is False
    assert COOKIE_NAME not in secured.cookies
    assert secured.get("/api/learner").status_code == 401


def test_throttling(secured: TestClient, clock: Clock) -> None:
    for _ in range(4):
        assert login(secured, "bad").status_code == 401
    assert login(secured, "bad").status_code == 401  # 5th failure triggers the lockout
    assert login(secured, "bad").status_code == 429
    assert login(secured).status_code == 429  # even the right token while locked out
    clock.advance(minutes=14)
    assert login(secured).status_code == 429
    clock.advance(minutes=2)
    assert login(secured).status_code == 200


def test_failures_expire_and_success_resets(secured: TestClient, clock: Clock) -> None:
    for _ in range(4):
        login(secured, "bad")
    clock.advance(minutes=16)
    for _ in range(4):
        assert login(secured, "bad").status_code == 401  # window expired, no lockout
    assert login(secured).status_code == 200
    for _ in range(4):
        assert login(secured, "bad").status_code == 401  # success cleared the counter


def test_token_rotation_invalidates_sessions(migrated_settings: Settings, clock: Clock) -> None:
    with make_client(migrated_settings, clock) as old:
        login(old)
        cookie = old.cookies.get(COOKIE_NAME)
    assert cookie
    with make_client(migrated_settings, clock, token="rotated-token") as new:
        new.cookies.set(COOKIE_NAME, cookie)
        assert new.get("/api/auth/status").json()["authenticated"] is False
        assert new.get("/api/learner").status_code == 401
        assert login(new, "rotated-token").status_code == 200
        assert new.get("/api/learner").status_code == 404


def test_login_when_disabled(client: TestClient) -> None:
    resp = login(client, "anything")
    assert resp.status_code == 200
    assert resp.json() == {"auth": "disabled", "authenticated": True}
    assert COOKIE_NAME not in client.cookies


def test_empty_token_means_disabled(migrated_settings: Settings, clock: Clock) -> None:
    with make_client(migrated_settings, clock, token="") as c:
        assert c.get("/api/health").json()["auth"] == "disabled"
