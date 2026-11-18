"""Director Híbrido Qt-free (refactor Electron).

Misma lógica que el DirectionWorker clásico: capa 1 por reglas (instantánea)
y capa 2 con Ollama opcional para narración ambigua. Los callbacks reemplazan
a las señales Qt para que el servidor AUREA pueda dirigir sin PySide6.
"""
import json
import re
import urllib.request

from ..core.database import Database
from .rules import clamp_pause_after, direct_segment, needs_llm

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.5:2b"          # cámbialo aquí si prefieres otro modelo local
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
    with urllib.request.urlopen(req, timeout=3600) as r:
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


def run_direction(chapter_id, use_llm=False,
                  on_progress=None, on_failed=None, on_done=None):
    """1) Reglas: todo lo claro, al instante. 2) Ollama (opcional): solo
    narración ambigua. on_done(n_reglas, n_llm). Bloquea: correrlo en hilo."""
    on_progress = on_progress or (lambda pct, msg: None)
    on_failed = on_failed or (lambda msg: None)
    on_done = on_done or (lambda a, b: None)

    try:
        db = Database()
        segs = db.segments(chapter_id)
        if not segs:
            on_failed("No hay segmentos: genera los segmentos primero.")
            return
        pending = [s for s in segs if not s.directed]
        if not pending:
            on_done(0, 0)
            return

        # ---- Capa 1: reglas (instantánea) ----
        for i, s in enumerate(pending):
            is_first = (i == 0)
            prev_speaker = pending[i - 1].character_id if i > 0 else None
            d = direct_segment(s, is_first=is_first, prev_speaker=prev_speaker)
            # Último segmento del capítulo: +800 ms de cola final
            if i == len(pending) - 1:
                d["pause_after_ms"] = clamp_pause_after(
                    d["pause_after_ms"] + 800)
            db.update_direction(
                s.id, d["emotion"], d["delivery"], d["intensity"],
                d["pace"], d["pause_before_ms"], d["pause_after_ms"])
        on_progress(
            100, f"{len(pending)} segmentos dirigidos por reglas (instantáneo)")

        # ---- Capa 2: Ollama opcional, solo narración ----
        llm_count = 0
        if use_llm:
            targets = [s for s in pending if needs_llm(s)]
            if not targets:
                on_progress(100, "Todo era diálogo con reglas claras: "
                                 "Ollama no hizo falta 😎")
            else:
                names = {c.id: c.name for c in db.characters(
                    db.chapter(chapter_id).book_id)}
                items = build_items(targets)
                batches = batch_items(items)
                try:
                    for bi, batch in enumerate(batches):
                        lines = []
                        for it in batch:
                            s = it["seg"]
                            who = names.get(s.character_id, "narrador")
                            lines.append(f'{it["n"]}. (narración; contexto: {who}) '
                                         f'"{it["text"]}"')
                        prompt = ("Dirige estos segmentos de narración de un "
                                  "audiolibro dramatizado:\n" + "\n".join(lines))
                        for d in _parse(_chat(prompt)):
                            idx = d.get("i")
                            if idx is None:
                                continue
                            seg = targets[int(idx)]
                            delivery = str(d.get("delivery", "normal")).lower()
                            if delivery not in VALID_DELIVERY:
                                delivery = "normal"
                            db.update_direction(
                                seg.id,
                                str(d.get("emotion", "neutral"))[:30],
                                delivery,
                                _clamp(d.get("intensity"), 0.0, 1.0, 0.6),
                                _clamp(d.get("pace"), 0.6, 1.3, 1.0),
                                int(_clamp(d.get("pause_before_ms"), 0, 3000, 300)),
                                int(_clamp(d.get("pause_after_ms"), 0, 3000, 300)))
                        llm_count += len(batch)
                        on_progress(
                            int(llm_count * 100 / len(targets)),
                            f"Lote {bi + 1}/{len(batches)} refinado con {MODEL}")
                except Exception as e:  # noqa: BLE001
                    on_progress(
                        100, f"Ollama no respondió ({e}); "
                             "se conservan las reglas ya aplicadas")
        on_done(len(pending), llm_count)
    except Exception as e:  # noqa: BLE001
        on_failed(f"{type(e).__name__}: {e}")
