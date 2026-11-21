import re
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QThread, Signal

from .audio_processor import AudioProcessor
from .ambience import mix_chapter
from .database import Database, aurea_home
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
    log = Signal(str)          # M7: el log va por su propia señal; antes
    failed = Signal(str)       # emitía progress(0, msg) y reiniciaba la barra
    cancelled = Signal()       # C5: el usuario pidió parar
    finished_ok = Signal(str)

    def __init__(self, chapter: Chapter, voice, speed, force=False, parent=None,
                 ambience=None, bed_level=0.25):
        super().__init__(parent)
        self.chapter = chapter
        self.voice = voice
        self.speed = speed
        self.force = force
        # v0.7.0: ambiente sonoro del capítulo ('none' = sin ambiente).
        # Si no se pasa, se lee del capítulo (BD).
        self.ambience = ambience
        self.bed_level = bed_level
        self._last_wav = None
        # C5: cancelación y pausa reales (el bucle las comprueba por segmento).
        self._cancel_requested = False
        self._pause_event = threading.Event()  # set = en pausa

    # ---- control real (C5) ----
    def request_cancel(self):
        """Pide parar. No bloquea: el bucle lo ve en el siguiente segmento."""
        self._cancel_requested = True
        self._pause_event.clear()  # desbloquear si estaba en pausa

    def request_pause(self):
        self._pause_event.set()

    def request_resume(self):
        self._pause_event.clear()

    @property
    def paused(self):
        return self._pause_event.is_set()

    def _wait_if_paused(self):
        while self._pause_event.is_set() and not self._cancel_requested:
            time.sleep(0.1)

    def run(self):
        db = None
        xtts = None
        try:
            db = Database()
            book_id = self.chapter.book_id
            out_dir = aurea_home() / "output" / f"book_{book_id}"
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
                with db.transaction():
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
                for i, seg in enumerate(todo):
                    if self._cancel_requested:
                        break
                    self._wait_if_paused()
                    if self._cancel_requested:
                        break
                    engine, voice, vspeed = vmap.get(seg.character_id, fallback)
                    pace = seg.pace if (seg.directed and seg.pace) else 1.0
                    t0 = time.perf_counter()
                    use_xtts = (engine == "xtts")
                    sample = clone_map.get(seg.character_id) if use_xtts else None
                    if use_xtts and sample and Path(sample).exists():
                        if xtts is None:
                            xtts = XTTSAdapter()
                            xtts.load()
                        samples, sr = xtts.generate(
                            seg.text, speaker_wav=sample, speed=vspeed * pace)
                        voice_tag = "clon"
                    else:
                        if use_xtts:
                            # A6: XTTS sin muestra (o muestra borrada) → voz
                            # narradora con aviso; nunca revienta el capítulo.
                            self.log.emit(
                                f"⚠ Seg {seg.idx:04d}: voz clonada sin muestra; "
                                f"uso narradora {fallback[1]}")
                        samples, sr = adapter.synthesize(
                            seg.text,
                            voice=(fallback[1] if use_xtts else voice),
                            speed=vspeed * pace)
                        voice_tag = voice if not use_xtts else f"{fallback[1]} (fallback)"
                    samples = np.clip(samples * _gain_for(seg), -1.0, 1.0)
                    seg_wav = out_dir / f"cap_{self.chapter.idx:03d}_seg{seg.idx:04d}.wav"
                    adapter.save_wav(seg_wav, samples, sr)
                    db.update_segment(seg.id, str(seg_wav), "done")
                    # C1: refrescar el objeto en memoria. update_segment solo
                    # toca la BD; sin esto _concat no veía el WAV en la
                    # primera generación y se reportaba éxito falso.
                    seg.wav_path = str(seg_wav)
                    seg.status = "done"
                    tag = f" · {seg.emotion}/{seg.delivery}" if seg.directed else ""
                    self.progress.emit(
                        int((i + 1) * 100 / total),
                        f"Segmento {i + 1}/{total} voz {voice_tag}{tag} "
                        f"({time.perf_counter() - t0:.1f}s)")
                    self.log.emit(f"✅ Cap {self.chapter.idx:03d} · Seg {seg.idx:04d} → {seg_wav}")
                    self._last_wav = str(seg_wav)

            if self._cancel_requested:
                self.cancelled.emit()
                return

            # v0.7.0: ambiente del capítulo (parámetro > BD).
            ambience = self.ambience
            if ambience is None:
                ambience = getattr(self.chapter, "ambience", "none") or "none"
            bed_level = self.bed_level
            if self.ambience is None:
                bed_level = getattr(self.chapter, "bed_level", 0.25) or 0.25
            final_wav = self._concat(segs, vmap, fallback, wav_path,
                                     log=self.log.emit, ambience=ambience,
                                     bed_level=bed_level)
            if final_wav and Path(final_wav).exists():
                self._last_wav = str(final_wav)
                target = aurea_home() / "output" / "latest.wav"
                try:
                    import shutil
                    shutil.copy2(str(final_wav), target)
                except Exception:
                    pass
                self.finished_ok.emit(f"✔ Cap {self.chapter.idx:03d} listo → {self._last_wav}")
            else:
                # C1: sin WAV final no hay éxito (antes se emitía
                # finished_ok con la ruta del último segmento).
                self.failed.emit(
                    f"No se pudo producir el WAV final del capítulo {self.chapter.idx:03d}.")

        except Exception as e:
            self.failed.emit(str(e))
        finally:
            # M8: liberar XTTS (~4.4 GB en RAM) y cerrar la conexión del
            # hilo aunque haya una excepción a mitad de generación.
            if xtts is not None:
                try:
                    xtts.release()
                except Exception:
                    pass
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    @staticmethod
    def _concat(segs, vmap, fallback, wav_path, log=None,
                ambience="none", bed_level=0.25):
        clips, sr = [], 24000
        ordered = [s for s in segs if s.wav_path and Path(s.wav_path).exists()]
        if not ordered:
            return None

        # A5: ya no se aborta el capítulo por un '[' en el texto. Los
        # marcadores [pausa=Xs] se eliminan en segmentize; cualquier '['
        # restante es contenido legítimo (p. ej. "[risas]").

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
        # v0.7.0: mezcla de ambiente con ducking ANTES del master
        # (el master normaliza el conjunto y conserva la relación
        # voz/cama). Un fallo de mezcla NUNCA mata el capítulo.
        if ambience and ambience != "none":
            try:
                audio = mix_chapter(audio, ambience, sr, bed_level=bed_level)
                if log is not None:
                    log(f"◆ Ambiente «{ambience}» mezclado (cama {bed_level:.2f})")
            except Exception as e:
                if log is not None:
                    log(f"⚠ mezcla de ambiente falló ({e}); se guarda sin cama")
        # A9: procesado master (RMS/compresión/limitador/graves) sobre el
        # WAV final. Un fallo de procesado NUNCA mata el capítulo.
        try:
            audio = AudioProcessor(sr=sr).process_master(audio, sr=sr)
        except Exception as e:
            if log is not None:
                log(f"⚠ process_master falló ({e}); se guarda el audio sin procesar")
        Path(wav_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(wav_path), audio, sr, subtype="PCM_16")  # A7
        return wav_path