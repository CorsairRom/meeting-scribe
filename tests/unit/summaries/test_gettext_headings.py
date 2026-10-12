from pathlib import Path

import pytest

from meeting_scribe.summaries.domain.headings import ENGLISH, Headings
from meeting_scribe.summaries.infrastructure.gettext_headings import (
    GettextHeadingCatalog,
    load_catalog,
)

PO = """# synthetic catalog
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "Topics"
msgstr "Alpha"

msgid "Decisions"
msgstr "Beta \\"quoted\\""

msgid "Action items"
msgstr ""

msgid "Open questions"
msgstr "Gamma "
"split"
"""


def _locale(tmp_path: Path, language: str, content: str = PO) -> Path:
    folder = tmp_path / language / "LC_MESSAGES"
    folder.mkdir(parents=True)
    (folder / "meeting_scribe.po").write_text(content, encoding="utf-8")
    return tmp_path


def test_a_catalog_translates_its_entries_and_leaves_the_rest_in_english(tmp_path: Path) -> None:
    catalog = GettextHeadingCatalog(_locale(tmp_path, "zz"))

    headings = catalog.headings_for("zz")

    assert headings.topics == "Alpha"
    assert headings.decisions == 'Beta "quoted"'
    assert headings.open_questions == "Gamma split"
    assert headings.action_items == ENGLISH.action_items
    assert headings.owner == ENGLISH.owner


def test_english_and_unknown_languages_get_english(tmp_path: Path) -> None:
    catalog = GettextHeadingCatalog(_locale(tmp_path, "zz"))

    assert catalog.headings_for("en") == ENGLISH
    assert catalog.headings_for("xx") == ENGLISH
    assert catalog.headings_for("") == ENGLISH


@pytest.mark.parametrize("language", ["../zz", "zz/../zz", "ZZ", "z", "zzzzz", "z z", "zz\n"])
def test_a_language_that_is_not_a_plain_lowercase_code_is_never_looked_up(
    tmp_path: Path, language: str
) -> None:
    catalog = GettextHeadingCatalog(_locale(tmp_path, "zz"))

    assert catalog.headings_for(language) == ENGLISH


def test_a_broken_catalog_falls_back_to_english(tmp_path: Path) -> None:
    root = _locale(tmp_path, "zz", 'msgid "Topics"\nmsgstr oops\n')

    assert GettextHeadingCatalog(root).headings_for("zz") == ENGLISH


def test_a_missing_locale_directory_falls_back_to_english(tmp_path: Path) -> None:
    assert GettextHeadingCatalog(tmp_path / "missing").headings_for("zz") == ENGLISH


def test_the_shipped_spanish_catalog_translates_every_heading() -> None:
    headings = GettextHeadingCatalog().headings_for("es")

    for field in Headings.model_fields:
        assert getattr(headings, field).strip(), field
        assert getattr(headings, field) != getattr(ENGLISH, field), field
    assert len(set(headings.sections)) == 4


def test_the_shipped_catalogs_cover_every_message_id() -> None:
    catalog = load_catalog("es")

    assert catalog is not None
    for field in Headings.model_fields:
        assert getattr(ENGLISH, field) in catalog


def test_the_shipped_catalog_is_found_through_package_resources() -> None:
    assert GettextHeadingCatalog().headings_for("es") != ENGLISH


def test_a_rejected_heading_value_falls_back_to_english(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Rejecting:
        def __init__(self, **values: str) -> None:  # noqa: ARG002
            Headings.model_validate({"topics": 1})

    monkeypatch.setattr(
        "meeting_scribe.summaries.infrastructure.gettext_headings.Headings", Rejecting
    )

    assert GettextHeadingCatalog(_locale(tmp_path, "zz")).headings_for("zz") == ENGLISH


def test_a_broken_catalog_logs_a_warning_with_the_language_and_path_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = _locale(tmp_path, "zz", 'msgid "Topics"\nmsgstr oops\n')

    GettextHeadingCatalog(root).headings_for("zz")

    assert "zz" in caplog.text
    assert "meeting_scribe.po" in caplog.text
    assert "oops" not in caplog.text
    assert "English" in caplog.text


def test_a_rejected_heading_value_logs_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class Rejecting:
        def __init__(self, **values: str) -> None:  # noqa: ARG002
            Headings.model_validate({"topics": 1})

    monkeypatch.setattr(
        "meeting_scribe.summaries.infrastructure.gettext_headings.Headings", Rejecting
    )

    GettextHeadingCatalog(_locale(tmp_path, "zz")).headings_for("zz")

    assert "zz" in caplog.text
    assert "English" in caplog.text


def test_a_missing_catalog_is_normal_and_logs_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    GettextHeadingCatalog(tmp_path / "missing").headings_for("zz")

    assert caplog.text == ""
