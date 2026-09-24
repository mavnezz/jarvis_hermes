#!/usr/bin/env bash
# Baut und flasht die Firmware über den ESPHome-Container.
#
# Warum Docker: esphome ist hier nicht installiert (NixOS ohne pip), und
# /dev/ttyACM0 gehört root:dialout — dieser User ist nicht in der Gruppe.
# Der Docker-Daemon läuft als root, --device reicht den Port trotzdem durch.
#
#   ./flash.sh            compile + upload über USB (/dev/ttyACM0)
#   ./flash.sh ota        compile + upload über WLAN, kein Kabel nötig
#   ./flash.sh logs       serielle Konsole mitlesen (USB)
#   ./flash.sh compile    nur bauen, nichts aufs Gerät
set -euo pipefail
cd "$(dirname "$0")"

DEV="${DEVICE:-/dev/ttyACM0}"
OTA_HOST="${OTA_HOST:-jarvis-voice.local}"
IMAGE="esphome/esphome:latest"
MODE="${1:-flash}"

grep -q 'BRIDGE_IP_HIER' jarvis-voice.yaml && { echo "va_url ist noch ein Platzhalter"; exit 1; }
grep -q 'PLACEHOLDER_WLAN_PASSWORT' secrets.yaml && { echo "wifi_password ist noch ein Platzhalter"; exit 1; }

TTY_FLAGS=""
[ -t 0 ] && TTY_FLAGS="-it"

esphome() {
  local need_dev="$1"; shift
  local dev_flag=()
  if [ "$need_dev" = "yes" ]; then
    [ -e "$DEV" ] || { echo "Gerät $DEV nicht gefunden — angeschlossen?"; exit 1; }
    dev_flag=(--device="$DEV")
  fi
  docker run --rm $TTY_FLAGS "${dev_flag[@]}" \
    --network host -v "$PWD":/config -w /config "$IMAGE" "$@"
}

case "$MODE" in
  compile) esphome no  compile jarvis-voice.yaml ;;
  ota)
    echo "### 1/2 compile"
    esphome no compile jarvis-voice.yaml
    echo "### 2/2 upload -> $OTA_HOST (OTA)"
    esphome no upload jarvis-voice.yaml --device "$OTA_HOST"
    echo "### fertig"
    ;;
  logs)    esphome yes logs    jarvis-voice.yaml --device "$DEV" ;;
  flash)
    echo "### 1/2 compile"
    esphome no compile jarvis-voice.yaml
    echo "### 2/2 upload -> $DEV"
    esphome yes upload jarvis-voice.yaml --device "$DEV"
    echo "### fertig"
    ;;
  *) echo "unbekannter Modus: $MODE"; exit 1 ;;
esac
