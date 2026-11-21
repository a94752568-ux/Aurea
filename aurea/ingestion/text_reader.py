import re
from pathlib import Path

from .base import BookReader, read_text_smart

HEADING = re.compile(
    r"^\s*(cap[íi]tulo|CAP[ÍI]TULO|parte|PARTE|prólogo|PRÓLOGO|epílogo|EPÍLOGO)\b.{0,60}$")


class TextReader(BookReader):
    def read(self, path):
        raw = read_text_smart(path)
        lines = raw.splitlines()
        heads = [i for i, l in enumerate(lines)
                 if len(l.strip()) < 80 and HEADING.match(l)]
        if len(heads) >= 2:
            chapters = []
            bounds = heads + [len(lines)]
            for a, b in zip(bounds, bounds[1:]):
                text = "\n".join(lines[a:b]).strip()
                if text:
                    chapters.append((lines[a].strip()[:80], text))
            if chapters:
                return chapters
        # Sin encabezados claros: partir por bloques de ~8000 caracteres
        paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
        chapters, buf, size = [], [], 0
        for p in paras:
            buf.append(p)
            size += len(p)
            if size > 8000:
                chapters.append((f"Parte {len(chapters) + 1}", "\n\n".join(buf)))
                buf, size = [], 0
        if buf:
            chapters.append((f"Parte {len(chapters) + 1}", "\n\n".join(buf)))
        return chapters
