import pytest
from returns.result import Failure

from meeting_scribe.meetings.domain.errors import (
    MeetingAudioMissingError,
    MeetingAudioUnsupportedError,
)
from meeting_scribe.meetings.domain.timeline import ChunkHeader, audio_fingerprint, build_timeline


def _header(name: str, frames: int, **overrides: int) -> ChunkHeader:
    fields = {"channels": 2, "sample_rate": 16000, "sample_width_bytes": 2}
    fields.update(overrides)
    return ChunkHeader(name=name, frames=frames, size_bytes=44 + frames * 4, **fields)


HEADERS = [
    _header("chunk_00000.wav", 480_640),
    _header("chunk_00001.wav", 480_320),
    _header("chunk_00002.wav", 12_000),
]


def test_offsets_use_frame_counts_not_indices() -> None:
    timeline = build_timeline(HEADERS).unwrap()

    assert [c.offset_seconds for c in timeline.chunks] == pytest.approx([0.0, 30.04, 60.06])
    assert timeline.duration_seconds == pytest.approx(60.81)
    assert timeline.total_frames == 972_960


def test_chunks_are_ordered_by_name_not_by_input_order() -> None:
    timeline = build_timeline(list(reversed(HEADERS))).unwrap()

    assert [c.name for c in timeline.chunks] == [h.name for h in HEADERS]


def test_zero_frame_chunks_are_ignored() -> None:
    timeline = build_timeline([*HEADERS, _header("chunk_00003.wav", 0)]).unwrap()

    assert len(timeline.chunks) == 3


@pytest.mark.parametrize("headers", [[], [_header("chunk_00000.wav", 0)]])
def test_no_audio_frames_is_audio_missing(headers: list[ChunkHeader]) -> None:
    result = build_timeline(headers)

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), MeetingAudioMissingError)


@pytest.mark.parametrize(
    "overrides",
    [{"channels": 1}, {"sample_rate": 48000}, {"sample_width_bytes": 3}],
)
def test_unsupported_format_is_audio_unsupported(overrides: dict[str, int]) -> None:
    result = build_timeline([HEADERS[0], _header("chunk_00001.wav", 100, **overrides)])

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), MeetingAudioUnsupportedError)
    assert "chunk_00001.wav" in str(result.failure())


def test_fingerprint_lists_name_frames_and_size_per_chunk() -> None:
    assert audio_fingerprint(build_timeline(HEADERS).unwrap()) == (
        ("chunk_00000.wav", 480_640, 44 + 480_640 * 4),
        ("chunk_00001.wav", 480_320, 44 + 480_320 * 4),
        ("chunk_00002.wav", 12_000, 44 + 12_000 * 4),
    )


def test_fingerprint_changes_when_a_frame_count_or_the_chunk_list_changes() -> None:
    base = audio_fingerprint(build_timeline(HEADERS).unwrap())
    shorter = [HEADERS[0], HEADERS[1], _header("chunk_00002.wav", 11_999)]

    assert audio_fingerprint(build_timeline(shorter).unwrap()) != base
    assert audio_fingerprint(build_timeline(HEADERS[:2]).unwrap()) != base
