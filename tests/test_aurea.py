"""Suite de regresión AUREA (Fase 5).

Cubre los arreglos críticos y de semántica:
  C1  primera generación produce cap_XXX.wav + latest.wav
  C2  asignar XTTS no levanta TypeError
  C3  asignar Kokoro guarda la voz en BD
  C4  borrar un libro no deja huérfanos
  C5  Stop termina el hilo sin congelar; Pausa congela y reanuda
  A1-A3  pace float + intensidad normalizada (877 ms; lento no acelera)
  A4  MD con H2 antes de H1 no revienta y conserva el contenido
  M11  re-importar no duplica (book_by_path)
  M25  transaction() con rollback no deja capítulos a medias
  + regresión: clone_sample_map() con BD sin tabla characters -> {}

Stubs (la VM no tiene Kokoro/XTTS reales ni modelos):
  - kokoro_onnx / misaki se sustituyen por módulos vacíos antes de
    importar aurea (los adaptadores solo se importan, no se cargan).
  - La síntesis usa FakeKokoro (seno 220 Hz) inyectado en
    aurea.core.generator.KokoroAdapter; el pipeline probado es el real.
  - XTTS real, Ollama real y la GUI visual solo se pueden verificar
    en la laptop de Alberto (ver informe final).

Ejecutar: QT_QPA_PLATFORM=offscreen pytest tests/test_aurea.py -v
"""
import os
import sys
import tempfile
import types
from pathlib import Path

# --- Stubs de dependencias pesadas (antes de importar aurea) ---
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

import aurea.core.generator as gen_mod  # noqa: E402
from aurea.core.database import Database  # noqa: E402
from aurea.core.generator import ChapterGenerator, segmentize  # noqa: E402
from aurea.core.models import Segment  # noqa: E402
from aurea.direction.director import DirectionWorker  # noqa: E402
from aurea.direction.rules import apply_emotion_pace, direct_segment  # noqa: E402
from aurea.ingestion.md_reader import MarkdownReader  # noqa: E402
from aurea.voice import clone_store, sample_prep  # noqa: E402

SR = 24000


class FakeKokoro:
    """TTS falso: seno 220 Hz, amplitud 0.5, duración según el texto."""

    def __init__(self, delay=0.0):
        self.delay = delay

    def synthesize(self, text, voice="em_alex", speed=1.0, log=None):
        # v0.7.2: el adaptador real acepta log (sustituciones de
        # pronunciación); el falso refleja el contrato.
        import time as _time
        if self.delay:
            _time.sleep(self.delay)
        dur = max(0.3, min(len(text) * 0.05, 3.0))
        t = np.linspace(0, dur, int(SR * dur), endpoint=False)
        return (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), SR

    def save_wav(self, path, samples, sr):
        sf.write(str(path), samples, sr, subtype="PCM_16")


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture(scope="session")
def qapp():
    # QApplication si hay libs GUI (la necesitan los widgets); si no,
    # QCoreApplication basta para hilos/señales. Offscreen en VM.
    try:
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication(sys.argv)
    except ImportError:
        return QCoreApplication.instance() or QCoreApplication(sys.argv)


@pytest.fixture()
def tmp_home(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="aurea-test-")
    monkeypatch.setenv("AUREA_HOME", tmp)
    monkeypatch.setenv("AUREA_DB_PATH", os.path.join(tmp, "aurea.db"))
    monkeypatch.setenv("AUREA_VOICES_DIR", os.path.join(tmp, "voices"))
    return Path(tmp)


@pytest.fixture()
def fake_tts(monkeypatch):
    monkeypatch.setattr(gen_mod, "KokoroAdapter", FakeKokoro)
    return FakeKokoro


