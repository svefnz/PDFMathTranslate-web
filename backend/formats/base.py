"""Generic segment-based translation pipeline for non-PDF documents.

Why this exists
---------------
BabelDOC (behind ``pdf2zh_next``) only understands PDF. Every other format is,
as far as AI translation is concerned, just "a list of text strings embedded in
a container file". So instead of one pipeline per format we reduce every format
to a single abstraction:

* a **slot** is one writable position holding text (a line, a CSV cell, a
  spreadsheet cell, a Word paragraph, a PowerPoint paragraph);
* ``slot.get()`` returns the text to translate, or ``None`` when no translation
  is needed (pure numbers, formulas, empty lines);
* ``slot.set(text)`` writes the translated text back **into the original
  container**, so styling, hyperlinks and layout survive untouched.

The driver then does: collect slots -> dedup -> batch -> translate concurrently
-> scatter results back. Adding a format means implementing three methods
(``load`` / ``slots`` / ``save``) and nothing else.

The LLM client itself is *not* reimplemented here: it is reused from
``pdf2zh_next.translator``, which already provides 17 engines, on-disk caching,
rate limiting and chain-of-thought stripping.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol, Sequence

logger = logging.getLogger("pdf2zh-web.formats")

# --- tuning -----------------------------------------------------------------
MAX_BATCH_CHARS = 2400  # per LLM request, keeps prompts small and cheap
MAX_BATCH_ITEMS = 40
MAX_CONCURRENCY = 4
MAX_SPLIT_DEPTH = 3  # batch -> halves -> ... -> per-item fallback

# A "letter" in any script (excludes digits, punctuation, whitespace). Used to
# decide whether a slot is worth spending tokens on.
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def needs_translation(text: str | None) -> bool:
    """True when a string contains letters worth sending to a translator."""
    return bool(text) and bool(_LETTER_RE.search(text))


# --- slots ------------------------------------------------------------------
class Slot(Protocol):
    def get(self) -> str | None: ...
    def set(self, text: str) -> None: ...


class BatchTranslationError(RuntimeError):
    """The translation engine itself failed (network / auth / quota / rate limit).

    Deliberately distinct from "the model replied with something unusable":
    that case is worth retrying in smaller pieces, a dead engine is not.
    """


def describe_engine_error(exc: BaseException) -> str:
    """Turn an opaque SDK exception into something a user can act on."""
    name = type(exc).__name__
    text = str(exc)
    lowered = text.lower()
    if "429" in text or "ratelimit" in name.lower() or "rate limit" in lowered or "quota" in lowered:
        hint = "（接口限流或额度不足：请检查 API Key 的余额/速率限制，或换用其他引擎）"
    elif "401" in text or "authentication" in name.lower() or "invalid_api_key" in lowered:
        hint = "（API Key 无效或已过期）"
    elif "403" in text or "permission" in lowered:
        hint = "（API Key 无该模型权限）"
    elif "timeout" in lowered or "timeout" in name.lower():
        hint = "（接口超时：可调低并发或稍后重试）"
    elif "connection" in lowered or "connect" in name.lower():
        hint = "（无法连接接口：请检查网络或 Base URL）"
    else:
        hint = ""
    return f"大模型接口调用失败：{name}: {text[:300]}{hint}"


class StringSlot:
    """Slot over one element of a mutable string sequence (list of lines/rows)."""
    __slots__ = ("_owner", "_index")

    def __init__(self, owner: list, index: int) -> None:
        self._owner = owner
        self._index = index

    def get(self) -> str | None:
        value = self._owner[self._index]
        if not isinstance(value, str) or not value.strip():
            return None
        return value

    def set(self, text: str) -> None:
        self._owner[self._index] = text


class LineSlot:
    """Slot over one line, preserving the original CR/LF terminator.

    A translator will happily drop or duplicate newlines, so the terminator is
    never sent out; it is re-attached verbatim on write-back.
    """
    __slots__ = ("_lines", "_index", "_term", "_text")

    def __init__(self, lines: list[str], index: int) -> None:
        self._lines = lines
        self._index = index
        raw = lines[index]
        self._term = raw[len(raw.rstrip("\r\n")):]
        self._text = raw[: len(raw) - len(self._term)] if self._term else raw

    @property
    def text(self) -> str:
        return self._text

    def get(self) -> str | None:
        if not self._text.strip():
            return None
        return self._text

    def set(self, text: str) -> None:
        self._lines[self._index] = text + self._term


class FileHandler:
    """Base class: load -> slots -> save. Subclasses implement the three."""

    exts: tuple[str, ...] = ()
    label: str = ""
    #: currently only "pdf" gets layout analysis; everything else is segment based
    kind: str = "segments"

    def load(self, src: Path) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def slots(self, model: Any) -> Iterator[Slot]:  # pragma: no cover - abstract
        raise NotImplementedError

    def save(self, model: Any, dst: Path) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def collect(self, src: Path) -> list[tuple[Slot, str | None]]:
        """Return ``(slot, original_text_or_None)`` in canonical write order."""
        model = self.load(src)
        return [(slot, slot.get()) for slot in self.slots(model)]

    def write_back(self, src: Path, dst: Path, values: Sequence[str | None]) -> None:
        """Re-load ``src``, apply translated values, save as ``dst``.

        Alignment is positional: ``values`` was produced against the same slot
        generator, so the order is identical by construction.
        """
        model = self.load(src)
        for slot, value in zip(self.slots(model), values):
            if value is not None:
                slot.set(value)
        self.save(model, dst)


# --- run-level write-back (docx / pptx) -------------------------------------
def leaf_runs(paragraph: Any) -> list[Any]:
    """All ``Run`` objects of a paragraph, including runs inside hyperlinks.

    ``paragraph.runs`` silently *excludes* hyperlink runs while
    ``paragraph.text`` *includes* them, so writing back through ``runs`` alone
    would delete link text. ``iter_inner_content`` (python-docx >= 1.1) fixes
    that; python-pptx has no such API and a plain ``runs`` list is already
    complete.
    """
    iterate = getattr(paragraph, "iter_inner_content", None)
    if iterate is None:
        return list(paragraph.runs)
    runs: list[Any] = []
    for child in iterate():
        # Hyperlink exposes .runs; Run does not. That is the discriminator.
        nested = getattr(child, "runs", None)
        if nested is None and not hasattr(child, "text"):
            continue
        if nested is None:
            runs.append(child)
        else:
            runs.extend(nested)
    return runs


def apply_paragraph_text(paragraph: Any, text: str) -> None:
    """Write ``text`` back into a paragraph without destroying its styling.

    Word and PowerPoint split a paragraph into runs at arbitrary points (editing
    a spelling mistake often splits a sentence in two), so the paragraph is
    translated as one unit and the result is redistributed proportionally to the
    original run lengths. That keeps bold/italic/colour spans roughly aligned
    with their text instead of collapsing the whole paragraph to one style.
    """
    runs = leaf_runs(paragraph)
    if not runs:
        paragraph.text = text
        return

    filled = [i for i, run in enumerate(runs) if run.text]
    if not filled:
        runs[0].text = text
        for i, run in enumerate(runs):
            if i:
                run.text = ""
        return

    if len(filled) == 1:
        runs[filled[0]].text = text
    else:
        total = sum(len(runs[i].text) for i in filled)
        pos = 0
        for order, index in enumerate(filled):
            if order == len(filled) - 1:
                runs[index].text = text[pos:]
                continue
            share = len(runs[index].text) / total
            take = max(0, min(int(round(len(text) * share)), len(text) - pos))
            runs[index].text = text[pos : pos + take]
            pos += take

    keep = set(filled)
    for i, run in enumerate(runs):
        if i not in keep:
            run.text = ""


class ParagraphSlot:
    """Slot over a docx/pptx paragraph: whole-paragraph text in, runs out."""

    __slots__ = ("_paragraph",)

    def __init__(self, paragraph: Any) -> None:
        self._paragraph = paragraph

    def get(self) -> str | None:
        text = self._paragraph.text
        if not text or not text.strip():
            return None
        return text

    def set(self, text: str) -> None:
        apply_paragraph_text(self._paragraph, text)


# --- translator wrapper -----------------------------------------------------
_BATCH_TEMPLATE = """\
You are a professional translation engine. Translate every string in the JSON array below into __LANG__.

