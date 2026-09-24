"""Speech to text.

Two backends, because "GPU" means very different things:

* ``faster-whisper`` (CTranslate2) — runs on CPU everywhere and on NVIDIA CUDA
  where available. ``int8`` on CPU is the portable default.
* ``openvino`` — Intel's runtime. This is the only one of the two that can use
  an Intel iGPU. Needs a model exported with ``optimum-cli export openvino``
  and ``/dev/dri`` passed into the container.

Both return plain text; the session code does not care which is loaded.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Protocol

import numpy as np

from .config import Config
from .protocol import MIC_SAMPLE_RATE

_LOG = logging.getLogger(__name__)


def _to_float32(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


class _Backend(Protocol):
    def transcribe(self, audio: np.ndarray) -> str: ...


class FasterWhisperBackend:
    def __init__(self, cfg: Config) -> None:
        from faster_whisper import WhisperModel

        self._cfg = cfg
        _LOG.info(
            "loading faster-whisper %s on %s (%s)",
            cfg.whisper_model, cfg.whisper_device, cfg.whisper_compute,
        )
        self._model = WhisperModel(
            cfg.whisper_model,
            device=cfg.whisper_device,
            compute_type=cfg.whisper_compute,
            cpu_threads=cfg.whisper_cpu_threads,
        )

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _info = self._model.transcribe(
            audio,
            language=self._cfg.language,
            beam_size=self._cfg.whisper_beam,
            vad_filter=False,  # already endpointed upstream
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


class OpenVinoBackend:
    """Intel iGPU (or CPU) via OpenVINO GenAI.

    ``whisper_model`` may be either a local directory holding an exported
    model, or a HuggingFace repo id such as
    ``OpenVINO/whisper-large-v3-turbo-int8-ov`` — Intel publishes ready-made
    conversions, which saves an ``optimum-cli export`` step. WhisperPipeline
    itself only accepts a local path, so a repo id is downloaded first.
    """

    def __init__(self, cfg: Config) -> None:
        import openvino_genai

        self._cfg = cfg
        path = self._resolve(cfg.whisper_model)
        device = cfg.whisper_device.upper()  # "GPU" | "CPU" | "AUTO" | "NPU"

        try:
            self._pipe = self._open(openvino_genai, path, device)
            self._device = device
        except Exception as exc:  # noqa: BLE001
            if device == "CPU":
                raise
            # Weak iGPUs run out of memory on the large encoder
            # (CL_OUT_OF_RESOURCES). Falling back beats a restart loop.
            _LOG.warning("openvino on %s failed (%s)", device, str(exc).strip()[:200])
            _LOG.warning("falling back to CPU — expect this to be slower")
            self._pipe = self._open(openvino_genai, path, "CPU")
            self._device = "CPU"

        _LOG.info("openvino whisper ready on %s (%s)", self._device, path)

    @staticmethod
    def _resolve(model: str) -> str:
        if Path(model).is_dir():
            return model
        from huggingface_hub import snapshot_download

        _LOG.info("downloading openvino model %s", model)
        return snapshot_download(model)

    def _open(self, openvino_genai, path: str, device: str):
        _LOG.info("compiling openvino whisper for %s (first start takes a while)", device)
        pipe = openvino_genai.WhisperPipeline(path, device=device)
        # Compilation succeeds long before inference does, so prove the device
        # can actually run a window before we accept it.
        silence = np.zeros(16_000, dtype=np.float32)
        pipe.generate(silence, language=f"<|{self._cfg.language}|>", task="transcribe")
        return pipe

    def transcribe(self, audio: np.ndarray) -> str:
        result = self._pipe.generate(
            audio,
            language=f"<|{self._cfg.language}|>",
            task="transcribe",
        )
        texts = getattr(result, "texts", None)
        text = texts[0] if texts else str(result)
        return text.strip()


_BACKENDS = {
    "faster-whisper": FasterWhisperBackend,
    "openvino": OpenVinoBackend,
}


class Transcriber:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        try:
            factory = _BACKENDS[cfg.whisper_backend]
        except KeyError:
            raise ValueError(
                f"unknown WHISPER_BACKEND {cfg.whisper_backend!r}; "
                f"expected one of {', '.join(sorted(_BACKENDS))}"
            ) from None
        self._backend: _Backend = factory(cfg)

    async def transcribe(self, pcm: bytes) -> str:
        return await asyncio.to_thread(self._transcribe_sync, pcm)

    def _transcribe_sync(self, pcm: bytes) -> str:
        started = time.monotonic()
        audio = _to_float32(pcm)
        text = self._backend.transcribe(audio)
        duration = len(audio) / MIC_SAMPLE_RATE
        elapsed = time.monotonic() - started
        _LOG.info(
            "stt %.2fs audio in %.2fs (%.1fx realtime) -> %r",
            duration, elapsed, duration / elapsed if elapsed else 0.0, text,
        )
        return text
