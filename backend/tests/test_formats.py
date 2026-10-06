"""Tests for the non-PDF segment translation pipeline.

Run with:  .venv/bin/python -m pytest backend/tests -q
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.formats import (  # noqa: E402
    ACCEPT_ATTR,
    HANDLER_BY_EXT,
    SUPPORTED_EXTENSIONS,
    SegmentTranslator,
    describe_formats,
    get_handler,
    run_pipeline,
)
from backend.formats.base import (  # noqa: E402
    BatchTranslationError,
    apply_paragraph_text,
    needs_translation,
    _chunk,
    _parse_json_array,
)
from backend.formats.text import protect_markdown, restore_markdown  # noqa: E402

LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def shout(text: str) -> str:
    """Deterministic pseudo-translation: uppercase every letter in place.

    Preserves placeholders, URLs, markdown markers and whitespace, so it is a
    good oracle for "was the structure preserved?".
    """
    return LETTER_RE.sub(lambda m: m.group(0).upper(), text)


class FakeLlm:
    """Stands in for a pdf2zh_next translator with a real prompt entry point."""

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.llm_calls = 0
        self.seen_items: list[str] = []

    def llm_translate(self, prompt: str) -> str:
        self.llm_calls += 1
        payload = prompt.split("Input JSON array:\n", 1)[1]
        items = json.loads(payload)
        self.seen_items.extend(items)
        if self.mode == "drop_placeholder":
            return json.dumps(["".join(x for x in shout(i) if x not in "{}") for i in items], ensure_ascii=False)
        if self.mode == "invalid" and len(items) > 1:
            return "I cannot do that."  # forces the split fallback
        if self.mode == "fenced":
            return "```json\n" + json.dumps([shout(i) for i in items], ensure_ascii=False) + "\n```"
        return json.dumps([shout(i) for i in items], ensure_ascii=False)

    def translate(self, text: str) -> str:
        return shout(text)


class NonLlm(FakeLlm):
    """Engine without a raw-prompt API (Google/Bing/DeepL style)."""

    def llm_translate(self, prompt: str) -> str:  # pragma: no cover
        raise AssertionError("llm_translate must not be used for non-LLM engines")


def make_translator(fake: FakeLlm | None = None, llm: bool = True) -> SegmentTranslator:
    return SegmentTranslator(fake or FakeLlm(), "zh-CN", llm_capable=llm)


def run(handler, fake, src: Path, dst: Path, llm: bool = True):
    return asyncio.run(run_pipeline(handler, make_translator(fake, llm), src, dst))


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
def test_registry_covers_requested_formats():
    for ext in (".pdf", ".docx", ".pptx", ".xlsx", ".md", ".txt", ".csv"):
        assert ext in SUPPORTED_EXTENSIONS, ext
        assert ext in ACCEPT_ATTR
    assert get_handler("a.pdf") is None  # pdf goes through BabelDOC
    assert get_handler("a.DOCX") is not None
    assert get_handler("a.doc") is None
    payload = describe_formats()
    assert payload["legacy_hints"] == {".doc": ".docx", ".ppt": ".pptx", ".xls": ".xlsx"}
    assert len(payload["formats"]) == len(SUPPORTED_EXTENSIONS)
    assert len(HANDLER_BY_EXT) == len(SUPPORTED_EXTENSIONS) - 1


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------
def test_needs_translation_filter():
    assert needs_translation("Hello")
    assert needs_translation("你好")
    assert not needs_translation("12345")
    assert not needs_translation("   ")
    assert not needs_translation("| --- | --- |")
    assert not needs_translation(None)


def test_json_parsing_variants():
    assert _parse_json_array('["a","b"]', 2) == ["a", "b"]
    assert _parse_json_array('```json\n["a","b"]\n```', 2) == ["a", "b"]
    assert _parse_json_array('Sure! ["a","b"] done', 2) == ["a", "b"]
    assert _parse_json_array('["a"]', 2) is None
    assert _parse_json_array('{"a":1}', 1) is None
    assert _parse_json_array('[1,2]', 2) is None
    assert _parse_json_array('', 1) is None


def test_chunking_respects_budget():
    items = ["x" * 1000 for _ in range(5)]
    batches = _chunk(items, max_chars=2400)
    assert sum(len(b) for b in batches) == 5
    assert all(len(b) <= 2 for b in batches)
    assert _chunk(["a", "b"], max_chars=2400) == [["a", "b"]]


def test_paragraph_run_redistribution_keeps_styling():
    from docx import Document

    doc = Document()
    paragraph = doc.add_paragraph()
    bold = paragraph.add_run("Hello ")
    bold.bold = True
    paragraph.add_run("world")

    apply_paragraph_text(paragraph, "NIHAO SHIJIE")
    assert paragraph.text == "NIHAO SHIJIE"
    assert any(run.bold for run in paragraph.runs), "bold run must survive"
    assert "".join(run.text or "" for run in paragraph.runs).strip() == "NIHAO SHIJIE"


def test_paragraph_write_back_includes_hyperlink_runs():
    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    doc = Document()
    paragraph = doc.add_paragraph("Click ")
    r_id = doc.part.relate_to("https://example.com", RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    node = OxmlElement("w:t")
    node.text = "here"
    run.append(node)
    link.append(run)
    paragraph._p.append(link)

    assert paragraph.text == "Click here"
    apply_paragraph_text(paragraph, "DIANJI ZHELI")
    assert paragraph.text == "DIANJI ZHELI"
    assert paragraph.hyperlinks[0].address == "https://example.com"


# ---------------------------------------------------------------------------
# markdown protection
# ---------------------------------------------------------------------------
def test_markdown_roundtrip_preserves_code_and_urls():
    line = "See [`code()`](https://x.dev/a_b) and <b>html</b> plus https://y.dev and `snippet`."
    protected, table = protect_markdown(line)
    assert "https://x.dev/a_b" not in protected
    assert "`code()`" not in protected
    assert "`snippet`" not in protected
    assert restore_markdown(protected, table) == line


def test_markdown_lost_placeholder_is_detected():
    protected, table = protect_markdown("Use `x` here")
    assert restore_markdown("USE here", table) is None
    assert restore_markdown(protected, table) == "Use `x` here"


# ---------------------------------------------------------------------------
# txt
# ---------------------------------------------------------------------------
def test_txt_translation_and_encoding_roundtrip(tmp_path):
    src = tmp_path / "a.txt"
    src.write_bytes("Hello world\r\n第二次\r\n".encode("utf-8"))
    dst = tmp_path / "out.txt"
    fake = FakeLlm()
    outcome = run(get_handler("a.txt"), fake, src, dst)

    data = dst.read_bytes().decode("utf-8")
    assert "HELLO WORLD\r\n" in data, "CRLF terminators must be preserved"
    assert "第二次" in data
    assert outcome.translated_count == 1
    assert set(fake.seen_items) == {"Hello world", "第二次"}


def test_txt_starts_with_bom_is_preserved(tmp_path):
    src = tmp_path / "b.txt"
    src.write_bytes(b"\xef\xbb\xbfHello")
    dst = tmp_path / "out.txt"
    run(get_handler("b.txt"), FakeLlm(), src, dst)
    assert dst.read_bytes().startswith(b"\xef\xbb\xbf")
    assert dst.read_bytes().decode("utf-8-sig") == "HELLO"


def test_txt_cancellation_safe_when_nothing_translatable(tmp_path):
    src = tmp_path / "c.txt"
    src.write_text("12345\n\n---\n")
    dst = tmp_path / "out.txt"
    fake = FakeLlm()
    run(get_handler("c.txt"), fake, src, dst)
    assert fake.llm_calls == 0
    assert dst.read_text() == "12345\n\n---\n"


# ---------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------
MD_SOURCE = """---
title: not translated
---

