"""Builds the FastHTML app: pages, JSON API, error handling, optional Basic auth."""

from __future__ import annotations

import base64
import secrets
from pathlib import Path

from fasthtml.common import FastHTML, Link, Response
from fasthtml.pico import picolink

from app.config import Settings
from app.config import settings as default_settings
from app.service import Platform
from app.web.api import register_api, register_error_handlers
from app.web.pages import register_pages

STATIC = Path(__file__).resolve().parent / "static"


def basic_auth_before(settings: Settings):
    """In-app HTTP Basic, for running without Caddy in front. Off unless E2_PASSWORD is set."""

    def check(req, session):
        if not settings.auth_enabled or req.url.path.startswith("/api/v1/health"):
            return None
        header = req.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, password = base64.b64decode(header[6:]).decode().partition(":")
            except (ValueError, UnicodeDecodeError):
                user = password = ""
            if secrets.compare_digest(user, settings.user) and secrets.compare_digest(
                password, settings.password
            ):
                return None
        return Response(
            "authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="engineer2.me"'},
        )

    return check


def build_app(settings: Settings | None = None, platform: Platform | None = None):
    settings = settings or default_settings
    platform = platform or Platform(settings)

    from fasthtml.common import Beforeware

    hdrs = [picolink, Link(rel="stylesheet", href="/static/style.css")]
    app = FastHTML(
        hdrs=hdrs,
        title="engineer2.me",
        before=Beforeware(basic_auth_before(settings), skip=[r"/static/.*"]),
        htmx=True,
        secret_key=settings.password or "engineer2-local-dev",
    )
    register_error_handlers(app)
    register_pages(app, platform)
    register_api(app, platform)

    @app.get("/static/{fname}")
    def static(fname: str):
        from fasthtml.common import FileResponse

        path = STATIC / fname
        if not path.exists():
            return Response("not found", status_code=404)
        return FileResponse(path)

    app.platform = platform
    return app
