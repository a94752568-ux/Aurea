"""Suite v0.7.0: reparto inteligente por género + ambientes con ducking.

Ejecutar: QT_QPA_PLATFORM=offscreen pytest tests/test_v070.py -v

Stubs: como en test_aurea.py (kokoro_onnx/misaki no existen en la VM).
El TTS real, las voces reales y la GUI visual solo se verifican en la
laptop de Alberto (ver informe final).
"""
import os
import sys
import tempfile
import time
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

from aurea.casting.cast_manager import CastManager, pick_voice  # noqa: E402
from aurea.casting.gender import (  # noqa: E402
    DESCONOCIDO,
    FEMENINO,
    MASCULINO,
    infer_gender,
    voice_gender,
)
from aurea.core.ambience import (  # noqa: E402
    BedCache,
    mix_ambience,
    mix_chapter,
    tile_bed,
    voice_activity,
)
from aurea.core.database import Database  # noqa: E402
from aurea.core.generator import ChapterGenerator  # noqa: E402
from aurea.core.models import Segment  # noqa: E402

SR = 24000


@pytest.fixture()
def tmp_home(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="aurea-test-v070-")
    monkeypatch.setenv("AUREA_HOME", tmp)
    monkeypatch.setenv("AUREA_DB_PATH", os.path.join(tmp, "aurea.db"))
    monkeypatch.setenv("AUREA_VOICES_DIR", os.path.join(tmp, "voices"))
    return Path(tmp)


def _db_with_book(db):
    bid = db.add_book("Libro v0.7.0", "/tmp/v070.txt", "txt")
    db.add_chapter(bid, 0, "Capítulo 1", "Texto.")
    return bid


# ======================================================================
# A — género: léxico, heurísticas, voz
# ======================================================================
def test_gender_lexicon_popolvuh_quintosol():
    for name in ("Hunahpú", "Ixbalanqué", "Tochtli", "Tlahuicole",
                 "Moctezuma", "Yaotl", "Tlilpotonqui", "Zipacna",
                 "Cabracán", "Xpiyacoc", "Kukulkán"):
        assert infer_gender(name) == (MASCULINO, "lexico"), name
    for name in ("Ixquic", "Citlali", "Xochitl", "Iztacxochitl",
                 "Xmucane", "Xochiquetzal"):
        assert infer_gender(name) == (FEMENINO, "lexico"), name


def test_gender_heuristics_titles_articles_endings():
    # Jimena/Rodrigo no están en el léxico: el título decide.
    ctx = "Don Rodrigo entró. Doña Jimena salió."
    assert infer_gender("Rodrigo", ctx) == (MASCULINO, "titulo")
    assert infer_gender("Jimena", ctx) == (FEMENINO, "titulo")
    assert infer_gender("Rodrigo", "el Rodrigo dijo") == (MASCULINO, "articulo")
    assert infer_gender("Jimena", "la Jimena dijo") == (FEMENINO, "articulo")
    # Bruno/Nadia no están en el léxico: decide la terminación.
    assert infer_gender("Bruno", "") == (MASCULINO, "terminacion")
    assert infer_gender("Nadia", "") == (FEMENINO, "terminacion")


def test_gender_unknown_never_invented():
    # Nombres sin pista en ningún lado -> desconocido, nunca inventado.
    assert infer_gender("Zotz") == (DESCONOCIDO, "desconocido")
    assert infer_gender("Balam") == (DESCONOCIDO, "desconocido")
    assert infer_gender("") == (DESCONOCIDO, "desconocido")
    # Guadalupe es unisex en México: sin contexto, desconocido.
    assert infer_gender("Guadalupe") == (DESCONOCIDO, "desconocido")
    assert infer_gender("Guadalupe", "don Guadalupe llegó") == (
        MASCULINO, "titulo")
    assert infer_gender("Guadalupe", "doña Guadalupe llegó") == (
        FEMENINO, "titulo")


