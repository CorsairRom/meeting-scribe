"""Rewrites ``transcript.md`` from the stored utterances and the reviewed speakers (spec 2)."""

from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.speakers.domain.ports import UtterancesStore
from meeting_scribe.speakers.domain.review import ReviewState, effective_utterances
from meeting_scribe.speakers.domain.transcript import render_transcript
from meeting_scribe.speakers.domain.turns import SpeakerLabel, Utterance


class RenderTranscript:
    def __init__(self, store: UtterancesStore) -> None:
        self._store = store

    def run(self, meeting_dir: Path, state: ReviewState) -> Result[str, MeetingScribeError]:
        """Render with names and merges applied and replace ``transcript.md``; the file name."""
        shown = self.effective_utterances(meeting_dir, state)
        if isinstance(shown, Failure):
            return shown
        labels = {
            speaker.id: SpeakerLabel(id=speaker.id, label=speaker.label, name=speaker.name)
            for speaker in state.speakers
        }
        return self._store.write_transcript(meeting_dir, render_transcript(shown.unwrap(), labels))

    def effective_utterances(
        self, meeting_dir: Path, state: ReviewState
    ) -> Result[tuple[Utterance, ...], MeetingScribeError]:
        """The stored utterances with merges applied, as served to clients (never reads the md)."""
        loaded = self._store.load(meeting_dir)
        if isinstance(loaded, Failure):
            return loaded
        return Success(effective_utterances(loaded.unwrap(), state.merged_into))
