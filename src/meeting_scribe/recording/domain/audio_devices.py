"""Audio sources used by a capture (ADR-002: microphone and system monitor)."""

from pydantic import BaseModel, ConfigDict, Field


class AudioDevices(BaseModel):
    """PipeWire/PulseAudio source names. ``monitor_source`` is ``<sink>.monitor``."""

    model_config = ConfigDict(frozen=True)

    monitor_source: str = Field(min_length=1)
    mic_source: str = Field(min_length=1)
