"""The real faster-whisper backend: the only module that imports the ML libraries (AC-20).

Every import is inside a method, so importing this module costs nothing and the service process
never loads ``faster_whisper`` or ``ctranslate2``; only the transcribe worker calls these.
"""

import importlib.metadata

from meeting_scribe.transcription.domain.params import Device
from meeting_scribe.transcription.infrastructure.cuda_preload import preload_cuda_libraries
from meeting_scribe.transcription.infrastructure.faster_whisper_transcriber import ModelLike

_ENGINES = ("faster-whisper", "ctranslate2")


class FasterWhisperBackend:
    def prepare(self, device: Device) -> None:
        if device == "cuda":  # a CPU run needs none of the NVIDIA wheels
            preload_cuda_libraries()

    def cuda_device_count(self) -> int:
        import ctranslate2  # noqa: PLC0415 - lazy by design

        return int(ctranslate2.get_cuda_device_count())

    def load_model(self, model: str, device: Device, compute_type: str) -> ModelLike:
        from faster_whisper import WhisperModel  # noqa: PLC0415 - lazy by design

        return WhisperModel(model, device=device, compute_type=compute_type)

    def engine_versions(self) -> dict[str, str]:
        """Read from package metadata: no import, so a cache hit never loads the library."""
        versions: dict[str, str] = {}
        for name in _ENGINES:
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                versions[name] = "not-installed"
        return versions
