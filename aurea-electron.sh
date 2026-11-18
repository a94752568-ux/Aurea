#!/usr/bin/env bash
# Arranca AUREA Studio (Electron): levanta el servidor Python + la ventana.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/desktop"
exec npm start
