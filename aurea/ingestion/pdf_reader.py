from pathlib import Path

from .base import BookReader


class PdfBookReader(BookReader):
    """Extrae texto de PDFs con capa de texto. Los escaneos requieren OCR
    (pendiente para una fase posterior)."""

    def read(self, path):
        from pypdf import PdfReader

        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(text)
        if not pages:
            raise ValueError(
                "El PDF no contiene texto extraíble (parece un escaneo). "
                "El OCR llegará en una fase posterior de AUREA.")
        return [(Path(path).stem[:80], "\n\n".join(pages))]
