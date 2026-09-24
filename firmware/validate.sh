#!/usr/bin/env bash
# Prüft die YAML, ohne Gerät und ohne Platzhalter-Check — fängt Fehler aus
# patch-upstream.sh ab, bevor geflasht wird.
set -euo pipefail
cd "$(dirname "$0")"
exec docker run --rm --network host -v "$PWD":/config -w /config \
  esphome/esphome:latest config jarvis-voice.yaml
