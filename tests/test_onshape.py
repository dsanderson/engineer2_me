"""FeatureScript decoding and the Onshape client, against recorded shapes and a mock transport."""

from __future__ import annotations

import json

import httpx
import pytest

from app.onshape import NotConfigured, OnshapeClient, OnshapeError, decode_fs, has_undecoded, model_url

MAP = {
    "btType": "BTFSValueMap-2062",
    "value": [
        {
            "key": {"btType": "BTFSValueString-1188", "value": "mass"},
            "value": {
                "btType": "BTFSValueWithUnits-1817",
                "value": 0.0431,
                "unitToPower": [{"key": "kilogram", "value": 1}],
            },
        },
        {
            "key": {"btType": "BTFSValueString-1188", "value": "count"},
            "value": {"btType": "BTFSValueNumber-1197", "value": 3},
        },
        {
            "key": {"btType": "BTFSValueString-1188", "value": "names"},
            "value": {
                "btType": "BTFSValueArray-1188",
                "value": [{"btType": "BTFSValueString-1188", "value": "arm"}],
            },
        },
    ],
}


# Captured from a live Onshape response (api/v9, 2026-09). Note the inconsistency this exists
# to pin down: the *value* objects are fully qualified, while the map *entries* are bare.
# Hand-written fixtures used the bare form throughout, which is why the suite stayed green
# while every production evaluation failed.
QUALIFIED_MAP = {
    "btType": "com.belmonttech.serialize.fsvalue.BTFSValueMap",
    "value": [
        {
            "btType": "BTFSValueMapEntry-2077",
            "key": {"btType": "com.belmonttech.serialize.fsvalue.BTFSValueString", "value": "bore_mm"},
            "value": {
                "btType": "com.belmonttech.serialize.fsvalue.BTFSValueNumber",
                "value": 38.73475265716953,
            },
        },
        {
            "btType": "BTFSValueMapEntry-2077",
            "key": {"btType": "com.belmonttech.serialize.fsvalue.BTFSValueString", "value": "mass"},
            "value": {
                "btType": "com.belmonttech.serialize.fsvalue.BTFSValueWithUnits",
                "value": 0.32441,
                "unitToPower": [{"key": "kilogram", "value": 1}],
            },
        },
        {
            "btType": "BTFSValueMapEntry-2077",
            "key": {"btType": "com.belmonttech.serialize.fsvalue.BTFSValueString", "value": "missing"},
            "value": {"btType": "com.belmonttech.serialize.fsvalue.BTFSValueUndefined"},
        },
    ],
}


def test_decode_fully_qualified_bttypes_from_a_real_response():
    """Onshape qualifies its value btTypes; matching the raw string skipped every branch."""
    assert decode_fs(QUALIFIED_MAP) == {
        "bore_mm": 38.73475265716953,
        "mass": {"value": 0.32441, "units": "kg"},
        "missing": None,
    }


def test_a_qualified_result_is_fully_decoded():
    """The failure mode was silent: undecoded values marked the whole run `error`."""
    assert has_undecoded(decode_fs(QUALIFIED_MAP)) is False


def test_map_entry_is_not_mistaken_for_a_map():
    """`BTFSValueMapEntry` is a prefix of `BTFSValueMap`; only the map branch may read it."""
    entry = {"btType": "BTFSValueMapEntry-2077", "key": {}, "value": {}}
    assert has_undecoded(decode_fs(entry)) is True


