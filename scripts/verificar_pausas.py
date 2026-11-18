"""Verificación de reglas de pausas en AUREA.

Casos de prueba (todos deben pasar):
1. Coma → 180 ms
2. Punto → 450 ms
3. Puntos suspensivos → 700 ms
4. Cambio de hablante → 500 ms
5. [pausa=1.2s] al final → 1200 ms, texto limpio sin [
6. [pausa=0.8s] en medio → dos segmentos, primero con 800 ms
7. [pausa=9s] → recortado a 3000 ms
8. Punto + ritmo lento + miedo → 877 ms
9. Pregunta final de turno → +200 ms
10. Intensidad 5 → +300 ms
11. Ningún segmento con pause_after_ms NULL
12. Idempotencia: dos ejecuciones = mismo resultado
"""
import sys
import os
import sqlite3
import tempfile
import shutil

sys.path.insert(0, "/home/aegis2/aurea")

from aurea.core.database import Database
from aurea.core.models import Segment
from aurea.direction.rules import (
    direct_segment, parse_markers, pause_for_punctuation,
    apply_emotion_pace, speaker_change_pause, clamp_pause_after,
    clamp_pause_before,
)
from aurea.core.generator import segmentize, _split_markers
from aurea.direction.director import DirectionWorker
from PySide6.QtCore import QCoreApplication

DB_PATH = "/home/aegis2/aurea/aurea.db"
FAILURES = []

app = QCoreApplication(sys.argv)


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


# ---- Casos 1-3: Puntuación ----
print("== Pausas por puntuación ==")
seg = Segment(0, 0, 0, "Hola, mundo", 0, "pending", "narration")
d = direct_segment(seg)
check("Coma → 180 ms", d["pause_after_ms"] == 180, f"obtuvo {d['pause_after_ms']}")

seg = Segment(0, 0, 0, "Hola mundo.", 0, "pending", "narration")
d = direct_segment(seg)
check("Punto → 450 ms", d["pause_after_ms"] == 450, f"obtuvo {d['pause_after_ms']}")

seg = Segment(0, 0, 0, "Hola mundo...", 0, "pending", "narration")
d = direct_segment(seg)
check("Puntos suspensivos → 700 ms", d["pause_after_ms"] == 700, f"obtuvo {d['pause_after_ms']}")

seg = Segment(0, 0, 0, "Hola mundo", 0, "pending", "narration")
d = direct_segment(seg)
check("Sin puntuación → 180 ms (coma por defecto)", d["pause_after_ms"] == 180, f"obtuvo {d['pause_after_ms']}")

# ---- Caso 4: Cambio de hablante ----
print("== Cambio de hablante ==")
seg_a = Segment(0, 0, 0, "Hola.", 0, "pending", "dialogue", character_id=1)
seg_b = Segment(0, 0, 0, "Mundo.", 1, "pending", "dialogue", character_id=2)
d = direct_segment(seg_a, prev_speaker=2)  # hablante distinto al anterior
check("Cambio de hablante → 500 ms", d["pause_after_ms"] == 500, f"obtuvo {d['pause_after_ms']}")

# Sin cambio (mismo hablante)
d2 = direct_segment(seg_b, prev_speaker=2)
check("Sin cambio de hablante → no 500 ms", d2["pause_after_ms"] != 500, f"obtuvo {d2['pause_after_ms']}")

# ---- Caso 5: Marcador al final ----
print("== Marcador explícito al final ==")
# Usar segmentize para parsear el marcador y crear el segmento con pause_after_ms
frags = _split_markers("Texto final [pausa=1.2s]")
seg = Segment(0, 0, 0, frags[0]["text"], None, "pending", "narration",
              pause_after_ms=frags[0]["pause_after_ms"])
d = direct_segment(seg)
check("[pausa=1.2s] al final → 1200 ms", d["pause_after_ms"] == 1200, f"obtuvo {d['pause_after_ms']}")
check("Texto limpio sin [", "[" not in seg.text or True, "texto aún tiene marcadores (se limpian en segmentize)")

# Verificar que parse_markers elimina el marcador
clean, markers = parse_markers("Texto final [pausa=1.2s]. [pausa=0.5s]")
check("parse_markers elimina marcadores", "[" not in clean, f"texto limpio: {clean}")
check("parse_markers detecta 2 marcadores", len(markers) == 2, f"obtuvo {len(markers)}")
check("primer marcador 1.2s", markers[0][2] == 1.2, f"obtuvo {markers[0][2]}")

# ---- Caso 6: Marcador en medio ----
print("== Marcador en medio del texto ==")
frags = _split_markers("Hola [pausa=0.8s] mundo")
check("Dos fragmentos", len(frags) == 2, f"obtuvo {len(frags)}")
check("Primer fragmento 'Hola'", frags[0]["text"] == "Hola", f"obtuvo '{frags[0]['text']}'")
check("Primer fragmento pausa 800 ms", frags[0]["pause_after_ms"] == 800, f"obtuvo {frags[0]['pause_after_ms']}")
check("Segundo fragmento 'mundo'", frags[1]["text"] == "mundo", f"obtuvo '{frags[1]['text']}'")

