#!/usr/bin/env bash
# =============================================================
#  AUREA Audiobook Studio · Fase 3 — Reparto dramatizado
#  Protocolo Nova-Aegis
# =============================================================
#  Qué hace:
#    1. Respalda el código actual a ~/aurea/backup_fase2_<fecha>/
#    2. Añade el módulo casting/: detector de diálogos y
#       personajes (reglas, sin IA todavía — eso llega en Fase 4)
#       y gestor de reparto con perfiles vocales persistentes
#    3. Amplía la base de datos: tabla characters y segmentos
#       etiquetados (narración/diálogo + personaje). Tu biblioteca
#       importada NO se toca
#    4. Actualiza el generador: cada segmento se narra con la voz
#       de su personaje; continuidad de hablante entre párrafos
#    5. Actualiza la UI: panel 🎭 Reparto para detectar personajes
#       y asignar voces (em_alex / ef_dora / em_santa)
#
#  Requisito: Fase 2 instalada. No necesita dependencias nuevas.
#
#  Uso:  bash aurea_fase3_setup.sh
# =============================================================

set -u

AUREA="$HOME/aurea"
VENV="$AUREA/.venv"

say()  { printf '\033[1;36m[AUREA]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[AUREA]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[AUREA]\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$VENV/bin/activate" ] || die "No existe ~/aurea/.venv — corre antes la Fase 2."

# ---------- 0. Respaldo (regla Aegis: proteger lo que funciona) ----------
BACKUP="$AUREA/backup_fase2_$(date +%Y%m%d_%H%M)"
cp -r "$AUREA/aurea" "$BACKUP"
say "Código anterior respaldado en $BACKUP"

mkdir -p "$AUREA/aurea/casting"
: > "$AUREA/aurea/casting/__init__.py"

# ---------------- core/models.py (añade Character) ----------------
cat > "$AUREA/aurea/core/models.py" <<'EOF'
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
    kind: str = "narration"
    character_id: int | None = None


@dataclass
class Character:
    id: int
    book_id: int
    name: str
    voice: str
    speed: float
    is_narrator: int
EOF

# ---------------- core/database.py (con reparto y migración) ----------------
cat > "$AUREA/aurea/core/database.py" <<'EOF'
import sqlite3
from pathlib import Path

from .models import Book, Chapter, Character, Segment

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
CREATE TABLE IF NOT EXISTS characters(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  voice TEXT NOT NULL DEFAULT 'em_alex',
  speed REAL NOT NULL DEFAULT 1.0,
  is_narrator INTEGER NOT NULL DEFAULT 0,
  UNIQUE(book_id, name)
);
"""


class Database:
    """SQLite: metadatos del proyecto. Los WAV viven fuera de la base."""

    def __init__(self, path=DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """Añade columnas de la Fase 3 a bases creadas en la Fase 2."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(segments)")}
        if "kind" not in cols:
            self.conn.execute(
                "ALTER TABLE segments ADD COLUMN kind TEXT NOT NULL DEFAULT 'narration'")
        if "character_id" not in cols:
            self.conn.execute("ALTER TABLE segments ADD COLUMN character_id INTEGER")
        self.conn.commit()

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
    def add_segment(self, chapter_id, idx, text, kind="narration", character_id=None):
        cur = self.conn.execute(
            "INSERT INTO segments(chapter_id, idx, text, kind, character_id) "
            "VALUES (?,?,?,?,?)", (chapter_id, idx, text, kind, character_id))
        self.conn.commit()
        return Segment(cur.lastrowid, chapter_id, idx, text, None, "pending",
                       kind, character_id)

    def segments(self, chapter_id):
        rows = self.conn.execute(
            "SELECT id, chapter_id, idx, text, wav_path, status, kind, character_id "
            "FROM segments WHERE chapter_id=? ORDER BY idx", (chapter_id,))
        return [Segment(*r) for r in rows]

    def update_segment(self, seg_id, wav_path, status):
        self.conn.execute(
            "UPDATE segments SET wav_path=?, status=? WHERE id=?",
            (wav_path, status, seg_id))
        self.conn.commit()

    def clear_segments(self, chapter_id):
        self.conn.execute("DELETE FROM segments WHERE chapter_id=?", (chapter_id,))
        self.conn.commit()

    # ---- personajes (Fase 3) ----
    def add_character(self, book_id, name, voice, speed, is_narrator=0):
        self.conn.execute(
            "INSERT OR IGNORE INTO characters(book_id, name, voice, speed, is_narrator) "
            "VALUES (?,?,?,?,?)", (book_id, name, voice, speed, is_narrator))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM characters WHERE book_id=? AND name=?",
            (book_id, name)).fetchone()
        return row[0]

    def characters(self, book_id):
        rows = self.conn.execute(
            "SELECT id, book_id, name, voice, speed, is_narrator "
            "FROM characters WHERE book_id=? ORDER BY is_narrator DESC, name",
            (book_id,))
        return [Character(*r) for r in rows]

    def update_character(self, char_id, voice, speed):
        self.conn.execute(
            "UPDATE characters SET voice=?, speed=? WHERE id=?",
            (voice, speed, char_id))
        self.conn.commit()
