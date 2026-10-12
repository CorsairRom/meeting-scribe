"""The ports are structural: the fakes satisfy them, so presenters can be tested without gi."""

from meeting_scribe_review.core.gateway import (
    MeetingsGateway,
    ModelsGateway,
    SamplePlayer,
    SettingsGateway,
    SummariesGateway,
    TokenStore,
)
from meeting_scribe_review.core.models import Speaker
from meeting_scribe_review.core.result import Ok
from tests.unit.review_app.fakes import (
    FakeGateway,
    FakeModelsGateway,
    FakePlayer,
    FakeSettingsGateway,
    FakeSummariesGateway,
    FakeTokenStore,
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


def test_the_fake_models_gateway_is_a_models_gateway() -> None:
    assert isinstance(FakeModelsGateway(), ModelsGateway)


def test_the_fake_token_store_is_a_token_store() -> None:
    assert isinstance(FakeTokenStore(), TokenStore)
