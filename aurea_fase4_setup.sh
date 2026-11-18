#!/usr/bin/env bash
# =============================================================
#  AUREA Audiobook Studio · Fase 4b — Director Híbrido
#  Protocolo Nova-Aegis
# =============================================================
#  Problema: la espera de Ollama es un cuello de botella enorme.
#  Solución: las reglas dirigen al instante lo que el texto dice
#  literalmente (verbos de habla, puntuación); Ollama queda como
#  refinamiento OPCIONAL y solo para la narración ambigua.
#
#  - "susurró el anciano" → susurro, sin esperar a ningún modelo
#  - "¡...!" → grito/intensidad alta; "..." → pausa larga
#  - Casilla "Refinar con Ollama": solo segmentos de narración
#
#  Respalda el código actual antes de tocar nada.
#  Uso:  bash aurea_fase4b_setup.sh
# =============================================================

set -u

AUREA="$HOME/aurea"
VENV="$AUREA/.venv"

say()  { printf '\033[1;36m[AUREA]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[AUREA]\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$VENV/bin/activate" ] || die "No existe ~/aurea/.venv — corre antes las fases anteriores."

BACKUP="$AUREA/backup_fase4_$(date +%Y%m%d_%H%M)"
cp -r "$AUREA/aurea" "$BACKUP"
say "Código anterior respaldado en $BACKUP"

# ---------------- direction/rules.py (capa de reglas) ----------------
cat > "$AUREA/aurea/direction/rules.py" <<'EOF'
"""Capa de reglas del Director Híbrido: dirección instantánea y
determinista para los casos claros. El LLM (opcional) solo refina
la narración ambigua. Cero espera, cero errores en lo literal."""
import re

VERB_RE = re.compile(
    r"\b(susurr\w*|murmur\w*|balbuce\w*|cuchiche\w*|grit\w*|vocifer\w*|"
    r"exclam\w*|bram\w*|rug\w*|aull\w*|llor\w*|solloz\w*)", re.IGNORECASE)


def direct_segment(seg):
    """Devuelve la dirección dictada por reglas para un segmento."""
    text = seg.text
    delivery, emotion = "normal", "neutral"
    m = VERB_RE.search(text)
    if m:
        v = m.group(1).lower()
        if v.startswith(("susurr", "murmur", "balbuce", "cuchiche")):
            delivery, emotion = "susurro", "tensión"
        elif v.startswith(("grit", "vocifer", "exclam", "bram", "rug", "aull")):
            delivery, emotion = "grito", "enfado"
        elif v.startswith(("llor", "solloz")):
            emotion = "tristeza"

    excl, quest = text.count("!"), text.count("?")
    if delivery == "susurro":
        intensity = 0.35
    elif excl >= 2:
        intensity = 0.95
    elif excl:
        intensity = 0.85
    elif quest:
        intensity = 0.6
    else:
        intensity = 0.6

    if delivery == "susurro":
        pace = 0.85
    elif delivery == "grito":
        pace = 1.05
    elif "..." in text or "…" in text:
        pace = 0.9
    else:
        pace = 1.0

    end = text.rstrip()
    if end.endswith(("...", "…")):
        pause_after = 1000
    elif end.endswith(("!", "?")):
        pause_after = 500
    else:
        pause_after = 300
    pause_before = 800 if (seg.kind == "dialogue" and delivery == "susurro") else 300

    return {"emotion": emotion, "delivery": delivery, "intensity": intensity,
            "pace": pace, "pause_before_ms": pause_before,
            "pause_after_ms": pause_after}


def needs_llm(seg):
    """Solo la narración (sin verbos de habla) se deja al LLM opcional."""
    return seg.kind == "narration"
EOF

# ---------------- direction/director.py (híbrido) ----------------
cat > "$AUREA/aurea/direction/director.py" <<'EOF'
"""Director Híbrido: reglas instantáneas + refinamiento opcional con Ollama."""
import json
import re
import urllib.request

from PySide6.QtCore import QThread, Signal

from ..core.database import Database
from .rules import direct_segment, needs_llm

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.5:2b"          # cámbialo aquí si prefieres otro modelo local
BATCH_CHARS = 1100
MAX_BATCH = 8

SYSTEM = (
    "Eres un director de doblaje para audiolibros dramatizados en español. "
    "Para cada segmento numerado decide la interpretación. Responde ÚNICAMENTE "
    "un array JSON válido, sin texto adicional, con un objeto por segmento: "
    '{"i": <número>, "emotion": <una palabra en español>, '
    '"delivery": "normal"|"susurro"|"grito", "intensity": <0.0-1.0>, '
    '"pace": <0.6-1.3>, "pause_before_ms": <0-3000>, "pause_after_ms": <0-3000>}. '
    "Usa susurro para miedo o intimidad, grito para enfado o alarma, y pausas "
    "largas antes de las advertencias o revelaciones."
)

VALID_DELIVERY = {"normal", "susurro", "grito"}


def _chat(prompt):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["message"]["content"]


def _parse(content):
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        m = re.search(r"\[.*\]", content, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def build_items(segs):
    return [{"n": n, "seg": s, "text": " ".join(s.text.split())[:280]}
            for n, s in enumerate(segs)]


def batch_items(items, max_chars=BATCH_CHARS, max_n=MAX_BATCH):
    batches, cur, size = [], [], 0
    for it in items:
        if cur and (size + len(it["text"]) > max_chars or len(cur) >= max_n):
            batches.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += len(it["text"])
    if cur:
        batches.append(cur)
    return batches


def _clamp(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi)


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
        try:
            db = Database()
            segs = db.segments(self.chapter_id)
            if not segs:
                self.failed.emit("No hay segmentos: genera primero el capítulo.")
                return
            pending = [s for s in segs if not s.directed]
            if not pending:
                self.finished_ok.emit(0, 0)
                return

            # ---- Capa 1: reglas (instantánea) ----
            for s in pending:
                d = direct_segment(s)
                db.update_direction(
                    s.id, d["emotion"], d["delivery"], d["intensity"],
                    d["pace"], d["pause_before_ms"], d["pause_after_ms"])
            self.progress.emit(
                100, f"✔ {len(pending)} segmentos dirigidos por reglas (instantáneo)")

            # ---- Capa 2: Ollama opcional, solo narración ----
            llm_count = 0
            if self.use_llm:
                targets = [s for s in pending if needs_llm(s)]
                if not targets:
                    self.progress.emit(100, "Todo era diálogo con reglas claras: "
                                            "Ollama no hizo falta 😎")
                else:
                    names = {c.id: c.name for c in db.characters(
                        db.chapter(self.chapter_id).book_id)}
                    items = build_items(targets)
                    batches = batch_items(items)
                    try:
                        for bi, batch in enumerate(batches):
                            lines = []
                            for it in batch:
                                s = it["seg"]
                                who = names.get(s.character_id, "narrador")
                                lines.append(f'{it["n"]}. (narración; contexto: {who}) '
                                             f'"{it["text"]}"')
                            prompt = ("Dirige estos segmentos de narración de un "
                                      "audiolibro dramatizado:\n" + "\n".join(lines))
                            for d in _parse(_chat(prompt)):
                                idx = d.get("i")
                                if idx is None:
                                    continue
                                seg = targets[int(idx)]
                                delivery = str(d.get("delivery", "normal")).lower()
                                if delivery not in VALID_DELIVERY:
                                    delivery = "normal"
                                db.update_direction(
                                    seg.id,
                                    str(d.get("emotion", "neutral"))[:30],
                                    delivery,
                                    _clamp(d.get("intensity"), 0.0, 1.0, 0.6),
                                    _clamp(d.get("pace"), 0.6, 1.3, 1.0),
                                    int(_clamp(d.get("pause_before_ms"), 0, 3000, 300)),
                                    int(_clamp(d.get("pause_after_ms"), 0, 3000, 300)))
                            llm_count += len(batch)
                            self.progress.emit(
                                int(llm_count * 100 / len(targets)),
                                f"Lote {bi + 1}/{len(batches)} refinado con {MODEL}")
                    except Exception as e:  # noqa: BLE001
                        self.progress.emit(
                            100, f"⚠ Ollama no respondió ({e}); "
                                 "se conservan las reglas ya aplicadas")
            self.finished_ok.emit(len(pending), llm_count)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
EOF

# ---------------- ui/main_window.py (casilla de refinamiento) ----------------
cat > "$AUREA/aurea/ui/main_window.py" <<'EOF'
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from ..casting.cast_manager import CastManager
from ..casting.detector import detect_characters
from ..core.database import Database
from ..core.generator import ChapterGenerator
from ..direction.director import MODEL, DirectionWorker
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
QCheckBox::indicator { width: 15px; height: 15px; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AUREA Audiobook Studio — 0.4b (Director Híbrido)")
        self.resize(1280, 760)
        self.db = Database()
        self.worker = None
        self.director = None
        self.player = None
        self._books = []
        self._chapters = []
        self._cast = []
        self._segs = []
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
        self.chapters.currentItemChanged.connect(lambda *_: self.refresh_segments())
        mv.addWidget(self.chapters)

        # Producción + reparto + dirección
        right = QWidget()
        rv = QVBoxLayout(right)
        row_prod = QHBoxLayout()
        self.btn_gen = QPushButton("▶ Generar")
        self.btn_gen.clicked.connect(lambda: self.generate(False))
        self.btn_regen = QPushButton("↻ Regenerar")
        self.btn_regen.clicked.connect(lambda: self.generate(True))
        self.btn_play = QPushButton("🔊 Escuchar")
        self.btn_play.clicked.connect(self.play)
        for b in (self.btn_gen, self.btn_regen, self.btn_play):
            row_prod.addWidget(b)
        rv.addLayout(row_prod)
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

        rv.addWidget(QLabel(f"🎬 Dirección narrativa (reglas + Ollama opcional · {MODEL})"))
        self.btn_direct = QPushButton("Dirigir capítulo (instantáneo)")
        self.btn_direct.clicked.connect(self.direct_chapter)
        rv.addWidget(self.btn_direct)
        self.chk_llm = QCheckBox("Refinar narración con Ollama (lento, opcional)")
        rv.addWidget(self.chk_llm)
        self.seg_list = QListWidget()
        self.seg_list.currentRowChanged.connect(self.on_segment_selected)
        rv.addWidget(self.seg_list, 1)

        grid = QGridLayout()
        grid.addWidget(QLabel("Entrega:"), 0, 0)
        self.dir_delivery = QComboBox()
        self.dir_delivery.addItems(["normal", "susurro", "grito"])
        grid.addWidget(self.dir_delivery, 0, 1)
        grid.addWidget(QLabel("Ritmo:"), 0, 2)
        self.dir_pace = QDoubleSpinBox()
        self.dir_pace.setRange(0.6, 1.3)
        self.dir_pace.setSingleStep(0.05)
        grid.addWidget(self.dir_pace, 0, 3)
        grid.addWidget(QLabel("Intensidad:"), 1, 0)
        self.dir_intensity = QDoubleSpinBox()
        self.dir_intensity.setRange(0.0, 1.0)
        self.dir_intensity.setSingleStep(0.05)
        grid.addWidget(self.dir_intensity, 1, 1)
        grid.addWidget(QLabel("Pausa antes (ms):"), 1, 2)
        self.dir_pb = QDoubleSpinBox()
        self.dir_pb.setRange(0, 3000)
        self.dir_pb.setSingleStep(100)
        grid.addWidget(self.dir_pb, 1, 3)
        grid.addWidget(QLabel("Pausa después (ms):"), 2, 0)
        self.dir_pa = QDoubleSpinBox()
        self.dir_pa.setRange(0, 3000)
        self.dir_pa.setSingleStep(100)
        grid.addWidget(self.dir_pa, 2, 1)
        btn_apply = QPushButton("Aplicar al segmento")
        btn_apply.clicked.connect(self.apply_segment_direction)
        grid.addWidget(btn_apply, 2, 2, 1, 2)
        rv.addLayout(grid)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        rv.addWidget(self.log, 1)

        split.addWidget(left)
        split.addWidget(mid)
        split.addWidget(right)
        split.setSizes([220, 300, 760])
        self.setStyleSheet(DARK_QSS)

    # ---------- biblioteca ----------
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
        self.refresh_segments()

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

    # ---------- dirección ----------
    def _current_chapter_id(self):
        item = self.chapters.currentItem()
        return item.data(Qt.UserRole) if item else None

    def refresh_segments(self):
        self.seg_list.clear()
        self._segs = []
        cid = self._current_chapter_id()
        if not cid:
            return
        book = self._current_book()
        names = ({c.id: c.name for c in self.db.characters(book.id)}
                 if book else {})
        for s in self.db.segments(cid):
            who = names.get(s.character_id, "—")
            if s.directed:
                extra = f" · {s.emotion}/{s.delivery} · vel {s.pace:.2f}"
            else:
                extra = " · sin dirigir"
            item = QListWidgetItem(f"{s.idx + 1:02d} [{s.kind}] {who}{extra}")
            item.setData(Qt.UserRole, s.id)
            self.seg_list.addItem(item)
            self._segs.append(s)

    def direct_chapter(self):
        cid = self._current_chapter_id()
        if not cid:
            return
        if self.director and self.director.isRunning():
            QMessageBox.information(self, "AUREA", "Ya hay una dirección en curso.")
            return
        self.btn_direct.setEnabled(False)
        self.progress.setValue(0)
        self.log_line("🎬 Dirigiendo con reglas"
                      + (" + refinamiento Ollama..." if self.chk_llm.isChecked()
                         else " (instantáneo)..."))
        self.director = DirectionWorker(cid, use_llm=self.chk_llm.isChecked())
        self.director.progress.connect(
            lambda p, m: (self.progress.setValue(p), self.log_line(m)))
        self.director.failed.connect(self.on_direct_failed)
        self.director.finished_ok.connect(self.on_directed)
        self.director.start()

    def on_direct_failed(self, err):
        self.log_line(f"✗ Dirección: {err}")
        self.btn_direct.setEnabled(True)

    def on_directed(self, rules_count, llm_count):
        if rules_count == 0 and llm_count == 0:
            self.log_line("🎬 Todos los segmentos ya estaban dirigidos.")
        else:
            msg = f"✔ {rules_count} segmentos dirigidos por reglas"
            if llm_count:
                msg += f" + {llm_count} refinados con Ollama"
            self.log_line(msg + ". Regenera el capítulo para escucharla.")
        self.btn_direct.setEnabled(True)
        self.refresh_segments()

    def on_segment_selected(self, row):
        if row < 0 or row >= len(self._segs):
            return
        s = self._segs[row]
        self.dir_delivery.setCurrentText(s.delivery if s.directed else "normal")
        self.dir_pace.setValue(s.pace if (s.directed and s.pace) else 1.0)
        self.dir_intensity.setValue(s.intensity if (s.directed and s.intensity is not None) else 0.6)
        self.dir_pb.setValue(s.pause_before_ms or 0)
        self.dir_pa.setValue(s.pause_after_ms or 0)

    def apply_segment_direction(self):
        row = self.seg_list.currentRow()
        if row < 0 or row >= len(self._segs):
            QMessageBox.information(self, "AUREA", "Selecciona un segmento.")
            return
        s = self._segs[row]
        self.db.update_direction(
            s.id, s.emotion or "manual", self.dir_delivery.currentText(),
            float(self.dir_intensity.value()), float(self.dir_pace.value()),
            int(self.dir_pb.value()), int(self.dir_pa.value()))
        self.log_line(f"🎬 Segmento {s.idx + 1} corregido a mano: "
                      f"{self.dir_delivery.currentText()}, "
                      f"vel {self.dir_pace.value():.2f}")
        self.refresh_segments()

    # ---------- generación ----------
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
        self.refresh_segments()

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
say "Prueba de humo (reglas del director híbrido)..."
python - <<'PYEOF' || die "La prueba de humo falló. Pega la salida para diagnosticarla."
from aurea.core.models import Segment
from aurea.direction.rules import direct_segment, needs_llm

seg = Segment(1, 1, 0, "—No deberías haber venido aquí —susurró el anciano—.",
              None, "pending", "dialogue", None)
d = direct_segment(seg)
assert d["delivery"] == "susurro" and d["intensity"] == 0.35, d

seg2 = Segment(2, 1, 1, "¡Te lo dije mil veces! ¡No debiste abrir esa puerta!",
               None, "pending", "dialogue", None)
d2 = direct_segment(seg2)
assert d2["delivery"] == "normal" and d2["intensity"] == 0.95, d2

seg3 = Segment(3, 1, 2, "El viento aullaba entre los árboles.",
               None, "pending", "narration", None)
assert needs_llm(seg3), "la narración debería ir al refinamiento opcional"

assert not needs_llm(seg), "un diálogo con verbo claro no necesita LLM"
print("✔ Reglas del director híbrido OK (susurro, grito, narración)")
PYEOF

say "Fase 4b instalada. Abre AUREA con:"
echo ""
echo "    cd ~/aurea && source .venv/bin/activate && python -m aurea.main"
echo ""
echo "  Ahora 'Dirigir capítulo' es instantáneo. Si quieres el matiz"
echo "  extra del LLM, activa la casilla de refinamiento antes de dirigir."