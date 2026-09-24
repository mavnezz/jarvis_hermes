#!/usr/bin/env sh
set -eu
/app/scripts/fetch_voice.sh
exec python -m app
