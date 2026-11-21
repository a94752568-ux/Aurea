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
set -euo pipefail
cd "$(dirname "$0")"

STAMP=$(date +%Y%m%d_%H%M)
BACKUP="backup_fase4c_${STAMP}"
echo "→ Respaldo en ${BACKUP}"
mkdir -p "$BACKUP"
cp -r aurea "$BACKUP/" 2>/dev/null || true

mkdir -p aurea/voice voices

# ---------- aurea/voice/__init__.py ----------
write_once aurea/voice/__init__.py <<'EOF'
EOF

# ---------- aurea/voice/clone_store.py ----------
write_once aurea/voice/clone_store.py <<'EOF'
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
EOF

# ---------- aurea/voice/sample_prep.py ----------
write_once aurea/voice/sample_prep.py <<'EOF'
"""Importación y análisis de muestras: mp3/wav/flac/m4a/ogg → WAV 22050 Hz mono limpio."""
import os, subprocess, tempfile
import numpy as np
import soundfile as sf

SR = 22050
MIN_SEC, MAX_SEC = 6.0, 60.0
VALID = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus")

def convert(src_path, out_path):
    """FFmpeg: mono, 22050 Hz, normalizado a -3 dBFS, silencios extremos recortados."""
    tmp = out_path + ".tmp.wav"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", src_path,
        "-af", "silenceremove=start_periods=1:start_threshold=-45dB,"
               "areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
               "loudnorm=I=-19:TP=-3",
        "-ar", str(SR), "-ac", "1", tmp,
    ], check=True)
    data, sr = sf.read(tmp, dtype="float32")
    os.replace(tmp, out_path)
    return out_path

