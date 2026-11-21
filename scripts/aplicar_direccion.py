"""Aplicar tabla de dirección del usuario a los segmentos de un capítulo.

Lee un CSV con columnas: escena,turno,personaje,emocion,volumen,ritmo,intensidad
y aplica la dirección a los segmentos del capítulo importado, en orden,
por escena y turno.

Valida que el personaje del CSV coincida con el personaje detectado del
segmento; si no coincide, NO aplica esa fila y la reporta como error.

La aplicación es idempotente: correrla dos veces deja los mismos valores,
no duplica nada.

Escalas (decididas 2026-11-20, A1–A3): el CSV trae ritmo como nombre y
intensidad 1–5; se guardan normalizados: ritmo → factor de velocidad
PACE_SPEED (lento=0.75, normal=1.0, rápido=1.2) e intensidad → float 0–1
con (n-1)/4. El generador usa `pace` como velocidad: un "lento" del CSV
ya no se sintetiza 1.5× más rápido.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aurea.core.database import Database
from aurea.casting.cast_manager import CastManager
from aurea.direction.rules import (
    pause_for_punctuation, apply_emotion_pace,
    clamp_pause_after, clamp_pause_before, PACE_SPEED
)

DB_PATH = None  # se usa la ruta por defecto de Database()


def apply_direction(book_id, csv_path, chapter_idx=0):
    """Aplica la tabla de dirección a los segmentos de un capítulo.

    Para cada fila del CSV, busca el siguiente segmento de diálogo no
    dirigido cuyo personaje coincida con el personaje del CSV.

    Args:
        book_id: ID del libro en la BD.
        csv_path: Ruta al CSV con columnas escena,turno,personaje,emocion,
                  volumen,ritmo,intensidad.
        chapter_idx: Índice del capítulo dentro del libro (0 = primero).

    Returns:
        (aplicadas, errores) donde aplicadas es el número de segmentos
        dirigidos y errores es una lista de descripciones de filas
        rechazadas por desajuste de personaje.
    """
    db = Database()
    chapters = db.chapters(book_id)
    if not chapters:
        raise ValueError(f"El libro {book_id} no tiene capítulos.")
    if chapter_idx >= len(chapters):
        raise ValueError(
            f"El libro {book_id} tiene {len(chapters)} capítulos; "
            f"se pidió el índice {chapter_idx}."
        )
    chapter = chapters[chapter_idx]
    cid = chapter.id

    segs = db.segments(cid)
    if not segs:
        raise ValueError(
            f"El capítulo {cid} no tiene segmentos. Importe el capítulo primero."
        )

    # Cargar personajes del libro mapeados por nombre (case-insensitive)
    chars_by_name = {c.name.lower(): c for c in db.characters(book_id)}

    # Leer CSV
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    errores = []
    aplicadas = 0

    # Índice del siguiente segmento de diálogo no dirigido a considerar
    seg_idx = 0

    for row in rows:
        escena = row.get("escena", "").strip()
        turno = row.get("turno", "").strip()
        personaje_csv = row.get("personaje", "").strip()
        emocion = row.get("emocion", "").strip().lower()
        volumen = row.get("volumen", "").strip().lower()
        ritmo = row.get("ritmo", "").strip().lower()
        intensidad = row.get("intensidad", "").strip()

        # Buscar el siguiente segmento de diálogo no dirigido cuyo personaje
        # coincida con el personaje del CSV
        seg = None
        seg_idx_found = None
        for j in range(seg_idx, len(segs)):
            s = segs[j]
            if s.kind != "dialogue" or s.directed:
                continue
            # Verificar si el personaje coincide
            char_name = None
            if s.character_id is not None:
                for c in db.characters(book_id):
                    if c.id == s.character_id:
                        char_name = c.name.lower()
                        break
            if char_name is not None and char_name == personaje_csv.lower():
                seg = s
                seg_idx_found = j
                break

        if seg is None:
            errores.append(
                f"Fila escena={escena} turno={turno} personaje={personaje_csv}: "
                f"no se encontró segmento de diálogo sin dirigir con ese personaje."
            )
            continue

        # Aplicar dirección (escalas normalizadas, A1–A3):
        # ritmo nombre → factor de VELOCIDAD; intensidad 1–5 → 0–1.
        pace = PACE_SPEED.get(ritmo, 1.0) if ritmo else 1.0
        emotion = emocion if emocion else "neutral"
        delivery = volumen if volumen else "normal"

        try:
            intensity_raw = int(intensidad) if intensidad else 1
        except ValueError:
            intensity_raw = 1
        intensity = (min(max(intensity_raw, 1), 5) - 1) / 4.0

        # Pausa antes
        if seg.pause_before_ms is not None and seg.pause_before_ms > 0:
            pause_before = seg.pause_before_ms
        elif seg_idx_found == 0:
            pause_before = clamp_pause_before(600)
        else:
            pause_before = 0

        # Pausa después
        if seg.pause_after_ms is not None and seg.pause_after_ms > 0:
            pause_after = seg.pause_after_ms
        else:
            base = pause_for_punctuation(seg.text)
            pause_after = apply_emotion_pace(base, pace, emotion)
            pause_after = clamp_pause_after(pause_after)

            # Pregunta al final de turno de diálogo: +200 ms
            if seg.kind == "dialogue" and seg.text.rstrip().endswith("?"):
                pause_after += 200

            # Intensidad alta (>= 0.8 en escala 0–1): +300 ms después
            # (golpe dramático). Antes era `== 5` (escala CSV), que tras
            # la normalización nunca se cumplía (A2).
            if intensity >= 0.8:
                pause_after += 300

            pause_after = clamp_pause_after(pause_after)

        db.update_direction(
            seg.id,
            emotion,
            delivery,
            intensity,
            pace,
            pause_before,
            pause_after,
            directed=1,
        )
        aplicadas += 1

        # Marcar este segmento como usado para no volver a buscarlo
        seg_idx = seg_idx_found + 1

    return aplicadas, errores


def main():
    if len(sys.argv) < 3:
        print("Uso: python scripts/aplicar_direccion.py <libro_id> <archivo.csv> [capitulo_idx]")
        sys.exit(1)

    libro_id = int(sys.argv[1])
    csv_path = sys.argv[2]
    capitulo_idx = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    aplicadas, errores = apply_direction(libro_id, csv_path, capitulo_idx)

    print(f"Aplicadas: {aplicadas} segmentos dirigidos.")
    if errores:
        print(f"Errores ({len(errores)}):")
        for e in errores:
            print(f"  - {e}")
    else:
        print("Sin errores de personaje.")


if __name__ == "__main__":
    main()
