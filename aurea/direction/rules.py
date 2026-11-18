"""Capa de reglas del Director Híbrido: dirección instantánea y
determinista para los casos claros. El LLM (opcional) solo refina
la narración ambigua. Cero espera, cero errores en lo literal.

Vocabularios (Espec 4.1):
  Eje A Emoción (12): neutral, alegría, tristeza, enojo, miedo,
    sorpresa, desprecio, ternura, orgullo, vergüenza, desesperación,
    determinación.
  Eje B Volumen (6): susurro, bajo, normal, firme, elevado, grito.
  Eje C Ritmo (5): lento, pausado, normal, rápido, entrecortado.
  Intensidad: 1–5.
"""
import re

# ============================================================
# VOCABULARIOS (Espec 4.1)
# ============================================================

EMOTIONS = {
    "neutral", "alegría", "tristeza", "enojo", "miedo", "sorpresa",
    "desprecio", "ternura", "orgullo", "vergüenza", "desesperación",
    "determinación",
}

DELIVERIES = {
    "susurro", "bajo", "normal", "firme", "elevado", "grito",
}

PACES = {
    "lento", "pausado", "normal", "rápido", "entrecortado",
}

# ============================================================
# MARCADORES EXPLÍCITOS DEL AUTOR
# ============================================================

MARKER_RE = re.compile(r"\[(pausa|silencio)=([0-9]+(?:\.[0-9]+)?)s\]")

# Puntuación final y su pausa base (ms)
PUNCT_PAUSE = {
    ".": 450,
    "?": 450,
    "!": 450,
    ",": 180,
    ";": 300,
    ":": 300,
    "…": 700,
    "—": 250,
}

# Multiplicadores de ritmo (Espec 2.3)
PACE_MULT = {
    "lento": 1.5,
    "pausado": 1.8,
    "normal": 1.0,
    "rápido": 0.7,
    "entrecortado": 0.8,
}

# Multiplicadores de emoción (Espec 2.3)
EMOTION_MULT = {
    "miedo": 1.3,
    "tristeza": 1.3,
    "determinación": 1.2,
    "enojo": 0.8,
    "enfado": 0.8,
    "alegría": 1.1,
    "sorpresa": 1.15,
    "desprecio": 0.9,
    "ternura": 0.95,
    "orgullo": 1.05,
    "vergüenza": 0.85,
    "desesperación": 1.25,
    "neutral": 1.0,
}

# Verbos de habla con defaults de volumen (Espec 4.2)
VERB_TO_DELIVERY = {
    "susurró": "susurro", "susurro": "susurro",
    "murmuró": "susurro", "murmuro": "susurro",
    "balbuceó": "susurro", "balbuceo": "susurro",
    "cuchicheó": "susurro", "cuchicheo": "susurro",
    "exclamó": "elevado", "exclamo": "elevado",
    "gritó": "grito", "grito": "grito",
    "ordenó": "firme", "ordeno": "firme",
    "advirtió": "firme", "advirtio": "firme",
    "concluyó": "firme", "concluyo": "firme",
    "dijo": "normal", "respondió": "normal", "respondio": "normal",
    "preguntó": "normal", "pregunto": "normal",
    "explicó": "normal", "explico": "normal",
    "añadió": "normal", "anadio": "normal",
    "señaló": "normal", "senalo": "normal",
    "comentó": "normal", "comento": "normal",
}