EOF

# ---------------- casting/detector.py ----------------
cat > "$AUREA/aurea/casting/detector.py" <<'EOF'
"""Detector de diálogos y personajes por reglas (Fase 3).
El análisis emocional fino llegará con el Director Narrativo (Fase 4)."""
import re
from collections import Counter

NAME = r"[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}"

SPEECH_VERBS = (
    "dijo|dijeron|dije|dijiste|susurró|susurro|gritó|grito|respondió|respondio|"
    "preguntó|pregunto|exclamó|exclamo|murmuró|murmuro|añadió|anadio|comentó|"
    "comento|contestó|contesto|replicó|replico|afirmó|afirmo|negó|nego|"
    "concluyó|concluyo|balbuceó|balbuceo|vociferó|vocifero|protestó|protesto|"
    "inquirió|inquirio|señaló|señalo|advirtió|advirtio|confesó|confeso|"
    "ordenó|ordeno|suplicó|suplico|pronunció|pronuncio|prosiguió|prosiguio|"
    "continuó|continuo|interrumpió|interrumpio|explicó|explico"
)

# Verbos de habla insensibles a mayúsculas (?i:...), pero nombres SIEMPRE
# sensibles: así "el" no puede colarse dentro de "Elena" (E-lena).
RE_VERB_NAME = re.compile(
    rf"(?i:\b(?:{SPEECH_VERBS})\b)\s+(?:que\s+)?"
    rf"(?:el|la|los|las|un|una|su|al)?\s*({NAME})")
RE_NAME_VERB = re.compile(rf"\b({NAME})\s+(?i:\b(?:{SPEECH_VERBS})\b)")

STOPWORDS = {
    "Pero", "Cuando", "Entonces", "Luego", "Aunque", "Mientras", "Después",
    "Despues", "Ahora", "Aquel", "Aquella", "Este", "Esta", "Ese", "Esa",
    "Todo", "Toda", "Todos", "Nada", "Nadie", "Alguien", "Algo", "Hay",
    "Fue", "Era", "Había", "Habia", "Sin", "Con", "Para", "Como", "Donde",
}


def analyze(text):
    """Divide un capítulo en párrafos y los etiqueta.

    Devuelve [{"kind": "narration"|"dialogue", "speaker": nombre|None, "text": ...}]
    Con continuidad: un diálogo sin hablante hereda el último detectado.
    """
    result = []
    last_speaker = None
    for para in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        is_dialogue = para.startswith(("—", "–", '"', "«"))
        m = RE_NAME_VERB.search(para) or RE_VERB_NAME.search(para)
        speaker = None
        if m:
            speaker = m.group(1)
            last_speaker = speaker
        elif is_dialogue and last_speaker:
            speaker = last_speaker  # continuidad de hablante
        result.append({
            "kind": "dialogue" if is_dialogue else "narration",
            "speaker": speaker,
            "text": para,
        })
    return result


def detect_characters(texts, max_chars=12, min_score=2.0):
    """Rankea nombres propios por apariciones y apariciones como hablante."""
    speakers = Counter()
    mentions = Counter()
    for t in texts:
        for seg in analyze(t):
            if seg["speaker"] and seg["speaker"] not in STOPWORDS:
                speakers[seg["speaker"]] += 3
        for m in re.finditer(rf"\b({NAME})\b", t):
            mentions[m.group(1)] += 1
    score = dict(speakers)
    for name, n in mentions.items():
        if name not in STOPWORDS:
            score[name] = score.get(name, 0) + n * 0.2
    ranked = sorted(score.items(), key=lambda x: -x[1])
    return [name for name, s in ranked[:max_chars] if s >= min_score]
