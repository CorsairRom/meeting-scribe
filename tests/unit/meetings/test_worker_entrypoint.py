import json
import os
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).parents[3] / "src")
ENV = {**os.environ, "PYTHONPATH": SRC}
ML_LIBRARIES = ("torch", "torchaudio", "faster_whisper", "ctranslate2", "pyannote")


def _run(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, no model
        [sys.executable, *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=ENV,
        timeout=30,
        check=False,
    )


def test_unknown_stage_is_rejected_with_an_error_event_and_exit_1() -> None:
    done = _run("-m", "meeting_scribe.worker", "bogus", stdin="{}")

    assert done.returncode == 1
    event = json.loads(done.stdout.strip())
    assert event["event"] == "error"
    assert event["error_code"] == "WORKER_UNKNOWN_STAGE"
    assert "bogus" in event["message"]


def test_known_stages_are_not_available_until_their_task_lands() -> None:
    done = _run("-m", "meeting_scribe.worker", "transcribe", stdin="{}")

    assert done.returncode == 1
    assert json.loads(done.stdout.strip())["error_code"] == "WORKER_STAGE_UNAVAILABLE"


def test_a_missing_stage_argument_is_an_error_event_too() -> None:
    done = _run("-m", "meeting_scribe.worker")

    assert done.returncode == 1
    assert json.loads(done.stdout.strip())["event"] == "error"


def test_stray_prints_go_to_stderr_and_never_corrupt_the_event_stream() -> None:
    code = (
        "from meeting_scribe.worker import open_event_stream\n"
        "events = open_event_stream()\n"
        "print('a library says hello')\n"
        'events.write(\'{"event": "cache_hit", "artifact": "x"}\\n\'); events.flush()\n'
    )

    done = _run("-c", code)

    assert done.stdout == '{"event": "cache_hit", "artifact": "x"}\n'
    assert "a library says hello" in done.stderr


def test_the_worker_skeleton_imports_no_ml_library() -> None:
    code = (
        "import sys, meeting_scribe.worker\n"
        f"print([m for m in {ML_LIBRARIES!r} if m in sys.modules])\n"
    )

    assert _run("-c", code).stdout.strip() == "[]"
