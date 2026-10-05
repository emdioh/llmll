from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_static_frontend_served(migrated_settings: Settings, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>hello</html>")
    settings = migrated_settings.model_copy(update={"frontend_dist": dist})

    with TestClient(create_app(settings)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "hello" in response.text
        assert client.get("/api/health").json()["status"] == "ok"