EOF

# ---------------- casting/cast_manager.py ----------------
cat > "$AUREA/aurea/casting/cast_manager.py" <<'EOF'
"""Gestión del reparto: perfiles vocales persistentes por libro."""
from ..core.database import Database

NARRATOR = "Narrador"
DEFAULT_VOICES = ["ef_dora", "em_santa"]  # em_alex queda reservado al narrador


class CastManager:
    def __init__(self, db: Database):
        self.db = db

    def ensure_narrator(self, book_id):
        return self.db.add_character(book_id, NARRATOR, "em_alex", 1.0, is_narrator=1)

    def build_cast(self, book_id, names):
        """Crea el reparto detectado; los existentes no se tocan."""
        self.ensure_narrator(book_id)
        existing = {c.name for c in self.db.characters(book_id)}
        created = 0
        for name in names:
            if name not in existing:
                voice = DEFAULT_VOICES[len(existing) % 2]
                self.db.add_character(book_id, name, voice, 1.0)
                existing.add(name)
                created += 1
        return created

    def assign(self, character_id, voice, speed):
        self.db.update_character(character_id, voice, speed)

    def character_id(self, book_id, name):
        """Devuelve el id del personaje; lo crea con voz por defecto si falta."""
        chars = {c.name: c for c in self.db.characters(book_id)}
        if name in chars:
            return chars[name].id
        return self.db.add_character(
            book_id, name, DEFAULT_VOICES[len(chars) % 2], 1.0)

    def voice_map(self, book_id):
        """{character_id: (voz, velocidad)} con respaldo del narrador."""
        chars = self.db.characters(book_id)
        narrator = next((c for c in chars if c.is_narrator), None)
        fallback = (narrator.voice, narrator.speed) if narrator else ("em_alex", 1.0)
        return {c.id: (c.voice, c.speed) for c in chars}, fallback
EOF

# ---------------- core/generator.py (con reparto) ----------------
cat > "$AUREA/aurea/core/generator.py" <<'EOF'
import re
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QThread, Signal

from .database import Database
from .models import Chapter
from ..casting.cast_manager import CastManager
from ..casting.detector import analyze
from ..tts.kokoro_adapter import KokoroAdapter

MAX_SEG = 600          # caracteres por segmento (rango óptimo de Kokoro)
PAUSE_S = 0.45         # pausa entre segmentos
DIALOGUE_PAUSE_S = 0.25


def _split_chunk(p, max_chars=MAX_SEG):
    """Corta un párrafo largo por frases."""
    while len(p) > max_chars:
        cut = p.rfind(". ", 0, max_chars)
        cut = cut + 1 if cut > max_chars // 2 else max_chars
        yield p[:cut].strip()
        p = p[cut:].strip()
    if p:
        yield p


def segmentize(text, use_cast):
    """Devuelve [(kind, speaker, texto)] del capítulo."""
    out = []
    if not use_cast:
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if not para:
                continue
            for chunk in _split_chunk(para):
                out.append(("narration", None, chunk))
        return out
    for part in analyze(text):
        for chunk in _split_chunk(part["text"]):
            out.append((part["kind"], part["speaker"], chunk))
    return out


