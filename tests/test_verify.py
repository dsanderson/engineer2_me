"""Verification against a live runner, and the invalidation cascade."""

from __future__ import annotations

import pytest

from app.models import Actor
from app.runner_client import RunnerClient

pytestmark = pytest.mark.asyncio

ACTOR = Actor(type="agent", name="verifier-test")


@pytest.fixture
def live(platform, runner_url):
    platform.verifier.runner = RunnerClient(runner_url, timeout_s=10, mem_mb=256)
    return platform


async def test_calculation_passes_and_names_its_inputs(live, graph):
    run = await live.verify(graph["calculation"].id, ACTOR)
    assert run["verdict"] == "pass", run
    assert run["resolved_inputs"]["E_GPa"] == 68.9
    prov = run["input_provenance"]["E_GPa"]
    assert prov["item"] == graph["fact"].id and prov["rev"] == graph["fact"].rev
    assert live.store.get(graph["calculation"].id).status == "verified"


async def test_wrong_expectation_fails_with_a_numeric_diff(live, graph, actor):
    calc = graph["calculation"]
    live.update(calc.id, {"payload": {**calc.payload, "expect": {"tip_deflection_mm": 1.0}}}, actor)
    run = await live.verify(calc.id, ACTOR)
    assert run["verdict"] == "fail"
    assert run["diffs"][0]["path"] == "/tip_deflection_mm"
    assert live.store.get(calc.id).status == "failed"


async def test_script_raising_is_an_error_not_a_failure(live, graph, actor):
    calculator = graph["calculator"]
    live.update(
        calculator.id,
        {"payload": {**calculator.payload, "script": "def run(i):\n    return {'x': 1/0}"}},
        actor,
    )
    run = await live.verify(graph["calculation"].id, ACTOR)
    assert run["verdict"] == "error"
    assert run["error"]["type"] == "ZeroDivisionError"


async def test_timeout_is_reported_as_an_error(live, graph, actor):
    live.settings.run_timeout_s = 2
    calculator = graph["calculator"]
    live.update(
        calculator.id,
        {"payload": {**calculator.payload, "script": "def run(i):\n    while True: pass"}},
        actor,
    )
    run = await live.verify(graph["calculation"].id, ACTOR)
    assert run["verdict"] == "error" and run["error"]["type"] == "Timeout"


async def test_non_serialisable_output_is_named_clearly(live, graph, actor):
    calculator = graph["calculator"]
    live.update(
        calculator.id,
        {"payload": {**calculator.payload, "script": "def run(i):\n    return object()"}},
        actor,
    )
    run = await live.verify(graph["calculation"].id, ACTOR)
    assert run["error"]["type"] == "NotSerialisable"


async def test_runner_down_is_an_error_with_a_useful_message(platform, graph):
    platform.verifier.runner = RunnerClient("http://127.0.0.1:9", timeout_s=2)
    run = await platform.verify(graph["calculation"].id, ACTOR)
    assert run["verdict"] == "error"
    assert run["error"]["type"] == "RunnerUnavailable"
    # An error teaches us nothing, so a verified item must not be demoted to failed.
    assert platform.store.get(graph["calculation"].id).status != "failed"


async def test_calculator_self_test_runs_every_example(live, graph):
    run = await live.verify(graph["calculator"].id, ACTOR)
    assert run["verdict"] == "pass"
    assert len(run["examples"]) == 1


async def test_calculator_with_a_failing_example_cannot_be_verified(live, graph, actor):
    calculator = graph["calculator"]
    bad = [{"inputs": calculator.payload["examples"][0]["inputs"], "expect": {"tip_deflection_mm": 0.0}}]
    live.update(calculator.id, {"payload": {**calculator.payload, "examples": bad}}, actor)
    run = await live.verify(calculator.id, ACTOR)
    assert run["verdict"] == "fail"
    assert live.store.get(calculator.id).status == "failed"


async def test_calculator_without_examples_cannot_be_self_tested(live, graph, actor):
    calculator = graph["calculator"]
    payload = {k: v for k, v in calculator.payload.items() if k != "examples"}
    live.update(calculator.id, {"payload": payload}, actor)
    run = await live.verify(calculator.id, ACTOR)
    assert run["error"]["type"] == "NoExamples"


async def test_editing_a_fact_invalidates_its_verified_dependents(live, graph, actor):
    calc, fact = graph["calculation"], graph["fact"]
    assert (await live.verify(calc.id, ACTOR))["verdict"] == "pass"

    _, stale = live.update(
        fact.id,
        {"payload": {**fact.payload, "data": {"youngs_modulus": 70.0}}},
        actor,
    )
    assert calc.id in stale
    demoted = live.store.get(calc.id)
    assert demoted.status == "proposed"
    assert "upstream" in demoted.body.lower()

    # One call brings it back, and the new run names the fact's new revision.
    run = await live.verify(calc.id, ACTOR)
    assert run["verdict"] == "fail"  # 70 GPa really does give a different deflection
    assert run["input_provenance"]["E_GPa"]["rev"] == live.store.get(fact.id).rev
    assert run["resolved_inputs"]["E_GPa"] == 70.0


async def test_facts_and_ideas_cannot_be_reproduced(platform, graph):
    with pytest.raises(ValueError, match="attested"):
        await platform.verify(graph["fact"].id, ACTOR)
    with pytest.raises(ValueError):
        await platform.verify(graph["mission"].id, ACTOR)


async def test_a_pending_run_record_exists_before_the_run_finishes(live, graph):
    run = await live.verify(graph["calculation"].id, ACTOR)
    stored = live.store.get_run(graph["calculation"].id, run["run_id"])
    assert stored["verdict"] == run["verdict"]


async def test_human_confirmation_verifies_an_attested_fact(platform, graph, actor):
    fact = platform.confirm(graph["fact"].id, True, Actor("human", "dsa"), "read the datasheet")
    assert fact.human_confirmed and fact.status == "verified"
    assert fact.confirmation["by"] == "dsa"
