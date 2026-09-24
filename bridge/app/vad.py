"""Streaming endpointer: decides when the user has finished speaking.

The Voice PE runs an XMOS XU316 front end (AEC, beamforming, noise
suppression) before anything leaves the device, so the PCM arriving here is
already cleaned up and WebRTC's VAD is sufficient. `Endpointer` is deliberately
narrow — swap `_is_voiced` for a Silero call if your room proves harder.
"""

from __future__ import annotations

import collections
import logging

import webrtcvad

from .config import Config
from .protocol import MIC_SAMPLE_RATE, SAMPLE_WIDTH

_LOG = logging.getLogger(__name__)

FRAME_MS = 30
FRAME_BYTES = int(MIC_SAMPLE_RATE * FRAME_MS / 1000) * SAMPLE_WIDTH  # 960


class Endpointer:
    """Feed microphone bytes in, get a complete utterance out."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._vad = webrtcvad.Vad(cfg.vad_aggressiveness)
        self._onset_window = max(1, int(300 / FRAME_MS))
        self._preroll_frames = max(1, int(cfg.vad_speech_pad_ms / FRAME_MS))
        self._max_bytes = int(cfg.vad_max_utterance_s * MIC_SAMPLE_RATE) * SAMPLE_WIDTH
        self.reset()

    def reset(self) -> None:
        self._tail = bytearray()
        self._preroll: collections.deque[bytes] = collections.deque(maxlen=self._preroll_frames)
        self._onset: collections.deque[bool] = collections.deque(maxlen=self._onset_window)
        self._voiced = bytearray()
        self._triggered = False
        self._silence_ms = 0.0
        self._speech_ms = 0.0

    @property
    def triggered(self) -> bool:
        return self._triggered

    def push(self, pcm: bytes) -> bytes | None:
        """Consume microphone bytes. Returns the utterance once it ends."""
        self._tail.extend(pcm)
        utterance: bytes | None = None

        while len(self._tail) >= FRAME_BYTES and utterance is None:
            frame = bytes(self._tail[:FRAME_BYTES])
            del self._tail[:FRAME_BYTES]
            utterance = self._consume_frame(frame)

        return utterance

    def flush(self) -> bytes | None:
        """Force an endpoint, e.g. when the follow-up window closes."""
        if not self._triggered:
            self.reset()
            return None
        utterance = self._finish()
        self.reset()
        return utterance

    # -- internals ---------------------------------------------------------

    def _is_voiced(self, frame: bytes) -> bool:
        try:
            return self._vad.is_speech(frame, MIC_SAMPLE_RATE)
        except Exception:  # malformed frame length; treat as silence
            return False

    def _consume_frame(self, frame: bytes) -> bytes | None:
        voiced = self._is_voiced(frame)

        if not self._triggered:
            self._preroll.append(frame)
            self._onset.append(voiced)
            ratio = sum(self._onset) / self._onset.maxlen
            if ratio >= self._cfg.vad_threshold:
                self._triggered = True
                for buffered in self._preroll:
                    self._voiced.extend(buffered)
                self._speech_ms = len(self._preroll) * FRAME_MS
                self._silence_ms = 0.0
                self._preroll.clear()
                self._onset.clear()
            return None

        self._voiced.extend(frame)
        if voiced:
            self._speech_ms += FRAME_MS
            self._silence_ms = 0.0
        else:
            self._silence_ms += FRAME_MS

        if self._silence_ms >= self._cfg.vad_min_silence_ms:
            utterance = self._finish()
            self.reset()
            return utterance

        if len(self._voiced) >= self._max_bytes:
            _LOG.info("utterance hit the %.0fs ceiling, cutting", self._cfg.vad_max_utterance_s)
            utterance = self._finish()
            self.reset()
            return utterance

        return None

    def _finish(self) -> bytes | None:
        if self._speech_ms < self._cfg.vad_min_speech_ms:
            _LOG.debug("dropping %.0f ms blip", self._speech_ms)
            return None
        return bytes(self._voiced)
