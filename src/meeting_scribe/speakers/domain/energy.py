"""Frame-energy helpers shared by "Me" detection and sample selection (spec 2.4).

Energies are mean squares per 100 ms frame of a signal in [-1, 1], precomputed by the worker (the
only place that holds audio arrays), so everything here is plain Python over ``Sequence[float]``.
"""

import math
from collections.abc import Sequence

FRAME_SECONDS = 0.1
ENERGY_FLOOR = 1e-12
"""About -120 dBFS: stands in for a noise floor of exactly zero (digital silence)."""


def db_to_ratio(db: float) -> float:
    return 10 ** (db / 10)


def percentile(values: Sequence[float], fraction: float) -> float:
    """Linear-interpolated percentile (``fraction`` in 0..1), like numpy's default."""
    if not values:
        return 0.0
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def noise_floor(energies: Sequence[float]) -> float:
    """10th percentile of the envelope, never below ``ENERGY_FLOOR``."""
    return max(percentile(energies, 0.10), ENERGY_FLOOR)


def active_threshold(energies: Sequence[float], margin_db: float) -> float:
    """Energy from which a frame of this channel is clearly active: ``margin_db`` over its floor.

    ``margin_db`` is ``SPEAKERS_ME_NOISE_MARGIN_DB`` everywhere (one source for "active over
    noise": microphone speech, system bleed, the in-person decision and quiet-system samples).
    """
    return noise_floor(energies) * db_to_ratio(margin_db)


def frames_until(end: float) -> int:
    """How many frames from 0 a span ending at ``end`` seconds touches (not clipped)."""
    return math.ceil(end / FRAME_SECONDS - 1e-9)


def frame_range(start: float, end: float, frames: int) -> range:
    """Indices of the frames a ``[start, end)`` span touches, clipped to ``frames``."""
    first = max(int(start / FRAME_SECONDS + 1e-9), 0)
    last = min(frames_until(end), frames)
    return range(first, max(last, first))