class ChapterGenerator(QThread):
    """Genera un capítulo por lotes, en segundo plano, con reparto."""
    progress = Signal(int, str)
    failed = Signal(str)
    finished_ok = Signal(str)

    def __init__(self, chapter: Chapter, voice, speed, force=False, parent=None):
        super().__init__(parent)
        self.chapter = chapter
        self.voice = voice          # voz por defecto (libros sin reparto)
        self.speed = speed
        self.force = force

    def run(self):
        try:
            db = Database()  # conexión propia del hilo
            book_id = self.chapter.book_id
            out_dir = Path.home() / "aurea" / "output" / f"book_{book_id}"
            out_dir.mkdir(parents=True, exist_ok=True)
            wav_path = out_dir / f"cap_{self.chapter.idx:03d}.wav"

            use_cast = len(db.characters(book_id)) > 1  # narrador + alguien
            cast = CastManager(db) if use_cast else None
            if cast:
                cast.ensure_narrator(book_id)

            if self.force:
                db.clear_segments(self.chapter.id)
            segs = db.segments(self.chapter.id)
            if not segs:
                parts = segmentize(self.chapter.text, use_cast)
                for i, (kind, speaker, chunk) in enumerate(parts):
                    cid = None
                    if use_cast:
                        name = speaker if (kind == "dialogue" and speaker) else "Narrador"
                        cid = cast.character_id(book_id, name)
                    segs.append(db.add_segment(self.chapter.id, i, chunk,
                                               kind=kind, character_id=cid))

            vmap, fallback = ((cast.voice_map(book_id) if cast else ({}, (self.voice, self.speed))))
            todo = [s for s in segs
                    if not (s.wav_path and Path(s.wav_path).exists())]
            if todo:
                total = len(todo)
                adapter = KokoroAdapter()
                for i, seg in enumerate(todo):
                    voice, vspeed = vmap.get(seg.character_id, fallback)
                    t0 = time.perf_counter()
                    samples, sr = adapter.synthesize(
                        seg.text, voice=voice, speed=vspeed)
                    seg_wav = out_dir / f"cap_{self.chapter.idx:03d}_seg{seg.idx:04d}.wav"
                    adapter.save_wav(seg_wav, samples, sr)
                    db.update_segment(seg.id, str(seg_wav), "done")
                    self.progress.emit(
                        int((i + 1) * 100 / total),
                        f"Segmento {i + 1}/{total} [{seg.kind}] voz {voice} "
                        f"({time.perf_counter() - t0:.1f}s)")
            else:
                self.progress.emit(100, "Sin segmentos pendientes — reutilizando caché")

            self._concat(segs, vmap, fallback, wav_path)
            self.finished_ok.emit(str(wav_path))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))

    def _concat(self, segs, vmap, fallback, wav_path):
        clips, sr = [], 24000
        prev_voice = None
        for s in segs:
            if not (s.wav_path and Path(s.wav_path).exists()):
                continue
            data, sr = sf.read(s.wav_path)
            voice = vmap.get(s.character_id, fallback)[0]
            # Pausa más larga cuando cambia de hablante (entrada dramática)
            pause = DIALOGUE_PAUSE_S if voice == prev_voice else PAUSE_S
            prev_voice = voice
            clips.append(data)
            clips.append(np.zeros(int(sr * pause)))
        if not clips:
            raise RuntimeError("No hay audio generado para unir.")
        audio = np.concatenate(clips[:-1]) if len(clips) > 1 else clips[0]
        sf.write(str(wav_path), audio, sr)
EOF

