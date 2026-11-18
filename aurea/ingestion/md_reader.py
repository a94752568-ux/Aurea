"""Lector de archivos Markdown (.md) para AUREA.

Convierte Markdown a capítulos de audiolibro. Principio base: el TTS
nunca lee marcas de formato. Todo lo que es formato se elimina o se
convierte en estructura (capítulos, pausas).
"""
import re
from pathlib import Path

from .base import BookReader

# Encabezados
H1 = re.compile(r"^#{1}\s+(.+)$")
H2 = re.compile(r"^#{2}\s+(.+)$")
H3PLUS = re.compile(r"^#{3,}\s+(.+)$")

# Reglas horizontales
HR = re.compile(r"^(\*\*\*|---|___)\s*$")

# Bloques de código cercados
FENCE_START = re.compile(r"^```")
FENCE_END = re.compile(r"^```")

# Enlaces [texto](url)
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Imágenes ![alt](url)
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
# Código en línea `codigo`
INLINE_CODE_RE = re.compile(r"`([^`]*)`")
# Negrita **texto** o __texto__
BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
# Cursiva *texto* o _texto_ (no dentro de palabras)
ITALIC_RE = re.compile(r"(?<!\w)(\*|_)(.+?)\1(?!\w)")
# Referencia a nota al pie [^1]
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\]")
# Definición de nota al pie al final: [^1]: texto
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]]+)\]:\s*.+$", re.MULTILINE)
# Etiquetas HTML
HTML_TAG_RE = re.compile(r"<[^>]+>")
# Marcadores de lista al inicio de línea
LIST_MARKER_RE = re.compile(r"^\s*([-*+]|\d+\.)\s+")
# Línea en blanco múltiple
MULTI_BLANK_RE = re.compile(r"\n{3,}")
PAUSE_MARKER_RE = re.compile(r"\[(pausa|silencio)=([0-9]+(?:\.[0-9]+)?)s\]")


class MarkdownReader(BookReader):
    """Lee un .md y devuelve [(titulo_capitulo, texto), ...]."""

    def read(self, path: str):
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()

        # ---- Pre-procesamiento: marcar ítems de lista que son el último
        # de su grupo para no añadirles pausa final. Los demás ítems de
        # lista reciben [pausa=0.18s] (pausa de coma) tras el texto.
        last_list_item = set()
        i = 0
        while i < len(lines):
            if LIST_MARKER_RE.match(lines[i]):
                # Inicio de un grupo de ítems de lista
                j = i
                while j < len(lines) and LIST_MARKER_RE.match(lines[j]):
                    j += 1
                # El último ítem del grupo es j-1
                last_list_item.add(j - 1)
                i = j
            else:
                i += 1

        chapters = []          # [(titulo, texto_acumulado), ...]
        current_title = None
        current_parts = []     # fragmentos de texto del capítulo actual

        i = 0
        while i < len(lines):
            line = lines[i]

            # Detectar bloque de código cercado
            if FENCE_START.match(line):
                # Buscar el cierre
                j = i + 1
                while j < len(lines) and not FENCE_END.match(lines[j]):
                    j += 1
                i = j + 1  # Saltar todo el bloque
                continue

            # H1 → nuevo capítulo
            m = H1.match(line)
            if m:
                if current_title is not None:
                    chapters.append((current_title, "\n".join(current_parts).strip()))
                current_title = m.group(1).strip()
                current_parts = []
                # Anuncio del título: segmento de narración con voz narradora
                # (sin character_id → usa voz narradora por defecto), seguido
                # de pausa de 800 ms (Espec 2.1). El marcador se concatena al
                # título en el mismo párrafo para que segmentize lo procese.
                current_parts.append(current_title + "[pausa=0.8s]")
                i += 1
                continue

            # H2 → corte de sección (800 ms en segmento anterior)
            m = H2.match(line)
            if m:
                if current_parts and not current_parts[-1].startswith(current_title):
                    current_parts[-1] = current_parts[-1] + "[pausa=0.8s]"
                i += 1
                continue

            # H3 o más → subsección (500 ms en segmento anterior)
            m = H3PLUS.match(line)
            if m:
                if current_parts and not current_parts[-1].startswith(current_title):
                    current_parts[-1] = current_parts[-1] + "[pausa=0.5s]"
                i += 1
                continue

            # Regla horizontal → corte de sección (800 ms)
            if HR.match(line):
                if current_parts and not current_parts[-1].startswith(current_title):
                    current_parts[-1] = current_parts[-1] + "[pausa=0.8s]"
                i += 1
                continue

            # Línea de contenido: limpiar formato Markdown
            cleaned = self._clean_line(line)
            if cleaned:
                # Si es un ítem de lista y ya había contenido previo,
                # añadir salto de párrafo para que cada ítem sea segmento separado
                if current_parts and LIST_MARKER_RE.match(line):
                    current_parts.append("")
                # Añadir pausa de coma (180 ms) tras cada ítem salvo el último
                if LIST_MARKER_RE.match(line) and i not in last_list_item:
                    cleaned = cleaned + "[pausa=0.18s]"
                current_parts.append(cleaned)

            i += 1

        # Guardar último capítulo
        if current_title is not None:
            chapters.append((current_title, "\n".join(current_parts).strip()))
        elif current_parts:
            # .md sin H1: un solo capítulo con nombre del archivo
            chapters.append((Path(path).stem, "\n".join(current_parts).strip()))

        return chapters if chapters else [(Path(path).stem, "")]

    def _clean_line(self, line: str) -> str:
        """Elimina formato Markdown de una línea individual, manteniendo el texto."""
        t = line

        # Eliminar imágenes completamente
        t = IMAGE_RE.sub("", t)
        # Enlaces: conservar texto, quitar URL
        t = LINK_RE.sub(r"\1", t)
        # Código en línea: quitar comillas invertidas
        t = INLINE_CODE_RE.sub(r"\1", t)
        # Negrita
        t = BOLD_RE.sub(r"\2", t)
        # Cursiva
        t = ITALIC_RE.sub(r"\2", t)
        # Definiciones de notas al pie (líneas completas) - ANTES que referencias
        t = FOOTNOTE_DEF_RE.sub("", t)
        # Referencias a notas al pie en el texto
        t = FOOTNOTE_REF_RE.sub("", t)
        # Etiquetas HTML
        t = HTML_TAG_RE.sub("", t)
        # Marcador de lista al inicio
        t = LIST_MARKER_RE.sub("", t)
        # Colapsar espacios
        t = re.sub(r"[ \t]+", " ", t).strip()

        return t


def read_md(path: str):
    """Función de conveniencia para leer un .md."""
    return MarkdownReader().read(path)
