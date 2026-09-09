"""Runs inside the forked child: exec the script, call run(inputs), write JSON to the result pipe.

Deliberately tiny. Everything it can go wrong with is reported as a structured error
rather than a traceback on stderr that nobody reads.
"""

from __future__ import annotations

import json
import os
import sys
import traceback


def main() -> int:
    out_fd = int(os.environ.get("E2_RESULT_FD", 3))
    try:
        script = open("script.py", encoding="utf-8").read()
        inputs = json.loads(open("inputs.json", encoding="utf-8").read())
    except Exception as exc:  # noqa: BLE001 - the parent needs a message, not a crash
        os.write(out_fd, json.dumps({"ok": False, "error": _err(exc, "setup")}).encode())
        return 1

    namespace: dict = {"__name__": "calculator"}
    try:
        exec(compile(script, "calculator.py", "exec"), namespace)  # noqa: S102 - the container is the boundary
    except Exception as exc:  # noqa: BLE001
        os.write(out_fd, json.dumps({"ok": False, "error": _err(exc, "import")}).encode())
        return 1

    fn = namespace.get("run")
    if not callable(fn):
        payload = {
            "ok": False,
            "error": {
                "type": "ContractError",
                "message": "script must define run(inputs: dict) -> dict",
                "traceback": "",
            },
        }
        os.write(out_fd, json.dumps(payload).encode())
        return 1

    try:
        result = fn(inputs)
    except Exception as exc:  # noqa: BLE001
        os.write(out_fd, json.dumps({"ok": False, "error": _err(exc, "run")}).encode())
        return 1

    try:
        body = json.dumps({"ok": True, "result": result})
    except (TypeError, ValueError) as exc:
        payload = {
            "ok": False,
            "error": {
                "type": "NotSerialisable",
                "message": (
                    f"run() returned something JSON cannot represent ({exc}). "
                    "Cast numpy scalars/arrays to Python floats and lists before returning."
                ),
                "traceback": "",
            },
        }
        os.write(out_fd, json.dumps(payload).encode())
        return 1

    os.write(out_fd, body.encode())
    return 0


def _err(exc: BaseException, phase: str) -> dict:
    return {
        "type": type(exc).__name__,
        "message": f"{exc}" or phase,
        "phase": phase,
        "traceback": "".join(traceback.format_exception(exc))[-4000:],
    }


if __name__ == "__main__":
    sys.exit(main())
