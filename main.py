"""engineer2.me entrypoint. `uv run python main.py` (or `make dev`)."""

from __future__ import annotations

import sys

from app.config import settings
from app.web.app import build_app

app = build_app(settings)


def self_check() -> None:
    """Say plainly, at startup, what works and what does not."""
    data_ok = settings.data_dir.exists() and settings.data_dir.is_dir()
    lines = [
        f"engineer2.me — {len(app.platform.index)} items",
        f"  data dir : {settings.data_dir} ({'writable' if data_ok else 'MISSING'})",
        f"  runner   : {settings.runner_url}",
        f"  onshape  : {'configured' if settings.onshape_configured else 'NOT configured (CAD verify disabled)'}",
        f"  auth     : {'basic auth enabled in-app' if settings.auth_enabled else 'open (put Caddy in front for auth)'}",
        f"  base url : {settings.base_url}",
    ]
    print("\n".join(lines), file=sys.stderr)


if __name__ == "__main__":
    from fasthtml.common import serve

    self_check()
    serve(app="app", host=settings.host, port=settings.port, reload=False)
