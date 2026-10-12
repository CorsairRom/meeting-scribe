"""A model change is checked against the model list when it is available (AC-19)."""

from pathlib import Path

from returns.result import Failure, Success

from meeting_scribe.preferences.application.model_check import ModelCheck
from meeting_scribe.preferences.domain.fields import FieldCode
from meeting_scribe.preferences.domain.models import MODELS_OK, ModelListing, ModelRow
from tests.unit.preferences.fakes import Service

KEY = "summaries.cloud_model"


class FakeCatalog:
    """Answers from its cache only: a ``Set`` must never reach the network (it holds the lock)."""

    def __init__(self, listing: ModelListing | None) -> None:
        self.listing = listing
        self.scopes: list[str] = []
        self.fetches = 0

    def cached_models(self, scope: str) -> ModelListing | None:
        self.scopes.append(scope)
        return self.listing

    def list_models(self, scope: str) -> ModelListing:
        del scope
        self.fetches += 1
        raise AssertionError("Set must not fetch the model list")


def available(*rows: ModelRow) -> ModelListing:
    return ModelListing(MODELS_OK, rows)


LISTED = available(ModelRow("glm-5", True, ""), ModelRow("gpt-5", False, "NOT_SUPPORTED_YET"))


def check(listing: ModelListing | None, raw: str, scope: str = "cloud"):
    return ModelCheck(FakeCatalog(listing), scope, KEY)(raw)


def test_a_listed_supported_model_passes() -> None:
    assert check(LISTED, "glm-5") == Success("glm-5")


def test_a_model_missing_from_an_available_list_is_invalid_without_its_name() -> None:
    result = check(LISTED, "made-up-model")

    problem = result.failure()
    assert (problem.key, problem.code) == (KEY, FieldCode.INVALID)
    assert "made-up-model" not in problem.message
    assert "list" in problem.message


def test_an_unsupported_model_is_invalid() -> None:
    problem = check(LISTED, "gpt-5").failure()

    assert problem.code is FieldCode.INVALID
    assert "not supported yet" in problem.message
    assert "gpt-5" not in problem.message


def test_an_unavailable_list_never_blocks_a_save() -> None:
    assert check(ModelListing("SUMMARY_CLOUD_KEY_MISSING"), "anything") == Success("anything")
    assert check(ModelListing("SUMMARY_MODELS_UNAVAILABLE"), "anything") == Success("anything")


def test_no_fresh_list_accepts_the_save_without_a_fetch() -> None:
    catalog = FakeCatalog(None)

    assert ModelCheck(catalog, "cloud", KEY)("anything") == Success("anything")
    assert catalog.fetches == 0


def test_the_check_asks_for_its_own_scope() -> None:
    catalog = FakeCatalog(LISTED)

    ModelCheck(catalog, "local", "summaries.local_model")("glm-5")

    assert catalog.scopes == ["local"]


def test_set_rejects_an_unsupported_model_and_saves_nothing(tmp_path: Path) -> None:
    rig = Service(tmp_path, checks={KEY: ModelCheck(FakeCatalog(LISTED), "cloud", KEY)})

    outcome = rig.service.set({KEY: "gpt-5", "summaries.language": "es"}).unwrap()

    assert outcome.applied == ()
    assert [(e.key, e.code) for e in outcome.errors] == [(KEY, FieldCode.INVALID)]
    assert rig.store.writes == []


def test_set_saves_a_listed_model(tmp_path: Path) -> None:
    rig = Service(tmp_path, checks={KEY: ModelCheck(FakeCatalog(LISTED), "cloud", KEY)})

    outcome = rig.service.set({KEY: "glm-5"}).unwrap()

    assert outcome.applied == (KEY,)
    assert rig.store.values == {KEY: "glm-5"}


def test_a_failed_check_is_a_failure_type() -> None:
    assert isinstance(check(LISTED, "nope"), Failure)
