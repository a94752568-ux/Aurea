"""Verificación de reglas de pausas en AUREA.

Casos de prueba (todos deben pasar):
1. Coma → 180 ms
2. Punto → 450 ms
3. Puntos suspensivos → 700 ms
4. Cambio de hablante → 500 ms
5. [pausa=1.2s] al final → 1200 ms, texto limpio sin [
6. [pausa=0.8s] en medio → dos segmentos, primero con 800 ms
7. [pausa=9s] → recortado a 3000 ms
8. Punto + ritmo lento + miedo → 877 ms (nombre y float 0.75 → 780 ms)
9. Pregunta final de turno → +200 ms
10. Intensidad 1.0 (normalizada 0-1) → +300 ms; 0.5 → sin bonus
11. Ningún segmento con pause_after_ms NULL (BD temporal propia)
12. Idempotencia: dos ejecuciones = mismo resultado (BD temporal propia)

M22: los casos 5/8/10/11/12 antes eran vacuos (or True, escala 1-5
obsoleta, o pasaban sin datos). Ahora prueban caminos reales de
producción sobre una BD temporal (AUREA_HOME), nunca la de producción.
"""
import os
import sqlite3
import sys
import tempfile

# Raíz del repo (este script vive en scripts/): nunca /home/aegis2/aurea.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stubs de Kokoro/XTTS: la VM no tiene kokoro_onnx/misaki instalados ni
# modelos; los adaptadores solo se importan, no se sintetiza audio aquí.
# (Mismo patrón que tests/test_aurea.py.)
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

_TMP = tempfile.mkdtemp(prefix="aurea-verificar-pausas-")
os.environ["AUREA_HOME"] = _TMP  # la BD y el audio van al tmpdir

from PySide6.QtCore import QCoreApplication  # noqa: E402

from aurea.core.database import Database  # noqa: E402
from aurea.core.generator import _split_markers, segmentize  # noqa: E402
from aurea.core.models import Segment  # noqa: E402
from aurea.direction.director import DirectionWorker  # noqa: E402
from aurea.direction.rules import (  # noqa: E402
    apply_emotion_pace,
    clamp_pause_after,
    clamp_pause_before,
    direct_segment,
    parse_markers,
    pause_for_punctuation,
    speaker_change_pause,
)

FAILURES = []

app = QCoreApplication(sys.argv)


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def make_chapter(text="Hola mundo. Adiós mundo."):
    """BD temporal con un libro/capítulo/segmentos propios (casos 11-12)."""
    db = Database()
    bid = db.add_book("Libro de prueba", "/tmp/prueba.txt", "txt")
    cid = db.add_chapter(bid, 0, "Capítulo de prueba", text)
    parts = segmentize(text, False)
    for i, (kind, _sp, chunk, pause) in enumerate(parts):
        db.add_segment(cid, i, chunk, kind=kind, pause_after_ms=pause)
    return db, cid


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
# La base por puntuación es 700 ms, pero "..." fija pace 0.9 (habla más
# lenta) y las pausas se alargan: 700 * (1/0.9) = 777 ms. La expectativa
# antigua (700 exactos) era anterior a la semántica float de pace (A1).
check("Puntos suspensivos base → 700 ms",
      pause_for_punctuation("Hola mundo...") == 700,
      f"obtuvo {pause_for_punctuation('Hola mundo...')}")
check("Puntos suspensivos dirigidos → 777 ms (700 * 1/0.9)",
      d["pause_after_ms"] == 777, f"obtuvo {d['pause_after_ms']}")

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
# M22: antes era `"[" not in seg.text or True` (siempre PASS). El marcador
# real se limpia en segmentize; el texto del segmento no lleva corchetes.
check("Texto limpio sin [", "[" not in seg.text, f"texto: {seg.text!r}")

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
d_manual = apply_emotion_pace(450, "lento", "miedo")
check("Punto + lento + miedo → 877 ms", d_manual == 877, f"obtuvo {d_manual}")

d_manual2 = apply_emotion_pace(180, "rápido", "enojo")
check("Coma + rápido + enojo → 100 ms", d_manual2 == 100, f"obtuvo {d_manual2}")

# M22: camino real de producción — el pace llega como float (factor de
# velocidad 0.6-1.3), no como nombre. 450 * (1/0.75) * 1.3 = 780.
d_float = apply_emotion_pace(450, 0.75, "miedo")
check("Punto + pace 0.75 + miedo → 780 ms", d_float == 780, f"obtuvo {d_float}")

# ---- Caso 9: Pregunta final de turno ----
print("== Pregunta final de turno ==")
seg = Segment(0, 0, 0, "¿Cómo estás?", 0, "pending", "dialogue")
d = direct_segment(seg)
check("Pregunta diálogo → 450 + 200 = 650 ms", d["pause_after_ms"] == 650, f"obtuvo {d['pause_after_ms']}")

# ---- Caso 10: Intensidad normalizada 0-1 ----
print("== Intensidad normalizada ==")
# M22: antes usaba intensity=5 (escala CSV 1-5 obsoleta). En BD la
# intensidad es float 0-1 y la regla +300 ms dispara con >= 0.8.
seg = Segment(0, 0, 0, "Hola mundo.", 0, "pending", "narration", intensity=1.0)
d = direct_segment(seg)
check("Intensidad 1.0 → 450 + 300 = 750 ms", d["pause_after_ms"] == 750, f"obtuvo {d['pause_after_ms']}")

seg = Segment(0, 0, 0, "Hola mundo.", 0, "pending", "narration", intensity=0.5)
d = direct_segment(seg)
check("Intensidad 0.5 → 450 ms (sin bonus)", d["pause_after_ms"] == 450, f"obtuvo {d['pause_after_ms']}")

seg = Segment(0, 0, 0, "Hola mundo.", 0, "pending", "narration", intensity=0.8)
d = direct_segment(seg)
check("Intensidad 0.8 → 750 ms (umbral)", d["pause_after_ms"] == 750, f"obtuvo {d['pause_after_ms']}")

# ---- Caso 11: Ningún segmento NULL después de dirigir ----
print("== Ningún segmento NULL después de dirigir ==")
# M22: antes buscaba un capítulo sin dirigir en la BD REAL y pasaba en
# vacío si no había ninguno. Ahora construye sus propios datos en BD
# temporal y dirige de verdad con DirectionWorker.
db, chap_id = make_chapter()
w = DirectionWorker(chap_id, use_llm=False)
w.start()
w.wait()
segs = db.segments(chap_id)
all_not_null = all(s.pause_after_ms is not None for s in segs)
check("Todos los segmentos dirigidos tienen pause_after_ms", all_not_null,
      f"algunos NULL: {[(s.id, s.pause_after_ms) for s in segs if s.pause_after_ms is None][:5]}")
check("Todos los segmentos quedaron dirigidos",
      all(s.directed for s in segs),
      f"sin dirigir: {[s.id for s in segs if not s.directed]}")
db.close()

# ---- Caso 12: Idempotencia ----
print("== Idempotencia ==")
db, chap_id = make_chapter()
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
db.close()

# ---- Resumen ----
print()
if FAILURES:
    print(f"FALLOS ({len(FAILURES)}): {FAILURES}")
    sys.exit(1)
else:
    print("TODAS LAS PRUEBAS PASARON")
    sys.exit(0)
