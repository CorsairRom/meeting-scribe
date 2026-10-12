from meeting_scribe.summaries.domain.fingerprint import input_fingerprint
from tests.unit.summaries.factories import line


def test_fingerprint_is_a_stable_sha256() -> None:
    lines = (line(), line("Person B", 5.0, "other."))

    assert input_fingerprint(lines) == input_fingerprint(tuple(lines))
    assert input_fingerprint(lines).startswith("sha256:")
    assert len(input_fingerprint(lines)) == len("sha256:") + 64


def test_name_time_and_text_each_change_the_fingerprint() -> None:
    base = input_fingerprint((line(),))

    assert input_fingerprint((line(speaker="Person C"),)) != base
    assert input_fingerprint((line(start=1.0),)) != base
    assert input_fingerprint((line(text="changed."),)) != base


def test_field_boundaries_are_unambiguous() -> None:
    first = input_fingerprint((line(speaker="ab", text="c"),))
    second = input_fingerprint((line(speaker="a", text="bc"),))

    assert first != second
