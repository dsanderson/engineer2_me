"""Environment-driven settings. No config files; everything is an env var with a default."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("E2_DATA_DIR", "./data")).expanduser())
    base_url: str = field(default_factory=lambda: _env("E2_BASE_URL", "http://localhost:8000"))
    runner_url: str = field(default_factory=lambda: _env("E2_RUNNER_URL", "http://localhost:8001"))
    onshape_base: str = field(default_factory=lambda: _env("E2_ONSHAPE_BASE", "https://cad.onshape.com"))
    onshape_access_key: str = field(default_factory=lambda: _env("ONSHAPE_ACCESS_KEY", ""))
    onshape_secret_key: str = field(default_factory=lambda: _env("ONSHAPE_SECRET_KEY", ""))
    onshape_api_version: str = field(default_factory=lambda: _env("E2_ONSHAPE_API_VERSION", "v9"))
    run_timeout_s: int = field(default_factory=lambda: _int_env("E2_RUN_TIMEOUT_S", 30))
    run_mem_mb: int = field(default_factory=lambda: _int_env("E2_RUN_MEM_MB", 512))
    host: str = field(default_factory=lambda: _env("E2_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int_env("E2_PORT", 8000))
    # Optional in-app HTTP Basic auth, for running without Caddy in front.
    # In the compose stack Caddy does this; leave E2_PASSWORD unset there.
    user: str = field(default_factory=lambda: _env("E2_USER", "agent"))
    password: str = field(default_factory=lambda: _env("E2_PASSWORD", ""))

    @property
    def onshape_configured(self) -> bool:
        return bool(self.onshape_access_key and self.onshape_secret_key)

    @property
    def auth_enabled(self) -> bool:
        return bool(self.password)


settings = Settings()
