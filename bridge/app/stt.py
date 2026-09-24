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

    ``whisper_model`` must point at an exported model directory, e.g.

        optimum-cli export openvino --model openai/whisper-large-v3-turbo \\
            --weight-format int8 /models/whisper-turbo-int8
    """

    def __init__(self, cfg: Config) -> None:
        import openvino_genai

        self._cfg = cfg
        device = cfg.whisper_device.upper()  # "GPU" | "CPU" | "AUTO" | "NPU"
        _LOG.info("loading openvino whisper from %s on %s", cfg.whisper_model, device)
        self._pipe = openvino_genai.WhisperPipeline(cfg.whisper_model, device=device)

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
