"""End-to-end exercise of the session state machine against a fake device."""

import asyncio
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import stubs  # noqa: E402

stubs.install()

from app import protocol  # noqa: E402
from app.config import Config  # noqa: E402
from app.session import Session  # noqa: E402
from app.vad import FRAME_BYTES, Endpointer  # noqa: E402

SPEECH = b"\x11\x22" * (FRAME_BYTES // 2)
SILENCE = b"\x00" * FRAME_BYTES


def make_config(**overrides):
    cfg = Config()
    return dataclasses.replace(cfg, **overrides)


# --------------------------------------------------------------------------
# endpointer
# --------------------------------------------------------------------------

def test_endpointer_emits_after_trailing_silence():
    cfg = make_config(vad_min_silence_ms=700, vad_speech_pad_ms=300, vad_min_speech_ms=250)
    ep = Endpointer(cfg)

    for _ in range(20):                      # 600 ms of speech
        assert ep.push(SPEECH) is None
    assert ep.triggered

    utterance = None
    for _ in range(30):                      # up to 900 ms of silence
        utterance = ep.push(SILENCE)
        if utterance:
            break
    assert utterance is not None, "never endpointed"

    # 600 ms speech plus the 700 ms of silence that proved the end, rounded up
    # to whole 30 ms frames. Nothing may be truncated off the front.
    seconds = len(utterance) / 2 / protocol.MIC_SAMPLE_RATE
    assert 1.28 < seconds < 1.36, seconds
    assert not ep.triggered, "endpointer did not reset"


def test_endpointer_keeps_audio_from_before_the_trigger():
    """Onset detection needs a few frames; those must not be clipped off."""
    cfg = make_config(vad_min_silence_ms=300, vad_speech_pad_ms=300, vad_min_speech_ms=100)
    ep = Endpointer(cfg)

    for _ in range(20):                      # room silence before anyone speaks
        assert ep.push(SILENCE) is None
    assert not ep.triggered

    for _ in range(20):
        ep.push(SPEECH)
    assert ep.triggered

    utterance = None
    for _ in range(20):
        utterance = ep.push(SILENCE)
        if utterance:
            break
    assert utterance is not None

    # the 300 ms pad means the utterance opens on pre-onset room tone
    lead_in = utterance[: FRAME_BYTES * 3]
    assert set(lead_in) == {0}, "speech pad did not survive into the utterance"
    seconds = len(utterance) / 2 / protocol.MIC_SAMPLE_RATE
    assert seconds > 0.9, seconds


def test_endpointer_drops_short_blips():
    cfg = make_config(vad_min_silence_ms=300, vad_speech_pad_ms=0, vad_min_speech_ms=2000)
    ep = Endpointer(cfg)
    for _ in range(8):
        ep.push(SPEECH)
    result = None
    for _ in range(20):
        result = ep.push(SILENCE)
        if result is not None:
            break
    assert result is None, "a 240 ms blip should not become an utterance"


def test_endpointer_reassembles_across_frame_boundaries():
    """The device sends arbitrary-sized frames, not neat 30 ms ones."""
    cfg = make_config(vad_min_silence_ms=300, vad_speech_pad_ms=0, vad_min_speech_ms=100)
    ep = Endpointer(cfg)
    blob = SPEECH * 20
    for offset in range(0, len(blob), 137):   # deliberately unaligned
        ep.push(blob[offset : offset + 137])
    assert ep.triggered


# --------------------------------------------------------------------------
# session
# --------------------------------------------------------------------------

class FakeWebSocket:
    def __init__(self):
        self.inbox = asyncio.Queue()
        self.sent = []
        self._closed = False

    async def send(self, message):
        self.sent.append(message)

    def feed(self, message):
        self.inbox.put_nowait(message)

    def close(self):
        self.inbox.put_nowait(None)

    def __aiter__(self):
        async def gen():
            while True:
                item = await self.inbox.get()
                if item is None:
                    return
                yield item
        return gen()

    # -- assertions helpers -------------------------------------------------

    @property
    def texts(self):
        return [m for m in self.sent if isinstance(m, str)]

    @property
    def audio_bytes(self):
        return sum(len(m) for m in self.sent if isinstance(m, bytes))

    def phases(self):
        out = []
        for msg in self.texts:
            if '"type":"phase"' in msg:
                out.append(msg.split('"value":"')[1].split('"')[0])
        return out


class FakeTranscriber:
    def __init__(self, text="wie spaet ist es"):
        self.text = text
        self.calls = 0

    async def transcribe(self, pcm):
        self.calls += 1
        await asyncio.sleep(0)
        return self.text


class FakeSpeechStream:
    def __init__(self, sink):
        self.sink = sink

    async def synthesize(self, text):
        self.sink.append(text)
        await asyncio.sleep(0)
        return b"\x01\x00" * 240          # 10 ms @ 24 kHz

    def finish(self):
        return b""


class FakeSynthesizer:
    def __init__(self):
        self.spoken = []

    def new_stream(self):
        return FakeSpeechStream(self.spoken)


class FakeHermes:
    def __init__(self, tokens=None, delay=0.0):
        self.tokens = tokens or ["Es ", "ist ", "kurz ", "nach ", "drei", "."]
        self.delay = delay
        self.seen_messages = None

    async def stream(self, messages):
        self.seen_messages = messages
        for token in self.tokens:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield token


def build_session(ws, cfg=None, **parts):
    cfg = cfg or make_config(playback_prebuffer_ms=0, no_speech_timeout_s=5.0)
    return Session(
        ws,
        cfg=cfg,
        transcriber=parts.get("transcriber") or FakeTranscriber(),
        synthesizer=parts.get("synthesizer") or FakeSynthesizer(),
        hermes=parts.get("hermes") or FakeHermes(),
        peer="test",
    )


async def wait_for(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def speak_into(ws, speech_frames=20, silence_frames=30):
    for _ in range(speech_frames):
        ws.feed(SPEECH)
    for _ in range(silence_frames):
        ws.feed(SILENCE)


async def _full_turn():
    ws = FakeWebSocket()
    hermes = FakeHermes()
    synth = FakeSynthesizer()
    session = build_session(ws, hermes=hermes, synthesizer=synth)
    task = asyncio.create_task(session.run())

    assert await wait_for(lambda: len(ws.texts) >= 2)
    assert '"type":"hello"' in ws.texts[0], ws.texts[0]
    assert ws.phases()[0] == protocol.PHASE_IDLE

    ws.feed('{"type":"start"}')
    ws.feed('{"type":"wake"}')
    assert await wait_for(lambda: protocol.PHASE_LISTENING in ws.phases())

    speak_into(ws)
    assert await wait_for(lambda: '"type":"request_follow_up"' in " ".join(ws.texts), timeout=5.0)

    ws.close()
    await asyncio.wait_for(task, timeout=3.0)
    return ws, hermes, synth


def test_full_turn_phase_order():
    ws, hermes, synth = asyncio.run(_full_turn())
    phases = ws.phases()
    # idle -> listening -> thinking -> replying -> listening (follow-up)
    assert phases[:4] == ["idle", "listening", "thinking", "replying"], phases
    assert phases[-1] == "listening", phases
    assert ws.audio_bytes > 0, "no TTS audio reached the device"


def test_full_turn_sends_follow_up_after_audio():
    ws, _, _ = asyncio.run(_full_turn())
    audio_indices = [i for i, m in enumerate(ws.sent) if isinstance(m, bytes)]
    follow_up = next(i for i, m in enumerate(ws.sent)
                     if isinstance(m, str) and '"type":"request_follow_up"' in m)
    assert max(audio_indices) < follow_up, "follow-up must come after the last audio frame"


def test_transcript_and_reply_reach_hermes_and_piper():
    ws, hermes, synth = asyncio.run(_full_turn())
    roles = [m["role"] for m in hermes.seen_messages]
    assert roles[0] == "system"
    assert roles[-1] == "user"
    assert hermes.seen_messages[-1]["content"] == "wie spaet ist es"
    assert "".join(synth.spoken).replace(" ", "") == "Esistkurznachdrei."


async def _interrupt_mid_reply():
    ws = FakeWebSocket()
    hermes = FakeHermes(tokens=["Das ", "hier ", "dauert ", "lange", "."], delay=0.25)
    session = build_session(ws, hermes=hermes)
    task = asyncio.create_task(session.run())

    ws.feed('{"type":"wake"}')
    assert await wait_for(lambda: protocol.PHASE_LISTENING in ws.phases())
    speak_into(ws)
    assert await wait_for(lambda: protocol.PHASE_REPLYING in ws.phases(), timeout=5.0)

    audio_at_interrupt = ws.audio_bytes
    ws.feed('{"type":"interrupt"}')
    assert await wait_for(lambda: ws.phases()[-1] == protocol.PHASE_IDLE, timeout=3.0)

    await asyncio.sleep(0.6)  # the reply task would have produced more by now
    stalled = ws.audio_bytes
    ws.close()
    await asyncio.wait_for(task, timeout=3.0)
    return audio_at_interrupt, stalled, ws


def test_interrupt_stops_the_reply():
    at_interrupt, after, ws = asyncio.run(_interrupt_mid_reply())
    assert after == at_interrupt, "audio kept flowing after interrupt"
    assert ws.phases()[-1] == protocol.PHASE_IDLE


async def _no_speech_timeout():
    ws = FakeWebSocket()
    cfg = make_config(playback_prebuffer_ms=0, no_speech_timeout_s=0.3)
    session = build_session(ws, cfg=cfg)
    task = asyncio.create_task(session.run())
    ws.feed('{"type":"wake"}')
    assert await wait_for(lambda: protocol.PHASE_LISTENING in ws.phases())
    ok = await wait_for(lambda: ws.phases()[-1] == protocol.PHASE_IDLE, timeout=2.0)
    ws.close()
    await asyncio.wait_for(task, timeout=3.0)
    return ok


def test_no_speech_returns_to_idle():
    assert asyncio.run(_no_speech_timeout()), "watchdog never fired"


async def _empty_transcript():
    ws = FakeWebSocket()
    session = build_session(ws, transcriber=FakeTranscriber(text=""))
    task = asyncio.create_task(session.run())
    ws.feed('{"type":"wake"}')
    assert await wait_for(lambda: protocol.PHASE_LISTENING in ws.phases())
    speak_into(ws)
    ok = await wait_for(lambda: ws.phases()[-1] == protocol.PHASE_IDLE
                        and protocol.PHASE_THINKING in ws.phases(), timeout=4.0)
    ws.close()
    await asyncio.wait_for(task, timeout=3.0)
    return ok, ws


def test_empty_transcript_does_not_call_the_llm():
    ok, ws = asyncio.run(_empty_transcript())
    assert ok, ws.phases()
    assert protocol.PHASE_REPLYING not in ws.phases(), ws.phases()
    assert ws.audio_bytes == 0


class ExplodingHermes:
    def __init__(self):
        self.calls = 0

    async def stream(self, messages):
        self.calls += 1
        await asyncio.sleep(0)
        raise RuntimeError("hermes returned 503")
        yield  # pragma: no cover - makes this an async generator


async def _hermes_failure():
    ws = FakeWebSocket()
    hermes = ExplodingHermes()
    session = build_session(ws, hermes=hermes)
    task = asyncio.create_task(session.run())

    ws.feed('{"type":"wake"}')
    assert await wait_for(lambda: protocol.PHASE_LISTENING in ws.phases())
    speak_into(ws)
    assert await wait_for(lambda: any('"type":"error"' in m for m in ws.texts), timeout=5.0)
    assert await wait_for(lambda: ws.phases()[-1] == protocol.PHASE_IDLE, timeout=3.0)

    # the session must survive and accept another turn
    before = len(ws.phases())
    ws.feed('{"type":"wake"}')
    recovered = await wait_for(
        lambda: ws.phases()[before:].count(protocol.PHASE_LISTENING) >= 1, timeout=3.0
    )

    ws.close()
    await asyncio.wait_for(task, timeout=3.0)
    return recovered, hermes, ws


def test_llm_failure_reports_error_and_recovers():
    recovered, hermes, ws = asyncio.run(_hermes_failure())
    assert hermes.calls == 1
    assert any('"type":"error"' in m for m in ws.texts), ws.texts
    assert recovered, "session did not accept a new wake after the failure"
    assert ws.audio_bytes == 0


def test_audio_is_ignored_while_idle():
    async def scenario():
        ws = FakeWebSocket()
        session = build_session(ws)
        task = asyncio.create_task(session.run())
        await wait_for(lambda: len(ws.texts) >= 2)
        speak_into(ws)                 # no wake first
        await asyncio.sleep(0.3)
        ws.close()
        await asyncio.wait_for(task, timeout=3.0)
        return ws

    ws = asyncio.run(scenario())
    assert protocol.PHASE_THINKING not in ws.phases(), ws.phases()


if __name__ == "__main__":
    failures = []
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failures.append(name)
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(failures)} failed" if failures else "\nall session checks passed")
    sys.exit(1 if failures else 0)
