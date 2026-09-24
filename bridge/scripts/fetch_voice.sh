#!/usr/bin/env sh
# Download a Piper voice into the shared volume. Default: German, Thorsten, medium.
set -eu

VOICE="${PIPER_VOICE:-de_DE-thorsten-medium}"
DEST="${PIPER_VOICE_DIR:-/voices}"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main"

# de_DE-thorsten-medium -> de/de_DE/thorsten/medium
LOCALE="$(echo "$VOICE" | cut -d- -f1)"
LANG_DIR="$(echo "$LOCALE" | cut -d_ -f1)"
NAME="$(echo "$VOICE" | cut -d- -f2)"
QUALITY="$(echo "$VOICE" | cut -d- -f3)"
PATH_PART="$LANG_DIR/$LOCALE/$NAME/$QUALITY/$VOICE"

mkdir -p "$DEST"
if [ -f "$DEST/$VOICE.onnx" ] && [ -f "$DEST/$VOICE.onnx.json" ]; then
  echo "voice $VOICE already present in $DEST"
  exit 0
fi

echo "downloading $VOICE from rhasspy/piper-voices ..."
for suffix in onnx onnx.json; do
  curl -fSL --retry 3 -o "$DEST/$VOICE.$suffix" "$BASE/$PATH_PART.$suffix"
done
echo "voice ready at $DEST/$VOICE.onnx"