Rules:
- Output ONLY a JSON array of strings, same length, same order. No code fences, no commentary, no explanation.
- Preserve placeholders such as {{0}}, {{1}} exactly as written, in their original position.
- Preserve markup and its structure: #, *, -, >, |, `inline code`, [text](url), list numbers.
- Keep leading and trailing whitespace of every string unchanged.
- Reproduce unchanged anything that should not be translated (code, numbers, URLs, proper nouns).
__EXTRA__Input JSON array:
__PAYLOAD__"""


class SegmentTranslator:
    """Batches text segments into LLM requests, reusing pdf2zh_next's engines.

    Two strategies:

    * **LLM engines** (OpenAI/DeepSeek/Ollama/...): many segments per request via
      a JSON array, which is roughly an order of magnitude cheaper and faster
      than one request per segment, and gives each string its neighbours as
      context.
    * **Non-LLM engines** (Google/Bing/DeepL/...): those have no raw-prompt entry
      point, so we fall back to their per-segment ``translate()``.
    """

    def __init__(
        self,
        translator: Any,
        lang_out: str,
        llm_capable: bool,
        glossary: str = "",
        batch_timeout: float | None = None,
    ) -> None:
        self._translator = translator
        self._lang_out = lang_out
        self._llm_capable = llm_capable
        self._glossary = glossary
        # The SDK retries a rate-limited engine for a very long time. Without a
        # ceiling of our own, one stalled batch freezes the whole document.
        # ponytail: the abandoned worker thread keeps running until the SDK gives
        # up (it cannot be killed); acceptable because a timeout aborts the job.
        self._batch_timeout = batch_timeout if batch_timeout and batch_timeout > 0 else None

    @property
    def llm_capable(self) -> bool:
        """True when the engine accepts a raw prompt (batch strategy available)."""
        return self._llm_capable

    # -- single calls (blocking, run in a worker thread) --
    def _llm_request(self, prompt: str) -> str:
        return self._translator.llm_translate(prompt)

    def _plain(self, text: str) -> str:
        return self._translator.translate(text)

    def _build_prompt(self, batch: Sequence[str]) -> str:
        extra = f"- Use this glossary consistently: {self._glossary}\n" if self._glossary else ""
        return (
            _BATCH_TEMPLATE.replace("__LANG__", self._lang_out)
            .replace("__EXTRA__", extra)
            .replace("__PAYLOAD__", json.dumps(list(batch), ensure_ascii=False))
        )

    async def _call(self, func: Callable[..., Any], *args: Any) -> Any:
        """Run a blocking engine call in a thread, optionally bounded in time."""
        call = asyncio.to_thread(func, *args)
        if self._batch_timeout is None:
            return await call
        try:
            return await asyncio.wait_for(call, timeout=self._batch_timeout)
        except asyncio.TimeoutError as exc:
            raise BatchTranslationError(
                f"大模型接口在 {self._batch_timeout:.0f} 秒内没有返回"
                "（接口限流、排队或网络问题）。请稍后重试或改用其他引擎。"
            ) from exc

    async def _translate_batch(self, batch: Sequence[str], depth: int = 0) -> list[str]:
        if self._llm_capable:
            try:
                raw = await self._call(self._llm_request, self._build_prompt(batch))
            except BatchTranslationError:
                raise
            except Exception as exc:  # noqa: BLE001
                # The *engine* failed: network, auth, quota, rate limit. The SDK
                # and tenacity have already retried this request hard, so
                # splitting the batch would only multiply the same failing call
                # (one 429 batch would become thousands). Fail fast instead, with
                # a message that says what the user can actually do.
                raise BatchTranslationError(describe_engine_error(exc)) from exc

            parsed = _parse_json_array(raw, len(batch))
            if parsed is not None:
                return parsed
            # The model answered, we just could not use the shape of the reply.
            # Splitting is the right move here: smaller batches are easier for a
            # model to keep in JSON order.
            logger.warning(
                "Batch reply was not a usable JSON array (size=%d, depth=%d); splitting",
                len(batch),
                depth,
            )

        if len(batch) > 1 and depth < MAX_SPLIT_DEPTH:
            middle = len(batch) // 2
            head = await self._translate_batch(batch[:middle], depth + 1)
            tail = await self._translate_batch(batch[middle:], depth + 1)
            return head + tail

        return [await self._call(self._plain, item) for item in batch]

    async def translate_all(
        self,
        texts: Sequence[str | None],
        on_progress: Callable[[int, int], None] | None = None,
        on_plan: Callable[[int, int], None] | None = None,
    ) -> list[str | None]:
        """Translate a slot-aligned list, returning a list of the same length."""
        out: list[str | None] = list(texts)

        unique: dict[str, list[int]] = {}
        for index, text in enumerate(texts):
            if needs_translation(text):
                unique.setdefault(text, []).append(index)  # type: ignore[arg-type]
        if not unique:
            logger.info("Nothing to translate: no segment contains translatable letters")
            return out

        batches = _chunk(list(unique.keys()))
        total = len(batches)
        # Report the workload before the first request goes out, so the UI can
        # show a real denominator instead of an apparently frozen 1%.
        logger.info(
            "Segment pipeline plan: %d slots, %d unique strings, %d batches",
            len(texts),
            len(unique),
            total,
        )
        if on_plan:
            on_plan(total, len(unique))

        completed = 0
        started = time.monotonic()
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

        async def run(batch: list[str]) -> tuple[list[str], list[str]]:
            async with semaphore:
                return batch, await self._translate_batch(batch)

        tasks = [asyncio.create_task(run(batch)) for batch in batches]
        try:
            for finished in asyncio.as_completed(tasks):
                batch, results = await finished
                for source, translated in zip(batch, results):
                    if not isinstance(translated, str):
                        continue
                    for index in unique[source]:
                        out[index] = translated
                completed += 1
                logger.info(
                    "Batch %d/%d done (%.1fs elapsed, %d strings)",
                    completed,
                    total,
                    time.monotonic() - started,
                    len(batch),
                )
                if on_progress:
                    on_progress(completed, total)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

        return out


def _parse_json_array(raw: str, expected: int) -> list[str] | None:
    """Best-effort extraction of a JSON string array from an LLM reply."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(data, list) or len(data) != expected:
        return None
    if not all(isinstance(item, str) for item in data):
        return None
    return data


