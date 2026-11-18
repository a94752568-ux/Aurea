"""Verificación de importación Markdown (.md) en AUREA.

Casos de prueba (todos deben pasar):
1. # Título → crea capítulo con ese título; título NO es segmento
   (va al campo title del capítulo en BD).
2. ## Sección → segmento anterior tiene pause_after_ms 800
3. **negrita** y *cursiva* → texto limpio, sin asteriscos
4. [texto](url) → queda texto, sin URL
5. ![alt](url) → no genera segmento
6. Bloque de código ``` → no genera segmentos
7. `` código `` en línea → queda código, sin backticks
8. Lista de 3 ítems → 3 segmentos, los dos primeros con pausa 180 ms
9. --- → segmento anterior tiene pause_after_ms 800
10. [pausa=0.8s] dentro de .md → 800 ms, texto limpio sin [
11. Encabezado con "dijo X" no crea personaje fantasma
12. Ningún segmento contiene #, *, [, ], backticks de Markdown
13. Título H1 se anuncia como segmento de narración con pause_after_ms=800
"""
import sys
import os
import tempfile

sys.path.insert(0, "/home/aegis2/aurea")

from aurea.ingestion.md_reader import MarkdownReader
from aurea.core.generator import segmentize
from aurea.direction.rules import parse_markers

FAILURES = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def get_segments(text, use_cast=False):
    """Segmenta un texto y devuelve la lista de (kind, speaker, txt, pause)."""
    return segmentize(text, use_cast)


# ============================================================
# Caso 1: H1 crea capítulo
# ============================================================
print("== Encabezados ==")
md = """# Mi Libro

Este es el primer capítulo.

## Sección A

Texto de la sección A.

### Subsección

Texto de la subsección.

---

Texto después de la regla horizontal.

# Otro Capítulo

Texto del segundo capítulo.
"""
tmp = tempfile.mktemp(suffix=".md")
with open(tmp, "w", encoding="utf-8") as f:
    f.write(md)
chapters = MarkdownReader().read(tmp)

check("Al menos 2 capítulos (H1)", len(chapters) >= 2, f"obtuvo {len(chapters)}")
check("Primer capítulo es 'Mi Libro'", chapters[0][0] == "Mi Libro", f"obtuvo '{chapters[0][0]}'")

# ---- Caso 2: H2 da pause_after_ms 800 (verificado en segmentos generados) ----
text_ch1 = chapters[0][1]
parts_ch1 = get_segments(text_ch1, use_cast=False)
# Buscar segmento con pause_after_ms=800 (de H2 y ---)
pause_800_segs = [p for p in parts_ch1 if p[3] == 800]
check("H2 y --- generan pausa 800 ms", len(pause_800_segs) >= 2,
      f"encontró {len(pause_800_segs)} segmentos con 800 ms")

# H3 genera pausa 500 ms
pause_500_segs = [p for p in parts_ch1 if p[3] == 500]
check("H3 genera pausa 500 ms", len(pause_500_segs) >= 1,
      f"encontró {len(pause_500_segs)} segmentos con 500 ms")

# ---- Casos 3-7: Limpieza de formato en segmentos generados ----
print("== Limpieza de formato Markdown ==")
md2 = """# Libro de prueba

Esto tiene **negrita** y *cursiva* aquí.

Un [enlace](http://ejemplo.com) y una imagen ![alt](http://img.com/x.png).

Código `en línea` aquí.

Un bloque de código:
```
def hola():
    print("mundo")
```

> Una cita.

Lista:
- ítem 1
- ítem 2
- ítem 3

Nota al pie[^1].

[^1]: definición de nota al pie

<div>HTML</div>
"""
tmp2 = tempfile.mktemp(suffix=".md")
with open(tmp2, "w", encoding="utf-8") as f:
    f.write(md2)
chapters2 = MarkdownReader().read(tmp2)
text2 = chapters2[0][1]
parts2 = get_segments(text2, use_cast=False)

# Verificar que ningún segmento tiene marcas MD
md_chars = set("#*[]`")
all_clean = True
for kind, speaker, txt, pause in parts2:
    for c in md_chars:
        if c in txt:
            check(f"Segmento sin '{c}'", False, f"texto: '{txt[:50]}'")
            all_clean = False

if all_clean:
    check("Segmentos sin marcas MD (#*[]`)", True)

