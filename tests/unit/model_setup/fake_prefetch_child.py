"""A stand-in for ``meeting-scribe prefetch-models`` used by the runner and probe tests.

It never downloads anything. It records what it was given (argv, environment, the first stdin line
when ``--token-stdin`` is present) in the JSON file named by ``FAKE_CHILD_REPORT`` and then acts
out the mode in ``FAKE_CHILD_MODE``.
"""

import json
import os
import sys
import time
from pathlib import Path

mode = os.environ.get("FAKE_CHILD_MODE", "ok")
report = os.environ.get("FAKE_CHILD_REPORT")
stdin_line = sys.stdin.readline().strip() if "--token-stdin" in sys.argv else None
if report:
    with Path(report).open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "argv": sys.argv[1:],
                "env": dict(os.environ),
                "stdin": stdin_line,
                "pid": os.getpid(),
            },
            handle,
        )


def emit(event: dict[str, object]) -> None:
    print(json.dumps(event), flush=True)


if "--check" in sys.argv:
    print(os.environ.get("FAKE_CHECK_OUTPUT", '{"whisper": true, "pipeline": true}'), flush=True)
    sys.exit(int(os.environ.get("FAKE_CHECK_EXIT", "0")))

emit({"event": "step", "model": "pipeline", "step": 1, "total_steps": 2})
if mode == "ok":
    emit({"event": "step", "model": "whisper", "step": 2, "total_steps": 2})
    emit({"event": "finished", "ready": True})
elif mode == "access":
    emit(
        {
            "event": "error",
            "model": "pipeline",
            "error_code": "DIARIZATION_MODEL_ACCESS_DENIED",
            "message": "x",
        }
    )
    emit({"event": "error", "model": "whisper", "error_code": "MODEL_UNAVAILABLE", "message": "y"})
    emit({"event": "finished", "ready": False})
    sys.exit(1)
elif mode == "silent_exit":
    sys.exit(3)
elif mode == "noise":
    print("not json at all", flush=True)
    print('{"event": "mystery"}', flush=True)
    emit({"event": "step", "model": "whisper", "step": 2, "total_steps": 2})
    emit({"event": "finished", "ready": True})
elif mode == "leaky_stderr":
    print(f"boom {stdin_line} and hf_AnotherShapedToken99", file=sys.stderr, flush=True)
    sys.exit(2)
elif mode == "straddle":
    # One line whose last 1000 characters begin inside the token (token is 26 characters long).
    print("a" * 5 + str(stdin_line) + "b" * 984, file=sys.stderr, flush=True)
    sys.exit(2)
elif mode == "progress_bar":
    # tqdm redraws with \r and never writes \n: millions of characters in one "line".
    for percent in range(200_000):
        sys.stderr.write(f"download {percent}%\r")
    sys.stderr.write(f"token {stdin_line}\r")
    sys.stderr.flush()
    sys.exit(2)
elif mode == "endless_segment":
    sys.stderr.write("x" * 3_000_000 + str(stdin_line) + "y" * 10)  # no separator at all
    sys.stderr.flush()
    sys.exit(2)
elif mode == "hang":
    time.sleep(120)
