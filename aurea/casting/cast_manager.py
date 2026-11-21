"""Gestión del reparto: perfiles vocales persistentes por libro.

v0.7.0 — Reparto inteligente por género (decisión de Alberto 2026-11-20):
las voces Kokoro `em_*` son masculinas y `ef_*` femeninas. El
auto-casting infiere el género de cada personaje (léxico, títulos,
artículos, terminaciones; ver `casting/gender.py`) y asigna:

  masculino  -> em_santa (em_alex es del narrador)
  femenino   -> ef_dora
  desconocido -> rotación de las voces por defecto (nunca se inventa
                 el género; la UI marca el personaje para confirmar)

Reglas que no se rompen:
  - Nunca `ef_*` a un masculino inferido ni `em_*` a una femenina.
  - Las asignaciones existentes NO se reasignan solas. Si un
    personaje ya existe sin género, se le infiere y guarda el género,
    pero su voz queda intacta.
  - Corregir el género a mano (`set_gender`) conserva la voz.
  - Varias voces del mismo género se diferencian con velocidad
    (1.0 / 0.95 / 1.05 en rotación): solo hay una voz Kokoro en
    español por género además del narrador.
"""
from pathlib import Path

from ..core.database import Database
from ..voice import clone_store
from .gender import (
    DESCONOCIDO,
    FEMENINO,
    MASCULINO,
    infer_gender,
    voice_gender,
)

NARRATOR = "Narrador"
DEFAULT_VOICES = ["ef_dora", "em_santa"]  # em_alex queda reservado al narrador

# Voces Kokoro en español por género (kokoro_adapter.VOICES_ES).
MALE_VOICES = ["em_santa"]
FEMALE_VOICES = ["ef_dora"]
# Variación de velocidad al repetir la misma voz en un género.
SPEED_ROTATION = [1.0, 0.95, 1.05]


def pick_voice(gender, used):
    """Elige (voz, velocidad) para un género dado.

    `used`: dict voz -> nº de personajes que ya la usan en este libro.
    Masculino -> em_*; femenino -> ef_*; desconocido -> la menos usada
    de las dos por defecto. La velocidad rota al reutilizar una voz.
    """
    if gender == MASCULINO:
        pool = MALE_VOICES
    elif gender == FEMENINO:
        pool = FEMALE_VOICES
    else:
        pool = DEFAULT_VOICES
    voice = min(pool, key=lambda v: used.get(v, 0))
    speed = SPEED_ROTATION[used.get(voice, 0) % len(SPEED_ROTATION)]
    return voice, speed


