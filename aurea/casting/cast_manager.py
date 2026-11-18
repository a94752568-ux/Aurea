"""Gestión del reparto: perfiles vocales persistentes por libro."""
from ..core.database import Database
from ..voice import clone_store

NARRATOR = "Narrador"
DEFAULT_VOICES = ["ef_dora", "em_santa"]  # em_alex queda reservado al narrador


class CastManager:
    def __init__(self, db: Database):
        self.db = db

    def ensure_narrator(self, book_id):
        return self.db.add_character(book_id, NARRATOR, "em_alex", 1.0, is_narrator=1)

    def _available_clones(self):
        """Devuelve lista de perfiles de clonación disponibles (id, name, sample_path)."""
        return clone_store.list_profiles()

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
