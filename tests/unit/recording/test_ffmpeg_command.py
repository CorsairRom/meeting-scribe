from pathlib import Path

from meeting_scribe.recording.infrastructure.ffmpeg_command import (
    build_ffmpeg_command,
    pulse_input_args,
)

AUDIO_DIR = Path("/data/meeting/audio")


def _command() -> list[str]:
    return build_ffmpeg_command(
        "ffmpeg",
        input_args_mic=pulse_input_args("mic-source"),
        input_args_system=pulse_input_args("speakers.monitor"),
        audio_dir=AUDIO_DIR,
        chunk_seconds=30,
        sample_rate=16000,
    )


def test_pulse_input_args_is_plain_data() -> None:
    assert pulse_input_args("dev") == ["-f", "pulse", "-i", "dev"]


def test_exact_command_for_pulse_inputs() -> None:
    assert _command() == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "pulse",
        "-i",
        "mic-source",
        "-f",
        "pulse",
        "-i",
        "speakers.monitor",
        "-filter_complex",
        (
            "[0:a]aformat=sample_rates=16000:channel_layouts=mono[mic];"
            "[1:a]aformat=sample_rates=16000:channel_layouts=mono[system];"
            "[mic][system]amerge=inputs=2[out]"
        ),
        "-map",
        "[out]",
        "-c:a",
        "pcm_s16le",
        "-f",
        "segment",
        "-segment_time",
        "30",
        "-reset_timestamps",
        "1",
        "/data/meeting/audio/chunk_%05d.wav",
    ]


def test_mic_is_first_input_so_it_becomes_channel_zero() -> None:
    command = _command()

    assert command.index("mic-source") < command.index("speakers.monitor")
    assert "[mic][system]amerge=inputs=2" in command[command.index("-filter_complex") + 1]


def test_stdin_is_not_disabled_because_it_carries_q() -> None:
    assert "-nostdin" not in _command()


def test_chunk_seconds_and_sample_rate_are_parameters() -> None:
    command = build_ffmpeg_command(
        "/opt/ffmpeg", ["-i", "a"], ["-i", "b"], AUDIO_DIR, chunk_seconds=5, sample_rate=48000
    )

    assert command[0] == "/opt/ffmpeg"
    assert command[command.index("-segment_time") + 1] == "5"
    assert "sample_rates=48000" in command[command.index("-filter_complex") + 1]