def _chunk(items: Sequence[str], max_chars: int = MAX_BATCH_CHARS) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for item in items:
        length = len(item) + 8  # JSON quoting overhead
        if current and (size + length > max_chars or len(current) >= MAX_BATCH_ITEMS):
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += length
    if current:
        batches.append(current)
    return batches


# --- text file helpers ------------------------------------------------------
ENCODINGS = ("utf-8", "gb18030", "big5", "latin-1")


def read_text_file(path: Path) -> tuple[str, str]:
    """Decode a text file, trying the encodings that actually show up in the wild.

    A BOM is only honoured when one is really present. Decoding unconditionally
    with ``utf-8-sig`` would work on BOM-less input too, but the matching
    encoder then *prepends* a BOM, silently corrupting the output file.
    """
    raw = path.read_bytes()
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def write_text_file(path: Path, text: str, encoding: str) -> None:
    path.write_bytes(text.encode(encoding, errors="replace"))


# --- misc -------------------------------------------------------------------
@dataclass
class TranslationOutcome:
    output_path: Path
    slot_count: int
    translated_count: int


async def run_pipeline(
    handler: FileHandler,
    translator: SegmentTranslator,
    src: Path,
    dst: Path,
    on_progress: Callable[[int, int], None] | None = None,
    on_plan: Callable[[int, int], None] | None = None,
) -> TranslationOutcome:
    """Collect -> translate -> write back. The whole non-PDF pipeline."""
    started = time.monotonic()
    collected = handler.collect(src)
    texts = [text for _slot, text in collected]
    logger.info(
        "Parsed %s: %d slots in %.2fs",
        src.name,
        len(collected),
        time.monotonic() - started,
    )
    translated = await translator.translate_all(texts, on_progress=on_progress, on_plan=on_plan)
    handler.write_back(src, dst, translated)
    changed = sum(
        1
        for original, result in zip(texts, translated)
        if result is not None and result != original
    )
    logger.info(
        "Finished %s -> %s in %.1fs (%d/%d slots changed)",
        src.name,
        dst.name,
        time.monotonic() - started,
        changed,
        len(collected),
    )
    return TranslationOutcome(
        output_path=dst,
        slot_count=len(collected),
        translated_count=changed,
    )
