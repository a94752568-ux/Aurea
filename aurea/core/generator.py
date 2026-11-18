import re
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QThread, Signal

from .database import Database
from .models import Chapter
from ..casting.cast_manager import CastManager
from ..casting.detector import analyze, _strip_speech_tag
from ..direction.rules import parse_markers
from ..tts.kokoro_adapter import KokoroAdapter
from ..voice import clone_store
from ..voice.xtts_adapter import XTTSAdapter


MAX_SEG = 600
PAUSE_S = 0.45
DIALOGUE_PAUSE_S = 0.25


def _split_chunk(p, max_chars=MAX_SEG):
    while len(p) > max_chars:
        cut = p.rfind(". ", 0, max_chars)
        cut = cut + 1 if cut > max_chars // 2 else max_chars
        yield p[:cut].strip()
        p = p[cut:].strip()
    if p:
        yield p


def _split_markers(text):
    """Divide texto en fragmentos separando en marcadores [pausa=Xs]/[silencio=Xs].

    Devuelve lista de dicts con 'text' (limpio, sin marcadores) y
    'pause_after_ms' (ms del marcador que cierra el fragmento, o None).
    """
    clean, markers = parse_markers(text)

    if not markers:
        return [{"text": clean, "pause_after_ms": None}]

    result = []
    last_end = 0
    for start, end, seconds, marker_type in markers:
        fragment = text[last_end:start].strip()
        fragment = re.sub(r"\s+", " ", fragment).strip()
        pause_ms = int(round(seconds * 1000))
        if fragment:
            result.append({"text": fragment, "pause_after_ms": pause_ms})
        last_end = end

    fragment = text[last_end:].strip()
    fragment = re.sub(r"\s+", " ", fragment).strip()
    if fragment:
        result.append({"text": fragment, "pause_after_ms": None})

    return result


def segmentize(text, use_cast):
    out = []
    if not use_cast:
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if not para:
                continue
            para = _strip_speech_tag(para)
            fragments = _split_markers(para)
            for frag in fragments:
                sub_chunks = list(_split_chunk(frag["text"]))
                for sub_idx, sub_chunk in enumerate(sub_chunks):
                    pause = frag["pause_after_ms"] if sub_idx == len(sub_chunks) - 1 else None
                    out.append(("narration", None, sub_chunk, pause))
    else:
        for part in analyze(text):
            if part["kind"] == "dialogue":
                # Para diálogo: extraer el primer marcador de pausa mid-texto
                # y usarlo como pausa del segmento, manteniendo el texto unido
                clean_text, markers = parse_markers(part["text"])
                pause_after = None
                if markers:
                    # Usar el primer marcador mid-texto como pausa del segmento
                    pause_after = int(round(markers[0][2] * 1000))
                # Dividir en chunks si es muy largo
                sub_chunks = list(_split_chunk(clean_text))
                for sub_idx, sub_chunk in enumerate(sub_chunks):
                    pause = pause_after if sub_idx == len(sub_chunks) - 1 else None
                    out.append((part["kind"], part["speaker"], sub_chunk, pause))
            else:
                # Narración: comportamiento original con fragmentos
                fragments = _split_markers(part["text"])
                for frag in fragments:
                    sub_chunks = list(_split_chunk(frag["text"]))
                    for sub_idx, sub_chunk in enumerate(sub_chunks):
                        pause = frag["pause_after_ms"] if sub_idx == len(sub_chunks) - 1 else None
                        out.append((part["kind"], part["speaker"], sub_chunk, pause))
    return out


def _gain_for(seg):
    """Volumen según intensidad y entrega dirigidas."""
    if not seg.directed:
        return 1.0
    gain = 0.55 + 0.45 * (seg.intensity if seg.intensity is not None else 0.6)
    if seg.delivery == "susurro":
        gain *= 0.6
    elif seg.delivery == "grito":
        gain *= 1.25
    return min(gain, 1.4)


