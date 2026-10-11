"""Parsers turn the plain values of the Summaries1 replies into models (AC-16, AC-17)."""

from datetime import UTC, datetime

from meeting_scribe_review.core.errors import INVALID_REPLY, ServiceError
from meeting_scribe_review.core.models import ProviderRow, SummaryDocument, SummaryInfo
from meeting_scribe_review.core.parsers import (
    parse_cancelled,
    parse_providers,
    parse_started,
    parse_summary_document,
    parse_summary_info,
)
from meeting_scribe_review.core.result import Err, Ok

PROVIDER = {
    "id": "local",
    "label": "Local (fake-model)",
    "cloud": False,
    "model": "fake-model",
    "available": True,
    "reason": "",
    "default": True,
}
INFO = {
    "state": "DONE",
    "stale": True,
    "cloud": False,
    "provider_label": "Local (fake-model)",
    "generated_at": "2026-10-09T11:00:00+00:00",
    "step": 2,
    "total_steps": 3,
    "failure_code": "",
}
DOCUMENT = {
    "markdown": "# Fake title\n\nFake body.",
    "title": "Fake title",
    "generated_at": "2026-10-09T11:00:00+00:00",
    "provider_label": "cloud: Fake Gateway, fake-model",
    "cloud": True,
    "model": "fake-model",
    "language": "en",
    "stale": False,
}
INVALID = Err(ServiceError(INVALID_REPLY))


def test_parse_providers() -> None:
    assert parse_providers([PROVIDER]) == Ok(
        [ProviderRow("local", "Local (fake-model)", False, "fake-model", True, "", True)]
    )


def test_parse_summary_info() -> None:
    assert parse_summary_info(INFO) == Ok(
        SummaryInfo(
            state="DONE",
            stale=True,
            cloud=False,
            provider_label="Local (fake-model)",
            generated_at=datetime(2026, 10, 9, 11, 0, tzinfo=UTC),
            step=2,
            total_steps=3,
            failure_code="",
        )
    )


def test_an_empty_generated_at_means_no_summary_file() -> None:
    result = parse_summary_info({**INFO, "state": "NONE", "generated_at": ""})
    assert isinstance(result, Ok)
    assert result.value.generated_at is None
    assert not result.value.has_summary


def test_parse_summary_document() -> None:
    result = parse_summary_document(DOCUMENT)
    assert isinstance(result, Ok)
    assert result.value == SummaryDocument(
        markdown="# Fake title\n\nFake body.",
        title="Fake title",
        generated_at=datetime(2026, 10, 9, 11, 0, tzinfo=UTC),
        provider_label="cloud: Fake Gateway, fake-model",
        cloud=True,
        model="fake-model",
        language="en",
        stale=False,
    )


def test_parse_started_and_cancelled() -> None:
    assert parse_started("RUNNING") == Ok("RUNNING")
    assert parse_cancelled(True) == Ok(True)
    assert parse_cancelled(False) == Ok(False)


def test_malformed_replies_are_invalid_without_quoting_them() -> None:
    assert parse_providers("nope") == INVALID
    assert parse_providers([{**PROVIDER, "cloud": "yes"}]) == INVALID
    assert parse_providers([{k: v for k, v in PROVIDER.items() if k != "default"}]) == INVALID
    assert parse_summary_info([]) == INVALID
    assert parse_summary_info({**INFO, "step": True}) == INVALID
    assert parse_summary_info({**INFO, "generated_at": "not a date"}) == INVALID
    assert parse_summary_document({**DOCUMENT, "markdown": 3}) == INVALID
    assert parse_summary_document({**DOCUMENT, "generated_at": ""}) == INVALID
    assert parse_started(7) == INVALID
    assert parse_cancelled("true") == INVALID
