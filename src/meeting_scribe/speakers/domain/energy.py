"""Frame-energy helpers shared by "Me" detection and sample selection (spec 2.4).

Energies are mean squares per 100 ms frame of a signal in [-1, 1], precomputed by the worker (the
only place that holds audio arrays), so everything here is plain Python over ``Sequence[float]``.
"""

import math
from collections.abc import Sequence

FRAME_SECONDS = 0.1
ENERGY_FLOOR = 1e-12
"""About -120 dBFS: stands in for a noise floor of exactly zero (digital silence)."""
ACTIVE_OVER_NOISE_DB = 10.0
"""A frame is clearly active when it is this far over its channel's noise floor."""


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


def active_threshold(energies: Sequence[float]) -> float:
    """Energy from which a frame of this channel is clearly active."""
    return noise_floor(energies) * db_to_ratio(ACTIVE_OVER_NOISE_DB)


def frame_range(start: float, end: float, frames: int) -> range:
    """Indices of the frames a ``[start, end)`` span touches, clipped to ``frames``."""
    first = max(int(start / FRAME_SECONDS + 1e-9), 0)
    last = min(math.ceil(end / FRAME_SECONDS - 1e-9), frames)
    return range(first, max(last, first))
