"""Serving the built single-page frontend."""

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

# Files that decide which build the browser runs: always revalidated, so that a deploy reaches
# the installed PWA instead of the service worker's cached copy of the old build.
NO_CACHE = {"index.html", "sw.js", "registerSW.js", "manifest.webmanifest"}
# Vite puts a content hash in every file name under assets/: safe to cache forever.
IMMUTABLE_PREFIX = "assets/"


class SPAStaticFiles(StaticFiles):
    """Static files with a single-page-app fallback.

    Unknown paths are answered with `index.html`, so client-side routes such as `/reading`
    survive a full page reload. Paths under `/api/` keep their 404.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or path == "api" or path.startswith("api/"):
                raise
            path = "index.html"
            response = await super().get_response(path, scope)
        if path in NO_CACHE or path in ("", "."):
            response.headers["Cache-Control"] = "no-cache"
        elif path.startswith(IMMUTABLE_PREFIX):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
