"""Audio facts shared by the domains that read the recording (no I/O)."""

from typing import Any

SAMPLE_RATE = 16_000

type Samples = Any
"""A mono float32 waveform in [-1, 1] at ``SAMPLE_RATE`` (a numpy array in practice).

Kept opaque here so that no domain needs numpy to be imported; only the adapters that decode or
feed the models touch the array.
"""
