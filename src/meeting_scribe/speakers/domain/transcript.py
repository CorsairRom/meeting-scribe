"""Speaker numbering and the transcript markdown (spec 3)."""

from collections.abc import Mapping, Sequence

from meeting_scribe.speakers.domain.turns import SpeakerLabel, Utterance, unnumbered_label

NO_SPEECH_TEXT = "(no speech detected)"


def format_timestamp(seconds: float) -> str:
    """``HH:MM:SS``, truncated to whole seconds; hours never shrink below two digits."""
    total = max(int(seconds), 0)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def number_speakers(utterances: Sequence[Utterance]) -> dict[str, SpeakerLabel]:
    """Label every speaker that owns an utterance; diarization speakers become ``S1..SN`` in order
    of their first utterance (so there are no gaps), ``ME`` and ``UNKNOWN`` are never numbered."""
    labels: dict[str, SpeakerLabel] = {}
    numbered = 0
    for utterance in utterances:
        speaker = utterance.speaker
        if speaker in labels:
            continue
        fixed = unnumbered_label(speaker)
        if fixed is None:
            numbered += 1
            fixed = SpeakerLabel(id=f"S{numbered}", label=f"Speaker {numbered}")
        labels[speaker] = fixed
    return labels


def render_transcript(utterances: Sequence[Utterance], labels: Mapping[str, SpeakerLabel]) -> str:
    """One paragraph per utterance: ``[HH:MM:SS] **<name or label>:** <text>``."""
    if not utterances:
        return f"{NO_SPEECH_TEXT}\n"
    paragraphs = [
        f"[{format_timestamp(u.start)}] **{_display(u.speaker, labels)}:** {u.text}"
        for u in utterances
    ]
    return "\n\n".join(paragraphs) + "\n"


def _display(speaker: str, labels: Mapping[str, SpeakerLabel]) -> str:
    label = labels.get(speaker)
    return label.display if label else speaker