def analyze(wav_path):
    """Informe de calidad: duración, pico, RMS, recorte y SNR estimado."""
    data, sr = sf.read(wav_path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    dur = len(data) / sr
    peak = float(np.max(np.abs(data))) if len(data) else 0.0
    rms = float(np.sqrt(np.mean(data ** 2))) if len(data) else 0.0
    clipped = float(np.mean(np.abs(data) > 0.99)) if len(data) else 0.0
    # SNR estimado: percentil 5 de energía por ventanas de 50 ms como "ruido"
    win = int(sr * 0.05)
    n = len(data) // win
    if n >= 4:
        e = np.array([np.mean(data[i*win:(i+1)*win] ** 2) for i in range(n)])
        e = e[e > 1e-10]
        noise = float(np.percentile(e, 5)) if len(e) else 0.0
        snr = 10 * np.log10(max(rms ** 2, 1e-10) / max(noise, 1e-10))
    else:
        snr = 0.0
    problems = []
    if dur < MIN_SEC: problems.append(f"Muy corta ({dur:.1f}s < {MIN_SEC:.0f}s mínimos)")
    if dur > MAX_SEC: problems.append(f"Muy larga ({dur:.1f}s); se recomienda 10-30 s")
    if clipped > 0.001: problems.append("Recorte detectado (picos saturados)")
    if snr < 15: problems.append(f"SNR bajo ({snr:.0f} dB < 15 dB): mucho ruido de fondo")
    verdict = "LISTA" if not problems else "CON AVISOS"
    return {"duration": round(dur, 1), "peak_dbfs": round(20*np.log10(max(peak,1e-6)), 1),
            "snr_db": round(snr, 1), "clipped_pct": round(clipped*100, 2),
            "verdict": verdict, "problems": problems}

def import_sample(src_path, profile_name):
    """Convierte, analiza y registra el perfil. Devuelve (profile_id, report)."""
    if not src_path.lower().endswith(VALID):
        raise ValueError(f"Formato no soportado: {src_path}")
    os.makedirs(VOICES_DIR, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile_name)
    out = os.path.join(VOICES_DIR, f"{safe}.wav")
    convert(src_path, out)
    report = analyze(out)
    if report["duration"] < MIN_SEC:
        os.remove(out)
        raise ValueError("Muestra descartada: " + "; ".join(report["problems"]))
    from . import clone_store
    pid = clone_store.add_profile(profile_name, out)
    return pid, report
EOF

# ---------- aurea/voice/xtts_adapter.py ----------
write_once aurea/voice/xtts_adapter.py <<'EOF'
"""Adaptador XTTS-v2 (clonación, CPU). Mismo contrato que KokoroAdapter:
cargar (lazy), generar, cancelar, liberar."""
import os, threading, time
import numpy as np
import soundfile as sf

class XTTSAdapter:
    SAMPLE_RATE = 24000

    def __init__(self):
        self._tts = None
        self._lock = threading.Lock()
        self._cancel = False

    # ---------- contrato ----------
    def load(self):
        with self._lock:
            if self._tts is None:
                from TTS.api import TTS  # coqui-tts
                self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
        return self

    def generate(self, text, speaker_wav, speed=1.0, out_path=None, language="es"):
        """Genera audio clonado. Devuelve (audio ndarray float32, sr)."""
        self.load()
        self._cancel = False
        t0 = time.time()
        with self._lock:
            if self._cancel:
                return None, self.SAMPLE_RATE
            wav = self._tts.tts(text=text, speaker_wav=speaker_wav,
                                language=language, speed=speed)
        audio = np.asarray(wav, dtype=np.float32)
        if out_path:
            sf.write(out_path, audio, self.SAMPLE_RATE)
        return audio, self.SAMPLE_RATE

    def cancel(self):
        self._cancel = True

    def release(self):
        with self._lock:
            self._tts = None

    # ---------- utilidades ----------
    @staticmethod
    def apply_intensity(audio, intensity=1.0):
        """Dirección sin timbre emocional: ganancia acotada (evita clip)."""
        g = max(0.2, min(1.6, float(intensity)))
        out = audio * g
        peak = np.max(np.abs(out)) or 1.0
        if peak > 0.98:
            out = out * (0.98 / peak)
        return out

    @staticmethod
    def silence(seconds):
        return np.zeros(int(seconds * 24000), dtype=np.float32)
EOF

# ---------- scripts/smoke_fase4c.py ----------
mkdir -p scripts
write_once scripts/smoke_fase4c.py <<'EOF'
"""Prueba de humo Fase 4c: crea un perfil desde un WAV sintético de Kokoro,
genera una frase clonada y mide RTF/RAM."""
import os, sys, time, resource
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from aurea.voice import sample_prep, clone_store
from aurea.voice.xtts_adapter import XTTSAdapter

os.makedirs("output", exist_ok=True)

# 1) Perfil de prueba: si no hay muestras reales, se usa una voz Kokoro exportada
profiles = clone_store.list_profiles()
if profiles:
    pid = profiles[0]["id"]; name = profiles[0]["name"]
    src = profiles[0]["sample_path"]
    print(f"Perfil existente: {name}")
else:
    # genera 8 s de voz Kokoro como muestra fuente (si el motor base está disponible)
    try:
        from aurea.core.tts import KokoroAdapter
        k = KokoroAdapter().load()
        audio, sr = k.generate("Esta es una muestra de prueba para el laboratorio de voces clonadas de Aurea.",
                               voice="em_alex")
        src = "output/_muestra_prueba.wav"
        import soundfile as sf; sf.write(src, audio, sr)
    except Exception:
        # último recurso: tono modulado (solo valida el pipeline, no la calidad)
        sr = 22050; t = np.linspace(0, 8, sr*8)
        audio = (0.3*np.sin(2*np.pi*180*t*(1+0.2*np.sin(2*np.pi*0.5*t)))).astype(np.float32)
        src = "output/_muestra_prueba.wav"
        import soundfile as sf; sf.write(src, audio, sr)
    pid, report = sample_prep.import_sample(src, "prueba_fase4c")
    print("Informe:", report)

# 2) Generación clonada
p = clone_store.get_profile(pid)
xtts = XTTSAdapter()
texto = "El viejo faro resistía estoico el embate de las olas, como lo había hecho por siglos."
t0 = time.time()
audio, sr = xtts.generate(texto, speaker_wav=p["sample_path"], speed=1.0,
                          out_path="output/smoke_fase4c.wav")
dt = time.time() - t0
dur = len(audio) / sr
ram = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(f"OK: {dur:.1f}s de audio en {dt:.1f}s → RTF {dt/max(dur,0.01):.2f} | RAM pico {ram:.0f} MB")
print("Archivo: output/smoke_fase4c.wav")
EOF

chmod +x scripts/smoke_fase4c.py
echo "✔ Fase 4c instalada. Siguiente paso: pip install coqui-tts (si no lo hiciste) y:"
echo "  python scripts/smoke_fase4c.py"