"""Segmentación de capítulos y utilidades puras de audio (Qt-free).

Extraído de core/generator.py en el refactor Electron para que el motor
pueda correr sin PySide6 (servidor HTTP / Electron). La UI PySide6 sigue
funcionando: core/generator.py re-exporta todo esto.
"""
import re

from ..casting.detector import analyze, _strip_speech_tag
from ..direction.rules import parse_markers


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
                # y usarlo como pausa del contenido del segmento.
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
                # Narración: mismos fragmentos, pero quitando etiquetas de
                # habla tipo "—dijo Narrador." que el guion dejó en el párrafo
                fragments = _split_markers(_strip_speech_tag(part["text"]))
                for frag in fragments:
                    sub_chunks = list(_split_chunk(frag["text"]))
                    for sub_idx, sub_chunk in enumerate(sub_chunks):
                        pause = frag["pause_after_ms"] if sub_idx == len(sub_chunks) - 1 else None
                        out.append((part["kind"], part["speaker"], sub_chunk, pause))
    return out


def gain_for(seg):
    """Volumen según intensidad y entrega dirigidas."""
    if not seg.directed:
        return 1.0
    gain = 0.55 + 0.45 * (seg.intensity if seg.intensity is not None else 0.6)
    if seg.delivery == "susurro":
        gain *= 0.6
    elif seg.delivery == "grito":
        gain *= 1.25
    return min(gain, 1.4)


# Alias de compatibilidad con el código existente
_gain_for = gain_for
