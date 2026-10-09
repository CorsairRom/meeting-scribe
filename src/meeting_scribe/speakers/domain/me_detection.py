"""The user's voice by per-frame energy dominance, and the in-person fallback (spec 2.4, OQ-4).

ADR-002 keeps the microphone (channel 0) and the system audio (channel 1) apart. A frame is "Me"
when the microphone carries energy the system audio cannot explain: bleed from loudspeakers is
estimated and subtracted, and what is left must dominate. Pure Python over precomputed frame
energies; the worker computes them from the audio (F3.3b).
"""

import statistics
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from meeting_scribe.speakers.domain.energy import (
    FRAME_SECONDS,
    active_threshold,
    db_to_ratio,
    noise_floor,
)
from meeting_scribe.speakers.domain.params import MeParams
from meeting_scribe.speakers.domain.turns import ME, UNKNOWN, Turn

BLEED_MIN_ACTIVE_SECONDS = 30.0
"""Fewer seconds of clearly active system audio and the bleed is not estimated (k = 0)."""
MERGE_GAP_SECONDS = 0.3
"""Two runs of "Me" (or "Unknown") frames closer than this are one turn: a breath or a short
pause is not a change of speaker. Fixed on purpose (spec 2.4 step 5); not a setting."""
IN_PERSON_SYSTEM_SPEECH_FRACTION = 0.01
"""OQ-4: system audio with speech in less than this share of the meeting means an in-person one."""

type DiarizedChannel = Literal["system", "mic"]


class MeDetection(BaseModel):
    model_config = ConfigDict(frozen=True)

    me_turns: tuple[Turn, ...]
    unknown_turns: tuple[Turn, ...]
    bleed: float
    """``k``: the share of the system energy that reaches the microphone (0 with a headset)."""


def estimate_bleed(mic: Sequence[float], system: Sequence[float], margin_db: float) -> float:
    """Median ``E_mic / E_sys`` over clearly active system frames, clamped to [0, 1].

    The median is robust because the user is silent during most remote speech.
    """
    threshold = active_threshold(system, margin_db)
    ratios = [m / s for m, s in zip(mic, system, strict=False) if s >= threshold]
    if len(ratios) * FRAME_SECONDS < BLEED_MIN_ACTIVE_SECONDS:
        return 0.0
    return min(max(statistics.median(ratios), 0.0), 1.0)


def detect_me(
    mic: Sequence[float],
    system: Sequence[float],
    system_turns: Sequence[Turn],
    params: MeParams,
) -> MeDetection:
    """ "Me" turns and "Unknown" turns; channels of different length use the shorter one."""
    frames = min(len(mic), len(system))
    mic, system = mic[:frames], system[:frames]
    if not frames:
        return MeDetection(me_turns=(), unknown_turns=(), bleed=0.0)
    bleed = estimate_bleed(mic, system, params.noise_margin_db)
    floor = noise_floor(mic)
    speech_level = floor * db_to_ratio(params.noise_margin_db)
    dominance = db_to_ratio(params.dominance_db)
    speech: list[bool] = []
    me: list[bool] = []
    for m, s in zip(mic, system, strict=True):
        residual = max(m - bleed * s, 0.0)
        is_speech = residual >= speech_level
        speech.append(is_speech)
        me.append(is_speech and residual >= dominance * (bleed * s + floor))
    min_frames = round(params.min_turn_seconds / FRAME_SECONDS)
    me_turns = _turns(me, min_frames, ME)
    ambiguous = [is_speech and not is_me for is_speech, is_me in zip(speech, me, strict=True)]
    unknown_turns = [
        turn
        for turn in _turns(ambiguous, min_frames, UNKNOWN)
        if not any(turn.overlap(other) > 0 for other in system_turns)
    ]
    return MeDetection(me_turns=me_turns, unknown_turns=tuple(unknown_turns), bleed=bleed)


def merge_turns(
    system: Sequence[Turn], me: Sequence[Turn], unknown: Sequence[Turn] = ()
) -> tuple[Turn, ...]:
    """One speaker per instant: "Me" wins its overlaps and the other turns are cut around it."""
    covered = sorted((turn.start, turn.end) for turn in me)
    pieces = [piece for turn in (*system, *unknown) for piece in _cut(turn, covered)]
    return tuple(sorted((*me, *pieces), key=lambda turn: (turn.start, turn.end)))


def choose_diarization_channel(system: Sequence[float], margin_db: float) -> DiarizedChannel:
    """OQ-4: with speech in under 1 % of the system frames everyone is on the mic (in person)."""
    if not system:
        return "system"
    threshold = active_threshold(system, margin_db)
    active = sum(1 for energy in system if energy >= threshold)
    return "mic" if active / len(system) < IN_PERSON_SYSTEM_SPEECH_FRACTION else "system"


def _turns(flags: Sequence[bool], min_frames: int, speaker: str) -> tuple[Turn, ...]:
    """Runs of true frames, merged across gaps up to ``MERGE_GAP_SECONDS``, short ones dropped."""
    max_gap = round(MERGE_GAP_SECONDS / FRAME_SECONDS)
    runs: list[list[int]] = []
    for index, flag in enumerate(flags):
        if not flag:
            continue
        if runs and index - runs[-1][1] <= max_gap:
            runs[-1][1] = index + 1
        else:
            runs.append([index, index + 1])
    return tuple(
        Turn(start=first * FRAME_SECONDS, end=last * FRAME_SECONDS, speaker=speaker)
        for first, last in runs
        if last - first >= min_frames
    )


def _cut(turn: Turn, covered: Sequence[tuple[float, float]]) -> list[Turn]:
    start, pieces = turn.start, []
    for cover_start, cover_end in covered:
        if cover_end <= start or cover_start >= turn.end:
            continue
        if cover_start > start:
            pieces.append(turn.model_copy(update={"start": start, "end": cover_start}))
        start = max(start, cover_end)
    if start < turn.end:
        pieces.append(turn.model_copy(update={"start": start}))
    return pieces