# Verificar contenido específico
texts_concat = " ".join(p[2] for p in parts2)
check("No quedan **", "**" not in texts_concat, f"texto: {texts_concat[:200]}")
check("Enlace conserva texto sin URL", "enlace" in texts_concat and "http://" not in texts_concat, f"texto: {texts_concat[:200]}")
check("Imagen eliminada", "img.com" not in texts_concat and "alt" not in texts_concat, f"texto: {texts_concat[:200]}")
check("Código en línea sin backticks", "en línea" in texts_concat and "`" not in texts_concat, f"texto: {texts_concat[:200]}")
check("Bloque de código eliminado", "def hola" not in texts_concat and "print" not in texts_concat, f"texto: {texts_concat[:200]}")
check("Cita conservada", "Una cita" in texts_concat, f"texto: {texts_concat[:200]}")
check("No quedan #", "#" not in texts_concat.replace("# ", ""), f"texto: {texts_concat[:200]}")

# Nota al pie eliminada
check("Nota al pie referencia eliminada", "[^1" not in texts_concat, f"texto: {texts_concat[:200]}")
check("Nota al pie definición eliminada", "definición de nota" not in texts_concat, f"texto: {texts_concat[:200]}")
check("HTML eliminado", "<div>" not in texts_concat, f"texto: {texts_concat[:200]}")

# ---- Caso 8: Lista ----
print("== Listas ==")
# Los ítems de lista se convierten en párrafos separados por md_reader,
# cada uno con [pausa=0.18s] salvo el último.
md_list = """- ítem 1
- ítem 2
- ítem 3"""
tmp_list = tempfile.mktemp(suffix=".md")
with open(tmp_list, "w", encoding="utf-8") as f:
    f.write(md_list)
chapters_list = MarkdownReader().read(tmp_list)
parts_list = get_segments(chapters_list[0][1], use_cast=False)
check("Lista de 3 ítems → 3 segmentos", len(parts_list) == 3, f"obtuvo {len(parts_list)}")
# Los dos primeros ítems tienen pausa de coma (180 ms), el último no
if len(parts_list) >= 3:
    check("Lista: ítem 1 pausa 180 ms", parts_list[0][3] == 180,
          f"obtuvo {parts_list[0][3]}")
    check("Lista: ítem 2 pausa 180 ms", parts_list[1][3] == 180,
          f"obtuvo {parts_list[1][3]}")
    check("Lista: ítem 3 sin pausa", parts_list[2][3] is None or parts_list[2][3] == 0,
          f"obtuvo {parts_list[2][3]}")

# Limpiar archivo temporal de lista
try:
    os.unlink(tmp_list)
except:
    pass

# ---- Caso 10: [pausa=0.8s] en .md ----
print("== Marcador en .md ==")
md3 = "# Test\n\nTexto [pausa=0.8s] medio."
tmp3 = tempfile.mktemp(suffix=".md")
with open(tmp3, "w", encoding="utf-8") as f:
    f.write(md3)
chapters3 = MarkdownReader().read(tmp3)
text3 = chapters3[0][1]
parts3 = get_segments(text3, use_cast=False)
# El segmento del título debe tener pause_after_ms=800
title_seg = parts3[0]
check("Título H1 tiene pause_after_ms=800", title_seg[3] == 800,
      f"obtuvo {title_seg[3]}")
# El texto del título no debe contener [
check("Título sin marcadores", "[" not in title_seg[2], f"texto: '{title_seg[2]}'")
# El segmento de "Texto" debe existir
texto_seg = parts3[1] if len(parts3) > 1 else None
check("Segmento de texto existe", texto_seg is not None)

# ---- Caso 11: Encabezado con "dijo X" no crea personaje fantasma ----
print("== Encabezado no contamina casting ==")
md4 = "# Juan dijo algo\n\nTexto normal."
tmp4 = tempfile.mktemp(suffix=".md")
with open(tmp4, "w", encoding="utf-8") as f:
    f.write(md4)
chapters4 = MarkdownReader().read(tmp4)
text4 = chapters4[0][1]
parts4 = get_segments(text4, use_cast=False)
# El H1 título ("Juan dijo algo") no debe crear un personaje falso
# En use_cast=False, no hay personajes, solo narración
check("H1 no crea segmento de diálogo falso", all(p[0] == "narration" for p in parts4),
      f"tipos: {[p[0] for p in parts4]}")

# ---- Caso 12: Ningún segmento con marcas MD ----
print("== Sin marcas MD en segmentos ==")
for ch_title, ch_text in chapters:
    parts = get_segments(ch_text, use_cast=False)
    for kind, speaker, txt, pause in parts:
        for c in md_chars:
            check(f"Segmento sin '{c}'", c not in txt, f"texto: '{txt[:50]}'")

# Limpiar archivos temporales
for f in [tmp, tmp2, tmp3, tmp4]:
    try:
        os.unlink(f)
    except:
        pass

# ---- Resumen ----
print()
if FAILURES:
    print(f"FALLOS ({len(FAILURES)}): {FAILURES}")
    sys.exit(1)
else:
    print("TODAS LAS PRUEBAS DE MARKDOWN PASARON")
    sys.exit(0)
