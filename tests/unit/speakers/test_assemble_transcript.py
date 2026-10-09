from pathlib import Path
from typing import cast

import pytest
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.speakers.application.assemble_transcript import (
    AssembleTranscript,
    AssemblyRequest,
)
from meeting_scribe.speakers.domain.diarization import DiarizationResult
from meeting_scribe.speakers.domain.me_detection import DiarizedChannel
from meeting_scribe.speakers.domain.params import AssemblyParams
from meeting_scribe.speakers.domain.turns import TimedWord, Turn
from tests.unit.speakers.envelopes import SPEECH, envelope
from tests.unit.speakers.fakes import FakeTranscriptFiles, InMemoryTurnsCache

MEETING = Path("/meetings/m1")
SPEC = {
    "utterance_gap_seconds": 5.0,
    "me_dominance_db": 6.0,
    "me_noise_margin_db": 10.0,
    "me_min_turn_seconds": 0.5,
    "sample_max_clips": 3,
    "sample_target_seconds": 15.0,
    "sample_min_clip_seconds": 1.5,
    "sample_max_clip_seconds": 6.0,
}
# 90 s meeting: the user speaks 10-20 s (mic only), a remote voice 30-60 s (system channel only)
MIC = envelope(90, [(10, 20, SPEECH)])
SYSTEM = envelope(90, [(30, 60, SPEECH)])
REMOTE = Turn(start=30, end=60, speaker="SPEAKER_03")


def _word(start: float, text: str) -> TimedWord:
    return TimedWord(start=start, end=start + 0.5, text=text)


class Harness:
    def __init__(self) -> None:
        self.files = FakeTranscriptFiles()
        self.cache = InMemoryTurnsCache()
        self.cache.document = {"cache_version": 1, "key": {}, "data": {"diarization": []}}
        self.loads: list[DiarizedChannel] = []
        self.events: list[str] = []
        self.load_error: MeetingScribeError | None = None

    def extracting_samples(self) -> None:
        self.events.append("extracting_samples")
        self.files.calls.append("EVENT")

    def _load(self, channel: DiarizedChannel) -> Result[Samples, MeetingScribeError]:
        self.loads.append(channel)
        return Failure(self.load_error) if self.load_error else Success(cast("Samples", object()))

    def run(
        self,
        words: list[TimedWord],
        turns: tuple[Turn, ...] = (REMOTE,),
        channel: DiarizedChannel = "system",
        regular: tuple[Turn, ...] | None = None,
        mic: list[float] = MIC,
        system: list[float] = SYSTEM,
    ):
        request = AssemblyRequest.from_spec(
            SPEC,
            words,
            DiarizationResult(turns=regular if regular is not None else turns, exclusive=turns),
            channel,
            mic,
            system,
            self._load,
        ).unwrap()
        return AssembleTranscript(self.cache, self.files).run(MEETING, request, self)


def test_me_and_a_remote_speaker_are_assigned_numbered_and_listed() -> None:
    harness = Harness()
    words = [_word(12, "hola"), _word(12.6, "todos"), _word(35, "buenas"), _word(70, "adios")]

    outcome = harness.run(words).unwrap()

    assert harness.files.transcript == (
        "[00:00:12] **Me:** hola todos\n\n"
        "[00:00:35] **Speaker 1:** buenas\n\n"
        "[00:01:10] **Speaker 1:** adios\n"  # past the turn: the nearest one, a new utterance
    )
    assert [(s.id, s.label, s.is_me) for s in outcome.speakers][:2] == [
        ("ME", "Me", True),
        ("S1", "Speaker 1", False),
    ]
    me = outcome.speakers[0]
    assert me.utterance_count == 1 and me.talk_seconds == pytest.approx(1.1)
    assert outcome.artifacts == {"utterances": "utterances.json", "transcript": "transcript.md"}
    assert harness.files.utterances[0] == {"speaker": "ME", "start": 12, "text": "hola todos"}


def test_samples_follow_the_source_channel_of_each_speaker() -> None:
    harness = Harness()

    outcome = harness.run([_word(12, "hola"), _word(35, "buenas")]).unwrap()

    assert {s.id: s.sample for s in outcome.speakers} == {
        "ME": "samples/ME.wav",
        "S1": "samples/S1.wav",
    }
    assert sorted(harness.loads) == ["mic", "system"]  # one decode per channel, no more
    assert [(c.start, c.end) for c in harness.files.samples["S1"]] == [(30, 36)]  # first 6 s
    assert [(c.start, c.end) for c in harness.files.samples["ME"]] == [(10, 16)]


