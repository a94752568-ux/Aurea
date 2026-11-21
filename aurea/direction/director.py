"""Director Híbrido: reglas instantáneas + refinamiento opcional con Ollama."""
import json
import os
import re
import threading
import time
import urllib.request

from PySide6.QtCore import QThread, Signal

from ..core.database import Database
from .rules import EMOTIONS, direct_segment, needs_llm, clamp_pause_after

OLLAMA_URL = "http://localhost:11434/api/chat"
# A10: modelo configurable por entorno; default el de la laptop (qwen3.5:4b).
# Antes era "qwen3.5:2b" fijo, probablemente no instalado en ningún lado.
MODEL = os.environ.get("AUREA_OLLAMA_MODEL", "qwen3.5:4b")
BATCH_CHARS = 1100
MAX_BATCH = 8

SYSTEM = (
    "Eres un director de doblaje para audiolibros dramatizados en español. "
    "Para cada segmento numerado decide la interpretación. Responde ÚNICAMENTE "
    "un array JSON válido, sin texto adicional, con un objeto por segmento: "
    '{"i": <número>, "emotion": <una palabra en español>, '
    '"delivery": "normal"|"susurro"|"bajo"|"firme"|"elevado"|"grito", '
    '"intensity": <0.0-1.0>, "pace": <0.6-1.3>, "pause_before_ms": <0-3000>, '
    '"pause_after_ms": <0-3000>}. '
    "Emociones válidas: neutral, alegría, tristeza, enojo, miedo, sorpresa, "
    "desprecio, ternura, orgullo, vergüenza, desesperación, determinación. "
    "Usa susurro para miedo o intimidad, grito para enfado o alarma, y pausas "
    "largas antes de las advertencias o revelaciones."
)

VALID_DELIVERY = {
    "normal", "susurro", "bajo", "firme", "elevado", "grito",
}


def _chat(prompt):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:  # M3: antes 3600 s
        data = json.loads(r.read().decode("utf-8"))
    return data["message"]["content"]


