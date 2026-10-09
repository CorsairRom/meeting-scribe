"""Voice sample selection: which stretches of a speaker's audio to cut (spec 5).

Pure functions over turns; the worker cuts, joins and normalizes the audio (F3.3b).
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from meeting_scribe.speakers.domain.energy import active_threshold, frame_range, frames_until
from meeting_scribe.speakers.domain.params import SampleParams
from meeting_scribe.speakers.domain.turns import Turn

_EPSILON = 1e-9


class Clip(BaseModel):
    """A stretch of one channel to put in a sample."""

    model_config = ConfigDict(frozen=True)

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def clean_turns_of(speaker: str, turns: Sequence[Turn]) -> tuple[Turn, ...]:
    """The speaker's turns (regular, overlapping diarization) that no one else talks in."""
    others = [turn for turn in turns if turn.speaker != speaker]
    return tuple(
        turn
        for turn in turns
        if turn.speaker == speaker and not any(turn.overlap(other) > 0 for other in others)
    )


def me_turns_with_quiet_system(
    me_turns: Sequence[Turn], system: Sequence[float], margin_db: float
) -> tuple[Turn, ...]:
    """ "Me" turns during which no frame of the system channel is clearly active (no bleed).

    A frame the system envelope does not have is not evidence of quiet: a turn reaching past a
    shorter (or empty) envelope is left out.
    """
    threshold = active_threshold(system, margin_db)
    return tuple(
        turn
        for turn in me_turns
        if frames_until(turn.end) <= len(system)
        and all(system[i] < threshold for i in frame_range(turn.start, turn.end, len(system)))
    )


def select_sample_clips(clean_turns: Sequence[Turn], params: SampleParams) -> tuple[Clip, ...]:
    """Longest eligible turns first (earliest on a tie), each trimmed to its first
    ``max_clip_seconds``, at most ``max_clips`` of them and ``target_seconds`` in total.

    The last clip is shortened to fit the target; when what is left of the target is shorter than
    ``min_clip_seconds`` the selection ends instead of adding a useless sliver.
    """
    eligible = [t for t in clean_turns if t.duration >= params.min_clip_seconds - _EPSILON]
    clips: list[Clip] = []
    total = 0.0
    for turn in sorted(eligible, key=lambda t: (-t.duration, t.start)):
        if len(clips) >= params.max_clips:
            break
        length = min(turn.duration, params.max_clip_seconds, params.target_seconds - total)
        if length < params.min_clip_seconds - _EPSILON:
            break
        clips.append(Clip(start=turn.start, end=turn.start + length))
        total += length
    return tuple(clips)
