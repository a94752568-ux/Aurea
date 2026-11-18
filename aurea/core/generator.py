"""Adaptador Qt del motor de generación (refactor Electron).

La lógica vive en core/engine.py y core/segmentation.py (Qt-free): este
módulo solo conserva la API QThread que usa la UI PySide6 (main_window.py)
y los scripts, para que la app clásica siga funcionando sin cambios.
"""
from PySide6.QtCore import QThread, Signal

from .engine import concat_segments, run_generation
from .segmentation import (MAX_SEG, PAUSE_S, DIALOGUE_PAUSE_S,
                           segmentize, _split_chunk, _split_markers,
                           _gain_for)  # noqa: F401  (reexporta API previa)


class ChapterGenerator(QThread):
    progress = Signal(int, str)
    failed = Signal(str)
    finished_ok = Signal(str)

    def __init__(self, chapter, voice, speed, force=False, parent=None):
        super().__init__(parent)
        self.chapter = chapter
        self.voice = voice
        self.speed = speed
        self.force = force
        self.last_wav = None

    def run(self):
        run_generation(
            self.chapter, self.voice, self.speed, force=self.force,
            on_progress=lambda pct, msg: self.progress.emit(pct, msg),
            on_failed=lambda msg: self.failed.emit(msg),
            on_done=lambda msg: self.finished_ok.emit(msg))

    @staticmethod
    def _concat(segs, vmap=None, fallback=None, wav_path=None):
        """Compat: unir segmentos (los dirs generados por run_generation)."""
        return concat_segments(segs, wav_path)
