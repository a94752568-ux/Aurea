"""Suite v0.7.1: normalización por segmento (A9 completado).

Ejecutar: QT_QPA_PLATFORM=offscreen pytest tests/test_v071.py -v

Stubs: como en test_aurea.py / test_v070.py (kokoro_onnx/misaki no existen
en la VM). El TTS real y las voces reales solo se verifican en la laptop
de Alberto.

Decisión medida (ver /tmp/medir_v071.py, 2026-11-21): variante LIGERA
(normalizar RMS 0.12 + limitar 0.90). Iguala la sonoridad exactamente
igual que la completa (RMS 0.1200 en los tres niveles probados) sin
duplicar la compresión ni los graves del master (bass_ratio 0.005 =
control, frente a 0.013 de la completa; crest 2.97 frente a 2.71).
"""
import os
import sys
import tempfile
import types
from pathlib import Path

_kokoro = types.ModuleType("kokoro_onnx")
_kokoro.Kokoro = object
sys.modules.setdefault("kokoro_onnx", _kokoro)
_misaki = types.ModuleType("misaki")
_espeak = types.ModuleType("misaki.espeak")
_espeak.EspeakG2P = object
_espeak.EspeakFallback = object
_misaki.espeak = _espeak
sys.modules.setdefault("misaki", _misaki)
sys.modules.setdefault("misaki.espeak", _espeak)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import soundfile as sf  # noqa: E402

from aurea.core.audio_processor import AudioProcessor  # noqa: E402
from aurea.core.generator import ChapterGenerator, _process_segment  # noqa: E402
from aurea.core.models import Segment  # noqa: E402

SR = 24000


@pytest.fixture
def tmp_home(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="aurea-v071-")
    monkeypatch.setenv("AUREA_HOME", tmp)
    return tmp


def _rms(x):
    x = np.asarray(x, dtype=np.float32)
    return float(np.sqrt(np.mean(x ** 2) + 1e-12))


def _synth(rms_target, dur=2.0, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * SR)) / SR
    sig = 0.7 * np.sin(2 * np.pi * 220 * t) + 0.3 * rng.standard_normal(len(t))
    return (sig / (_rms(sig) + 1e-12) * rms_target).astype(np.float32)


# ======================================================================
# Variante ligera: iguala la sonoridad entre segmentos
# ======================================================================
def test_light_hits_target_exactly():
    proc = AudioProcessor(sr=SR)
    for target in (0.05, 0.15, 0.35):
        out = proc.process_segment_light(_synth(target, seed=int(target * 100)))
        assert abs(_rms(out) - 0.12) < 0.001, f"RMS {target} -> {_rms(out)}"


def test_segments_leveled():
    for target in (0.05, 0.15, 0.35):
        out = _process_segment(_synth(target, seed=int(target * 100)), SR)
        assert abs(_rms(out) - 0.12) < 0.005


def test_segments_leveled_after_master():
    """La cadena completa (segmento -> ganancia 1.0 -> concat -> master)
    deja los tres niveles en la misma banda que el master (0.14 ± 0.06,
    como verificar_wav_capitulo)."""
    proc = AudioProcessor(sr=SR)
    for target in (0.05, 0.35):
        seg = _process_segment(_synth(target, seed=int(target * 100)), SR)
        master = proc.process_master(seg)
        assert 0.08 < _rms(master) < 0.20, f"target {target}: RMS final {_rms(master)}"


# ======================================================================
# Guarda de silencio
# ======================================================================
def test_silence_not_amplified():
    silence = _synth(1e-4, seed=7)
    out = _process_segment(silence, SR)
    # Sin procesar: el RMS no cambia (la ganancia con tope 8x sola
    # lo subiría a 0.0008; el piso explícito lo impide).
    assert _rms(out) == pytest.approx(_rms(silence), rel=1e-6)
    assert np.array_equal(out, silence)


def test_quiet_real_segment_is_processed():
    # RMS 0.01: voz real bajita, por encima del piso (0.005) -> se procesa
    # (tope de ganancia 8x -> 0.08, no llega al 0.12 pero se acerca).
    out = _process_segment(_synth(0.01, seed=8), SR)
    assert abs(_rms(out) - 0.08) < 0.005


