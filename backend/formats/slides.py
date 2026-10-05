"""PowerPoint (.pptx) handler built on python-pptx.

Covers slide shapes, tables inside slides, grouped shapes and speaker notes, so
styling and layout survive. Slide layouts and masters are deliberately skipped:
the slide itself already carries the rendered text, and translating both would
duplicate the work. Chart data labels and SmartArt are not exposed by
python-pptx and are left untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from .base import FileHandler, ParagraphSlot, Slot


def _iter_text_frames(shapes: Any) -> Iterator[Any]:
    from pptx.shapes.group import GroupShape

    for shape in shapes:
        if isinstance(shape, GroupShape):
            yield from _iter_text_frames(shape.shapes)
            continue
        if getattr(shape, "has_text_frame", False):
            yield shape.text_frame
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                for cell in row.cells:
                    yield cell.text_frame


def _iter_paragraphs(presentation: Any) -> Iterator[Any]:
    for slide in presentation.slides:
        for frame in _iter_text_frames(slide.shapes):
            yield from frame.paragraphs
        # has_notes_slide guards against creating an empty notes part on save
        if getattr(slide, "has_notes_slide", False):
            notes_frame = slide.notes_slide.notes_text_frame
            if notes_frame is not None:
                yield from notes_frame.paragraphs


class PptxHandler(FileHandler):
    exts = (".pptx",)
    label = "PowerPoint 演示文稿"

    def load(self, src: Path):
        from pptx import Presentation

        return Presentation(str(src))

    def slots(self, presentation: Any) -> Iterator[Slot]:
        for paragraph in _iter_paragraphs(presentation):
            yield ParagraphSlot(paragraph)

    def save(self, presentation: Any, dst: Path) -> None:
        presentation.save(str(dst))