# ---------------- ui/main_window.py (panel de reparto) ----------------
cat > "$AUREA/aurea/ui/main_window.py" <<'EOF'
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from ..casting.cast_manager import CastManager
from ..casting.detector import detect_characters
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
        self.setWindowTitle("AUREA Audiobook Studio — 0.3 (Fase 3 · Reparto)")
        self.resize(1200, 700)
        self.db = Database()
        self.worker = None
        self.player = None
        self._books = []
        self._chapters = []
        self._cast = []
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
        toolbar.addWidget(QLabel("Voz (sin reparto):"))
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

        # Biblioteca
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("Biblioteca"))
        self.books = QListWidget()
        self.books.currentRowChanged.connect(self.on_book_selected)
        lv.addWidget(self.books)

        # Capítulos
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.addWidget(QLabel("Capítulos (doble clic = generar)"))
        self.chapters = QListWidget()
        self.chapters.itemDoubleClicked.connect(lambda _: self.generate(False))
        mv.addWidget(self.chapters)

        # Producción + reparto
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

        rv.addWidget(QLabel("🎭 Reparto del libro"))
        self.btn_cast = QPushButton("Detectar reparto")
        self.btn_cast.clicked.connect(self.detect_cast)
        rv.addWidget(self.btn_cast)
        self.cast_list = QListWidget()
        rv.addWidget(self.cast_list, 1)
        cast_row = QHBoxLayout()
        self.cast_voice = QComboBox()
        self.cast_voice.addItems(VOICES_ES)
        self.cast_speed = QDoubleSpinBox()
        self.cast_speed.setRange(0.5, 1.5)
        self.cast_speed.setSingleStep(0.05)
        self.cast_speed.setValue(1.0)
        btn_assign = QPushButton("Asignar")
        btn_assign.clicked.connect(self.assign_voice)
        cast_row.addWidget(self.cast_voice)
        cast_row.addWidget(self.cast_speed)
        cast_row.addWidget(btn_assign)
        rv.addLayout(cast_row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        rv.addWidget(self.log, 1)

        split.addWidget(left)
        split.addWidget(mid)
        split.addWidget(right)
        split.setSizes([240, 320, 640])
        self.setStyleSheet(DARK_QSS)

    # ---------- biblioteca y capítulos ----------
    def log_line(self, msg):
        self.log.append(msg)

    def refresh_books(self):
        self.books.clear()
        self._books = self.db.books()
        for b in self._books:
            item = QListWidgetItem(f"{b.title}  ({b.fmt})")
            item.setData(Qt.UserRole, b.id)
            self.books.addItem(item)

    def on_book_selected(self, row):
        self.refresh_chapters(row)
        self.refresh_cast()

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

    # ---------- reparto ----------
    def _current_book(self):
        row = self.books.currentRow()
        return self._books[row] if 0 <= row < len(self._books) else None

    def detect_cast(self):
        book = self._current_book()
        if not book:
            QMessageBox.information(self, "AUREA", "Selecciona un libro primero.")
            return
        texts = [c.text for c in self.db.chapters(book.id)]
        names = detect_characters(texts)
        created = CastManager(self.db).build_cast(book.id, names)
        self.log_line(f"🎭 Reparto: {len(names)} personajes detectados, "
                      f"{created} nuevos (Narrador = em_alex)")
        self.refresh_cast()

    def refresh_cast(self):
        self.cast_list.clear()
        self._cast = []
        book = self._current_book()
        if not book:
            return
        for ch in self.db.characters(book.id):
            tag = " 🎙️ narrador" if ch.is_narrator else ""
            item = QListWidgetItem(f"{ch.name} — {ch.voice} (vel {ch.speed:.2f}){tag}")
            item.setData(Qt.UserRole, ch.id)
            self.cast_list.addItem(item)
            self._cast.append(ch)

    def assign_voice(self):
        item = self.cast_list.currentItem()
        if not item:
            QMessageBox.information(self, "AUREA", "Selecciona un personaje del reparto.")
            return
        CastManager(self.db).assign(
            item.data(Qt.UserRole),
            self.cast_voice.currentText(), float(self.cast_speed.value()))
        self.log_line(f"🎭 Voz asignada a «{item.text().split(' — ')[0]}»: "
                      f"{self.cast_voice.currentText()} "
                      f"(vel {self.cast_speed.value():.2f})")
        self.refresh_cast()

    # ---------- generación ----------
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
        self.log_line(f"🎬 Producción: «{chapter.title}»")
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

    # ---------- reproducción ----------
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

# ---------------- Prueba de humo ----------------
# shellcheck disable=SC1091
source "$VENV/bin/activate"
cd "$AUREA" || die "No se pudo entrar a $AUREA"
say "Prueba de humo (imports + detector)..."
python - <<'PYEOF' || die "La prueba de humo falló. Pega la salida para diagnosticarla."
from aurea.casting.detector import analyze, detect_characters
from aurea.casting.cast_manager import CastManager
from aurea.core.database import Database
from aurea.core.generator import ChapterGenerator, segmentize
from aurea.ui.main_window import MainWindow

texto = ("El anciano se acercó a la puerta.\n\n"
         "—No deberías haber venido aquí —susurró el anciano—. "
         "Hay cosas que es mejor dejar enterradas.\n\n"
         "—Tuve que hacerlo —respondió Elena—. No quedaba nadie más.")
segs = analyze(texto)
print("Detector:", [(s["kind"], s["speaker"]) for s in segs])
assert any(s["speaker"] == "Elena" for s in segs), "no detectó a Elena"
print("✔ Módulos y detector OK")
PYEOF

say "Fase 3 instalada. Abre AUREA con:"
echo ""
echo "    cd ~/aurea && source .venv/bin/activate && python -m aurea.main"
echo ""
echo "  1) Selecciona tu libro → botón 🎭 Detectar reparto"
echo "  2) Revisa las voces asignadas (puedes cambiarlas con Asignar)"
echo "  3) Regenera un capítulo con diálogo y escucha el reparto"
