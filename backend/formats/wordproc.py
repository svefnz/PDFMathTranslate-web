"""Word (.docx) handler built on python-docx.

Text is replaced strictly inside existing runs, so styles, numbering, tables,
images, section layout and hyperlink targets are preserved. Headers and footers
are covered too. Text boxes / SmartArt / embedded objects are not reachable
through the python-docx object model and are left as-is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from .base import FileHandler, ParagraphSlot, Slot


def _iter_paragraphs(container: Any) -> Iterator[Any]:
    """Paragraphs of a story container, in document order, tables included."""
    for paragraph in container.paragraphs:
        yield paragraph
    for table in container.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from _iter_paragraphs(cell)


def _iter_stories(document: Any) -> Iterator[Any]:
    yield from _iter_paragraphs(document)
    for section in document.sections:
        for part in (
            section.header,
            section.footer,
            section.first_page_header,
            section.first_page_footer,
            section.even_page_header,
            section.even_page_footer,
        ):
            if part is not None:
                yield from _iter_paragraphs(part)


class DocxHandler(FileHandler):
    exts = (".docx",)
    label = "Word 文档"

    def load(self, src: Path):
        from docx import Document

        return Document(str(src))

    def slots(self, document: Any) -> Iterator[Slot]:
        for paragraph in _iter_stories(document):
            yield ParagraphSlot(paragraph)

    def save(self, document: Any, dst: Path) -> None:
        document.save(str(dst))
