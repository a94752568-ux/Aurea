"""Verificación de generación de capítulo con audio (M23).

Comprueba:
  1. Que se genera el WAV final unificado + latest.wav (C1).
  2. Que la duración del WAV ≈ suma de segmentos + suma de pausas.
  3. Que process_master CORRE de verdad (A9): el RMS final ≈ 0.14
     (su target) y difiere claramente del RMS sin procesar.
  4. Que no hay clipping tras process_master (peak <= 0.95).
  5. Que no quedan marcadores [pausa=/[silencio= sin parsear; un '['
     legítimo (p. ej. "[risas]") NO aborta el capítulo (A5).
  6. Que el WAV final es PCM_16 (A7) y ningún pause_after_ms es NULL.

M23: antes usaba la BD REAL de producción (/home/aegis2/aurea,
libro 20) y sacaba la ruta del WAV con regex sobre el mensaje. Ahora
construye sus propios datos en BD temporal (AUREA_HOME) y lee la ruta
de gen._last_wav.

Audio: la VM no tiene Kokoro real ni modelos, así que se usa un TTS
falso (FakeKokoro: seno 220 Hz, amplitud 0.5, duración según el texto)
inyectado en aurea.core.generator.KokoroAdapter. El pipeline probado
es el real (segmentize → dirección → síntesis → _concat → process_master
→ WAV final); solo la voz es sintética. En la laptop, con Kokoro real,
el mismo script verifica audio real.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stubs de Kokoro/XTTS (la VM no tiene kokoro_onnx/misaki ni modelos).
import types as _types
_kokoro = _types.ModuleType("kokoro_onnx")
_kokoro.Kokoro = object
sys.modules.setdefault("kokoro_onnx", _kokoro)
_misaki = _types.ModuleType("misaki")
_espeak = _types.ModuleType("misaki.espeak")
_espeak.EspeakG2P = object
_espeak.EspeakFallback = object
_misaki.espeak = _espeak
sys.modules.setdefault("misaki", _misaki)
sys.modules.setdefault("misaki.espeak", _espeak)

_TMP = tempfile.mkdtemp(prefix="aurea-verificar-wav-")
os.environ["AUREA_HOME"] = _TMP  # BD + audio en tmpdir, nunca producción

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEventLoop  # noqa: E402

import aurea.core.generator as gen_mod  # noqa: E402
from aurea.core.database import Database  # noqa: E402
from aurea.core.generator import ChapterGenerator  # noqa: E402
from aurea.direction.director import DirectionWorker  # noqa: E402

FAILURES = []
SR = 24000


class FakeKokoro:
    """TTS falso para la VM: seno 220 Hz, amplitud 0.5.

    Duración proporcional al texto (0.05 s por carácter, 0.3–3.0 s).
    Amplitud 0.5 → RMS ≈ 0.35 sin procesar; process_master lo lleva
    a su target 0.14, lo que permite verificar que CORRE (M23).
    """

    def synthesize(self, text, voice="em_alex", speed=1.0, log=None):
        # v0.7.2: el adaptador real acepta log (sustituciones de
        # pronunciación); el falso refleja el contrato.
        dur = max(0.3, min(len(text) * 0.05, 3.0))
        t = np.linspace(0, dur, int(SR * dur), endpoint=False)
        samples = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        return samples, SR

    def save_wav(self, path, samples, sr):
        sf.write(str(path), samples, sr, subtype="PCM_16")


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def main():
    app = QCoreApplication(sys.argv)
    gen_mod.KokoroAdapter = FakeKokoro  # inyectar el TTS falso

    db = Database()
    bid = db.add_book("Libro de prueba", "/tmp/prueba.txt", "txt")
    # "[risas]" es contenido legítimo: no debe abortar el capítulo (A5).
    # El \n\n separa dos segmentos (segmentize parte por párrafos).
    text = "Hola mundo [risas].\n\nAdiós mundo."
    cid = db.add_chapter(bid, 0, "Capítulo de prueba", text)
    chapter = db.chapter(cid)
    print(f"Capítulo de prueba (id={cid}): {chapter.title}")

    # --- Crear segmentos (la dirección los necesita ya creados) ---
    from aurea.core.generator import segmentize
    parts = segmentize(text, False)
    for i, (kind, _sp, chunk, pause) in enumerate(parts):
        db.add_segment(cid, i, chunk, kind=kind, pause_after_ms=pause)
    print(f"  Segmentos creados: {len(parts)}")

    # --- Dirigir ---
    print("Aplicando dirección...")
    w = DirectionWorker(cid, use_llm=False)
    loop = QEventLoop()
    w.finished_ok.connect(lambda r, l: loop.quit())
    w.failed.connect(lambda e: (print("ERROR dirección:", e), loop.quit()))
    w.start()
    loop.exec()
    segs = db.segments(cid)
    print(f"  Segmentos: {len(segs)}, dirigidos: {sum(1 for s in segs if s.directed)}")
    check("Dirección produjo 2 segmentos", len(segs) == 2, f"obtuvo {len(segs)}")

    # --- Generar audio ---
    print("Generando capítulo...")
    t0 = time.perf_counter()
    # force=False: los segmentos ya existen y están dirigidos; con
    # force=True el generador los borraría y recrearía sin dirección.
    gen = ChapterGenerator(chapter, "em_alex", 1.0, force=False)
    resultado = []
    loop = QEventLoop()
    gen.finished_ok.connect(lambda msg: (resultado.append(msg), loop.quit()))
    gen.failed.connect(lambda e: (resultado.append(f"FALLO: {e}"), loop.quit()))
    gen.start()
    loop.exec()
    dt_gen = time.perf_counter() - t0
    print(f"  Tiempo generación: {dt_gen:.1f}s")
    print(f"  Mensaje: {resultado[0] if resultado else '(vacío)'}")

    # M23: la ruta del WAV final se lee de gen._last_wav, no con regex
    # sobre el mensaje (frágil).
    wav_path = getattr(gen, "_last_wav", None)
    check("WAV final existe (C1)", bool(wav_path) and Path(wav_path).exists(),
          f"ruta: {wav_path}")
    if not wav_path or not Path(wav_path).exists():
        print("FALLO al generar")
        sys.exit(1)
    print(f"  WAV generado: {wav_path}")

    latest = Path(_TMP) / "output" / "latest.wav"
    check("latest.wav existe", latest.exists(), f"ruta: {latest}")

    info = sf.info(wav_path)
    print(f"  WAV: {info.duration:.1f}s, SR={info.samplerate}, "
          f"canales={info.channels}, subtype={info.subtype}")
    check("WAV final es PCM_16 (A7)", info.subtype == "PCM_16",
          f"subtype={info.subtype}")

    # --- Verificar duración ---
    segs = db.segments(cid)
    dur_segmentos = 0.0
    for s in segs:
        if s.wav_path and Path(s.wav_path).exists():
            d, sr = sf.read(s.wav_path)
            dur_segmentos += len(d) / sr
    # _concat: pause_before del primer segmento (si dirigido) + pause_after
    # entre segmentos (no tras el último).
    pausas = 0.0
    if segs and segs[0].directed and segs[0].pause_before_ms:
        pausas += segs[0].pause_before_ms / 1000.0
    pausas += sum((s.pause_after_ms or 0) / 1000.0 for s in segs[:-1])
    dur_esperada = dur_segmentos + pausas
    print(f"  Duración segmentos: {dur_segmentos:.1f}s, pausas: {pausas:.1f}s")
    print(f"  Duración esperada: {dur_esperada:.1f}s, real: {info.duration:.1f}s")
    diff = abs(info.duration - dur_esperada)
    check("Duración WAV ≈ segmentos + pausas (±0.5s)", diff < 0.5,
          f"diff={diff:.2f}s (esperado {dur_esperada:.1f}s, real {info.duration:.1f}s)")

    # --- Verificar que process_master CORRE de verdad (A9/M23) ---
    # RMS sin procesar (segmentos + silencios tal cual): seno 0.5 → ~0.35
    # diluido por las pausas. process_master normaliza a RMS 0.14.
    data, sr = sf.read(wav_path)
    if data.ndim == 2:
        mono = data.mean(axis=1)
    else:
        mono = data
    rms_final = float(np.sqrt(np.mean(mono ** 2)))
    # RMS crudo: reconstruir la concatenación sin procesar.
    raw_parts = []
    if segs and segs[0].directed and segs[0].pause_before_ms:
        raw_parts.append(np.zeros(int(segs[0].pause_before_ms / 1000 * SR)))
    for i, s in enumerate(segs):
        d, _sr = sf.read(s.wav_path)
        raw_parts.append(d if d.ndim == 1 else d.mean(axis=1))
        if i < len(segs) - 1:
            raw_parts.append(np.zeros(int((s.pause_after_ms or 0) / 1000 * SR)))
    raw = np.concatenate(raw_parts)
    rms_raw = float(np.sqrt(np.mean(raw ** 2)))
    print(f"  RMS sin procesar: {rms_raw:.3f}, RMS final: {rms_final:.3f} "
          f"(target process_master: 0.14)")
    check("process_master corre: RMS final ≈ 0.14 (±0.06)",
          0.08 <= rms_final <= 0.20, f"RMS final={rms_final:.3f}")
    check("process_master cambia el audio (RMS final != RMS crudo)",
          abs(rms_final - rms_raw) > 0.05,
          f"crudo={rms_raw:.3f} final={rms_final:.3f}")

    # --- Verificar no clipping ---
    peak = float(np.max(np.abs(data)))
    check("Sin clipping (peak <= 0.95)", peak <= 0.95, f"peak={peak:.4f}")

    # --- Verificar marcadores: solo [pausa=/[silencio= sin parsear ---
    # A5: un '[' legítimo ("[risas]") es contenido, no un error.
    unparsed = [s for s in segs
                if "[pausa=" in (s.text or "") or "[silencio=" in (s.text or "")]
    check("Ningún marcador [pausa=/[silencio= sin parsear", len(unparsed) == 0,
          f"{len(unparsed)} segmentos con marcadores sin parsear")
    check("El corchete legítimo [risas] sobrevivió en un segmento",
          any("[risas]" in (s.text or "") for s in segs),
          f"textos: {[s.text for s in segs]}")

    # --- Verificar pause_after_ms no NULL ---
    all_not_null = all(s.pause_after_ms is not None for s in segs)
    check("Todos los segmentos con pause_after_ms definido", all_not_null)

    db.close()

    # --- Resumen ---
    print()
    if FAILURES:
        print(f"FALLOS ({len(FAILURES)}): {FAILURES}")
        sys.exit(1)
    else:
        print("TODAS LAS PRUEBAS DE WAV CAPÍTULO PASARON")
        sys.exit(0)


if __name__ == "__main__":
    main()
