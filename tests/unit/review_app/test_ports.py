"""The ports are structural: the fakes satisfy them, so presenters can be tested without gi."""

from meeting_scribe_review.core.gateway import (
    MeetingsGateway,
    SamplePlayer,
    SettingsGateway,
    SummariesGateway,
)
from meeting_scribe_review.core.models import Speaker
from meeting_scribe_review.core.result import Ok
from tests.unit.review_app.fakes import (
    FakeGateway,
    FakePlayer,
    FakeSettingsGateway,
    FakeSummariesGateway,
    speaker,
)


def test_the_fake_gateway_is_a_meetings_gateway() -> None:
    assert isinstance(FakeGateway(), MeetingsGateway)


def test_the_fake_player_is_a_sample_player() -> None:
    assert isinstance(FakePlayer(), SamplePlayer)


def test_the_fake_gateway_answers_synchronously_with_its_script() -> None:
    gateway = FakeGateway()
    gateway.replies["get_speakers"] = Ok([speaker("S1")])
    got: list[object] = []
    gateway.get_speakers("m1", got.append)
    assert got == [Ok([speaker("S1")])]
    assert isinstance(speaker("S1"), Speaker)


def test_the_fake_summaries_gateway_is_a_summaries_gateway() -> None:
    assert isinstance(FakeSummariesGateway(), SummariesGateway)


def test_the_fake_settings_gateway_is_a_settings_gateway() -> None:
    assert isinstance(FakeSettingsGateway(), SettingsGateway)
