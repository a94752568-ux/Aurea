from dataclasses import dataclass


@dataclass
class Book:
    id: int
    title: str
    path: str
    fmt: str


@dataclass
class Chapter:
    id: int
    book_id: int
    idx: int
    title: str
    text: str
    # v0.7.0: ambiente sonoro del capítulo ('none' = sin ambiente).
    ambience: str = "none"
    bed_level: float = 0.25


@dataclass
class Segment:
    id: int
    chapter_id: int
    idx: int
    text: str
    wav_path: str | None
    status: str
    kind: str = "narration"
    character_id: int | None = None
    emotion: str | None = None
    delivery: str | None = None
    intensity: float | None = None
    pace: float | None = None
    pause_before_ms: int | None = None
    pause_after_ms: int | None = None
    directed: int = 0

    def display_narration(self):
        """Display narration information for the segment."""
        return {
            "idx": self.idx,
            "kind": self.kind,
            "character_id": self.character_id,
            "emotion": self.emotion,
            "delivery": self.delivery,
            "pace": self.pace,
            "pause_before_ms": self.pause_before_ms,
            "pause_after_ms": self.pause_after_ms,
            "directed": self.directed,
            "text_preview": self.text[:100] + "..." if len(self.text) > 100 else self.text,
            "has_audio": self.wav_path is not None and self.wav_path.strip() != ""
        }


@dataclass
class Character:
    id: int
    book_id: int
    name: str
    voice: str
    speed: float
    is_narrator: int
    engine: str = "kokoro"
    voice_profile_id: int | None = None
    # v0.7.0: género inferido ('masculino'/'femenino'/'desconocido');
    # gender_manual=1 si Alberto lo corrigió a mano (entonces el
    # auto-casting nunca lo vuelve a tocar).
    gender: str = "desconocido"
    gender_manual: int = 0
