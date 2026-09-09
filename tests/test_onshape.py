"""FeatureScript decoding and the Onshape client, against recorded shapes and a mock transport."""

from __future__ import annotations

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
