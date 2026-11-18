"""Motor de generación Qt-free (refactor Electron).

run_generation() ejecuta la generación completa de un capítulo en el hilo
del llamador: el servidor AUREA lo corre en un hilo de trabajo y el
adaptador PySide6 (core/generator.py) dentro de un QThread. Sin Qt.

v0.6: sanitize_cast() reconcilia el reparto al arrancar (perfiles clonados
con nombre tipo em_origins → XTTS automáticamente; voces kokoro
inexistentes → voz del narrador), clean_segment_text() limpia los textos
heredados de la BD (fuera "—dijo Narrador.") invalidando su WAV, y
voice_tag muestra la voz REAL usada tras cualquier fallback.
"""
import os
import shutil
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from .database import Database
from .segmentation import clean_segment_text, gain_for, segmentize
from ..casting.cast_manager import CastManager
from ..tts.kokoro_adapter import KokoroAdapter, VOICES_ES
from ..voice import clone_store
from ..voice.xtts_adapter import XTTSAdapter


def concat_segments(segs, wav_path):
    """Une los WAV de los segmentos (con pausas dirigidas) en un capítulo."""
    clips, sr = [], 24000
    ordered = [s for s in segs if s.wav_path and Path(s.wav_path).exists()]
    if not ordered:
        return None

    for s in ordered:
        if s.text and "[" in s.text:
            raise ValueError(
                f"Segmento {s.idx} contiene marcador sin eliminar: {s.text[:60]}...")

    first = ordered[0]
    if first.directed and first.pause_before_ms is not None:
        clip = np.zeros(int(first.pause_before_ms / 1000 * sr), dtype=np.float32)
        clip = np.stack([clip, clip], axis=1)
        clips.append(clip)

    for i, s in enumerate(ordered):
        data, sr = sf.read(s.wav_path)
        if data.ndim == 1:
            data = np.stack([data, data], axis=1)
        clips.append(data)
        if i < len(ordered) - 1:
            pause_ms = 0
            if s.directed and s.pause_after_ms is not None:
                pause_ms = s.pause_after_ms
            clip = np.zeros(int(pause_ms / 1000 * sr), dtype=np.float32)
            clip = np.stack([clip, clip], axis=1)
            clips.append(clip)

    audio = np.concatenate(clips) if len(clips) > 1 else clips[0]
    Path(wav_path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(wav_path), audio, sr)
    return wav_path


def _synth_kokoro(adapter, seg, v, vsp, pace, fallback, cast, on_progress):
    """Sintetiza con Kokoro con red de seguridad: si la voz del personaje
    no existe en el bin de voces, se corrige en la BD y se reintenta.
    Devuelve (samples, sr, voz_efectiva_usada)."""
    if v not in VOICES_ES:
        safe = fallback[1] if fallback[1] in VOICES_ES else VOICES_ES[0]
        if cast is not None and seg.character_id is not None:
            try:
                cast.assign(seg.character_id, safe, vsp,
                            engine="kokoro", profile_id=None)
                on_progress(0, f"Voz inválida '{v}' corregida a '{safe}' "
                               f"en la BD (personaje del seg {seg.idx})")
            except Exception:  # noqa: BLE001
                on_progress(0, f"Voz inválida '{v}'; uso '{safe}' "
                               "(no se pudo corregir en la BD)")
        v = safe
    text = clean_segment_text(seg.text)  # defensa final anti "—dijo Narrador."
    try:
        samples, sr = adapter.synthesize(text, voice=v, speed=vsp * pace)
        return samples, sr, v
    except ValueError as e:
        # Última red: el bin no trae la voz pedida pese a todo
        safe = fallback[1] if fallback[1] in VOICES_ES else VOICES_ES[0]
        on_progress(0, f"{e}; reintento con '{safe}'")
        samples, sr = adapter.synthesize(text, voice=safe, speed=vsp * pace)
        return samples, sr, safe


def _clean_stored_segments(db, segs, on_progress):
    """Limpieza de textos heredados de la BD: fuera etiquetas de habla tipo
    "—dijo Narrador.". Si el texto cambia, el WAV viejo queda invalidado
    (se borra) para que se regenere con el texto limpio."""
    cleaned = 0
    for s in segs:
        clean = clean_segment_text(s.text)
        if clean != s.text:
            if s.wav_path and Path(s.wav_path).exists():
                try:
                    os.remove(s.wav_path)
                except OSError:
                    pass
            db.conn.execute("UPDATE segments SET text=?, wav_path=NULL, "
                            "status='pending' WHERE id=?", (clean, s.id))
            s.text = clean
            cleaned += 1
    if cleaned:
        db.conn.commit()
        on_progress(0, f"Limpieza: {cleaned} segmento(s) con etiquetas de "
                       "habla eliminadas del texto; se regenerarán")
    return cleaned


