"""Mitigación de anomalías de pronunciación (v0.7.2).

El G2P de Kokoro (Misaki/espeak-ng) es el cuello de botella: convierte el
texto a fonemas antes de sintetizar, y su voz española por defecto es
peninsular ("es": ceceo, /θ/ en z/c+e/i) mientras Alberto narra en
español mexicano (seseo, /s/). Además hay palabras que el G2P no
resuelve bien (nombres mayas/náhuatl con "x" = /ʃ/) y números que
espeak expande mal ("1,000" -> "uno coma cero cero cero").

Este módulo prepara el texto SOLO en el momento de sintetizar: el texto
guardado en BD y el guion no se tocan nunca. Cada sustitución se registra
en el log de generación para que Alberto vea qué se cambió.

Piezas:
1. G2P latinoamericano: KokoroAdapter usa EspeakG2P(language="es-419")
   (verificado en VM: "Moctezuma" mˌoktesˈuma, "cielo" sjˈelo, "hace"
   ˈase; con "es" eran /θ/: mˌokteθˈuma, θjˈelo, ˈaθe).
2. Léxico de pronunciación: palabra -> reescritura que el G2P sí
   resuelve (p. ej. "Ixbalanqué" -> "Ishbalanqué": /iʃβalaŋˈke/ maya,
   frente a /iksβalaŋˈke/). El usuario puede ampliarlo/corregirlo en
   AUREA_HOME/pronunciation.json (sus entradas ganan a las de serie).
3. Números en dígitos -> palabras en español ("1519" -> "mil
   quinientos diecinueve"; "1,000" -> "mil"; "3.5" -> "tres punto
   cinco"; "50%" -> "cincuenta por ciento").

Límites honestos: el diccionario mitiga las palabras CONOCIDAS; no
puede arreglar palabras desconocidas, y el G2P sigue mandando. "baja"
NO está en el léxico: en la VM el G2P da /b/ correcto (bˈaxa) y el ASR
(faster-whisper tiny) distingue "baja" de "paja" en par mínimo con las
3 voces Kokoro, así que el "baja"->"paja" no se reprodujo con Kokoro
(queda para oído en la laptop; podría ser XTTS o un contexto concreto).
"""
import json
import os
import re
from pathlib import Path

# --------------------------------------------------------------------- #
# Léxico de serie (verificado por fonemas en VM, misaki 0.9.4 es-419)
# --------------------------------------------------------------------- #
DEFAULT_LEXICON = {
    # Nombres mayas: la "x" es /ʃ/ (sh), no /ks/ ni /s/.
    "ixbalanqué": "ishbalanqué",
    "xibalbá": "shibalbá",
    "ixquic": "ishquic",
    # Hunahpú ya sale bien del G2P (ˌunapˈu = /unaˈpu/); sin entrada.
    # Moctezuma/Citlali/Quetzalcóatl los arregla es-419 solo; sin entrada.
}

_LEXICON_CACHE = {"mtime": None, "path": None, "data": None}


def lexicon_path() -> Path:
    """Ruta del léxico editable por el usuario (AUREA_HOME honrado)."""
    home = Path(os.environ.get("AUREA_HOME", Path.home() / "aurea"))
    return home / "pronunciation.json"


def load_lexicon() -> dict:
    """Léxico efectivo: serie + archivo del usuario (el usuario gana).

    El archivo es un JSON plano {"palabra": "reescritura", ...}. Una
    entrada con valor "" desactiva esa palabra de serie. Se recarga
    solo si el archivo cambia (mtime).
    """
    path = lexicon_path()
    try:
        mtime = path.stat().st_mtime if path.exists() else None
    except OSError:
        mtime = None
    if _LEXICON_CACHE["path"] == str(path) and _LEXICON_CACHE["mtime"] == mtime:
        return _LEXICON_CACHE["data"]
    data = dict(DEFAULT_LEXICON)
    if mtime is not None:
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                for key, value in user.items():
                    key = str(key).strip().lower()
                    if not key:
                        continue
                    if value is None or str(value).strip() == "":
                        data.pop(key, None)
                    else:
                        data[key] = str(value).strip()
        except (OSError, ValueError, UnicodeDecodeError):
            pass  # archivo roto: se ignora y manda el léxico de serie
    _LEXICON_CACHE.update({"mtime": mtime, "path": str(path), "data": data})
    return data


# --------------------------------------------------------------------- #
# Números en español (sin dependencias nuevas)
# --------------------------------------------------------------------- #
_UNITS = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis",
          "siete", "ocho", "nueve", "diez", "once", "doce", "trece",
          "catorce", "quince"]
_TENS = ["", "", "veinte", "treinta", "cuarenta", "cincuenta",
         "sesenta", "setenta", "ochenta", "noventa"]
_HUNDREDS = ["", "ciento", "doscientos", "trescientos", "cuatrocientos",
             "quinientos", "seiscientos", "setecientos", "ochocientos",
             "novecientos"]