def test_voice_gender_prefix():
    assert voice_gender("em_alex") == MASCULINO
    assert voice_gender("em_santa") == MASCULINO
    assert voice_gender("ef_dora") == FEMENINO
    assert voice_gender("am_adam") == MASCULINO
    assert voice_gender("af_heart") == FEMENINO
    assert voice_gender("yo_limpia") == DESCONOCIDO  # perfil XTTS
    assert voice_gender("") == DESCONOCIDO


def test_pick_voice_never_crosses_gender():
    assert pick_voice(MASCULINO, {})[0].startswith("em_")
    assert pick_voice(FEMENINO, {})[0].startswith("ef_")
    # Reutilizar la misma voz rota la velocidad (1 voz/español por género).
    v1 = pick_voice(MASCULINO, {})
    v2 = pick_voice(MASCULINO, {v1[0]: 1})
    v3 = pick_voice(MASCULINO, {v1[0]: 2})
    assert v1[0] == v2[0] == v3[0] == "em_santa"
    assert len({v1[1], v2[1], v3[1]}) == 3


# ======================================================================
# A — build_cast con género
# ======================================================================
def test_build_cast_assigns_voices_by_gender(tmp_home):
    db = Database()
    bid = _db_with_book(db)
    created = CastManager(db).build_cast(
        bid, ["Hunahpú", "Ixquic", "Zotz"],
        texts=["—dijo Hunahpú. —dijo Ixquic. —dijo Zotz."])
    assert created == 3
    chars = {c.name: c for c in db.characters(bid)}
    assert chars["Hunahpú"].gender == MASCULINO
    assert chars["Hunahpú"].voice.startswith("em_")
    assert chars["Ixquic"].gender == FEMENINO
    assert chars["Ixquic"].voice.startswith("ef_")
    # Desconocido: voz por defecto (nunca em_alex del narrador).
    assert chars["Zotz"].gender == DESCONOCIDO
    assert chars["Zotz"].voice in ("ef_dora", "em_santa")
    # El narrador sigue siendo em_alex masculino.
    narr = next(c for c in db.characters(bid) if c.is_narrator)
    assert narr.voice == "em_alex" and narr.gender == MASCULINO


def test_existing_character_gains_gender_keeps_voice(tmp_home):
    db = Database()
    bid = _db_with_book(db)
    # Personaje viejo (pre-v0.7.0): voz por defecto, sin género.
    db.add_character(bid, "Hunahpú", "ef_dora", 1.0)
    CastManager(db).build_cast(bid, ["Hunahpú"], texts=[])
    ch = next(c for c in db.characters(bid) if c.name == "Hunahpú")
    assert ch.gender == MASCULINO  # género inferido y guardado
    assert ch.voice == "ef_dora"  # la voz NO se toca
    assert ch.gender_manual == 0


def test_manual_gender_correction_keeps_voice(tmp_home):
    db = Database()
    bid = _db_with_book(db)
    CastManager(db).build_cast(bid, ["Ixquic"], texts=[])
    ch = next(c for c in db.characters(bid) if c.name == "Ixquic")
    assert ch.voice == "ef_dora" and ch.gender == FEMENINO
    # Alberto corrige el género a mano: la voz se conserva.
    CastManager(db).set_gender(ch.id, MASCULINO)
    ch2 = next(c for c in db.characters(bid) if c.name == "Ixquic")
    assert ch2.gender == MASCULINO and ch2.gender_manual == 1
    assert ch2.voice == "ef_dora"
    # Un rebuild posterior no re-infiere ni reasigna nada.
    CastManager(db).build_cast(bid, ["Ixquic"], texts=[])
    ch3 = next(c for c in db.characters(bid) if c.name == "Ixquic")
    assert ch3.gender == MASCULINO and ch3.voice == "ef_dora"


def test_build_cast_duplicate_names_no_crash(tmp_home):
    db = Database()
    bid = _db_with_book(db)
    created = CastManager(db).build_cast(
        bid, ["Hunahpú", "Hunahpú", "Ixquic"], texts=[])
    assert created == 2
    names = [c.name for c in db.characters(bid) if not c.is_narrator]
    assert sorted(names) == ["Hunahpú", "Ixquic"]


