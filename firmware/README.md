# Firmware für die Voice PE

Die Stock-Firmware der Home Assistant Voice PE nutzt ESPHomes `voice_assistant`-
Komponente und spricht ausschließlich die native Home-Assistant-API. Ohne HA hat
sie keinen Gesprächspartner — deshalb muss neue Firmware auf das Gerät. Einmal
per USB, danach OTA.

Wir schreiben die Firmware nicht selbst: [xandervanerven/home-assistant-voice-pe](https://github.com/xandervanerven/home-assistant-voice-pe)
hat mit der `va_client`-Komponente bereits genau das, was wir brauchen — einen
WebSocket-Client, der Mikrofon-PCM hochstreamt und TTS-Audio abspielt. Nur das
Backend tauschen wir gegen unsere Bridge aus.

## Stand des Forks (Branch `dev`, verifiziert)

```yaml
substitutions:
  va_url: "ws://homeassistant.local:8080/"

va_client:
  id: va
  url: ${va_url}
  microphone: i2s_mics
  mic_channel: 0
  barge_in: false
  speaker: media_resampling_speaker
  on_phase: [...]
  on_repeated_failure: [...]
  on_followup_opened: [...]

micro_wake_word:
  id: mww
  models:
    - model: .../v2.1_models/alexa.json
      id: alexa
    - model: .../stop/stop.json
      id: stop
      probability_cutoff: 0.4

time:
  - platform: sntp      # gut: keine HA-Abhängigkeit für die Uhrzeit
```

## Die vier Änderungen

Forke das Repo und editiere `home-assistant-voice.realtime.yaml` direkt. Über
die ESPHome-Builder-Stubs allein geht es nicht — die können nur Substitutions
überschreiben, und zwei der vier Änderungen liegen ausserhalb davon.

### 1. Bridge-URL

```yaml
substitutions:
  va_url: "ws://192.168.1.50:8765/"   # Host der Bridge
```

Das ist die einzige Änderung, die sich auch per Substitution im Builder-Stub
erledigen lässt.

### 2. `api:` entschärfen — sonst rebootet das Gerät alle 15 Minuten

Der Fork hat weiterhin einen `api:`-Block für Home Assistant. ESPHome gibt der
API standardmässig ein `reboot_timeout` von 15 Minuten: verbindet sich in dieser
Zeit kein Client, startet das Gerät neu. Ohne HA im Netz heisst das eine
Boot-Schleife im Viertelstundentakt.

Entweder entschärfen:

```yaml
api:
  encryption:
    key: ${api_key}
  reboot_timeout: 0s        # <-- ohne das: Boot-Schleife
```

Oder den `api:`-Block ganz entfernen. Dann müssen auch die daran hängenden
Automationen weg (`on_client_connected`, `on_client_disconnected`) sowie alles,
was `homeassistant.`-Actions aufruft. `ota:` und `wifi:` bleiben — darüber
läuft das OTA-Update weiterhin.

Der schmerzfreie Weg ist `reboot_timeout: 0s`: der Block bleibt unbenutzt liegen
und stört nicht.

### 3. Wake Word

Voreingestellt ist `alexa`. Für dieses Projekt passender:

```yaml
micro_wake_word:
  models:
    - model: https://github.com/kahrendt/microWakeWord/releases/download/v2.1_models/hey_jarvis.json
      id: hey_jarvis
    - model: https://github.com/kahrendt/microWakeWord/releases/download/stop/stop.json
      id: stop
      probability_cutoff: 0.4
```

Das `stop`-Modell nicht entfernen — daraus entsteht das
`{"type":"interrupt"}`, mit dem du der Antwort ins Wort fallen kannst.

### 4. Barge-in einschalten (optional)

```yaml
va_client:
  barge_in: true
```

Dann darfst du während der Antwort reden, nicht nur „Stop" sagen. Kostet etwas
Robustheit: das Gerät hört sich in ungünstigen Räumen gelegentlich selbst.

## Secrets

`secrets.yaml` neben der YAML anlegen (Vorlage: `secrets.yaml.example` in
diesem Verzeichnis):

```yaml
wifi_ssid: "DeinWLAN"
wifi_password: "..."
ota_password: "..."
api_key: "..."            # bleibt nötig, solange der api:-Block drin ist
va_url: "ws://192.168.1.50:8765/"
```

`va_url` steht bewusst hier und nicht in `jarvis-voice.yaml`: das Repo ist
öffentlich, und die interne Netzadresse hat darin nichts verloren.

## Flashen

```bash
./patch-upstream.sh     # einmal nach dem Clone von upstream/
./validate.sh           # prüft die YAML, ohne das Gerät anzufassen
./flash.sh              # erstes Mal per USB
./flash.sh ota          # danach über WLAN
./flash.sh logs         # serielle Konsole
```

Die Skripte nutzen den `esphome/esphome`-Container. Das hat zwei Gründe: auf
NixOS gibt es kein `pip install esphome`, und `/dev/ttyACM0` gehört
`root:dialout` — der Docker-Daemon läuft als root und reicht den Port durch,
auch ohne Gruppenmitgliedschaft.

Der Bootloader muss nicht angefasst werden.

## Zurück auf die Originalfirmware

Das Gerät ist nicht verbrannt. Die Stock-Firmware liegt beim Home-Assistant-
Projekt und lässt sich per Web-Flasher über USB-C aus dem Browser
zurückspielen — kein Anlöten, kein Bootloader-Gefummel.

## Was die Firmware an die Bridge schickt

Siehe `bridge/app/protocol.py` — dort steht der vollständige Vertrag. Kurzform:

| Richtung | Frame | Inhalt |
|---|---|---|
| Gerät → Bridge | text | `{"type":"start"}`, `{"type":"wake"}`, `{"type":"interrupt"}`, `{"type":"flush"}` |
| Gerät → Bridge | binary | PCM16 mono 16 kHz |
| Bridge → Gerät | text | `hello`, `phase`, `request_follow_up`, `error` |
| Bridge → Gerät | binary | PCM16 mono 24 kHz |

Die Firmware parst Textframes per Substring-Suche, nicht mit einem JSON-Parser.
Deshalb serialisiert die Bridge kompakt und mit fester Schlüsselreihenfolge.
Ein Leerzeichen nach dem Doppelpunkt bricht die Erkennung.
