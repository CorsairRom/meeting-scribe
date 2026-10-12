"""The real pyannote backend: the only module that imports torch and pyannote (AC-20).

Every import is inside a method, so importing this module costs nothing and the service process
never loads ``torch`` or ``pyannote``; only the diarize worker calls these.
"""

import importlib.metadata
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from meeting_scribe.base.domain.audio import SAMPLE_RATE, Samples
from meeting_scribe.base.infrastructure.hf_cache import snapshot_is_complete
from meeting_scribe.speakers.domain.diarization import Device
from meeting_scribe.speakers.infrastructure.pyannote_diarizer import PipelineLike

_ENGINES = ("pyannote.audio", "torch")
TELEMETRY_VARIABLE = "PYANNOTE_METRICS_ENABLED"
"""pyannote.audio 4.0.7 ships ``metrics_enabled: true`` and reads this variable on every call."""
_MODEL_REFERENCE = re.compile(r"\$model/([\w./-]+)")
"""How a pipeline's ``config.yaml`` names the files it ships (``embedding: $model/embedding``)."""


class RealPyannoteBackend:
    def prepare(self) -> None:
        # Forced, not ``setdefault``: whatever the environment says, nothing is ever reported.
        os.environ[TELEMETRY_VARIABLE] = "false"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    def cuda_device_count(self) -> int:
        import torch  # noqa: PLC0415 - lazy by design

        return int(torch.cuda.device_count())

    def load_pipeline(self, pipeline: str, device: Device) -> PipelineLike:
        import torch  # noqa: PLC0415 - lazy by design
        from pyannote.audio import Pipeline  # noqa: PLC0415 - lazy by design

        # token=None: processing is offline and needs no login. The cached files load as they are;
        # the loader never reads a stored token (AC-15).
        loaded = Pipeline.from_pretrained(pipeline, token=None)
        if loaded is None:
            raise RuntimeError(f"Pipeline '{pipeline}' could not be loaded")
        loaded.to(torch.device(device))
        # pyannote's ``__call__`` is loosely typed (``**kwargs``); the port states what we use.
        return cast("PipelineLike", loaded)

    def fetch_pipeline(self, pipeline: str, token: str | None = None) -> None:
        """Fetch the pipeline and every model it names into the cache (download time).

        The same loader the worker uses, so a successful fetch guarantees an offline load. It stays
        on the CPU. With a ``token`` it is passed explicitly (never stored, never logged); without
        one the ambient Hugging Face login is used (``token=True``), never read here.
        """
        from pyannote.audio import Pipeline  # noqa: PLC0415 - lazy by design

        self.prepare()  # telemetry off before the first import, as in the worker
        if Pipeline.from_pretrained(pipeline, token=True if token is None else token) is None:
            raise RuntimeError(f"Pipeline '{pipeline}' could not be loaded")

    def is_pipeline_cached(self, pipeline: str) -> bool:
        """True when the pipeline and every file its config names are fully in the cache.

        Offline: ``local_files_only`` means no network and no login, and nothing is loaded.
        """
        from huggingface_hub import snapshot_download  # noqa: PLC0415 - lazy by design

        try:
            snapshot = Path(snapshot_download(pipeline, local_files_only=True))
            config = (snapshot / "config.yaml").read_text(encoding="utf-8")
        except Exception:  # not cached, unreadable, library missing: all "not cached"
            return False
        required = ["config.yaml", *_MODEL_REFERENCE.findall(config)]
        return snapshot_is_complete(snapshot, required)

    def make_input(self, audio: Samples) -> Mapping[str, object]:
        import torch  # noqa: PLC0415 - lazy by design

        # (channel, time) float32 sharing memory with the array: no copy of ~230 MB per hour.
        return {"waveform": torch.from_numpy(audio).unsqueeze(0), "sample_rate": SAMPLE_RATE}

    def engine_versions(self) -> dict[str, str]:
        """Read from package metadata: no import, so a cache hit never loads the library."""
        versions: dict[str, str] = {}
        for name in _ENGINES:
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                versions[name] = "not-installed"
        return versions
