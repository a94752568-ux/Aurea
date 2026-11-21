"""Inferencia de género para el reparto inteligente (v0.7.0).

Decisión de Alberto (2026-11-20): las voces Kokoro `em_*` son
masculinas y `ef_*` femeninas. El casting automático asigna la voz
según el género del personaje; el género se infiere así, en orden:

  1. léxico de nombres (español común + Popol Vuh + EL QUINTO SOL),
  2. heurísticas de español (títulos don/doña, artículos el/la
     delante del nombre en el contexto, terminaciones -o/-a),
  3. si nada decide -> "desconocido": NUNCA se inventa el género.

Un género "desconocido" usa la voz del narrador por defecto en el
auto-casting y la UI lo marca para que Alberto lo confirme.

El género de una VOZ se lee de su id Kokoro: la segunda letra del
prefijo es el género (`em_alex` -> masculino, `ef_dora` -> femenino).
Los perfiles XTTS no llevan género en el id -> "desconocido".
"""
import re
import unicodedata

MASCULINO = "masculino"
FEMENINO = "femenino"
DESCONOCIDO = "desconocido"

GENDERS = (MASCULINO, FEMENINO, DESCONOCIDO)

# Etiquetas cortas para la UI (glifos seguros, sin emoji).
GENDER_LABEL = {MASCULINO: "M", FEMENINO: "F", DESCONOCIDO: "?"}

# --- léxico de nombres -------------------------------------------------
# Nombres normalizados (minúsculas, sin acentos) -> género.
# Popol Vuh: Hunahpú, Ixbalanqué, Ixquic, Xmucane, Zipacna, Cabracán,
# Vucub-Caquix, Hun-Camé, Vucub-Camé, Xpiyacoc, Kukulkán, Huracán.
# EL QUINTO SOL: Moctezuma, Tlilpotonqui, Iztacxochitl, Xochitl,
# Citlali, Tochtli, Tlahuicole, Yaotl.
MALE_NAMES = {
    # Popol Vuh / maya
    "hunahpu", "ixbalanque", "zipacna", "cabracan", "vucub-caquix",
    "hun-came", "vucub-came", "xpiyacoc", "kukulkan", "huracan",
    "tohil", "awilix", "jacawitz", "quetzalcoatl", "kukulkan",
    # EL QUINTO SOL / mexica
    "moctezuma", "tlilpotonqui", "tochtli", "tlahuicole", "yaotl",
    "huitzilopochtli", "tezcatlipoca", "tlaloc", "nanahuatzin",
    "tecuciztecatl", "quetzalcoatl",
    # español común
    "alberto", "mauricio", "carlos", "juan", "jose", "miguel", "alejandro",
    "fernando", "ricardo", "jorge", "daniel", "david", "luis", "pedro",
    "pablo", "diego", "andres", "javier", "francisco", "antonio", "manuel",
    "rafael", "eduardo", "roberto", "mario", "oscar", "sergio", "victor",
    "arturo", "enrique", "gabriel", "samuel", "hector", "raul", "ruben",
    "ivan", "angel", "felix", "tomas", "marcos", "adrian", "ignacio",
    "emilio", "alfonso", "ramon", "esteban", "nicolas", "sebastian",
    "mateo", "santiago", "leonardo", "felipe", "rodolfo", "guillermo",
    "ernesto", "julian", "martin", "hugo", "cesar", "armando", "gerardo",
    "jaime", "rene", "saul", "abel", "cain", "adonai", "ezequiel",
    "isaias", "jeremias", "salomon", "abraham", "isaac", "jacob",
}

