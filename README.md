# jarvis_hermes

Home Assistant Voice PE, die direkt mit einem [Hermes-Agent](https://hermes-agent.nousresearch.com)
spricht. Kein Home Assistant im Pfad, keine Cloud ausser dem LLM, das Hermes
selbst anbindet.

```
Voice PE (ESP32-S3 + XMOS)                Bridge (dieser Code)
┌────────────────────────┐
│ micro_wake_word        │
│ "Hey Jarvis"           │               ┌──────────────────────────┐
│          ↓             │  WS binary    │ WebRTC-VAD → Endpointing │
│ Mic PCM16 16 kHz   ────┼──────────────▶│          ↓               │
│                        │               │ Whisper (CPU / Intel-GPU)│
│                        │  WS text      │          ↓               │
│ LED-Ring  ◀────────────┼───────────────│ Hermes /v1/chat/...      │
│  phase-Messages        │               │   (streaming)            │
│                        │               │          ↓               │
│ Speaker   ◀────────────┼───────────────│ Piper de_DE-thorsten     │
│                        │  WS binary    │ → 24 kHz PCM             │
└────────────────────────┘               └──────────────────────────┘
```

Die Antwort wird satzweise synthetisiert und abgespielt, während Hermes noch
generiert — das erste Audio kommt, sobald der erste Satz fertig ist, nicht erst
am Ende der Antwort.

## Aufbau

| Pfad | Inhalt |
|---|---|
| [bridge/app/protocol.py](bridge/app/protocol.py) | Wire-Protokoll zur Firmware, vollständig dokumentiert |
| [bridge/app/session.py](bridge/app/session.py) | State Machine: wake → listen → think → reply → follow-up |
| [bridge/app/vad.py](bridge/app/vad.py) | Endpointing — wann ist der Satz zu Ende |
| [bridge/app/stt.py](bridge/app/stt.py) | Whisper, zwei Backends (CPU / OpenVINO) |
| [bridge/app/llm.py](bridge/app/llm.py) | Hermes-Client, SSE-Streaming |
| [bridge/app/chunker.py](bridge/app/chunker.py) | Token-Stream → sprechbare Sätze |
| [bridge/app/tts.py](bridge/app/tts.py) | Piper + Resampling auf 24 kHz |
| [firmware/README.md](firmware/README.md) | Was auf das Gerät muss und warum |

## Setup

### 1. Hermes-API freischalten

In `~/.hermes/.env` auf dem Hermes-Host:

```bash
API_SERVER_ENABLED=true
API_SERVER_KEY=dein-secret          # muss zu HERMES_KEY in .env passen
API_SERVER_PORT=7237
API_SERVER_HOST=0.0.0.0             # sonst kommt die Bridge nicht dran
```

```bash
systemctl --user restart hermes-gateway
curl http://localhost:7237/v1/health     # → {"status":"ok",...}
```

### 2. Bridge starten

Das Image baut eine GitHub Action und legt es unter
`ghcr.io/mavnezz/jarvis_hermes/bridge` ab. Auf der NAS also kein lokaler Build:

```bash
cp .env.example .env     # HERMES_KEY eintragen
docker compose pull
docker compose up -d
docker compose logs -f
```

Lokal bauen geht weiterhin mit `docker compose up -d --build`.

| Tag | Inhalt |
|---|---|
| `latest` | CPU-Variante, faster-whisper. Der Default |
| `latest-openvino` | zusätzlich OpenVINO und `intel-opencl-icd` für die Intel-iGPU |

Der erste Start lädt das Whisper-Modell und die Piper-Stimme
(`de_DE-thorsten-medium`) herunter; beides landet in Named Volumes und
überlebt Rebuilds. Erwartete Logzeile:

```
listening on ws://0.0.0.0:8765
hermes reachable at http://host.docker.internal:7237/v1
```

#### Gemessen: welche Kombination wie schnell ist

Alle Werte auf derselben deutschen Aeusserung (~3 s), i5-8500T mit UHD 630:

| Modell | Backend | Geraet | Zeit |
|---|---|---|---|
| small | faster-whisper | CPU | **2,5 s** |
| small | OpenVINO | GPU | 3,7 s |
| large-v3-turbo | faster-whisper | CPU | 8,5 s |
| large-v3-turbo | OpenVINO | GPU | 15,0 s |
| large-v3-turbo | OpenVINO | CPU | 16,8 s |
| medium | faster-whisper | CPU | 13,8 s |

Zwei Dinge daran sind kontraintuitiv genug, um sie festzuhalten.

**Die iGPU hilft nicht.** Sie war in jeder Paarung langsamer als die CPU. Eine
integrierte GPU teilt sich den Speicherbus mit der CPU, und Whisper haengt
genau daran — nicht an Rechenwerken. `docker-compose.gpu.yml` bleibt fuer
kraeftigere Hardware liegen, ist hier aber die schlechtere Wahl.

**Kurze Fragen sind nicht schneller.** Whisper arbeitet immer auf einem
30-Sekunden-Fenster; eine 2-Sekunden-Frage kostet den Encoder so viel wie eine
25-Sekunden-Frage. "Mal Echtzeit" ist fuer diesen Anwendungsfall die falsche
Kennzahl — es zaehlt die absolute Zeit pro Turn, und die ist konstant.

Damit ist die Modellgroesse der einzige wirksame Hebel. `small` verhoert sich
gelegentlich bei Wortendungen ("Nennen wir" statt "Nenn mir"); fuer einen
Assistenten, dessen Antwort ohnehin ein Sprachmodell formuliert, ist das meist
verkraftbar. Ein Kommandoparser waere hier die falsche Architektur.

### 3. Firmware flashen

Siehe [firmware/README.md](firmware/README.md). Kurz: Fork von
xandervanervens `va_client`-Firmware, `va_url` auf die Bridge zeigen,
`reboot_timeout: 0s` setzen, Wake Word auf `hey_jarvis`, einmal per USB
flashen.

## Tests

Laufen ohne GPU, ohne Modelle und ohne Gerät — die externen Abhängigkeiten sind
gestubbt. Deshalb laufen sie in CI auch ohne `pip install`, als Gate vor jedem
Image-Build:

```bash
python3 bridge/tests/test_offline.py    # Protokoll-Literale, Satzsegmentierung
python3 bridge/tests/test_session.py    # Endpointing, State Machine, Barge-in
```

## Konfiguration

Alles über Env-Variablen, siehe [.env.example](.env.example) und
[bridge/app/config.py](bridge/app/config.py). Die Knöpfe, an denen man
tatsächlich dreht:

| Variable | Default | Wirkung |
|---|---|---|
| `VAD_MIN_SILENCE_MS` | 700 | Wie lange Stille, bevor die Bridge dich für fertig hält. Runter = reaktiver, aber schneidet dir ins Wort |
| `VAD_AGGRESSIVENESS` | 2 | 0–3. Höher = weniger Fehlauslösung durch Nebengeräusche |
| `PLAYBACK_PREBUFFER_MS` | 400 | Jitter-Polster auf dem Gerät. Runter = schnellerer Start, rauf = weniger Aussetzer im schwachen WLAN |
| `FOLLOW_UP_MS` | 6000 | Wie lange das Mikrofon nach der Antwort für eine Rückfrage offen bleibt |
| `WHISPER_MODEL` | deutsches Turbo-ct2 | Der grösste Latenzhebel. Wenn es klemmt: erst die iGPU, dann `medium`, dann `small` — die kleinen sind im Deutschen merklich schwächer |
| `WHISPER_BEAM` | 1 | Auf 5 für etwas bessere Transkription bei mehr Latenz |
| `HISTORY_TURNS` | 8 | Gesprächsverlauf, den Hermes mitbekommt |

## Designentscheidungen

**Endpointing mit WebRTC statt Silero.** Die Voice PE hat einen XMOS XU316 als
Audio-Frontend — Echokompensation, Beamforming, Rauschunterdrückung passieren
auf dem Gerät. Was bei der Bridge ankommt, ist bereits sauber, und WebRTC-VAD
reicht dafür. Spart ein 2-GB-Torch-Image. Wenn dein Raum sich als härter
erweist, ist in [vad.py](bridge/app/vad.py) genau eine Methode zu ersetzen:
`Endpointer._is_voiced`.

**Satzweise TTS.** Hermes ist eine Chat-Completion, kein Speech-to-Speech. Die
Gesamtlatenz lässt sich nicht wegzaubern, aber die *wahrgenommene* schon: wer
nach 900 ms den ersten Satz hört, wartet gefühlt nicht. Der Chunker ist dabei
deutschspezifisch — „21.5", „1. Januar" und „z. B." sind keine Satzenden.

**TTS bleibt auf der CPU.** Die deutsche Thorsten-Stimme rendert dort schneller
als Echtzeit — eine Beschleunigung würde nichts bringen, die Zeit geht woanders
hin.

**STT-Backend ist austauschbar.** Ziel ist eine NAS mit Intel-CPU und iGPU,
nicht die ursprünglich angenommene NVIDIA-Karte. CTranslate2 (faster-whisper)
kann mit einer Intel-iGPU nichts anfangen, OpenVINO schon — deshalb zwei
Backends hinter einer gemeinsamen Schnittstelle in
[stt.py](bridge/app/stt.py), CPU als portabler Default.

## Bekannte Grenzen

- **Ein Gerät pro Bridge-Instanz** ist getestet. Mehrere Geräte bekommen je
  eine eigene Session mit eigenem Verlauf, teilen sich aber Whisper-Instanz und
  GPU — parallele Anfragen serialisieren sich dort.
- **Kein Timer, kein Wecker.** Die Stock-Firmware kann das über HA; hier fehlt
  es, weil es Zustand auf dem Gerät braucht.
- **Kein Hausgerätezugriff.** Hermes weiss nichts von deinen Lampen. Wenn das
  dazu soll, ist der Weg Tool-Calling in Hermes, nicht die Bridge.
- **Der Gesprächsverlauf lebt im RAM** und ist nach einem Neustart der Bridge
  weg.
