#!/usr/bin/env bash
# --- A11: escritura segura (no revierte codigo existente) ---
# Sin --force, un archivo destino que ya existe NO se sobrescribe: se
# avisa y se conserva. Con --force se hace backup con timestamp y se
# reescribe. Una instalacion limpia escribe todo con normalidad.
AUREA_FORCE=0
if [ "${1:-}" = "--force" ]; then AUREA_FORCE=1; fi
write_once() {
  local dest="$1"
  if [ -e "$dest" ] && [ "$AUREA_FORCE" != "1" ]; then
    echo "AVISO: $dest ya existe; no se sobrescribe (usa --force para reescribir con backup)" >&2
    cat > /dev/null
    return 0
  fi
  if [ -e "$dest" ]; then
    local bak="$dest.bak.$(date +%Y%m%d-%H%M%S)"
    cp "$dest" "$bak"
    echo "Backup: $dest -> $bak" >&2
  fi
  mkdir -p "$(dirname "$dest")"
  cat > "$dest"
}
# --- fin A11 ---
# =============================================================
#  AUREA Audiobook Studio · Fase 2 — Núcleo funcional
#  Protocolo Nova-Aegis
# =============================================================
#  Qué hace:
#    1. Crea la estructura del proyecto en ~/aurea (sin borrar nada)
#    2. Escribe los módulos: ingestion (TXT/EPUB/PDF), core
#       (SQLite + generador por lotes), tts (adaptador Kokoro) y
#       ui (ventana PySide6 oscura)
#    3. Instala las dependencias nuevas SOLO en el venv existente
#    4. Corre una prueba de humo (imports)
#
#  Requisito: haber corrido antes el Voice Lab (Fase 1),
#  porque reutiliza ~/aurea/.venv y ~/aurea/models.
#
#  Uso:  bash aurea_fase2_setup.sh
#  Después:  cd ~/aurea && source .venv/bin/activate && python -m aurea.main
# =============================================================

set -u

AUREA="$HOME/aurea"
VENV="$AUREA/.venv"