def make_book(db, text="Hola mundo.\n\nAdiós mundo.", title="Libro de prueba"):
    bid = db.add_book(title, "/tmp/prueba.txt", "txt")
    cid = db.add_chapter(bid, 0, "Capítulo de prueba", text)
    parts = segmentize(text, False)
    for i, (kind, _sp, chunk, pause) in enumerate(parts):
        db.add_segment(cid, i, chunk, kind=kind, pause_after_ms=pause)
    return bid, cid, db.chapter(cid)


def direct_chapter(cid):
    """Dirigir síncronamente (sin hilo) para los tests que no prueban hilos."""
    w = DirectionWorker(cid, use_llm=False)
    w.run()
    return w


# ----------------------------------------------------------------------
# C1 — primera generación produce el WAV final
# ----------------------------------------------------------------------
def test_c1_first_generation_produces_final_wav(tmp_home, fake_tts, qapp):
    db = Database()
    bid, cid, chapter = make_book(db)
    direct_chapter(cid)
    gen = ChapterGenerator(chapter, "em_alex", 1.0, force=False)
    gen.run()  # síncrono: el bug C1 era justo aquí (éxito sin WAV)
    final = Path(tmp_home) / "output" / f"book_{bid}" / "cap_000.wav"
    latest = Path(tmp_home) / "output" / "latest.wav"
    assert final.exists(), "C1: no se produjo cap_000.wav en la primera generación"
    assert latest.exists(), "C1: no se produjo latest.wav"
    assert sf.info(str(final)).subtype == "PCM_16", "A7: el WAV final debe ser PCM_16"
    segs = db.segments(cid)
    assert all(s.wav_path and Path(s.wav_path).exists() for s in segs)
    assert gen._last_wav == str(final)
    db.close()


# ----------------------------------------------------------------------
# C2/C3 — asignación de voces
# ----------------------------------------------------------------------
def test_c2_assign_xtts_no_type_error(tmp_home):
    db = Database()
    bid = db.add_book("T", "/tmp/t.txt", "txt")
    cid_char = db.add_character(bid, "Elena", "ef_dora", 1.0)
    pid = clone_store.add_profile("ElenaClon", "/tmp/muestra.wav")
    # C2: antes levantaba TypeError (4 args a una función de 3).
    clone_store.assign_character(cid_char, "xtts", pid, voice="ElenaClon")
    ch = db.characters(bid)[0]
    assert ch.engine == "xtts"
    assert ch.voice_profile_id == pid
    assert ch.voice == "ElenaClon"
    db.close()


def test_c3_assign_kokoro_saves_voice(tmp_home):
    db = Database()
    bid = db.add_book("T", "/tmp/t.txt", "txt")
    cid_char = db.add_character(bid, "Elena", "ef_dora", 1.0)
    # C3: antes decía éxito pero no guardaba la voz.
    clone_store.assign_character(cid_char, "kokoro", None, voice="em_santa")
    ch = db.characters(bid)[0]
    assert ch.engine == "kokoro"
    assert ch.voice == "em_santa"
    db.close()


# ----------------------------------------------------------------------
# C4 — borrar libro no deja huérfanos
# ----------------------------------------------------------------------
def test_c4_delete_book_no_orphans(tmp_home):
    db = Database()
    bid = db.add_book("T", "/tmp/t.txt", "txt")
    cid = db.add_chapter(bid, 0, "C1", "texto")
    db.add_segment(cid, 0, "hola.", kind="narration")
    db.add_character(bid, "Narrador", "em_alex", 1.0, is_narrator=1)
    db.delete_book(bid)
    assert db.books() == []
    assert db.conn.execute("SELECT COUNT(*) FROM chapters").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0
    db.close()


