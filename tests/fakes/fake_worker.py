"""A scripted stand-in for ``python -m meeting_scribe.worker`` (never loads a model).

Usage: ``python fake_worker.py <stage>``. Reads the job spec (JSON) from stdin, then plays the
operations listed in ``<meeting_dir>/scenario.json``:

- ``{"emit": {...}}``: one protocol line; ``{"raw": "text"}``: a raw stdout line.
- ``{"stderr": "text"}``, ``{"sleep": seconds}``, ``{"hang": true}`` (never returns).
- ``{"ignore_sigterm": true}``, ``{"pidfile": "name"}`` (pid written into the meeting folder).
- ``{"echo_job": true}``: a ``cache_hit`` naming the stage and protocol it received.
- ``{"echo_env": ["NAME", ...]}``: a ``cache_hit`` listing ``NAME=value`` of its environment.
- ``{"exit": code}``, ``{"kill_self": signal}``.
"""

import json
import os
import signal
import sys
import time
from pathlib import Path


def _line(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _stderr(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _hang(_: object) -> None:
    while True:
        time.sleep(0.05)


def _kill_self(number: int) -> None:
    os.kill(os.getpid(), number)
    time.sleep(5)


def main() -> None:
    job = json.loads(sys.stdin.read())
    folder = Path(job["meeting_dir"])
    handlers = {
        "emit": lambda value: _line(json.dumps(value)),
        "raw": _line,
        "stderr": _stderr,
        "sleep": time.sleep,
        "hang": _hang,
        "ignore_sigterm": lambda _: signal.signal(signal.SIGTERM, signal.SIG_IGN),
        "pidfile": lambda name: (folder / name).write_text(str(os.getpid()), encoding="utf-8"),
        "echo_job": lambda _: _line(
            json.dumps({"event": "cache_hit", "artifact": f"{job['stage']}:{job['protocol']}"})
        ),
        "echo_env": lambda names: _line(
            json.dumps(
                {
                    "event": "cache_hit",
                    "artifact": ",".join(f"{n}={os.environ.get(n)}" for n in names),
                }
            )
        ),
        "exit": sys.exit,
        "kill_self": _kill_self,
    }
    for operation in json.loads((folder / "scenario.json").read_text(encoding="utf-8")):
        for name, argument in operation.items():
            handlers[name](argument)


if __name__ == "__main__":
    main()