say()  { printf '\033[1;36m[AUREA]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[AUREA]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[AUREA]\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$VENV/bin/activate" ] || die "No existe ~/aurea/.venv — corre primero el Voice Lab (Fase 1)."

say "Creando estructura del proyecto en $AUREA ..."
mkdir -p "$AUREA/aurea/core" "$AUREA/aurea/ingestion" "$AUREA/aurea/tts" "$AUREA/aurea/ui"
mkdir -p "$AUREA/books" "$AUREA/output"

# ---------------- Paquete ----------------
write_once "$AUREA/aurea/__init__.py" <<'EOF'
__version__ = "0.2.0-fase2"
EOF

# ---------------- core/models.py ----------------
write_once "$AUREA/aurea/core/models.py" <<'EOF'
from dataclasses import dataclass


@dataclass
class Book:
    id: int
    title: str
    path: str
    fmt: str


@dataclass
class Chapter:
    id: int
    book_id: int
    idx: int
    title: str
    text: str


@dataclass
class Segment:
    id: int
    chapter_id: int
    idx: int
    text: str
    wav_path: str | None
    status: str
EOF

# ---------------- core/database.py ----------------
write_once "$AUREA/aurea/core/database.py" <<'EOF'
import sqlite3
from pathlib import Path

from .models import Book, Chapter, Segment

DB_PATH = Path.home() / "aurea" / "aurea.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS books(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  path TEXT NOT NULL,
  fmt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chapters(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  title TEXT NOT NULL,
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS segments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  text TEXT NOT NULL,
  wav_path TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
);
"""


class Database:
    """SQLite: metadatos del proyecto. Los WAV viven fuera de la base."""

    def __init__(self, path=DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)

    # ---- libros ----
    def add_book(self, title, path, fmt):
        cur = self.conn.execute(
            "INSERT INTO books(title, path, fmt) VALUES (?,?,?)", (title, path, fmt))
        self.conn.commit()
        return cur.lastrowid

    def books(self):
        rows = self.conn.execute("SELECT id, title, path, fmt FROM books ORDER BY id")
        return [Book(*r) for r in rows]

    # ---- capítulos ----
    def add_chapter(self, book_id, idx, title, text):
        cur = self.conn.execute(
            "INSERT INTO chapters(book_id, idx, title, text) VALUES (?,?,?,?)",
            (book_id, idx, title, text))
        self.conn.commit()
        return cur.lastrowid

    def chapters(self, book_id):
        rows = self.conn.execute(
            "SELECT id, book_id, idx, title, text FROM chapters "
            "WHERE book_id=? ORDER BY idx", (book_id,))
        return [Chapter(*r) for r in rows]

    def chapter(self, chapter_id):
        row = self.conn.execute(
            "SELECT id, book_id, idx, title, text FROM chapters WHERE id=?",
            (chapter_id,)).fetchone()
        return Chapter(*row) if row else None

    # ---- segmentos ----
    def add_segment(self, chapter_id, idx, text):
        cur = self.conn.execute(
            "INSERT INTO segments(chapter_id, idx, text) VALUES (?,?,?)",
            (chapter_id, idx, text))
        self.conn.commit()
        return Segment(cur.lastrowid, chapter_id, idx, text, None, "pending")

    def segments(self, chapter_id):
        rows = self.conn.execute(
            "SELECT id, chapter_id, idx, text, wav_path, status FROM segments "
            "WHERE chapter_id=? ORDER BY idx", (chapter_id,))
        return [Segment(*r) for r in rows]

    def update_segment(self, seg_id, wav_path, status):
        self.conn.execute(
            "UPDATE segments SET wav_path=?, status=? WHERE id=?",
            (wav_path, status, seg_id))
        self.conn.commit()

    def clear_segments(self, chapter_id):
        self.conn.execute("DELETE FROM segments WHERE chapter_id=?", (chapter_id,))
        self.conn.commit()
EOF

# ---------------- ingestion/base.py ----------------
write_once "$AUREA/aurea/ingestion/base.py" <<'EOF'
from abc import ABC, abstractmethod


class BookReader(ABC):
    """Contrato: todo lector devuelve [(título_capítulo, texto), ...]."""

    @abstractmethod
    def read(self, path: str) -> list[tuple[str, str]]:
        ...
EOF

# ---------------- ingestion/text_reader.py ----------------
write_once "$AUREA/aurea/ingestion/text_reader.py" <<'EOF'
import re
from pathlib import Path

from .base import BookReader

HEADING = re.compile(
    r"^\s*(cap[íi]tulo|CAP[ÍI]TULO|parte|PARTE|prólogo|PRÓLOGO|epílogo|EPÍLOGO)\b.{0,60}$")


class TextReader(BookReader):
    def read(self, path):
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        heads = [i for i, l in enumerate(lines)
                 if len(l.strip()) < 80 and HEADING.match(l)]
        if len(heads) >= 2:
            chapters = []
            bounds = heads + [len(lines)]
            for a, b in zip(bounds, bounds[1:]):
                text = "\n".join(lines[a:b]).strip()
                if text:
                    chapters.append((lines[a].strip()[:80], text))
            if chapters:
                return chapters
        # Sin encabezados claros: partir por bloques de ~8000 caracteres
        paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
        chapters, buf, size = [], [], 0
        for p in paras:
            buf.append(p)
            size += len(p)
            if size > 8000:
                chapters.append((f"Parte {len(chapters) + 1}", "\n\n".join(buf)))
                buf, size = [], 0
        if buf:
            chapters.append((f"Parte {len(chapters) + 1}", "\n\n".join(buf)))
        return chapters
EOF

# ---------------- ingestion/epub_reader.py ----------------
write_once "$AUREA/aurea/ingestion/epub_reader.py" <<'EOF'
import re
from html.parser import HTMLParser
from pathlib import Path

from .base import BookReader


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("style", "script") and self.skip:
            self.skip -= 1
        if tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


class EpubReader(BookReader):
    def read(self, path):
        import ebooklib
        from ebooklib import epub

        book = epub.read_epub(path)
        chapters = []
        for i, item in enumerate(book.get_items_of_type(ebooklib.ITEM_DOCUMENT)):
            parser = _TextExtractor()
            parser.feed(item.get_content().decode("utf-8", "replace"))
            text = re.sub(r"\n{3,}", "\n\n",
                          "\n".join(l.strip() for l in "".join(parser.parts).splitlines())).strip()
            if len(text) > 40:
                title = getattr(item, "title", None) or f"Sección {i + 1}"
                chapters.append((title[:80], text))
        return chapters
EOF

# ---------------- ingestion/pdf_reader.py ----------------
write_once "$AUREA/aurea/ingestion/pdf_reader.py" <<'EOF'
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
EOF

# ---------------- ingestion/importer.py ----------------
write_once "$AUREA/aurea/ingestion/importer.py" <<'EOF'
from pathlib import Path

from .epub_reader import EpubReader
from .pdf_reader import PdfBookReader
from .text_reader import TextReader

READERS = {".txt": TextReader, ".epub": EpubReader, ".pdf": PdfBookReader}


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
EOF

# ---------------- tts/kokoro_adapter.py ----------------
write_once "$AUREA/aurea/tts/kokoro_adapter.py" <<'EOF'
import gc
import threading
from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro
from misaki import espeak
from misaki.espeak import EspeakG2P

MODELS_DIR = Path.home() / "aurea" / "models"
VOICES_ES = ["em_alex", "ef_dora", "em_santa"]  # las 3 voces en español de Kokoro


class KokoroAdapter:
    """Adaptador del motor de voz. Un solo modelo cargado a la vez (regla AUREA)."""

    _lock = threading.Lock()
    _instance = None

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._kokoro = None
                inst._model_file = None
                inst._g2p = EspeakG2P(language="es")
                inst._fallback = espeak.EspeakFallback(british=False)
                cls._instance = inst
        return cls._instance

    @property
    def loaded(self):
        return self._kokoro is not None

    def load(self, model_file="kokoro-v1.0.fp16.onnx"):
        if self._kokoro is not None and self._model_file == model_file:
            return
        self.free()
        model = MODELS_DIR / model_file
        voices = MODELS_DIR / "voices-v1.0.bin"
        if not model.exists() or not voices.exists():
            raise FileNotFoundError(
                "Faltan modelos en ~/aurea/models — corre el Voice Lab (Fase 1) primero.")
        self._kokoro = Kokoro(str(model), str(voices))
        self._model_file = model_file

    def synthesize(self, text, voice="em_alex", speed=1.0):
        self.load()
        phonemes, _ = self._g2p(text)
        return self._kokoro.create(phonemes, voice, speed=speed, is_phonemes=True)

    @staticmethod
    def save_wav(path, samples, sr):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), samples, sr)

    def free(self):
        self._kokoro = None
        self._model_file = None
        gc.collect()
EOF

# ---------------- core/generator.py ----------------
write_once "$AUREA/aurea/core/generator.py" <<'EOF'
import re
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QThread, Signal

from .database import Database
from .models import Chapter
from ..tts.kokoro_adapter import KokoroAdapter

MAX_SEG = 600          # caracteres por segmento (rango óptimo de Kokoro)
PAUSE_S = 0.45         # pausa entre segmentos en el capítulo final


def split_text(text, max_chars=MAX_SEG):
    """Divide el capítulo en segmentos por párrafo, cortando por frases."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    segs = []
    for p in paras:
        while len(p) > max_chars:
            cut = p.rfind(". ", 0, max_chars)
            cut = cut + 1 if cut > max_chars // 2 else max_chars
            segs.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            segs.append(p)
    return segs


class ChapterGenerator(QThread):
    """Genera un capítulo por lotes, en segundo plano. Reanuda donde quedó."""
    progress = Signal(int, str)
    failed = Signal(str)
    finished_ok = Signal(str)

    def __init__(self, chapter: Chapter, voice, speed, force=False, parent=None):
        super().__init__(parent)
        self.chapter = chapter
        self.voice = voice
        self.speed = speed
        self.force = force

    def run(self):
        try:
            # Conexión propia: SQLite no comparte conexiones entre hilos
            db = Database()
            out_dir = Path.home() / "aurea" / "output" / f"book_{self.chapter.book_id}"
            out_dir.mkdir(parents=True, exist_ok=True)
            wav_path = out_dir / f"cap_{self.chapter.idx:03d}.wav"

            if self.force:
                db.clear_segments(self.chapter.id)
            segs = db.segments(self.chapter.id)
            if not segs:
                parts = split_text(self.chapter.text)
                segs = [db.add_segment(self.chapter.id, i, t)
                        for i, t in enumerate(parts)]

            todo = [s for s in segs
                    if not (s.wav_path and Path(s.wav_path).exists())]
            if todo:
                total = len(todo)
                adapter = KokoroAdapter()
                for i, seg in enumerate(todo):
                    t0 = time.perf_counter()
                    samples, sr = adapter.synthesize(
                        seg.text, voice=self.voice, speed=self.speed)
                    seg_wav = out_dir / f"cap_{self.chapter.idx:03d}_seg{seg.idx:04d}.wav"
                    adapter.save_wav(seg_wav, samples, sr)
                    db.update_segment(seg.id, str(seg_wav), "done")
                    self.progress.emit(
                        int((i + 1) * 100 / total),
                        f"Segmento {i + 1}/{total} "
                        f"({time.perf_counter() - t0:.1f}s, voz {self.voice})")
            else:
                self.progress.emit(100, "Sin segmentos pendientes — reutilizando caché")

            self._concat(segs, wav_path)
            self.finished_ok.emit(str(wav_path))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))

    @staticmethod
    def _concat(segs, wav_path):
        clips, sr = [], 24000
        for s in segs:
            if s.wav_path and Path(s.wav_path).exists():
                data, sr = sf.read(s.wav_path)
                clips.append(data)
                clips.append(np.zeros(int(sr * PAUSE_S)))
        if not clips:
            raise RuntimeError("No hay audio generado para unir.")
        audio = np.concatenate(clips[:-1]) if len(clips) > 1 else clips[0]
        sf.write(str(wav_path), audio, sr)
