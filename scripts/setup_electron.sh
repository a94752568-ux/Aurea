#!/usr/bin/env bash
# Setup del refactor Electron de AUREA (rama electron-refactor).
# No toca el sistema ni los modelos existentes; solo instala Electron en desktop/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "→ Verificando Node.js / npm"
if ! command -v npm >/dev/null 2>&1; then
  echo "Falta npm (Node 18+). Instálalo con nvm o: sudo apt install nodejs npm"
  exit 1
fi
node --version

echo "→ Instalando dependencias de escritorio (Electron) en $ROOT/desktop"
(cd "$ROOT/desktop" && npm install)

PYTHON="${AUREA_PYTHON:-$ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then PYTHON="python3"; fi
echo "→ Verificando dependencias del motor Python con: $PYTHON"
"$PYTHON" - <<'EOF'
import sys
sys.path.insert(0, ".")
import numpy, soundfile  # noqa
import kokoro_onnx       # noqa
import aurea.server      # noqa: el servidor importa sin Qt
print("OK: servidor AUREA importable, sin PySide6")
EOF

echo ""
echo "Listo. Arranca con:  bash aurea-electron.sh"
