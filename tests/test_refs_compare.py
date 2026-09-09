"""$ref resolution with provenance, and tolerant comparison."""

from __future__ import annotations

import math

import pytest

from app.compare import compare
from app.refs import RefError, is_ref, parse_ref, referenced_items, resolve_inputs, resolve_pointer


def test_parse_ref():
    item_id, pointer = parse_ref("item:8a1c0000-0000-4000-8000-000000000000#/payload/data/E")
    assert item_id == "8a1c0000-0000-4000-8000-000000000000"
    assert pointer == "/payload/data/E"
    with pytest.raises(RefError):
        parse_ref("http://example.com/thing")


def test_resolve_pointer_escapes_and_lists():
    doc = {"a/b": {"~c": [10, 20]}}
    assert resolve_pointer(doc, "/a~1b/~0c/1") == 20
    assert resolve_pointer(doc, "") == doc
    with pytest.raises(RefError):
        resolve_pointer(doc, "/nope")
    with pytest.raises(RefError):
        resolve_pointer(doc, "a/b")


def test_resolve_inputs_captures_provenance(graph, platform):
    fact = graph["fact"]
    inputs = {"E_GPa": {"$ref": f"item:{fact.id}#/payload/data/youngs_modulus"}, "L_mm": 250}
    resolved, provenance = resolve_inputs(platform.store, inputs)
    assert resolved == {"E_GPa": 68.9, "L_mm": 250}
    assert provenance["E_GPa"] == {
        "item": fact.id,
        "rev": fact.rev,
        "pointer": "/payload/data/youngs_modulus",
        "title": fact.title,
        "status": fact.status,
    }


def test_resolving_a_retired_source_warns(graph, platform, actor):
    fact = graph["fact"]
    platform.set_status(fact.id, "deprecated", actor, reason="superseded")
    _, provenance = resolve_inputs(
        platform.store, {"E": {"$ref": f"item:{fact.id}#/payload/data/youngs_modulus"}}
    )
    assert "deprecated" in provenance["E"]["warning"]


def test_missing_item_and_bad_pointer_are_clear(platform):
    with pytest.raises(RefError, match="no such item"):
        resolve_inputs(platform.store, {"x": {"$ref": "item:8a1c0000-0000-4000-8000-000000000000#/a"}})


def test_referenced_items_finds_nested_refs():
    inputs = {
        "a": {"$ref": "item:8a1c0000-0000-4000-8000-000000000000#/x"},
        "b": [{"$ref": "item:8a1c0000-0000-4000-8000-000000000001#/y"}],
    }
    assert len(referenced_items(inputs)) == 2
    assert is_ref(inputs["a"])


def test_numeric_tolerance_boundaries():
    assert compare({"x": 1.0}, {"x": 1.0 + 9e-7}, {"rel": 1e-6}) == []
    assert compare({"x": 1.0}, {"x": 1.0 + 2e-6}, {"rel": 1e-6})
    assert compare({"x": 0.0}, {"x": 1e-9}, {"abs": 1e-8}) == []


def test_per_key_tolerance_override():
    tol = {"rel": 1e-6, "keys": {"mass_kg": {"rel": 1e-2}}}
    assert compare({"mass_kg": 1.0, "len_mm": 10.0}, {"mass_kg": 1.005, "len_mm": 10.0}, tol) == []
    assert compare({"mass_kg": 1.0, "len_mm": 10.0}, {"mass_kg": 1.0, "len_mm": 10.1}, tol)


def test_dict_key_mismatch_is_a_failure_not_a_warning():
    diffs = compare({"a": 1}, {"a": 1, "b": 2})
    assert [d["reason"] for d in diffs] == ["unexpected key in output"]
    assert compare({"a": 1, "b": 2}, {"a": 1})[0]["reason"] == "missing key in output"


def test_non_finite_values_never_match():
    assert compare({"x": float("nan")}, {"x": float("nan")})
    assert compare({"x": float("inf")}, {"x": float("inf")}) == []
    assert compare({"x": float("inf")}, {"x": 1e308})


def test_units_must_match_exactly():
    assert compare({"m": {"value": 1.0, "units": "kg"}}, {"m": {"value": 1.0, "units": "kg"}}) == []
    diffs = compare({"m": {"value": 1.0, "units": "m"}}, {"m": {"value": 1.0, "units": "mm"}})
    assert "unit mismatch" in diffs[0]["reason"]
    assert compare({"m": {"value": 1.0, "units": "kg"}}, {"m": 1.0})


def test_lists_and_scalars():
    assert compare([1, 2, 3], [1, 2, 3]) == []
    assert compare([1, 2], [1, 2, 3])[0]["reason"].startswith("length mismatch")
    assert compare("a", "b")[0]["reason"] == "value mismatch"
    assert compare(True, 1)[0]["reason"] == "boolean mismatch"
    assert not math.isnan(0)