def _parse(content):
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        m = re.search(r"\[.*\]", content, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def build_items(segs):
    return [{"n": n, "seg": s, "text": " ".join(s.text.split())[:280]}
            for n, s in enumerate(segs)]


def batch_items(items, max_chars=BATCH_CHARS, max_n=MAX_BATCH):
    batches, cur, size = [], [], 0
    for it in items:
        if cur and (size + len(it["text"]) > max_chars or len(cur) >= max_n):
            batches.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += len(it["text"])
    if cur:
        batches.append(cur)
    return batches


def _clamp(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi)


class DirectionWorker(QThread):
    """1) Reglas: todo lo claro, al instante. 2) Ollama (opcional): solo
    narración ambigua."""
    progress = Signal(int, str)
    log = Signal(str)          # M7: log por su propia señal
    failed = Signal(str)
    cancelled = Signal()       # C5: el usuario pidió parar
    finished_ok = Signal(int, int)   # (segmentos por reglas, refinados por LLM)

    def __init__(self, chapter_id, use_llm=False, parent=None):
        super().__init__(parent)
        self.chapter_id = chapter_id
        self.use_llm = use_llm
        # C5/M3: cancelación y pausa reales (se comprueban por segmento/lote).
        self._cancel_requested = False
        self._pause_event = threading.Event()  # set = en pausa

    def request_cancel(self):
        self._cancel_requested = True
        self._pause_event.clear()

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
        try:
            db = Database()
            segs = db.segments(self.chapter_id)
            if not segs:
                self.failed.emit("No hay segmentos: genera primero el capítulo.")
                return
            pending = [s for s in segs if not s.directed]
            if not pending:
                self.finished_ok.emit(0, 0)
                return

            # ---- Capa 1: reglas (instantánea) ----
            # M1: is_first / prev_speaker / cola final se calculan sobre el
            # CAPÍTULO COMPLETO, no sobre `pending`: al re-dirigir un
            # capítulo parcial, la pausa inicial y la cola caían en
            # segmentos intermedios.
            pending_ids = {s.id for s in pending}
            for i, s in enumerate(segs):
                if s.id not in pending_ids:
                    continue
                if self._cancel_requested:
                    self.cancelled.emit()
                    return
                self._wait_if_paused()
                if self._cancel_requested:
                    self.cancelled.emit()
                    return
                is_first = (i == 0)
                prev_speaker = segs[i - 1].character_id if i > 0 else None
                d = direct_segment(s, is_first=is_first, prev_speaker=prev_speaker)
                # Último segmento DEL CAPÍTULO: +800 ms de cola final
                if i == len(segs) - 1:
                    d["pause_after_ms"] = clamp_pause_after(
                        d["pause_after_ms"] + 800)
                db.update_direction(
                    s.id, d["emotion"], d["delivery"], d["intensity"],
                    d["pace"], d["pause_before_ms"], d["pause_after_ms"])
            self.progress.emit(
                100, f"✔ {len(pending)} segmentos dirigidos por reglas (instantáneo)")

            # ---- Capa 2: Ollama opcional, solo narración ----
            llm_count = 0
            if self.use_llm:
                targets = [s for s in pending if needs_llm(s)]
                if not targets:
                    self.progress.emit(100, "Todo era diálogo con reglas claras: "
                                            "Ollama no hizo falta")
                else:
                    names = {c.id: c.name for c in db.characters(
                        db.chapter(self.chapter_id).book_id)}
                    items = build_items(targets)
                    batches = batch_items(items)
                    try:
                        for bi, batch in enumerate(batches):
                            if self._cancel_requested:
                                self.cancelled.emit()
                                return
                            self._wait_if_paused()
                            lines = []
                            for it in batch:
                                s = it["seg"]
                                who = names.get(s.character_id, "narrador")
                                lines.append(f'{it["n"]}. (narración; contexto: {who}) '
                                             f'"{it["text"]}"')
                            prompt = ("Dirige estos segmentos de narración de un "
                                      "audiolibro dramatizado:\n" + "\n".join(lines))
                            for d in _parse(_chat(prompt)):
                                # M2: validar el índice del LLM (un idx
                                # negativo escribía en el ÚLTIMO segmento;
                                # uno fuera de rango abortaba los lotes) y
                                # la emoción contra el vocabulario de 12.
                                try:
                                    idx = int(d.get("i"))
                                except (TypeError, ValueError):
                                    continue
                                if not 0 <= idx < len(targets):
                                    continue
                                seg = targets[idx]
                                emotion = str(d.get("emotion", "neutral")).lower()
                                if emotion not in EMOTIONS:
                                    emotion = "neutral"
                                delivery = str(d.get("delivery", "normal")).lower()
                                if delivery not in VALID_DELIVERY:
                                    delivery = "normal"
                                db.update_direction(
                                    seg.id,
                                    emotion,
                                    delivery,
                                    _clamp(d.get("intensity"), 0.0, 1.0, 0.6),
                                    _clamp(d.get("pace"), 0.6, 1.3, 1.0),
                                    int(_clamp(d.get("pause_before_ms"), 0, 3000, 300)),
                                    int(_clamp(d.get("pause_after_ms"), 0, 3000, 300)))
                                # M2: contar solo los ítems aplicados, no
                                # el lote entero.
                                llm_count += 1
                            self.progress.emit(
                                int(llm_count * 100 / max(len(targets), 1)),
                                f"Lote {bi + 1}/{len(batches)} refinado con {MODEL}")
                    except Exception as e:  # noqa: BLE001
                        self.progress.emit(
                            100, f"! Ollama no respondió ({e}); "
                                 "se conservan las reglas ya aplicadas")
            if self._cancel_requested:
                self.cancelled.emit()
                return
            self.finished_ok.emit(len(pending), llm_count)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
