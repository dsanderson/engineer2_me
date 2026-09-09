"""Tolerant comparison of an expected result against an actual one.

Returns a list of diffs; empty means pass. Units are compared as strings and must match
exactly — a silent metre/millimetre swap is the most likely real bug in this system, so
it fails loudly rather than converting (design §11.2).
"""

from __future__ import annotations

import math
from typing import Any

DEFAULT_TOLERANCE = {"rel": 1e-6, "abs": 0.0}


def _tol_for(tolerance: dict[str, Any] | None, key: str | None) -> dict[str, float]:
    tol = dict(DEFAULT_TOLERANCE)
    if tolerance:
        tol.update({k: v for k, v in tolerance.items() if k in ("rel", "abs")})
        override = (tolerance.get("keys") or {}).get(key) if key else None
        if override:
            tol.update({k: v for k, v in override.items() if k in ("rel", "abs")})
    return tol


def numbers_match(expected: float, actual: float, tol: dict[str, float]) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if math.isnan(expected) or math.isnan(actual):
        return False  # NaN is never equal to anything, including itself
    if math.isinf(expected) or math.isinf(actual):
        return expected == actual
    return abs(actual - expected) <= max(tol["abs"], tol["rel"] * abs(expected))


def _is_quantity(v: Any) -> bool:
    return isinstance(v, dict) and set(v) >= {"value", "units"} and isinstance(v.get("units"), str)


def compare(expected: Any, actual: Any, tolerance: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Walk expected and actual in parallel; return `{path, expected, actual, reason}` diffs."""
    diffs: list[dict[str, Any]] = []

    def note(path: str, exp: Any, act: Any, reason: str) -> None:
        diffs.append({"path": path or "/", "expected": exp, "actual": act, "reason": reason})

    def walk(exp: Any, act: Any, path: str, key: str | None) -> None:
        tol = _tol_for(tolerance, key)

        if _is_quantity(exp) or _is_quantity(act):
            if not (_is_quantity(exp) and _is_quantity(act)):
                note(path, exp, act, "one side carries units and the other does not")
                return
            if exp["units"] != act["units"]:
                note(path, exp, act, f"unit mismatch: expected {exp['units']!r}, got {act['units']!r}")
                return
            walk(exp["value"], act["value"], f"{path}/value", key)
            return

        if isinstance(exp, bool) or isinstance(act, bool):
            if exp is not act:
                note(path, exp, act, "boolean mismatch")
            return

        if isinstance(exp, int | float) and isinstance(act, int | float):
            if not numbers_match(float(exp), float(act), tol):
                note(path, exp, act, f"outside tolerance rel={tol['rel']:g} abs={tol['abs']:g}")
            return

        if isinstance(exp, dict):
            if not isinstance(act, dict):
                note(path, exp, act, f"expected an object, got {type(act).__name__}")
                return
            missing = sorted(set(exp) - set(act))
            extra = sorted(set(act) - set(exp))
            for k in missing:
                note(f"{path}/{k}", exp[k], None, "missing key in output")
            for k in extra:
                note(f"{path}/{k}", None, act[k], "unexpected key in output")
            for k in exp:
                if k in act:
                    walk(exp[k], act[k], f"{path}/{k}", k)
            return

        if isinstance(exp, list):
            if not isinstance(act, list):
                note(path, exp, act, f"expected a list, got {type(act).__name__}")
                return
            if len(exp) != len(act):
                note(path, exp, act, f"length mismatch: expected {len(exp)}, got {len(act)}")
                return
            for i, (e, a) in enumerate(zip(exp, act, strict=True)):
                walk(e, a, f"{path}/{i}", key)
            return

        if exp != act:
            note(path, exp, act, "value mismatch")

    walk(expected, actual, "", None)
    return diffs


def verdict_for(diffs: list[dict[str, Any]]) -> str:
    return "pass" if not diffs else "fail"
