"""Per-device session: wake -> listen -> transcribe -> Hermes -> speak."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from . import protocol
from .chunker import SentenceChunker
from .config import Config
from .llm import HermesClient
from .protocol import TTS_SAMPLE_RATE
from .stt import Transcriber
from .tts import Synthesizer
from .vad import Endpointer

_LOG = logging.getLogger(__name__)

AUDIO_FRAME_MS = 40
MAX_LEAD_MS = 700  # how far ahead of playback we are allowed to buffer


class State:
    IDLE = "idle"
    LISTENING = "listening"
    BUSY = "busy"  # thinking or replying; the reply task owns the phase


class Session:
    def __init__(
        self,
        websocket,
        *,
        cfg: Config,
        transcriber: Transcriber,
        synthesizer: Synthesizer,
        hermes: HermesClient,
        peer: str,
    ) -> None:
        self._ws = websocket
        self._cfg = cfg
        self._stt = transcriber
        self._tts = synthesizer
        self._hermes = hermes
        self._peer = peer

        self._endpointer = Endpointer(cfg)
        self._state = State.IDLE
        self._history: list[dict] = []
        self._reply_task: asyncio.Task | None = None
        self._silence_task: asyncio.Task | None = None
        self._audio_deadline = 0.0

    # -- lifecycle ---------------------------------------------------------

    async def run(self) -> None:
        await self._send_text(
            protocol.hello(
                follow_up_ms=self._cfg.follow_up_ms,
                follow_up_open_delay_ms=self._cfg.follow_up_open_delay_ms,
                wake_open_delay_ms=self._cfg.wake_open_delay_ms,
                playback_prebuffer_ms=self._cfg.playback_prebuffer_ms,
            )
        )
        await self._set_phase(protocol.PHASE_IDLE)
        _LOG.info("[%s] session open", self._peer)

        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    await self._on_audio(message)
                else:
                    await self._on_text(message)
        finally:
            await self._cancel_reply()
            self._cancel_silence_timer()
            _LOG.info("[%s] session closed", self._peer)

    # -- inbound -----------------------------------------------------------

    async def _on_text(self, raw: str) -> None:
        kind = protocol.parse_device_message(raw)
        if kind is None:
            _LOG.debug("[%s] ignoring text frame: %r", self._peer, raw[:120])
            return

        if kind == "start":
            _LOG.debug("[%s] device started", self._peer)
        elif kind == "wake":
            await self._begin_listening()
        elif kind == "interrupt":
            _LOG.info("[%s] interrupt", self._peer)
            await self._go_idle()
        elif kind == "flush":
            self._endpointer.reset()
            if self._state == State.LISTENING:
                await self._go_idle()
        else:
            _LOG.debug("[%s] unhandled message type %r", self._peer, kind)

    async def _on_audio(self, pcm: bytes) -> None:
        if self._state != State.LISTENING:
            return
        utterance = self._endpointer.push(pcm)
        if self._endpointer.triggered:
            self._cancel_silence_timer()
        if utterance:
            self._state = State.BUSY
            self._reply_task = asyncio.create_task(self._reply(utterance))

    # -- state transitions -------------------------------------------------

    async def _begin_listening(self) -> None:
        await self._cancel_reply()
        self._endpointer.reset()
        self._state = State.LISTENING
        await self._set_phase(protocol.PHASE_LISTENING)
        self._arm_silence_timer(self._cfg.no_speech_timeout_s)

    async def _go_idle(self) -> None:
        await self._cancel_reply()
        self._cancel_silence_timer()
        self._endpointer.reset()
        self._audio_deadline = 0.0
        self._state = State.IDLE
        await self._set_phase(protocol.PHASE_IDLE)

    async def _cancel_reply(self) -> None:
        task, self._reply_task = self._reply_task, None
        if task is None or task.done():
            return
        if task is asyncio.current_task():
            # _reply() is unwinding itself (empty transcript, or an error it
            # already handled). Cancelling here would make the task await on
            # itself; let it return on its own instead.
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _arm_silence_timer(self, seconds: float) -> None:
        self._cancel_silence_timer()
        self._silence_task = asyncio.create_task(self._silence_watchdog(seconds))

    def _cancel_silence_timer(self) -> None:
        task, self._silence_task = self._silence_task, None
        if task and not task.done():
            task.cancel()

    async def _silence_watchdog(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        if self._state == State.LISTENING and not self._endpointer.triggered:
            _LOG.info("[%s] no speech after %.1fs, going idle", self._peer, seconds)
            await self._go_idle()

    # -- the reply pipeline ------------------------------------------------

    async def _reply(self, pcm: bytes) -> None:
        try:
            await self._set_phase(protocol.PHASE_THINKING)
            transcript = await self._stt.transcribe(pcm)
            if not transcript:
                _LOG.info("[%s] empty transcript, dropping turn", self._peer)
                await self._go_idle()
                return

            reply = await self._speak_response(transcript)
            _LOG.info("[%s] %r -> %r", self._peer, transcript, reply)

            self._remember(transcript, reply)
            await self._drain()
            await self._send_text(protocol.request_follow_up())
            self._endpointer.reset()
            self._state = State.LISTENING
            await self._set_phase(protocol.PHASE_LISTENING)
            self._arm_silence_timer((self._cfg.follow_up_ms / 1000.0) + 2.0)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never kill the session
            _LOG.exception("[%s] reply failed: %s", self._peer, exc)
            with contextlib.suppress(Exception):
                await self._send_text(protocol.error(str(exc)[:200]))
            await self._go_idle()

    async def _speak_response(self, transcript: str) -> str:
        messages = [{"role": "system", "content": self._cfg.system_prompt}]
        messages.extend(self._history)
        messages.append({"role": "user", "content": transcript})

        stream = self._tts.new_stream()
        chunker = SentenceChunker()
        spoken: list[str] = []
        started = False

        async for token in self._hermes.stream(messages):
            for chunk in chunker.push(token):
                audio = await stream.synthesize(chunk)
                if not started:
                    await self._set_phase(protocol.PHASE_REPLYING)
                    started = True
                spoken.append(chunk)
                await self._send_audio(audio)

        tail = chunker.flush()
        if tail:
            audio = await stream.synthesize(tail)
            if not started:
                await self._set_phase(protocol.PHASE_REPLYING)
                started = True
            spoken.append(tail)
            await self._send_audio(audio)

        await self._send_audio(stream.finish())
        return " ".join(spoken).strip()

    def _remember(self, user_text: str, assistant_text: str) -> None:
        if not assistant_text:
            return
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": assistant_text})
        limit = self._cfg.history_turns * 2
        if len(self._history) > limit:
            self._history = self._history[-limit:]

    # -- outbound ----------------------------------------------------------

    async def _set_phase(self, value: str) -> None:
        await self._send_text(protocol.phase(value))

    async def _send_text(self, payload: str) -> None:
        await self._ws.send(payload)

    async def _send_audio(self, pcm: bytes) -> None:
        """Stream PCM to the device, staying just ahead of playback."""
        if not pcm:
            return

        now = time.monotonic()
        if self._audio_deadline < now:
            self._audio_deadline = now + self._cfg.playback_prebuffer_ms / 1000.0

        max_lead = (self._cfg.playback_prebuffer_ms + MAX_LEAD_MS) / 1000.0
        for frame in protocol.chunk_pcm(pcm, AUDIO_FRAME_MS, TTS_SAMPLE_RATE):
            await self._ws.send(frame)
            self._audio_deadline += protocol.frame_duration_ms(len(frame), TTS_SAMPLE_RATE) / 1000.0
            lead = self._audio_deadline - time.monotonic()
            if lead > max_lead:
                await asyncio.sleep(lead - max_lead)

    async def _drain(self) -> None:
        remaining = self._audio_deadline - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._audio_deadline = 0.0
