"""Envelope, payload validation, transitions and the ref vocabulary."""

from __future__ import annotations

import pytest

from app.models import (
    Item,
    ValidationError,
    can_transition,
    tier_for,
    validate_item,
    validate_payload,
    validate_ref,
)


def test_tier_is_derived_from_kind():
    assert tier_for("calculation") == "reproduced"
    assert tier_for("fact") == "attested"
    assert tier_for("idea") == "asserted"
    assert Item(kind="fact", title="x").tier == "attested"


def test_round_trip_every_kind():
    payloads = {
        "fact": {"data": {"a": 1}, "sources": [{"url": "u"}]},
        "calculator": {"entrypoint": "calc.py", "sha256": "x"},
        "calculation": {
            "calculator": "8a1c0000-0000-4000-8000-000000000000",
            "inputs": {},
            "expect": {"y": 1},
        },
        "cad_model": {"provider": "onshape", "did": "d", "wid": "w", "eid": "e"},
        "cad_evaluation": {
            "model": "8a1c0000-0000-4000-8000-000000000000",
            "entrypoint": "eval.fs",
            "expect": {"mass_kg": 1.0},
        },
        "idea": {"markdown": "hi", "is_mission": True, "goal": "done looks like this"},
    }
    for kind, payload in payloads.items():
        item = Item(kind=kind, title=f"a {kind}", payload=payload)
        assert validate_item(item) == [], (kind, validate_item(item))
        back = Item.from_json(item.to_json())
        assert back.to_json() == item.to_json()


def test_unknown_payload_keys_are_rejected():
    errs = validate_payload("fact", {"data": {"a": 1}, "sources": [{"url": "u"}], "wat": 1})
    assert any("unknown key 'wat'" in e for e in errs)


def test_fact_with_data_needs_a_source():
    errs = validate_payload("fact", {"data": {"a": 1}})
    assert any("needs at least one source" in e for e in errs)
    assert validate_payload("fact", {"question": None} if False else {}) == []


def test_mission_needs_a_goal():
    assert any("needs a goal" in e for e in validate_payload("idea", {"is_mission": True}))


def test_transitions():
    assert can_transition("open", "claimed")
    assert can_transition("verified", "proposed")
    assert not can_transition("deprecated", "open")
    assert not can_transition("draft", "verified")
    assert can_transition("failed", "verified")


def test_ref_vocabulary():
    a, b = "a" * 8, "b" * 8
    assert validate_ref("part_of", "fact", "idea", "", a, b) == []
    assert validate_ref("part_of", "fact", "fact", "", a, b)  # must point at an idea
    assert validate_ref("nonsense", "fact", "idea", "", a, b)
    assert validate_ref("related", "fact", "idea", "", a, b)  # needs a note
    assert validate_ref("related", "fact", "idea", "why", a, b) == []
    assert validate_ref("depends_on", "fact", "fact", "", a, a)  # no self-refs


def test_validation_error_carries_every_problem():
    with pytest.raises(ValidationError) as exc:
        raise ValidationError(["one", "two"])
    assert exc.value.errors == ["one", "two"]
