"""Audio facts shared by the domains that read the recording (no I/O)."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # numpy belongs to the ``ml`` extra; the domains never need it at runtime
    import numpy as np
    from numpy.typing import NDArray

SAMPLE_RATE = 16_000

type Samples = NDArray[np.float32]
"""A mono float32 waveform in [-1, 1] at ``SAMPLE_RATE``; only adapters touch the array."""
