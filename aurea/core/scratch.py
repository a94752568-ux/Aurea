"""Síntesis de texto libre y pruebas de voz (Qt-free).

run_scratch() es el laboratorio de voces: sintetiza cualquier texto con
una voz base Kokoro, un perfil clonado XTTS o un personaje del reparto.
Para personajes usa EXACTAMENTE el mismo camino que run_generation()
(sanitize_cast + voice_map), de modo que lo que oyes aquí es lo que
sonará en la lectura final.
"""
import numpy as np
import soundfile as sf
from pathlib import Path

from .database import Database
from .segmentation import clean_segment_text, segmentize
from ..casting.cast_manager import CastManager
from ..tts.kokoro_adapter import KokoroAdapter, VOICES_ES
from ..voice import clone_store
from ..voice.xtts_adapter import XTTSAdapter

PAUSE_SCRATCH_S = 0.30


def _resolve_source(source, db):
    """Resuelve la fuente de voz. Devuelve (engine, voice, speed, sample_path).

    Fuentes: 'kokoro:<voice>', 'xtts:<profile_id>', 'char:<character_id>'.
    """
    if source.startswith("char:"):
        cid = int(source[5:])
        found = None
        for b in db.books():
            for c in db.characters(b.id):
                if c.id == cid:
                    found = (b, c)
                    break
            if found:
                break
        if not found:
            raise ValueError("Personaje no encontrado")
        book, ch = found
        cast = CastManager(db)
        cast.ensure_narrator(book.id)
        cast.sanitize_cast(book.id, set(VOICES_ES))
        vmap, fallback = cast.voice_map(book.id)
        engine, voice, speed = vmap.get(cid, fallback)
        sample = None
        if engine == "xtts":
            sample = clone_store.clone_sample_map().get(cid)
            if not sample or not Path(sample).exists():
                raise ValueError(
                    f"{ch.name}: el clon no tiene muestra utilizable en disco; "
                    "reasigna el perfil en Reparto")
        if engine == "kokoro" and voice not in VOICES_ES:
            engine, voice = "kokoro", fallback[1]
        return engine, voice, speed, sample

    if source.startswith("xtts:"):
        pid = int(source[5:])
        prof = clone_store.get_profile(pid)
        if not prof:
            raise ValueError(f"Perfil {pid} no encontrado")
        sample = prof.get("sample_path")
        if not sample or not Path(sample).exists():
            raise ValueError(
                f"Perfil '{prof.get('name')}': la muestra no existe en disco "
                "(¿muestra temporal borrada?)")
        return "xtts", prof.get("name"), 1.0, sample

    if source.startswith("kokoro:"):
        voice = source[7:]
        if voice not in VOICES_ES:
            raise ValueError(f"Voz Kokoro inválida: {voice}")
        return "kokoro", voice, 1.0, None

    raise ValueError(f"Fuente de voz inválida: {source}")


def run_scratch(text, source, on_progress=None, db=None):
    """Sintetiza texto libre con la fuente de voz indicada.

    Devuelve la ruta del WAV generado (~/aurea/output/scratch/scratch.wav).
    Textos largos se dividen con segmentize y se unen con una pausa corta.
    """
    on_progress = on_progress or (lambda pct, msg: None)
    db = db or Database()
    engine, voice, speed, sample = _resolve_source(source, db)

    text = (text or "").strip()
    if not text:
        raise ValueError("Escribe algo de texto")
    chunks = [c for c in
              (clean_segment_text(t) for _k, _s, t, _p in segmentize(text, False))
              if c]
    if not chunks:
        raise ValueError("El texto quedó vacío tras limpiar etiquetas")

    out_dir = Path.home() / "aurea" / "output" / "scratch"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "scratch.wav"

    parts = []
    if engine == "xtts":
        xtts = XTTSAdapter()
        xtts.load()
        try:
            for i, chunk in enumerate(chunks):
                on_progress(int(70 * i / len(chunks)),
                            f"Sintetizando {i + 1}/{len(chunks)} con el clon…")
                s, sr = xtts.generate(chunk, speaker_wav=sample, speed=speed)
                parts.append((s, sr))
        finally:
            xtts.release()
    else:
        adapter = KokoroAdapter()
        for i, chunk in enumerate(chunks):
            on_progress(int(70 * i / len(chunks)),
                        f"Sintetizando {i + 1}/{len(chunks)} ({voice})…")
            s, sr = adapter.synthesize(chunk, voice=voice, speed=speed)
            parts.append((s, sr))

    sr0 = parts[0][1]
    pieces = []
    for i, (s, sr) in enumerate(parts):
        pieces.append(np.clip(s, -1.0, 1.0))
        if i < len(parts) - 1:
            pieces.append(np.zeros(int(sr * PAUSE_SCRATCH_S),
                                   dtype=getattr(s, "dtype", np.float32)))
    audio = np.concatenate(pieces) if len(pieces) > 1 else pieces[0]
    sf.write(str(out), audio, sr0)
    on_progress(100, "Listo")
    return out
