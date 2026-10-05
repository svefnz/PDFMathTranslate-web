from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, AsyncGenerator

# Ensure upstream-core is in sys.path
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
UPSTREAM_CORE_PATH = PROJECT_ROOT / "upstream-core"

if str(UPSTREAM_CORE_PATH) not in sys.path:
    sys.path.insert(0, str(UPSTREAM_CORE_PATH))

import pdf2zh_next
from pdf2zh_next.config import ConfigManager
from pdf2zh_next.config.model import SettingsModel
from pdf2zh_next.config.translate_engine_model import (
    GUI_PASSWORD_FIELDS,
    GUI_SENSITIVE_FIELDS,
    TRANSLATION_ENGINE_METADATA,
    TRANSLATION_ENGINE_METADATA_MAP,
)
from pdf2zh_next.high_level import TranslationError, do_translate_async_stream

try:
    from backend.formats.base import SegmentTranslator
except ImportError:  # pragma: no cover - flat sys.path fallback
    from formats.base import SegmentTranslator

logger = logging.getLogger(__name__)

#: With no explicit timeout the OpenAI SDK waits forever, so a stalled socket
#: freezes the job with no way out. Applied only when the engine config leaves
#: the timeout empty.
DEFAULT_ENGINE_TIMEOUT_SECONDS = int(os.environ.get("SEGMENT_ENGINE_TIMEOUT_S", "180"))


