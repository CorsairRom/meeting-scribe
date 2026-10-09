"""Thresholds the domain functions take as arguments; the settings class builds them."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    @model_validator(mode="after")
    def _clips_fit_the_target(self) -> Self:
        """Contradictory bounds would select nothing, silently."""
        if self.min_clip_seconds > self.max_clip_seconds:
            raise ValueError("min_clip_seconds must not exceed max_clip_seconds")
        if self.target_seconds < self.min_clip_seconds:
            raise ValueError("target_seconds must be at least min_clip_seconds")
        return self


class DiarizationParams(BaseModel):
    """What selects and steers the pyannote pipeline (part of the ``turns.json`` cache key)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    pipeline: str = Field(min_length=1)
    num_speakers: int | None = Field(default=None, ge=1)
    min_speakers: int | None = Field(default=None, ge=1)
    max_speakers: int | None = Field(default=None, ge=1)
    me_noise_margin_db: float = Field(ge=0)
    """Not a pipeline knob: the margin that decides the in-person (mic) channel choice."""


class AssemblyParams(BaseModel):
    """Everything the post-diarization step needs; none of it is part of a cache key (spec 4)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    utterance_gap_seconds: float = Field(ge=0)
    me_dominance_db: float = Field(ge=0)
    me_noise_margin_db: float = Field(ge=0)
    me_min_turn_seconds: float = Field(gt=0)
    sample_max_clips: int = Field(ge=1)
    sample_target_seconds: float = Field(gt=0)
    sample_min_clip_seconds: float = Field(gt=0)
    sample_max_clip_seconds: float = Field(gt=0)

    def me_params(self) -> MeParams:
        return MeParams(
            dominance_db=self.me_dominance_db,
            noise_margin_db=self.me_noise_margin_db,
            min_turn_seconds=self.me_min_turn_seconds,
        )

    def sample_params(self) -> SampleParams:
        return SampleParams(
            max_clips=self.sample_max_clips,
            target_seconds=self.sample_target_seconds,
            min_clip_seconds=self.sample_min_clip_seconds,
            max_clip_seconds=self.sample_max_clip_seconds,
        )
