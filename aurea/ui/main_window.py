from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSlider,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtCore import QTimer

from aurea import __version__

from ..ui.theme import (
    COLORS,
    SPACING_MD,
    SPACING_SM,
    HudPanel,
    Sidebar,
    WaveformView,
    apply_theme,
    apply_dark_palette,
    status_text_style,
    toast,
)

from ..casting.cast_manager import CastManager
from ..casting.detector import detect_characters
from ..core.database import Database, aurea_home
from ..core.generator import ChapterGenerator, segmentize
from ..direction.director import DirectionWorker
from ..direction.rules import EMOTIONS
from ..ingestion.import_worker import ImportWorker
from ..ingestion.importer import import_book
from ..ingestion.ocr_reader import write_ocr_report
from ..ingestion.pdf_reader import PdfBookReader
from ..voice import clone_store
from .clone_panel import ClonePanel
from .ocr_dialog import OcrOptionsDialog

# Glifos de la barra lateral: solo caracteres presentes en DejaVu Sans Mono
# (verificado con fontTools en la VM). NADA de emoji: en Lubuntu sin fuentes
# emoji se ven como tofu. ◆ biblioteca · ▶ estudio · ● reparto · ▸ dirección
# · ✓ voz clonada. El tooltip de cada botón dice qué sección es.
SECTIONS = [
    ("biblioteca", "◆", "Biblioteca"),
    ("estudio", "▶", "Estudio"),
    ("reparto", "●", "Reparto"),
    ("direccion", "▸", "Dirección"),
    ("voz", "✓", "Voz clonada"),
]


