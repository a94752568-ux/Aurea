"""Ambientes sonoros sintetizados + mezcla con ducking (v0.7.0).

Adaptado de ~/workspace/aurea-ambientes/generar_ambientes.py (muestras
aprobadas por Alberto 2026-11-20, "si pasa"). 100% local, CPU-only,
sin modelos, sin nube: todo es matemáticas sobre numpy + soundfile
(dependencias que AUREA ya tenía).

Piezas:
  - Generadores procedurales: viento, lluvia, fuego, selva de noche.
    Se generan a la tasa del capítulo (24 kHz) y se cachean como un
    bucle de 60 s reutilizable (`ambience_cache/` bajo AUREA_HOME).
  - `tile_bed`: repite el bucle con crossfade de 1 s en las juntas
    (sin clics al empalmar).
  - `mix_ambience`: la cama suena bajo la narración con ducking real
    (baja mientras hay voz, sube en las pausas) y fades de entrada y
    salida por capítulo (sin clics en los bordes).

Parámetros por defecto (documentados; la UI los expone):
  - bed_level 0.25: la cama suena al 25% de su nivel en los silencios.
  - duck_level 0.30: bajo la voz la cama baja al 30% de su nivel
    (es decir, al 7.5% absoluto con bed_level 0.25).
  - fade_s 2.0: fundido de entrada/salida de la cama por capítulo.
"""
from pathlib import Path

import numpy as np
import soundfile as sf

from .database import aurea_home

# --- parámetros ---------------------------------------------------------
LOOP_SECONDS = 60        # duración del bucle cacheado por ambiente
CACHE_VERSION = 1        # subir si cambia la síntesis (invalida la caché)
DEFAULT_BED_LEVEL = 0.25
DEFAULT_DUCK_LEVEL = 0.30
DEFAULT_FADE_S = 2.0
CROSSFADE_S = 1.0        # crossfade entre repeticiones del bucle

AMBIENCES = {
    "none": "Sin ambiente",
    "viento": "Viento",
    "lluvia": "Lluvia",
    "fuego": "Fuego",
    "selva_noche": "Selva de noche",
}

# Recomendación documentada para el Popol Vuh: selva de noche
# (grillos + viento suave + zumbido grave; es el paisaje del libro).
RECOMMENDED_AMBIENCE = "selva_noche"

# Semillas fijas: la misma cama suena igual en cada generación.
_SEEDS = {"viento": 1, "lluvia": 2, "fuego": 3, "selva_noche": 4}

_GENERATORS = {}


def _register(name):
    def deco(fn):
        _GENERATORS[name] = fn
        return fn
    return deco


# ----------------------------------------------------------------------
# Utilidades de síntesis (las mismas del generador original)
# ----------------------------------------------------------------------
def _ma(x, k):
    """Media móvil O(n) vía cumsum (pasa-bajas barato)."""
    k = max(1, int(k))
    c = np.concatenate([[0.0], np.cumsum(x)])
    y = (c[k:] - c[:-k]) / k
    return np.concatenate([np.full(k - 1, y[0]), y])[: len(x)]


def _brown(n, rng):
    y = np.cumsum(rng.standard_normal(n))
    y -= np.linspace(y[0], y[-1], n)  # quita deriva DC
    return y / (np.max(np.abs(y)) + 1e-9)