def test_a_remote_turn_overlapped_by_another_voice_gives_no_sample() -> None:
    harness = Harness()
    other = Turn(start=29, end=61, speaker="SPEAKER_04")
    regular = (REMOTE, other)

    outcome = harness.run([_word(35, "buenas")], turns=(REMOTE,), regular=regular).unwrap()

    assert outcome.speakers[0].sample == ""
    assert "S1" not in harness.files.samples


def test_me_with_loudspeaker_bleed_gets_no_sample_from_the_noisy_stretch() -> None:
    harness = Harness()
    system = envelope(90, [(10, 20, SPEECH / 4), (30, 60, SPEECH)])  # the system plays under Me

    outcome = harness.run([_word(12, "hola")], system=system).unwrap()

    assert all(s.sample == "" for s in outcome.speakers if s.id == "ME")


def test_in_person_meetings_diarize_the_mic_skip_me_and_sample_the_mic() -> None:
    harness = Harness()
    silent = envelope(90)
    turns = (Turn(start=10, end=20, speaker="SPEAKER_00"),)

    outcome = harness.run([_word(12, "hola")], turns=turns, channel="mic", system=silent).unwrap()

    assert [(s.id, s.is_me) for s in outcome.speakers] == [("S1", False)]
    assert harness.loads == ["mic"]
    assert harness.cache.document["data"]["me_turns"] == []  # type: ignore[index]


def test_no_words_is_a_valid_empty_transcript() -> None:
    harness = Harness()

    outcome = harness.run([]).unwrap()

    assert outcome.speakers == ()
    assert harness.files.transcript == "(no speech detected)\n"
    assert harness.loads == []  # nobody to sample, so no audio is decoded


def test_no_turns_at_all_numbers_everything_as_speaker_1() -> None:
    harness = Harness()

    outcome = harness.run(
        [_word(5, "hola")], turns=(), mic=envelope(90), system=envelope(90)
    ).unwrap()

    assert [(s.id, s.sample) for s in outcome.speakers] == [("S1", "")]


def test_the_stage_event_comes_after_assignment_and_before_any_file() -> None:
    harness = Harness()

    harness.run([_word(35, "buenas")]).unwrap()

    assert harness.events == ["extracting_samples"]
    assert harness.files.calls[0] == "EVENT"
    assert harness.files.calls[1:] == ["reset", "sample", "utterances", "transcript"]


def test_the_me_turns_and_bleed_are_added_to_turns_json() -> None:
    harness = Harness()

    harness.run([_word(12, "hola")]).unwrap()

    data = harness.cache.document["data"]
    assert data["diarization"] == []  # type: ignore[index]  the model output is kept
    assert data["bleed"] < 0.01  # type: ignore[index]  no sustained system audio under Me
    first = data["me_turns"][0]  # type: ignore[index]
    assert (first["start"], first["speaker"]) == (10.0, "ME")


def test_a_missing_cache_document_is_not_an_error() -> None:
    harness = Harness()
    harness.cache.document = {}

    assert isinstance(harness.run([_word(12, "hola")]), Success)


@pytest.mark.parametrize("step", ["reset", "sample", "utterances", "transcript"])
def test_a_file_failure_fails_the_stage_with_its_error(step: str) -> None:
    harness = Harness()
    harness.files.fail_on = step

    result = harness.run([_word(12, "hola"), _word(35, "buenas")])

    assert isinstance(result, Failure)
    assert result.failure().error_code == "DIARIZATION_FAILED"


def test_an_audio_failure_while_cutting_samples_fails_the_stage() -> None:
    harness = Harness()
    harness.load_error = MeetingScribeError("cannot read")

    assert isinstance(harness.run([_word(35, "buenas")]), Failure)


def test_an_invalid_spec_is_an_invalid_value_error() -> None:
    result = AssemblyRequest.from_spec(
        {"sample_max_clips": 0},
        [],
        DiarizationResult(turns=(), exclusive=()),
        "system",
        [],
        [],
        lambda c: Success(cast("Samples", c)),
    )

    assert result.failure().error_code == "INVALID_VALUE"


def test_assembly_params_build_the_domain_params() -> None:
    params = AssemblyParams.model_validate(SPEC)

    assert params.me_params().dominance_db == 6.0
    assert params.sample_params().target_seconds == 15.0
