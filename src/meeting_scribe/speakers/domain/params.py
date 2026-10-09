"""Thresholds the domain functions take as arguments; the settings class builds them."""

from pydantic import BaseModel, ConfigDict, Field


class MeParams(BaseModel):
    """Spec 2.4: how strongly the mic must stand out to be the user's voice."""

    model_config = ConfigDict(frozen=True)

    dominance_db: float = Field(ge=0)
    """Residual mic energy over predicted bleed plus noise needed for "Me"."""
    noise_margin_db: float = Field(ge=0)
    """Residual mic energy over the mic noise floor needed to count as speech."""
    min_turn_seconds: float = Field(gt=0)


class SampleParams(BaseModel):
    """Spec 5: how a voice sample is cut from a speaker's clean turns."""

    model_config = ConfigDict(frozen=True)

    max_clips: int = Field(ge=1)
    target_seconds: float = Field(gt=0)
    min_clip_seconds: float = Field(gt=0)
    max_clip_seconds: float = Field(gt=0)