# Hello Title

Some *text* with `code()` and a [link](https://example.com/x).

```python
print("do not translate me")
```

- item one
- item two

| Col A | Col B |
| ----- | ----- |
| a1    | b1    |

Trailing hard break here.  
Next line.

<!-- a comment with words -->
[ref]: https://example.com
"""


def test_markdown_structure_preserved(tmp_path):
    src = tmp_path / "doc.md"
    src.write_text(MD_SOURCE, encoding="utf-8")
    dst = tmp_path / "out.md"
    run(get_handler("doc.md"), FakeLlm(), src, dst)
    out = dst.read_text(encoding="utf-8")

    assert "title: not translated" in out, "frontmatter untouched"
    assert "# HELLO TITLE" in out
    assert "`code()`" in out
    assert "(https://example.com/x)" in out, "link target untouched"
    assert '[LINK]' in out, "link label translated"
    assert 'print("do not translate me")' in out, "fenced code untouched"
    assert "- ITEM ONE" in out
    assert "| A1    | B1    |" in out
    assert "TRAILING HARD BREAK HERE.  \n" in out, "hard break preserved"
    assert "<!-- a comment with words -->" in out
    assert "[ref]: https://example.com" in out, "reference definition untouched"


def test_markdown_placeholder_loss_falls_back_to_source(tmp_path):
    src = tmp_path / "doc.md"
    src.write_text("Use `code()` in the doc.\n")
    dst = tmp_path / "out.md"
    run(get_handler("doc.md"), FakeLlm(mode="drop_placeholder"), src, dst)
    assert dst.read_text() == "Use `code()` in the doc.\n"


def test_markdown_fenced_json_reply_is_accepted(tmp_path):
    src = tmp_path / "doc.md"
    src.write_text("# Hello\n")
    dst = tmp_path / "out.md"
    run(get_handler("doc.md"), FakeLlm(mode="fenced"), src, dst)
    assert dst.read_text() == "# HELLO\n"


# ---------------------------------------------------------------------------
# csv
# ---------------------------------------------------------------------------
def test_csv_quotes_delimiter_and_text_only(tmp_path):
    src = tmp_path / "t.csv"
    src.write_text('name,note,count\nHello,"a, b",42\n"multi\nline",world,7\n', encoding="utf-8")
    dst = tmp_path / "out.csv"
    run(get_handler("t.csv"), FakeLlm(), src, dst)
    out = dst.read_text(encoding="utf-8")
    assert "NAME,NOTE,COUNT" in out
    assert '"A, B"' in out, "embedded delimiter must stay quoted"
    assert '"MULTI\nLINE",WORLD,7' in out
    assert "42" in out and ",7" in out


def test_tsv_autodetection(tmp_path):
    src = tmp_path / "t.tsv"
    src.write_text("key\tvalue\nhello\tworld\n", encoding="utf-8")
    dst = tmp_path / "out.tsv"
    run(get_handler("t.tsv"), FakeLlm(), src, dst)
    assert dst.read_text(encoding="utf-8") == "KEY\tVALUE\nHELLO\tWORLD\n"


# ---------------------------------------------------------------------------
# xlsx
# ---------------------------------------------------------------------------
def test_xlsx_translates_text_only(tmp_path):
    import openpyxl

    src = tmp_path / "b.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet["A1"] = "Hello"
    sheet["A2"] = 42
    sheet["A3"] = "=SUM(B1:B2)"
    sheet["B1"] = "World"
    sheet["A4"] = None
    other = workbook.create_sheet("Second")
    other["A1"] = "Nested text"
    workbook.save(src)

    dst = tmp_path / "out.xlsx"
    run(get_handler("b.xlsx"), FakeLlm(), src, dst)

    result = openpyxl.load_workbook(dst)
    assert [s.title for s in result.worksheets] == ["Sheet1", "Second"], "sheet names untouched"
    first = result["Sheet1"]
    assert first["A1"].value == "HELLO"
    assert first["B1"].value == "WORLD"
    assert first["A2"].value == 42
    assert first["A3"].value == "=SUM(B1:B2)", "formula must not be rewritten"
    assert result["Second"]["A1"].value == "NESTED TEXT"


# ---------------------------------------------------------------------------
# docx
# ---------------------------------------------------------------------------
def test_docx_body_table_header_and_styling(tmp_path):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    src = tmp_path / "d.docx"
    doc = Document()
    heading = doc.add_heading("Hello Title", level=1)
    body = doc.add_paragraph()
    bold = body.add_run("Bold part ")
    bold.bold = True
    body.add_run("plain part")
    body.alignment = WD_ALIGN_PARAGRAPH.CENTER

    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Cell A"
    table.cell(0, 1).text = "Cell B"
    doc.sections[0].header.paragraphs[0].text = "Header text"
    doc.sections[0].footer.paragraphs[0].text = "Footer text"
    doc.save(src)

    dst = tmp_path / "out.docx"
    outcome = run(get_handler("d.docx"), FakeLlm(), src, dst)
    assert outcome.translated_count > 0

    result = Document(str(dst))
    assert result.paragraphs[0].text == "HELLO TITLE"
    assert "BOLD PART" in result.paragraphs[1].text
    body_out = result.paragraphs[1]
    assert any(run.bold for run in body_out.runs if run.text), "bold run kept"
    assert body_out.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert result.tables[0].cell(0, 0).text == "CELL A"
    assert result.tables[0].cell(0, 1).text == "CELL B"
    assert result.sections[0].header.paragraphs[0].text == "HEADER TEXT"
    assert result.sections[0].footer.paragraphs[0].text == "FOOTER TEXT"
    assert result.paragraphs[0].style.name.startswith("Heading"), "heading style kept"


def test_docx_nested_table(tmp_path):
    from docx import Document

    src = tmp_path / "n.docx"
    doc = Document()
    outer = doc.add_table(rows=1, cols=1)
    inner = outer.cell(0, 0).add_table(rows=1, cols=1)
    inner.cell(0, 0).text = "Deep text"
    doc.save(src)

    dst = tmp_path / "out.docx"
    run(get_handler("n.docx"), FakeLlm(), src, dst)
    result = Document(str(dst))
    assert result.tables[0].cell(0, 0).tables[0].cell(0, 0).text == "DEEP TEXT"


# ---------------------------------------------------------------------------
# pptx
# ---------------------------------------------------------------------------
def test_pptx_shapes_table_group_and_notes(tmp_path):
    import pptx
    from pptx.shapes.group import GroupShape
    from pptx.util import Inches

    src = tmp_path / "p.pptx"
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
    box.text_frame.text = "Title text"
    box.text_frame.paragraphs[0].runs[0].font.bold = True

    bullet = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(3), Inches(1))
    bullet.text_frame.text = "First bullet"
    bullet.text_frame.add_paragraph().text = "Second bullet"

    table = slide.shapes.add_table(1, 2, Inches(1), Inches(3), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "Left"
    table.cell(0, 1).text = "Right"

    slide.notes_slide.notes_text_frame.text = "Speaker note"

    group_box = slide.shapes.add_textbox(Inches(5), Inches(1), Inches(2), Inches(1))
    group_box.text_frame.text = "Groupable"
    slide.shapes.add_group_shape().shapes.add_textbox(Inches(5), Inches(2), Inches(2), Inches(1)).text_frame.text = "In group"
    presentation.save(src)

    dst = tmp_path / "out.pptx"
    run(get_handler("p.pptx"), FakeLlm(), src, dst)

    result = pptx.Presentation(str(dst))
    out_slide = result.slides[0]

    def collect(shapes, sink):
        for shape in shapes:
            if isinstance(shape, GroupShape):
                collect(shape.shapes, sink)
                continue
            if getattr(shape, "has_text_frame", False):
                sink.append(shape.text_frame.text)
            if getattr(shape, "has_table", False):
                sink.extend(cell.text for row in shape.table.rows for cell in row.cells)

    texts: list[str] = []
    collect(out_slide.shapes, texts)
    joined = " | ".join(texts)
    assert "TITLE TEXT" in joined
    assert "FIRST BULLET" in joined and "SECOND BULLET" in joined
    assert "LEFT" in joined and "RIGHT" in joined
    assert "IN GROUP" in joined, "grouped shapes must be walked"
    assert result.slides[0].notes_slide.notes_text_frame.text == "SPEAKER NOTE"

    first_box = next(s for s in out_slide.shapes if s.has_text_frame and s.text_frame.text == "TITLE TEXT")
    assert first_box.text_frame.paragraphs[0].runs[0].font.bold is True, "run styling kept"


def test_pptx_without_notes_does_not_gain_one(tmp_path):
    import pptx
    from pptx.util import Inches

    src = tmp_path / "q.pptx"
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1)).text_frame.text = "Only text"
    presentation.save(src)
    assert slide.has_notes_slide is False

    dst = tmp_path / "out.pptx"
    run(get_handler("q.pptx"), FakeLlm(), src, dst)
    result = pptx.Presentation(str(dst))
    assert result.slides[0].has_notes_slide is False, "no empty notes part should be injected"
    assert "ONLY TEXT" in result.slides[0].shapes[0].text_frame.text


# ---------------------------------------------------------------------------
# pipeline behaviour
# ---------------------------------------------------------------------------
def test_duplicate_segments_are_translated_once(tmp_path):
    src = tmp_path / "dup.txt"
    src.write_text("Same line\nSame line\nSame line\nOther line\n")
    dst = tmp_path / "out.txt"
    fake = FakeLlm()
    run(get_handler("dup.txt"), fake, src, dst)
    assert sorted(fake.seen_items) == ["Other line", "Same line"]
    assert dst.read_text() == "SAME LINE\nSAME LINE\nSAME LINE\nOTHER LINE\n"


def test_invalid_batch_json_falls_back_without_losing_text(tmp_path):
    src = tmp_path / "f.txt"
    src.write_text("\n".join(f"line {i}" for i in range(6)) + "\n")
    dst = tmp_path / "out.txt"
    fake = FakeLlm(mode="invalid")
    run(get_handler("f.txt"), fake, src, dst)
    assert dst.read_text() == "\n".join(f"LINE {i}" for i in range(6)) + "\n"


def test_non_llm_engine_uses_per_segment_path(tmp_path):
    src = tmp_path / "n.txt"
    src.write_text("alpha\nbeta\n")
    dst = tmp_path / "out.txt"
    fake = NonLlm()
    outcome = run(get_handler("n.txt"), fake, src, dst, llm=False)
    assert fake.llm_calls == 0
    assert dst.read_text() == "ALPHA\nBETA\n"
    assert outcome.translated_count == 2


def test_progress_callback_is_monotonic(tmp_path):
    src = tmp_path / "p.txt"
    src.write_text("\n".join(f"line {i}" for i in range(120)) + "\n")
    dst = tmp_path / "out.txt"
    seen: list[tuple[int, int]] = []
    translator = make_translator(FakeLlm())

    async def go():
        from backend.formats import run_pipeline

        return await run_pipeline(
            get_handler("p.txt"), translator, src, dst, on_progress=lambda a, b: seen.append((a, b))
        )

    asyncio.run(go())
    assert seen, "progress must be reported"
    finished = [a for a, _total in seen]
    assert finished == sorted(finished)
    assert seen[-1][0] == seen[-1][1]
    assert seen[-1][1] > 1, "large input should produce several batches"
    assert len(dst.read_text().split("\n")) == 121


def test_empty_and_unsupported_files(tmp_path):
    src = tmp_path / "empty.txt"
    src.write_text("")
    dst = tmp_path / "out.txt"
    fake = FakeLlm()
    outcome = run(get_handler("empty.txt"), fake, src, dst)
    assert outcome.slot_count == 1
    assert fake.llm_calls == 0
    assert dst.read_text() == ""


# ---------------------------------------------------------------------------
# adapter integration (guards the seam against upstream drift)
# ---------------------------------------------------------------------------
def _build_settings(tmp_path, engine_type, engine_config):
    from backend.adapter import TranslationAdapter

    return TranslationAdapter.build_settings(
        lang_in="en",
        lang_out="zh-CN",
        engine_type=engine_type,
        engine_config=engine_config,
        output_dir=tmp_path,
    )


@pytest.mark.parametrize(
    ("engine_type", "engine_config", "expected_llm"),
    [
        ("OpenAI", {"openai_api_key": "sk-test", "openai_model": "gpt-4o-mini"}, True),
        ("DeepSeek", {"deepseek_api_key": "sk-test", "deepseek_model": "deepseek-chat"}, True),
        ("Google", {}, False),
        ("DeepL", {"deepl_auth_key": "test"}, False),
    ],
)
def test_adapter_reports_llm_capability(tmp_path, monkeypatch, engine_type, engine_config, expected_llm):
    """The pipeline picks its batching strategy from the engine's support_llm flag."""
    import pdf2zh_next.translator as upstream_translator

    from backend.adapter import TranslationAdapter

    settings = _build_settings(tmp_path, engine_type, engine_config)

    seen = {}

    def fake_get_translator(inner_settings):
        seen["lang_out"] = inner_settings.translation.lang_out
        return object()

    # get_translator performs a live health-check call; stub it out.
    monkeypatch.setattr(upstream_translator, "get_translator", fake_get_translator)

    translator = TranslationAdapter.build_segment_translator(settings)
    assert isinstance(translator, SegmentTranslator)
    assert translator.llm_capable is expected_llm
    assert seen["lang_out"] == "zh-CN"


# ---------------------------------------------------------------------------
# engine failures must be loud, fast, and must not amplify requests
# ---------------------------------------------------------------------------
class FailingEngine:
    """Stands in for a rate-limited / overloaded endpoint."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    def llm_translate(self, prompt: str) -> str:
        self.calls += 1
        raise self.exc

    def translate(self, text: str) -> str:
        self.calls += 1
        raise self.exc


def test_engine_failure_does_not_amplify_requests(tmp_path):
    """A dead engine must fail once per batch, not once per segment.

    The old code split a failed batch in half on *any* exception, so a single
    429 turned into 2**depth extra calls and eventually one call per segment.
    """
    # 30 short lines == exactly one batch (MAX_BATCH_ITEMS is 40)
    src = tmp_path / "many.txt"
    src.write_text("\n".join(f"Line {i} of text" for i in range(30)) + "\n")
    fake = FailingEngine(RuntimeError("Error code: 429 - rate limited"))
    translator = SegmentTranslator(fake, "zh-CN", llm_capable=True)

    with pytest.raises(BatchTranslationError) as excinfo:
        asyncio.run(
            run_pipeline(get_handler("many.txt"), translator, src, tmp_path / "out.txt")
        )

    assert fake.calls == 1, f"engine called {fake.calls} times for one batch"
    assert "429" in str(excinfo.value) or "限流" in str(excinfo.value)


def test_engine_failure_with_several_batches_stays_bounded(tmp_path):
    """Concurrency means a couple of batches may already be in flight; that is
    fine, but the count must stay near the batch count, not the segment count."""
    src = tmp_path / "bounded.txt"
    src.write_text("\n".join(f"Line {i} of text" for i in range(200)) + "\n")
    fake = FailingEngine(RuntimeError("Error code: 429 - rate limited"))
    translator = SegmentTranslator(fake, "zh-CN", llm_capable=True)

    with pytest.raises(BatchTranslationError):
        asyncio.run(
            run_pipeline(get_handler("bounded.txt"), translator, src, tmp_path / "out.txt")
        )

    # 200 lines / 40 per batch = 5 batches; MAX_CONCURRENCY is 4
    assert fake.calls <= 5, f"engine called {fake.calls} times"
    assert fake.calls < 20, "request amplification is back"


def test_engine_failure_message_is_actionable():
    from backend.formats.base import describe_engine_error

    rate = describe_engine_error(RuntimeError("Error code: 429 - rate limited"))
    assert "限流" in rate and "大模型接口调用失败" in rate

    auth = describe_engine_error(RuntimeError("Error code: 401 - invalid_api_key"))
    assert "API Key" in auth

    timeout = describe_engine_error(RuntimeError("Request timeout after 30s"))
    assert "超时" in timeout

    conn = describe_engine_error(RuntimeError("Connection error: refused"))
    assert "Base URL" in conn


def test_batch_timeout_surfaces_instead_of_hanging(tmp_path):
    """A stalled endpoint must not freeze the whole document forever."""
    import time as _time

    class Hanging:
        def llm_translate(self, prompt: str) -> str:
            _time.sleep(3)
            return "[]"

        def translate(self, text: str) -> str:
            _time.sleep(3)
            return text

    src = tmp_path / "hang.txt"
    src.write_text("Hello stalled world\n")
    translator = SegmentTranslator(Hanging(), "zh-CN", llm_capable=True, batch_timeout=0.2)

    async def go() -> float:
        started = _time.monotonic()
        with pytest.raises(BatchTranslationError) as excinfo:
            await run_pipeline(get_handler("hang.txt"), translator, src, tmp_path / "out.txt")
        assert "没有返回" in str(excinfo.value)
        return _time.monotonic() - started

    # measure inside the loop: asyncio.run() additionally waits for the abandoned
    # worker thread at executor shutdown, which is not what we are asserting
    assert asyncio.run(go()) < 1.0


def test_parse_failure_still_splits_and_recovers(tmp_path):
    """Only malformed *replies* justify splitting; that path must keep working."""
    src = tmp_path / "split.txt"
    src.write_text("\n".join(f"Sentence {i}" for i in range(8)) + "\n")
    fake = FakeLlm(mode="invalid")
    translator = SegmentTranslator(fake, "zh-CN", llm_capable=True)
    outcome = asyncio.run(
        run_pipeline(get_handler("split.txt"), translator, src, tmp_path / "out.txt")
    )
    assert outcome.translated_count == 8
    assert "SENTENCE 7" in (tmp_path / "out.txt").read_text()


def test_plan_is_reported_before_the_first_request(tmp_path):
    """The UI needs a real denominator immediately, not a frozen 1%."""
    src = tmp_path / "plan.txt"
    src.write_text("\n".join(f"Line {i}" for i in range(200)) + "\n")
    plan: list[tuple[int, int]] = []
    translator = make_translator(FakeLlm())

    asyncio.run(
        run_pipeline(
            get_handler("plan.txt"),
            translator,
            src,
            tmp_path / "out.txt",
            on_plan=lambda batches, unique: plan.append((batches, unique)),
        )
    )
    assert len(plan) == 1
    batches, unique = plan[0]
    assert unique == 200
    assert batches > 1


def test_concurrency_is_configurable_and_clamped(tmp_path):
    """The UI thread count must reach the segment pipeline, but stay sane."""
    from backend.formats.base import MAX_CONCURRENCY, MAX_CONCURRENCY_LIMIT

    src = tmp_path / "conc.txt"
    src.write_text("Hello concurrency\n")
    translator = SegmentTranslator(FakeLlm(), "zh-CN", llm_capable=True, concurrency=1)
    asyncio.run(run_pipeline(get_handler("conc.txt"), translator, src, tmp_path / "a.txt"))
    assert translator._concurrency == 1

    assert SegmentTranslator(FakeLlm(), "zh-CN", True)._concurrency == MAX_CONCURRENCY
    assert SegmentTranslator(FakeLlm(), "zh-CN", True, concurrency=0)._concurrency == MAX_CONCURRENCY
    assert (
        SegmentTranslator(FakeLlm(), "zh-CN", True, concurrency=9999)._concurrency
        == MAX_CONCURRENCY_LIMIT
    )


def test_segment_translator_honours_ui_thread_count(tmp_path):
    """pool_max_workers flows from the request into the pipeline."""
    from backend.adapter import TranslationAdapter

    settings = _build_settings(
        tmp_path, "OpenAI", {"openai_api_key": "sk-test", "openai_model": "gpt-4o-mini"}
    )
    settings.translation.pool_max_workers = 2

    import pdf2zh_next.translator as upstream_translator

    original = upstream_translator.get_translator
    upstream_translator.get_translator = lambda _s: object()
    try:
        translator = TranslationAdapter.build_segment_translator(settings)
    finally:
        upstream_translator.get_translator = original
    assert translator._concurrency == 2


# ---------------------------------------------------------------------------
# opaque SDK wrappers must be unwrapped into something actionable
# ---------------------------------------------------------------------------
def _retry_error(inner: BaseException):
    """Build the exact wrapper tenacity produces (a settled Future holding the cause)."""
    from tenacity import Future, RetryError

    attempt = Future(attempt_number=1)
    attempt.set_exception(inner)
    return RetryError(attempt)


def test_unwrapper_never_blocks_on_an_unfinished_future():
    """Regression: Future.exception() waits, so the error path could deadlock.

    Calling it without the done() guard hangs forever, taking the event loop
    with it -- which is far worse than the unhelpful message it was fixing.
    """
    import threading

    from tenacity import Future, RetryError

    from backend.formats.base import _root_cause, describe_engine_error

    pending = Future(attempt_number=1)  # never completed
    wrapped = RetryError(pending)

    finished = threading.Event()

    def run():
        _root_cause(wrapped)
        describe_engine_error(wrapped, "OpenAITranslator")
        finished.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert finished.wait(timeout=3), "error formatting blocked on an unfinished future"


def test_retry_error_is_unwrapped_to_the_real_cause():
    """tenacity stringifies as a Future repr; the user needs the status code."""
    import httpx

    from backend.formats.base import _root_cause, describe_engine_error

    inner = httpx.HTTPStatusError(
        "403 Forbidden",
        request=httpx.Request("POST", "https://api.example.com/v1"),
        response=httpx.Response(403),
    )
    wrapped = _retry_error(inner)
    assert "Future" in str(wrapped)  # the useless message users were seeing

    assert _root_cause(wrapped) is inner
    message = describe_engine_error(wrapped, "SiliconFlowFreeTranslator")
    assert "Future at 0x" not in message, message
    assert "403" in message and "拒绝访问" in message
    assert "SiliconFlowFreeTranslator" in message
    assert "设置中心" in message  # the free-engine advice


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "API Key 无效"),
        (403, "拒绝访问"),
        (404, "Base URL"),
        (429, "限流"),
        (500, "服务端错误"),
        (503, "服务端错误"),
    ],
)
def test_status_codes_map_to_readable_reasons(status, expected):
    import httpx

    from backend.formats.base import describe_engine_error

    request = httpx.Request("POST", "https://api.example.com/v1")
    exc = httpx.HTTPStatusError(
        f"{status}",
        request=request,
        response=httpx.Response(status, json={"error": "nope"}, request=request),
    )
    message = describe_engine_error(exc, "OpenAITranslator")
    assert expected in message, message
    assert "设置中心" not in message  # paid engines get no free-engine advice


def test_provider_body_is_included_in_the_message():
    """The free proxy answers 400 {"message":"Keyword not allowed"}; that text is
    the single most useful clue, so it must survive into the UI."""
    import httpx
    from tenacity import Future, RetryError

    from backend.formats.base import describe_engine_error

    request = httpx.Request("POST", "https://api1.pdf2zh-next.com/chatproxy")
    response = httpx.Response(400, json={"message": "Keyword not allowed"}, request=request)
    attempt = Future(attempt_number=1)
    attempt.set_exception(
        httpx.HTTPStatusError("400 Bad Request", request=request, response=response)
    )

    message = describe_engine_error(RetryError(attempt), "SiliconFlowFreeTranslator")
    assert "Keyword not allowed" in message, message
    assert "Future at 0x" not in message
    assert "HTTP 400" in message
    assert "SiliconFlowFree" in message


def test_exception_groups_and_chains_are_unwrapped():
    from backend.formats.base import describe_engine_error

    try:
        try:
            raise ValueError("the actual cause")
        except ValueError as inner:
            raise RuntimeError("wrapper") from inner
    except RuntimeError as exc:
        assert "the actual cause" in describe_engine_error(exc)

    group = BaseExceptionGroup("many", [ConnectionError("socket refused")])
    assert "无法连接" in describe_engine_error(group, "OpenAITranslator")


def test_engine_error_message_has_no_wrapper_jargon():
    """Whatever goes in, the user-facing text must not be a repr dump."""
    from backend.formats.base import describe_engine_error

    for exc in (
        RuntimeError("boom"),
        ValueError("weird"),
        ConnectionError("Network is unreachable"),
    ):
        message = describe_engine_error(exc, "OpenAITranslator")
        assert message.startswith("大模型接口调用失败")
        assert "原因：" in message


def test_plain_path_failures_are_wrapped_too(tmp_path):
    """Classical MT engines (Google/Bing/DeepL) raise from translate(), not the
    raw-prompt entry point, and used to leak the raw exception."""
    src = tmp_path / "mt.txt"
    src.write_text("Hello classical engine\n")
    fake = FailingEngine(ConnectionError("Network is unreachable"))
    translator = SegmentTranslator(fake, "zh-CN", llm_capable=False)

    with pytest.raises(BatchTranslationError) as excinfo:
        asyncio.run(run_pipeline(get_handler("mt.txt"), translator, src, tmp_path / "out.txt"))
    message = str(excinfo.value)
    assert "大模型接口调用失败" in message and "无法连接" in message
