from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.models import Actor  # noqa: E402
from app.service import Platform  # noqa: E402


@pytest.fixture
def settings(tmp_path) -> Settings:
    # Onshape credentials are pinned empty rather than inherited: Settings reads them from the
    # environment (and, since .env support landed, from a developer's .env), which would make
    # "unconfigured" tests pass or fail depending on whose machine ran them. Tests that need a
    # working client monkeypatch it directly.
    return Settings(
        data_dir=tmp_path / "data",
        runner_url="http://localhost:8001",
        onshape_access_key="",
        onshape_secret_key="",
    )


@pytest.fixture
def platform(settings) -> Platform:
    return Platform(settings)


@pytest.fixture
def actor() -> Actor:
    return Actor(type="agent", name="test-agent")


@pytest.fixture
def client(settings):
    """A TestClient over the whole app, sharing one Platform."""
    from starlette.testclient import TestClient

    from app.web.app import build_app

    app = build_app(settings)
    with TestClient(app) as c:
        c.platform = app.platform
        yield c


CALC_SCRIPT = """
def run(inputs):
    E = inputs["E_GPa"] * 1e9
    I = inputs["I_mm4"] * 1e-12
    L = inputs["L_mm"] * 1e-3
    return {"tip_deflection_mm": (inputs["F_N"] * L**3) / (3 * E * I) * 1e3}
"""


@pytest.fixture
def graph(platform, actor):
    """A small worked graph: mission, fact, calculator, calculation."""
    mission = platform.create(
        {
            "kind": "idea",
            "title": "Drone arm: stiff enough, light enough",
            "payload": {
                "is_mission": True,
                "goal": "< 2 mm tip deflection at 10 N, < 45 g",
                "markdown": "Mission body",
                "milestones": [{"label": "Material selection", "item": None, "note": "needs E"}],
            },
        },
        actor,
    )
    fact = platform.create(
        {
            "kind": "fact",
            "title": "Young's modulus of 6061-T6",
            "question": "What is E for 6061-T6 at 20 C?",
            "status": "proposed",
            "payload": {
                "data": {"youngs_modulus": 68.9},
                "units": {"youngs_modulus": "GPa"},
                "sources": [{"url": "https://www.matweb.com/x", "locator": "table 2"}],
                "confidence": "medium",
            },
            "refs": [{"rel": "part_of", "to": mission.id}],
        },
        actor,
    )
    calculator = platform.create(
        {
            "kind": "calculator",
            "title": "Cantilever tip deflection",
            "payload": {
                "script": CALC_SCRIPT,
                "inputs": {"E_GPa": "number", "I_mm4": "number", "L_mm": "number", "F_N": "number"},
                "outputs": {"tip_deflection_mm": "number"},
                "examples": [
                    {
                        "inputs": {"E_GPa": 68.9, "I_mm4": 160, "L_mm": 250, "F_N": 10},
                        "expect": {"tip_deflection_mm": 4.7245},
                        "tolerance": {"rel": 1e-3},
                    }
                ],
            },
            "refs": [{"rel": "part_of", "to": mission.id}],
        },
        actor,
    )
    calculation = platform.create(
        {
            "kind": "calculation",
            "title": "Arm tip deflection at 10 N",
            "payload": {
                "calculator": calculator.id,
                "inputs": {
                    "E_GPa": {"$ref": f"item:{fact.id}#/payload/data/youngs_modulus"},
                    "I_mm4": 160,
                    "L_mm": 250,
                    "F_N": 10,
                },
                "expect": {"tip_deflection_mm": 4.7245},
                "tolerance": {"rel": 1e-3},
            },
            "refs": [
                {"rel": "part_of", "to": mission.id},
                {"rel": "depends_on", "to": fact.id},
            ],
        },
        actor,
    )
    return {
        "mission": mission,
        "fact": fact,
        "calculator": calculator,
        "calculation": calculation,
    }


@pytest.fixture(scope="session")
def runner_url():
    """A live runner subprocess, or skip the test that asked for one."""
    import socket
    import subprocess
    import time

    port = 8099
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "E2_RUNNER_PORT": str(port), "PYTHONPATH": str(root)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "runner.server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        proc.kill()
        pytest.skip("runner did not start")
    yield url
    proc.terminate()
    proc.wait(timeout=5)
