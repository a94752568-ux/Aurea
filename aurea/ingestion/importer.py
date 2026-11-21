from pathlib import Path

from .epub_reader import EpubReader
from .md_reader import MarkdownReader
from .pdf_reader import PdfBookReader
from .text_reader import TextReader

READERS = {
    ".txt": TextReader,
    ".md": MarkdownReader,
    ".epub": EpubReader,
    ".pdf": PdfBookReader,
}


def import_book(path: str, ocr_lang: str = "spa+eng", ocr_dpi: int = 300,
                ocr_progress=None, ocr_cancel=None, ocr_cache_root=None):
    """Devuelve (título_del_libro, [(título_capítulo, texto), ...]).
    Los kwargs ocr_* solo afectan a los PDF y son aditivos: la
    interfaz anterior import_book(path) sigue igual. Tras importar
    un PDF, el informe OCR queda en import_book.last_ocr_report
    (None si no hubo OCR)."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext not in READERS:
        raise ValueError(f"Formato no soportado: {ext} (usa TXT, MD, EPUB o PDF)")
    if ext == ".pdf":
        reader = PdfBookReader(lang=ocr_lang, dpi=ocr_dpi,
                               progress_cb=ocr_progress, cancel_cb=ocr_cancel,
                               cache_root=ocr_cache_root)
        chapters = reader.read(str(p))
        import_book.last_ocr_report = reader.report
        import_book.last_ocr_pages = reader.page_log
    else:
        chapters = READERS[ext]().read(str(p))
        import_book.last_ocr_report = None
        import_book.last_ocr_pages = []
    if not chapters:
        raise ValueError("No se encontró texto importable en el archivo.")
    return p.stem, chapters


import_book.last_ocr_report = None
import_book.last_ocr_pages = []
