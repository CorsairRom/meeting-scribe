import subprocess

import pytest
from returns.result import Failure, Success

from meeting_scribe.recording.domain.errors import AudioDeviceNotFoundError
from meeting_scribe.recording.infrastructure.pactl_device_resolver import (
    PactlDeviceResolver,
    SubprocessCommandRunner,
)

type Reply = subprocess.CompletedProcess[str] | Exception


def _done(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class FakeCommandRunner:
    """Replies per command; records calls. Never spawns a process."""

    def __init__(self, replies: dict[str, Reply]) -> None:
        self.replies = replies
        self.calls: list[list[str]] = []

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        reply = self.replies[command[1]]
        if isinstance(reply, Exception):
            raise reply
        return reply


def _resolver(**replies: Reply) -> tuple[PactlDeviceResolver, FakeCommandRunner]:
    mapping: dict[str, Reply] = {
        "get-default-sink": _done("alsa_output.speakers\n"),
        "get-default-source": _done("alsa_input.usb-mic\n"),
    }
    mapping.update({key.replace("_", "-"): value for key, value in replies.items()})
    runner = FakeCommandRunner(mapping)
    return PactlDeviceResolver(runner), runner


def test_resolves_monitor_of_default_sink_and_default_source() -> None:
    resolver, runner = _resolver()

    result = resolver.resolve()

    devices = result.unwrap()
    assert devices.monitor_source == "alsa_output.speakers.monitor"
    assert devices.mic_source == "alsa_input.usb-mic"
    assert runner.calls == [["pactl", "get-default-sink"], ["pactl", "get-default-source"]]


def test_pactl_missing_fails() -> None:
    resolver, _ = _resolver(get_default_sink=FileNotFoundError("pactl"))

    result = resolver.resolve()

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), AudioDeviceNotFoundError)
    assert "pactl" in str(result.failure())


def test_non_zero_exit_fails() -> None:
    resolver, _ = _resolver(get_default_source=_done("", returncode=1))

    result = resolver.resolve()

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), AudioDeviceNotFoundError)


def test_empty_output_fails() -> None:
    resolver, _ = _resolver(get_default_sink=_done("  \n"))

    result = resolver.resolve()

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), AudioDeviceNotFoundError)


def test_timeout_fails() -> None:
    resolver, _ = _resolver(get_default_source=subprocess.TimeoutExpired("pactl", 5))

    result = resolver.resolve()

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), AudioDeviceNotFoundError)


def test_default_source_that_is_a_monitor_means_no_microphone() -> None:
    resolver, _ = _resolver(get_default_source=_done("alsa_output.speakers.monitor\n"))

    result = resolver.resolve()

    assert isinstance(result, Failure)
    assert "microphone" in str(result.failure())


def test_success_is_a_success_result() -> None:
    resolver, _ = _resolver()

    assert isinstance(resolver.resolve(), Success)


def test_subprocess_runner_wraps_subprocess_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        seen.update(kwargs)
        return _done("x")

    monkeypatch.setattr(subprocess, "run", fake_run)

    SubprocessCommandRunner().run(["pactl", "get-default-sink"])

    assert seen == {
        "command": ["pactl", "get-default-sink"],
        "capture_output": True,
        "text": True,
        "timeout": 5,
        "check": False,
    }
