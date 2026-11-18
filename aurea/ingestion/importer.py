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


def import_book(path: str):
    """Devuelve (título_del_libro, [(título_capítulo, texto), ...])."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext not in READERS:
        raise ValueError(f"Formato no soportado: {ext} (usa TXT, EPUB o PDF)")
    chapters = READERS[ext]().read(str(p))
    if not chapters:
        raise ValueError("No se encontró texto importable en el archivo.")
    return p.stem, chapters
