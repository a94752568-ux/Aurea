"""Gestión del reparto: perfiles vocales persistentes por libro.

v0.6: sanitize_cast() reconcilia las voces del reparto con la realidad de
los motores, automáticamente y sin intervención (idempotente):
  - voice == nombre exacto de un perfil clonado con muestra existente
    → el personaje pasa a XTTS con ese perfil.
  - voz Kokoro que no existe en el bin → voz del narrador (o em_alex).
"""
import os

from ..core.database import Database
from ..voice import clone_store

NARRATOR = "Narrador"
DEFAULT_VOICES = ["ef_dora", "em_santa"]  # em_alex queda reservado al narrador
FALLBACK_VOICE = "em_alex"


class CastManager:
    def __init__(self, db: Database):
        self.db = db

    def ensure_narrator(self, book_id):
        return self.db.add_character(book_id, NARRATOR, "em_alex", 1.0, is_narrator=1)

    def _available_clones(self):
        """Devuelve lista de perfiles de clonación disponibles (id, name, sample_path)."""
        return clone_store.list_profiles()

    def sanitize_cast(self, book_id, valid_kokoro):
        """Reconcilia las voces del reparto con la realidad de los motores.

        Reglas (idempotente):
          1. engine='kokoro' (o ambiguo) con voice == nombre exacto de un
             perfil clonado cuya muestra EXISTE en disco → pasa a XTTS.
          2. Voz que no es ni voz Kokoro válida ni perfil clonado usable
             → voz del narrador (o em_alex si hasta el narrador está mal).
        Devuelve el número de personajes corregidos.
        """
        usable = {}
        for p in clone_store.list_profiles():
            if p.get("sample_path") and os.path.exists(p["sample_path"]):
                usable[p["name"]] = p["id"]
        chars = self.db.characters(book_id)
        narrator = next((c for c in chars if c.is_narrator), None)
        safe_voice = (narrator.voice if narrator and narrator.voice in valid_kokoro
                      else FALLBACK_VOICE)
        changed = 0
        for c in chars:
            engine = c.engine if getattr(c, "engine", None) else "kokoro"
            if engine == "xtts" and getattr(c, "voice_profile_id", None):
                continue  # ya es clon asignado; nada que hacer
            if c.voice in usable:
                self.db.update_character(
                    c.id, c.voice, c.speed,
                    engine="xtts", profile_id=usable[c.voice])
                changed += 1
            elif engine == "kokoro" and c.voice not in valid_kokoro:
                self.db.update_character(
                    c.id, safe_voice, c.speed,
                    engine="kokoro", profile_id=None)
                changed += 1
        return changed

    def build_cast(self, book_id, names):
        """Crea el reparto detectado; los existentes no se tocan.

        Asigna voces de Kokoro por defecto (ef_dora, em_santa rotando).
        Las voces clonadas XTTS se asignan desde la GUI (panel de clonación),
        no automáticamente aquí.
        """
        self.ensure_narrator(book_id)
        existing = {c.name for c in self.db.characters(book_id)}
        created = 0

        for name in names:
            if name not in existing:
                voice = DEFAULT_VOICES[len(existing) % 2]
                self.db.add_character(book_id, name, voice, 1.0)
                existing.add(name)
                created += 1
        return created

    def assign(self, character_id, voice, speed, engine=None, profile_id=None):
        self.db.update_character(character_id, voice, speed, engine, profile_id)

    def character_id(self, book_id, name):
        """Devuelve el id del personaje; lo crea con voz por defecto si falta."""
        chars = {c.name.lower(): c for c in self.db.characters(book_id)}
        name_lower = name.lower()
        if name_lower in chars:
            return chars[name_lower].id
        return self.db.add_character(
            book_id, name, DEFAULT_VOICES[len(chars) % 2], 1.0)

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
