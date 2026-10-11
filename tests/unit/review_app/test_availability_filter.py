"""Only a change of the service's availability is announced; the first observation is a baseline."""

from meeting_scribe_review.core.availability import AvailabilityFilter


def test_the_first_observation_is_a_baseline_not_a_change() -> None:
    assert AvailabilityFilter().changed(available=False) is None
    assert AvailabilityFilter().changed(available=True) is None


def test_a_disappearance_after_an_appearance_is_announced() -> None:
    filtered = AvailabilityFilter()
    filtered.changed(available=True)
    assert filtered.changed(available=False) is False


def test_an_appearance_after_an_absence_is_announced() -> None:
    filtered = AvailabilityFilter()
    filtered.changed(available=False)
    assert filtered.changed(available=True) is True


def test_a_repeated_value_is_not_a_change() -> None:
    filtered = AvailabilityFilter()
    assert [filtered.changed(available=v) for v in (True, True, False, False, True)] == [
        None,
        None,
        False,
        None,
        True,
    ]
