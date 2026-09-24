"""Checks that need no model, no GPU and no device. Run: python bridge/tests/test_offline.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import protocol
from app.chunker import SentenceChunker


def stream(tokens):
    chunker = SentenceChunker()
    out = []
    for token in tokens:
        out.extend(chunker.push(token))
    tail = chunker.flush()
    if tail:
        out.append(tail)
    return out


def test_protocol_literals():
    hello = protocol.hello(
        follow_up_ms=6000, follow_up_open_delay_ms=250,
        wake_open_delay_ms=0, playback_prebuffer_ms=400,
    )
    # the firmware substring-matches; spaces after the colon would break it
    assert '"type":"hello"' in hello
    assert ": " not in hello
    for key in ("follow_up_ms", "follow_up_open_delay_ms",
                "wake_open_delay_ms", "playback_prebuffer_ms"):
        assert f'"{key}"' in hello
    # the device scans digits after '"follow_up_ms"' — that key must be unique
    assert hello.count('"follow_up_ms"') == 1
    # and must not be mistaken for the request_follow_up message
    assert '"type":"request_follow_up"' not in hello
    assert '"follow_up_ms"' not in protocol.request_follow_up()

    for phase in ("idle", "listening", "thinking", "replying"):
        msg = protocol.phase(phase)
        assert '"type":"phase"' in msg and f'"value":"{phase}"' in msg

    assert protocol.parse_device_message('{"type":"wake"}') == "wake"
    assert protocol.parse_device_message("not json") is None


def test_chunk_pcm_is_sample_aligned():
    frames = list(protocol.chunk_pcm(b"\x00" * 4000, 40, protocol.TTS_SAMPLE_RATE))
    assert sum(len(f) for f in frames) == 4000
    assert all(len(f) % 2 == 0 for f in frames)
    assert len(frames[0]) == 1920  # 40 ms @ 24 kHz, int16


def test_short_first_chunk_goes_out_early():
    out = stream(["Klar", ".", " Das", " Licht", " im", " Wohnzimmer", " ist",
                  " jetzt", " an", ",", " und", " die", " Heizung", " läuft",
                  " auf", " zwanzig", " Grad", "."])
    assert out[0] == "Klar.", out
    assert len(out) == 2, out


def test_decimal_is_not_a_sentence_end():
    out = stream(["Es", " sind", " 21", ".", "5", " Grad", " draußen", "."])
    assert out == ["Es sind 21.5 Grad draußen."], out


def test_ordinal_is_not_a_sentence_end():
    out = stream(["Der", " Termin", " ist", " am", " 1", ".", " Januar",
                  " um", " acht", " Uhr", "."])
    assert out == ["Der Termin ist am 1. Januar um acht Uhr."], out


def test_abbreviation_is_not_a_sentence_end():
    out = stream(["Nimm", " z", ".B", ".", " die", " Lampe", " bzw", ".",
                  " den", " Stehlampen", "schalter", "."])
    assert len(out) == 1, out
    assert out[0].endswith("schalter."), out


def test_end_of_buffer_is_not_a_boundary_until_flush():
    chunker = SentenceChunker()
    assert chunker.push("Es sind 21") == []
    assert chunker.push(".") == []          # could still be a decimal
    assert chunker.push("5 Grad") == []
    assert chunker.flush() == "Es sind 21.5 Grad"


def test_long_run_without_punctuation_is_cut():
    tokens = ["wort "] * 80  # 400 chars, no sentence end
    out = stream(tokens)
    assert len(out) >= 2, len(out)
    assert all(len(c) <= 300 for c in out)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {name}: {exc}")
    print("FAILED" if failures else "\nall offline checks passed")
    sys.exit(1 if failures else 0)