def test_silence_floor_boundary():
    proc = AudioProcessor(sr=SR)
    assert proc.SEGMENT_SILENCE_RMS == 0.005
    below = _synth(0.004, seed=9)
    assert _rms(_process_segment(below, SR)) == pytest.approx(0.004, rel=1e-3)
    above = _synth(0.006, seed=10)
    assert _rms(_process_segment(above, SR)) > 0.02


# ======================================================================
# La dinámica dirigida sobrevive (el proceso va ANTES de _gain_for)
# ======================================================================
def test_directed_gain_preserved():
    # Susurro (gain 0.33) frente a grito (gain 1.25): tras procesar +
    # ganancia, el susurro sigue ~3.8x más bajo que el grito.
    whisper = np.clip(_process_segment(_synth(0.15, seed=11), SR) * 0.33, -1, 1)
    shout = np.clip(_process_segment(_synth(0.15, seed=12), SR) * 1.25, -1, 1)
    ratio = _rms(shout) / _rms(whisper)
    assert 3.0 < ratio < 4.6, f"ratio susurro/grito: {ratio}"


# ======================================================================
# Seguridad ante fallos
# ======================================================================
def test_processing_failure_keeps_raw(monkeypatch):
    def _boom(self, samples, sr=None):
        raise RuntimeError("fallo simulado")
    monkeypatch.setattr(AudioProcessor, "process_segment_light", _boom)
    seg = _synth(0.15, seed=13)
    out = _process_segment(seg, SR, log=lambda m: None)
    assert np.array_equal(out, seg)


def test_full_pipeline_not_used(monkeypatch):
    # La completa NUNCA se llama desde el gancho: si revienta, el gancho
    # sigue funcionando (prueba de que usa la variante ligera).
    def _boom(self, samples, sr=None):
        raise RuntimeError("la completa no debe llamarse")
    monkeypatch.setattr(AudioProcessor, "process_segment", _boom)
    out = _process_segment(_synth(0.15, seed=14), SR)
    assert abs(_rms(out) - 0.12) < 0.005


# ======================================================================
# Integración: master al final + cama después de los segmentos
# ======================================================================
def _seg_wavs(out_dir, amps):
    segs = []
    for i, amp in enumerate(amps):
        p = out_dir / f"seg{i}.wav"
        t = np.arange(int(0.5 * SR)) / SR
        sf.write(str(p), (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32),
                 SR, subtype="PCM_16")
        segs.append(Segment(i + 1, 1, i, "texto", str(p), "done",
                            kind="narration", directed=1, pause_after_ms=100))
    return segs


def test_master_still_applied(tmp_home):
    out_dir = Path(tmp_home) / "output"
    out_dir.mkdir()
    segs = _seg_wavs(out_dir, (0.5, 0.4))
    wav = out_dir / "cap.wav"
    r = ChapterGenerator._concat(segs, {}, ("kokoro", "em_alex", 1.0),
                                 wav, ambience="none")
    assert r is not None and Path(r).exists()
    data, sr = sf.read(str(r))
    assert sr == SR
    # process_master corre: RMS final cerca del target 0.14 (como
    # verificar_wav_capitulo) y sin clipping.
    assert 0.08 < _rms(data) < 0.20, f"RMS final {_rms(data)}"
    assert float(np.max(np.abs(data))) <= 0.95


def test_ambience_mixed_after_segments(tmp_home):
    # La cama se mezcla en _concat DESPUÉS de los segmentos: hay cama en
    # la pausa (0.55-0.6 s) con ambiente y silencio sin él. El procesado
    # por segmento no toca la cama (la cama no pasa por _process_segment).
    out_dir = Path(tmp_home) / "output"
    out_dir.mkdir()
    segs = _seg_wavs(out_dir, (0.5, 0.4))
    wav_none = out_dir / "cap_none.wav"
    wav_amb = out_dir / "cap_amb.wav"
    ChapterGenerator._concat(segs, {}, ("kokoro", "em_alex", 1.0),
                             wav_none, ambience="none")
    ChapterGenerator._concat(segs, {}, ("kokoro", "em_alex", 1.0),
                             wav_amb, ambience="selva_noche", bed_level=0.25)
    d_none, _ = sf.read(str(wav_none))
    d_amb, _ = sf.read(str(wav_amb))
    pause_none = _rms(d_none[int(0.55 * SR):int(0.6 * SR)])
    pause_amb = _rms(d_amb[int(0.55 * SR):int(0.6 * SR)])
    assert pause_none < 0.01
    assert pause_amb > 0.002
    assert pause_amb > 3 * max(pause_none, 1e-6)
