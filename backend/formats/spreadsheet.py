"""Excel (.xlsx) handler built on openpyxl.

Deliberate limits (openpyxl cannot reach them, so they are passed through
untouched): charts, pivot tables, and text inside drawing objects.

Sheet *names* are intentionally never translated: they are addressable from
formulas (``=SUM(Sheet1!A1)``), and renaming them turns working workbooks into
``#REF!`` errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .base import FileHandler, Slot


class _CellSlot:
    __slots__ = ("_cell",)

    def __init__(self, cell) -> None:
        self._cell = cell

    def get(self) -> str | None:
        value = self._cell.value
        if not isinstance(value, str) or not value.strip():
            return None
        # A formula is a string too. Rewriting it corrupts the workbook, and
        # translating just its string literals is not worth the risk.
        if value.startswith("="):
            return None
        return value

    def set(self, text: str) -> None:
        self._cell.value = text


class XlsxHandler(FileHandler):
    exts = (".xlsx",)
    label = "Excel 表格"

    def load(self, src: Path):
        import openpyxl

        return openpyxl.load_workbook(src, data_only=False, keep_links=True)

    def slots(self, workbook) -> Iterator[Slot]:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str):
                        yield _CellSlot(cell)

    def save(self, workbook, dst: Path) -> None:
        workbook.save(dst)
