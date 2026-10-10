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


def test_spa_fallback(migrated_settings: Settings, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>spa</html>")
    settings = migrated_settings.model_copy(update={"frontend_dist": dist})

    with TestClient(create_app(settings)) as client:
        response = client.get("/reading")
        assert response.status_code == 200
        assert "spa" in response.text
        assert client.get("/api/does-not-exist").status_code == 404


def test_cache_headers(migrated_settings: Settings, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>")
    (dist / "sw.js").write_text("// sw")
    (dist / "assets" / "index-abc123.js").write_text("// app")
    (dist / "favicon.svg").write_text("<svg/>")
    settings = migrated_settings.model_copy(update={"frontend_dist": dist})

    with TestClient(create_app(settings)) as client:
        # The files that pick the build are always revalidated, also through the SPA fallback.
        for path in ("/", "/index.html", "/sw.js", "/reading"):
            assert client.get(path).headers["cache-control"] == "no-cache", path
        hashed = client.get("/assets/index-abc123.js").headers["cache-control"]
        assert "immutable" in hashed
        assert "cache-control" not in client.get("/favicon.svg").headers