def test_cast_info_flags_mismatch(tmp_home):
    db = Database()
    bid = _db_with_book(db)
    CastManager(db).build_cast(bid, ["Hunahpú", "Ixquic"], texts=[])
    info = {c["name"]: c for c in CastManager(db).cast_info(bid)}
    assert info["Hunahpú"]["voice_gender"] == MASCULINO
    assert info["Ixquic"]["voice_gender"] == FEMENINO
    assert not info["Hunahpú"]["mismatch"] and not info["Ixquic"]["mismatch"]
    # Forzar un desajuste a mano: la UI debe marcarlo.
    ch = next(c for c in db.characters(bid) if c.name == "Hunahpú")
    db.update_character(ch.id, "ef_dora", 1.0)
    info = {c["name"]: c for c in CastManager(db).cast_info(bid)}
    assert info["Hunahpú"]["mismatch"]


# ======================================================================
# B — caché de camas
# ======================================================================
def test_bed_cache_generates_once(tmp_home):
    cache = BedCache()
    bed1, sr1 = cache.get_bed("selva_noche", SR)
    path = cache.path_for("selva_noche", SR)
    assert path.exists()
    assert sr1 == SR and len(bed1) == 60 * SR and bed1.shape[1] == 2
    mtime1 = path.stat().st_mtime
    time.sleep(0.05)
    bed2, _ = cache.get_bed("selva_noche", SR)
    assert path.stat().st_mtime == mtime1  # segunda vez: de caché, sin regenerar
    assert np.array_equal(bed1, bed2)
    with pytest.raises(ValueError):
        cache.get_bed("inexistente", SR)


def test_tile_bed_length_and_joint_continuity():
    sr = SR
    # Rampa 0->1: un empalme ingenuo saltaría de 1 a 0 en la junta.
    bed = np.tile(np.linspace(0, 1, sr)[:, None], (1, 2)).astype(np.float32)
    out = tile_bed(bed, int(3.5 * sr), sr, crossfade_s=0.1)
    assert len(out) == int(3.5 * sr)
    # En las juntas (1.0 s y 2.0 s) no hay salto: diff acotado.
    for joint in (1.0, 2.0):
        j = int(joint * sr)
        window = out[j - 200:j + 200]
        assert np.max(np.abs(np.diff(window, axis=0))) < 0.01


# ======================================================================
# B — ducking y fades
# ======================================================================
def _synthetic_narration():
    """6 s: voz 0-2 s, silencio 2-4 s, voz 4-6 s (seno 220 Hz, amp 0.5)."""
    t = np.arange(6 * SR) / SR
    sig = np.zeros(6 * SR, dtype=np.float32)
    voice = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    sig[0:2 * SR] = voice[0:2 * SR]
    sig[4 * SR:6 * SR] = voice[4 * SR:6 * SR]
    return np.stack([sig, sig], axis=1)


def test_ducking_rms_voice_vs_pause():
    rng = np.random.default_rng(7)
    bed = rng.standard_normal((6 * SR, 2)).astype(np.float32) * 0.3
    narration = _synthetic_narration()
    mixed = mix_ambience(narration, bed, SR, bed_level=0.5,
                         duck_level=0.2, fade_s=0.5)
    assert mixed.shape == narration.shape
    # Residuo = cama con su ganancia (restamos la narración conocida).
    residual = mixed - narration
    # Ventanas lejos de transiciones y fades.
    rms_voice = np.sqrt(np.mean(residual[int(0.5 * SR):int(1.5 * SR)] ** 2))
    rms_pause = np.sqrt(np.mean(residual[int(2.5 * SR):int(3.5 * SR)] ** 2))
    assert rms_pause > 0.05  # hay cama audible en la pausa
    assert rms_voice < 0.6 * rms_pause  # bajo la voz la cama baja


def test_voice_activity_shape():
    narration = _synthetic_narration()
    act = voice_activity(narration, SR)
    assert len(act) == len(narration)
    assert act[int(1.0 * SR)] > 0.8  # plena voz
    assert act[int(3.0 * SR)] < 0.2  # pleno silencio
    assert np.all((act >= 0) & (act <= 1))