class MainWindow(QFrame):
    """Ventana principal de AUREA.

    B5 (decisión documentada 2026-11-20): se mantiene QFrame y NO se migra
    a QMainWindow en este pase. Un QMainWindow real cambiaría el layout raíz
    (central widget, barra de menú, barra de estado) y no hay una prueba que
    demuestre que nada se rompa; el cambio queda pendiente con prueba propia.
    Los atajos de transporte se implementan con QShortcut + keyPressEvent,
    que no necesitan QMainWindow.
    """
    def __init__(self):
        super().__init__()
        apply_theme(self)
        apply_dark_palette(QApplication.instance())
        # B1: una sola constante de versión (aurea/__init__.py).
        self.setWindowTitle(f"AUREA Audiobook Studio — {__version__} (Neon HUD)")
        self.resize(1180, 720)

        self.db = Database()
        # A8: UN SOLO guarda de "trabajo en curso" compartido por Estudio
        # y Dirección. Antes había dos (self.worker y self.director) y cada
        # uno solo miraba el suyo: dirigir mientras se generaba corría a
        # la vez sobre el mismo capítulo y la misma BD.
        self.active_worker = None
        self.player = None
        self._player_state_wired = False  # stateChanged del transporte
        self._books = []
        self._chapters = []
        self._cast = []
        self._segs = []
        self._current_book_id = None
        self._current_chapter_id = None
        self._last_wav = None
        self.audio_out = None
        self._pending_gen = None  # (chapter, force) de la cadena dirigir→generar
        self._last_import_path = None  # ruta del PDF en importación OCR
        self._use_llm = False     # A10: refinamiento Ollama (casilla Dirección)
        self._volume = 0.8        # M19: volumen real del deslizador

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)

        self.sidebar = Sidebar([(k, i, t) for k, i, t in SECTIONS])
        self.sidebar.add_footer("borrar", "✕", "Borrar libro seleccionado")
        self.sidebar.changed.connect(self._on_section)
        self.sidebar.footer_action.connect(self._on_sidebar_footer)
        mid.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_biblioteca())   # index 0
        self.stack.addWidget(self._page_estudio())      # index 1
        self.stack.addWidget(self._page_reparto())      # index 2
        self.stack.addWidget(self._page_direccion())    # index 3
        self.stack.addWidget(self._page_voz())          # index 4
        mid.addWidget(self.stack, 1)

        root.addLayout(mid, 1)
        root.addWidget(self._build_transport())

        self.sidebar.set_active("estudio")
        self._setup_transport_shortcuts()
        self.refresh_books()
        self._refresh_health()

    # ================================================================
    # HEADER
    # ================================================================
    def _build_header(self) -> QFrame:
        h = QFrame()
        h.setFixedHeight(52)
        lay = QHBoxLayout(h)
        lay.setContentsMargins(16, 0, 16, 0)

        logo = QLabel("AUR")
        logo.setProperty("hud", "title")
        e = QLabel("E")
        e.setProperty("hud", "title-accent")  # magenta; estilo en theme.py QSS
        a = QLabel("A")
        a.setProperty("hud", "title")
        sub = QLabel(f"AUDIOBOOK // STUDIO v{__version__}")
        sub.setProperty("hud", "true")
        for wdg in (logo, e, a, sub):
            lay.addWidget(wdg)

        lay.addSpacing(18)
        self.book_chip = QLabel("LIBRO · ninguno abierto")
        self.book_chip.setProperty("hud", "true")
        lay.addWidget(self.book_chip)

        lay.addStretch(1)
        # Indicador de salud REAL (antes: texto fijo "KOKORO FP16 · LISTO").
        # Se actualiza en _refresh_health(): LISTO solo si el modelo está
        # cargado; si no, dice si los archivos de modelo existen o no.
        # EXCEPCIÓN DOCUMENTADA (Fase 4): health_lbl y status_lbl llevan
        # setStyleSheet en línea porque su COLOR cambia con el estado
        # (verde/ámbar/magenta/cian). La fuente y el tamaño no se repiten
        # aquí: viven en theme.status_text_style(), una sola función.
        self.health_lbl = QLabel("● KOKORO · …")
        self.health_lbl.setStyleSheet(status_text_style(COLORS['dim']))
        lay.addWidget(self.health_lbl)
        self.status_lbl = QLabel("● LISTO")
        self.status_lbl.setStyleSheet(status_text_style(COLORS['green']))
        lay.addWidget(self.status_lbl)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(140)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setProperty("hud", "true")
        self.progress_bar.hide()
        lay.addWidget(self.progress_bar)
        return h

    # ================================================================
    # helpers
    # ================================================================
    @staticmethod
    def _hud_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setProperty("hud", "true")
        return lbl

    @staticmethod
    def _btn(text: str, variant: str | None = None, tip: str | None = None) -> QPushButton:
        b = QPushButton(text)
        if variant:
            b.setProperty("variant", variant)
        if tip:
            b.setToolTip(tip)
        return b

    def _update_progress(self, value: int, message: str, color: str | None = None):
        """Actualiza la barra de progreso y el estado en la cabecera."""
        self.progress_bar.show()
        self.progress_bar.setValue(max(0, min(100, value)))
        if message:
            self.status_lbl.setText(message)
        if color:
            self.status_lbl.setStyleSheet(status_text_style(color))
        if value >= 100:
            self.progress_bar.hide()
            self.progress_bar.setValue(0)

    def _refresh_health(self):
        """Indicador de salud REAL del motor Kokoro (sin cargar el modelo:
        solo mira si ya está cargado en memoria y si existen los archivos)."""
        try:
            from ..tts.kokoro_adapter import MODELS_DIR, KokoroAdapter
            adapter = KokoroAdapter()
            if adapter.loaded:
                text, color = "● KOKORO · LISTO", COLORS['green']
            elif (MODELS_DIR / "kokoro-v1.0.fp16.onnx").exists() and \
                    (MODELS_DIR / "voices-v1.0.bin").exists():
                text, color = "● KOKORO · MODELOS OK", COLORS['amber']
            else:
                text, color = "● KOKORO · SIN MODELOS", COLORS['magenta']
        except Exception:
            text, color = "● KOKORO · ?", COLORS['dim']
        self.health_lbl.setText(text)
        self.health_lbl.setStyleSheet(status_text_style(color))

    # ================================================================
    # PÁGINA 0 · BIBLIOTECA
    # ================================================================
    def _page_biblioteca(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        lay.setSpacing(SPACING_SM)

        panel = HudPanel("BIBLIOTECA", "TXT · EPUB · PDF · MD")
        row = QHBoxLayout()
        btn_import = self._btn("+ Importar libro", "primary", "Importar un libro (TXT, EPUB, PDF o MD)")
        btn_import.clicked.connect(self._import_book)
        row.addWidget(btn_import)
        row.addStretch(1)
        panel.body.addLayout(row)

        self.book_list = QListWidget()
        self.book_list.currentRowChanged.connect(self._on_book_selected)
        panel.body.addWidget(self.book_list, 1)

        btn_del = self._btn("✕ Borrar libro seleccionado", "danger", "Borrar el libro seleccionado (pide confirmación)")
        btn_del.clicked.connect(self._delete_book)
        panel.body.addWidget(btn_del)

        # v0.6.0 (OCR): cancelar una importación en curso (solo se
        # habilita mientras el ImportWorker corre).
        self.btn_cancel_import = self._btn(
            "✕ Cancelar importación", "ghost",
            "Cancelar la importación OCR en curso (lo ya procesado queda en caché)")
        self.btn_cancel_import.setEnabled(False)
        self.btn_cancel_import.clicked.connect(self._cancel_import)
        panel.body.addWidget(self.btn_cancel_import)

        lay.addWidget(panel, 1)
        return page

    def _import_book(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Importar libro", str(Path.home() / "aurea" / "books"),
            "Libros (*.txt *.epub *.pdf *.md)")
        if not path:
            return
        # M11: re-importar el mismo archivo no duplica el libro (se
        # comprueba ANTES de lanzar el worker OCR: la caché haría la
        # segunda importación casi instantánea, pero no debe crear otro
        # libro en la biblioteca).
        existing = self.db.book_by_path(path)
        if existing:
            toast(self, f"«{existing.title}» ya estaba importado; lo abro.", kind="info")
            self.refresh_books()
            for i, b in enumerate(self._books):
                if b.id == existing.id:
                    self.book_list.setCurrentRow(i)
                    break
            return
        # v0.6.0 (OCR): un PDF con páginas escaneadas va por el worker
        # en segundo plano, con diálogo de idioma/DPI.
        if Path(path).suffix.lower() == ".pdf":
            scan = PdfBookReader.scan_pdf(path)
            scanned = [s for s in scan if s["scanned"]]
            if scanned:
                if self.active_worker and self.active_worker.isRunning():
                    QMessageBox.information(
                        self, "AUREA", "Ya hay un proceso en curso.")
                    return
                dlg = OcrOptionsDialog(self, len(scanned), len(scan))
                if dlg.exec() != QDialog.Accepted:
                    return
                self._start_ocr_import(path, dlg.lang, dlg.dpi)
                return
        try:
            title, chapters = import_book(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Importación", f"No se pudo importar:\n{e}")
            return
        self._finish_import(path, title, chapters)

    def _finish_import(self, path, title, chapters, ocr_report=None,
                       ocr_pages=None) -> None:
        """Guarda el libro importado (síncrono u OCR) en la BD. Si trae
        informe OCR, guarda la confianza por página y escribe el
        reporte en output/ocr_<book_id>_reporte.md."""
        book_id = self.db.add_book(title, path, Path(path).suffix.lower().lstrip("."))
        # M25: un solo commit por libro, no uno por capítulo/segmento.
        with self.db.transaction():
            for i, (ctitle, text) in enumerate(chapters):
                self.db.add_chapter(book_id, i, ctitle, text)
        report_path = None
        if ocr_report is not None and ocr_pages:
            self.db.set_ocr_pages(book_id, ocr_pages)
            report_path = write_ocr_report(
                ocr_report, book_id, aurea_home() / "output")
        self.log_line(f"» Importado «{title}»: {len(chapters)} capítulos")
        toast(self, f"» Libro importado: «{title}»", kind="ok")
        self.refresh_books()
        self.book_list.setCurrentRow(self.book_list.count() - 1)
        if report_path is not None:
            self._show_ocr_report(ocr_report, report_path)

    def _show_ocr_report(self, report, report_path) -> None:
        """Resumen de calidad tras importar con OCR + botón al reporte."""
        msg = QMessageBox(self)
        msg.setWindowTitle("AUREA — Informe OCR")
        msg.setText(
            f"Importado con OCR local.\n"
            f"Páginas por OCR: {report.ocr_pages} de {report.total_pages}.\n"
            f"Confianza media: {report.mean_conf:.1f}.\n"
            f"Páginas a revisar: "
            f"{', '.join(map(str, report.review_pages)) or 'ninguna'}.\n"
            f"Elementos eliminados: {len(report.removed)}.\n"
            f"Ritmo: {report.seconds_per_page:.1f} s/página.")
        msg.setInformativeText(f"Reporte completo en:\n{report_path}")
        open_btn = msg.addButton("Abrir reporte", QMessageBox.ActionRole)
        msg.addButton(QMessageBox.Ok)
        msg.exec()
        if msg.clickedButton() is open_btn:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(report_path)))

    # ---- Importación OCR en segundo plano (v0.6.0) ----
    def _start_ocr_import(self, path, lang, dpi) -> None:
        # Regla AUREA: OCR y TTS nunca a la vez (mismo guarda único).
        self._last_import_path = path
        self.active_worker = ImportWorker(path, lang=lang, dpi=dpi)
        self.active_worker.progress.connect(
            lambda p, m: self._update_progress(p, m, COLORS['cyan']))
        self.active_worker.log.connect(self.log_line)
        self.active_worker.failed.connect(self._on_import_failed)
        self.active_worker.cancelled.connect(self._on_import_cancelled)
        self.active_worker.finished_ok.connect(self._on_import_ok)
        self.btn_cancel_import.setEnabled(True)
        self.status_lbl.setText("● Importando con OCR…")
        self.status_lbl.setStyleSheet(
            f"color:{COLORS['cyan']};font-family:monospace;font-size:11px;")
        self.active_worker.start()

    def _cancel_import(self) -> None:
        if self.active_worker is not None:
            self.active_worker.request_cancel()

    def _on_import_ok(self, title, chapters, report, pages) -> None:
        self.active_worker = None
        self.btn_cancel_import.setEnabled(False)
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● KOKORO · LISTO")
        self.status_lbl.setStyleSheet(
            f"color:{COLORS['green']};font-family:monospace;font-size:11px;")
        # La ruta original es la elegida en el diálogo de archivo.
        path = getattr(self, "_last_import_path", None) or title
        self._finish_import(path, title, chapters,
                            ocr_report=report, ocr_pages=pages)

    def _on_import_failed(self, err) -> None:
        self.active_worker = None
        self.btn_cancel_import.setEnabled(False)
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● ERROR")
        self.status_lbl.setStyleSheet(
            f"color:{COLORS['magenta']};font-family:monospace;font-size:11px;")
        toast(self, f"✗ Error al importar: {err}", kind="error")

    def _on_import_cancelled(self) -> None:
        self.active_worker = None
        self.btn_cancel_import.setEnabled(False)
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● KOKORO · LISTO")
        self.status_lbl.setStyleSheet(
            f"color:{COLORS['green']};font-family:monospace;font-size:11px;")
        toast(self, "Importación cancelada: lo procesado quedó en caché.",
              kind="warn")

    def _delete_book(self) -> None:
        item = self.book_list.currentItem()
        if not item:
            QMessageBox.information(self, "AUREA", "Selecciona un libro de la biblioteca.")
            return
        book_id = item.data(Qt.UserRole)
        title = item.text().split("  (")[0]
        reply = QMessageBox.question(
            self, "Borrar libro",
            f"¿Borrar «{title}» y todos sus capítulos, segmentos y audio?",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.db.delete_book(book_id)
        self.log_line(f"✕ Libro borrado: «{title}»")
        toast(self, f"✕ Libro borrado: «{title}»", kind="warn")
        self.refresh_books()
        self._on_book_selected(-1)

    def refresh_books(self):
        self.book_list.clear()
        self._books = self.db.books()
        for b in self._books:
            item = QListWidgetItem(f"{b.title}  ({b.fmt})")
            item.setData(Qt.UserRole, b.id)
            self.book_list.addItem(item)

    def _on_book_selected(self, row):
        self._current_book_id = None
        self._current_chapter_id = None
        self.chapter_list.clear()
        self.segment_list.clear()
        self.book_chip.setText("LIBRO · ninguno abierto")
        if row < 0 or row >= len(self._books):
            return
        book = self._books[row]
        self._current_book_id = book.id
        self.book_chip.setText(f"LIBRO · {book.title}")
        self._refresh_chapters()
        self._refresh_cast()
        self._refresh_clone_panel()

    def _refresh_chapters(self):
        # M4: conservar la selección; solo ir a la fila 0 si no había
        # capítulo seleccionado (antes se perdía tras generar/dirigir).
        prev_id = self._current_chapter_id
        self.chapter_list.clear()
        self._chapters = []
        if not self._current_book_id:
            return
        self._chapters = self.db.chapters(self._current_book_id)
        for c in self._chapters:
            item = QListWidgetItem(f"{c.idx + 1:02d}. {c.title}")
            item.setData(Qt.UserRole, c.id)
            self.chapter_list.addItem(item)
        if self._chapters:
            target = 0
            if prev_id is not None:
                for i, c in enumerate(self._chapters):
                    if c.id == prev_id:
                        target = i
                        break
            self.chapter_list.setCurrentRow(target)
            self._current_chapter_id = self._chapters[target].id

    def _on_chapter_selected(self, item):
        if item:
            self._current_chapter_id = item.data(Qt.UserRole)
            # M5: al cambiar de capítulo, refrescar sus segmentos (antes
            # la lista seguía mostrando los del capítulo anterior).
            self._refresh_segments_for_current()

    # ================================================================
    # PÁGINA 1 · ESTUDIO
    # ================================================================
    def _page_estudio(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        lay.setSpacing(SPACING_SM)

        caps = HudPanel("CAPÍTULOS", "DOBLE CLIC = GENERAR")
        self.chapter_list = QListWidget()
        self.chapter_list.itemDoubleClicked.connect(self._on_chapter_generate)
        self.chapter_list.currentItemChanged.connect(self._on_chapter_selected)
        caps.body.addWidget(self.chapter_list, 1)

        btns = QHBoxLayout()
        btn_gen = self._btn("▶ Generar", "primary", "Generar el audio del capítulo seleccionado")
        btn_gen.clicked.connect(self._on_chapter_generate)
        btns.addWidget(btn_gen, 1)
        btn_dir = self._btn("▸ Dirigir", "primary", "Dirigir el capítulo con reglas (sin generar audio)")
        btn_dir.clicked.connect(self._on_chapter_direct)
        btns.addWidget(btn_dir)
        btn_regen = self._btn("↻ Regen.", tip="Regenerar el audio del capítulo")
        btn_regen.clicked.connect(self._on_chapter_regen)
        btns.addWidget(btn_regen)
        btn_pause = self._btn("II", "ghost", "Pausar o reanudar la generación en curso")
        btn_pause.clicked.connect(self._pause_generation)
        btns.addWidget(btn_pause)
        btn_stop = self._btn("■", "ghost", "Detener la generación en curso")
        btn_stop.clicked.connect(self._stop_generation)
        btns.addWidget(btn_stop)
        caps.body.addLayout(btns)
        lay.addWidget(caps, 1)

        det = HudPanel("SEGMENTOS DEL CAPÍTULO")
        self.segment_list = QListWidget()
        self.segment_list.currentItemChanged.connect(self._on_segment_selected)
        det.body.addWidget(self.segment_list, 1)
        # M7: log visible (se perdió en la reescritura Neon HUD). Las
        # señales `log` de los workers escriben aquí, no en progress(0).
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(96)
        # QTextEdit no tiene setMaximumBlockCount (es de QPlainTextEdit):
        # el límite va en el documento. Hallado al verificar Fase 4: sin
        # esto MainWindow no se instanciaba (AttributeError en __init__).
        self.log_view.document().setMaximumBlockCount(500)
        self.log_view.setPlaceholderText("// registro de generación y dirección")
        det.body.addWidget(self.log_view)
        lay.addWidget(det, 2)
        return page

    def _on_segment_selected(self, current, previous=None):
        """Rellena los controles de Dirección con el segmento elegido (A12)."""
        if not current or not hasattr(self, "dir_delivery"):
            return
        seg_id = current.data(Qt.UserRole)
        seg = next((s for s in self._segs if s.id == seg_id), None)
        if not seg:
            return
        self.dir_delivery.setCurrentText(seg.delivery or "normal")
        self.dir_pace.setValue(seg.pace if seg.pace else 1.0)
        self.dir_intensity.setValue(seg.intensity if seg.intensity is not None else 0.6)
        self.dir_pb.setValue(seg.pause_before_ms or 0)
        self.dir_pa.setValue(seg.pause_after_ms or 0)
        if hasattr(self, "dir_emotion"):
            self.dir_emotion.setCurrentText(seg.emotion or "neutral")

    def _on_chapter_generate(self):
        item = self.chapter_list.currentItem()
        if not item:
            QMessageBox.information(self, "AUREA",
                "Selecciona un capítulo de la biblioteca para generar audio.")
            return
        self._generate_chapter_with_direction(force=False)

    def _on_chapter_regen(self):
        item = self.chapter_list.currentItem()
        if not item:
            QMessageBox.information(self, "AUREA",
                "Selecciona un capítulo de la biblioteca para regenerar.")
            return
        self._generate_chapter_with_direction(force=True)

    def _on_chapter_direct(self):
        item = self.chapter_list.currentItem()
        if not item:
            QMessageBox.information(self, "AUREA",
                "Selecciona un capítulo de la biblioteca antes de dirigir.")
            return
        cid = item.data(Qt.UserRole)
        if self.active_worker and self.active_worker.isRunning():
            QMessageBox.information(self, "AUREA", "Ya hay un proceso en curso.")
            return
        chapter = self.db.chapter(cid)
        if not chapter:
            QMessageBox.information(self, "AUREA", "No se encontró el capítulo seleccionado.")
            return
        if not chapter.text or not chapter.text.strip():
            QMessageBox.information(self, "AUREA",
                f"El capítulo «{chapter.title}» no tiene texto para dirigir.")
            return

        # Crear segmentos si no existen
        if not self._ensure_segments(cid):
            QMessageBox.information(self, "AUREA", "No se pudieron crear segmentos.")
            return

        self.status_lbl.setText(f"● Dirigiendo «{chapter.title}»…")
        self.status_lbl.setStyleSheet(status_text_style(COLORS['cyan']))
        self.active_worker = DirectionWorker(cid, use_llm=self._use_llm)
        self.active_worker.progress.connect(
            lambda p, m: self._update_progress(p, m, COLORS['cyan']))
        self.active_worker.log.connect(self.log_line)
        self.active_worker.failed.connect(self._on_dir_failed)
        self.active_worker.cancelled.connect(self._on_dir_cancelled)
        self.active_worker.finished_ok.connect(self._on_dir_ok)
        self.active_worker.start()

    def _on_dir_failed(self, err):
        self.active_worker = None
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● ERROR")
        self.status_lbl.setStyleSheet(status_text_style(COLORS['magenta']))
        toast(self, f"✗ Error al dirigir: {err}", kind="error")

    def _on_dir_cancelled(self):
        self.active_worker = None
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● LISTO")
        self.status_lbl.setStyleSheet(status_text_style(COLORS['green']))
        toast(self, "■ Dirección cancelada", kind="warn")

    def _ensure_segments(self, cid):
        """Crea segmentos vacíos para un capítulo si no existen."""
        chapter = self.db.chapter(cid)
        if not chapter:
            return False
        existing = self.db.segments(cid)
        if existing:
            return True
        # Crear segmentos desde el texto del capítulo
        use_cast = len(self.db.characters(chapter.book_id)) > 1
        cast = CastManager(self.db) if use_cast else None
        if cast:
            cast.ensure_narrator(chapter.book_id)
        parts = segmentize(chapter.text, use_cast)
        for i, (kind, speaker, chunk, pause) in enumerate(parts):
            cid_char = None
            if use_cast:
                name = speaker if (kind == "dialogue" and speaker) else "Narrador"
                cid_char = cast.character_id(chapter.book_id, name)
            self.db.add_segment(cid, i, chunk, kind=kind,
                                character_id=cid_char,
                                pause_after_ms=pause)
        return True

    def _generate_chapter_with_direction(self, force: bool):
        """Dirige primero los segmentos no dirigidos, luego genera el audio."""
        cid = self._current_chapter_id
        if not cid:
            QMessageBox.information(self, "AUREA",
                "Selecciona un capítulo de la biblioteca antes de generar.")
            return
        if self.active_worker and self.active_worker.isRunning():
            QMessageBox.information(self, "AUREA", "Ya hay un proceso en curso.")
            return
        chapter = self.db.chapter(cid)
        if not chapter:
            QMessageBox.information(self, "AUREA", "No se encontró el capítulo seleccionado.")
            return
        if not chapter.text or not chapter.text.strip():
            QMessageBox.information(self, "AUREA",
                f"El capítulo «{chapter.title}» no tiene texto para generar.")
            return

        # Crear segmentos si no existen
        if not self._ensure_segments(cid):
            QMessageBox.information(self, "AUREA", "No se pudieron crear segmentos del capítulo.")
            return

        # Verificar si hay segmentos no dirigidos
        db = self.db
        segs = db.segments(cid)
        if not segs:
            QMessageBox.information(self, "AUREA",
                f"El capítulo «{chapter.title}» no tiene segmentos creados.")
            return
        pending = [s for s in segs if not s.directed]

        if pending:
            # Primero dirigir, luego generar
            self.status_lbl.setText(f"● Dirigiendo «{chapter.title}» antes de generar…")
            self.status_lbl.setStyleSheet(status_text_style(COLORS['cyan']))
            self._pending_gen = (chapter, force)
            self.active_worker = DirectionWorker(cid, use_llm=self._use_llm)
            self.active_worker.progress.connect(
                lambda p, m: self._update_progress(p, m, COLORS['cyan']))
            self.active_worker.log.connect(self.log_line)
            self.active_worker.failed.connect(self._on_gen_chain_failed)
            self.active_worker.cancelled.connect(self._on_dir_cancelled)
            self.active_worker.finished_ok.connect(self._on_gen_chain_ok)
            self.active_worker.start()
        else:
            # Ya está dirigido, generar directamente
            self._generate_chapter_inner(chapter, force)

    def _on_gen_chain_failed(self, err):
        self.active_worker = None
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● ERROR")
        self.status_lbl.setStyleSheet(status_text_style(COLORS['magenta']))
        toast(self, f"✗ Error al dirigir: {err}", kind="error")
        self._pending_gen = None

    def _on_gen_chain_ok(self, rules_count, llm_count):
        msg = f"✔ {rules_count} segmentos dirigidos"
        if llm_count:
            msg += f" + {llm_count} con Ollama"
        self.status_lbl.setText("● Generando audio…")
        self.status_lbl.setStyleSheet(status_text_style(COLORS['amber']))
        toast(self, msg + ". Generando audio…", kind="info")
        chapter, force = self._pending_gen
        self._pending_gen = None
        self.active_worker = None  # el director terminó; libre para generar
        self._generate_chapter_inner(chapter, force)

    def _generate_chapter_inner(self, chapter, force: bool):
        """Genera el audio del capítulo (ya debe estar dirigido)."""
        cid = chapter.id
        if self.active_worker and self.active_worker.isRunning():
            QMessageBox.information(self, "AUREA", "Ya hay un proceso en curso.")
            return
        self.status_lbl.setText(f"● Generando «{chapter.title}»…")
        self.status_lbl.setStyleSheet(status_text_style(COLORS['amber']))
        self.active_worker = ChapterGenerator(chapter, "em_alex", 1.0, force=force)
        self.active_worker.progress.connect(
            lambda p, m: self._update_progress(p, m, COLORS['amber']))
        self.active_worker.log.connect(self.log_line)
        self.active_worker.failed.connect(self._on_gen_failed)
        self.active_worker.cancelled.connect(self._on_gen_cancelled)
        self.active_worker.finished_ok.connect(self._on_gen_ok)
        self.active_worker.start()

    def _on_gen_failed(self, err):
        self.active_worker = None
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● ERROR")
        self.status_lbl.setStyleSheet(status_text_style(COLORS['magenta']))
        toast(self, f"✗ Error: {err}", kind="error")
        self._refresh_health()

    def _on_gen_cancelled(self):
        self.active_worker = None
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● LISTO")
        self.status_lbl.setStyleSheet(status_text_style(COLORS['green']))
        toast(self, "■ Generación cancelada (los segmentos ya generados se reutilizan)", kind="warn")
        self._refresh_segments_for_current()

    def _on_gen_ok(self, wav):
        self.active_worker = None
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● LISTO")
        self.status_lbl.setStyleSheet(status_text_style(COLORS['green']))
        self._last_wav = wav
        toast(self, "✓ Capítulo generado", kind="ok")
        self.wave.set_wav(wav)
        self._refresh_segments_for_current()
        self._refresh_chapters()
        self._refresh_health()

    def _pause_generation(self):
        # C5: pausa REAL por flag (el bucle la comprueba entre segmentos).
        # Antes era QThread.quit(), que no hace nada en un run() bloqueante.
        w = self.active_worker
        if w and w.isRunning() and hasattr(w, "request_pause"):
            if w.paused:
                w.request_resume()
                toast(self, "▶ Producción reanudada", kind="info")
            else:
                w.request_pause()
                toast(self, "II Producción en pausa", kind="info")

    def _stop_generation(self):
        # C5: stop REAL por flag, sin wait() en el hilo de UI (antes el
        # wait() congelaba la ventana hasta que el capítulo terminaba).
        w = self.active_worker
        if w and w.isRunning() and hasattr(w, "request_cancel"):
            w.request_cancel()
            toast(self, "■ Parando…", kind="warn")

    def log_line(self, msg):
        # M7: el log visible de la página Estudio.
        if hasattr(self, "log_view"):
            self.log_view.append(msg)

    def _refresh_segments_for_current(self):
        if not self._current_chapter_id:
            return
        self.segment_list.clear()
        self._segs = []
        cid = self._current_chapter_id
        book = None
        for b in self._books:
            if b.id == self._current_book_id:
                book = b
                break
        names = ({c.id: c.name for c in self.db.characters(book.id)} if book else {})
        for s in self.db.segments(cid):
            who = names.get(s.character_id, "—")
            item = QListWidgetItem(f"{s.idx + 1:02d} [{s.kind}] {who}")
            item.setData(Qt.UserRole, s.id)
            self.segment_list.addItem(item)
            self._segs.append(s)

    # ================================================================
    # PÁGINA 2 · REPARTO
    # ================================================================
    def _page_reparto(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        lay.setSpacing(SPACING_SM)

        panel = HudPanel("REPARTO DEL LIBRO", "DETECCIÓN + VOCES")
        btns = QHBoxLayout()
        btn_detect = self._btn("▸ Detectar reparto", "primary", "Detectar los personajes del libro")
        btn_detect.clicked.connect(self._detect_cast)
        btns.addWidget(btn_detect)
        btn_apply = self._btn("✔ Aplicar voces", "ghost", "Ir a la página de voz clonada para asignar voces")
        btn_apply.clicked.connect(self._apply_cast_voices)
        btns.addWidget(btn_apply)
        btns.addStretch(1)
        panel.body.addLayout(btns)

        self.cast_list = QListWidget()
        self.cast_list.setSelectionMode(QListWidget.NoSelection)
        panel.body.addWidget(self.cast_list, 1)

        legend = QLabel("Voces Kokoro: em_alex (narrador), ef_dora, em_santa · "
                        "Voces clonadas: XTTS (perfiles importados)")
        legend.setProperty("hud", "true")
        panel.body.addWidget(legend)
        lay.addWidget(panel, 1)
        return page

    def _refresh_cast(self):
        self.cast_list.clear()
        if not self._current_book_id:
            return
        chars = self.db.characters(self._current_book_id)

        for ch in chars:
            tag = " ● narrador" if ch.is_narrator else ""
            engine = getattr(ch, 'engine', 'kokoro') or 'kokoro'
            voice_label = ch.voice
            if engine == "xtts" and ch.voice_profile_id:
                prof = clone_store.get_profile(ch.voice_profile_id)
                if prof:
                    voice_label = f"CLON: {prof['name']}"
            item = QListWidgetItem(
                f"{ch.name} → {voice_label}{tag}")
            item.setData(Qt.UserRole, ch.id)
            self.cast_list.addItem(item)

    def _detect_cast(self):
        if not self._current_book_id:
            QMessageBox.information(self, "AUREA", "Selecciona un libro primero.")
            return
        texts = [c.text for c in self.db.chapters(self._current_book_id)]
        names = detect_characters(texts)
        created = CastManager(self.db).build_cast(self._current_book_id, names)
        self.log_line(f"◆ Reparto: {len(names)} personajes detectados, {created} nuevos")
        toast(self, f"◆ Reparto: {len(names)} personajes", kind="ok")
        self._refresh_cast()

    def _apply_cast_voices(self):
        # A12: antes solo mostraba un toast. Las voces se asignan en la
        # página "Voz clonada": llevar ahí al usuario es la acción real.
        self.sidebar.set_active("voz")
        toast(self, "Asigna las voces en 'Voz clonada' y vuelve al reparto", kind="info")

    # ================================================================
    # PÁGINA 3 · DIRECCIÓN
    # ================================================================
    def _page_direccion(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)

        panel = HudPanel("DIRECCIÓN NARRATIVA", "REGLAS + OLLAMA OPCIONAL")
        btns = QHBoxLayout()
        btn_dir = self._btn("▸ Dirigir capítulo (instantáneo)", "primary", "Dirigir el capítulo con reglas")
        btn_dir.clicked.connect(lambda: self._direct_chapter(use_llm=False))
        btns.addWidget(btn_dir)
        # A10: el botón Ollama vuelve a existir y hace algo: dirige con
        # refinamiento LLM (use_llm=True). Antes no tenía clicked conectado.
        btn_llm = self._btn("Refinar con Ollama (lento)", "ghost", "Dirigir con refinamiento de Ollama (lento)")
        btn_llm.clicked.connect(lambda: self._direct_chapter(use_llm=True))
        btns.addWidget(btn_llm)
        btns.addStretch(1)
        panel.body.addLayout(btns)

        # A10: casilla que gobierna el refinamiento Ollama también en la
        # página Estudio (Dirigir / Generar).
        self.ollama_check = QCheckBox("Refinar con Ollama al dirigir")
        self.ollama_check.toggled.connect(self._on_ollama_toggled)
        panel.body.addWidget(self.ollama_check)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        self.dir_emotion = QComboBox()
        self.dir_emotion.addItems(sorted(EMOTIONS))
        self.dir_delivery = QComboBox()
        self.dir_delivery.addItems(["normal", "susurro", "bajo", "firme", "elevado", "grito"])
        self.dir_pace = QDoubleSpinBox()
        self.dir_pace.setRange(0.6, 1.3)
        self.dir_pace.setSingleStep(0.05)
        self.dir_pace.setValue(1.0)
        self.dir_intensity = QDoubleSpinBox()
        self.dir_intensity.setRange(0.0, 1.0)
        self.dir_intensity.setSingleStep(0.05)
        self.dir_pb = QDoubleSpinBox()
        self.dir_pb.setRange(0, 3000)
        self.dir_pb.setSingleStep(100)
        self.dir_pb.setSuffix(" ms")
        self.dir_pa = QDoubleSpinBox()
        self.dir_pa.setRange(0, 3000)
        self.dir_pa.setSingleStep(100)
        self.dir_pa.setSuffix(" ms")

        for r, (lbl, wdg) in enumerate([
            ("EMOCIÓN", self.dir_emotion),
            ("ENTREGA", self.dir_delivery),
            ("RITMO", self.dir_pace),
            ("INTENSIDAD", self.dir_intensity),
            ("PAUSA ANTES", self.dir_pb),
            ("PAUSA DESPUÉS", self.dir_pa),
        ]):
            grid.addWidget(self._hud_label(lbl), r, 0)
            grid.addWidget(wdg, r, 1)
        grid.setColumnStretch(2, 1)
        panel.body.addLayout(grid)
        # A12: aplicar los controles al segmento seleccionado (antes no se
        # leían en ningún sitio).
        btn_apply_seg = self._btn("✓ Aplicar al segmento seleccionado", "primary", "Aplicar emoción, entrega, ritmo e intensidad al segmento seleccionado")
        btn_apply_seg.clicked.connect(self._apply_segment_direction)
        panel.body.addWidget(btn_apply_seg)
        panel.body.addStretch(1)
        lay.addWidget(panel, 1)
        return page

    def _on_ollama_toggled(self, checked):
        self._use_llm = bool(checked)

    def _apply_segment_direction(self):
        """Aplica los controles de Dirección al segmento seleccionado."""
        item = self.segment_list.currentItem()
        if not item:
            QMessageBox.information(self, "AUREA", "Selecciona un segmento de la lista.")
            return
        seg_id = item.data(Qt.UserRole)
        self.db.update_direction(
            seg_id,
            self.dir_emotion.currentText(),
            self.dir_delivery.currentText(),
            float(self.dir_intensity.value()),
            float(self.dir_pace.value()),
            int(self.dir_pb.value()),
            int(self.dir_pa.value()),
            directed=1,
        )
        self._refresh_segments_for_current()
        toast(self, "✓ Dirección aplicada al segmento", kind="ok")

    def _direct_chapter(self, use_llm=False):
        cid = self._current_chapter_id
        if not cid:
            QMessageBox.information(self, "AUREA", "Selecciona un capítulo.")
            return
        if self.active_worker and self.active_worker.isRunning():
            QMessageBox.information(self, "AUREA", "Ya hay un proceso en curso.")
            return
        # M6: crear los segmentos si el capítulo es nuevo (antes fallaba
        # con "genera primero el capítulo" aunque dirigir los crea).
        if not self._ensure_segments(cid):
            QMessageBox.information(self, "AUREA", "No se pudieron crear segmentos.")
            return
        self.status_lbl.setText("● Dirigiendo con reglas…")
        self.status_lbl.setStyleSheet(status_text_style(COLORS['cyan']))
        self.active_worker = DirectionWorker(cid, use_llm=bool(use_llm or self._use_llm))
        self.active_worker.progress.connect(
            lambda p, m: self._update_progress(p, m, COLORS['cyan']))
        self.active_worker.log.connect(self.log_line)
        self.active_worker.failed.connect(lambda e: toast(self, f"✗ Dirección: {e}", kind="error"))
        self.active_worker.cancelled.connect(self._on_dir_cancelled)
        self.active_worker.finished_ok.connect(self._on_dir_ok)
        self.active_worker.start()

    def _on_dir_ok(self, rules_count, llm_count):
        self.active_worker = None
        msg = f"✔ {rules_count} segmentos dirigidos por reglas"
        if llm_count:
            msg += f" + {llm_count} refinados con Ollama"
        self.status_lbl.setText("● LISTO")
        self.status_lbl.setStyleSheet(status_text_style(COLORS['green']))
        toast(self, msg + ". Regenera para escuchar.", kind="ok")
        self._refresh_chapters()
        self._refresh_segments_for_current()

    # ================================================================
    # PÁGINA 4 · VOZ CLONADA
    # ================================================================
    def _page_voz(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        lay.setSpacing(SPACING_SM)

        panel = HudPanel("VOCES CLONADAS · XTTS v2")

        self.clone_panel = ClonePanel(self.db)
        panel.body.addWidget(self.clone_panel, 1)

        lay.addWidget(panel, 1)
        return page

    def _refresh_clone_panel(self):
        """Actualiza el clone panel con el libro actual."""
        if hasattr(self, "clone_panel"):
            self.clone_panel.set_book_id(self._current_book_id)

    # ================================================================
    # TRANSPORTE
    # ================================================================
    def _build_transport(self) -> QFrame:
        t = QFrame()
        t.setObjectName("transportBar")  # estilo en theme.py QSS, no ad hoc
        t.setFixedHeight(74)
        lay = QHBoxLayout(t)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(16)

        # Botones de transporte: se guardan como atributos para que el
        # estado del reproductor pueda alternar ▶/II (ver
        # _on_player_state_changed). El clic usa _toggle_playback, que
        # pausa si suena y si no llama a _play (cuyo comportamiento no cambia).
        self.btn_play = self._btn("▶", tip="Reproducir o pausar (Espacio o Ctrl+Espacio)")
        self.btn_play.setProperty("transport", "true")
        self.btn_play.clicked.connect(self._toggle_playback)
        lay.addWidget(self.btn_play)
        self.btn_stop_play = self._btn("■", tip="Detener reproducción (S)")
        self.btn_stop_play.setProperty("transport", "true")
        self.btn_stop_play.clicked.connect(self._stop_playback)
        lay.addWidget(self.btn_stop_play)

        self.time_lbl = QLabel("00:00 / 00:00")
        self.time_lbl.setObjectName("timeLabel")  # estilo en theme.py QSS
        lay.addWidget(self.time_lbl)

        self.wave = WaveformView()
        lay.addWidget(self.wave, 1)

        lay.addWidget(QLabel("VOL"))
        # M19: guardar la referencia (antes el deslizador era una variable
        # local y _play ignoraba su valor, fijando 0.8 siempre).
        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setFixedWidth(90)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(int(self._volume * 100))
        self.vol_slider.setToolTip("Volumen de reproducción")
        self.vol_slider.valueChanged.connect(self._set_volume)
        lay.addWidget(self.vol_slider)
        return t

    def _play(self):
        wav = self._last_wav
        if not wav:
            item = self.chapter_list.currentItem()
            if item and self._chapters:
                cid = item.data(Qt.UserRole)
                c = next((c for c in self._chapters if c.id == cid), None)
                if c:
                    p = (Path.home() / "aurea" / "output" / f"book_{c.book_id}"
                         / f"cap_{c.idx:03d}.wav")
                    wav = str(p) if p.exists() else None
        if not wav:
            QMessageBox.information(self, "AUREA", "Genera primero el capítulo.")
            return
        if self.player is None:
            self.player = QMediaPlayer()
            self.audio_out = QAudioOutput()
            self.player.setAudioOutput(self.audio_out)
            self.player.positionChanged.connect(self._on_position_changed)
        self.player.setSource(QUrl.fromLocalFile(wav))
        # M19: respetar el deslizador (antes fijaba 0.8 siempre).
        self.audio_out.setVolume(self._volume)
        self.player.play()
        self.wave.set_wav(wav)

    def _stop_playback(self):
        if self.player:
            self.player.stop()

    # --- Transporte premium: SOLO presentación ---
    # _play / _stop_playback / _set_volume NO se tocan. Lo nuevo es el
    # cableado de presentación: el botón alterna ▶/II según el estado del
    # reproductor y hay atajos de teclado.
    def _setup_transport_shortcuts(self):
        """Atajos del transporte (Fase 4).

        Ctrl+Espacio: reproducir/pausar en toda la ventana. Espacio y S se
        gestionan en keyPressEvent y solo actúan cuando el foco NO está en
        un control editable ni en un botón, para no romper la escritura ni
        la activación de botones con Espacio. No había atajos previos en la
        app con los que colisionar.
        """
        sc_play = QShortcut(QKeySequence("Ctrl+Space"), self)
        sc_play.activated.connect(self._toggle_playback)

    def _toggle_playback(self):
        """Reproducir/pausar desde el botón ▶/II o el atajo.

        Si no suena nada, delega en _play (sin cambiarlo); si suena, pausa;
        si está en pausa, reanuda. El cableado de playbackStateChanged se
        hace aquí, tras crear el reproductor, para no tocar _play.
        """
        if self.player is not None:
            if self.player.playbackState() == QMediaPlayer.PlayingState:
                self.player.pause()
                return
            if self.player.playbackState() == QMediaPlayer.PausedState:
                self.player.play()
                return
        self._play()
        if self.player is not None and not self._player_state_wired:
            self.player.playbackStateChanged.connect(self._on_player_state_changed)
            self._player_state_wired = True
            self._on_player_state_changed(self.player.playbackState())

    def _on_player_state_changed(self, state):
        """Refleja el estado del reproductor en el botón play (▶/II)."""
        if not hasattr(self, "btn_play"):
            return
        if state == QMediaPlayer.PlayingState:
            self.btn_play.setText("II")
            self.btn_play.setToolTip("Pausar (Espacio o Ctrl+Espacio)")
        else:
            self.btn_play.setText("▶")
            self.btn_play.setToolTip("Reproducir o pausar (Espacio o Ctrl+Espacio)")

    def keyPressEvent(self, event):  # noqa: N802
        """Espacio = reproducir/pausar, S = detener (transporte).

        Solo cuando el foco no está en un control editable (escribir no
        debe parar el audio) ni en un botón (Espacio es su activación).
        En listas el atajo puede no llegar si el widget consume la tecla
        (búsqueda por teclado); Ctrl+Espacio siempre funciona.
        """
        from PySide6.QtWidgets import (
            QComboBox as _QComboBox,
            QDoubleSpinBox as _QDoubleSpinBox,
            QLineEdit as _QLineEdit,
            QSpinBox as _QSpinBox,
            QTextEdit as _QTextEdit,
        )

        focus = QApplication.focusWidget()
        editable = isinstance(
            focus, (_QLineEdit, _QTextEdit, _QComboBox, _QSpinBox, _QDoubleSpinBox)
        )
        if not editable and not isinstance(focus, QAbstractButton):
            if event.key() == Qt.Key_Space:
                self._toggle_playback()
                event.accept()
                return
            if event.key() == Qt.Key_S:
                self._stop_playback()
                event.accept()
                return
        super().keyPressEvent(event)

    def _set_volume(self, val):
        self._volume = val / 100.0
        if self.audio_out:
            self.audio_out.setVolume(self._volume)

    def _on_position_changed(self, pos):
        if self.player and self.player.duration() > 0:
            frac = pos / self.player.duration()
            self.wave.set_played(frac)
            m, s = divmod(pos // 1000, 60)
            tm, ts = divmod(self.player.duration() // 1000, 60)
            self.time_lbl.setText(f"{m:02d}:{s:02d} / {tm:02d}:{ts:02d}")

    # ================================================================
    def _on_sidebar_footer(self, key: str) -> None:
        # A12: la papelera del sidebar borra el libro seleccionado (con
        # confirmación, vía _delete_book); antes solo parpadeaba.
        if key == "borrar":
            self._delete_book()

    def _on_section(self, key: str) -> None:
        idx = {k: i for i, (k, _, _) in enumerate(SECTIONS)}.get(key)
        if idx is not None:
            self.stack.setCurrentIndex(idx)
