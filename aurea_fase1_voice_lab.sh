#!/usr/bin/env bash
# =============================================================
#  AUREA Audiobook Studio · Fase 1 — AUREA Voice Lab
#  Protocolo Nova-Aegis · Entorno aislado
# =============================================================
#  Qué hace:
#    1. Crea un entorno virtual aislado en ~/aurea/.venv
#    2. Instala kokoro-onnx, soundfile y misaki (SOLO en el venv)
#    3. Descarga los modelos Kokoro (fp16 e int8) y las voces
#    4. Corre 6 pruebas narrativas en español (más 1 comparativa)
#    5. Mide tiempo de generación, RTF y RAM de cada prueba
#
#  Qué toca: SOLO lo que está dentro de ~/aurea/
#  No instala nada a nivel de sistema. No usa sudo.
#  Necesita Internet una sola vez (pip + descargas).
#  Después de eso, la generación de voz funciona sin conexión.
#
#  Uso:  bash aurea_fase1_voice_lab.sh
# =============================================================

set -u

AUREA="$HOME/aurea"
VENV="$AUREA/.venv"
MODELS="$AUREA/models"
RESULTS="$AUREA/results/$(date '+%Y-%m-%d_%H%M')"
BASE_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"

say()  { printf '\033[1;36m[AUREA]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[AUREA]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[AUREA]\033[0m %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "python3 no encontrado"
command -v curl    >/dev/null 2>&1 || die "curl no encontrado"
python3 -c 'import venv' 2>/dev/null || die "venv no disponible en el sistema"
command -v espeak-ng >/dev/null 2>&1 || warn "espeak-ng no encontrado en PATH (el G2P en español podría fallar)"

FREE_MB=$(df -m "$HOME" | awk 'NR==2 {print $4}')
[ "${FREE_MB:-0}" -lt 1500 ] && die "Hacen falta ~700 MB libres para los modelos (tienes ${FREE_MB} MB)"

# ---------- 1. Estructura ----------
mkdir -p "$MODELS" "$RESULTS"
say "Carpeta del proyecto: $AUREA"

# ---------- 2. Entorno virtual ----------
if [ ! -f "$VENV/bin/activate" ]; then
    say "Creando entorno virtual aislado (~/aurea/.venv)..."
    python3 -m venv "$VENV" || die "No se pudo crear el venv"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
say "Python del laboratorio: $(python --version 2>&1)"
python -m pip install --quiet --upgrade pip || warn "No se pudo actualizar pip (continuando)"

# ---------- 3. Dependencias (solo dentro del venv) ----------
say "Instalando dependencias (kokoro-onnx, soundfile, misaki, onnxruntime)..."
python -m pip install --quiet kokoro-onnx soundfile "misaki-fork[en]" espeakng-loader || {
    warn "La instalación con Python $(python --version 2>&1) falló."
    warn "Plan B (el recomendado por el propio proyecto kokoro-onnx): Python 3.12 con uv"
    warn "   1) curl -LsSf https://astral.sh/uv/install.sh | sh"
    warn "   2) cd $AUREA && uv venv --seed -p 3.12 && source .venv/bin/activate"
    warn "   3) uv pip install -U kokoro-onnx soundfile 'misaki-fork[en]' espeakng-loader"
    warn "   4) Vuelve a correr este script"
    die "Instalación interrumpida. No se rompió nada del sistema."
}

# ---------- 4. Modelos ----------
download() {
    local file="$1"
    local dest="$MODELS/$file"
    if [ -s "$dest" ] && [ "$(stat -c%s "$dest" 2>/dev/null || echo 0)" -gt 1000000 ]; then
        say "Ya existe: $file (se reutiliza)"
        return 0
    fi
    say "Descargando $file ..."
    curl -L --fail -C - --retry 3 -o "$dest" "$BASE_URL/$file" \
        || { warn "No se pudo descargar $file — se continúa sin él"; rm -f "$dest"; return 1; }
}

FP16=""; INT8=""; VOICES=""
download "voices-v1.0.bin"       && VOICES="$MODELS/voices-v1.0.bin"
download "kokoro-v1.0.fp16.onnx" && FP16="$MODELS/kokoro-v1.0.fp16.onnx"
download "kokoro-v1.0.int8.onnx" && INT8="$MODELS/kokoro-v1.0.int8.onnx"

[ -n "$VOICES" ] || die "Sin el banco de voces no hay laboratorio. Revisa tu conexión."
[ -n "$FP16$INT8" ] || die "No se descargó ningún modelo. Revisa tu conexión y reintenta."

# ---------- 5. Pruebas narrativas ----------
say "Corriendo pruebas de voz (en CPU esto toma varios minutos; es normal)..."
python - "$RESULTS" "$VOICES" "$FP16" "$INT8" <<'PY'
import gc
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf
from misaki import espeak
from misaki.espeak import EspeakG2P
from kokoro_onnx import Kokoro

results_dir = Path(sys.argv[1])
voices_path = Path(sys.argv[2])
fp16_path = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else None
int8_path = Path(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else None

if not (fp16_path or int8_path):
    sys.exit("No hay modelos disponibles; no se puede continuar.")

audio_dir = results_dir / "audio"
audio_dir.mkdir(parents=True, exist_ok=True)

# G2P en español (usa espeak-ng, ya presente en el sistema)
g2p = EspeakG2P(language="es")
_ = espeak.EspeakFallback(british=False)


def rss_mb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS"):
                return round(int(line.split()[1]) / 1024, 1)
    return -1


TESTS = [
    ("01_neutra", "em_alex", 1.0,
     "La lluvia comenzaba a caer sobre el pueblo cuando Elena cerró la puerta "
     "de su casa por última vez. Nadie la vio partir."),
    ("02_misterio", "em_alex", 0.85,
     "Al fondo del pasillo, algo se movió entre las sombras. "
     "No debería estar ahí. Nada debería estar ahí."),
    ("03_enfado", "em_santa", 1.1,
     "¡Te lo dije mil veces! ¡No debiste abrir esa puerta sin saber "
     "lo que había detrás!"),
    ("04_susurro", "ef_dora", 0.8,
     "Escucha con atención... ¿Oíste eso? Viene del sótano, "
     "y no deberíamos quedarnos aquí."),
    ("05_triste", "em_santa", 0.8,
     "Guardé su carta en el cajón, junto a todas las cosas "
     "que ya no puedo decirle."),
]

DIALOGO = [
    ("A", "em_alex", 0.9, "No deberías haber venido aquí. Hay cosas que es mejor dejar enterradas."),
    ("B", "ef_dora", 1.0, "Tuve que hacerlo. No quedaba nadie más que pudiera venir."),
]

rows = []


def run_case(kokoro, tag, model_name, voice, speed, text):
    try:
        phonemes, _ = g2p(text)
        t0 = time.perf_counter()
        samples, sr = kokoro.create(phonemes, voice, speed=speed, is_phonemes=True)
        gen = time.perf_counter() - t0
    except Exception as e:
        print(f"  [FALLO] {tag}: {e}")
        rows.append({"prueba": tag, "modelo": model_name, "voz": voice,
                     "velocidad": speed, "error": str(e)})
        return None
    sf.write(str(audio_dir / f"{tag}.wav"), samples, sr)
    dur = len(samples) / sr
    rtf = gen / dur if dur else -1
    rows.append({"prueba": tag, "modelo": model_name, "voz": voice, "velocidad": speed,
                 "duracion_audio_s": round(dur, 2), "generacion_s": round(gen, 2),
                 "rtf": round(rtf, 2), "ram_mb": rss_mb(), "wav": f"{tag}.wav"})
    print(f"  [OK] {tag} ({voice}, vel {speed}): {dur:.1f}s de audio en {gen:.1f}s "
          f"(RTF {rtf:.2f}) — RAM {rss_mb()} MB")
    return samples, sr


if fp16_path and fp16_path.exists():
    print("== Motor: kokoro-v1.0.fp16 (calidad de referencia) ==")
    t0 = time.perf_counter()
    kokoro = Kokoro(str(fp16_path), str(voices_path))
    print(f"  Modelo cargado en {time.perf_counter() - t0:.1f}s")
    for tag, voice, speed, text in TESTS:
        run_case(kokoro, tag, "fp16", voice, speed, text)
    parts, sr = [], 24000
    for who, voice, speed, text in DIALOGO:
        r = run_case(kokoro, f"06_dialogo_{who}", "fp16", voice, speed, text)
        if r:
            parts.append(r[0])
            sr = r[1]
    if len(parts) == 2:
        pause = np.zeros(int(sr * 0.5))  # pausa dramática de medio segundo
        sf.write(str(audio_dir / "06_dialogo_completo.wav"),
                 np.concatenate([parts[0], pause, parts[1]]), sr)
        print("  [OK] 06_dialogo_completo.wav (dos voces + pausa dramática)")
    del kokoro
    gc.collect()

if int8_path and int8_path.exists():
    print("== Motor: kokoro-v1.0.int8 (candidato ligero) ==")
    t0 = time.perf_counter()
    kokoro = Kokoro(str(int8_path), str(voices_path))
    print(f"  Modelo cargado en {time.perf_counter() - t0:.1f}s")
    run_case(kokoro, "07_comparativa_int8", "int8", "em_alex", 1.0, TESTS[0][3])
    del kokoro
    gc.collect()

# ---------- Informe ----------
ok = [r for r in rows if "error" not in r]
errores = [r for r in rows if "error" in r]
err_txt = "\n".join(f"- {r['prueba']}: {r['error']}" for r in errores) if errores else "*(ninguno)*"

lines = [
    "# AUREA Voice Lab — Resultados",
    f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    "",
    "| Prueba | Modelo | Voz | Vel. | Audio (s) | Gen (s) | RTF | RAM (MB) |",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
]
for r in ok:
    lines.append(f"| {r['prueba']} | {r['modelo']} | {r['voz']} | {r['velocidad']} | "
                  f"{r['duracion_audio_s']} | {r['generacion_s']} | {r['rtf']} | {r['ram_mb']} |")
lines += [
    "",
    "RTF < 1.0 = se genera más rápido de lo que dura el audio (ideal para producción por lotes).",
    "",
    "Nota honesta: Kokoro no \"actúa\" emocionalmente; estas pruebas varían voz y velocidad",
    "para escuchar cuánto matiz se logra en la línea base. La interpretación verdadera",
    "llegará con el Director Narrativo, las pausas y la mezcla (Fases 3-5).",
    "",
    "## Errores",
    err_txt,
    "",
    "---",
    "Escucha los WAV en audio/ y anota: ¿qué voz suena mejor como narrador?",
    "Compara 01_neutra (fp16) contra 07_comparativa_int8 para decidir el modelo diario.",
]
(results_dir / "informe.md").write_text("\n".join(lines), encoding="utf-8")
(results_dir / "results.json").write_text(
    json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nInforme: {results_dir / 'informe.md'}")
PY

say "Pruebas terminadas."
echo ""
echo "============================================================"
say "Resultados en: $RESULTS"
echo "   audio/*.wav   → escucha cada prueba"
echo "   informe.md    → tabla comparativa (tiempos, RTF, RAM)"
echo "   results.json  → datos crudos para comparar después"
echo "============================================================"
