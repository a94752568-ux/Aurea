"""Tests OCR v0.6.0 (fixture SINTÉTICO + regresión + caché).

El fixture es un PDF de 6 páginas construido con PIL + PyMuPDF
(ninguna imagen real): p1 con capa de texto; p2-p6 escaneadas
(imágenes de texto renderizado), con encabezado repetido, números
de página, palabras guionadas, una página a 2 columnas y una
página rotada 180° (deskew). Etiquetado SINTÉTICO en el informe.

La verificación REAL con la muestra de Alberto (Popol Vuh, págs.
1-10) vive en scripts/verificar_ocr_popolvuh.py, no aquí: el PDF
real no pertenece al repo.

Ejecutar: QT_QPA_PLATFORM=offscreen pytest tests/test_ocr.py -v
"""
import os
import sys
import types
from pathlib import Path

# Stubs de dependencias pesadas (como tests/test_aurea.py): la VM no
# tiene Kokoro/XTTS reales; generator.py solo se importa, no se carga.
_kokoro = types.ModuleType("kokoro_onnx")
_kokoro.Kokoro = object
sys.modules.setdefault("kokoro_onnx", _kokoro)
_misaki = types.ModuleType("misaki")
_espeak = types.ModuleType("misaki.espeak")
_espeak.EspeakG2P = object
_espeak.EspeakFallback = object
_misaki.espeak = _espeak
sys.modules.setdefault("misaki", _misaki)
sys.modules.setdefault("misaki.espeak", _espeak)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
HEADER = "EL QUINTO SOL · CAPÍTULO PRIMERO"

BODY_P2 = [
    "En el principio, cuando el mundo era joven y los dioses",
    "todavía caminaban entre los hombres, existía un reino",
    "de piedra y de jade donde la sabiduría se guardaba en",
    "códices que solo los sacerdotes sabían leer. El conoci-",
    "miento de los antiguos era un tesoro que se transmitía",
    "de boca en boca, de generación en generación, sin que",
    "nada se perdiera en el camino de las palabras.",
    "Los ancianos contaban que el sol nacía cada mañana",
    "del vientre de la tierra y moría cada noche en el mar,",
    "y que los hombres debían agradecer con ofrendas de",
    "maíz y de flores el milagro de un nuevo amanecer.",
]
BODY_P3 = [
    "La ﬁesta del maíz reunía a todo el pueblo en la plaza",
    "mayor, donde los tambores marcaban el ritmo de la",
    "danza y los cantos subían hasta las estrellas. Nadie",
    "dormía esa noche: los niños corrían entre las antorchas",
    "y los viejos recordaban los tiempos en que los dioses",
    "bajaban a compartir el pan con los mortales.",
    "Al amanecer, el sacerdote mayor alzaba los brazos y",
    "pedía por la lluvia, por la cosecha y por la vida de",
    "todos los que allí se habían reunido a celebrar.",
]
BODY_P4_LEFT = [
    "Columna izquierda: el",
    "río bajaba de la montaña",
    "con furia de toro bravo,",
    "arrastrando piedras y",
    "troncos en su camino.",
    "Los pescadores miraban",
    "desde la orilla sin",
    "atreverse a lanzar la red.",
]
BODY_P4_RIGHT = [
    "Columna derecha: en la",
    "otra orilla, las mujeres",
    "lavaban la ropa cantando",
    "canciones viejas que sus",
    "madres les enseñaron de",
    "niñas, bajo el sol de",
    "la tarde que ya caía.",
]
BODY_P5 = [
    "Esta página está girada ciento ochenta grados en el",
    "documento original, como un escaneo mal colocado en",
    "el alimentador. El OCR debe enderezarla solo con la",
    "orientación de Tesseract y leer su contenido bien.",
    "Si el deskew falla, esta página saldrá con el texto",
    "al revés o con confianza muy baja en el informe.",
]
BODY_P6 = [
    "Última página del libro de prueba. Aquí termina la",
    "historia que los ancianos contaron junto al fuego,",
    "y el escriba cierra su códice con una última línea",
    "para quien venga después a leer estas palabras.",
]


