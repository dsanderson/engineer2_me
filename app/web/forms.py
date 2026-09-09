"""Per-kind create/edit forms — table-driven, so rendering and parsing cannot drift apart."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fasthtml.common import (
    Button,
    Details,
    Div,
    Form,
    Input,
    Label,
    Option,
    P,
    Select,
    Small,
    Summary,
    Textarea,
)

from app.models import KINDS, STATUSES


@dataclass
class Field:
    name: str  # form field name; payload fields are prefixed "p_"
    label: str
    type: str = "text"  # text | textarea | json | script | bool | select
    help: str = ""
    options: tuple[str, ...] = ()
    rows: int = 6


COMMON = [
    Field("title", "Title", help="One line. What a reader scanning a list needs to see."),
    Field(
        "question",
        "Question",
        type="textarea",
        rows=2,
        help="What this item is meant to answer. Facts start as a question with no data.",
    ),
    Field(
        "body",
        "Notes",
        type="textarea",
        help="Markdown: rationale, caveats, what was ruled out.",
    ),
    Field("tags", "Tags", help="Space or comma separated."),
    Field("status", "Status", type="select", options=STATUSES),
]

PAYLOAD_FIELDS: dict[str, list[Field]] = {
    "fact": [
        Field("p_data", "Data (JSON object)", type="json", rows=6, help='e.g. {"youngs_modulus": 68.9}'),
        Field("p_units", "Units (JSON object)", type="json", rows=3, help='e.g. {"youngs_modulus": "GPa"}'),
        Field(
            "p_conditions", "Conditions (JSON object)", type="json", rows=3, help='e.g. {"temperature_C": 20}'
        ),
        Field(
            "p_sources",
            "Sources (JSON list)",
            type="json",
            rows=5,
            help='Each needs an attachment or url: [{"url": "https://…", "locator": "p.1 table 2"}]. '
            "A fact with data and no source will be rejected.",
        ),
        Field("p_confidence", "Confidence", type="select", options=("", "high", "medium", "low")),
    ],
    "calculator": [
        Field(
            "p_script",
            "Script",
            type="script",
            rows=16,
            help="One function: def run(inputs: dict) -> dict. No I/O. numpy/scipy/pint are importable; "
            "cast results to Python floats before returning.",
        ),
        Field("p_entrypoint", "Entrypoint filename", help="Default calc.py"),
        Field("p_inputs", "Inputs (JSON object)", type="json", rows=4, help='{"E_GPa": "number"}'),
        Field(
            "p_outputs", "Outputs (JSON object)", type="json", rows=3, help='{"tip_deflection_mm": "number"}'
        ),
        Field(
            "p_examples",
            "Examples (JSON list)",
            type="json",
            rows=8,
            help='Self-test, run on every verify: [{"inputs": {...}, "expect": {...}, "tolerance": {"rel": 1e-4}}]',
        ),
    ],
    "calculation": [
        Field("p_calculator", "Calculator item id", help="uuid of the calculator this binds"),
        Field(
            "p_inputs",
            "Inputs (JSON object)",
            type="json",
            rows=8,
            help='Literals, or {"$ref": "item:<uuid>#/payload/data/youngs_modulus"} to pull a value '
            "from another item at run time.",
        ),
        Field("p_expect", "Expected output (JSON object)", type="json", rows=4),
        Field(
            "p_tolerance", "Tolerance (JSON object)", type="json", rows=3, help='{"rel": 1e-4, "abs": 0.0}'
        ),
    ],
    "cad_model": [
        Field("p_url", "Onshape URL", help="Paste the document URL; did/wid/eid are filled from it."),
        Field("p_did", "Document id"),
        Field("p_wid", "Workspace id"),
        Field("p_eid", "Element id"),
        Field("p_element_type", "Element type", help="PARTSTUDIO, ASSEMBLY, …"),
        Field(
            "p_pin",
            "Pin (JSON)",
            type="json",
            rows=2,
            help='{"kind": "workspace"} or {"kind": "version", "vid": "…"}',
        ),
        Field("p_description", "Description", type="textarea", rows=3),
    ],
    "cad_evaluation": [
        Field("p_model", "cad_model item id", help="uuid of the model this evaluates"),
        Field(
            "p_script",
            "FeatureScript",
            type="script",
            rows=14,
            help="function(context is Context, queries) { … return … } — keep the canonical copy in a "
            "Feature Studio in the same Onshape document; this is the mirrored, hashed copy.",
        ),
        Field("p_entrypoint", "Entrypoint filename", help="Default eval.fs"),
        Field("p_expect", "Expected output (JSON object)", type="json", rows=4),
        Field("p_tolerance", "Tolerance (JSON object)", type="json", rows=2),
        Field(
            "p_onshape_source",
            "Onshape source (JSON)",
            type="json",
            rows=3,
            help='{"eid": "…", "name": "ArmMetrics"}',
        ),
    ],
    "idea": [
        Field("p_markdown", "Markdown", type="textarea", rows=12),
        Field("p_is_mission", "This is a mission", type="bool", help="Missions are the roots of the graph."),
        Field("p_goal", "Goal", help="One sentence stating what done looks like. Required for missions."),
        Field(
            "p_milestones",
            "Milestones (JSON list)",
            type="json",
            rows=6,
            help='Ordered attack path: [{"label": "Material selection", "item": null, "note": "needs E and ρ"}]',
        ),
    ],
}


def fields_for(kind: str) -> list[Field]:
    return COMMON + PAYLOAD_FIELDS[kind]


def _value_for(field: Field, item) -> Any:
    if not item:
        return "" if field.type != "bool" else False
    if not field.name.startswith("p_"):
        value = getattr(item, field.name, "")
        return " ".join(value) if field.name == "tags" else value
    key = field.name[2:]
    if key == "script":
        from app.web.pages import script_text  # local import: pages owns store access

        return script_text(item)
    value = item.payload.get(key, "" if field.type != "bool" else False)
    if field.type == "json" and value not in ("", None):
        return json.dumps(value, indent=2)
    return value


def render_field(field: Field, item=None, errors: dict[str, str] | None = None):
    value = _value_for(field, item)
    errors = errors or {}
    if field.type == "select":
        control = Select(
            *[Option(o, value=o, selected=(str(value) == o)) for o in field.options],
            name=field.name,
            id=field.name,
        )
    elif field.type == "bool":
        control = Input(type="checkbox", name=field.name, id=field.name, checked=bool(value))
    elif field.type in ("textarea", "json", "script"):
        control = Textarea(
            str(value or ""),
            name=field.name,
            id=field.name,
            rows=field.rows,
            cls="mono" if field.type in ("json", "script") else None,
            spellcheck="false" if field.type in ("json", "script") else None,
        )
    else:
        control = Input(type="text", name=field.name, id=field.name, value=str(value or ""))
    return Div(
        Label(field.label, fr=field.name),
        control,
        Small(field.help, cls="muted") if field.help else "",
        Small(errors.get(field.name, ""), cls="field-error") if errors.get(field.name) else "",
        cls="field",
    )


def item_form(kind: str, item=None, errors: list[str] | None = None, action: str = "", by: str = ""):
    heading = f"Edit {kind}" if item else f"New {kind}"
    return Form(
        P(", ".join(errors), cls="banner error") if errors else "",
        Div(*[render_field(f, item) for f in COMMON], cls="fieldset"),
        Details(
            Summary(f"{kind} payload"),
            Div(*[render_field(f, item) for f in PAYLOAD_FIELDS[kind]], cls="fieldset"),
            open=True,
        ),
        Div(
            Label("Your name", fr="by"),
            Input(type="text", name="by", id="by", value=by or "anonymous"),
            Small(
                "Self-reported. There is one shared credential; this is a label, not a login.", cls="muted"
            ),
            cls="field",
        ),
        Button(heading, type="submit"),
        method="post",
        action=action,
        cls="item-form",
    )


def _parse_json_field(raw: str, name: str, errors: list[str]) -> Any:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(f"{name}: not valid JSON ({exc})")
        return None


def parse_form(kind: str, form: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Form data → the same dict shape the JSON API takes."""
    errors: list[str] = []
    data: dict[str, Any] = {"kind": kind, "payload": {}}
    for field in fields_for(kind):
        raw = form.get(field.name)
        if field.type == "bool":
            value: Any = str(raw or "").lower() in ("on", "true", "1", "yes")
        elif field.type == "json":
            value = _parse_json_field(str(raw or ""), field.label, errors)
        else:
            value = (str(raw) if raw is not None else "").strip()
        if field.name == "tags":
            value = [t for t in str(value).replace(",", " ").split() if t]
        if field.name.startswith("p_"):
            if value not in ("", None, [], {}):
                data["payload"][field.name[2:]] = value
        elif value not in ("", None):
            data[field.name] = value
    if kind == "cad_model":
        data["payload"].update(_onshape_ids(data["payload"]))
    return data, errors