def _under_1000(n: int) -> str:
    if n == 100:
        return "cien"
    if n < 16:
        return _UNITS[n]
    if n < 20:
        return {16: "dieciséis", 17: "diecisiete", 18: "dieciocho",
                19: "diecinueve"}[n]
    if n < 30:
        return {20: "veinte", 21: "veintiuno", 22: "veintidós",
                23: "veintitrés", 24: "veinticuatro", 25: "veinticinco",
                26: "veintiséis", 27: "veintisiete", 28: "veintiocho",
                29: "veintinueve"}[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return _TENS[tens] if unit == 0 else f"{_TENS[tens]} y {_UNITS[unit]}"
    hundreds, rest = divmod(n, 100)
    head = "cien" if hundreds == 1 and rest == 0 else _HUNDREDS[hundreds]
    return head if rest == 0 else f"{head} {_under_1000(rest)}"


def number_to_words_es(n: int) -> str:
    """Entero 0..999_999_999_999 en palabras (años incluidos: 1519).

    "uno" es la forma de cita; delante de un sustantivo ("1 casa") el
    español pediría "un/una", pero sin contexto de género "uno" es la
    opción sin inventar.
    """
    if n < 0:
        return "menos " + number_to_words_es(-n)
    if n < 1000:
        return _under_1000(n)
    if n < 1_000_000:
        thousands, rest = divmod(n, 1000)
        head = "mil" if thousands == 1 else f"{number_to_words_es(thousands)} mil"
        return head if rest == 0 else f"{head} {_under_1000(rest)}"
    if n < 1_000_000_000:
        millions, rest = divmod(n, 1_000_000)
        head = "un millón" if millions == 1 else \
            f"{number_to_words_es(millions)} millones"
        tail = ""
        if rest:
            tail = f" {number_to_words_es(rest)}" if rest >= 1000 else \
                f" {_under_1000(rest)}"
        return head + tail
    billions, rest = divmod(n, 1_000_000_000)
    head = "mil millones" if billions == 1 else \
        f"{number_to_words_es(billions)} mil millones"
    return head if rest == 0 else f"{head} {number_to_words_es(rest)}"


def _expand_number_token(token: str, percent: bool) -> str:
    """Expande UN token numérico; si no se clasifica, se deja intacto."""
    if not re.search(r"\d", token):
        return token
    # Separador de miles + decimal (español): 1.000,5 -> mil coma cinco
    m = re.fullmatch(r"(\d{1,3}(?:\.\d{3})+),(\d+)", token)
    if m:
        entero = int(m.group(1).replace(".", ""))
        dec = " ".join(number_to_words_es(int(d)) for d in m.group(2))
        out = f"{number_to_words_es(entero)} coma {dec}"
        return out + " por ciento" if percent else out
    # Solo separadores de miles (punto o coma, grupos de 3): 1.000 / 1,000,000
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", token):
        out = number_to_words_es(int(token.replace(".", "").replace(",", "")))
        return out + " por ciento" if percent else out
    # Decimal con un solo separador: 3.5 / 3,5 -> tres punto/coma cinco
    m = re.fullmatch(r"(\d+)([.,])(\d+)", token)
    if m:
        dec = " ".join(number_to_words_es(int(d)) for d in m.group(3))
        sep = "punto" if m.group(2) == "." else "coma"
        out = f"{number_to_words_es(int(m.group(1)))} {sep} {dec}"
        return out + " por ciento" if percent else out
    # Entero simple
    if re.fullmatch(r"\d+", token):
        out = number_to_words_es(int(token))
        return out + " por ciento" if percent else out
    return token  # p. ej. versiones "2.1.2": se dejan como están


def expand_numbers(text: str, substitutions: list | None = None) -> str:
    """Números en dígitos -> palabras. Registra cada cambio en substitutions."""
    def repl(m):
        token = m.group(0)
        percent = token.endswith("%")
        core = token[:-1] if percent else token
        expanded = _expand_number_token(core, percent)
        if expanded != token and substitutions is not None:
            substitutions.append((token, expanded))
        return expanded

    # % va pegado al número; se captura junto al token.
    return re.sub(r"\d[\d.,]*%|\d[\d.,]*\d|\d", repl, text)


# --------------------------------------------------------------------- #
# Preparación en el momento de sintetizar (no toca BD ni guion)
# --------------------------------------------------------------------- #
def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def apply_lexicon(text: str, substitutions: list | None = None,
                  lexicon: dict | None = None) -> str:
    """Aplica el léxico por palabra completa (insensible a mayúsculas)."""
    lex = lexicon if lexicon is not None else load_lexicon()
    if not lex:
        return text
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in sorted(lex, key=len, reverse=True)) + r")\b",
        re.IGNORECASE,
    )

    def repl(m):
        word = m.group(1)
        new = _match_case(word, lex[word.lower()])
        if new != word and substitutions is not None:
            substitutions.append((word, new))
        return new

    return pattern.sub(repl, text)


def prepare_text_for_tts(text: str, log=None):
    """Texto listo para sintetizar + sustituciones aplicadas.

    Orden: léxico primero (los nombres no son números), números después.
    Devuelve (nuevo_texto, sustituciones). Cada sustitución se registra
    con log("◆ Pronunciación: «X» -> «Y»") si se pasa un log.
    """
    substitutions = []
    out = apply_lexicon(text, substitutions)
    out = expand_numbers(out, substitutions)
    if log is not None:
        for old, new in substitutions:
            log(f"◆ Pronunciación: «{old}» -> «{new}»")
    return out, substitutions
