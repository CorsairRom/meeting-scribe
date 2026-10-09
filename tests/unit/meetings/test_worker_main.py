import io
import json
import os
import subprocess
import sys
from pathlib import Path

from meeting_scribe.worker import main

SRC = str(Path(__file__).parents[3] / "src")

JOB = json.dumps(
    {
        "protocol": 1,
        "stage": "transcribe",
        "meeting_dir": "/x",
        "device": "cuda",
        "transcription": {},
        "speakers": {},
    }
)


class Factories:
    """Stage factories that record whether anything asked for a stage to be built."""

    def __init__(self, error: Exception | None = None) -> None:
        self.built: list[str] = []
        self.error = error

    def transcribe(self):
        self.built.append("transcribe")
        if self.error:
            raise self.error
        raise AssertionError("the stage must not run in these tests")


def _drive(argv: list[str], stdin: str, factories: Factories):
    events = io.StringIO()
    code = main(
        argv,
        stdin=io.StringIO(stdin),
        events=events,
        stages={"transcribe": factories.transcribe},
    )
    return code, [json.loads(line) for line in events.getvalue().splitlines()]


def test_a_refused_invocation_builds_no_stage() -> None:
    for argv, stdin, code in (
        (["bogus"], JOB, "WORKER_UNKNOWN_STAGE"),
        (["diarize"], JOB, "WORKER_STAGE_UNAVAILABLE"),
        (["transcribe"], "not json", "WORKER_PROTOCOL_ERROR"),
    ):
        factories = Factories()

        exit_code, events = _drive(argv, stdin, factories)

        assert (exit_code, [e["error_code"] for e in events]) == (1, [code])
        assert factories.built == [], code


def test_a_stage_that_cannot_be_built_is_one_error_event_not_a_traceback() -> None:
    factories = Factories(error=ImportError("No module named 'numpy'"))

    exit_code, events = _drive(["transcribe"], JOB, factories)

    assert exit_code == 1
    assert [e["event"] for e in events] == ["error"]
    assert events[0]["error_code"] == "INTERNAL_ERROR"
    assert "numpy" in events[0]["message"]


def test_a_refused_invocation_imports_neither_the_adapters_nor_numpy() -> None:
    code = (
        "import io, sys\n"
        "from meeting_scribe.worker import main\n"
        "main(['bogus'], stdin=io.StringIO('{}'), events=io.StringIO())\n"
        "loaded = [m for m in ('numpy', 'meeting_scribe.meetings.infrastructure.wav_mix_reader',\n"
        "          'meeting_scribe.transcription.infrastructure.faster_whisper_backend')\n"
        "          if m in sys.modules]\n"
        "print(loaded)\n"
    )
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell, no model
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": SRC},
        timeout=30,
        check=False,
    )

    assert done.stdout.strip() == "[]", done.stderr