class ChapterGenerator(QThread):
    progress = Signal(int, str)
    failed = Signal(str)
    finished_ok = Signal(str)

    def __init__(self, chapter: Chapter, voice, speed, force=False, parent=None):
        super().__init__(parent)
        self.chapter = chapter
        self.voice = voice
        self.speed = speed
        self.force = force
        self._last_wav = None

    def run(self):
        try:
            db = Database()
            book_id = self.chapter.book_id
            out_dir = Path.home() / "aurea" / "output" / f"book_{book_id}"
            out_dir.mkdir(parents=True, exist_ok=True)
            wav_path = out_dir / f"cap_{self.chapter.idx:03d}.wav"

            use_cast = len(db.characters(book_id)) > 1
            cast = CastManager(db) if use_cast else None
            if cast:
                cast.ensure_narrator(book_id)

            if self.force:
                db.clear_segments(self.chapter.id)
            segs = db.segments(self.chapter.id)
            if not segs:
                parts = segmentize(self.chapter.text, use_cast)
                for i, (kind, speaker, chunk, pause) in enumerate(parts):
                    cid = None
                    if use_cast:
                        name = speaker if (kind == "dialogue" and speaker) else "Narrador"
                        cid = cast.character_id(book_id, name)
                    segs.append(db.add_segment(self.chapter.id, i, chunk,
                                               kind=kind, character_id=cid,
                                               pause_after_ms=pause))

            vmap, fallback = (cast.voice_map(book_id) if cast
                              else ({}, ("kokoro", self.voice, self.speed)))
            todo = [s for s in segs
                    if not (s.wav_path and Path(s.wav_path).exists())]
            if todo:
                total = len(todo)
                adapter = KokoroAdapter()
                clone_map = clone_store.clone_sample_map()
                xtts = None
                for i, seg in enumerate(todo):
                    engine, voice, vspeed = vmap.get(seg.character_id, fallback)
                    pace = seg.pace if (seg.directed and seg.pace) else 1.0
                    t0 = time.perf_counter()
                    use_xtts = (engine == "xtts")
                    sample = clone_map.get(seg.character_id) if use_xtts else None
                    if use_xtts and sample:
                        if xtts is None:
                            xtts = XTTSAdapter()
                            xtts.load()
                        samples, sr = xtts.generate(
                            seg.text, speaker_wav=sample, speed=vspeed * pace)
                        voice_tag = "clon"
                    else:
                        samples, sr = adapter.synthesize(
                            seg.text, voice=voice, speed=vspeed * pace)
                        voice_tag = voice
                    samples = np.clip(samples * _gain_for(seg), -1.0, 1.0)
                    seg_wav = out_dir / f"cap_{self.chapter.idx:03d}_seg{seg.idx:04d}.wav"
                    adapter.save_wav(seg_wav, samples, sr)
                    db.update_segment(seg.id, str(seg_wav), "done")
                    tag = f" · {seg.emotion}/{seg.delivery}" if seg.directed else ""
                    self.progress.emit(
                        int((i + 1) * 100 / total),
                        f"Segmento {i + 1}/{total} voz {voice_tag}{tag} "
                        f"({time.perf_counter() - t0:.1f}s)")
                    if seg.directed:
                        seg.display_narration()
                        log_line = f"✔ {seg.idx + 1:02d} [{seg.kind}]{tag}"
                        log_line += f" · {seg.emotion}/{seg.delivery} · vel {seg.pace:.2f}"
                        self.log_line(log_line)
                    self.worker_log(f"✅ Cap {self.chapter.idx:03d} · Seg {seg.idx:04d} → {seg_wav}")
                    self._last_wav = str(seg_wav)
                if xtts is not None:
                    xtts.release()

            final_wav = self._concat(segs, vmap, fallback, wav_path)
            if final_wav:
                self._last_wav = str(final_wav)
                target = Path.home() / "aurea" / "output" / "latest.wav"
                try:
                    import shutil
                    shutil.copy2(str(final_wav), target)
                except Exception:
                    pass
            self.finished_ok.emit(f"✔ Cap {self.chapter.idx:03d} listo → {self._last_wav}")

        except Exception as e:
            self.failed.emit(str(e))

    def log_line(self, msg):
        self.progress.emit(0, msg)

    def worker_log(self, msg):
        self.progress.emit(0, msg)

    @staticmethod
    def _concat(segs, vmap, fallback, wav_path):
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