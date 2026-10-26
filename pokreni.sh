#!/bin/zsh
# Dvoklik na ovaj fajl pokreće generator glasa.
cd "$(dirname "$0")"

PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Nema python3. Instaliraj ga (xcode-select --install ili brew install python3)."
  read -k 1 "?Pritisni bilo dugme..."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo ">> Pravim virtualno okruzenje (prvi put)..."
  "$PY" -m venv .venv || { echo "GRESKA pri pravljenju .venv"; read -k 1 "?Pritisni bilo dugme..."; exit 1; }
  echo ">> Instaliram edge-tts (Microsoft neural glasovi)..."
  .venv/bin/pip install --quiet edge-tts || echo ">> edge-tts nije instaliran, koristim rezervni Google glas."
fi

exec .venv/bin/python server.py