class CastManager:
    def __init__(self, db: Database):
        self.db = db

    def ensure_narrator(self, book_id):
        return self.db.add_character(
            book_id, NARRATOR, "em_alex", 1.0, is_narrator=1,
            gender=MASCULINO, gender_manual=1)

    def _available_clones(self):
        """Devuelve lista de perfiles de clonación disponibles (id, name, sample_path)."""
        return clone_store.list_profiles()

    def set_gender(self, character_id, gender):
        """Corrección manual de género: guarda el género y marca
        gender_manual=1. La voz asignada NO se toca."""
        if gender not in (MASCULINO, FEMENINO, DESCONOCIDO):
            raise ValueError(f"Género no válido: {gender!r}")
        self.db.update_gender(character_id, gender)

    def sanitize_cast(self, book_id, valid_kokoro_voices):
        """Reconcilia el reparto con la realidad de los motores (v0.6 Electron).

        Restaurado en v0.7.3: el método vivía solo en la rama Electron y
        el tarball v0.7.2 lo borró al sobrescribir este archivo (E-C1).
        Los llamadores son `core/engine.py:186`, `core/scratch.py:43` y
        `POST /api/books/{id}/cast-sanitize`: todos esperan un entero
        con el nº de personajes reconciliados.

        Reglas (las del docstring de `engine.py:7-9`):
          - Perfil clonado XTTS cuyo NOMBRE está en `voice` de un
            personaje (p. ej. "em_origins") -> engine='xtts' con su
            profile_id (solo si la muestra existe en disco).
          - Voz Kokoro inexistente (no está en `valid_kokoro_voices`)
            -> voz del narrador.
          - Personaje XTTS con perfil borrado o muestra perdida ->
            voz del narrador.
        Solo toca personajes con voz inválida; nunca inventa voces ni
        reasigna las válidas. Idempotente: una segunda llamada devuelve 0.
        Si `valid_kokoro_voices` viene vacío no se toca nada (no se puede
        distinguir una voz inválida de un conjunto vacío).
        """
        valid = set(valid_kokoro_voices or [])
        if not valid:
            return 0
        chars = self.db.characters(book_id)
        narrator = next((c for c in chars if c.is_narrator), None)
        narr_voice = (narrator.voice if narrator and narrator.voice in valid
                      else "em_alex")
        profiles = {p["name"]: p for p in self._available_clones()}
        fixed = 0
        for c in chars:
            engine = c.engine or "kokoro"
            if engine == "xtts":
                prof = None
                if c.voice_profile_id:
                    prof = clone_store.get_profile(c.voice_profile_id)
                if prof is None and c.voice in profiles:
                    prof = profiles[c.voice]
                sample = (prof or {}).get("sample_path")
                if prof is not None and sample and Path(sample).exists():
                    if c.voice_profile_id != prof["id"]:
                        self.db.update_character(
                            c.id, c.voice, c.speed,
                            engine="xtts", profile_id=prof["id"])
                        fixed += 1
                    continue
                # Perfil borrado o muestra perdida -> voz del narrador.
                self.db.update_character(c.id, narr_voice, c.speed,
                                         engine="kokoro", profile_id=None)
                fixed += 1
                continue
            # Motor Kokoro (o sin motor): ¿la voz es un perfil clonado
            # guardado por nombre?
            if c.voice in profiles:
                prof = profiles[c.voice]
                sample = prof.get("sample_path")
                if sample and Path(sample).exists():
                    self.db.update_character(c.id, c.voice, c.speed,
                                             engine="xtts",
                                             profile_id=prof["id"])
                    fixed += 1
                    continue
            # Voz Kokoro inexistente -> voz del narrador.
            if c.voice not in valid:
                self.db.update_character(c.id, narr_voice, c.speed,
                                         engine="kokoro", profile_id=None)
                fixed += 1
        return fixed

    def build_cast(self, book_id, names, texts=None):
        """Crea el reparto detectado; los existentes no se reasignan.

        `texts`: textos del libro (opcional) para inferir el género
        por contexto (títulos, artículos). Asigna voces Kokoro por
        género; los personajes existentes sin género lo reciben
        inferido, sin tocar su voz. Las voces clonadas XTTS se asignan
        desde la GUI (panel de clonación), no automáticamente aquí.
        """
        self.ensure_narrator(book_id)
        chars = {c.name: c for c in self.db.characters(book_id)}
        context = "\n".join(texts) if texts else ""
        used = {}
        for c in chars.values():
            if c.engine in (None, "", "kokoro"):
                used[c.voice] = used.get(c.voice, 0) + 1
        created = 0
        seen = set(chars)  # nombres ya procesados (incluye duplicados)

        for name in names:
            if name in seen:
                ch = chars.get(name)
                # Personaje viejo sin género: inferir y guardar (la voz
                # no se toca). Con género manual, ni se mira.
                if (ch is not None and not ch.gender_manual
                        and (not ch.gender or ch.gender == DESCONOCIDO)):
                    gender, _src = infer_gender(name, context)
                    if gender != DESCONOCIDO:
                        self.db.set_character_gender(ch.id, gender, manual=0)
                continue
            gender, _src = infer_gender(name, context)
            voice, speed = pick_voice(gender, used)
            used[voice] = used.get(voice, 0) + 1
            self.db.add_character(book_id, name, voice, speed,
                                  gender=gender, gender_manual=0)
            seen.add(name)
            created += 1
        return created

    def assign(self, character_id, voice, speed, engine=None, profile_id=None):
        self.db.update_character(character_id, voice, speed, engine, profile_id)

    def character_id(self, book_id, name):
        """Devuelve el id del personaje; lo crea con voz por género si falta."""
        chars = {c.name.lower(): c for c in self.db.characters(book_id)}
        name_lower = name.lower()
        if name_lower in chars:
            return chars[name_lower].id
        gender, _src = infer_gender(name)
        used = {}
        for c in chars.values():
            if (c.engine in (None, "", "kokoro")):
                used[c.voice] = used.get(c.voice, 0) + 1
        voice, speed = pick_voice(gender, used)
        return self.db.add_character(
            book_id, name, voice, speed, gender=gender, gender_manual=0)

    def voice_map(self, book_id):
        """{character_id: (engine, voz, velocidad)} con respaldo del narrador.

        engine: 'kokoro' o 'xtts'. Si engine='xtts', la voz es el nombre del perfil
        y el sample_path se resuelve vía clone_store.clone_sample_map().
        """
        chars = self.db.characters(book_id)
        narrator = next((c for c in chars if c.is_narrator), None)
        fallback = ("kokoro", narrator.voice, narrator.speed) if narrator \
            else ("kokoro", "em_alex", 1.0)
        result = {}
        for c in chars:
            engine = c.engine if hasattr(c, 'engine') and c.engine else "kokoro"
            result[c.id] = (engine, c.voice, c.speed)
        return result, fallback

    def cast_info(self, book_id):
        """Info de reparto para la UI: por personaje, género inferido y
        género de la voz asignada (para detectar desajustes)."""
        info = []
        for c in self.db.characters(book_id):
            engine = c.engine if hasattr(c, 'engine') and c.engine else "kokoro"
            vg = voice_gender(c.voice) if engine == "kokoro" else DESCONOCIDO
            info.append({
                "id": c.id,
                "name": c.name,
                "voice": c.voice,
                "speed": c.speed,
                "is_narrator": bool(c.is_narrator),
                "engine": engine,
                "gender": c.gender or DESCONOCIDO,
                "gender_manual": bool(c.gender_manual),
                "voice_gender": vg,
                # Desajuste: género conocido y voz Kokoro del otro género.
                "mismatch": (
                    c.gender in (MASCULINO, FEMENINO)
                    and vg in (MASCULINO, FEMENINO)
                    and vg != c.gender),
            })
        return info
