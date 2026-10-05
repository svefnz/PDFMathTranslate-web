"""Format registry for the non-PDF translation pipeline.

``PDF`` is handled by BabelDOC through :mod:`backend.adapter` (layout aware);
everything registered here goes through the generic segment pipeline in
:mod:`backend.formats.base`.
"""

from __future__ import annotations

from pathlib import Path

from .base import (
    FileHandler,
    SegmentTranslator,
    TranslationOutcome,
    run_pipeline,
)
from .csvfmt import CsvHandler
from .slides import PptxHandler
from .spreadsheet import XlsxHandler
from .text import MarkdownHandler, PlainTextHandler
from .wordproc import DocxHandler

__all__ = [
    "FileHandler",
    "SegmentTranslator",
    "TranslationOutcome",
    "run_pipeline",
    "HANDLERS",
    "HANDLER_BY_EXT",
    "SUPPORTED_EXTENSIONS",
    "ACCEPT_ATTR",
    "LEGACY_OFFICE_EXTS",
    "get_handler",
    "is_pdf",
    "describe_formats",
]

HANDLERS: tuple[FileHandler, ...] = (
    PlainTextHandler(),
    MarkdownHandler(),
    CsvHandler(),
    XlsxHandler(),
    DocxHandler(),
    PptxHandler(),
)

HANDLER_BY_EXT: dict[str, FileHandler] = {
    ext: handler for handler in HANDLERS for ext in handler.exts
}

#: PDF first so the UI lists the layout-preserving path prominently
SUPPORTED_EXTENSIONS: tuple[str, ...] = (".pdf",) + tuple(sorted(HANDLER_BY_EXT))

ACCEPT_ATTR = ",".join(SUPPORTED_EXTENSIONS)

#: Old binary Office formats are not readable by the python libraries we use.
LEGACY_OFFICE_EXTS: dict[str, str] = {
    ".doc": ".docx",
    ".ppt": ".pptx",
    ".xls": ".xlsx",
}

_PDF_LABEL = "PDF（版式保留 / 双语对照）"


def describe_formats() -> dict[str, object]:
    """Payload for ``GET /api/formats`` so the frontend stays in sync."""
    formats = [
        {
            "ext": ".pdf",
            "label": _PDF_LABEL,
            "kind": "pdf",
            "layout_aware": True,
        }
    ]
    for handler in HANDLERS:
        for ext in handler.exts:
            formats.append(
                {
                    "ext": ext,
                    "label": handler.label,
                    "kind": handler.kind,
                    "layout_aware": False,
                }
            )
    return {
        "accept": ACCEPT_ATTR,
        "extensions": list(SUPPORTED_EXTENSIONS),
        "legacy_hints": LEGACY_OFFICE_EXTS,
        "formats": formats,
    }


def get_handler(path: Path | str) -> FileHandler | None:
    name = path.name if isinstance(path, Path) else str(path)
    return HANDLER_BY_EXT.get(Path(name).suffix.lower())


def is_pdf(path: Path | str) -> bool:
    name = path.name if isinstance(path, Path) else str(path)
    return Path(name).suffix.lower() == ".pdf"
