"""The runner sidecar: POST /run executes one calculator script and returns its result.

Isolation is the container plus a forked child with rlimits — enough to stop accidents
and casual mischief, *not* a security boundary against a determined attacker. See
design §4.1/§11.1: swap in gVisor or Firecracker before pointing this at untrusted input.
"""

from __future__ import annotations

import contextlib
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 8 << 20
DEFAULT_TIMEOUT_S = 30
DEFAULT_MEM_MB = 512
BOOTSTRAP = Path(__file__).with_name("bootstrap.py")


def package_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for name in ("numpy", "scipy", "pint"):
        try:
            import importlib.metadata as md

            out[name] = md.version(name)
        except Exception:  # noqa: BLE001 - a missing optional package is not an error
            continue
    return out


RUNTIME = {
    "image": os.environ.get("E2_RUNNER_IMAGE", "engineer2-runner:0.1"),
    "python": sys.version.split()[0],
    "packages": package_versions(),
}


def _limits(mem_mb: int, timeout_s: int):
    def apply() -> None:
        mem = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        cpu = max(1, timeout_s)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_FSIZE, (32 << 20, 32 << 20))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        os.setsid()  # own process group, so a timeout can kill the whole tree

    return apply


def run_script(script: str, inputs: dict, timeout_s: int, mem_mb: int) -> dict:
    workdir = Path(tempfile.mkdtemp(prefix="e2run-"))
    started = time.time()
    try:
        (workdir / "script.py").write_text(script, encoding="utf-8")
        (workdir / "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
        shutil.copy(BOOTSTRAP, workdir / "bootstrap.py")
        read_fd, write_fd = os.pipe()
        proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-I", "-B", "bootstrap.py"],
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(write_fd,),
            preexec_fn=_limits(mem_mb, timeout_s),
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(workdir),
                "PYTHONHASHSEED": "0",
                # subprocess closes fds above 2 after preexec_fn, so the result pipe keeps
                # its original number and the child is told which one it is.
                "E2_RESULT_FD": str(write_fd),
            },
        )
        os.close(write_fd)
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        payload = os.read(read_fd, 8 << 20)
        os.close(read_fd)
        duration_ms = int((time.time() - started) * 1000)

        if timed_out:
            return {
                "ok": False,
                "error": {
                    "type": "Timeout",
                    "message": f"script exceeded {timeout_s}s and was killed",
                    "traceback": "",
                },
                "stdout": _txt(stdout),
                "stderr": _txt(stderr),
                "duration_ms": duration_ms,
                "runtime": RUNTIME,
            }
        if not payload:
            killed = proc.returncode is not None and proc.returncode < 0
            message = (
                f"script died (signal {-proc.returncode}) — usually the memory limit of {mem_mb} MB"
                if killed
                else f"script produced no result (exit {proc.returncode})"
            )
            return {
                "ok": False,
                "error": {"type": "NoResult", "message": message, "traceback": ""},
                "stdout": _txt(stdout),
                "stderr": _txt(stderr),
                "duration_ms": duration_ms,
                "runtime": RUNTIME,
            }
        result = json.loads(payload.decode())
        result["stdout"] = _txt(stdout)
        result["stderr"] = _txt(stderr)
        result["duration_ms"] = duration_ms
        result["runtime"] = RUNTIME
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _txt(b: bytes | None) -> str:
    return (b or b"").decode("utf-8", "replace")[-8000:]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        if self.path.rstrip("/") in ("/health", ""):
            self._send(200, {"ok": True, "runtime": RUNTIME})
        else:
            self._send(404, {"ok": False, "error": {"type": "NotFound", "message": self.path}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/run":
            self._send(404, {"ok": False, "error": {"type": "NotFound", "message": self.path}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send(413, {"ok": False, "error": {"type": "TooLarge", "message": "body too large"}})
            return
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self._send(400, {"ok": False, "error": {"type": "BadRequest", "message": str(exc)}})
            return
        script = req.get("script")
        if not isinstance(script, str):
            self._send(400, {"ok": False, "error": {"type": "BadRequest", "message": "script is required"}})
            return
        result = run_script(
            script,
            req.get("inputs") or {},
            int(req.get("timeout_s") or DEFAULT_TIMEOUT_S),
            int(req.get("mem_mb") or DEFAULT_MEM_MB),
        )
        self._send(200, result)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("runner: " + fmt % args + "\n")


def main() -> None:
    port = int(os.environ.get("E2_RUNNER_PORT", 8001))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    sys.stderr.write(f"runner listening on :{port} ({RUNTIME})\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
