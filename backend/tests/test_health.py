from collections.abc import Iterator

from fastapi.testclient import TestClient

from app.store.db import get_session


class BrokenSession:
    def execute(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("database unavailable")


def test_health_ok(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"]


def test_health_database_error(client: TestClient) -> None:
    def broken() -> Iterator[BrokenSession]:
        yield BrokenSession()

    client.app.dependency_overrides[get_session] = broken  # type: ignore[attr-defined]
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["database"] == "error"
