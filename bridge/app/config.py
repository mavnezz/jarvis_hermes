"""Runtime configuration, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_SYSTEM_PROMPT = (
    "Du bist Jarvis, ein gesprochener Assistent. Deine Antworten werden vorgelesen, "
    "nicht gelesen. Antworte deshalb kurz, in ganzen Sätzen und ohne Aufzählungen, "
    "Markdown, Emojis oder Sonderzeichen. Zahlen und Einheiten schreibst du aus. "
    "Wenn du etwas nicht weisst, sagst du das in einem Satz."
)


@dataclass(frozen=True)
class Config:
    # --- websocket server the Voice PE connects to ---------------------------
    host: str = field(default_factory=lambda: _str("BRIDGE_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("BRIDGE_PORT", 8765))

    # --- Hermes (OpenAI-compatible chat completions) -------------------------
    hermes_url: str = field(default_factory=lambda: _str("HERMES_URL", "http://127.0.0.1:7237/v1"))
    hermes_key: str = field(default_factory=lambda: _str("HERMES_KEY", ""))
    hermes_model: str = field(default_factory=lambda: _str("HERMES_MODEL", "hermes"))
    hermes_stream: bool = field(default_factory=lambda: _bool("HERMES_STREAM", True))
    hermes_timeout_s: float = field(default_factory=lambda: _float("HERMES_TIMEOUT_S", 60.0))
    system_prompt: str = field(default_factory=lambda: _str("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT))
    history_turns: int = field(default_factory=lambda: _int("HISTORY_TURNS", 8))

    # --- speech to text ------------------------------------------------------
    # "faster-whisper" runs on CPU everywhere (and on CUDA where present);
    # "openvino" is the only backend that can use an Intel iGPU.
    whisper_backend: str = field(default_factory=lambda: _str("WHISPER_BACKEND", "faster-whisper"))
    whisper_model: str = field(default_factory=lambda: _str("WHISPER_MODEL", "cstr/whisper-large-v3-turbo-german-int8_float32"))
    whisper_device: str = field(default_factory=lambda: _str("WHISPER_DEVICE", "cpu"))
    whisper_compute: str = field(default_factory=lambda: _str("WHISPER_COMPUTE", "default"))
    whisper_cpu_threads: int = field(default_factory=lambda: _int("WHISPER_CPU_THREADS", 0))
    whisper_beam: int = field(default_factory=lambda: _int("WHISPER_BEAM", 1))
    language: str = field(default_factory=lambda: _str("LANGUAGE", "de"))

    # --- text to speech ------------------------------------------------------
    piper_model: str = field(
        default_factory=lambda: _str("PIPER_MODEL", "/voices/de_DE-thorsten-medium.onnx")
    )
    piper_length_scale: float = field(default_factory=lambda: _float("PIPER_LENGTH_SCALE", 1.0))
    piper_noise_scale: float = field(default_factory=lambda: _float("PIPER_NOISE_SCALE", 0.667))
    piper_noise_w: float = field(default_factory=lambda: _float("PIPER_NOISE_W", 0.8))

    # --- endpointing ---------------------------------------------------------
    vad_aggressiveness: int = field(default_factory=lambda: _int("VAD_AGGRESSIVENESS", 2))
    vad_threshold: float = field(default_factory=lambda: _float("VAD_THRESHOLD", 0.6))
    vad_min_silence_ms: int = field(default_factory=lambda: _int("VAD_MIN_SILENCE_MS", 700))
    vad_speech_pad_ms: int = field(default_factory=lambda: _int("VAD_SPEECH_PAD_MS", 300))
    vad_min_speech_ms: int = field(default_factory=lambda: _int("VAD_MIN_SPEECH_MS", 250))
    vad_max_utterance_s: float = field(default_factory=lambda: _float("VAD_MAX_UTTERANCE_S", 30.0))
    no_speech_timeout_s: float = field(default_factory=lambda: _float("NO_SPEECH_TIMEOUT_S", 8.0))

    # --- values pushed to the device in the hello handshake ------------------
    follow_up_ms: int = field(default_factory=lambda: _int("FOLLOW_UP_MS", 6000))
    follow_up_open_delay_ms: int = field(default_factory=lambda: _int("FOLLOW_UP_OPEN_DELAY_MS", 250))
    wake_open_delay_ms: int = field(default_factory=lambda: _int("WAKE_OPEN_DELAY_MS", 0))
    playback_prebuffer_ms: int = field(default_factory=lambda: _int("PLAYBACK_PREBUFFER_MS", 400))

    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO"))

    @property
    def chat_url(self) -> str:
        return self.hermes_url.rstrip("/") + "/chat/completions"


def load() -> Config:
    return Config()
