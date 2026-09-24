#!/usr/bin/env bash
# Die zwei Änderungen an der geklonten Upstream-YAML, die sich NICHT über
# Substitutions erledigen lassen. Idempotent — nach einem frischen Clone
# einfach erneut ausführen.
set -euo pipefail

YAML="$(dirname "$0")/upstream/home-assistant-voice.realtime.yaml"
[ -f "$YAML" ] || { echo "upstream nicht geklont: $YAML"; exit 1; }

# 1) Ohne Home Assistant verbindet sich nie ein API-Client. ESPHome rebootet das
#    Gerät dann alle 15 Minuten (reboot_timeout-Default). 0s schaltet das ab.
if grep -q 'reboot_timeout' "$YAML"; then
  echo "[1/2] reboot_timeout bereits gesetzt"
else
  python3 - "$YAML" <<'PYEOF'
import sys, re
path = sys.argv[1]
src = open(path, encoding="utf-8").read()
needle = "api:\n  encryption:\n    key: ${api_key}\n"
assert needle in src, "api-Block sieht anders aus als erwartet"
patch = (
    "api:\n"
    "  # jarvis_hermes: ohne Home Assistant verbindet sich nie ein API-Client und\n"
    "  # ESPHome rebootet nach reboot_timeout (Default 15min) — eine Boot-Schleife.\n"
    "  reboot_timeout: 0s\n"
    "  encryption:\n"
    "    key: ${api_key}\n"
)
open(path, "w", encoding="utf-8").write(src.replace(needle, patch, 1))
print("[1/2] reboot_timeout: 0s gesetzt")
PYEOF
fi

# 2) Wake Word: alexa -> hey_jarvis. Das "stop"-Modell bleibt, daraus entsteht
#    das {"type":"interrupt"} zum Reinreden.
#
#    Achtung bei der Quelle: das kahrendt-Release v2.1_models enthaelt NUR das
#    alexa-Modell. Die Stock-Wake-Words liegen bei esphome/micro-wake-word-models
#    (dieselbe Quelle wie das vad.json weiter unten in der Datei).
OLD_URL='https://github.com/kahrendt/microWakeWord/releases/download/v2.1_models/alexa.json'
NEW_URL='https://github.com/esphome/micro-wake-word-models/raw/main/models/v2/hey_jarvis.json'

if grep -q "$NEW_URL" "$YAML"; then
  echo "[2/2] Wake Word bereits hey_jarvis"
else
  # Reihenfolge zaehlt: erst die ganze URL tauschen, dann den Token. Andersherum
  # wuerde das globale s/alexa/hey_jarvis/ die URL zu .../v2.1_models/hey_jarvis.json
  # machen -- die es dort nicht gibt (404 beim Validieren).
  sed -i "s#${OLD_URL}#${NEW_URL}#" "$YAML"
  # Die Model-ID wird auch in den Sensitivity-Lambdas benutzt (id(alexa)
  # .set_probability_cutoff). Wer nur die URL tauscht, bekommt einen
  # Compile-Fehler. Kommentare gleich mit, damit die Datei nicht luegt.
  sed -i 's#\balexa\b#hey_jarvis#g' "$YAML"
  echo "[2/2] Wake Word auf hey_jarvis umgestellt (Modell, ID, Lambdas)"
fi