def _fade(x, sr, sec=0.8):
    k = int(sec * sr)
    k = min(k, len(x) // 2)
    if k <= 0:
        return x
    ramp = np.linspace(0, 1, k)
    x[:k] *= ramp
    x[-k:] *= ramp[::-1]
    return x


def _norm(x, peak=0.7):
    return (x / (np.max(np.abs(x)) + 1e-9) * peak).astype(np.float32)


def _stereo(mono, rng, width=0.25):
    """Estéreo: dos canales con semillas distintas y mezcla cruzada."""
    n = len(mono)
    extra = rng.standard_normal(n) * 0.15
    left = mono + extra * width
    right = mono - extra * width
    return np.stack([left, right], axis=1)


# ----------------------------------------------------------------------
# Generadores por ambiente (mono; el estéreo se hace al cachear)
# ----------------------------------------------------------------------
@_register("viento")
def viento(dur=20.0, seed=1, sr=24000, fade=True):
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    base = _brown(n, rng)
    # ráfagas: LFO lento (0.07 + 0.13 Hz) modulando la amplitud
    lfo = 0.55 + 0.45 * np.sin(2 * np.pi * 0.07 * t + 0.4) * np.sin(2 * np.pi * 0.13 * t)
    gust = base * lfo
    # aire agudo: ruido blanco menos su media móvil = pasa-altas
    w = rng.standard_normal(n)
    airy = (w - _ma(w, int(0.018 * sr))) * 0.12
    out = _norm(gust * 0.8 + airy, 0.65)
    return _fade(out, sr) if fade else out


@_register("lluvia")
def lluvia(dur=20.0, seed=2, sr=24000, fade=True):
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    w = rng.standard_normal(n)
    # cuerpo de lluvia: pasa-altas suave
    body = (w - _ma(w, int(0.005 * sr))) * 0.55
    # gotas: ticks cortos (seno amortiguado) en instantes aleatorios
    drops = np.zeros(n)
    n_drops = int(dur * 90)
    starts = rng.integers(0, n - 2000, n_drops)
    freqs = rng.uniform(1800, 5200, n_drops)
    amps = rng.uniform(0.05, 0.35, n_drops)
    env = np.exp(-np.arange(600) / 90.0)
    for s, f, a in zip(starts, freqs, amps):
        tt = np.arange(600) / sr
        drops[s: s + 600] += a * env * np.sin(2 * np.pi * f * tt)
    out = _norm(body + drops, 0.65)
    return _fade(out, sr) if fade else out


@_register("fuego")
def fuego(dur=20.0, seed=3, sr=24000, fade=True):
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    rumble = _ma(rng.standard_normal(n), int(0.02 * sr)) * 1.6  # rumor grave
    # chasquidos: impulsos de ruido con decaimiento rápido
    crack = np.zeros(n)
    n_crack = int(dur * 14)
    starts = rng.integers(0, n - 3000, n_crack)
    for s in starts:
        L = int(rng.integers(300, 2500))
        env = np.exp(-np.arange(L) / rng.uniform(60, 300))
        crack[s: s + L] += rng.standard_normal(L) * env * rng.uniform(0.3, 1.0)
    crack = crack - _ma(crack, int(0.003 * sr))  # quita graves del chasquido
    out = _norm(rumble * 0.5 + crack * 0.8, 0.65)
    return _fade(out, sr) if fade else out


@_register("selva_noche")
def selva_noche(dur=20.0, seed=4, sr=24000, fade=True):
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    # grillos: 2 "voces" con patrones distintos
    sig = np.zeros(n)
    for f0, rate in ((4200, 0.9), (5100, 0.6)):
        chirp = np.zeros(n)
        # un canto = 3-4 pulsos de 40 ms separados 60 ms
        pulse = np.sin(2 * np.pi * f0 * np.arange(int(0.04 * sr)) / sr)
        pulse *= np.hanning(len(pulse))
        pos = 0.2
        while pos < dur - 0.5:
            for j in range(int(rng.integers(3, 5))):
                s = int((pos + j * 0.10) * sr)
                chirp[s: s + len(pulse)] += pulse * rng.uniform(0.25, 0.5)
            pos += rng.uniform(0.7, 1.4) / rate
        sig += chirp
    # cama: viento muy suave + zumbido grave de la noche
    bed = viento(dur, seed=seed + 100, sr=sr, fade=False) * 0.35
    drone = 0.08 * np.sin(2 * np.pi * 55 * t)
    mono = sig * 0.5 + bed + drone
    out = _norm(mono, 0.65)
    return _fade(out, sr) if fade else out


# ----------------------------------------------------------------------
# Caché de camas (un bucle de 60 s por ambiente, reutilizable)
# ----------------------------------------------------------------------
class BedCache:
    """Caché en disco de bucles de ambiente.

    Clave: <ambiente>_<sr>hz_v<CACHE_VERSION>.wav bajo
    AUREA_HOME/ambience_cache/. La primera generación sintetiza el
    bucle (unos segundos de CPU); las siguientes lo leen de disco.
    """

    def __init__(self, root=None):
        self.root = Path(root) if root else aurea_home() / "ambience_cache"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, name, sr):
        return self.root / f"bed_{name}_{int(sr)}hz_v{CACHE_VERSION}.wav"

    def get_bed(self, name, sr=24000):
        """Devuelve (muestras estéreo float32, sr) del bucle de 60 s.

        Genera y guarda el WAV si no existe en caché. Lanza ValueError
        si el ambiente no existe.
        """
        if name not in _GENERATORS:
            raise ValueError(f"Ambiente desconocido: {name!r}")
        path = self.path_for(name, sr)
        if path.exists():
            data, file_sr = sf.read(str(path), dtype="float32")
            if file_sr == sr:
                return np.asarray(data, dtype=np.float32), file_sr
        mono = _GENERATORS[name](LOOP_SECONDS, seed=_SEEDS[name], sr=sr, fade=False)
        rng = np.random.default_rng(99)
        stereo = _stereo(mono, rng).astype(np.float32)
        sf.write(str(path), stereo, sr, subtype="PCM_16")
        # Releer: el WAV cacheado es la fuente de verdad (PCM_16).
        data, file_sr = sf.read(str(path), dtype="float32")
        return np.asarray(data, dtype=np.float32), file_sr


