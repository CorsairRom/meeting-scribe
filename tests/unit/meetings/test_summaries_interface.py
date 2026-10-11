"""``SummariesInterface``: row shapes, error mapping, signal emission and throttling."""

import asyncio

import pytest
from dbus_fast import DBusError

from meeting_scribe.meetings.application.summary_events import ProgressThrottle
from meeting_scribe.meetings.infrastructure.dbus.summaries_interface import SummariesInterface
from meeting_scribe.summaries.domain.errors import SummaryProviderUnavailableError
from tests.unit.meetings.test_summary_controller import Rig

PREFIX = "dev.meetingscribe.Error."


class Harness:
    def __init__(self, rig: Rig, monkeypatch: pytest.MonkeyPatch, now: list[float]) -> None:
        self.rig = rig
        self.interface = SummariesInterface(rig.controller, ProgressThrottle(1.0, lambda: now[0]))
        self.progress: list[tuple[str, int, int]] = []
        self.finished: list[tuple[str, str, str]] = []
        self.published = 0
        monkeypatch.setattr(self.interface, "_emit_progress", lambda *a: self.progress.append(a))
        monkeypatch.setattr(self.interface, "_emit_finished", lambda *a: self.finished.append(a))
        monkeypatch.setattr(self.interface, "_publish_active", self._publish)

    def _publish(self) -> None:
        self.published += 1


def _harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    rig = Rig()
    rig.add()
    rig.events.log.clear()
    return Harness(rig, monkeypatch, [100.0])


async def _error(call) -> str:
    with pytest.raises(DBusError) as excinfo:
        await call
    return excinfo.value.type


def test_providers_rows_carry_typed_variants_and_the_cloud_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        rows = await _harness(monkeypatch).interface.providers()
        shaped = [{k: (v.signature, v.value) for k, v in row.items()} for row in rows]
        assert shaped[0] == {
            "id": ("s", "local"),
            "label": ("s", "Local (fake-model)"),
            "cloud": ("b", False),
            "model": ("s", "fake-model"),
            "available": ("b", True),
            "reason": ("s", ""),
            "default": ("b", True),
        }
        assert shaped[1]["label"] == ("s", "cloud: Fake Gateway, fake-cloud-model")
        assert shaped[1]["cloud"] == ("b", True)
        assert "url" not in shaped[1]
        assert "key" not in shaped[1]

    asyncio.run(scenario())


def test_summarize_returns_running_and_errors_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        interface = _harness(monkeypatch).interface
        assert await _error(interface.summarize("m1", "cloud", False)) == (
            PREFIX + "SUMMARY_CLOUD_NOT_CONFIRMED"
        )
        assert await _error(interface.summarize("nope", "local", False)) == (
            PREFIX + "ENTITY_NOT_FOUND_MEETING"
        )
        assert await _error(interface.summarize("m1", "other", False)) == PREFIX + "INVALID_VALUE"
        assert await interface.summarize("m1", "local", False) == "RUNNING"
        assert await _error(interface.summarize("m1", "local", False)) == (
            PREFIX + "SUMMARY_ALREADY_RUNNING"
        )

    asyncio.run(scenario())


def test_status_summary_and_cancel_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        harness = _harness(monkeypatch)
        interface = harness.interface
        none = {
            k: (v.signature, v.value) for k, v in (await interface.get_summary_status("m1")).items()
        }
        assert none == {
            "state": ("s", "NONE"),
            "stale": ("b", False),
            "cloud": ("b", False),
            "provider_label": ("s", ""),
            "generated_at": ("s", ""),
            "step": ("i", 0),
            "total_steps": ("i", 0),
            "failure_code": ("s", ""),
        }
        assert await _error(interface.get_summary("m1")) == PREFIX + "ENTITY_NOT_FOUND_SUMMARY"
        assert await _error(interface.last_summary()) == PREFIX + "ENTITY_NOT_FOUND_SUMMARY"
        assert await interface.cancel_summary("m1") is False
        assert await _error(interface.cancel_summary("nope")) == (
            PREFIX + "ENTITY_NOT_FOUND_MEETING"
        )
        harness.rig.summarize_and_run()
        status = await interface.get_summary_status("m1")
        assert (status["state"].value, status["generated_at"].value != "") == ("DONE", True)
        document = {
            k: (v.signature, v.value) for k, v in (await interface.get_summary("m1")).items()
        }
        assert set(document) == {
            "markdown",
            "title",
            "generated_at",
            "provider_label",
            "cloud",
            "model",
            "language",
            "stale",
        }
        assert document["title"] == ("s", "Synthetic title")
        last = {k: (v.signature, v.value) for k, v in (await interface.last_summary()).items()}
        assert set(last) == {"meeting_id", "started_at", "generated_at", "path"}
        assert last["meeting_id"] == ("s", "m1")

    asyncio.run(scenario())


def test_events_become_signals_on_the_loop_and_progress_is_throttled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        harness = _harness(monkeypatch)
        interface = harness.interface
        interface.progress("m1", 1, 4)
        interface.progress("m1", 2, 4)  # inside the same second
        interface.progress("m1", 4, 4)  # the last step always goes out
        interface.finished("m1", "DONE", "")
        interface.active_changed()
        await asyncio.sleep(0)
        assert harness.progress == [("m1", 1, 4), ("m1", 4, 4)]
        assert harness.finished == [("m1", "DONE", "")]
        assert harness.published == 1
        interface.progress("m2", 1, 4)  # a new run is not throttled by the last one
        await asyncio.sleep(0)
        assert harness.progress[-1] == ("m2", 1, 4)

    asyncio.run(scenario())


def test_a_failed_summary_announces_its_code_and_no_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        harness = _harness(monkeypatch)
        harness.rig.local_provider.responder = lambda _m: SummaryProviderUnavailableError("down")
        harness.rig.controller._events = harness.interface  # type: ignore[assignment]
        await harness.interface.summarize("m1", "local", False)
        await asyncio.to_thread(harness.rig.starter.run_next)
        await asyncio.sleep(0.05)
        assert harness.finished == [("m1", "FAILED", "SUMMARY_PROVIDER_UNAVAILABLE")]

    asyncio.run(scenario())
