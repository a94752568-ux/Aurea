"""Importación OCR en segundo plano (QThread, como el generador).

Regla AUREA: OCR y TTS nunca a la vez. La UI usa el mismo guarda
único `active_worker` para este worker, el generador y el director:
si uno está activo, los demás esperan con mensaje.
La cancelación es real (flag comprobado entre páginas) y reanudable:
lo ya procesado queda en la caché por página.
"""
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..ingestion.ocr_reader import OcrCancelled
from ..ingestion.pdf_reader import PdfBookReader


class ImportWorker(QThread):
    """Importa un PDF (con OCR si hace falta) sin bloquear la UI."""
    progress = Signal(int, str)
    log = Signal(str)          # log por su propia señal (M7)
    failed = Signal(str)
    cancelled = Signal()       # el usuario pidió parar
    # (título, capítulos, informe OCR, [OcrPageResult])
    finished_ok = Signal(str, list, object, object)

    def __init__(self, path, lang="spa+eng", dpi=300, parent=None):
        super().__init__(parent)
        self.path = str(path)
        self.lang = lang
        self.dpi = dpi
        self._cancel_requested = False
        self._pause_event = threading.Event()  # set = en pausa

    def request_cancel(self):
        self._cancel_requested = True
        self._pause_event.clear()

    def request_pause(self):
        self._pause_event.set()

    def request_resume(self):
        self._pause_event.clear()

    @property
    def paused(self):
        return self._pause_event.is_set()

    def _wait_if_paused(self):
        while self._pause_event.is_set() and not self._cancel_requested:
            time.sleep(0.1)

    def _on_page(self, page_no, total, method):
        self._wait_if_paused()
        pct = int(page_no * 100 / max(total, 1))
        self.progress.emit(pct, f"Página {page_no}/{total} · {method}")
        self.log.emit(f"Página {page_no}/{total}: {method}")

    def run(self):
        try:
            reader = PdfBookReader(
                lang=self.lang, dpi=self.dpi,
                progress_cb=self._on_page,
                cancel_cb=lambda: self._cancel_requested)
            chapters = reader.read(self.path)
            if self._cancel_requested:
                self.cancelled.emit()
                return
            self.finished_ok.emit(Path(self.path).stem, chapters,
                                  reader.report, reader.page_log)
        except OcrCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
