"""Speakers settings (``SPEAKERS_*``), composed into the root ``Settings`` (spec 7)."""

from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict

from meeting_scribe.base.infrastructure.settings import MeetingScribeSettings
from meeting_scribe.speakers.domain.params import MeParams, SampleParams


class SpeakersSettings(MeetingScribeSettings):
    model_config = SettingsConfigDict(env_prefix="SPEAKERS_")

    pipeline: str = Field(default="pyannote/speaker-diarization-community-1", min_length=1)
    num_speakers: int | None = Field(default=None, ge=1)
    min_speakers: int | None = Field(default=None, ge=1)
    max_speakers: int | None = Field(default=None, ge=1)
    utterance_gap_seconds: float = Field(default=5.0, ge=0)
    me_dominance_db: float = Field(default=6.0, ge=0)
    me_noise_margin_db: float = Field(default=10.0, ge=0)
    me_min_turn_seconds: float = Field(default=0.5, gt=0)
    sample_max_clips: int = Field(default=3, ge=1)
    sample_target_seconds: float = Field(default=15.0, gt=0)
    sample_min_clip_seconds: float = Field(default=1.5, gt=0)
    sample_max_clip_seconds: float = Field(default=6.0, gt=0)

    @model_validator(mode="after")
    def _ranges_are_ordered(self) -> Self:
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise ValueError("SPEAKERS_MIN_SPEAKERS must not exceed SPEAKERS_MAX_SPEAKERS")
        if self.sample_min_clip_seconds > self.sample_max_clip_seconds:
            raise ValueError(
                "SPEAKERS_SAMPLE_MIN_CLIP_SECONDS must not exceed SPEAKERS_SAMPLE_MAX_CLIP_SECONDS"
            )
        if self.sample_target_seconds < self.sample_min_clip_seconds:
            raise ValueError(
                "SPEAKERS_SAMPLE_TARGET_SECONDS must be at least SPEAKERS_SAMPLE_MIN_CLIP_SECONDS"
            )
        self._num_speakers_is_within_bounds()
        return self

    def _num_speakers_is_within_bounds(self) -> None:
        count = self.num_speakers
        if count is None:
            return
        if self.min_speakers is not None and count < self.min_speakers:
            raise ValueError("SPEAKERS_NUM_SPEAKERS must not be below SPEAKERS_MIN_SPEAKERS")
        if self.max_speakers is not None and count > self.max_speakers:
            raise ValueError("SPEAKERS_NUM_SPEAKERS must not exceed SPEAKERS_MAX_SPEAKERS")

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
