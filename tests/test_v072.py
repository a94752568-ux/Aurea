"""v0.7.2 — mitigación de anomalías de voz (pronunciación, clics, volumen).

Diagnóstico medido en VM con Kokoro real (fp16, em_alex/em_santa/ef_dora)
y faster-whisper tiny como oído aproximado; ver /tmp/v072/*.txt.
"""
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurea.core.audio_processor import AudioProcessor
from aurea.core.generator import (
    ChapterGenerator,
    _apply_directed_gain,
    _split_chunk,
)
from aurea.voice.pronunciation import (
    apply_lexicon,
    expand_numbers,
    number_to_words_es,
    prepare_text_for_tts,
)

SR = 24000


def _rms(x):
    x = np.asarray(x, dtype=np.float32)
    return float(np.sqrt(np.mean(x ** 2) + 1e-12))


def _synth(rms_target, dur=2.0, seed=0, crest="sine"):
    """Señal sintética con RMS dado. crest='spiky' imita voz real
    (cresta alta: picos de 1.2-1.4 tras normalizar a 0.12)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(SR * dur)) / SR
    if crest == "spiky":
        sig = (np.sin(2 * np.pi * 220 * t)
               + 0.9 * np.sin(2 * np.pi * 440 * t + 1.0)
               + 0.6 * rng.standard_normal(len(t)))
    else:
        sig = np.sin(2 * np.pi * 220 * t)
    return (sig / (_rms(sig) + 1e-12) * rms_target).astype(np.float32)


# ------------------------------------------------------------------ #
# (1) Pronunciación: G2P latinoamericano (seseo, no ceceo)
# ------------------------------------------------------------------ #
# NOTA: tests/test_aurea.py sustituye misaki/kokoro_onnx por módulos
# vacíos (EspeakG2P = object) para que la suite corra sin el modelo.
# Por eso los fonemas se verifican en un SUBPROCESO con el misaki real,
# no en el proceso de pytest.
_G2P_PROBE = """
from misaki.espeak import EspeakG2P
g2p = EspeakG2P(language="es-419")
for w in ["Moctezuma", "cielo", "zapato", "hace", "Citlali",
          "Quetzalcóatl", "corazón", "gracias", "diez", "baja", "paja"]:
    ph, _ = g2p(w)
    print(w + "\\t" + ph)
