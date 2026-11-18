"""Importación y análisis de muestras: mp3/wav/flac/m4a/ogg → WAV 22050 Hz mono limpio."""
import os, subprocess, tempfile
import numpy as np
import soundfile as sf

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VOICES_DIR = os.path.join(_ROOT, "voices")

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