def run_generation(chapter, voice, speed, force=False,
                   on_progress=None, on_failed=None, on_done=None):
    """Genera el capítulo completo: segmentos TTS + unión con pausas.

    chapter: core.models.Chapter (de Database.chapter(id)).
    voice/speed: voz y velocidad por defecto (narrador) cuando no hay reparto.
    Cero dependencia de Qt: los callbacks reciben (progreso, mensaje).
    """
    on_progress = on_progress or (lambda pct, msg: None)
    on_failed = on_failed or (lambda msg: None)
    on_done = on_done or (lambda msg: None)

    try:
        db = Database()
        book_id = chapter.book_id
        out_dir = Path.home() / "aurea" / "output" / f"book_{book_id}"
        out_dir.mkdir(parents=True, exist_ok=True)
        wav_path = out_dir / f"cap_{chapter.idx:03d}.wav"
        last_wav = None

        use_cast = len(db.characters(book_id)) > 1
        cast = CastManager(db) if use_cast else None
        if cast:
            cast.ensure_narrator(book_id)
            # v0.6: reconciliación automática de voces con la realidad
            # de los motores (clones XTTS y voces Kokoro válidas).
            fixed = cast.sanitize_cast(book_id, set(VOICES_ES))
            if fixed:
                on_progress(0, f"Reparto saneado: {fixed} personaje(s) con "
                               "la voz reconciliada automáticamente")

        if force:
            db.clear_segments(chapter.id)
        segs = db.segments(chapter.id)

        # Textos guardados en BD pueden contener etiquetas de habla viejas:
        # se limpian y su audio queda invalidado para regenerarse.
        _clean_stored_segments(db, segs, on_progress)

        if not segs:
            parts = segmentize(chapter.text, use_cast)
            for i, (kind, speaker, chunk, pause) in enumerate(parts):
                cid = None
                if use_cast:
                    name = speaker if (kind == "dialogue" and speaker) else "Narrador"
                    cid = cast.character_id(book_id, name)
                segs.append(db.add_segment(chapter.id, i, chunk,
                                           kind=kind, character_id=cid,
                                           pause_after_ms=pause))

        vmap, fallback = (cast.voice_map(book_id) if cast
                          else ({}, ("kokoro", voice, speed)))
        todo = [s for s in segs
                if not (s.wav_path and Path(s.wav_path).exists())]
        if todo:
            total = len(todo)
            adapter = KokoroAdapter()
            clone_map = clone_store.clone_sample_map()
            xtts = None
            for i, seg in enumerate(todo):
                eng, v, vsp = vmap.get(seg.character_id, fallback)
                pace = seg.pace if (seg.directed and seg.pace) else 1.0
                t0 = time.perf_counter()
                use_xtts = (eng == "xtts")
                sample = clone_map.get(seg.character_id) if use_xtts else None
                if use_xtts and sample:
                    if xtts is None:
                        xtts = XTTSAdapter()
                        xtts.load()
                    samples, sr = xtts.generate(
                        clean_segment_text(seg.text),
                        speaker_wav=sample, speed=vsp * pace)
                    voice_tag = "clon"
                else:
                    if use_xtts:
                        on_progress(0, f"Clon sin muestra usable en seg "
                                       f"{seg.idx}; uso Kokoro")
                    samples, sr, used_voice = _synth_kokoro(
                        adapter, seg, v, vsp, pace, fallback, cast,
                        on_progress)
                    voice_tag = used_voice
                samples = np.clip(samples * gain_for(seg), -1.0, 1.0)
                seg_wav = out_dir / f"cap_{chapter.idx:03d}_seg{seg.idx:04d}.wav"
                adapter.save_wav(seg_wav, samples, sr)
                db.update_segment(seg.id, str(seg_wav), "done")
                tag = f" · {seg.emotion}/{seg.delivery}" if seg.directed else ""
                on_progress(
                    int((i + 1) * 100 / total),
                    f"Segmento {i + 1}/{total} voz {voice_tag}{tag} "
                    f"({time.perf_counter() - t0:.1f}s)")
                last_wav = str(seg_wav)
            if xtts is not None:
                xtts.release()

        final_wav = concat_segments(segs, wav_path)
        if final_wav:
            last_wav = str(final_wav)
            target = Path.home() / "aurea" / "output" / "latest.wav"
            try:
                shutil.copy2(str(final_wav), target)
            except Exception:
                pass
        on_done(f"Cap {chapter.idx:03d} listo → {last_wav or 'sin audio'}")

    except Exception as e:  # noqa: BLE001
        on_failed(f"{type(e).__name__}: {e}")
