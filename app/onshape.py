"""Onshape: FeatureScript evaluation and value decoding.

Auth is an API key pair used as HTTP Basic. Without credentials, CAD items still store
and render — only verification is disabled, with a clear message (design §5.1).
"""

from __future__ import annotations

from typing import Any

import httpx

UNIT_ABBREV = {
    "meter": "m",
    "metre": "m",
    "millimeter": "mm",
    "centimeter": "cm",
    "inch": "in",
    "foot": "ft",
    "kilogram": "kg",
    "gram": "g",
    "pound": "lb",
    "second": "s",
    "radian": "rad",
    "degree": "deg",
    "newton": "N",
    "pascal": "Pa",
    "kelvin": "K",
    "celsius": "degC",
}


class OnshapeError(Exception):
    pass


class NotConfigured(OnshapeError):
    pass


def _unit_string(unit_to_power: list[dict[str, Any]]) -> str:
    parts = []
    for entry in sorted(unit_to_power, key=lambda e: -float(e.get("value", 0))):
        name = str(entry.get("key", "?"))
        power = entry.get("value", 1)
        abbrev = UNIT_ABBREV.get(name, name)
        if power == 1:
            parts.append(abbrev)
        else:
            power = int(power) if float(power).is_integer() else power
            parts.append(f"{abbrev}^{power}")
    return "*".join(parts) if parts else ""


def decode_fs(value: Any) -> Any:
    """Collapse Onshape's FS value encoding into ordinary Python.

    Unknown `btType`s are passed through untouched — `verify` marks a run as `error`
    if one of them shows up inside a compared path, rather than guessing.
    """
    if not isinstance(value, dict):
        if isinstance(value, list):
            return [decode_fs(v) for v in value]
        return value

    bt = value.get("btType", "")
    if bt.startswith("BTFSValueMap"):
        out: dict[str, Any] = {}
        for pair in value.get("value", []):
            key = decode_fs(pair.get("key"))
            if not isinstance(key, str):
                key = repr(key)
            out[key] = decode_fs(pair.get("value"))
        return out
    if bt.startswith("BTFSValueArray"):
        return [decode_fs(v) for v in value.get("value", [])]
    if bt.startswith("BTFSValueWithUnits"):
        return {"value": value.get("value"), "units": _unit_string(value.get("unitToPower") or [])}
    if bt.startswith(("BTFSValueNumber", "BTFSValueString", "BTFSValueBoolean")):
        return value.get("value")
    if bt.startswith("BTFSValueUndefined") or bt.startswith("BTFSValueNothing"):
        return None
    if bt:
        return value  # unknown btType: pass through, poisoning any compared path
    return {k: decode_fs(v) for k, v in value.items()}


def has_undecoded(value: Any) -> bool:
    """True if a decoded structure still contains a raw FS value we did not understand."""
    if isinstance(value, dict):
        if isinstance(value.get("btType"), str):
            return True
        return any(has_undecoded(v) for v in value.values())
    if isinstance(value, list):
        return any(has_undecoded(v) for v in value)
    return False


class OnshapeClient:
    def __init__(
        self,
        access_key: str,
        secret_key: str,
        base: str = "https://cad.onshape.com",
        api_version: str = "v9",
        timeout_s: int = 60,
    ):
        self.access_key = access_key
        self.secret_key = secret_key
        self.base = base.rstrip("/")
        self.api_version = api_version
        self.timeout_s = timeout_s

    @property
    def configured(self) -> bool:
        return bool(self.access_key and self.secret_key)

    def _require(self) -> None:
        if not self.configured:
            raise NotConfigured(
                "Onshape credentials are not configured; set ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY"
            )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            auth=(self.access_key, self.secret_key),
            timeout=self.timeout_s,
            headers={"Accept": "application/json;charset=UTF-8;qs=0.09"},
        )

    def _wve(self, pin: dict[str, Any] | None, wid: str) -> tuple[str, str]:
        """Workspace or version path segment: models can be pinned to a frozen version."""
        if pin and pin.get("kind") == "version" and pin.get("vid"):
            return "v", pin["vid"]
        return "w", wid

    async def _get(self, url: str) -> Any:
        async with self._client() as client:
            resp = await client.get(url)
            _raise_for(resp)
            return resp.json()

    async def element_metadata(
        self, did: str, wid: str, eid: str, pin: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Reachability probe: does this element exist, and at what microversion?"""
        self._require()
        wv, wvid = self._wve(pin, wid)
        url = f"{self.base}/api/{self.api_version}/documents/d/{did}/{wv}/{wvid}/elements?elementId={eid}"
        elements = await self._get(url)
        if not elements:
            raise OnshapeError(f"no element {eid} in document {did}")
        element = elements[0]
        micro = await self.microversion(did, wid, pin)
        return {
            "name": element.get("name"),
            "element_type": element.get("elementType"),
            "microversion": element.get("microversionId") or micro,
            "data_type": element.get("dataType"),
        }

    async def microversion(self, did: str, wid: str, pin: dict[str, Any] | None = None) -> str | None:
        wv, wvid = self._wve(pin, wid)
        url = f"{self.base}/api/{self.api_version}/documents/d/{did}/{wv}/{wvid}/currentmicroversion"
        try:
            return (await self._get(url)).get("microversion")
        except OnshapeError:
            return None

    async def eval_featurescript(
        self,
        did: str,
        wid: str,
        eid: str,
        script: str,
        queries: list[Any] | None = None,
        pin: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST the FS lambda to a Part Studio and return `{result, sourceMicroversion, notices}`."""
        self._require()
        wv, wvid = self._wve(pin, wid)
        url = f"{self.base}/api/{self.api_version}/partstudios/d/{did}/{wv}/{wvid}/e/{eid}/featurescript"
        body = {"script": script, "queries": queries or [], "rejectMicroversionSkew": False}
        async with self._client() as client:
            resp = await client.post(url, json=body)
            _raise_for(resp)
            data = resp.json()
        notices = [n for n in (data.get("notices") or []) if n.get("level") in ("ERROR", "WARNING")]
        return {
            "result": decode_fs(data.get("result")),
            "raw": data.get("result"),
            "source_microversion": data.get("sourceMicroversion"),
            "notices": notices,
        }


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    hint = {
        401: "Onshape rejected the credentials (check ONSHAPE_ACCESS_KEY/ONSHAPE_SECRET_KEY)",
        403: "the API key has no access to this document",
        404: "no such document, workspace or element",
        409: "microversion skew — the document moved while the request was in flight",
        429: "Onshape rate limit; back off and retry",
    }.get(resp.status_code, "Onshape returned an error")
    body = resp.text[:500]
    raise OnshapeError(f"{hint} (HTTP {resp.status_code}): {body}")


def model_url(payload: dict[str, Any], base: str = "https://cad.onshape.com") -> str:
    if payload.get("url"):
        return str(payload["url"])
    pin = payload.get("pin") or {}
    if pin.get("kind") == "version" and pin.get("vid"):
        return f"{base}/documents/{payload.get('did')}/v/{pin['vid']}/e/{payload.get('eid')}"
    return f"{base}/documents/{payload.get('did')}/w/{payload.get('wid')}/e/{payload.get('eid')}"
