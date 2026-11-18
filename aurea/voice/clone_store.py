"""Perfiles de voz clonada: esquema, alta y consulta. SQLite en aurea.db."""
import os, sqlite3, time

# Tres dirname: voice/ → aurea/ (paquete) → raíz del proyecto
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(_ROOT, "aurea.db")
VOICES_DIR = os.path.join(_ROOT, "voices")

def _connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def clone_sample_map():
    """Return a dict mapping character_id to sample_path from voice_profiles."""
    ensure_schema()
    con = _connect()
    rows = con.execute(
        "SELECT p.name, cp.id, p.sample_path "
        "FROM voice_profiles p "
        "JOIN characters cp ON cp.voice_profile_id = p.id"
    ).fetchall()
    con.close()
    return {row["id"]: row["sample_path"] for row in rows}


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None

def ensure_schema():
    con = _connect()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS voice_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        sample_path TEXT NOT NULL,
        notes TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)
    # Columnas nuevas en characters (solo si la tabla ya existe; la crea CastManager)
    if _table_exists(con, "characters"):
        cols = {r[1] for r in con.execute("PRAGMA table_info(characters)")}
        if "engine" not in cols:
            con.execute("ALTER TABLE characters ADD COLUMN engine TEXT DEFAULT 'kokoro'")
        if "voice_profile_id" not in cols:
            con.execute("ALTER TABLE characters ADD COLUMN voice_profile_id INTEGER")
    con.commit(); con.close()

def add_profile(name, sample_path, notes=""):
    ensure_schema()
    con = _connect()
    cur = con.execute(
        "INSERT INTO voice_profiles (name, sample_path, notes) VALUES (?,?,?)",
        (name, sample_path, notes))
    con.commit(); pid = cur.lastrowid; con.close()
    return pid

def list_profiles():
    ensure_schema()
    con = _connect()
    rows = con.execute("SELECT * FROM voice_profiles ORDER BY name").fetchall()
    con.close()
    return [dict(r) for r in rows]

def get_profile(pid):
    ensure_schema()
    con = _connect()
    row = con.execute("SELECT * FROM voice_profiles WHERE id=?", (pid,)).fetchone()
    con.close()
    return dict(row) if row else None

def delete_profile(pid):
    ensure_schema()
    con = _connect()
    p = get_profile(pid)
    if p and os.path.exists(p["sample_path"]):
        os.remove(p["sample_path"])
    con.execute("UPDATE characters SET engine='kokoro', voice_profile_id=NULL WHERE voice_profile_id=?", (pid,))
    con.execute("DELETE FROM voice_profiles WHERE id=?", (pid,))
    con.commit(); con.close()

def assign_character(character_id, engine, profile_id=None):
    ensure_schema()
    con = _connect()
    con.execute("UPDATE characters SET engine=?, voice_profile_id=? WHERE id=?",
                (engine, profile_id, character_id))
    con.commit(); con.close()