def _render_page(lines, header=True, page_no=None, two_col=None,
                 footnote=None, rotate=0, size=(2480, 3508)):
    """Renderiza una página escaneada (imagen PIL, blanco sobre negro)."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    f_body = ImageFont.truetype(FONT, 44)
    f_small = ImageFont.truetype(FONT, 28)
    y = 120
    if header:
        d.text((size[0] // 2, y), HEADER, font=f_body, fill="black",
               anchor="ma")
        y = 400
    if two_col:
        left, right = two_col
        ly = y
        for ln in left:
            d.text((200, ly), ln, font=f_body, fill="black")
            ly += 64
        ry = y
        for ln in right:
            d.text((1330, ry), ln, font=f_body, fill="black")
            ry += 64
    else:
        for ln in lines:
            d.text((200, y), ln, font=f_body, fill="black")
            y += 64
    if footnote:
        d.text((200, 3100), footnote, font=f_small, fill="black")
    if page_no is not None:
        d.text((size[0] // 2, 3380), str(page_no), font=f_body,
               fill="black", anchor="ma")
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white")
    return img


def build_fixture(path: Path) -> Path:
    """PDF sintético de 6 páginas (1 de texto + 5 escaneadas)."""
    import pymupdf
    doc = pymupdf.open()
    # p1: capa de texto real.
    p1 = doc.new_page(width=595, height=842)
    y = 72
    for ln in ["Página con capa de texto real, sin OCR.",
               "El Popol Vuh es el libro sagrado de los mayas quichés.",
               "Aquí el texto vive en el PDF y se extrae directo.",
               "Ninguna de estas líneas debe pasar por Tesseract."]:
        p1.insert_text((72, y), ln, fontsize=12, fontname="helv")
        y += 20
    pages = [
        _render_page(BODY_P2, page_no=2, footnote=(
            "Nota del escriba: los nombres se conservan como en el original.")),
        _render_page(BODY_P3, page_no=3),
        _render_page([], page_no=4, two_col=(BODY_P4_LEFT, BODY_P4_RIGHT)),
        _render_page(BODY_P5, page_no=5, rotate=180),
        _render_page(BODY_P6, page_no=6),
    ]
    for img in pages:
        pg = doc.new_page(width=595, height=842)
        # JPEG (el Pillow del venv no trae el codificador PNG; la
        # calidad 95 apenas afecta al OCR del texto limpio).
        import io
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=95)
        pg.insert_image(pg.rect, stream=buf.getvalue())
    doc.save(str(path))
    doc.close()
    return path


def build_text_pdf(path: Path, pages: int = 3) -> Path:
    """PDF puro de texto (regresión: 0 páginas por OCR)."""
    import pymupdf
    doc = pymupdf.open()
    for i in range(pages):
        pg = doc.new_page(width=595, height=842)
        y = 72
        for ln in [f"Capítulo {i + 1} de un libro con texto real.",
                   "Cada página tiene su capa de texto completa y legible.",
                   "El OCR no debe activarse en ninguna página."]:
            pg.insert_text((72, y), ln, fontsize=12, fontname="helv")
            y += 20
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def fixture_pdf(tmp_path):
    return build_fixture(tmp_path / "sintetico.pdf")


@pytest.fixture()
def text_pdf(tmp_path):
    return build_text_pdf(tmp_path / "texto.pdf")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """AUREA_HOME temporal: BD y caché no tocan producción."""
    h = tmp_path / "aurea_home"
    h.mkdir()
    monkeypatch.setenv("AUREA_HOME", str(h))
    return h


@pytest.fixture(scope="module")
def ocr_run(tmp_path_factory):
    """Una sola pasada OCR sobre el fixture, compartida por los tests
    que solo inspeccionan el resultado (el OCR es lo lento)."""
    base = tmp_path_factory.mktemp("ocr_run")
    home_dir = base / "aurea_home"
    home_dir.mkdir()
    os.environ["AUREA_HOME"] = str(home_dir)
    pdf = build_fixture(base / "sintetico.pdf")
    from aurea.ingestion.ocr_reader import read_pdf_ocr
    chapters, report, pages = read_pdf_ocr(str(pdf), dpi=300)
    yield {"pdf": pdf, "chapters": chapters, "report": report,
           "pages": pages, "home": home_dir}
    os.environ.pop("AUREA_HOME", None)


def test_scan_detection_mixed(fixture_pdf):
    from aurea.ingestion.ocr_reader import scan_pdf_pages
    scan = scan_pdf_pages(str(fixture_pdf))
    assert len(scan) == 6
    assert scan[0]["scanned"] is False  # p1 tiene capa de texto
    assert all(s["scanned"] for s in scan[1:])


def test_ocr_import_pages(ocr_run):
    report, pages = ocr_run["report"], ocr_run["pages"]
    chapters = ocr_run["chapters"]
    assert report.total_pages == 6
    assert report.ocr_pages == 5
    assert report.text_pages == 1
    assert len(chapters) == 1
    assert len(chapters[0][1]) > 500
    # Confianza global razonable en texto sintético limpio.
    assert report.mean_conf > 70


def test_surgical_header_footer_removed(ocr_run):
    chapters, report = ocr_run["chapters"], ocr_run["report"]
    text = chapters[0][1]
    assert "EL QUINTO SOL" not in text  # encabezado repetido fuera
    headers = [r for r in report.removed if r["motivo"] == "encabezado repetido"]
    assert len(headers) >= 4  # en las 5 páginas escaneadas
    pagenums = [r for r in report.removed if r["motivo"] == "número de página"]
    assert len(pagenums) == 5  # uno por página escaneada
    # Ningún número de página suelto queda como línea en el texto.
    for ln in text.splitlines():
        assert ln.strip() not in {"2", "3", "4", "5", "6"}


def test_dehyphenation(ocr_run):
    text = ocr_run["chapters"][0][1]
    assert "conocimiento" in text
    assert "conoci-" not in text


def test_dehyphenation_chained():
    # Regresión (Popol Vuh p5): dos cortes seguidos (Gua-/temala,
    # na-/cional, elocuen-/cia) — la unión en cadena no debe dejar
    # el segundo guion. Antes del fix, "elocuen-" sobrevivía.
    from aurea.ingestion.ocr_reader import dehyphenate_lines
    lines = [
        "De todas los pueblos americanos, los quichés de Gua-",
        "temala son los que nes han dejado el más rico legado",
        "tnitolágico. Su descripción de la excación, según apar",
        "rece en el Popp! Poa, que puede Namarse el libro na-",
        "cional de los quichés, es, en so ruda y extraña elocuen-",
        "cia y poética originalidad, una de las más raras reliquias",
        "del pensamiento aborigen.",
    ]
    out, joins = dehyphenate_lines(lines)
    assert joins == 3
    text = "\n".join(out)
    assert "Guatemala" in text
    assert "nacional" in text
    assert "elocuencia" in text
    assert "-\n" not in text
    # La continuación en mayúscula NO se une.
    out2, joins2 = dehyphenate_lines(["palabra-", "Mayúscula sigue"])
    assert joins2 == 0
    assert out2 == ["palabra-", "Mayúscula sigue"]


def test_no_brackets_in_segments(ocr_run):
    from aurea.core.generator import segmentize
    parts = segmentize(ocr_run["chapters"][0][1], use_cast=False)
    assert parts, "el texto OCR debe segmentarse"
    for kind, speaker, chunk, pause in parts:
        assert "[" not in chunk and "]" not in chunk


def test_normalize():
    from aurea.ingestion.ocr_reader import normalize_text
    assert normalize_text("La ﬁesta ﬂoreció") == "La fiesta floreció"
    assert normalize_text("“hola” — dijo") == '"hola" — dijo'
    assert normalize_text("a   b\t c") == "a b c"
    assert normalize_text("texto [pausa] resto") == "texto pausa resto"


def test_confidence_review_flag():
    from aurea.ingestion.ocr_reader import (
        _low_conf_words, _page_mean_conf, _tsv_to_lines)
    tsv = {
        "text": ["clara", "dudosa", "otra"],
        "conf": ["95", "25", "88"],
        "block_num": [1, 1, 1], "par_num": [1, 1, 1],
        "line_num": [1, 1, 1], "left": [0, 50, 100],
        "top": [0, 0, 0], "width": [40, 40, 40], "height": [20, 20, 20],
    }
    lines = _tsv_to_lines(tsv)
    assert 60 < _page_mean_conf(lines) < 70  # < 70 -> revisar
    assert _low_conf_words(lines) == [(1, "dudosa")]


def test_two_columns_detected(ocr_run):
    pages = ocr_run["pages"]
    p4 = pages[3]  # página 4: dos columnas
    assert p4.columns == 2
    assert p4.psm in (4, 6)
    assert "río bajaba" in p4.text and "otra orilla" in p4.text


def test_deskew_rotated_page(ocr_run):
    pages = ocr_run["pages"]
    p5 = pages[4]  # página 5: girada 180°
    assert p5.deskew_deg in (180, -180)
    assert "alimentador" in p5.text or "ciento ochenta" in p5.text


def test_cache_second_import_no_ocr(fixture_pdf, home, capsys):
    from aurea.ingestion.ocr_reader import read_pdf_ocr
    _, rep1, _ = read_pdf_ocr(str(fixture_pdf), dpi=300)
    assert rep1.ocr_calls == 5
    _, rep2, _ = read_pdf_ocr(str(fixture_pdf), dpi=300)
    assert rep2.ocr_calls == 0  # nada re-OCReado
    assert rep2.cache_hits == 5
    assert rep2.mean_conf == pytest.approx(rep1.mean_conf, abs=0.5)


def test_regression_text_pdf(text_pdf, home):
    from aurea.ingestion.ocr_reader import read_pdf_ocr
    chapters, report, _ = read_pdf_ocr(str(text_pdf), dpi=300)
    assert report.ocr_pages == 0
    assert report.text_pages == 3
    assert "capa de texto completa" in chapters[0][1]


def test_db_ocr_pages_migration(home):
    from aurea.core.database import Database
    db = Database()
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(ocr_pages)")}
    assert {"book_id", "page_no", "method", "mean_confidence",
            "psm", "columns", "needs_review"} <= cols
    book_id = db.add_book("Prueba", "/tmp/x.pdf", "pdf")
    db.set_ocr_pages(book_id, [
        {"page_no": 1, "method": "OCR", "mean_confidence": 82.5,
         "psm": 6, "columns": 1, "needs_review": 0},
        {"page_no": 2, "method": "texto", "mean_confidence": 100.0,
         "psm": None, "columns": 1, "needs_review": 0},
    ])
    rows = db.ocr_pages(book_id)
    assert len(rows) == 2
    assert rows[0]["mean_confidence"] == 82.5
    assert rows[1]["method"] == "texto"
    db.close()


def test_report_written(ocr_run, tmp_path):
    from aurea.ingestion.ocr_reader import write_ocr_report
    out = write_ocr_report(ocr_run["report"], 7, tmp_path / "output")
    md = out.read_text(encoding="utf-8")
    assert "Páginas por OCR: 5" in md
    assert "Elementos eliminados" in md
    assert "Limitaciones aplicadas" in md
    assert "Tablas" in md


def test_importer_ocr_interface_unchanged(fixture_pdf, home):
    """import_book(path) sigue funcionando para un PDF escaneado."""
    from aurea.ingestion.importer import import_book
    title, chapters = import_book(str(fixture_pdf))
    assert title == "sintetico"
    assert len(chapters) == 1
    assert import_book.last_ocr_report is not None
    assert import_book.last_ocr_report.ocr_pages == 5
