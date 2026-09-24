"""Text to speech via Piper, resampled to the 24 kHz the firmware expects.

Piper's German voices render at 22.05 kHz; the Voice PE speaker path is fixed
at 24 kHz PCM16 mono. Resampling runs through a single streaming soxr instance
per reply so consecutive sentences join without boundary clicks.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import numpy as np
import soxr

from .config import Config
from .protocol import TTS_SAMPLE_RATE

_LOG = logging.getLogger(__name__)


def _load_voice(cfg: Config):
    from piper import PiperVoice  # imported late so import errors surface here

    model = Path(cfg.piper_model)
    if not model.exists():
        raise FileNotFoundError(
            f"piper voice not found at {model} — see bridge/scripts/fetch_voice.sh"
        )
    config = model.with_suffix(model.suffix + ".json")
    if not config.exists():
        config = model.with_suffix(".json")
    return PiperVoice.load(str(model), config_path=str(config) if config.exists() else None)


class Synthesizer:
    """Wraps a loaded Piper voice and hides the 1.2 / 1.3+ API split."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._voice = _load_voice(cfg)
        self._native_rate = int(getattr(self._voice.config, "sample_rate", 22050))
        self._legacy_api = hasattr(self._voice, "synthesize_stream_raw")
        _LOG.info(
            "piper voice %s at %d Hz (%s api)",
            cfg.piper_model,
            self._native_rate,
            "1.2" if self._legacy_api else "1.3+",
        )

    @property
    def native_rate(self) -> int:
        return self._native_rate

    def new_stream(self) -> "SpeechStream":
        return SpeechStream(self, self._native_rate)

    def render(self, text: str) -> bytes:
        """Blocking synthesis at the voice's native rate."""
        if self._legacy_api:
            return b"".join(
                self._voice.synthesize_stream_raw(
                    text,
                    length_scale=self._cfg.piper_length_scale,
                    noise_scale=self._cfg.piper_noise_scale,
                    noise_w=self._cfg.piper_noise_w,
                )
            )
        return b"".join(self._render_modern(text))

    def _render_modern(self, text: str):
        from piper import SynthesisConfig

        syn_config = SynthesisConfig(
            length_scale=self._cfg.piper_length_scale,
            noise_scale=self._cfg.piper_noise_scale,
            noise_w_scale=self._cfg.piper_noise_w,
        )
        for chunk in self._voice.synthesize(text, syn_config=syn_config):
            yield chunk.audio_int16_bytes


class SpeechStream:
    """One reply's worth of audio; keeps resampler state across sentences."""

    def __init__(self, synthesizer: Synthesizer, in_rate: int) -> None:
        self._synth = synthesizer
        self._in_rate = in_rate
        self._passthrough = in_rate == TTS_SAMPLE_RATE
        self._resampler = (
            None
            if self._passthrough
            else soxr.ResampleStream(in_rate, TTS_SAMPLE_RATE, 1, dtype="int16", quality="HQ")
        )

    async def synthesize(self, text: str) -> bytes:
        raw = await asyncio.to_thread(self._synth.render, text)
        if not raw:
            return b""
        return self._resample(raw, last=False)

    def finish(self) -> bytes:
        if self._passthrough or self._resampler is None:
            return b""
        return self._resample(b"", last=True)

    def _resample(self, raw: bytes, *, last: bool) -> bytes:
        if self._passthrough:
            return raw
        samples = np.frombuffer(raw, dtype=np.int16)
        out = self._resampler.resample_chunk(samples, last=last)
        return np.asarray(out, dtype=np.int16).tobytes()