FEMALE_NAMES = {
    # Popol Vuh / maya
    "ixquic", "xquic", "xmucane", "ixmucane", "xibalba", "ixchel",
    # EL QUINTO SOL / mexica
    "citlali", "xochitl", "iztacxochitl", "xochiquetzal", "coatlicue",
    "tonantzin", "chalchiuhtlicue", "mayahuel", "tlazolteotl",
    # español común
    "maria", "ana", "lucia", "carmen", "elena", "sofia", "valeria",
    "camila", "isabella", "fernanda", "daniela", "gabriela", "alejandra",
    "mariana", "paola", "natalia", "patricia", "rosa", "laura", "teresa",
    "veronica", "adriana", "claudia", "silvia", "marta", "gloria",
    "beatriz", "alicia", "angelica", "carolina", "diana", "liliana",
    "monica", "norma", "susana", "leticia", "lorena", "marcela",
    "ivonne", "erika", "sandra", "olga", "irene", "pilar", "mercedes",
    "dolores", "concepcion", "juana", "francisca", "antonieta",
    "rosario", "amparo", "consuelo", "socorro", "esperanza", "luz",
    "angela", "elisa", "isabel", "carmen", "raquel", "esther", "rebeca",
    "noemi", "sara", "eva", "judith", "debora", "ruth", "magdalena",
}

# Nota: "guadalupe" NO está en ningún léxico: en México es unisex
# (José Guadalupe / María Guadalupe) y solo el contexto lo decide.
# Excepciones a la regla de terminación -o/-a (el léxico manda antes,
# pero un nombre nuevo que termine en -a y sea masculino, o en -o y
# sea femenino, cae aquí).
MALE_ENDING_A = {"ezra", "luca", "noa", "elijah", "jonah"}
FEMALE_ENDING_O = {"rosario", "amparo", "consuelo", "socorro"}


def _norm(name: str) -> str:
    """Minúsculas, sin acentos, espacios recortados."""
    name = unicodedata.normalize("NFD", (name or "").strip().lower())
    return "".join(c for c in name if unicodedata.category(c) != "Mn")


def voice_gender(voice_id: str) -> str:
    """Género de una voz por su id Kokoro (`em_*`/`ef_*`).

    La convención Kokoro es <idioma><género>_: la segunda letra es
    'm' (masculino) o 'f' (femenino). Cualquier otro id (XTTS, vacío)
    -> "desconocido".
    """
    m = re.match(r"^[a-z]([mf])_", (voice_id or "").strip().lower())
    if not m:
        return DESCONOCIDO
    return MASCULINO if m.group(1) == "m" else FEMENINO


def infer_gender(name: str, context: str = "") -> tuple[str, str]:
    """Infiere el género de un personaje.

    Devuelve (género, fuente). Fuente: 'lexico', 'titulo', 'articulo',
    'terminacion' o 'desconocido'. El contexto es el texto del libro
    (o del capítulo) donde aparece el personaje; se usa solo para
    títulos y artículos.
    """
    norm = _norm(name)
    if not norm:
        return DESCONOCIDO, "desconocido"
    if norm in MALE_NAMES:
        return MASCULINO, "lexico"
    if norm in FEMALE_NAMES:
        return FEMENINO, "lexico"

    ctx = (context or "").lower()
    # Títulos: "don X" / "doña X" (también "señor/señora X").
    if re.search(rf"\bdon\s+{re.escape(norm)}\b", ctx):
        return MASCULINO, "titulo"
    if re.search(rf"\bdoña\s+{re.escape(norm)}\b", ctx):
        return FEMENINO, "titulo"
    if re.search(rf"\bseñor\s+{re.escape(norm)}\b", ctx):
        return MASCULINO, "titulo"
    if re.search(rf"\bseñora\s+{re.escape(norm)}\b", ctx):
        return FEMENINO, "titulo"
    # Artículos delante del nombre: "el X dijo" / "la X dijo".
    if re.search(rf"\bel\s+{re.escape(norm)}\b", ctx):
        return MASCULINO, "articulo"
    if re.search(rf"\bla\s+{re.escape(norm)}\b", ctx):
        return FEMENINO, "articulo"

    # Terminaciones (débil: solo si nada anterior decidió).
    if norm in MALE_ENDING_A:
        return MASCULINO, "terminacion"
    if norm in FEMALE_ENDING_O:
        return FEMENINO, "terminacion"
    if len(norm) >= 3 and norm.endswith("o"):
        return MASCULINO, "terminacion"
    if len(norm) >= 3 and norm.endswith("a"):
        return FEMENINO, "terminacion"

    return DESCONOCIDO, "desconocido"
