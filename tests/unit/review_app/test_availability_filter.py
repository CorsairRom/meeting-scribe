"""A name that was never seen is reported by the failed call, not as a disappearance."""

from meeting_scribe_review.core.availability import AvailabilityFilter


def test_the_first_absence_is_not_reported() -> None:
    assert AvailabilityFilter().changed(available=False) is None


def test_an_appearance_is_always_reported() -> None:
    assert AvailabilityFilter().changed(available=True) is True


def test_a_disappearance_after_an_appearance_is_reported() -> None:
    filtered = AvailabilityFilter()
    filtered.changed(available=True)
    assert filtered.changed(available=False) is False


def test_repeated_values_are_reported_each_time() -> None:
    filtered = AvailabilityFilter()
    assert [filtered.changed(available=v) for v in (True, True, False, False)] == [
        True,
        True,
        False,
        False,
    ]
