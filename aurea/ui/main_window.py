from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
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
    QVBoxLayout,
    QWidget,
    QMessageBox,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtCore import QTimer

from ..ui.theme import (
    COLORS,
    HudPanel,
    Sidebar,
    WaveformView,
    apply_theme,
    apply_dark_palette,
    toast,
)

from ..casting.cast_manager import CastManager
from ..casting.detector import detect_characters
from ..core.database import Database
from ..core.generator import ChapterGenerator, segmentize
from ..direction.director import MODEL, DirectionWorker
from ..ingestion.importer import import_book
from ..tts.kokoro_adapter import VOICES_ES
from ..voice import clone_store
from .clone_panel import ClonePanel

SECTIONS = [
    ("biblioteca", "📚", "Biblioteca"),
    ("estudio", "🎚️", "Estudio"),
    ("reparto", "🎭", "Reparto"),
    ("direccion", "🎬", "Dirección"),
    ("voz", "🧬", "Voz clonada"),
]


class MainWindow(QFrame):
    def __init__(self):
        super().__init__()
        apply_theme(self)
        apply_dark_palette(QApplication.instance())
        self.setWindowTitle("AUREA Audiobook Studio — 0.5 (Neon HUD)")
        self.resize(1180, 720)

        self.db = Database()
        self.worker = None
        self.director = None
        self.player = None
        self._books = []
        self._chapters = []
        self._cast = []
        self._segs = []
        self._current_book_id = None
        self._current_chapter_id = None
        self._last_wav = None
        self.player = None
        self.audio_out = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)

        self.sidebar = Sidebar([(k, i, t) for k, i, t in SECTIONS])
        self.sidebar.add_footer("borrar", "🗑️", "Borrar libro seleccionado")
        self.sidebar.changed.connect(self._on_section)
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
        self.refresh_books()

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
        e.setProperty("hud", "title")
        e.setStyleSheet(f"color:{COLORS['magenta']};")
        a = QLabel("A")
        a.setProperty("hud", "title")
        sub = QLabel("AUDIOBOOK // STUDIO v0.5")
        sub.setProperty("hud", "true")
        for wdg in (logo, e, a, sub):
            lay.addWidget(wdg)

        lay.addSpacing(18)
        self.book_chip = QLabel("LIBRO · ninguno abierto")
        self.book_chip.setProperty("hud", "true")
        lay.addWidget(self.book_chip)

        lay.addStretch(1)
        self.status_lbl = QLabel("● KOKORO FP16 · LISTO")
        self.status_lbl.setStyleSheet(
            f"color:{COLORS['green']};font-family:monospace;font-size:11px;"
        )
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
    def _btn(text: str, variant: str | None = None) -> QPushButton:
        b = QPushButton(text)
        if variant:
            b.setProperty("variant", variant)
        return b

    def _update_progress(self, value: int, message: str, color: str | None = None):
        """Actualiza la barra de progreso y el estado en la cabecera."""
        self.progress_bar.show()
        self.progress_bar.setValue(max(0, min(100, value)))
        if message:
            self.status_lbl.setText(message)
        if color:
            self.status_lbl.setStyleSheet(
                f"color:{color};font-family:monospace;font-size:11px;"
            )
        if value >= 100:
            self.progress_bar.hide()
            self.progress_bar.setValue(0)

    # ================================================================
    # PÁGINA 0 · BIBLIOTECA
    # ================================================================
    def _page_biblioteca(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)

        panel = HudPanel("BIBLIOTECA", "TXT · EPUB · PDF · MD")
        row = QHBoxLayout()
        btn_import = self._btn("+ Importar libro", "primary")
        btn_import.clicked.connect(self._import_book)
        row.addWidget(btn_import)
        row.addStretch(1)
        panel.body.addLayout(row)

        self.book_list = QListWidget()
        self.book_list.currentRowChanged.connect(self._on_book_selected)
        panel.body.addWidget(self.book_list, 1)

        btn_del = self._btn("🗑 Borrar libro seleccionado", "danger")
        btn_del.clicked.connect(self._delete_book)
        panel.body.addWidget(btn_del)

        lay.addWidget(panel, 1)
        return page

    def _import_book(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Importar libro", str(Path.home() / "aurea" / "books"),
            "Libros (*.txt *.epub *.pdf *.md)")
        if not path:
            return
        try:
            title, chapters = import_book(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Importación", f"No se pudo importar:\n{e}")
            return
        book_id = self.db.add_book(title, path, Path(path).suffix.lower().lstrip("."))
        for i, (ctitle, text) in enumerate(chapters):
            self.db.add_chapter(book_id, i, ctitle, text)
        self.log_line(f"📚 Importado «{title}»: {len(chapters)} capítulos")
        toast(self, f"📚 Libro importado: «{title}»", kind="ok")
        self.refresh_books()
        self.book_list.setCurrentRow(self.book_list.count() - 1)

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
        self.log_line(f"🗑 Libro borrado: «{title}»")
        toast(self, f"🗑 Libro borrado: «{title}»", kind="warn")
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
        self.chapter_list.clear()
        self._chapters = []
        if not self._current_book_id:
            return
        self._chapters = self.db.chapters(self._current_book_id)
        for c in self._chapters:
            item = QListWidgetItem(f"{c.idx + 1:02d}. {c.title}")
            item.setData(Qt.UserRole, c.id)
            self.chapter_list.addItem(item)
        # Seleccionar automáticamente el primer capítulo
        if self._chapters:
            self.chapter_list.setCurrentRow(0)
            self._current_chapter_id = self._chapters[0].id

    def _on_chapter_selected(self, item):
        if item:
            self._current_chapter_id = item.data(Qt.UserRole)

    def _refresh_cast(self):
        self.cast_list.clear()
        self._cast = []
        if not self._current_book_id:
            return
        for ch in self.db.characters(self._current_book_id):
            tag = " 🎙️ narrador" if ch.is_narrator else ""
            voice_label = ch.voice
            if ch.engine == "xtts" and ch.voice_profile_id:
                prof = clone_store.get_profile(ch.voice_profile_id)
                if prof:
                    voice_label = f"clon: {prof['name']}"
            item = QListWidgetItem(f"{ch.name} — {voice_label} (vel {ch.speed:.2f}){tag}")
            item.setData(Qt.UserRole, ch.id)
            self.cast_list.addItem(item)
            self._cast.append(ch)

    # ================================================================
    # PÁGINA 1 · ESTUDIO
    # ================================================================
    def _page_estudio(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)

        caps = HudPanel("CAPÍTULOS", "DOBLE CLIC = GENERAR")
        self.chapter_list = QListWidget()
        self.chapter_list.itemDoubleClicked.connect(self._on_chapter_generate)
        self.chapter_list.currentItemChanged.connect(self._on_chapter_selected)
        caps.body.addWidget(self.chapter_list, 1)

        btns = QHBoxLayout()
        btn_gen = self._btn("▶ Generar", "primary")
        btn_gen.clicked.connect(self._on_chapter_generate)
        btns.addWidget(btn_gen, 1)
        btn_dir = self._btn("🎬 Dirigir", "primary")
        btn_dir.clicked.connect(self._on_chapter_direct)
        btns.addWidget(btn_dir)
        btn_regen = self._btn("↻ Regen.")
        btn_regen.clicked.connect(self._on_chapter_regen)
        btns.addWidget(btn_regen)
        btn_pause = self._btn("⏸", "ghost")
        btn_pause.clicked.connect(self._pause_generation)
        btns.addWidget(btn_pause)
        btn_stop = self._btn("■", "ghost")
        btn_stop.clicked.connect(self._stop_generation)
        btns.addWidget(btn_stop)
        caps.body.addLayout(btns)
        lay.addWidget(caps, 1)

        det = HudPanel("SEGMENTOS DEL CAPÍTULO")
        self.segment_list = QListWidget()
        det.body.addWidget(self.segment_list, 1)
        lay.addWidget(det, 2)
        return page

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
        if self.worker and self.worker.isRunning():
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
        self.status_lbl.setStyleSheet(f"color:{COLORS['cyan']};font-family:monospace;font-size:11px;")
        self.worker = DirectionWorker(cid, use_llm=False)
        self.worker.progress.connect(
            lambda p, m: self._update_progress(p, m, COLORS['cyan']))
        self.worker.failed.connect(self._on_dir_failed)
        self.worker.finished_ok.connect(self._on_dir_ok)
        self.worker.start()

    def _on_dir_failed(self, err):
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● ERROR")
        self.status_lbl.setStyleSheet(f"color:{COLORS['magenta']};font-family:monospace;font-size:11px;")
        toast(self, f"✗ Error al dirigir: {err}", kind="error")

    def _on_dir_ok(self, rules_count, llm_count):
        msg = f"✔ {rules_count} segmentos dirigidos"
        if llm_count:
            msg += f" + {llm_count} con Ollama"
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● KOKORO FP16 · LISTO")
        self.status_lbl.setStyleSheet(f"color:{COLORS['green']};font-family:monospace;font-size:11px;")
        toast(self, msg + ". Ahora genera el audio.", kind="ok")
        self._refresh_chapters()
        self._refresh_segments_for_current()

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
        if self.worker and self.worker.isRunning():
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
            self.status_lbl.setStyleSheet(f"color:{COLORS['cyan']};font-family:monospace;font-size:11px;")
            self._pending_gen = (chapter, force)
            self.worker = DirectionWorker(cid, use_llm=False)
            self.worker.progress.connect(
                lambda p, m: self._update_progress(p, m, COLORS['cyan']))
            self.worker.failed.connect(self._on_gen_chain_failed)
            self.worker.finished_ok.connect(self._on_gen_chain_ok)
            self.worker.start()
        else:
            # Ya está dirigido, generar directamente
            self._generate_chapter_inner(chapter, force)

    def _on_gen_chain_failed(self, err):
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● ERROR")
        self.status_lbl.setStyleSheet(f"color:{COLORS['magenta']};font-family:monospace;font-size:11px;")
        toast(self, f"✗ Error al dirigir: {err}", kind="error")
        self._pending_gen = None

    def _on_gen_chain_ok(self, rules_count, llm_count):
        msg = f"✔ {rules_count} segmentos dirigidos"
        if llm_count:
            msg += f" + {llm_count} con Ollama"
        self.status_lbl.setText("● Generando audio…")
        self.status_lbl.setStyleSheet(f"color:{COLORS['amber']};font-family:monospace;font-size:11px;")
        toast(self, msg + ". Generando audio…", kind="info")
        chapter, force = self._pending_gen
        self._pending_gen = None
        self._generate_chapter_inner(chapter, force)

    def _generate_chapter_inner(self, chapter, force: bool):
        """Genera el audio del capítulo (ya debe estar dirigido)."""
        cid = chapter.id
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "AUREA", "Ya hay un proceso en curso.")
            return
        self.status_lbl.setText(f"● Generando «{chapter.title}»…")
        self.status_lbl.setStyleSheet(f"color:{COLORS['amber']};font-family:monospace;font-size:11px;")
        self.worker = ChapterGenerator(chapter, "em_alex", 1.0, force=force)
        self.worker.progress.connect(
            lambda p, m: self._update_progress(p, m, COLORS['amber']))
        self.worker.failed.connect(self._on_gen_failed)
        self.worker.finished_ok.connect(self._on_gen_ok)
        self.worker.start()

    def _on_gen_failed(self, err):
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● ERROR")
        self.status_lbl.setStyleSheet(f"color:{COLORS['magenta']};font-family:monospace;font-size:11px;")
        toast(self, f"✗ Error: {err}", kind="error")

    def _on_gen_ok(self, wav):
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("● KOKORO FP16 · LISTO")
        self.status_lbl.setStyleSheet(f"color:{COLORS['green']};font-family:monospace;font-size:11px;")
        self._last_wav = wav
        toast(self, "✅ Capítulo generado", kind="ok")
        self.wave.set_wav(wav)
        self._refresh_segments_for_current()
        self._refresh_chapters()

    def _pause_generation(self):
        if self.worker and self.worker.isRunning():
            self.worker.quit()
            toast(self, "⏸ Producción pausada", kind="info")

    def _stop_generation(self):
        if self.worker and self.worker.isRunning():
            self.worker.quit()
            self.worker.wait()
            self.worker = None
            toast(self, "⏹ Producción detenida", kind="warn")

    def log_line(self, msg):
        # Se conectará al QTextEdit de la página estudio si existe
        pass

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
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)

        panel = HudPanel("REPARTO DEL LIBRO", "DETECCIÓN + VOCES")
        btns = QHBoxLayout()
        btn_detect = self._btn("⚡ Detectar reparto", "primary")
        btn_detect.clicked.connect(self._detect_cast)
        btns.addWidget(btn_detect)
        btn_apply = self._btn("✔ Aplicar voces", "ghost")
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
        clones = clone_store.list_profiles()
        clone_names = {p["name"] for p in clones}

        for ch in chars:
            tag = " 🎙️ narrador" if ch.is_narrator else ""
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
        self.log_line(f"🎭 Reparto: {len(names)} personajes detectados, {created} nuevos")
        toast(self, f"🎭 Reparto: {len(names)} personajes", kind="ok")
        self._refresh_cast()

    def _apply_cast_voices(self):
        toast(self, "Usa la página 'Voz clonada' para asignar voces a personajes", kind="info")

    # ================================================================
    # PÁGINA 3 · DIRECCIÓN
    # ================================================================
    def _page_direccion(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)

        panel = HudPanel("DIRECCIÓN NARRATIVA", "REGLAS + OLLAMA OPCIONAL")
        btns = QHBoxLayout()
        btn_dir = self._btn("🎬 Dirigir capítulo (instantáneo)", "primary")
        btn_dir.clicked.connect(self._direct_chapter)
        btns.addWidget(btn_dir)
        btn_llm = self._btn("Refinar con Ollama (lento)", "ghost")
        btns.addWidget(btn_llm)
        btns.addStretch(1)
        panel.body.addLayout(btns)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        self.dir_delivery = QComboBox()
        self.dir_delivery.addItems(["normal", "susurro", "grito"])
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
        panel.body.addStretch(1)
        lay.addWidget(panel, 1)
        return page

    def _direct_chapter(self):
        cid = self._current_chapter_id
        if not cid:
            QMessageBox.information(self, "AUREA", "Selecciona un capítulo.")
            return
        if self.director and self.director.isRunning():
            QMessageBox.information(self, "AUREA", "Ya hay una dirección en curso.")
            return
        self.status_lbl.setText("● Dirigiendo con reglas…")
        self.status_lbl.setStyleSheet(f"color:{COLORS['cyan']};font-family:monospace;font-size:11px;")
        self.director = DirectionWorker(cid, use_llm=False)
        self.director.progress.connect(
            lambda p, m: self._update_progress(p, m, COLORS['cyan']))
        self.director.failed.connect(lambda e: toast(self, f"✗ Dirección: {e}", kind="error"))
        self.director.finished_ok.connect(self._on_dir_ok)
        self.director.start()

    def _on_dir_ok(self, rules_count, llm_count):
        msg = f"✔ {rules_count} segmentos dirigidos por reglas"
        if llm_count:
            msg += f" + {llm_count} refinados con Ollama"
        self.status_lbl.setText("● KOKORO FP16 · LISTO")
        self.status_lbl.setStyleSheet(f"color:{COLORS['green']};font-family:monospace;font-size:11px;")
        toast(self, msg + ". Regenera para escuchar.", kind="ok")
        self._refresh_chapters()
        self._refresh_segments_for_current()

    # ================================================================
    # PÁGINA 4 · VOZ CLONADA
    # ================================================================
    def _page_voz(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)

        panel = HudPanel("VOCES CLONADAS · XTTS v2")

        self.clone_panel = ClonePanel(self.db)
        panel.body.addWidget(self.clone_panel, 1)

        lay.addWidget(panel, 1)
        return page

    def _refresh_clone_panel(self):
        """Actualiza el clone panel con el libro actual."""
        if hasattr(self, "clone_panel"):
            self.clone_panel.set_book_id(self._current_book_id)

    def _import_voice_sample(self):
        if not self._current_book_id:
            QMessageBox.information(self, "AUREA",
                                    "Selecciona un libro primero.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Importar muestra de voz",
            str(Path.home() / "aurea" / "voices"),
            "Audio (*.wav *.mp3 *.flac *.m4a *.ogg *.opus)")
        if not path:
            return
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Nombre del perfil",
                                        "Nombre para esta voz clonada:")
        if not ok or not name.strip():
            return
        try:
            from aurea.voice import sample_prep
            pid, report = sample_prep.import_sample(path, name.strip())
            self.log_line(
                f"🎙 Muestra importada: «{name.strip()}» "
                f"({report['duration']}s, SNR {report['snr_db']} dB, "
                f"{report['verdict']})")
            toast(self, f"🎙 Voz clonada: «{name.strip()}»", kind="ok")
            self.clone_panel.refresh()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Importar voz",
                                f"No se pudo importar:\n{e}")

    # ================================================================
    # TRANSPORTE
    # ================================================================
    def _build_transport(self) -> QFrame:
        t = QFrame()
        t.setFixedHeight(74)
        t.setStyleSheet(
            f"background:{COLORS['panel']}; border-top:1px solid {COLORS['line']};"
        )
        lay = QHBoxLayout(t)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(14)

        btn_play = self._btn("▶")
        btn_play.clicked.connect(self._play)
        lay.addWidget(btn_play)
        btn_stop = self._btn("■")
        btn_stop.clicked.connect(self._stop_playback)
        lay.addWidget(btn_stop)

        self.time_lbl = QLabel("00:00 / 00:00")
        self.time_lbl.setStyleSheet(f"font-family:monospace;color:{COLORS['dim']};")
        lay.addWidget(self.time_lbl)

        self.wave = WaveformView()
        lay.addWidget(self.wave, 1)

        lay.addWidget(QLabel("VOL"))
        vol = QSlider(Qt.Horizontal)
        vol.setFixedWidth(90)
        vol.setRange(0, 100)
        vol.setValue(80)
        vol.valueChanged.connect(self._set_volume)
        lay.addWidget(vol)
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
        self.audio_out.setVolume(0.8)
        self.player.play()
        self.wave.set_wav(wav)

    def _stop_playback(self):
        if self.player:
            self.player.stop()

    def _set_volume(self, val):
        if self.audio_out:
            self.audio_out.setVolume(val / 100.0)

    def _on_position_changed(self, pos):
        if self.player and self.player.duration() > 0:
            frac = pos / self.player.duration()
            self.wave.set_played(frac)
            m, s = divmod(pos // 1000, 60)
            tm, ts = divmod(self.player.duration() // 1000, 60)
            self.time_lbl.setText(f"{m:02d}:{s:02d} / {tm:02d}:{ts:02d}")

    # ================================================================
    def _on_section(self, key: str) -> None:
        idx = {k: i for i, (k, _, _) in enumerate(SECTIONS)}.get(key)
        if idx is not None:
            self.stack.setCurrentIndex(idx)