# ----------------------------------------------------------------------
# C5 — Stop real y Pausa real (con hilos de verdad)
# ----------------------------------------------------------------------
def test_c5_stop_ends_thread_without_freeze(tmp_home, qapp):
    db = Database()
    text = "\n\n".join(f"Frase número {i} del capítulo." for i in range(8))
    bid, cid, chapter = make_book(db, text=text)
    direct_chapter(cid)

    class SlowFake(FakeKokoro):
        def __init__(self):
            super().__init__(delay=0.3)

    gen_mod.KokoroAdapter = SlowFake
    gen = ChapterGenerator(chapter, "em_alex", 1.0, force=False)
    events = []
    loop = QEventLoop()
    gen.cancelled.connect(lambda: (events.append("cancelled"), loop.quit()))
    gen.finished_ok.connect(lambda m: (events.append("ok"), loop.quit()))
    gen.failed.connect(lambda e: (events.append(f"failed:{e}"), loop.quit()))
    gen.start()
    QTimer.singleShot(500, gen.request_cancel)  # parar a mitad (~2 de 8)
    QTimer.singleShot(10000, loop.quit)  # seguridad: si se congela, salir
    loop.exec()
    assert gen.wait(5000), "C5: el hilo no terminó tras Stop (¿congelado?)"
    assert "cancelled" in events, f"C5: no se emitió cancelled: {events}"
    done = [s for s in db.segments(cid) if s.status == "done"]
    assert len(done) < 8, "C5: Stop no paró la generación a mitad"
    db.close()


def test_c5_pause_freezes_and_resumes(tmp_home, qapp):
    db = Database()
    text = "\n\n".join(f"Frase número {i} del capítulo." for i in range(6))
    bid, cid, chapter = make_book(db, text=text)
    direct_chapter(cid)

    class SlowFake(FakeKokoro):
        def __init__(self):
            super().__init__(delay=0.3)

    gen_mod.KokoroAdapter = SlowFake
    gen = ChapterGenerator(chapter, "em_alex", 1.0, force=False)
    gen.request_pause()  # pausado desde antes de arrancar
    assert gen.paused
    events = []
    loop = QEventLoop()
    gen.finished_ok.connect(lambda m: (events.append("ok"), loop.quit()))
    gen.failed.connect(lambda e: (events.append(f"failed:{e}"), loop.quit()))
    gen.start()
    QTimer.singleShot(900, loop.quit)  # ventana de pausa: no debe avanzar
    loop.exec()
    done_paused = [s for s in db.segments(cid) if s.status == "done"]
    assert len(done_paused) == 0, f"C5: en pausa se generaron {len(done_paused)} segmentos"
    gen.request_resume()
    assert not gen.paused
    loop2 = QEventLoop()
    gen.finished_ok.connect(lambda m: loop2.quit())
    gen.failed.connect(lambda e: loop2.quit())
    QTimer.singleShot(10000, loop2.quit)
    loop2.exec()
    assert gen.wait(5000), "C5: el hilo no terminó tras reanudar"
    assert "ok" in events, f"C5: no terminó bien tras reanudar: {events}"
    done = [s for s in db.segments(cid) if s.status == "done"]
    assert len(done) == 6
    db.close()


# ----------------------------------------------------------------------
# A1-A3 — semántica pace (velocidad) / intensity (0-1)
# ----------------------------------------------------------------------
def test_a1_pace_float_and_name():
    # 450 * (1/0.75) * 1.3 = 780 (pace float de producción)
    assert apply_emotion_pace(450, 0.75, "miedo") == 780
    # 450 * 1.5 * 1.3 = 877 (nombre "lento")
    assert apply_emotion_pace(450, "lento", "miedo") == 877


def test_a2_lento_does_not_accelerate():
    # El bug A1: "lento" sintetizaba 1.5x MÁS RÁPIDO. Con pace float, un
    # segmento lento alarga las pausas (780 > 450), nunca las acorta.
    assert apply_emotion_pace(450, 0.75, "neutral") == 600
    assert apply_emotion_pace(450, 0.75, "neutral") > 450