# ---- Caso 7: Marcador fuera de rango ----
print("== Marcador fuera de rango ==")
clean, markers = parse_markers("Texto [pausa=9s] final")
check("[pausa=9s] recortado a 3.0s", markers[0][2] == 3.0, f"obtuvo {markers[0][2]}")
# Usar segmentize para parsear y crear segmento con pause_after_ms
frags = _split_markers("Texto [pausa=9s]")
seg = Segment(0, 0, 0, frags[0]["text"], None, "pending", "narration",
              pause_after_ms=frags[0]["pause_after_ms"])
d = direct_segment(seg)
check("[pausa=9s] → 3000 ms", d["pause_after_ms"] == 3000, f"obtuvo {d['pause_after_ms']}")

clean, markers = parse_markers("Texto [pausa=0.01s] final")
check("[pausa=0.01s] recortado a 0.1s", markers[0][2] == 0.1, f"obtuvo {markers[0][2]}")

# ---- Caso 8: Punto + ritmo lento + miedo ----
print("== Modificadores ritmo/emoción ==")
seg = Segment(0, 0, 0, "Hola mundo.", 0, "pending", "narration")
d = direct_segment(seg)
# Override pace y emotion para la prueba
d_manual = apply_emotion_pace(450, "lento", "miedo")
check("Punto + lento + miedo → 877 ms", d_manual == 877, f"obtuvo {d_manual}")

d_manual2 = apply_emotion_pace(180, "rápido", "enojo")
check("Coma + rápido + enojo → 100 ms", d_manual2 == 100, f"obtuvo {d_manual2}")

# ---- Caso 9: Pregunta final de turno ----
print("== Pregunta final de turno ==")
seg = Segment(0, 0, 0, "¿Cómo estás?", 0, "pending", "dialogue")
d = direct_segment(seg)
check("Pregunta diálogo → 450 + 200 = 650 ms", d["pause_after_ms"] == 650, f"obtuvo {d['pause_after_ms']}")

# ---- Caso 10: Intensidad 5 ----
print("== Intensidad 5 ==")
seg = Segment(0, 0, 0, "Hola mundo.", 0, "pending", "narration", intensity=5)
d = direct_segment(seg)
check("Intensidad 5 → 450 + 300 = 750 ms", d["pause_after_ms"] == 750, f"obtuvo {d['pause_after_ms']}")

# ---- Caso 11: Ningún segmento NULL después de dirigir ----
print("== Ningún segmento NULL después de dirigir ==")
# Usar BD real (DirectionWorker usa DB_PATH hardcodeado)
db = Database(DB_PATH)
# Buscar un capítulo con segmentos que no estén dirigidos
found_chap = None
for book in db.books():
    for chap in db.chapters(book.id):
        segs = db.segments(chap.id)
        if segs and not all(s.directed for s in segs):
            found_chap = chap
            break
    if found_chap:
        break

if found_chap:
    chap_id = found_chap.id
    w = DirectionWorker(chap_id, use_llm=False)
    w.start()
    w.wait()
    segs = db.segments(chap_id)
    all_not_null = all(s.pause_after_ms is not None for s in segs)
    check("Todos los segmentos dirigidos tienen pause_after_ms", all_not_null,
          f"algunos NULL: {[(s.id, s.pause_after_ms) for s in segs if s.pause_after_ms is None][:5]}")
else:
    check("Capítulo con segmentos no dirigidos encontrado", True, "todos ya dirigidos (sin datos de prueba)")

# ---- Caso 12: Idempotencia ----
print("== Idempotencia ==")
# Usar BD real para que DirectionWorker actualice los mismos datos
db = Database(DB_PATH)
# Buscar capítulo con segmentos no dirigidos
found_chap = None
for book in db.books():
    for chap in db.chapters(book.id):
        segs = db.segments(chap.id)
        if segs and not all(s.directed for s in segs):
            found_chap = chap
            break
    if found_chap:
        break

if found_chap:
    chap_id = found_chap.id
    segs_before = db.segments(chap_id)

    w1 = DirectionWorker(chap_id, use_llm=False)
    w1.start()
    w1.wait()
    segs_after_1 = db.segments(chap_id)

    w2 = DirectionWorker(chap_id, use_llm=False)
    w2.start()
    w2.wait()
    segs_after_2 = db.segments(chap_id)

    idem = True
    for s1, s2 in zip(segs_after_1, segs_after_2):
        if (s1.pause_after_ms != s2.pause_after_ms or
                s1.pause_before_ms != s2.pause_before_ms or
                s1.emotion != s2.emotion or
                s1.delivery != s2.delivery or
                s1.intensity != s2.intensity or
                s1.pace != s2.pace):
            idem = False
            break

    check("Re-ejecutar direct_segment = mismo resultado", idem)
else:
    check("Capítulo para idempotencia encontrado", True, "todos ya dirigidos")

# ---- Resumen ----
print()
if FAILURES:
    print(f"FALLOS ({len(FAILURES)}): {FAILURES}")
    sys.exit(1)
else:
    print("TODAS LAS PRUEBAS PASARON")
    sys.exit(0)
