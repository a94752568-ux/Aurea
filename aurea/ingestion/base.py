from abc import ABC, abstractmethod
from pathlib import Path


def read_text_smart(path):
    """Lee un texto: UTF-8 estricto primero; si falla, latin-1 (M12).
    Evita el mojibake silencioso de errors="replace"."""
    data = Path(path).read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


class BookReader(ABC):
    """Contrato: todo lector devuelve [(título_capítulo, texto), ...]."""

    @abstractmethod
    def read(self, path: str) -> list[tuple[str, str]]:
        ...
