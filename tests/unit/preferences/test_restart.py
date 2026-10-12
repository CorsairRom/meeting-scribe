"""``Restart``: only when idle, only when systemd answers (AC-8). Never the real systemd."""

import asyncio

from returns.result import Failure

from meeting_scribe.preferences.application.restart_service import RestartService
from tests.unit.preferences.fakes import FakeRestarter


class Activity:
    def __init__(self) -> None:
        self.is_busy = False

    def busy(self) -> bool:
        return self.is_busy


def _service(activity: Activity, restarter: FakeRestarter) -> RestartService:
    return RestartService(activity, restarter)


def test_a_busy_service_refuses_and_keeps_running() -> None:
    activity, restarter = Activity(), FakeRestarter()
    activity.is_busy = True

    result = asyncio.run(_service(activity, restarter).check())

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SETTINGS_RESTART_BUSY"
    assert restarter.restarts == 0


def test_an_idle_service_replies_restarting_and_does_not_restart_yet() -> None:
    restarter = FakeRestarter()

    result = asyncio.run(_service(Activity(), restarter).check())

    assert result.unwrap() == "RESTARTING"
    assert restarter.restarts == 0  # the interface calls perform() after the reply is sent


def test_perform_asks_systemd_once() -> None:
    restarter = FakeRestarter()

    asyncio.run(_service(Activity(), restarter).perform())

    assert restarter.restarts == 1


def test_perform_rechecks_the_activity_after_the_reply() -> None:
    activity, restarter = Activity(), FakeRestarter()
    service = _service(activity, restarter)
    asyncio.run(service.check()).unwrap()
    activity.is_busy = True  # a recording started in the gap

    asyncio.run(service.perform())

    assert restarter.restarts == 0


def test_an_unreachable_systemd_is_unavailable_with_the_manual_command() -> None:
    result = asyncio.run(_service(Activity(), FakeRestarter(reachable=False)).check())

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SETTINGS_RESTART_UNAVAILABLE"
    assert "systemctl --user restart meeting-scribe.service" in str(result.failure())


def test_a_failed_restart_after_the_reply_is_logged_not_raised(caplog) -> None:
    restarter = FakeRestarter()
    restarter.fail = True

    asyncio.run(_service(Activity(), restarter).perform())

    assert "SETTINGS_RESTART_UNAVAILABLE" in caplog.text


def test_a_probe_that_raises_is_unavailable_not_an_exception() -> None:
    restarter = FakeRestarter()
    restarter.explode = True

    result = asyncio.run(_service(Activity(), restarter).check())

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SETTINGS_RESTART_UNAVAILABLE"


def test_a_restart_that_raises_after_the_reply_is_logged(caplog) -> None:
    restarter = FakeRestarter()
    restarter.explode = True

    asyncio.run(_service(Activity(), restarter).perform())

    assert "RuntimeError" in caplog.text
    assert "exploded" not in caplog.text
