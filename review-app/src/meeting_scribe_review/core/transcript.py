"""Transcript rows as the view shows them: timestamp, speaker display name and text."""

from collections.abc import Sequence
from dataclasses import dataclass

from meeting_scribe_review.core.formatting import format_timestamp
from meeting_scribe_review.core.models import Speaker, Utterance


@dataclass(frozen=True, slots=True)
class TranscriptLine:
    speaker_id: str
    speaker_name: str
    timestamp: str
    text: str


def build_transcript(
    speakers: Sequence[Speaker], utterances: Sequence[Utterance]
) -> list[TranscriptLine]:
    """Join the utterances (effective ids after merges) with the speaker rows by id."""
    names = {speaker.id: speaker.display for speaker in speakers}
    return [
        TranscriptLine(
            speaker_id=utterance.speaker_id,
            speaker_name=names.get(utterance.speaker_id, utterance.speaker_id),
            timestamp=format_timestamp(utterance.start_seconds),
            text=utterance.text,
        )
        for utterance in utterances
    ]