EOF

# ---------------- ui/main_window.py ----------------
write_once "$AUREA/aurea/ui/main_window.py" <<'EOF'
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from ..core.database import Database
from ..core.generator import ChapterGenerator
from ..ingestion.importer import import_book
from ..tts.kokoro_adapter import VOICES_ES

DARK_QSS = """
QWidget { background-color: #1e1f22; color: #d7dadc; font-size: 13px; }
QListWidget, QTextEdit { background-color: #26282c; border: 1px solid #3a3d42;
                         border-radius: 6px; padding: 4px; }
QPushButton { background-color: #2f6feb; color: white; border: none;
              border-radius: 6px; padding: 8px 14px; }
QPushButton:hover { background-color: #3b7bf5; }
QPushButton:disabled { background-color: #3a3d42; color: #888; }
QProgressBar { border: 1px solid #3a3d42; border-radius: 6px; text-align: center; }
QProgressBar::chunk { background-color: #2f6feb; border-radius: 5px; }
QComboBox, QDoubleSpinBox { background-color: #26282c; border: 1px solid #3a3d42;
                            border-radius: 6px; padding: 4px 8px; }
QLabel { color: #9aa0a6; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AUREA Audiobook Studio — 0.2 (Fase 2)")
        self.resize(1100, 680)
        self.db = Database()
        self.worker = None
        self.player = None
        self._books = []
        self._chapters = []
        self._last_wav = None
        self._build_ui()
        self.refresh_books()

    # ---------- interfaz ----------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        toolbar = QHBoxLayout()
        btn_import = QPushButton("📚 Importar libro (TXT · EPUB · PDF)")
        btn_import.clicked.connect(self.import_book)
        toolbar.addWidget(btn_import)
        toolbar.addStretch(1)
        toolbar.addWidget(QLabel("Voz:"))
        self.voice = QComboBox()
        self.voice.addItems(VOICES_ES)
        toolbar.addWidget(self.voice)
        toolbar.addWidget(QLabel("Velocidad:"))
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.5, 1.5)
        self.speed.setSingleStep(0.05)
        self.speed.setValue(1.0)
        toolbar.addWidget(self.speed)
        root.addLayout(toolbar)

        split = QSplitter()
        root.addWidget(split, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("Biblioteca"))
        self.books = QListWidget()
        self.books.currentRowChanged.connect(self.refresh_chapters)
        lv.addWidget(self.books)

        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.addWidget(QLabel("Capítulos (doble clic = generar)"))
        self.chapters = QListWidget()
        self.chapters.itemDoubleClicked.connect(lambda _: self.generate(False))
        mv.addWidget(self.chapters)

        right = QWidget()
        rv = QVBoxLayout(right)
        self.btn_gen = QPushButton("▶ Generar capítulo seleccionado")
        self.btn_gen.clicked.connect(lambda: self.generate(False))
        self.btn_regen = QPushButton("↻ Regenerar capítulo (borra fragmentos)")
        self.btn_regen.clicked.connect(lambda: self.generate(True))
        self.btn_play = QPushButton("🔊 Reproducir capítulo")
        self.btn_play.clicked.connect(self.play)
        for b in (self.btn_gen, self.btn_regen, self.btn_play):
            rv.addWidget(b)
        self.progress = QProgressBar()
        rv.addWidget(self.progress)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        rv.addWidget(self.log, 1)

        split.addWidget(left)
        split.addWidget(mid)
        split.addWidget(right)
        split.setSizes([260, 340, 500])
        self.setStyleSheet(DARK_QSS)

    # ---------- acciones ----------
    def log_line(self, msg):
        self.log.append(msg)

    def refresh_books(self):
        self.books.clear()
        self._books = self.db.books()
        for b in self._books:
            item = QListWidgetItem(f"{b.title}  ({b.fmt})")
            item.setData(Qt.UserRole, b.id)
            self.books.addItem(item)

    def refresh_chapters(self, row):
        self.chapters.clear()
        self._chapters = []
        if row < 0 or row >= len(self._books):
            return
        self._chapters = self.db.chapters(self._books[row].id)
        for c in self._chapters:
            item = QListWidgetItem(f"{c.idx + 1:02d}. {c.title}")
            item.setData(Qt.UserRole, c.id)
            self.chapters.addItem(item)

    def import_book(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importar libro", str(Path.home() / "aurea" / "books"),
            "Libros (*.txt *.epub *.pdf)")
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
        self.refresh_books()
        self.books.setCurrentRow(self.books.count() - 1)

    def _current_chapter_id(self):
        item = self.chapters.currentItem()
        return item.data(Qt.UserRole) if item else None

    def generate(self, force):
        cid = self._current_chapter_id()
        if not cid:
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.information(
                self, "AUREA", "Ya hay una generación en curso (cola secuencial).")
            return
        chapter = self.db.chapter(cid)
        if not chapter:
            return
        self.btn_gen.setEnabled(False)
        self.btn_regen.setEnabled(False)
        self.progress.setValue(0)
        self.log_line(f"🎬 Producción: «{chapter.title}» "
                      f"(voz {self.voice.currentText()}, vel {self.speed.value():.2f})")
        self.worker = ChapterGenerator(
            chapter, self.voice.currentText(), float(self.speed.value()), force=force)
        self.worker.progress.connect(
            lambda p, m: (self.progress.setValue(p), self.log_line(m)))
        self.worker.failed.connect(self.on_failed)
        self.worker.finished_ok.connect(self.on_generated)
        self.worker.start()

    def on_failed(self, err):
        self.log_line(f"✗ Error: {err}")
        self._set_buttons(True)

    def on_generated(self, wav):
        self.log_line(f"✔ Capítulo listo: {wav}")
        self.progress.setValue(100)
        self._last_wav = wav
        self._set_buttons(True)

    def _set_buttons(self, enabled):
        self.btn_gen.setEnabled(enabled)
        self.btn_regen.setEnabled(enabled)

    def play(self):
        wav = self._last_wav
        if not wav:
            item = self.chapters.currentItem()
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
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            self.player = QMediaPlayer()
            self.audio_out = QAudioOutput()
            self.player.setAudioOutput(self.audio_out)
        self.player.setSource(QUrl.fromLocalFile(wav))
        self.audio_out.setVolume(1.0)
        self.player.play()
EOF

# ---------------- ui/__init__.py y demás ----------------
for pkg in core ingestion tts ui; do
    : > "$AUREA/aurea/$pkg/__init__.py"
done

# ---------------- main.py ----------------
write_once "$AUREA/aurea/main.py" <<'EOF'
import sys

from PySide6.QtWidgets import QApplication

from aurea.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
EOF

# ---------------- requirements y README ----------------
write_once "$AUREA/requirements-fase2.txt" <<'EOF'
PySide6>=6.6
pypdf>=4.0
EbookLib>=0.18
soundfile>=0.12
kokoro-onnx>=0.6
misaki-fork[en]
espeakng-loader
numpy
EOF

write_once "$AUREA/README.md" <<'EOF'
# AUREA Audiobook Studio

Estudio local de audiolibros dramatizados. 100% offline tras la instalación.

## Estado: Fase 2 — Núcleo funcional
- Importación TXT, EPUB y PDF (con capa de texto)
- Biblioteca, capítulos y segmentos en SQLite
- Voz del narrador: Kokoro fp16 (em_alex, ef_dora, em_santa)
- Generación por lotes en segundo plano, con caché y reanudación
- Reproducción del capítulo dentro de la app

## Uso
    cd ~/aurea
    source .venv/bin/activate
    python -m aurea.main
EOF

# ---------------- Dependencias (solo en el venv) ----------------
say "Instalando dependencias de la Fase 2 (PySide6 y lectores; puede descargar unos MB)..."
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --quiet -r "$AUREA/requirements-fase2.txt" \
    || die "Falló la instalación de dependencias. Revisa tu conexión y reintenta."

# ---------------- Prueba de humo ----------------
say "Prueba de humo (imports)..."
cd "$AUREA" || die "No se pudo entrar a $AUREA"
python -c "
from aurea.core.database import Database
from aurea.core.generator import ChapterGenerator, split_text
from aurea.ingestion.importer import import_book
from aurea.tts.kokoro_adapter import KokoroAdapter, VOICES_ES
from aurea.ui.main_window import MainWindow
print('✔ Módulos OK — voces:', ', '.join(VOICES_ES))
" || die "La prueba de humo falló. Pega la salida para diagnosticarla."

say "Fase 2 instalada. Para abrir AUREA:"
echo ""
echo "    cd ~/aurea && source .venv/bin/activate && python -m aurea.main"
echo ""
echo "  1) Importa un TXT/EPUB/PDF (botón 📚)"
echo "  2) Doble clic en un capítulo para generarlo con em_alex"
echo "  3) 🔊 para escucharlo"
