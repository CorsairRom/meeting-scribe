"""Synthetic frame-energy envelopes (mean square per 100 ms frame); no audio at all."""

from collections.abc import Sequence

FRAME = 0.1
FLOOR = 1e-6  # about -60 dBFS: the noise floor of a quiet room or an idle channel
SPEECH = 1e-2  # about -20 dBFS


def envelope(
    seconds: float, bursts: Sequence[tuple[float, float, float]] = (), floor: float = FLOOR
) -> list[float]:
    """A noise floor with ``(start, end, energy)`` bursts added on top (energies add)."""
    frames = [floor] * round(seconds / FRAME)
    for start, end, energy in bursts:
        for index in range(round(start / FRAME), round(end / FRAME)):
            frames[index] += energy
    return frames


def attenuated(db: float) -> float:
    """The energy factor of ``db`` decibels of loss."""
    return 10 ** (-db / 10)
