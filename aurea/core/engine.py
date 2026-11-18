"""Motor de generación Qt-free (refactor Electron).

run_generation() ejecuta la generación completa de un capítulo en el hilo
del llamador: el servidor AUREA lo corre en un hilo de trabajo y el
adaptador PySide6 (core/generator.py) dentro de un QThread. Sin Qt.
"""
import shutil
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from .database import Database
from .segmentation import gain_for, segmentize
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

        if force:
            db.clear_segments(chapter.id)
        segs = db.segments(chapter.id)
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
                if not use_xtts and v not in VOICES_ES:
                    # Defensa: una voz inválida guardada en la BD no debe
                    # tumbar el job entero; avisa y usa la voz del narrador.
                    on_progress(0, f"Voz '{v}' no existe en Kokoro; "
                                   f"uso '{fallback[1]}' (corrígela en Reparto)")
                    v = fallback[1]
                if use_xtts and sample:
                    if xtts is None:
                        xtts = XTTSAdapter()
                        xtts.load()
                    samples, sr = xtts.generate(
                        seg.text, speaker_wav=sample, speed=vsp * pace)
                    voice_tag = "clon"
                else:
                    samples, sr = adapter.synthesize(
                        seg.text, voice=v, speed=vsp * pace)
                    voice_tag = v
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
