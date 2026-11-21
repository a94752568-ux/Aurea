from pathlib import Path

from .base import BookReader


class PdfBookReader(BookReader):
    """Extrae texto de PDFs, página a página: capa de texto donde la
    hay, OCR local (Tesseract) donde es un escaneo (v0.6.0).

    Sin las dependencias OCR (PyMuPDF/pytesseract/Tesseract) conserva
    el comportamiento anterior: solo capa de texto con pypdf, y un
    escaneo lanza ValueError.
    """

    def __init__(self, lang="spa+eng", dpi=300, progress_cb=None,
                 cancel_cb=None, cache_root=None):
        from .ocr_reader import DEFAULT_DPI, DEFAULT_LANG
        self.lang = lang or DEFAULT_LANG
        self.dpi = dpi or DEFAULT_DPI
        self.progress_cb = progress_cb
        self.cancel_cb = cancel_cb
        self.cache_root = cache_root
        # Estado tras read(): informe y detalle por página (la UI los
        # lee para mostrar la calidad y escribir el reporte).
        self.report = None
        self.page_log = []
        self.ocr_calls = 0
        self.cache_hits = 0

    def read(self, path):
        try:
            import pymupdf  # noqa: F401
        except ImportError:
            return self._read_pypdf(path)
        from .ocr_reader import OcrUnavailable, read_pdf_ocr
        try:
            chapters, report, pages = read_pdf_ocr(
                path, lang=self.lang, dpi=self.dpi,
                progress_cb=self.progress_cb, cancel_cb=self.cancel_cb,
                cache_root=self.cache_root)
        except OcrUnavailable:
            # Motor no disponible: cae a la ruta antigua (texto o error).
            return self._read_pypdf(path)
        self.report = report
        self.page_log = pages
        self.ocr_calls = report.ocr_calls
        self.cache_hits = report.cache_hits
        return chapters

    def _read_pypdf(self, path):
        """Ruta antigua (v0.5.0): solo capa de texto con pypdf."""
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
                "Instala el OCR local: apt install tesseract-ocr "
                "tesseract-ocr-spa y pip install pymupdf pillow pytesseract.")
        return [(Path(path).stem[:80], "\n\n".join(pages))]

    @staticmethod
    def scan_pdf(path):
        """Pre-escaneo rápido (solo capa de texto, sin OCR): por página,
        si es escaneada. Lo usa la UI para decidir si muestra el diálogo
        de opciones OCR. Devuelve [] si PyMuPDF no está disponible."""
        try:
            from .ocr_reader import scan_pdf_pages
        except ImportError:
            return []
        try:
            return scan_pdf_pages(path)
        except Exception:
            return []
