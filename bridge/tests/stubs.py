"""Minimal stand-ins for the heavy runtime deps, so the state machine can be
tested without a GPU, a model download or the device."""

import sys
import types


def _module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


class ScriptedVad:
    """Frames of all-zero bytes count as silence, anything else as speech."""

    def __init__(self, aggressiveness=2):
        self.aggressiveness = aggressiveness

    def is_speech(self, frame, sample_rate):
        return any(frame)


def install():
    if "webrtcvad" not in sys.modules:
        _module("webrtcvad", Vad=ScriptedVad)

    if "numpy" not in sys.modules:
        _module(
            "numpy",
            frombuffer=lambda *a, **k: [],
            asarray=lambda *a, **k: [],
            int16="int16",
            float32="float32",
        )

    if "soxr" not in sys.modules:
        _module("soxr", ResampleStream=object)

    if "faster_whisper" not in sys.modules:
        _module("faster_whisper", WhisperModel=object)

    if "httpx" not in sys.modules:
        _module(
            "httpx",
            AsyncClient=object,
            Timeout=lambda *a, **k: None,
            HTTPError=Exception,
        )

    if "piper" not in sys.modules:
        _module("piper", PiperVoice=object, SynthesisConfig=object)
