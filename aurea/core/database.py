import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import Book, Chapter, Character, Segment

# Raíz de datos de AUREA. AUREA_HOME permite redirigir BD y audio a un
# directorio temporal (tests): sin la variable, es ~/aurea como siempre.
AUREA_HOME = Path(os.environ.get("AUREA_HOME", Path.home() / "aurea"))
DB_PATH = AUREA_HOME / "aurea.db"


def aurea_home() -> Path:
    """Raíz de datos resuelta EN CADA LLAMADA (no en import).

    El módulo congelaba AUREA_HOME al importar: los tests que fijaban
    AUREA_HOME después del import seguían escribiendo en ~/aurea.
    Database.__init__ y el generador usan esta función; en producción
    (sin la variable) devuelve ~/aurea como siempre."""
    return Path(os.environ.get("AUREA_HOME", Path.home() / "aurea"))

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

NEW_SEGMENT_COLS = {
    "emotion": "TEXT",
    "delivery": "TEXT",
    "intensity": "REAL",
    "pace": "REAL",
    "pause_before_ms": "INTEGER",
    "pause_after_ms": "INTEGER",
    "directed": "INTEGER NOT NULL DEFAULT 0",
}


class Database:
    """SQLite: metadatos del proyecto. Los WAV viven fuera de la base."""

    def __init__(self, path=None):
        if path is None:
            # Se resuelve en cada llamada (no en import): los tests pueden
            # fijar AUREA_HOME antes de crear la BD.
            path = aurea_home() / "aurea.db"
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        # C4: las ON DELETE CASCADE del esquema son inertes sin esto
        # (SQLite lo trae desactivado por defecto).
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._defer_commit = False
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _commit(self):
        """Commit inmediato, salvo dentro de transaction() (M25)."""
        if not self._defer_commit:
            self.conn.commit()

    @contextmanager
    def transaction(self):
        """Agrupa varias escrituras en un solo commit (importar un libro
        con 1300 segmentos era un commit con fsync por segmento)."""
        prev = self._defer_commit
        self._defer_commit = True
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self._defer_commit = prev

    def close(self):
        """Cierra la conexión. Llamar en finally de los hilos (M8)."""
        try:
            self.conn.commit()
        except Exception:
            pass
        self.conn.close()

    def _migrate(self):
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(segments)")}
        for name, decl in NEW_SEGMENT_COLS.items():
            if name not in cols:
                self.conn.execute(f"ALTER TABLE segments ADD COLUMN {name} {decl}")
        if "kind" not in cols:
            self.conn.execute(
                "ALTER TABLE segments ADD COLUMN kind TEXT NOT NULL DEFAULT 'narration'")
        if "character_id" not in cols:
            self.conn.execute("ALTER TABLE segments ADD COLUMN character_id INTEGER")
        # Migración Fase 4c: columnas de clonación en characters
        char_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(characters)")}
        if "engine" not in char_cols:
            self.conn.execute(
                "ALTER TABLE characters ADD COLUMN engine TEXT DEFAULT 'kokoro'")
        if "voice_profile_id" not in char_cols:
            self.conn.execute(
                "ALTER TABLE characters ADD COLUMN voice_profile_id INTEGER")
        # Migración v0.6.0 (OCR): confianza media por página.
        # Tabla propia (no columna en chapters: un capítulo no es una
        # página; aquí cabe además método, PSM, columnas y revisión).
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS ocr_pages("
            "book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE, "
            "page_no INTEGER NOT NULL, "
            "method TEXT NOT NULL, "
            "mean_confidence REAL NOT NULL DEFAULT 100.0, "
            "psm INTEGER, columns INTEGER NOT NULL DEFAULT 1, "
            "needs_review INTEGER NOT NULL DEFAULT 0, "
            "PRIMARY KEY(book_id, page_no))")
        # Migración v0.7.0 (reparto inteligente + ambientes):
        # - characters.gender / gender_manual (género inferido; 1 si
        #   Alberto lo corrigió a mano y no debe re-inferirse).
        # - chapters.ambience / bed_level (ambiente sonoro por capítulo).
        if "gender" not in char_cols:
            self.conn.execute(
                "ALTER TABLE characters ADD COLUMN gender TEXT "
                "DEFAULT 'desconocido'")
        if "gender_manual" not in char_cols:
            self.conn.execute(
                "ALTER TABLE characters ADD COLUMN gender_manual "
                "INTEGER NOT NULL DEFAULT 0")
        chap_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(chapters)")}
        if "ambience" not in chap_cols:
            self.conn.execute(
                "ALTER TABLE chapters ADD COLUMN ambience TEXT DEFAULT 'none'")
        if "bed_level" not in chap_cols:
            self.conn.execute(
                "ALTER TABLE chapters ADD COLUMN bed_level REAL "
                "NOT NULL DEFAULT 0.25")
        self._commit()

    # ---- páginas OCR (v0.6.0) ----
    def set_ocr_pages(self, book_id, pages):
        """Guarda la confianza por página de un libro importado con OCR.
        `pages`: lista de OcrPageResult o de dicts to_dict()."""
        self.conn.execute("DELETE FROM ocr_pages WHERE book_id=?", (book_id,))
        for p in pages:
            d = p.to_dict() if hasattr(p, "to_dict") else p
            self.conn.execute(
                "INSERT INTO ocr_pages(book_id, page_no, method, "
                "mean_confidence, psm, columns, needs_review) "
                "VALUES (?,?,?,?,?,?,?)",
                (book_id, d["page_no"], d["method"],
                 d["mean_confidence"], d.get("psm"), d["columns"],
                 int(d["needs_review"])))
        self._commit()

    def ocr_pages(self, book_id):
        rows = self.conn.execute(
            "SELECT book_id, page_no, method, mean_confidence, psm, "
            "columns, needs_review FROM ocr_pages "
            "WHERE book_id=? ORDER BY page_no", (book_id,))
        return [dict(zip(("book_id", "page_no", "method",
                          "mean_confidence", "psm", "columns",
                          "needs_review"), r)) for r in rows]

    # ---- libros ----
    def add_book(self, title, path, fmt):
        cur = self.conn.execute(
            "INSERT INTO books(title, path, fmt) VALUES (?,?,?)", (title, path, fmt))
        self._commit()
        return cur.lastrowid

    def books(self):
        rows = self.conn.execute("SELECT id, title, path, fmt FROM books ORDER BY id")
        return [Book(*r) for r in rows]

    # ---- capítulos ----
    def add_chapter(self, book_id, idx, title, text):
        cur = self.conn.execute(
            "INSERT INTO chapters(book_id, idx, title, text) VALUES (?,?,?,?)",
            (book_id, idx, title, text))
        self._commit()
        return cur.lastrowid

    def chapters(self, book_id):
        rows = self.conn.execute(
            "SELECT id, book_id, idx, title, text, ambience, bed_level "
            "FROM chapters WHERE book_id=? ORDER BY idx", (book_id,))
        return [Chapter(*r) for r in rows]

    def chapter(self, chapter_id):
        row = self.conn.execute(
            "SELECT id, book_id, idx, title, text, ambience, bed_level "
            "FROM chapters WHERE id=?",
            (chapter_id,)).fetchone()
        return Chapter(*row) if row else None

    # ---- segmentos ----
    SEG_COLS = ("id, chapter_id, idx, text, wav_path, status, kind, character_id, "
                "emotion, delivery, intensity, pace, pause_before_ms, "
                "pause_after_ms, directed")
    def add_segment(self, chapter_id, idx, text, kind="narration",
                    character_id=None, pause_after_ms=None):
        cur = self.conn.execute(
            "INSERT INTO segments(chapter_id, idx, text, kind, character_id, "
            "pause_after_ms) VALUES (?,?,?,?,?,?)",
            (chapter_id, idx, text, kind, character_id, pause_after_ms))
        self._commit()
        return Segment(cur.lastrowid, chapter_id, idx, text, None, "pending",
                       kind, character_id, pause_after_ms=pause_after_ms)

    def segments(self, chapter_id):
        rows = self.conn.execute(
            f"SELECT {self.SEG_COLS} FROM segments WHERE chapter_id=? ORDER BY idx",
            (chapter_id,))
        return [Segment(*r) for r in rows]

    def update_segment(self, seg_id, wav_path, status):
        self.conn.execute(
            "UPDATE segments SET wav_path=?, status=? WHERE id=?",
            (wav_path, status, seg_id))
        self._commit()

    def clear_segments(self, chapter_id):
        self.conn.execute("DELETE FROM segments WHERE chapter_id=?", (chapter_id,))
        self._commit()

    def update_direction(self, seg_id, emotion, delivery, intensity, pace,
                         pause_before_ms, pause_after_ms, directed=1):
        self.conn.execute(
            "UPDATE segments SET emotion=?, delivery=?, intensity=?, pace=?, "
            "pause_before_ms=?, pause_after_ms=?, directed=? WHERE id=?",
            (emotion, delivery, intensity, pace, pause_before_ms,
             pause_after_ms, directed, seg_id))
        self._commit()

    # ---- personajes ----
    def add_character(self, book_id, name, voice, speed, is_narrator=0,
                      gender="desconocido", gender_manual=0):
        self.conn.execute(
            "INSERT OR IGNORE INTO characters(book_id, name, voice, speed, "
            "is_narrator, gender, gender_manual) VALUES (?,?,?,?,?,?,?)",
            (book_id, name, voice, speed, is_narrator, gender, gender_manual))
        self._commit()
        row = self.conn.execute(
            "SELECT id FROM characters WHERE book_id=? AND name=?",
            (book_id, name)).fetchone()
        return row[0]

    def characters(self, book_id):
        rows = self.conn.execute(
            "SELECT id, book_id, name, voice, speed, is_narrator, engine, "
            "voice_profile_id, gender, gender_manual "
            "FROM characters WHERE book_id=? ORDER BY is_narrator DESC, name",
            (book_id,))
        return [Character(*r) for r in rows]

    def update_gender(self, char_id, gender):
        """Corrección manual de género (v0.7.0): marca gender_manual=1
        para que el auto-casting no vuelva a inferirlo. La voz NO se
        toca aquí: corregir el género conserva la voz asignada."""
        self.conn.execute(
            "UPDATE characters SET gender=?, gender_manual=1 WHERE id=?",
            (gender, char_id))
        self._commit()

    def set_character_gender(self, char_id, gender, manual=0):
        """Guarda el género inferido (manual=0) o corregido (manual=1)."""
        self.conn.execute(
            "UPDATE characters SET gender=?, gender_manual=? WHERE id=?",
            (gender, int(manual), char_id))
        self._commit()

    def set_chapter_ambience(self, chapter_id, ambience, bed_level=0.25):
        """Ambiente sonoro del capítulo (v0.7.0)."""
        self.conn.execute(
            "UPDATE chapters SET ambience=?, bed_level=? WHERE id=?",
            (ambience, float(bed_level), chapter_id))
        self._commit()

    def update_character(self, char_id, voice, speed, engine=None, profile_id=None):
        self.conn.execute(
            "UPDATE characters SET voice=?, speed=?, engine=?, voice_profile_id=? "
            "WHERE id=?",
            (voice, speed, engine or "kokoro", profile_id, char_id))
        self._commit()

    def add_character_with_profile(self, book_id, name, voice, speed,
                                   engine="kokoro", voice_profile_id=None,
                                   is_narrator=0, gender="desconocido",
                                   gender_manual=0):
        self.conn.execute(
            "INSERT OR IGNORE INTO characters(book_id, name, voice, speed, is_narrator, "
            "engine, voice_profile_id, gender, gender_manual) VALUES (?,?,?,?,?,?,?,?,?)",
            (book_id, name, voice, speed, is_narrator, engine, voice_profile_id,
             gender, gender_manual))
        self._commit()
        row = self.conn.execute(
            "SELECT id FROM characters WHERE book_id=? AND name=?",
            (book_id, name)).fetchone()
        return row[0]

    # ---- borrado de libro ----
    def delete_book(self, book_id):
        """Borra un libro y sus capítulos, segmentos y personajes de la BD.
        Los archivos WAV en disco bajo output/book_<id>/ se borran si existen."""
        from pathlib import Path
        import shutil
        # Borrar archivos de audio asociados
        out_dir = Path(os.environ.get("AUREA_HOME", Path.home() / "aurea")) / "output" / f"book_{book_id}"
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        # C4: borrado en cascada explícito, además del PRAGMA foreign_keys=ON
        # de __init__: sin él, borrar un libro dejaba capítulos, segmentos
        # y personajes huérfanos (las CASCADE del esquema eran inertes).
        self.conn.execute(
            "DELETE FROM segments WHERE chapter_id IN "
            "(SELECT id FROM chapters WHERE book_id=?)", (book_id,))
        self.conn.execute("DELETE FROM chapters WHERE book_id=?", (book_id,))
        self.conn.execute("DELETE FROM characters WHERE book_id=?", (book_id,))
        self.conn.execute("DELETE FROM books WHERE id=?", (book_id,))
        self._commit()

    def book_by_path(self, path):
        """Devuelve el Book ya importado desde esa ruta, o None (M11:
        re-importar el mismo archivo no debe duplicar el libro)."""
        row = self.conn.execute(
            "SELECT id, title, path, fmt FROM books WHERE path=?",
            (str(path),)).fetchone()
        return Book(*row) if row else None
