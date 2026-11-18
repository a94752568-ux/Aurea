"""Detector de diálogos y personajes por reglas (Fase 3).
El análisis emocional fino llegará con el Director Narrativo (Fase 4)."""
import re
from collections import Counter

NAME = r"[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}"

SPEECH_VERBS = (
    "dijo|dijeron|dije|dijiste|susurró|susurro|gritó|grito|respondió|respondio|"
    "preguntó|pregunto|exclamó|exclamo|murmuró|murmuro|añadió|anadio|comentó|"
    "comento|contestó|contesto|replicó|replico|afirmó|afirmo|negó|nego|"
    "concluyó|concluyo|balbuceó|balbuceo|vociferó|vocifero|protestó|protesto|"
    "inquirió|inquirio|señaló|señalo|advirtió|advirtio|confesó|confeso|"
    "ordenó|ordeno|suplicó|suplico|pronunció|pronuncio|prosiguió|prosiguio|"
    "continuó|continuo|interrumpió|interrumpio|explicó|explico"
)

# Verbos de habla insensibles a mayúsculas (?i:...), pero nombres SIEMPRE
# sensibles: así "el" no puede colarse dentro de "Elena" (E-lena).
RE_VERB_NAME = re.compile(
    rf"(?i:\b(?:{SPEECH_VERBS})\b)\s+(?:que\s+)?"
    rf"(?:el|la|los|las|un|una|su|al)?\s*({NAME})")
RE_NAME_VERB = re.compile(rf"\b({NAME})\s+(?i:\b(?:{SPEECH_VERBS})\b)")

STOPWORDS = {
    "Pero", "Cuando", "Entonces", "Luego", "Aunque", "Mientras", "Después",
    "Despues", "Ahora", "Aquel", "Aquella", "Este", "Esta", "Ese", "Esa",
    "Todo", "Toda", "Todos", "Nada", "Nadie", "Alguien", "Algo", "Hay",
    "Fue", "Era", "Había", "Habia", "Sin", "Con", "Para", "Como", "Donde",
}


def _strip_speech_tag(para):
    """Elimina etiquetas de hablante tipo '—dijo Alberto,' o ', dijo Alberto.'
    del texto que se va a sintetizar. El hablante ya se detectó aparte."""
    # End: atribución al final "texto. —dijo Nombre." (NO al inicio del párrafo)
    # Busca la atribución al final y verifica que haya puntuación antes
    # Permite marcadores [pausa=Xs] / [silencio=Xs] después del nombre
    end = re.compile(
        rf"\s*[—–-]\s*(?i:\b(?:{SPEECH_VERBS})\b)\s+(?:el|la|los|las|un|una|su|al)?\s*({NAME})[.,]?\s*(?:\[pausa=[0-9.]+s\]|\[silencio=[0-9.]+s\])?\s*$"
    )
    m = end.search(para)
    if m:
        before = para[:m.start()].rstrip()
        if re.search(r'[.!?…]\s*$', before):
            para = before
    # Start: verbo al inicio "—dijo Nombre, texto" (requiere texto después)
    start = re.compile(
        rf"^—\s*(?i:\b(?:{SPEECH_VERBS})\b)\s+(?:el|la|los|las|un|una|su|al)?\s*({NAME})[.,]?\s+.+"
    )
    para = start.sub("", para)
    # Strip leading em dash that marks Spanish dialogue (—¿Texto)
    if para.startswith("—"):
        para = para[1:].lstrip()
    return para.strip()


def analyze(text):
    """Divide un texto en segmentos y los etiqueta.

    Devuelve [{"kind": "narration"|"dialogue", "speaker": nombre|None, "text": ...}]
    Con continuidad: un diálogo sin hablante hereda el último detectado.
    El campo "text" ya viene limpio de etiquetas tipo "—dijo X." para que el TTS
    no las lea en voz alta.

    Division: se parte por lineas. Cada linea que empiece con "—" (o similar)
    es un segmento de dialogo independiente. Las lineas de narracion
    consecutivas se agrupan en un solo segmento.
    """
    result = []
    last_speaker = None

    lines = [l.strip() for l in text.split("\n") if l.strip()]

    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith(("—", "–", '"', "«")):
            # Es un dialogo: cada linea es un segmento independiente
            is_dialogue = True
            m = RE_NAME_VERB.search(line) or RE_VERB_NAME.search(line)
            speaker = None
            if m:
                speaker = m.group(1)
                last_speaker = speaker
            elif is_dialogue and last_speaker:
                speaker = last_speaker  # continuidad de hablante
            clean = _strip_speech_tag(line) if is_dialogue else line
            result.append({
                "kind": "dialogue",
                "speaker": speaker,
                "text": clean,
            })
            i += 1
        else:
            # Es narracion: agrupar lineas consecutivas de narracion
            narration_lines = [line]
            i += 1
            while i < len(lines) and not lines[i].startswith(("—", "–", '"', "«")):
                narration_lines.append(lines[i])
                i += 1
            clean_text = " ".join(narration_lines)
            result.append({
                "kind": "narration",
                "speaker": None,
                "text": clean_text,
            })

    return result


def detect_characters(texts, max_chars=12, min_score=2.0):
    """Rankea nombres propios por apariciones y apariciones como hablante."""
    speakers = Counter()
    mentions = Counter()
    for t in texts:
        for seg in analyze(t):
            if seg["speaker"] and seg["speaker"] not in STOPWORDS:
                speakers[seg["speaker"]] += 3
        for m in re.finditer(rf"\b({NAME})\b", t):
            mentions[m.group(1)] += 1
    score = dict(speakers)
    for name, n in mentions.items():
        if name not in STOPWORDS:
            score[name] = score.get(name, 0) + n * 0.2
    ranked = sorted(score.items(), key=lambda x: -x[1])
    return [name for name, s in ranked[:max_chars] if s >= min_score]
