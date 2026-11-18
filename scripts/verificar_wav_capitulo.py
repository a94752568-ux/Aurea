"""Verificación de generación de capítulo con audio real.

Comprueba:
  1. Que se puede generar un capítulo dirigido (WAV final unificado).
  2. Que la duración del WAV ≈ suma de segmentos + suma de pausas.
  3. Que no hay clipping tras process_master (peak <= 0.95).
  4. Que ningún segmento sintetizado contiene '['.

Uso:
    python scripts/verificar_wav_capitulo.py [libro_id] [capitulo_idx]
"""
import sys
import os
import re
import time
import argparse
from pathlib import Path

sys.path.insert(0, "/home/aegis2/aurea")

import numpy as np
import soundfile as sf

from aurea.core.database import Database
from aurea.core.generator import ChapterGenerator
from aurea.core.models import Chapter
from aurea.direction.director import DirectionWorker
from PySide6.QtCore import QCoreApplication, QEventLoop

DB_PATH = "/home/aegis2/aurea/aurea.db"
FAILURES = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def main():
    parser = argparse.ArgumentParser(description="Verificar generación de capítulo con audio real")
    parser.add_argument("libro_id", type=int, nargs="?", default=20, help="ID del libro")
    parser.add_argument("capitulo_idx", type=int, nargs="?", default=0, help="Índice del capítulo")
    parser.add_argument("--force", action="store_true", help="Forzar regeneración de todo el audio")
    args = parser.parse_args()

    book_id = args.libro_id
    cap_idx = args.capitulo_idx
    force = args.force
    db = Database()

    chapters = db.chapters(book_id)
    chapter = next((c for c in chapters if c.idx == cap_idx), None)
    if not chapter:
        print(f"No se encontró capítulo {cap_idx} en libro {book_id}")
        sys.exit(1)

    cid = chapter.id
    print(f"Libro {book_id}, capítulo {cap_idx} (id={cid}): {chapter.title}")

    # --- Re-dirigir el capítulo si es necesario ---
    segs = db.segments(cid)
    if not all(s.directed for s in segs):
        print("Aplicando dirección...")
        w = DirectionWorker(cid, use_llm=False)
        loop = QEventLoop()
        w.finished_ok.connect(lambda r, l: loop.quit())
        w.failed.connect(lambda e: (print("ERROR dirección:", e), loop.quit()))
        w.start()
        loop.exec()
        segs = db.segments(cid)

    print(f"  Segmentos: {len(segs)}, dirigidos: {sum(1 for s in segs if s.directed)}")
    total_pause_ms = sum(s.pause_after_ms or 0 for s in segs)
    print(f"  Suma de pausas: {total_pause_ms/1000:.1f}s")

    # --- Generar audio ---
    print("Generando capítulo...")
    t0 = time.perf_counter()
    gen = ChapterGenerator(chapter, "em_alex", 1.0, force=force)
    resultado = []

    loop = QEventLoop()
    gen.finished_ok.connect(lambda wav_msg: (resultado.append(wav_msg), loop.quit()))
    gen.failed.connect(lambda e: (resultado.append(str(e)), loop.quit()))
    gen.start()
    loop.exec()
    dt_gen = time.perf_counter() - t0

    msg = resultado[0] if resultado else ""
    print(f"  Tiempo generación: {dt_gen:.1f}s")

    # Extraer ruta del mensaje: "✔ Cap 000 listo → /path.wav"
    match = re.search(r"→\s*(.+)", msg)
    wav_path = match.group(1).strip() if match else None

    if not wav_path or not Path(wav_path).exists():
        print(f"  FALLO al generar: {msg}")
        sys.exit(1)

    print(f"  WAV generado: {wav_path}")

    # --- Verificar duración ---
    info = sf.info(wav_path)
    duracion_real = info.duration
    print(f"  WAV: {info.duration:.1f}s, SR={info.samplerate}, canales={info.channels}")

    # Volver a leer segmentos de la BD (después de la generación)
    segs = db.segments(cid)
    dur_segmentos = 0.0
    for s in segs:
        if s.wav_path and Path(s.wav_path).exists():
            d, sr = sf.read(s.wav_path)
            dur_segmentos += len(d) / sr

    dur_esperada = dur_segmentos + total_pause_ms / 1000.0
    print(f"  Duración segmentos: {dur_segmentos:.1f}s")
    print(f"  Duración esperada (segmentos + pausas): {dur_esperada:.1f}s")
    print(f"  Duración real WAV: {duracion_real:.1f}s")

    diff = abs(duracion_real - dur_esperada)
    check("Duración WAV ≈ segmentos + pausas (±0.5s)", diff < 0.5,
          f"diff={diff:.2f}s (esperado {dur_esperada:.1f}s, real {duracion_real:.1f}s)")

    # --- Verificar no clipping ---
    data, sr = sf.read(wav_path)
    peak = np.max(np.abs(data))
    check("Sin clipping (peak <= 0.95)", peak <= 0.95,
          f"peak={peak:.4f}")

    # --- Verificar que ningún segmento tiene '[' ---
    bracket_segs = [s for s in segs if '[' in (s.text or '')]
    check("Ningún segmento con '[' en texto", len(bracket_segs) == 0,
          f"{len(bracket_segs)} segmentos con corchetes")

    # --- Verificar pause_after_ms no NULL ---
    all_not_null = all(s.pause_after_ms is not None for s in segs)
    check("Todos los segmentos con pause_after_ms definido", all_not_null)

    # --- Resumen ---
    print()
    if FAILURES:
        print(f"FALLOS ({len(FAILURES)}): {FAILURES}")
        sys.exit(1)
    else:
        print("TODAS LAS PRUEBAS DE WAV CAPÍTULO PASARON")
        sys.exit(0)


if __name__ == "__main__":
    main()
