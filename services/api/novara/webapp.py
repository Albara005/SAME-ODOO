"""The built web app (apps/web/dist) served by the API itself, for hosts that give one container (novara/serve.py).

Behind nginx (infrastructure/deployment/nginx.conf) leave NOVARA_WEB_DIST unset: nginx serves the files. Here the
same rules apply: the strict content security policy and the other headers on every page and file, files under
/assets/ cached for good (their names change with their content), everything else revalidated, and any path
that isn't a file gets index.html (the app's own router takes it from there). /api/ and /sites/ never fall
through to the app: an unknown address there is a plain 404.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

# As in nginx.conf: scripts only from this origin, no inline script, no plugins, no framing.
HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; "
                               "base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}

mimetypes.add_type("application/manifest+json", ".webmanifest")


def mount_web(app: FastAPI, dist: str) -> None:
    """Serve ``dist`` for every GET the API's own routes don't answer. Call after including them."""
    root = Path(dist).resolve()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(f"NOVARA_WEB_DIST has no index.html: {root}")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web(path: str):
        if path == "api" or path.startswith(("api/", "sites/")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        target = (root / path).resolve()
        if path and target.is_file() and target.is_relative_to(root):
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return FileResponse(target, headers=HEADERS | {"Cache-Control": cache})
        if path.startswith("assets/"):
            return PlainTextResponse("not found", status_code=404, headers=HEADERS)
        return FileResponse(index, headers=HEADERS | {"Cache-Control": "no-cache"})