"""


def _run_g2p_probe():
    import subprocess
    r = subprocess.run([sys.executable, "-c", _G2P_PROBE],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    out = {}
    for line in r.stdout.strip().splitlines():
        word, ph = line.split("\t")
        out[word] = ph
    return out


def test_g2p_es419_sin_ceceo():
    ph = _run_g2p_probe()
    for word in ["Moctezuma", "cielo", "zapato", "hace", "Citlali",
                 "Quetzalcóatl", "corazón", "gracias", "diez"]:
        assert "θ" not in ph[word], f"{word} con ceceo: {ph[word]}"
        assert "s" in ph[word], f"{word} sin /s/: {ph[word]}"


def test_g2p_baja_con_b():
    # "baja"->"paja" NO se reprodujo: el G2P da /b/ y el ASR distingue
    # baja/paja en par mínimo con las 3 voces. Se documenta, no se inventa
    # una reescritura.
    ph = _run_g2p_probe()
    assert ph["baja"].startswith("b"), ph["baja"]
    assert ph["paja"].startswith("p"), ph["paja"]
    assert ph["baja"] != ph["paja"]


def test_adapter_usa_es419():
    from aurea.tts.kokoro_adapter import G2P_LANGUAGE
    assert G2P_LANGUAGE == "es-419"


# ------------------------------------------------------------------ #
# (1) Pronunciación: léxico (nombres mayas) y números
# ------------------------------------------------------------------ #
def test_lexicon_nombres_mayas():
    out, subs = prepare_text_for_tts(
        "Ixbalanqué bajó a Xibalbá con Ixquic.")
    assert "Ishbalanqué" in out and "Shibalbá" in out and "Ishquic" in out
    assert len(subs) == 3


def test_lexicon_respeta_mayusculas_y_no_toca_otras():
    out, _ = prepare_text_for_tts("ixbalanqué e IXBALANQUÉ y Hunahpú.")
    assert "ishbalanqué" in out and "ISHBALANQUÉ" in out
    assert "Hunahpú" in out  # sin entrada: intacto


def test_lexicon_no_modifica_el_texto_original():
    original = "Ixbalanqué bajó a Xibalbá."
    out, _ = prepare_text_for_tts(original)
    assert original == "Ixbalanqué bajó a Xibalbá."  # la BD/el guion no se tocan
    assert out != original


def test_numeros_a_palabras():
    assert number_to_words_es(0) == "cero"
    assert number_to_words_es(1) == "uno"
    assert number_to_words_es(16) == "dieciséis"
    assert number_to_words_es(21) == "veintiuno"
    assert number_to_words_es(42) == "cuarenta y dos"
    assert number_to_words_es(100) == "cien"
    assert number_to_words_es(101) == "ciento uno"
    assert number_to_words_es(1000) == "mil"
    assert number_to_words_es(1519) == "mil quinientos diecinueve"
    assert number_to_words_es(1000000) == "un millón"


def test_expand_numbers_casos():
    assert expand_numbers("Había 3 guerreros.") == "Había tres guerreros."
    assert expand_numbers("año 1519") == "año mil quinientos diecinueve"
    assert expand_numbers("1,000 granos") == "mil granos"
    assert expand_numbers("1.000.000 de pesos") == "un millón de pesos"
    assert expand_numbers("3.5 litros") == "tres punto cinco litros"
    assert expand_numbers("3,5 litros") == "tres coma cinco litros"
    assert expand_numbers("el 50% del maíz") == "el cincuenta por ciento del maíz"


def test_expand_numbers_deja_versiones():
    assert expand_numbers("versión 2.1.2") == "versión 2.1.2"


# ------------------------------------------------------------------ #
# (2) Clics: _split_chunk nunca corta a mitad de palabra
# ------------------------------------------------------------------ #
def test_split_chunk_no_corta_palabras():
    texto = ("cuando el sol se ocultó tras los volcanes y la noche llegó a "
             "Tenochtitlan los sacerdotes encendieron el fuego nuevo sobre "
             "el pecho de los cautivos mientras el pueblo guardaba silencio "
             "y las estrellas comenzaban a aparecer una por una sobre el lago "
             "y el viento frío bajaba de las montañas trayendo el olor de la "
             "lluvia que no tardaría en caer sobre los campos de maíz y las "
             "chinampas y los niños dormían ya en sus petates mientras los "
             "ancianos contaban historias de los dioses antiguos que habían "
             "creado el mundo y lo habían destruido cuatro veces antes de esta "
             "quinta era del sol y los guerreros afilaban sus macuahuitl")
    chunks = list(_split_chunk(texto))
    assert len(chunks) >= 2
    # Reconstrucción exacta uniendo con un espacio.
    assert " ".join(chunks) == texto
    # Ningún chunk termina a mitad de palabra: el carácter siguiente en el
    # original es un espacio (o fin de texto).
    pos = 0
    for chunk in chunks[:-1]:
        pos += len(chunk)
        assert texto[pos] == " ", f"corte a mitad de palabra en {pos}: ...{texto[pos-15:pos+15]!r}"


def test_split_chunk_caso_real_afilaban():
    # Regresión del caso medido: "afil" | "aban" se oían como dos palabras.
    texto = "x " * 300 + "los guerreros afilaban sus macuahuitl de obsidiana"
    chunks = list(_split_chunk(texto))
    joined = " ".join(chunks)
    assert "afilaban" in joined
    assert not any(c.endswith("afil") for c in chunks)


# ------------------------------------------------------------------ #
# (2) Clics: juntas de _concat (DC + fundido de borde)
# ------------------------------------------------------------------ #
def _abrupt_segment(path, freq=220.0, dur=1.0, dc=0.05, phase=0.0):
    """Segmento que termina BRUSCO (sin relleno de silencio): el peor
    caso para una junta (XTTS / caché vieja)."""
    t = np.arange(int(SR * dur)) / SR
    sig = (0.5 * np.sin(2 * np.pi * freq * t + phase) + dc).astype(np.float32)
    sf.write(str(path), sig, SR, subtype="PCM_16")
    return path


class _Seg:
    def __init__(self, idx, wav_path, pause_after_ms=450):
        self.idx = idx
        self.wav_path = wav_path
        self.directed = True
        self.pause_before_ms = None
        self.pause_after_ms = pause_after_ms


def test_concat_juntas_sin_clic(tmp_path):
    a = _abrupt_segment(tmp_path / "a.wav", freq=220.0, dc=0.05)
    b = _abrupt_segment(tmp_path / "b.wav", freq=330.0, dc=-0.04, phase=1.3)
    out = tmp_path / "cap.wav"
    segs = [_Seg(0, str(a)), _Seg(1, str(b))]
    ChapterGenerator._concat(segs, {}, ("kokoro", "em_alex", 1.0), out,
                             ambience="none")
    audio, sr = sf.read(str(out))
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio[:, 0]
    # La junta está donde termina el segmento A (1.0 s).
    joint = int(SR * 1.0)
    salto = abs(float(audio[joint]) - float(audio[joint - 1]))
    assert salto < 0.01, f"salto en la junta: {salto}"
    # Sin DC residual en el capítulo.
    assert abs(float(np.mean(audio))) < 0.001


def test_edge_fade_y_remove_dc():
    ap = AudioProcessor(sr=SR)
    x = np.full(4800, 0.05, dtype=np.float32) + 0.3 * np.sin(
        2 * np.pi * 220 * np.arange(4800) / SR).astype(np.float32)
    y = ap.remove_dc(x)
    assert abs(float(np.mean(y))) < 1e-6
    z = ap.edge_fade(y, SR)
    assert abs(float(z[0])) < 1e-6 and abs(float(z[-1])) < 1e-6
    assert abs(float(z[len(z) // 2]) - float(y[len(y) // 2])) < 1e-6


# ------------------------------------------------------------------ #
# (3) Volumen: sin recorte duro, banda estrecha entre voces
# ------------------------------------------------------------------ #
def test_light_sin_recorte_duro_en_voces_reales():
    # Señales de cresta alta (como voz Kokoro: pico escalado 1.19-1.44).
    proc = AudioProcessor(sr=SR)
    for target in (0.05, 0.15, 0.35):
        out = proc.process_segment_light(_synth(target, seed=7, crest="spiky"))
        assert int(np.sum(np.abs(out) >= 0.8999)) == 0, f"RMS {target}: hay recorte"
        assert 0.10 < _rms(out) < 0.12, f"RMS {target} -> {_rms(out)}"


def test_light_iguala_voces_distintas():
    proc = AudioProcessor(sr=SR)
    # Tres "voces" con RMS crudo 0.068/0.074/0.078 (medido real Kokoro).
    outs = [proc.process_segment_light(_synth(r, seed=i, crest="spiky"))
            for i, r in enumerate((0.068, 0.074, 0.078))]
    rmss = [_rms(o) for o in outs]
    assert max(rmss) / min(rmss) < 1.10, f"RMS por voz: {rmss}"


def test_directed_gain_sin_recorte_y_con_dinamica():
    class Seg:
        def __init__(self, delivery, intensity):
            self.directed = True
            self.delivery = delivery
            self.intensity = intensity

    loud = _synth(0.12, seed=3, crest="spiky")
    # Intensidades extremas (medición v0.7.1): susurro x0.33, grito x1.25.
    whisper = _apply_directed_gain(_synth(0.12, seed=3, crest="spiky"),
                                   Seg("susurro", 0.0))
    shout = _apply_directed_gain(loud, Seg("grito", 1.0))
    assert float(np.max(np.abs(shout))) <= 0.981
    assert int(np.sum(np.abs(shout) >= 0.98)) == 0
    ratio = _rms(shout) / _rms(whisper)
    assert 3.5 < ratio < 4.1, f"susurro/grito: {ratio:.2f}x"
    # El techo sí actúa cuando el pico lo exige (sin él habría recorte).
    very_loud = _synth(0.30, seed=5, crest="spiky")
    capped = _apply_directed_gain(very_loud, Seg("grito", 1.0))
    assert float(np.max(np.abs(capped))) <= 0.981


def test_prepare_logea_sustituciones():
    seen = []
    prepare_text_for_tts("Ixbalanqué y 3 guerreros.", log=seen.append)
    assert len(seen) == 2
    assert all("Pronunciación" in m for m in seen)