def test_fades_start_end_near_zero():
    rng = np.random.default_rng(7)
    bed = rng.standard_normal((4 * SR, 2)).astype(np.float32) * 0.3
    t = np.arange(4 * SR) / SR
    narration = np.stack(
        [0.4 * np.sin(2 * np.pi * 220 * t)] * 2, axis=1).astype(np.float32)
    mixed = mix_ambience(narration, bed, SR, bed_level=0.5,
                         duck_level=0.3, fade_s=1.0)
    residual = mixed - narration
    # La cama arranca y termina en ~0 (fundidos), sin salto brusco.
    assert np.max(np.abs(residual[:100])) < 0.05
    assert np.max(np.abs(residual[-100:])) < 0.05
    # La rampa del fundido se ve en la energía: el primer/último 0.1 s
    # tienen mucha menos cama que el centro.
    rms_mid = np.sqrt(np.mean(residual[int(1.9 * SR):int(2.1 * SR)] ** 2))
    rms_head = np.sqrt(np.mean(residual[:int(0.1 * SR)] ** 2))
    rms_tail = np.sqrt(np.mean(residual[-int(0.1 * SR):] ** 2))
    assert rms_mid > 0.01
    assert rms_head < 0.3 * rms_mid
    assert rms_tail < 0.3 * rms_mid


def test_mix_chapter_none_passthrough(tmp_home):
    narration = _synthetic_narration()
    out = mix_chapter(narration, "none", SR)
    assert np.array_equal(out, narration)


# ======================================================================
# B — integración en _concat y persistencia del ambiente
# ======================================================================
def test_ambience_setting_persisted(tmp_home):
    db = Database()
    bid = _db_with_book(db)
    ch = db.chapters(bid)[0]
    assert ch.ambience == "none" and ch.bed_level == 0.25
    db.set_chapter_ambience(ch.id, "selva_noche", 0.4)
    ch2 = db.chapter(ch.id)
    assert ch2.ambience == "selva_noche" and ch2.bed_level == 0.4


def test_concat_with_ambience_produces_wav(tmp_home):
    out_dir = Path(tmp_home) / "output"
    out_dir.mkdir()
    segs = []
    for i, amp in enumerate((0.5, 0.4)):
        p = out_dir / f"seg{i}.wav"
        t = np.arange(int(0.5 * SR)) / SR
        sf.write(str(p), (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32),
                 SR, subtype="PCM_16")
        segs.append(Segment(i + 1, 1, i, "texto", str(p), "done",
                            kind="narration", directed=1, pause_after_ms=100))
    wav_none = out_dir / "cap_none.wav"
    wav_amb = out_dir / "cap_amb.wav"
    r_none = ChapterGenerator._concat(segs, {}, ("kokoro", "em_alex", 1.0),
                                      wav_none, ambience="none")
    r_amb = ChapterGenerator._concat(segs, {}, ("kokoro", "em_alex", 1.0),
                                     wav_amb, ambience="selva_noche",
                                     bed_level=0.25)
    assert r_none is not None and Path(r_none).exists()
    assert r_amb is not None and Path(r_amb).exists()
    d_none, sr1 = sf.read(str(r_none))
    d_amb, sr2 = sf.read(str(r_amb))
    assert sr1 == sr2 == SR
    # Misma duración (~1.1 s: 0.5 + 0.1 pausa + 0.5); el ambiente no alarga.
    assert abs(len(d_none) - len(d_amb)) <= 2
    assert abs(len(d_amb) / SR - 1.1) < 0.05
    # Con ambiente hay contenido en la pausa (0.5-0.6 s) que sin él es silencio.
    pause_none = np.sqrt(np.mean(d_none[int(0.55 * SR):int(0.6 * SR)] ** 2))
    pause_amb = np.sqrt(np.mean(d_amb[int(0.55 * SR):int(0.6 * SR)] ** 2))
    assert pause_none < 0.01
    # La cama de selva de noche es tenue (grillos dispersos): el umbral
    # real medido es ~0.004; lo que importa es que haya cama donde
    # antes había silencio.
    assert pause_amb > 0.002
    assert pause_amb > 3 * max(pause_none, 1e-6)