VERB_RE = re.compile(
    r"\b(" + "|".join(sorted(VERB_TO_DELIVERY.keys(), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def default_delivery_for_verb(text):
    """Devuelve el default de volumen según el verbo de habla en el texto.
    Si no hay verbo reconocido, devuelve None (el llamador decide)."""
    m = VERB_RE.search(text)
    if m:
        v = m.group(1).lower()
        return VERB_TO_DELIVERY.get(v)
    return None


# ============================================================
# FUNCIONES PURAS DE PAUSAS
# ============================================================

def parse_markers(text):
    """Extrae marcadores [pausa=Xs] / [silencio=Xs] del texto.

    Devuelve (texto_limpio, marcadores) donde marcadores es una lista
    de tuplas (inicio, fin, segundos, tipo). El marcador se elimina
    del texto limpio.
    """
    markers = []
    for m in MARKER_RE.finditer(text):
        seconds = float(m.group(2))
        seconds = min(max(seconds, 0.1), 3.0)
        markers.append((m.start(), m.end(), seconds, m.group(1)))

    clean = MARKER_RE.sub("", text)
    clean = re.sub(r"\s+", " ", clean).strip()

    return clean, markers


def pause_for_punctuation(text):
    """Pausa base (ms) según el carácter final del segmento."""
    end = text.rstrip()
    if not end:
        return 180
    if end.endswith("..."):
        return 700
    last = end[-1]
    if last in PUNCT_PAUSE:
        return PUNCT_PAUSE[last]
    # Sin puntuación reconocida: se trata como coma
    return 180


def apply_emotion_pace(base_ms, pace, emotion):
    """Aplica multiplicadores de ritmo y emoción sobre un valor base.

    Orden: primero ritmo, luego emoción. Redondea al entero.
    """
    ms = base_ms
    if pace in PACE_MULT:
        ms = ms * PACE_MULT[pace]
    if emotion in EMOTION_MULT:
        ms = ms * EMOTION_MULT[emotion]
    return int(ms)  # truncamiento al entero, según ejemplos del spec


def speaker_change_pause():
    """Pausa (ms) por cambio de hablante entre segmentos consecutivos."""
    return 500


def clamp_pause_after(ms, explicit=False):
    """Clampa pause_after_ms: 0–2000 ms, o 0–3000 si es marcador explícito."""
    hi = 3000 if explicit else 2000
    return max(0, min(int(ms), hi))


def clamp_pause_before(ms):
    """Clampa pause_before_ms: máximo 1200 ms."""
    return max(0, min(int(ms), 1200))


# ============================================================
# DIRECCIÓN DE SEGMENTO
# ============================================================

def direct_segment(seg, is_first=False, prev_speaker=None):
    """Devuelve la dirección dictada por reglas para un segmento.

    Rellena pause_before_ms y pause_after_ms en TODOS los segmentos
    (nunca NULL). Prioridad para pause_after_ms:
      1. Marcador explícito del autor ([pausa=Xs] / [silencio=Xs])
      2. Cambio de hablante (500 ms)
      3. Puntuación final (tabla 2.2) + modificadores de ritmo/emoción
      4. 0 ms

    Args:
        seg: Segmento a dirigir.
        is_first: Si es el primer segmento del capítulo
                  (pause_before_ms = 600 ms).
        prev_speaker: character_id del segmento anterior (para detectar
                      cambio de hablante).
    """
    text = seg.text

    # --- Delivery por verbo de habla (Espec 4.2) ---
    verb_delivery = default_delivery_for_verb(text)
    delivery = verb_delivery if verb_delivery else "normal"

    # --- Emoción ---
    emotion = "neutral"
    m = re.search(
        r"\b(" + "|".join(sorted(EMOTIONS, key=len, reverse=True)) + r")\b",
        text, re.IGNORECASE,
    )
    if m:
        emotion = m.group(1).lower()

    # --- Intensity por puntuación y delivery ---
    excl, quest = text.count("!"), text.count("?")
    if delivery == "susurro":
        intensity = 0.35
    elif excl >= 2:
        intensity = 0.95
    elif excl:
        intensity = 0.85
    elif quest:
        intensity = 0.6
    else:
        intensity = 0.6

    # --- Pace ---
    if delivery == "susurro":
        pace = 0.85
    elif delivery == "grito":
        pace = 1.05
    elif "..." in text or "…" in text:
        pace = 0.9
    else:
        pace = 1.0

    # --- Calcular pause_after_ms ---
    # Prioridad: 1) Marcador explícito del autor (ya guardado en pause_after_ms
    #              por segmentize al procesar [pausa=Xs] / [silencio=Xs])
    #            2) Cambio de hablante (500 ms)
    #            3) Puntuación final + modificadores
    #            4) 0 ms
    if seg.pause_after_ms is not None and seg.pause_after_ms > 0:
        # Ya viene de un marcador explícito [pausa=Xs] / [silencio=Xs]
        # Los multiplicadores NO se aplican a marcadores explícitos (Espec 2.1)
        pause_after = int(seg.pause_after_ms)
        is_explicit = True
    elif prev_speaker is not None and seg.character_id is not None \
            and seg.character_id != prev_speaker:
        pause_after = speaker_change_pause()
        is_explicit = False
    else:
        base = pause_for_punctuation(text)
        pause_after = apply_emotion_pace(base, pace, emotion)
        is_explicit = False

    # Pregunta al final de turno de diálogo: +200 ms
    # Solo si NO es un marcador explícito ni cambio de hablante
    if not is_explicit and seg.kind == "dialogue" and text.rstrip().endswith("?"):
        pause_after += 200

    # Intensidad 5: +300 ms después (golpe dramático)
    # Solo si NO es un marcador explícito ni cambio de hablante
    if not is_explicit and seg.intensity == 5:
        pause_after += 300

    pause_after = clamp_pause_after(pause_after, explicit=is_explicit)

    # --- Calcular pause_before_ms ---
    if is_first:
        pause_before = clamp_pause_before(600)
    else:
        pause_before = 0

    return {
        "emotion": emotion,
        "delivery": delivery,
        "intensity": intensity,
        "pace": pace,
        "pause_before_ms": pause_before,
        "pause_after_ms": pause_after,
    }


def needs_llm(seg):
    """Solo la narración (sin verbos de habla) se deja al LLM opcional."""
    return seg.kind == "narration"
