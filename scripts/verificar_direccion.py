"""Verificación de la aplicación de dirección (scripts/aplicar_direccion.py).

Casos de prueba (todos deben pasar):
1. Aplicar direccion-larga.csv a un capítulo de prueba: rellena los segmentos
   con emoción, entrega, intensidad, ritmo y pausas.
2. Un CSV con un personaje que no coincide se rechaza y se reporta.
3. Aplicar dos veces deja los mismos valores (idempotente).
4. Los defaults por verbo (susurró→susurro, gritó→grito, ordenó→firme)
   se aplican cuando no hay fila de CSV.

M22: antes usaba la BD REAL de producción (/home/aegis2/aurea) y
esperaba las escalas VIEJAS (pace 1.5/0.7, intensidad 1-5). Ahora usa
BD temporal (AUREA_HOME) y las escalas decididas (pace = velocidad
0.6-1.3, intensidad 0-1 con (n-1)/4).
"""
import csv
import os
import sys
import tempfile

# Raíz del repo (este script vive en scripts/): nunca /home/aegis2/aurea.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stubs de Kokoro/XTTS: la VM no tiene kokoro_onnx/misaki instalados.
import types as _types
_kokoro = _types.ModuleType("kokoro_onnx")
_kokoro.Kokoro = object
sys.modules.setdefault("kokoro_onnx", _kokoro)
_misaki = _types.ModuleType("misaki")
_espeak = _types.ModuleType("misaki.espeak")
_espeak.EspeakG2P = object
_espeak.EspeakFallback = object
_misaki.espeak = _espeak
sys.modules.setdefault("misaki", _misaki)
sys.modules.setdefault("misaki.espeak", _espeak)

_TMP = tempfile.mkdtemp(prefix="aurea-verificar-direccion-")
os.environ["AUREA_HOME"] = _TMP  # BD temporal, nunca producción
os.environ["AUREA_DB_PATH"] = os.path.join(_TMP, "aurea.db")

from aurea.casting.cast_manager import CastManager  # noqa: E402
from aurea.casting.detector import detect_characters  # noqa: E402
from aurea.core.database import Database  # noqa: E402
from aurea.core.generator import segmentize  # noqa: E402
from aurea.direction.rules import direct_segment  # noqa: E402
from scripts.aplicar_direccion import apply_direction  # noqa: E402

FAILURES = []


def csv_temp(rows):
    """CSV temporal con NamedTemporaryFile (mktemp está obsoleto)."""
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                   newline="", encoding="utf-8")
    writer = csv.writer(f)
    writer.writerow(["escena", "turno", "personaje", "emocion", "volumen",
                     "ritmo", "intensidad"])
    writer.writerows(rows)
    f.close()
    return f.name


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def crear_libro_capitulo_prueba(db, texto):
    """Crea un libro y capítulo de prueba con segmentos y personajes detectados."""
    book_id = db.add_book("Libro prueba dirección", "/tmp/test_dir.txt", "txt")
    db.add_chapter(book_id, 0, "Capítulo prueba", texto)
    chapters = db.chapters(book_id)
    chapter = chapters[0]
    cid = chapter.id

    # Paso 1: Detectar personajes del texto
    names = detect_characters([texto])

    # Paso 2: Crear personajes (incluye narrador)
    cast = CastManager(db)
    cast.ensure_narrator(book_id)
    created = cast.build_cast(book_id, names)

    # Paso 3: Crear segmentos con use_cast=True (ahora hay personajes)
    db.clear_segments(cid)
    parts = segmentize(texto, True)
    for i, (kind, speaker, chunk, pause) in enumerate(parts):
        cid_char = None
        if speaker:
            name = speaker if kind == "dialogue" else "Narrador"
            cid_char = cast.character_id(book_id, name)
        db.add_segment(cid, i, chunk, kind=kind,
                       character_id=cid_char, pause_after_ms=pause)

    return book_id, cid


