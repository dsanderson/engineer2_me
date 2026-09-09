"""httpx client for the runner sidecar. A runner that is down is an `error`, never a `fail`."""

from __future__ import annotations

from typing import Any

import httpx


class RunnerClient:
    def __init__(self, base_url: str, timeout_s: int = 30, mem_mb: int = 512):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.mem_mb = mem_mb

    async def run(self, script: str, inputs: dict[str, Any], timeout_s: int | None = None) -> dict[str, Any]:
        timeout_s = timeout_s or self.timeout_s
        body = {
            "script": script,
            "inputs": inputs,
            "timeout_s": timeout_s,
            "mem_mb": self.mem_mb,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_s + 10) as client:
                resp = await client.post(f"{self.base_url}/run", json=body)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            return {
                "ok": False,
                "error": {
                    "type": "RunnerUnavailable",
                    "message": (
                        f"could not reach the runner at {self.base_url}: {exc}. "
                        "Start it with `make runner` or `docker compose up runner`."
                    ),
                    "traceback": "",
                },
                "stdout": "",
                "stderr": "",
                "duration_ms": 0,
            }

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.json()
        except httpx.HTTPError as exc:
            return {"ok": False, "error": str(exc)}
