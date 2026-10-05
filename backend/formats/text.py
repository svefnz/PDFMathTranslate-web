"""Plain text and Markdown handlers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from .base import FileHandler, LineSlot, Slot, read_text_file, write_text_file

# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------
class PlainTextHandler(FileHandler):
    exts = (".txt", ".text", ".log")
    label = "纯文本"

    def load(self, src: Path) -> tuple[list[str], str]:
        text, encoding = read_text_file(src)
        # Splitting on "\n" and rejoining with "\n" is an exact round trip; CR
        # characters stay inside each line and are stripped by LineSlot.
        return text.split("\n"), encoding

    def slots(self, model: tuple[list[str], str]) -> Iterator[Slot]:
        lines, _encoding = model
        for index in range(len(lines)):
            yield LineSlot(lines, index)

    def save(self, model: tuple[list[str], str], dst: Path) -> None:
        lines, encoding = model
        write_text_file(dst, "\n".join(lines), encoding)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
_REF_DEF_RE = re.compile(r"^\[[^\]\n]+\]:\s*\S")
#: inline spans that must survive byte-for-byte
_PROTECT_RULES: tuple[re.Pattern[str], ...] = (
    re.compile(r"`+[^`]*`+"),                                   # inline code
    re.compile(r"</?[A-Za-z][^>\n]*>"),                          # raw HTML tags
    re.compile(r"<[A-Za-z][^>\n]*>"),                            # autolinks <https://..>
    re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9-.]+"),  # e-mail
    re.compile(r"(?:https?|ftp)://[^\s<>)\]]+"),                 # bare URLs
    re.compile(r"\[\^[^\]\n]*\]"),                               # footnote refs
    re.compile(r"\[\d+\]"),                                      # citation refs
    re.compile(r"&[A-Za-z#0-9]+;"),                              # HTML entities
    re.compile(r"\\[\\`*_{}\[\]()#+\-.!|]"),                     # backslash escapes
)
#: (opening, url, closing) of a link/image destination
_LINK_DEST_RE = re.compile(r"(!?\[[^\]\n]*\]\()([^)\s]*)(\))")


def protect_markdown(line: str) -> tuple[str, list[str]]:
    """Replace untranslatable spans with ``{{n}}`` placeholders.

    Deterministic and pure, so it can be re-run on the original line to rebuild
    the same placeholder table during write-back.
    """
    store: list[str] = []

    def keep(match: re.Match[str]) -> str:
        store.append(match.group(0))
        return "{{%d}}" % (len(store) - 1)

    def keep_link(match: re.Match[str]) -> str:
        if not match.group(2):
            return match.group(0)
        store.append(match.group(2))
        return "%s{{%d}}%s" % (match.group(1), len(store) - 1, match.group(3))

    text = _LINK_DEST_RE.sub(keep_link, line)
    for rule in _PROTECT_RULES:
        text = rule.sub(keep, text)
    return text, store


def restore_markdown(text: str, table: list[str]) -> str | None:
    """Put protected spans back. ``None`` means a placeholder was lost."""
    if not table:
        return text
    for index in range(len(table)):
        if "{{%d}}" % index not in text:
            return None

    def put_back(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return table[index] if index < len(table) else match.group(0)

    return re.sub(r"\{\{(\d+)\}\}", put_back, text)


class MarkdownLineSlot(LineSlot):
    """A Markdown source line: placeholders in, placeholders out."""

    __slots__ = ("_hardbreak",)

    def __init__(self, lines: list[str], index: int) -> None:
        super().__init__(lines, index)
        # two trailing spaces are a hard line break in Markdown; keep them out
        # of the translator's input so they cannot be trimmed away
        stripped = self._text.rstrip(" ")
        self._hardbreak = self._text[len(stripped):]
        self._text = stripped

    def get(self) -> str | None:
        text = super().get()
        if text is None:
            return None
        protected, _table = protect_markdown(text)
        return protected

    def set(self, text: str) -> None:
        _protected, table = protect_markdown(self._text)
        restored = restore_markdown(text, table)
        if restored is None:
            # The model dropped or mangled a placeholder -> a corrupted line is
            # worse than an untranslated one, so keep the source line.
            restored = self._text
        super().set(restored + self._hardbreak)


class MarkdownHandler(FileHandler):
    exts = (".md", ".markdown")
    label = "Markdown"

    def load(self, src: Path) -> tuple[list[str], str]:
        text, encoding = read_text_file(src)
        return text.split("\n"), encoding

    def slots(self, model: tuple[list[str], str]) -> Iterator[Slot]:
        lines, _encoding = model
        fence: str | None = None
        frontmatter = bool(lines) and lines[0].strip() in ("---", "+++")
        for index, raw in enumerate(lines):
            stripped = raw.strip()
            if frontmatter:
                if index > 0 and stripped in ("---", "+++"):
                    frontmatter = False
                continue
            fence_match = _FENCE_RE.match(stripped)
            if fence_match:
                marker = fence_match.group(1)[0] * 3
                if fence is None:
                    fence = marker
                elif stripped.startswith(fence):
                    fence = None
                continue
            if fence is not None:
                continue
            if _REF_DEF_RE.match(stripped) or stripped.startswith("<!--"):
                continue
            yield MarkdownLineSlot(lines, index)

    def save(self, model: tuple[list[str], str], dst: Path) -> None:
        lines, encoding = model
        write_text_file(dst, "\n".join(lines), encoding)
