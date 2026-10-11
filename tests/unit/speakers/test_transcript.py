from meeting_scribe.speakers.domain.assignment import FALLBACK_SPEAKER
from meeting_scribe.speakers.domain.transcript import (
    NO_SPEECH_TEXT,
    format_timestamp,
    number_speakers,
    render_transcript,
)
from meeting_scribe.speakers.domain.turns import ME, UNKNOWN, SpeakerLabel, Utterance


def _u(speaker: str, start: float, text: str = "hola") -> Utterance:
    return Utterance(speaker=speaker, start=start, end=start + 1.0, text=text)


def test_renumber_by_first_appearance_and_render() -> None:
    utterances = (
        _u("SPEAKER_03", 5.0, "buenas"),
        _u(ME, 8.0, "yo"),
        _u("SPEAKER_00", 12.0, "adiós"),
        _u("SPEAKER_03", 3725.0, "otra vez"),
    )

    labels = number_speakers(utterances)
    transcript = render_transcript(utterances, labels)

    assert labels["SPEAKER_03"].id == "S1"
    assert labels["SPEAKER_00"].label == "Speaker 2"
    assert transcript == (
        "[00:00:05] **Speaker 1:** buenas\n\n"
        "[00:00:08] **Me:** yo\n\n"
        "[00:00:12] **Speaker 2:** adiós\n\n"
        "[01:02:05] **Speaker 1:** otra vez\n"
    )


def test_numbers_follow_the_first_utterance_not_the_first_turn() -> None:
    # SPEAKER_00 may own an early turn but no word: only speakers with utterances are numbered.
    labels = number_speakers((_u("SPEAKER_07", 10.0),))

    assert list(labels) == ["SPEAKER_07"]
    assert labels["SPEAKER_07"].id == "S1"


def test_me_and_unknown_are_labelled_but_never_numbered() -> None:
    labels = number_speakers((_u(UNKNOWN, 0.0), _u(ME, 1.0), _u("SPEAKER_01", 2.0)))

    assert (labels[ME].id, labels[ME].label) == ("ME", "Me")
    assert (labels[UNKNOWN].id, labels[UNKNOWN].label) == ("UNKNOWN", "Unknown")
    assert labels["SPEAKER_01"].label == "Speaker 1"


def test_the_fallback_speaker_is_speaker_1() -> None:
    labels = number_speakers((_u(FALLBACK_SPEAKER, 0.0),))

    assert labels[FALLBACK_SPEAKER].label == "Speaker 1"


def test_a_name_set_by_the_user_replaces_the_label_in_the_transcript() -> None:
    utterances = (_u("SPEAKER_00", 0.0, "hola"),)
    labels = number_speakers(utterances)
    labels["SPEAKER_00"] = labels["SPEAKER_00"].with_name("Ana")

    assert render_transcript(utterances, labels) == "[00:00:00] **Ana:** hola\n"
    assert labels["SPEAKER_00"].id == "S1"  # the id never changes with the name


def test_a_blank_name_keeps_the_label() -> None:
    label = number_speakers((_u("SPEAKER_00", 0.0),))["SPEAKER_00"].with_name("  ")

    assert label.display == "Speaker 1"


def test_timestamps_truncate_to_whole_seconds_and_hours_never_shrink() -> None:
    assert format_timestamp(59.99) == "00:00:59"
    assert format_timestamp(3661.5) == "01:01:01"
    assert format_timestamp(100 * 3600) == "100:00:00"
    assert format_timestamp(-1.0) == "00:00:00"


def test_no_utterances_render_the_no_speech_marker() -> None:
    assert render_transcript((), {}) == f"{NO_SPEECH_TEXT}\n"
    assert NO_SPEECH_TEXT == "(no speech detected)"


def test_markdown_in_a_name_is_escaped_in_the_rendered_transcript() -> None:
    labels = {"S1": SpeakerLabel(id="S1", label="Speaker 1", name="*A_l*[x](y) `z` <b> a|b\\")}

    rendered = render_transcript((_u("S1", 0.0, "plain *text*"),), labels)

    assert rendered == (
        "[00:00:00] **\\*A\\_l\\*\\[x\\](y) \\`z\\` \\<b\\> a\\|b\\\\:** plain *text*\n"
    )


def test_plain_names_and_labels_render_unchanged() -> None:
    labels = {"S1": SpeakerLabel(id="S1", label="Speaker 1", name="Zoë O'Brien-Smith, Dr.")}

    assert "**Zoë O'Brien-Smith, Dr.:**" in render_transcript((_u("S1", 0.0, "x"),), labels)
