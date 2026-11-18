from abc import ABC, abstractmethod


class BookReader(ABC):
    """Contrato: todo lector devuelve [(título_capítulo, texto), ...]."""

    @abstractmethod
    def read(self, path: str) -> list[tuple[str, str]]:
        ...
