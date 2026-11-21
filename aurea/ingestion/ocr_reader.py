"""OCR local para PDF escaneados (Fase 1, v0.6.0).

Motor: Tesseract (binario local, Apache 2.0) vía pytesseract
(Apache 2.0, verificado en metadato pip). Render con PyMuPDF
(AGPL-3.0 dual; uso local personal sin distribución — ver STATE.md).
Preprocesado con PIL (MIT-CMU) + numpy, sin OpenCV en v1.

Flujo por página:
  1. Capa de texto: si tiene >=30 caracteres y >=5 palabras -> "texto".
  2. Si no, ruta OCR: render a 300 DPI, grises + Otsu, deskew por OSD,
     TSV con PSM 6 (4 si 2 columnas, 3 fallback), orden de lectura por
     columna y posición vertical.
  3. Limpieza quirúrgica en dos pasadas: encabezados/pies repetidos
     (misma zona, mismo contenido, 3+ páginas consecutivas) y números
     de página se eliminan y se registran; desguionado y normalización.
  4. Confianza media por palabra/página; páginas <70 -> `revisar`;
     palabras conf <40 listadas en el informe.

Nada de esto toca generator.py, detector.py ni director.py: el texto
OCR entra al pipeline existente como texto de capítulo, sin cambios.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.database import aurea_home

# Umbrales de decisión (PROMPT Fase 1).
MIN_TEXT_CHARS = 30
MIN_TEXT_WORDS = 5
REVIEW_CONF = 70.0        # media de página < 70 -> revisar
LOW_WORD_CONF = 40        # palabra conf < 40 -> listada en el informe
OSD_MIN_CONFIDENCE = 5.0  # confianza mínima de OSD para aplicar el giro
CACHE_VERSION = 2        # subirlo si cambia el preprocesado/OCR (invalida la caché)
REPEAT_PAGES = 3          # encabezado/pie en 3+ páginas consecutivas
ZONE_TOP = 0.10           # 10% superior de la página
ZONE_BOTTOM = 0.10        # 10% inferior de la página
FOOTNOTE_ZONE = 0.15      # 15% inferior para notas al pie
FOOTNOTE_HEIGHT_RATIO = 0.75  # cuerpo menor que el 75% de la mediana
DESKEW_MIN_ANGLE = 0.5    # rotar si el ángulo OSD supera 0.5°

DEFAULT_LANG = "spa+eng"
DEFAULT_DPI = 300


class OcrCancelled(Exception):
    """El usuario canceló la importación OCR (reanudable por caché)."""


class OcrUnavailable(Exception):
    """Falta el motor OCR (Tesseract, pytesseract o PyMuPDF)."""


# ----------------------------------------------------------------------
# Disponibilidad y versión
# ----------------------------------------------------------------------
_TESS_VERSION = None


def tesseract_version() -> str:
    """Versión del binario Tesseract (parte de la clave de caché)."""
    global _TESS_VERSION
    if _TESS_VERSION is None:
        try:
            import pytesseract
            _TESS_VERSION = str(pytesseract.get_tesseract_version())
        except Exception:
            _TESS_VERSION = "desconocida"
    return _TESS_VERSION


def ocr_available() -> tuple[bool, str]:
    """(disponible, motivo). Todo local: binario + pytesseract + PyMuPDF."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False, "falta pytesseract (pip install pytesseract)"
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        return False, "falta PyMuPDF (pip install pymupdf)"
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False, "falta Pillow (pip install pillow)"
    if tesseract_version() == "desconocida":
        return False, ("no se encontró el binario tesseract "
                       "(apt install tesseract-ocr tesseract-ocr-spa)")
    return True, f"Tesseract {tesseract_version()} local"


