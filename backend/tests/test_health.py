from collections.abc import Iterator

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
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


def test_health_reports_provider_and_task_routing(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["llm"] == "fake"
    assert body["llm_tasks"]["grade_sentence"] == "fake/fake"
    assert set(body["llm_tasks"]) == {
        "generate_exercise",
        "grade_sentence",
        "explain",
        "simplify_text",
        "gloss",
    }


def test_health_with_a_provider_and_routing(migrated_settings: Settings) -> None:
    from pydantic import SecretStr

    settings = migrated_settings.model_copy(
        update={
            "llm_provider": "openai",
            "llm_model": "gpt-x",
            "openai_api_key": SecretStr("dummy"),
            "gemini_api_key": SecretStr("dummy"),
            "llm_tasks": {"gloss": {"provider": "google", "model": "gemini-x"}},
        }
    )
    with TestClient(create_app(settings)) as test_client:
        body = test_client.get("/api/health").json()
    assert body["llm"] == "openai"
    assert body["llm_tasks"]["gloss"] == "google/gemini-x"
    assert body["llm_tasks"]["explain"] == "openai/gpt-x"
