"""Wire protocol spoken by the Voice PE `va_client` ESPHome component.

The firmware does NOT run a JSON parser on incoming text frames — it looks for
literal substrings such as `"type":"hello"` and scans for digits after a key.
Every payload here is therefore serialised with compact separators and a fixed
key order. Do not "prettify" these messages; spaces after the colon break the
device-side match.

Device -> bridge
    text    {"type":"start"}       websocket connected
    text    {"type":"wake"}        wake word fired, session opening
    text    {"type":"interrupt"}   barge-in, stop word, or no speech detected
    text    {"type":"flush"}       follow-up window expired, drop partial audio
    binary  PCM16 mono @ 16 kHz    microphone frames

Bridge -> device
    text    {"type":"hello",...}          timing config, sent on connect
    text    {"type":"phase","value":...}  drives LED ring, mic gate, watchdog
    text    {"type":"request_follow_up"}  reopen mic once the speaker drains
    text    {"type":"error",...}          firmware plays its failure chime
    binary  PCM16 mono @ 24 kHz           TTS audio
"""

from __future__ import annotations

import json
from typing import Iterator

MIC_SAMPLE_RATE = 16_000
TTS_SAMPLE_RATE = 24_000
SAMPLE_WIDTH = 2  # int16

PHASE_IDLE = "idle"
PHASE_LISTENING = "listening"
PHASE_THINKING = "thinking"
PHASE_REPLYING = "replying"


def _dump(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def hello(
    *,
    follow_up_ms: int,
    follow_up_open_delay_ms: int,
    wake_open_delay_ms: int,
    playback_prebuffer_ms: int,
) -> str:
    return _dump(
        {
            "type": "hello",
            "follow_up_ms": follow_up_ms,
            "follow_up_open_delay_ms": follow_up_open_delay_ms,
            "wake_open_delay_ms": wake_open_delay_ms,
            "playback_prebuffer_ms": playback_prebuffer_ms,
        }
    )


def phase(value: str) -> str:
    return _dump({"type": "phase", "value": value})


def request_follow_up() -> str:
    return _dump({"type": "request_follow_up"})


def error(message: str = "") -> str:
    return _dump({"type": "error", "message": message})


def parse_device_message(raw: str) -> str | None:
    """Return the `type` of a device text frame, or None if unrecognised."""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    kind = payload.get("type")
    return kind if isinstance(kind, str) else None


def frame_duration_ms(num_bytes: int, sample_rate: int) -> float:
    return (num_bytes / SAMPLE_WIDTH) / sample_rate * 1000.0


def chunk_pcm(pcm: bytes, chunk_ms: int, sample_rate: int) -> Iterator[bytes]:
    """Split raw PCM16 into frames of roughly `chunk_ms`, aligned to samples."""
    size = max(SAMPLE_WIDTH, int(sample_rate * chunk_ms / 1000) * SAMPLE_WIDTH)
    for offset in range(0, len(pcm), size):
        yield pcm[offset : offset + size]
