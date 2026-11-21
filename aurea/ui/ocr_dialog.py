"""Diálogo de opciones OCR para un PDF escaneado o mixto.

Aviso "PDF escaneado detectado — OCR local (Tesseract)", selector de
idioma (Español / Español+Inglés) y DPI (300 por defecto). El OCR es
100% local y CPU-only; lento en esta laptop, cancelable y reanudable
por caché (lo ya procesado no se re-OCRea).
"""
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QLabel, QSpinBox, QVBoxLayout,
)


class OcrOptionsDialog(QDialog):
    def __init__(self, parent, scanned_count: int, total_pages: int):
        super().__init__(parent)
        self.setWindowTitle("AUREA — OCR local")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        title = QLabel("PDF escaneado detectado — OCR local (Tesseract)")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        lay.addWidget(title)
        lay.addWidget(QLabel(
            f"{scanned_count} de {total_pages} páginas son escaneos "
            "(imágenes sin capa de texto).\n"
            "Se extraerán con Tesseract, 100% local y sin nube. "
            "Es lento en CPU: puedes cancelar y reanudar después "
            "(lo ya procesado queda en caché)."))

        lay.addWidget(QLabel("Idioma del OCR:"))
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Español", "spa")
        self.lang_combo.addItem("Español + Inglés", "spa+eng")
        self.lang_combo.setCurrentIndex(1)
        lay.addWidget(self.lang_combo)

        lay.addWidget(QLabel("Resolución de render (DPI):"))
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(150, 600)
        self.dpi_spin.setSingleStep(50)
        self.dpi_spin.setValue(300)
        lay.addWidget(self.dpi_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Importar con OCR")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    @property
    def lang(self) -> str:
        return self.lang_combo.currentData()

    @property
    def dpi(self) -> int:
        return self.dpi_spin.value()
