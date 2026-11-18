"""Adaptador Qt del Director Híbrido (refactor Electron).

La lógica vive en direction/engine.py (Qt-free); este módulo conserva la
API QThread para main_window.py.
"""
from PySide6.QtCore import QThread, Signal

from .engine import run_direction  # noqa: F401  (reexporta la API Qt-free)


class DirectionWorker(QThread):
    """1) Reglas: todo lo claro, al instante. 2) Ollama (opcional): solo
    narración ambigua."""
    progress = Signal(int, str)
    failed = Signal(str)
    finished_ok = Signal(int, int)   # (segmentos por reglas, refinados por LLM)

    def __init__(self, chapter_id, use_llm=False, parent=None):
        super().__init__(parent)
        self.chapter_id = chapter_id
        self.use_llm = use_llm

    def run(self):
        run_direction(
            self.chapter_id, use_llm=self.use_llm,
            on_progress=lambda pct, msg: self.progress.emit(pct, msg),
            on_failed=lambda msg: self.failed.emit(msg),
            on_done=lambda rules, llm: self.finished_ok.emit(rules, llm))
