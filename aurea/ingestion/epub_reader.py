import re
from html.parser import HTMLParser
from pathlib import Path

from .base import BookReader


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("style", "script") and self.skip:
            self.skip -= 1
        if tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


class EpubReader(BookReader):
    def read(self, path):
        import ebooklib
        from ebooklib import epub

        book = epub.read_epub(path)
        chapters = []
        for i, item in enumerate(book.get_items_of_type(ebooklib.ITEM_DOCUMENT)):
            parser = _TextExtractor()
            parser.feed(item.get_content().decode("utf-8", "replace"))
            text = re.sub(r"\n{3,}", "\n\n",
                          "\n".join(l.strip() for l in "".join(parser.parts).splitlines())).strip()
            if len(text) > 40:
                title = getattr(item, "title", None) or f"Sección {i + 1}"
                chapters.append((title[:80], text))
        return chapters
