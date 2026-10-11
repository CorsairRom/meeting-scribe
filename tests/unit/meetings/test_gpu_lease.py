from meeting_scribe.meetings.application.gpu_lease import InMemoryGpuLease


def test_a_free_lease_is_granted_and_then_refused_to_everyone_else() -> None:
    lease = InMemoryGpuLease()

    assert lease.available("summary")
    assert lease.acquire("summary")
    assert not lease.available("processing")
    assert not lease.acquire("processing")
    assert not lease.acquire("summary")  # not re-entrant: one local job at a time


def test_a_release_frees_it_and_a_release_by_a_non_holder_does_nothing() -> None:
    lease = InMemoryGpuLease()
    lease.acquire("processing")

    lease.release("summary")
    assert not lease.available("summary")

    lease.release("processing")
    assert lease.acquire("summary")


def test_a_recording_is_always_granted_and_revokes_every_other_holder() -> None:
    lease = InMemoryGpuLease()
    revoked: list[str] = []
    lease.acquire("summary", on_revoke=lambda: revoked.append("summary"))

    assert lease.acquire("recording")

    assert revoked == ["summary"]
    assert not lease.available("summary")
    assert not lease.acquire("processing")


def test_a_recording_does_not_revoke_itself_and_is_idempotent() -> None:
    lease = InMemoryGpuLease()
    revoked: list[str] = []
    lease.acquire("recording", on_revoke=lambda: revoked.append("recording"))

    assert lease.acquire("recording")

    assert revoked == []


def test_the_revoked_holder_still_releases_normally_and_the_recording_keeps_it() -> None:
    lease = InMemoryGpuLease()
    lease.acquire("summary", on_revoke=lambda: None)
    lease.acquire("recording")

    lease.release("summary")
    assert not lease.available("processing")  # the recording still holds it

    lease.release("recording")
    assert lease.available("processing")


def test_a_failing_revoke_callback_does_not_break_the_recording() -> None:
    lease = InMemoryGpuLease()

    def broken() -> None:
        raise RuntimeError("boom")

    lease.acquire("summary", on_revoke=broken)

    assert lease.acquire("recording")