class TranslationAdapter:
    """
    Adapter layer to decouple the FastAPI server from the upstream core library.
    """

    @classmethod
    def get_supported_engines(cls) -> list[dict[str, Any]]:
        """
        Returns metadata of supported translation engines for dynamic frontend form rendering.
        """
        engines = []
        for metadata in TRANSLATION_ENGINE_METADATA:
            engine_info = {
                "name": metadata.translate_engine_type,
                "support_llm": metadata.support_llm,
                "fields": [],
            }
            if metadata.setting_model_type:
                for field_name, field in metadata.setting_model_type.model_fields.items():
                    if field_name in ("translate_engine_type", "support_llm"):
                        continue
                    gui_extra = (field.json_schema_extra or {}).get("gui", {})
                    engine_info["fields"].append({
                        "name": field_name,
                        "description": field.description or field_name,
                        "default": field.default if field.default is not ... else None,
                        "is_password": field_name in GUI_PASSWORD_FIELDS,
                        "widget": gui_extra.get("widget", "input"),
                        "choices": gui_extra.get("choices", []),
                        "visible_when": gui_extra.get("visible_when"),
                    })
            engines.append(engine_info)
        return engines

    @classmethod
    def build_settings(
        cls,
        lang_in: str,
        lang_out: str,
        engine_type: str,
        engine_config: dict[str, Any],
        output_dir: Path,
        thread_count: int | None = None,
        pages: str | None = None,
        advanced_settings: dict[str, Any] | None = None,
    ) -> SettingsModel:
        """
        Constructs and validates the SettingsModel required by BabelDOC / pdf2zh_next.
        """
        # Temporarily isolate sys.argv so argparse in ConfigManager does not choke on uvicorn CLI args
        saved_argv = sys.argv
        try:
            sys.argv = [sys.argv[0]]
            settings = ConfigManager().initialize_config()
        finally:
            sys.argv = saved_argv

        settings.translation.lang_in = lang_in
        settings.translation.lang_out = lang_out
        settings.translation.output = str(output_dir)

        if thread_count:
            settings.translation.pool_max_workers = thread_count

        if pages and pages.strip():
            settings.pdf.pages = pages.strip()

        if advanced_settings:
            # Watermark mode: "watermarked", "no_watermark", "both"
            if "watermark_output_mode" in advanced_settings:
                settings.pdf.watermark_output_mode = str(advanced_settings["watermark_output_mode"])

            # Glossary / Terminology
            if "no_auto_extract_glossary" in advanced_settings:
                settings.translation.no_auto_extract_glossary = bool(advanced_settings["no_auto_extract_glossary"])
            if "term_qps" in advanced_settings and advanced_settings["term_qps"] is not None:
                settings.translation.term_qps = int(advanced_settings["term_qps"])
            if "term_pool_max_workers" in advanced_settings and advanced_settings["term_pool_max_workers"] is not None:
                settings.translation.term_pool_max_workers = int(advanced_settings["term_pool_max_workers"])

            # PDF Output Layout
            if "dual_translate_first" in advanced_settings:
                settings.pdf.dual_translate_first = bool(advanced_settings["dual_translate_first"])
            if "use_alternating_pages_dual" in advanced_settings:
                settings.pdf.use_alternating_pages_dual = bool(advanced_settings["use_alternating_pages_dual"])
            if "only_include_translated_page" in advanced_settings:
                settings.pdf.only_include_translated_page = bool(advanced_settings["only_include_translated_page"])
            if "no_mono" in advanced_settings:
                settings.pdf.no_mono = bool(advanced_settings["no_mono"])
            if "no_dual" in advanced_settings:
                settings.pdf.no_dual = bool(advanced_settings["no_dual"])
            if "translate_table_text" in advanced_settings:
                settings.pdf.translate_table_text = bool(advanced_settings["translate_table_text"])
            if "skip_scanned_detection" in advanced_settings:
                settings.pdf.skip_scanned_detection = bool(advanced_settings["skip_scanned_detection"])
            if "auto_enable_ocr_workaround" in advanced_settings:
                settings.pdf.auto_enable_ocr_workaround = bool(advanced_settings["auto_enable_ocr_workaround"])
            if "ocr_workaround" in advanced_settings:
                settings.pdf.ocr_workaround = bool(advanced_settings["ocr_workaround"])

        metadata = TRANSLATION_ENGINE_METADATA_MAP.get(engine_type)
        if not metadata:
            raise ValueError(f"Unsupported translation engine: {engine_type}")

        # Clean config dictionary to only include valid fields for this engine
        valid_fields = metadata.setting_model_type.model_fields.keys() if metadata.setting_model_type else []
        filtered_config = {
            k: v for k, v in engine_config.items()
            if v is not None and v != "" and k in valid_fields
        }

        # Instantiate the engine settings model
        try:
            settings.translate_engine_settings = metadata.setting_model_type(**filtered_config)
        except Exception as e:
            raise ValueError(f"Invalid parameters for {engine_type}: {e}") from e

        # Validate settings (engine validation & term extraction engine setup)
        settings.validate_settings()

        return settings

    @classmethod
    def build_segment_translator(
        cls,
        settings: SettingsModel,
        glossary: str = "",
    ):
        """Create a translator for the non-PDF segment pipeline.

        The engine layer is deliberately *reused* rather than reimplemented:
        ``get_translator`` gives us any of the 17 configured engines together
        with their on-disk cache, rate limiting and chain-of-thought stripping.

        ``support_llm`` tells us whether the engine exposes a raw-prompt entry
        point. LLM engines can translate a whole batch of segments in one
        request; classical MT engines (Google/Bing/DeepL) must go one segment
        at a time, so the pipeline needs to know which strategy to use.
        """
        from pdf2zh_next.translator import get_translator

        engine_settings = settings.translate_engine_settings
        llm_capable = False
        for metadata in TRANSLATION_ENGINE_METADATA:
            if metadata.setting_model_type and isinstance(
                engine_settings, metadata.setting_model_type
            ):
                llm_capable = bool(metadata.support_llm)
                break

        translator = get_translator(settings)
        cls._ensure_engine_timeout(settings)
        return SegmentTranslator(
            translator,
            settings.translation.lang_out,
            llm_capable,
            glossary,
            batch_timeout=float(os.environ.get("SEGMENT_BATCH_TIMEOUT_S", "300")),
        )

    @staticmethod
    def _ensure_engine_timeout(settings: SettingsModel) -> None:
        """Fill in a request timeout when the engine config left it unset."""
        engine_settings = settings.translate_engine_settings
        fields = getattr(type(engine_settings), "model_fields", None) or {}
        for name in fields:
            if not name.endswith("_timeout"):
                continue
            if getattr(engine_settings, name, None) not in (None, ""):
                continue
            try:
                setattr(engine_settings, name, str(DEFAULT_ENGINE_TIMEOUT_SECONDS))
                logger.info("Applied default %s=%ss", name, DEFAULT_ENGINE_TIMEOUT_SECONDS)
            except Exception as exc:  # noqa: BLE001 - validation may refuse it
                logger.debug("Could not default %s: %s", name, exc)

    @classmethod
    async def translate_stream(
        cls,
        settings: SettingsModel,
        file_path: Path,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Wraps do_translate_async_stream and yields normalized events for SSE streaming.
        """
        async for event in do_translate_async_stream(settings, file_path):
            event_type = event.get("type")

            if event_type in ("progress_start", "progress_update", "progress_end"):
                yield {
                    "event": "progress",
                    "data": {
                        "stage": event.get("stage", ""),
                        "progress": event.get("overall_progress", 0),
                        "stage_current": event.get("stage_current", 0),
                        "stage_total": event.get("stage_total", 0),
                        "part_index": event.get("part_index", 1),
                        "total_parts": event.get("total_parts", 1),
                    },
                }

            elif event_type == "finish":
                res = event.get("translate_result")
                yield {
                    "event": "finish",
                    "data": {
                        "mono_pdf_path": str(res.mono_pdf_path) if res and res.mono_pdf_path else None,
                        "dual_pdf_path": str(res.dual_pdf_path) if res and res.dual_pdf_path else None,
                        "glossary_path": str(res.auto_extracted_glossary_path)
                        if res and res.auto_extracted_glossary_path
                        else None,
                        "token_usage": event.get("token_usage", {}),
                    },
                }
                break

            elif event_type == "error":
                yield {
                    "event": "error",
                    "data": {
                        "error": event.get("error", "Unknown translation error"),
                        "error_type": event.get("error_type", "TranslationError"),
                        "details": event.get("details", ""),
                    },
                }
                break
