"""Settings, read from the environment — optionally seeded from a gitignored `.env` file.

Every setting is an env var with a default. Secrets (the Onshape key pair, the Basic
auth password) are awkward to keep in a shell profile, so `.env` next to `main.py` is
read first and used only to fill in variables the environment does not already set.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

ENV_FILE_DEFAULT = ".env"


def load_env_file(path: str | Path | None = None) -> Path | None:
    """Seed os.environ from a KEY=value file. A real env var always wins.

    Deliberately tiny: `#` comments, blank lines, an optional `export ` prefix, and
    optional surrounding quotes. No interpolation — a literal `$` in a secret should
    stay a literal `$`.
    """
    path = Path(path or os.environ.get("E2_ENV_FILE") or ENV_FILE_DEFAULT).expanduser()
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)  # the environment overrides the file
    return path


def env_file_is_world_readable(path: Path) -> bool:
    """A secrets file other users can read is worth one line of warning at startup."""
    try:
        return bool(path.stat().st_mode & (stat.S_IRGRP | stat.S_IROTH))
    except OSError:
        return False


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


# Read before Settings is built, so the file can supply any variable below.
env_file = load_env_file()
settings = Settings()
