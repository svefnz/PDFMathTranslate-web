"""CSV handler built on the standard library ``csv`` module."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .base import FileHandler, Slot, StringSlot, read_text_file, write_text_file


@dataclass
class _Table:
    rows: list[list[str]] = field(default_factory=list)
    delimiter: str = ","
    quotechar: str = '"'
    encoding: str = "utf-8"


class CsvHandler(FileHandler):
    exts = (".csv", ".tsv")
    label = "CSV 表格"

    def load(self, src: Path) -> _Table:
        text, encoding = read_text_file(src)
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            delimiter, quotechar = dialect.delimiter, dialect.quotechar or '"'
        except csv.Error:
            default = "\t" if src.suffix.lower() == ".tsv" else ","
            delimiter, quotechar = default, '"'
        rows = [list(row) for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, quotechar=quotechar)]
        return _Table(rows=rows, delimiter=delimiter, quotechar=quotechar, encoding=encoding)

    def slots(self, model: _Table) -> Iterator[Slot]:
        for row in model.rows:
            for index in range(len(row)):
                yield StringSlot(row, index)

    def save(self, model: _Table, dst: Path) -> None:
        buffer = io.StringIO(newline="")
        writer = csv.writer(
            buffer,
            delimiter=model.delimiter,
            quotechar=model.quotechar,
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        writer.writerows(model.rows)
        write_text_file(dst, buffer.getvalue(), model.encoding)