# ----------------------------------------------------------------------
# Estructuras
# ----------------------------------------------------------------------
@dataclass
class OcrPageResult:
    page_no: int                # 1-based
    method: str                 # "texto" | "OCR"
    text: str                   # texto limpio de la página
    mean_conf: float = 100.0    # media de confianza por palabra (100 = texto)
    psm: int | None = None
    columns: int = 1
    needs_review: bool = False
    low_conf_words: list = field(default_factory=list)  # (línea, palabra)
    removed: list = field(default_factory=list)  # {text, motivo}
    footnotes_kept: list = field(default_factory=list)
    render_dpi: int = DEFAULT_DPI
    deskew_deg: int = 0
    lines: list = field(default_factory=list)  # líneas crudas (solo OCR)
    # Alto en píxeles de la imagen REALMENTE OCR-eada (tras el recorte
    # de márgenes del preprocesado). Las zonas del 10% de limpieza se
    # miden sobre este alto, no sobre el alto del render sin recortar.
    img_height: int = 0

    def to_dict(self) -> dict:
        return {
            "page_no": self.page_no, "method": self.method,
            "mean_confidence": round(self.mean_conf, 1), "psm": self.psm,
            "columns": self.columns, "needs_review": int(self.needs_review),
        }


@dataclass
class OcrReport:
    total_pages: int = 0
    ocr_pages: int = 0
    text_pages: int = 0
    mean_conf: float = 100.0      # media global (solo páginas OCR)
    review_pages: list = field(default_factory=list)  # nº de página
    removed: list = field(default_factory=list)  # {page, text, motivo}
    low_conf_words: list = field(default_factory=list)  # (pág, línea, palabra)
    footnotes_kept: list = field(default_factory=list)
    limitations: list = field(default_factory=list)
    seconds_per_page: float = 0.0
    ocr_calls: int = 0            # páginas realmente OCR-eadas (no caché)
    cache_hits: int = 0
    dehyphenated: int = 0         # palabras unidas por desguionado

    def to_markdown(self, book_id) -> str:
        lines = [
            f"# Informe OCR — libro {book_id}",
            "",
            f"- Páginas totales: {self.total_pages}",
            f"- Páginas por OCR: {self.ocr_pages}",
            f"- Páginas por texto: {self.text_pages}",
            f"- Confianza media global (OCR): {self.mean_conf:.1f}",
            f"- Páginas a revisar (media < {REVIEW_CONF:.0f}): "
            + (", ".join(map(str, self.review_pages)) or "ninguna"),
            f"- Segundos por página (media): {self.seconds_per_page:.2f}",
            f"- Llamadas OCR reales: {self.ocr_calls} "
            f"(caché: {self.cache_hits} páginas reutilizadas)",
            f"- Palabras desguionadas: {self.dehyphenated}",
            "",
            "## Elementos eliminados",
            "",
        ]
        if self.removed:
            for r in self.removed:
                lines.append(f"- p{r['page']}: «{r['text']}» — {r['motivo']}")
        else:
            lines.append("- Ninguno.")
        lines += ["", "## Palabras de baja confianza (conf < 40)", ""]
        if self.low_conf_words:
            for pg, ln, w in self.low_conf_words:
                lines.append(f"- p{pg}, línea {ln}: «{w}»")
        else:
            lines.append("- Ninguna.")
        lines += ["", "## Notas al pie conservadas", ""]
        if self.footnotes_kept:
            for r in self.footnotes_kept:
                lines.append(f"- p{r['page']}: «{r['text']}»")
        else:
            lines.append("- Ninguna.")
        lines += ["", "## Limitaciones aplicadas (v1)", ""]
        for lim in self.limitations:
            lines.append(f"- {lim}")
        return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# Caché por página