async def test_queries_is_sent_as_a_map_not_an_array(monkeypatch):
    """Onshape types `queries` as a map; an empty array 400s before the script compiles."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"result": QUALIFIED_MAP, "sourceMicroversion": "mv"})

    client = OnshapeClient("key", "secret")
    monkeypatch.setattr(client, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await client.eval_featurescript("D", "W", "E", "function(){}")
    assert seen["body"]["queries"] == {}, "an empty list here is rejected by Onshape with a 400"


def test_decode_map_array_and_scalars():
    got = decode_fs(MAP)
    assert got == {
        "mass": {"value": 0.0431, "units": "kg"},
        "count": 3,
        "names": ["arm"],
    }


def test_decode_units_with_powers():
    volume = {
        "btType": "BTFSValueWithUnits-1817",
        "value": 2.5,
        "unitToPower": [{"key": "meter", "value": 3}],
    }
    assert decode_fs(volume) == {"value": 2.5, "units": "m^3"}
    pressure = {
        "btType": "BTFSValueWithUnits-1817",
        "value": 1.0,
        "unitToPower": [{"key": "kilogram", "value": 1}, {"key": "second", "value": -2}],
    }
    assert decode_fs(pressure)["units"] == "kg*s^-2"


def test_units_from_the_current_mapping_shape():
    """Onshape returns `unitToPower` as an upper-cased mapping; we long assumed a list."""
    length = {
        "btType": "com.belmonttech.serialize.fsvalue.BTFSValueWithUnits",
        "value": 0.002,
        "unitToPower": {"METER": 1},
    }
    assert decode_fs(length) == {"value": 0.002, "units": "m"}


def test_units_mapping_shape_with_powers_and_ordering():
    density = {
        "btType": "com.belmonttech.serialize.fsvalue.BTFSValueWithUnits",
        "value": 7850.0,
        "unitToPower": {"KILOGRAM": 1, "METER": -3},
    }
    assert decode_fs(density)["units"] == "kg*m^-3"


def test_onshape_normalises_to_base_si_so_expect_must_too():
    """`2 * millimeter` comes back as 0.002 m. An expect of 2 mm will never match — by design:
    units compare as exact strings and there is no conversion anywhere in this system."""
    got = decode_fs(
        {
            "btType": "com.belmonttech.serialize.fsvalue.BTFSValueWithUnits",
            "value": 0.002,
            "unitToPower": {"METER": 1},
        }
    )
    assert got != {"value": 2.0, "units": "mm"}
    assert got == {"value": 0.002, "units": "m"}


def test_unknown_bttype_passes_through_and_poisons_the_path():
    weird = {"btType": "BTFSValueMysteryBox-9999", "value": 1}
    assert decode_fs(weird) == weird
    assert has_undecoded({"a": decode_fs(weird)})
    assert not has_undecoded(decode_fs(MAP))


def test_nested_maps():
    nested = {
        "btType": "BTFSValueMap-2062",
        "value": [{"key": {"btType": "BTFSValueString-1188", "value": "inner"}, "value": MAP}],
    }
    assert decode_fs(nested)["inner"]["count"] == 3


def test_model_url_prefers_the_pinned_version():
    payload = {"did": "D", "wid": "W", "eid": "E", "pin": {"kind": "version", "vid": "V"}}
    assert model_url(payload).endswith("/documents/D/v/V/e/E")
    assert model_url({"did": "D", "wid": "W", "eid": "E"}).endswith("/documents/D/w/W/e/E")


async def test_unconfigured_client_says_so_plainly():
    client = OnshapeClient("", "")
    assert not client.configured
    with pytest.raises(NotConfigured, match="ONSHAPE_ACCESS_KEY"):
        await client.eval_featurescript("d", "w", "e", "function(){}")


async def test_eval_featurescript_against_a_mock_transport(monkeypatch):
    calls = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["url"] = str(request.url)
        return httpx.Response(200, json={"result": MAP, "sourceMicroversion": "mv1", "notices": []})

    client = OnshapeClient("key", "secret")
    monkeypatch.setattr(client, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    got = await client.eval_featurescript("D", "W", "E", "function(){}")
    assert got["result"]["count"] == 3
    assert got["source_microversion"] == "mv1"
    assert "/partstudios/d/D/w/W/e/E/featurescript" in calls["url"]


async def test_http_errors_are_mapped_to_readable_messages(monkeypatch):
    client = OnshapeClient("key", "secret")
    monkeypatch.setattr(
        client,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(403, text="forbidden"))
        ),
    )
    with pytest.raises(OnshapeError, match="no access"):
        await client.eval_featurescript("D", "W", "E", "function(){}")


async def test_version_pinned_models_evaluate_against_the_version(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"result": MAP, "sourceMicroversion": "mv"})

    client = OnshapeClient("key", "secret")
    monkeypatch.setattr(client, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await client.eval_featurescript("D", "W", "E", "f", pin={"kind": "version", "vid": "V"})
    assert "/d/D/v/V/e/E/" in seen["url"]


async def test_unreachable_onshape_is_an_error_not_a_crash(monkeypatch):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nodename nor servname provided", request=request)

    client = OnshapeClient("key", "secret")
    monkeypatch.setattr(client, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    with pytest.raises(OnshapeError, match="could not reach Onshape"):
        await client.eval_featurescript("D", "W", "E", "function(){}")