# ----------------------------------------------------------------------
# Empalme del bucle (crossfade en las juntas)
# ----------------------------------------------------------------------
def tile_bed(bed, n_samples, sr, crossfade_s=CROSSFADE_S):
    """Repite el bucle hasta cubrir n_samples, con crossfade de 1 s
    en cada junta para que no haya clics al empalmar."""
    bed = np.asarray(bed, dtype=np.float32)
    if bed.ndim == 1:
        bed = np.stack([bed, bed], axis=1)
    n_samples = int(n_samples)
    L = len(bed)
    if n_samples <= 0:
        return np.zeros((0, 2), dtype=np.float32)
    if n_samples <= L:
        return bed[:n_samples].copy()
    cf = min(int(crossfade_s * sr), L // 4)
    ramp = np.linspace(0, 1, cf, dtype=np.float32)[:, None]
    out = np.zeros((n_samples, 2), dtype=np.float32)
    out[:L] = bed
    pos = L  # fin del último bucle colocado
    while pos < n_samples:
        start = pos - cf  # la siguiente repetición solapa cf muestras
        take = min(L, n_samples - start)
        seg = bed[:take]
        if take <= cf:
            # Cola final más corta que el crossfade: fundir lo que queda.
            r = ramp[:take]
            out[start:start + take] = (
                out[start:start + take] * (1 - r) + seg * r)
            break
        out[start:pos] = out[start:pos] * (1 - ramp) + seg[:cf] * ramp
        out[pos:start + take] = seg[cf:]
        pos = start + take
    return out


# ----------------------------------------------------------------------
# Ducking (la cama baja con la voz, sube en las pausas)
# ----------------------------------------------------------------------
def voice_activity(narration, sr, win_ms=50, attack_ms=80, release_ms=300):
    """Envolvente de actividad de voz 0..1 por muestra.

    RMS por ventana de 50 ms, normalizado contra el percentil 90
    (nivel típico de voz). Ataque rápido (la cama baja en cuanto
    empieza la voz) y liberación lenta (la cama no sube de golpe
    entre palabras).
    """
    audio = np.asarray(narration, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    win = max(1, int(win_ms * sr / 1000))
    n_frames = int(np.ceil(len(audio) / win))
    padded = np.zeros(n_frames * win, dtype=np.float32)
    padded[: len(audio)] = audio
    frames = padded.reshape(n_frames, win)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    ref = np.percentile(rms, 90) if len(rms) else 0.0
    if ref < 1e-6:
        return np.zeros(len(audio), dtype=np.float32)
    raw = np.clip(rms / (0.35 * ref), 0.0, 1.0)
    dt = win_ms / 1000.0
    a_up = 1.0 - np.exp(-dt / (attack_ms / 1000.0))
    a_down = 1.0 - np.exp(-dt / (release_ms / 1000.0))
    act = np.zeros_like(raw)
    prev = raw[0]
    for i, v in enumerate(raw):
        a = a_up if v > prev else a_down
        prev = prev + (v - prev) * a
        act[i] = prev
    # Interpolar a resolución de muestra.
    centers = (np.arange(n_frames) + 0.5) * win
    return np.interp(
        np.arange(len(audio)), centers, act,
        left=act[0], right=act[-1]).astype(np.float32)


def mix_ambience(narration, bed, sr, bed_level=DEFAULT_BED_LEVEL,
                 duck_level=DEFAULT_DUCK_LEVEL, fade_s=DEFAULT_FADE_S):
    """Mezcla la cama bajo la narración con ducking y fades.

    narration: (N,) o (N,2) float32. bed: bucle estéreo (cualquier
    duración; se repite con crossfade). Devuelve (N,2) float32.
    La cama suena a bed_level en los silencios y baja a
    bed_level*duck_level mientras hay voz. Fades lineales de fade_s
    en la cama al inicio y al final del capítulo.
    """
    audio = np.asarray(narration, dtype=np.float32)
    if audio.ndim == 1:
        audio = np.stack([audio, audio], axis=1)
    n = len(audio)
    if n == 0:
        return audio
    bed_tiled = tile_bed(bed, n, sr)
    act = voice_activity(audio, sr)
    gain = 1.0 - act * (1.0 - duck_level)  # 1.0 en silencio, duck_level con voz
    bed_mix = bed_tiled * (bed_level * gain)[:, None]
    # Fades de entrada/salida SOLO sobre la cama (la voz no se toca).
    fade_n = min(int(fade_s * sr), n // 2)
    if fade_n > 0:
        ramp = np.linspace(0, 1, fade_n, dtype=np.float32)[:, None]
        bed_mix[:fade_n] *= ramp
        bed_mix[-fade_n:] *= ramp[::-1]
    out = audio + bed_mix
    # Seguridad: si la suma supera el rango, escalar todo (no recortar).
    peak = float(np.max(np.abs(out))) if n else 0.0
    if peak > 0.99:
        out = out * (0.99 / peak)
    return out.astype(np.float32)


def mix_chapter(narration, ambience, sr, bed_level=DEFAULT_BED_LEVEL,
                duck_level=DEFAULT_DUCK_LEVEL, fade_s=DEFAULT_FADE_S,
                cache=None):
    """Atajo: cama desde la caché + mezcla. Si ambience es 'none' o
    vacío, devuelve la narración en estéreo sin tocarla."""
    audio = np.asarray(narration, dtype=np.float32)
    if audio.ndim == 1:
        audio = np.stack([audio, audio], axis=1)
    if not ambience or ambience == "none":
        return audio
    cache = cache or BedCache()
    bed, bsr = cache.get_bed(ambience, sr)
    return mix_ambience(audio, bed, bsr, bed_level, duck_level, fade_s)