def test_a3_intensity_normalized_threshold():
    seg_hi = Segment(0, 0, 0, "Hola mundo.", 0, "pending", "narration", intensity=1.0)
    assert direct_segment(seg_hi)["pause_after_ms"] == 750  # 450 + 300
    seg_mid = Segment(0, 0, 0, "Hola mundo.", 0, "pending", "narration", intensity=0.5)
    assert direct_segment(seg_mid)["pause_after_ms"] == 450  # sin bonus
    seg_edge = Segment(0, 0, 0, "Hola mundo.", 0, "pending", "narration", intensity=0.8)
    assert direct_segment(seg_edge)["pause_after_ms"] == 750  # umbral >= 0.8


# ----------------------------------------------------------------------
# A4 — MD con H2/H3/--- antes del primer H1
# ----------------------------------------------------------------------
def test_a4_md_heading_before_h1(tmp_path):
    md = ("## Sección previa\n\nTexto antes del título.\n\n---\n\n"
          "# Título real\n\nContenido.\n")
    p = tmp_path / "prueba.md"
    p.write_text(md, encoding="utf-8")
    chapters = MarkdownReader().read(str(p))
    assert len(chapters) == 2
    assert "Texto antes del título" in chapters[0][1]
    assert chapters[1][0] == "Título real"


def test_a4_md_tables_and_quotes_stripped(tmp_path):
    md = "# T\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> Una cita.\n\nTexto normal.\n"
    p = tmp_path / "prueba.md"
    p.write_text(md, encoding="utf-8")
    text = MarkdownReader().read(str(p))[0][1]
    assert "|" not in text
    assert "> Una" not in text
    assert "Una cita" in text


# ----------------------------------------------------------------------
# M11 — re-importar no duplica
# ----------------------------------------------------------------------
def test_m11_reimport_does_not_duplicate(tmp_home):
    db = Database()
    bid = db.add_book("Mi libro", "/tmp/mi_libro.txt", "txt")
    # book_by_path es la primitiva de dedup que usa la ventana al importar.
    found = db.book_by_path("/tmp/mi_libro.txt")
    assert found is not None and found.id == bid
    assert db.book_by_path("/tmp/otro.txt") is None
    db.close()


# ----------------------------------------------------------------------
# M25 — transaction() con rollback
# ----------------------------------------------------------------------
def test_m25_transaction_rollback(tmp_home):
    db = Database()
    bid = db.add_book("T", "/tmp/t.txt", "txt")
    try:
        with db.transaction():
            db.add_chapter(bid, 0, "C1", "texto")
            raise RuntimeError("fallo simulado")
    except RuntimeError:
        pass
    assert db.chapters(bid) == [], "M25: el rollback no deshizo el capítulo"
    db.close()


# ----------------------------------------------------------------------
# Regresiones varias
# ----------------------------------------------------------------------
def test_clone_sample_map_without_characters_table(tmp_home):
    # BD que solo tiene voice_profiles (sin characters): antes reventaba
    # con "no such table: characters" y tumbaba TODA la generación.
    clone_store.ensure_schema()
    assert clone_store.clone_sample_map() == {}


def test_voices_dir_env_override(tmp_home):
    assert sample_prep._voices_dir().rstrip("/") == os.environ["AUREA_VOICES_DIR"]


def test_wav_peaks_reads_pcm16(tmp_home, fake_tts, qapp):
    # A7: los WAV PCM_16 se leen con _wav_peaks (antes eran float32 y
    # la forma de onda nunca se dibujaba). Necesita QApplication.
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("sin libs GUI en la VM (libEGL)")
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        pytest.skip("sin QApplication (solo QCoreApplication disponible)")
    from aurea.ui.theme import WaveformView
    wav = Path(tmp_home) / "prueba.wav"
    fake = FakeKokoro()
    samples, sr = fake.synthesize("Hola mundo.", voice="em_alex")
    fake.save_wav(wav, samples, sr)
    view = WaveformView()
    view.set_wav(str(wav))
    assert view._peaks is not None and len(view._peaks) > 0