# ----------------------------------------------------------------------
class OcrCache:
    """Caché en disco: hash del PDF + página + (DPI, idiomas, versión
    Tesseract) -> texto crudo OCR + TSV. La segunda importación del
    mismo PDF reutiliza la caché y no re-OCRea nada."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else aurea_home() / "ocr_cache"
        self.root.mkdir(parents=True, exist_ok=True)

    def key(self, file_hash: str, page_no: int, dpi: int, lang: str) -> str:
        safe_lang = re.sub(r"[^A-Za-z0-9+]", "_", lang)
        return (f"{file_hash[:16]}_p{page_no:04d}_d{dpi}_"
                f"{safe_lang}_t{tesseract_version()}_v{CACHE_VERSION}")

    def get(self, key: str) -> dict | None:
        p = self.root / f"{key}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def put(self, key: str, data: dict) -> None:
        try:
            (self.root / f"{key}.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------
# Preprocesado
# ----------------------------------------------------------------------
def _otsu_threshold(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = gray.size
    sum_total = float(np.dot(hist, np.arange(256)))
    sum_b = 0.0
    w_b = 0.0
    max_var = -1.0
    thr = 127
    for i in range(256):
        w_b += hist[i]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += i * hist[i]
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > max_var:
            max_var, thr = var, i
    return thr


def _trim_borders(binary: np.ndarray, max_frac: float = 0.05) -> np.ndarray:
    """Quita bordes/manchas negras de escaneo en los márgenes: filas y
    columnas exteriores con más del 85% de píxeles oscuros, hasta el
    5% de la dimensión por lado."""
    h, w = binary.shape
    dark = binary < 128
    top = 0
    while top < int(h * max_frac) and dark[top].mean() > 0.85:
        top += 1
    bottom = h
    while bottom > h - int(h * max_frac) and dark[bottom - 1].mean() > 0.85:
        bottom -= 1
    left = 0
    while left < int(w * max_frac) and dark[:, left].mean() > 0.85:
        left += 1
    right = w
    while right > w - int(w * max_frac) and dark[:, right - 1].mean() > 0.85:
        right -= 1
    return binary[top:bottom, left:right]


def preprocess_image(img):
    """Grises + binarización Otsu + limpieza de bordes (PIL/numpy)."""
    from PIL import Image
    gray = img.convert("L")
    arr = np.asarray(gray)
    thr = _otsu_threshold(arr)
    binary = np.where(arr > thr, 255, 0).astype(np.uint8)
    binary = _trim_borders(binary)
    return Image.fromarray(binary, mode="L")


def deskew_image(img) -> tuple:
    """Orientación con OSD de Tesseract (--psm 0). Rota la imagen si el
    ángulo detectado supera 0.5°. Devuelve (imagen, grados_aplicados).

    OSD se equivoca en páginas reales (falso 180° en p9 del Popol Vuh:
    confianza 3.45 frente a 8.98-13.42 en las páginas bien detectadas).
    Por eso solo se rota si la confianza de orientación es >= 5.0;
    por debajo, la página se deja como está y se registra 0°."""
    try:
        import pytesseract
        osd = pytesseract.image_to_osd(img, config="--psm 0")
        m_rot = re.search(r"Rotate:\s*(\d+)", osd)
        m_conf = re.search(r"Orientation confidence:\s*([\d.]+)", osd)
        rotate = int(m_rot.group(1)) if m_rot else 0
        conf = float(m_conf.group(1)) if m_conf else 0.0
    except Exception:
        return img, 0
    if rotate == 0 or conf < OSD_MIN_CONFIDENCE:
        return img, 0
    # OSD 'rotate' es lo que hay que girar para enderezar; PIL gira en
    # sentido antihorario, Tesseract lo expresa en horario.
    return img.rotate(-rotate, expand=True, fillcolor=255), rotate


# ----------------------------------------------------------------------
# TSV -> líneas
# ----------------------------------------------------------------------
def _tsv_to_lines(tsv: dict) -> list:
    """Agrupa las palabras del TSV por (bloque, párrafo, línea)."""
    lines = {}
    n = len(tsv.get("text", []))
    for i in range(n):
        text = (tsv["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(tsv["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        key = (int(tsv["block_num"][i]), int(tsv["par_num"][i]),
               int(tsv["line_num"][i]))
        w = {
            "text": text, "conf": conf,
            "x0": int(tsv["left"][i]), "y0": int(tsv["top"][i]),
            "x1": int(tsv["left"][i]) + int(tsv["width"][i]),
            "y1": int(tsv["top"][i]) + int(tsv["height"][i]),
            "h": int(tsv["height"][i]),
        }
        lines.setdefault(key, []).append(w)
    out = []
    for key in sorted(lines):
        words = sorted(lines[key], key=lambda w: w["x0"])
        out.append({
            "text": " ".join(w["text"] for w in words),
            "x0": min(w["x0"] for w in words),
            "y0": min(w["y0"] for w in words),
            "x1": max(w["x1"] for w in words),
            "y1": max(w["y1"] for w in words),
            "words": words,
            "block": key[0], "par": key[1], "line": key[2],
        })
    return out


def _page_mean_conf(lines: list) -> float:
    confs = [w["conf"] for ln in lines for w in ln["words"] if w["conf"] >= 0]
    return float(sum(confs) / len(confs)) if confs else 0.0


def _low_conf_words(lines: list, threshold: float = LOW_WORD_CONF) -> list:
    """[(nº de línea en la página, palabra)] con conf < umbral."""
    out = []
    for li, ln in enumerate(lines, start=1):
        for w in ln["words"]:
            if 0 <= w["conf"] < threshold:
                out.append((li, w["text"]))
    return out


def detect_columns(lines: list, page_w: int) -> tuple[int, int | None]:
    """1 o 2 columnas por la posición x de las palabras: si hay una
    banda vertical vacía en el tercio central y al menos el 10% de las
    palabras a cada lado, son 2 columnas. Devuelve (n, split_x)."""
    centers = [ (w["x0"] + w["x1"]) / 2
                for ln in lines for w in ln["words"] ]
    if len(centers) < 20 or page_w <= 0:
        return 1, None
    lo, hi = int(page_w * 0.40), int(page_w * 0.60)
    left = sum(1 for c in centers if c < lo)
    right = sum(1 for c in centers if c > hi)
    if left < 0.10 * len(centers) or right < 0.10 * len(centers):
        return 1, None
    # Banda vacía más ancha dentro del tercio central.
    xs = sorted(set(int(c) for c in centers if lo <= c <= hi))
    best_gap, best_x = 0, None
    prev = lo
    for x in xs + [hi]:
        if x - prev > best_gap:
            best_gap, best_x = x - prev, (prev + x) // 2
        prev = x
    if best_gap >= 0.04 * page_w:
        return 2, best_x
    return 1, None


def order_lines(lines: list, columns: int, split_x: int | None) -> list:
    """Orden de lectura: por columna y luego por posición vertical."""
    if columns == 2 and split_x is not None:
        def key(ln):
            cx = (ln["x0"] + ln["x1"]) / 2
            return (0 if cx < split_x else 1, ln["y0"], ln["x0"])
    else:
        def key(ln):
            return (ln["y0"], ln["x0"])
    return sorted(lines, key=key)


# ----------------------------------------------------------------------
# Normalización y limpieza de texto
# ----------------------------------------------------------------------
_LIGATURES = {
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st",
    "–": "—",
}
_QUOTES = {"“": '"', "”": '"', "‘": "'", "’": "'"}


def normalize_text(text: str) -> str:
    """Ligaduras, comillas tipográficas, guiones largos a —, espacios
    múltiples a uno. Los corchetes se eliminan: ningún segmento
    importado puede contener `[` (serían marcadores)."""
    for src, dst in _LIGATURES.items():
        text = text.replace(src, dst)
    for src, dst in _QUOTES.items():
        text = text.replace(src, dst)
    text = text.replace("[", "").replace("]", "")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def dehyphenate_lines(line_texts: list[str]) -> tuple[list[str], int]:
    """Palabra terminada en `-` al final de línea cuya continuación
    empieza en minúscula -> se une sin guion ni espacio. Si la
    continuación empieza en mayúscula, se deja como está.
    Une en cadena: la línea resultante puede terminar a su vez en
    guion (dos cortes seguidos, p. ej. Gua-/temala, na-/cional,
    elocuen-/cia) y se vuelve a unir sin avanzar el índice.
    Devuelve (líneas, nº de uniones)."""
    lines = list(line_texts)
    joins = 0
    i = 0
    while i < len(lines) - 1:
        cur = lines[i]
        nxt = lines[i + 1].lstrip()
        if cur.endswith("-") and nxt and nxt[0].islower():
            first, _, rest = nxt.partition(" ")
            lines[i] = cur[:-1] + first
            joins += 1
            if rest:
                lines[i + 1] = rest
            else:
                del lines[i + 1]
            # Sin avanzar i: la línea unida puede terminar en guion.
            continue
        i += 1
    return lines, joins


def _norm_key(text: str) -> str:
    """Contenido normalizado para comparar encabezados/pies."""
    t = text.lower()
    t = re.sub(r"[^a-z0-9áéíóúñü ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_PAGE_NUM_RE = re.compile(r"^[\W_]*\d{1,4}[\W_]*$")


def _zone(y_center: float, page_h: int) -> str | None:
    if y_center < page_h * ZONE_TOP:
        return "top"
    if y_center > page_h * (1 - ZONE_BOTTOM):
        return "bottom"
    return None


def clean_pages(pages: list[OcrPageResult], page_h: int) -> tuple[list, int]:
    """Limpieza quirúrgica en dos pasadas sobre las páginas OCR:
    encabezados/pies repetidos (misma zona, mismo contenido, 3+
    páginas consecutivas) y números de página se eliminan y se
    registran. Las notas al pie se conservan y se registran.
    Devuelve (elementos eliminados, nº de palabras desguionadas)."""
    removed = []
    dehyph = 0
    ocr_pages = [p for p in pages if p.method == "OCR"]
    if not ocr_pages:
        return removed, dehyph

    # --- Pasada 1: candidatos de encabezado/pie por (zona, contenido) ---
    occurrences: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for p in ocr_pages:
        ph = p.img_height or page_h  # alto de la imagen OCR-eada
        for li, ln in enumerate(p.lines):
            yc = (ln["y0"] + ln["y1"]) / 2
            zone = _zone(yc, ph)
            if zone is None:
                continue
            key = _norm_key(ln["text"])
            if len(key) < 3:
                continue
            occurrences.setdefault((zone, key), []).append((p.page_no, li))

    repeated: dict[tuple[str, str], set[int]] = {}
    for (zone, key), occ in occurrences.items():
        pages_nos = sorted({pg for pg, _ in occ})
        # 3+ páginas consecutivas con el mismo contenido en la misma zona.
        run, best = 1, 1
        for a, b in zip(pages_nos, pages_nos[1:]):
            run = run + 1 if b == a + 1 else 1
            best = max(best, run)
        if best >= REPEAT_PAGES:
            repeated[(zone, key)] = {pg for pg, _ in occ}

    # --- Pasada 2: eliminar + registrar, reconstruir el texto ---
    for p in ocr_pages:
        ph = p.img_height or page_h  # alto de la imagen OCR-eada
        n_lines = len(p.lines)
        keep = []
        # Altura mediana del cuerpo para detectar notas al pie.
        body_h = [w["h"] for ln in p.lines for w in ln["words"]
                  if _zone((ln["y0"] + ln["y1"]) / 2, ph) is None]
        body_h.sort()
        median_h = body_h[len(body_h) // 2] if body_h else 0
        for li, ln in enumerate(p.lines):
            text = ln["text"]
            yc = (ln["y0"] + ln["y1"]) / 2
            zone = _zone(yc, ph)
            # Número de página: solo dígitos en el 10% sup/inf, O la
            # última línea de la página aunque caiga fuera de la zona:
            # los escaneos traen márgenes blancos anchos y el número
            # impreso queda a ~85% de la altura del escaneo.
            if _PAGE_NUM_RE.match(text.strip()) and (
                    zone is not None or li == n_lines - 1):
                removed.append({"page": p.page_no, "text": text,
                                "motivo": "número de página"})
                p.removed.append({"text": text,
                                  "motivo": "número de página"})
                continue
            if zone is not None:
                if (zone, _norm_key(text)) in repeated:
                    motivo = ("encabezado repetido" if zone == "top"
                              else "pie repetido")
                    removed.append({"page": p.page_no, "text": text,
                                    "motivo": motivo})
                    p.removed.append({"text": text, "motivo": motivo})
                    continue
            # Nota al pie: bloque final con cuerpo menor -> se conserva
            # como párrafo normal al final de la página y se registra.
            if (zone == "bottom" and median_h
                    and ln["words"]
                    and (sum(w["h"] for w in ln["words"]) / len(ln["words"])
                         < median_h * FOOTNOTE_HEIGHT_RATIO)
                    and yc > ph * (1 - FOOTNOTE_ZONE)):
                p.footnotes_kept.append({"page": p.page_no, "text": text})
            keep.append(ln)
        p.lines = keep
        clean_lines, n_joins = dehyphenate_lines(
            [normalize_text(ln["text"]) for ln in keep])
        dehyph += n_joins
        p.text = "\n".join(t for t in clean_lines if t)
    return removed, dehyph


# ----------------------------------------------------------------------
# OCR por página
# ----------------------------------------------------------------------
def _ocr_tsv(img, lang: str, psm: int) -> dict:
    import pytesseract
    return pytesseract.image_to_data(
        img, lang=lang, config=f"--psm {psm}",
        output_type=pytesseract.Output.DICT)


def ocr_page_image(img, lang: str, page_w: int, page_h: int) -> dict:
    """Pasa el OCR a una imagen ya preprocesada. Devuelve el resultado
    crudo (líneas, psm, columnas, confianza) para caché y limpieza."""
    # PSM 6 por defecto; 3 como fallback si sale muy poco texto.
    tsv = _ocr_tsv(img, lang, 6)
    lines = _tsv_to_lines(tsv)
    words = sum(len(ln["words"]) for ln in lines)
    psm = 6
    if words < 5 or _page_mean_conf(lines) < 30:
        tsv3 = _ocr_tsv(img, lang, 3)
        lines3 = _tsv_to_lines(tsv3)
        words3 = sum(len(ln["words"]) for ln in lines3)
        if words3 > words:
            tsv, lines, psm = tsv3, lines3, 3
    columns, split_x = detect_columns(lines, page_w)
    if columns == 2:
        # PSM 4 si hay 2 columnas; se queda solo si no pierde palabras
        # Y no fusiona las columnas en una sola línea (en este caso
        # PSM 6 separa mejor y se conserva, con el PSM elegido en el log).
        tsv4 = _ocr_tsv(img, lang, 4)
        lines4 = _tsv_to_lines(tsv4)
        words4 = sum(len(ln["words"]) for ln in lines4)
        merged = any(ln["x0"] < split_x < ln["x1"] for ln in lines4)
        if words4 >= 0.9 * max(words, 1) and not merged:
            tsv, lines, psm = tsv4, lines4, 4
    lines = order_lines(lines, columns, split_x)
    return {
        "psm": psm, "columns": columns,
        "mean_conf": _page_mean_conf(lines),
        "lines": lines,
        "low_conf": _low_conf_words(lines),
        "img_h": img.height,
    }


def _lines_to_json(lines: list) -> list:
    out = []
    for ln in lines:
        out.append({
            "text": ln["text"], "x0": ln["x0"], "y0": ln["y0"],
            "x1": ln["x1"], "y1": ln["y1"],
            "words": [{"text": w["text"], "conf": w["conf"],
                       "x0": w["x0"], "y0": w["y0"], "x1": w["x1"],
                       "y1": w["y1"], "h": w["h"]} for w in ln["words"]],
        })
    return out


def _lines_from_json(data: list) -> list:
    out = []
    for ln in data:
        words = [dict(w) for w in ln["words"]]
        out.append({**{k: v for k, v in ln.items() if k != "words"},
                    "words": words})
    return out


# ----------------------------------------------------------------------
# Lectura completa del PDF
# ----------------------------------------------------------------------
def scan_pdf_pages(path: str) -> list:
    """Pre-escaneo rápido (solo capa de texto): por página, si es
    escaneada. No hace OCR. Devuelve [{page, text_chars, text_words,
    scanned}]."""
    import pymupdf
    doc = pymupdf.open(path)
    out = []
    for i, page in enumerate(doc):
        text = (page.get_text() or "").strip()
        words = len(text.split())
        out.append({
            "page": i + 1, "text_chars": len(text), "text_words": words,
            "scanned": len(text) < MIN_TEXT_CHARS or words < MIN_TEXT_WORDS,
        })
    doc.close()
    return out


def read_pdf_ocr(path: str, lang: str = DEFAULT_LANG, dpi: int = DEFAULT_DPI,
                 progress_cb=None, cancel_cb=None,
                 cache_root: str | Path | None = None,
                 page_range: tuple[int, int] | None = None):
    """Lee un PDF página a página: texto donde hay capa, OCR donde no.
    `page_range` (1-based, inclusivo) limita las páginas procesadas
    (verificación de muestras: el libro completo se importa entero).
    Devuelve (capítulos [(título, texto)], informe OcrReport,
    [OcrPageResult]). Lanza OcrCancelled si cancel_cb() es True."""
    import pymupdf
    ok, why = ocr_available()
    if not ok:
        raise OcrUnavailable(why)
    t_start = time.time()
    doc = pymupdf.open(path)
    n_doc = len(doc)
    if n_doc == 0:
        doc.close()
        raise ValueError("El PDF no tiene páginas.")
    if page_range is None:
        indices = list(range(n_doc))
    else:
        lo = max(0, page_range[0] - 1)
        hi = min(n_doc, page_range[1])
        indices = list(range(lo, hi))
    total = len(indices)
    fhash = file_hash(path)
    cache = OcrCache(cache_root)
    pages: list[OcrPageResult] = []
    ocr_calls = 0
    cache_hits = 0
    # Alto del render en píxeles (igual para todas las páginas del PDF):
    # las zonas del 10% de limpieza se calculan sobre este alto.
    page_h = max(1, int(doc[0].rect.height * dpi / 72))
    try:
        for i in indices:
            page = doc[i]
            if cancel_cb and cancel_cb():
                raise OcrCancelled(f"Cancelado en la página {i + 1}/{n_doc}.")
            text_layer = (page.get_text() or "").strip()
            words = len(text_layer.split())
            if len(text_layer) >= MIN_TEXT_CHARS and words >= MIN_TEXT_WORDS:
                pages.append(OcrPageResult(
                    page_no=i + 1, method="texto",
                    text=re.sub(r"\n{3,}", "\n\n", text_layer),
                    mean_conf=100.0, render_dpi=dpi))
            else:
                key = cache.key(fhash, i + 1, dpi, lang)
                raw = cache.get(key)
                if raw is not None:
                    cache_hits += 1
                    lines = _lines_from_json(raw["lines"])
                    result = OcrPageResult(
                        page_no=i + 1, method="OCR", text="",
                        mean_conf=raw["mean_conf"], psm=raw["psm"],
                        columns=raw["columns"],
                        low_conf_words=[tuple(x) for x in raw["low_conf"]],
                        render_dpi=dpi, lines=lines,
                        img_height=raw.get("img_h", 0))
                else:
                    pix = page.get_pixmap(
                        matrix=pymupdf.Matrix(dpi / 72, dpi / 72))
                    from PIL import Image
                    img = Image.frombytes("RGB", [pix.width, pix.height],
                                          pix.samples)
                    gray = img.convert("L")
                    img2, rot = deskew_image(gray)
                    binary = preprocess_image(img2)
                    raw_res = ocr_page_image(binary, lang,
                                             binary.width, binary.height)
                    result = OcrPageResult(
                        page_no=i + 1, method="OCR", text="",
                        mean_conf=raw_res["mean_conf"], psm=raw_res["psm"],
                        columns=raw_res["columns"],
                        low_conf_words=raw_res["low_conf"],
                        render_dpi=dpi, deskew_deg=rot,
                        lines=raw_res["lines"],
                        img_height=raw_res["img_h"])
                    cache.put(key, {
                        "psm": raw_res["psm"], "columns": raw_res["columns"],
                        "mean_conf": raw_res["mean_conf"],
                        "lines": _lines_to_json(raw_res["lines"]),
                        "low_conf": raw_res["low_conf"],
                        "img_h": raw_res["img_h"],
                    })
                    ocr_calls += 1
                result.needs_review = result.mean_conf < REVIEW_CONF
                pages.append(result)
            if progress_cb:
                progress_cb(i + 1, total, pages[-1].method)
        # Limpieza quirúrgica sobre las páginas OCR (dos pasadas).
        removed, n_dehyph = clean_pages(pages, page_h)
        # Texto final por página (las de texto ya lo traen; clean_pages
        # ya fijó el de las OCR, esto es solo un seguro).
        for p in pages:
            if p.method == "OCR" and not p.text:
                lines, _ = dehyphenate_lines(
                    [normalize_text(ln["text"]) for ln in p.lines])
                p.text = "\n".join(t for t in lines if t)
        full_text = "\n\n".join(p.text for p in pages if p.text.strip())
        if not full_text.strip():
            raise ValueError("No se encontró texto importable en el archivo.")
        ocr_ps = [p for p in pages if p.method == "OCR"]
        mean_conf = (sum(p.mean_conf for p in ocr_ps) / len(ocr_ps)
                     if ocr_ps else 100.0)
        elapsed = time.time() - t_start
        report = OcrReport(
            total_pages=total, ocr_pages=len(ocr_ps),
            text_pages=total - len(ocr_ps), mean_conf=mean_conf,
            review_pages=[p.page_no for p in ocr_ps if p.needs_review],
            removed=removed,
            low_conf_words=[(p.page_no, ln, w)
                            for p in ocr_ps for ln, w in p.low_conf_words],
            footnotes_kept=[f for p in ocr_ps for f in p.footnotes_kept],
            limitations=[
                "Tablas: se aplanan fila por fila; no se reconstruyen celdas.",
                "Imágenes: no se describen; los pies de foto se conservan "
                "como texto normal.",
                "Deskew: solo giros de 90°/180°/270° detectados por OSD; "
                "la inclinación fina (<90°) no se corrige en v1.",
            ],
            seconds_per_page=elapsed / max(total, 1),
            ocr_calls=ocr_calls, cache_hits=cache_hits,
            dehyphenated=n_dehyph,
        )
        title = Path(path).stem[:80]
        return [(title, full_text)], report, pages
    finally:
        doc.close()


def write_ocr_report(report: OcrReport, book_id, out_dir: str | Path) -> Path:
    """Escribe output/ocr_<book_id>_reporte.md y devuelve su ruta."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"ocr_{book_id}_reporte.md"
    path.write_text(report.to_markdown(book_id), encoding="utf-8")
    return path
