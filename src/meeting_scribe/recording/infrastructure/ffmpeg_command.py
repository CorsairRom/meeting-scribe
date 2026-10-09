"""Pure ffmpeg command construction (ADR-002: channel 0 = microphone, channel 1 = system)."""

from pathlib import Path

CHUNK_PATTERN = "chunk_%05d.wav"


def pulse_input_args(device: str) -> list[str]:
    """Input arguments for a PulseAudio/PipeWire source. Production wiring only."""
    return ["-f", "pulse", "-i", device]


def build_ffmpeg_command(
    ffmpeg_binary: str,
    input_args_mic: list[str],
    input_args_system: list[str],
    audio_dir: Path,
    chunk_seconds: int,
    sample_rate: int,
) -> list[str]:
    """Record two inputs into 2-channel s16le WAV chunks (mic left, system right).

    Each input is converted to mono at ``sample_rate`` and merged: ``amerge`` keeps input order, so
    the microphone (first input) is channel 0. ``-nostdin`` must stay off: ``q`` on stdin is the
    clean stop signal.
    """
    mono = f"aformat=sample_rates={sample_rate}:channel_layouts=mono"
    graph = f"[0:a]{mono}[mic];[1:a]{mono}[system];[mic][system]amerge=inputs=2[out]"
    return [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        *input_args_mic,
        *input_args_system,
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-c:a",
        "pcm_s16le",
        "-f",
        "segment",
        "-segment_time",
        str(chunk_seconds),
        "-reset_timestamps",
        "1",
        str(audio_dir / CHUNK_PATTERN),
    ]