def main():
    # Crear libro y capítulo de prueba con 4 segmentos (1 narración + 3 diálogo)
    # Crear libro y capítulo de prueba con 3 segmentos de diálogo
    texto = (
        "—Dijo Alberto.\n\n"
        "—Gritó Elena.\n\n"
        "—Ordenó Marco."
    )
    book_id, cid = crear_libro_capitulo_prueba(Database(), texto)

    db = Database()
    segs = db.segments(cid)
    check(f"Capítulo tiene {len(segs)} segmentos", len(segs) == 3,
          f"obtuvo {len(segs)}")

    # Mostrar personajes detectados
    chars = db.characters(book_id)
    print(f"  Personajes: {[(c.id, c.name, c.voice) for c in chars]}")

    # Verificar que los segmentos de diálogo tienen character_id
    dialog_segs = [s for s in segs if s.kind == "dialogue"]
    check("Segmentos de diálogo tienen character_id",
          all(s.character_id is not None for s in dialog_segs),
          f"segmentos de diálogo: {[(s.idx, s.character_id) for s in dialog_segs]}")

    # Caso 1: Aplicar CSV con personaje correcto
    print("\n== Caso 1: Aplicar CSV con personajes correctos ==")
    csv_path = csv_temp([
        [1, 1, "Alberto", "miedo", "susurro", "lento", 3],
        [1, 2, "Elena", "enojo", "grito", "rápido", 5],
        [1, 3, "Marco", "determinación", "firme", "normal", 4],
    ])

    aplicadas, errores = apply_direction(book_id, csv_path, 0)
    check("Aplicar CSV aplica 3 segmentos", aplicadas == 3,
          f"obtuvo {aplicadas}")
    check("Sin errores de personaje", len(errores) == 0,
          f"errores: {errores}")

    # Verificar valores aplicados
    segs = db.segments(cid)
    for s in segs:
        print(f"  Seg {s.idx}: \"{s.text[:30]}\" emotion={s.emotion} "
              f"delivery={s.delivery} intensity={s.intensity} pace={s.pace} "
              f"pause_before={s.pause_before_ms} pause_after={s.pause_after_ms} "
              f"directed={s.directed}")

    # Alberto: miedo, susurro, lento, intensidad 3
    # Escalas decididas: pace = factor de VELOCIDAD (lento = 0.75, no 1.5);
    # intensidad CSV 1-5 -> 0-1 con (n-1)/4 (3 -> 0.5).
    seg_alberto = segs[0]  # —Dijo Alberto.
    check("Alberto: emotion=miedo", seg_alberto.emotion == "miedo",
          f"obtuvo {seg_alberto.emotion}")
    check("Alberto: delivery=susurro", seg_alberto.delivery == "susurro",
          f"obtuvo {seg_alberto.delivery}")
    check("Alberto: pace=lento (0.75 velocidad)", seg_alberto.pace == 0.75,
          f"obtuvo {seg_alberto.pace}")
    check("Alberto: intensity=3 -> 0.5", seg_alberto.intensity == 0.5,
          f"obtuvo {seg_alberto.intensity}")
    check("Alberto: directed=1", seg_alberto.directed == 1,
          f"obtuvo {seg_alberto.directed}")

    # Elena: enojo, grito, rápido, intensidad 5 -> 1.0
    seg_elena = segs[1]  # —Gritó Elena.
    check("Elena: emotion=enojo", seg_elena.emotion == "enojo",
          f"obtuvo {seg_elena.emotion}")
    check("Elena: delivery=grito", seg_elena.delivery == "grito",
          f"obtuvo {seg_elena.delivery}")
    check("Elena: pace=rápido (1.2 velocidad)", seg_elena.pace == 1.2,
          f"obtuvo {seg_elena.pace}")
    check("Elena: intensity=5 -> 1.0", seg_elena.intensity == 1.0,
          f"obtuvo {seg_elena.intensity}")
    check("Elena: directed=1", seg_elena.directed == 1,
          f"obtuvo {seg_elena.directed}")

    # Marco: intensidad 4 -> 0.75
    seg_marco = segs[2]  # —Ordenó Marco.
    check("Marco: intensity=4 -> 0.75", seg_marco.intensity == 0.75,
          f"obtuvo {seg_marco.intensity}")

    # Caso 2: CSV con personaje que no coincide -> rechazar
    print("\n== Caso 2: CSV con personaje no coincidente ==")
    csv_path2 = csv_temp([
        [1, 1, "PersonajeInexistente", "neutral", "normal", "normal", 1],
    ])

    aplicadas2, errores2 = apply_direction(book_id, csv_path2, 0)
    check("CSV con personaje no coincidente se rechaza", aplicadas2 == 0,
          f"obtuvo {aplicadas2}")
    check("Se reporta error", len(errores2) > 0,
          f"errores: {errores2}")

    # Caso 3: Idempotencia - aplicar dos veces da mismo resultado
    print("\n== Caso 3: Idempotencia ==")
    aplicadas_1, errores_1 = apply_direction(book_id, csv_path, 0)
    segs_1 = db.segments(cid)
    valores_1 = [(s.emotion, s.delivery, s.intensity, s.pace,
                  s.pause_before_ms, s.pause_after_ms) for s in segs_1]

    aplicadas_2, errores_2 = apply_direction(book_id, csv_path, 0)
    segs_2 = db.segments(cid)
    valores_2 = [(s.emotion, s.delivery, s.intensity, s.pace,
                  s.pause_before_ms, s.pause_after_ms) for s in segs_2]

    check("Idempotencia: mismo resultado tras 2 aplicaciones",
          valores_1 == valores_2,
          f"antes={valores_1} despues={valores_2}")

    # Caso 4: Defaults por verbo cuando no hay fila de CSV
    print("\n== Caso 4: Defaults por verbo ==")
    db2 = Database()
    book_id2 = db2.add_book("Libro defaults", "/tmp/test2.txt", "txt")
    db2.add_chapter(book_id2, 0, "Cap defaults",
                    "Él susurró algo.\n\nÉl gritó fuerte.\n\nÉl ordenó detenerse.")
    chapters2 = db2.chapters(book_id2)
    chapter2 = chapters2[0]
    cid2 = chapter2.id

    # Crear segmentos (solo narrador, sin personajes de diálogo)
    segs2 = db2.segments(cid2)
    if not segs2:
        use_cast = len(db2.characters(book_id2)) > 1
        cast2 = CastManager(db2) if use_cast else None
        if cast2:
            cast2.ensure_narrator(book_id2)
        parts = segmentize(chapters2[0].text, use_cast)
        for i, (kind, speaker, chunk, pause) in enumerate(parts):
            cid_char = None
            if use_cast:
                name = speaker if (kind == "dialogue" and speaker) else "Narrador"
                cid_char = cast2.character_id(book_id2, name)
            db2.add_segment(cid2, i, chunk, kind=kind,
                            character_id=cid_char, pause_after_ms=pause)

    # Dirigir con rules para establecer defaults por verbo
    from aurea.direction.director import DirectionWorker
    from PySide6.QtCore import QCoreApplication, QEventLoop

    app = QCoreApplication(sys.argv)
    w = DirectionWorker(cid2, use_llm=False)
    loop = QEventLoop()
    w.finished_ok.connect(lambda r, l: loop.quit())
    w.failed.connect(lambda e: (print("ERROR:", e), loop.quit()))
    w.start()
    loop.exec()

    segs2 = db2.segments(cid2)
    check("Caso 4: 3 segmentos de narración", len(segs2) == 3,
          f"obtuvo {len(segs2)}")
    print(f"  Después de dirigir:")
    for s in segs2:
        print(f"    Seg {s.idx}: \"{s.text[:30]}\" emotion={s.emotion} "
              f"delivery={s.delivery} intensity={s.intensity} pace={s.pace}")

    # Verificar defaults por verbo
    # El texto tiene 3 párrafos, cada uno es un segmento de narración
    # El narrador es el personaje para todos
    seg_susurro = segs2[0]  # Él susurró algo.
    check("Susurró → delivery=susurro", seg_susurro.delivery == "susurro",
          f"obtuvo {seg_susurro.delivery}")

    seg_grito = segs2[1]  # Él gritó fuerte.
    check("Gritó → delivery=grito", seg_grito.delivery == "grito",
          f"obtuvo {seg_grito.delivery}")

    seg_orden = segs2[2]  # Él ordenó detenerse.
    check("Ordenó → delivery=firme", seg_orden.delivery == "firme",
          f"obtuvo {seg_orden.delivery}")

    # Limpiar archivos temporales
    for f in [csv_path, csv_path2]:
        try:
            os.unlink(f)
        except:
            pass

    # Resumen
    print()
    if FAILURES:
        print(f"FALLOS ({len(FAILURES)}): {FAILURES}")
        sys.exit(1)
    else:
        print("TODAS LAS PRUEBAS DE DIRECCIÓN PASARON")
        sys.exit(0)


if __name__ == "__main__":
    main()