def _onshape_ids(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull did/wid/eid out of a pasted Onshape URL so nobody has to copy three ids by hand."""
    url = str(payload.get("url") or "")
    out: dict[str, Any] = {"provider": "onshape"}
    parts = url.split("/")
    for key, marker in (("did", "documents"), ("wid", "w"), ("eid", "e")):
        if payload.get(key):
            continue
        if marker in parts:
            i = parts.index(marker)
            if i + 1 < len(parts):
                out[key] = parts[i + 1]
    if "v" in parts and not payload.get("pin"):
        i = parts.index("v")
        if i + 1 < len(parts):
            out["pin"] = {"kind": "version", "vid": parts[i + 1]}
            out["wid"] = payload.get("wid") or parts[i + 1]
    return out


KIND_HELP = {
    "fact": "A JSONable answer plus its evidence. Attested, not reproduced: the platform timestamps it "
    "and hashes the sources, it cannot check it.",
    "calculator": "A pure Python run(inputs) -> dict, hashed and self-tested by its examples.",
    "calculation": "A calculator, bound inputs (literals or $refs), and a claimed answer. Reproduced.",
    "cad_model": "A pointer into an Onshape document. Attested: we check it is reachable, not that it is right.",
    "cad_evaluation": "A FeatureScript lambda run against a model, with a claimed result. Reproduced.",
    "idea": "Prose. A mission root, an intermediate goal, or a path worth recording. Asserted.",
}

__all__ = ["KINDS", "KIND_HELP", "fields_for", "item_form", "parse_form"]
