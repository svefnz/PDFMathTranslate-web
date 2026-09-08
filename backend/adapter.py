from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


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
    ) -> SettingsModel:
        """
        Constructs and validates the SettingsModel required by BabelDOC / pdf2zh_next.
        """
        settings = ConfigManager().initialize_config()
        settings.translation.lang_in = lang_in
        settings.translation.lang_out = lang_out
        settings.translation.output_dir = output_dir

        if thread_count:
            settings.translation.thread = thread_count

        # Activate selected engine
        metadata = TRANSLATION_ENGINE_METADATA_MAP.get(engine_type)
        if not metadata:
            raise ValueError(f"Unsupported translation engine: {engine_type}")

        # Set engine flag (e.g. settings.openai = True)
        setattr(settings, metadata.cli_flag_name, True)

        # Apply specific engine fields
        if metadata.cli_detail_field_name:
            detail_settings = getattr(settings, metadata.cli_detail_field_name)
            for k, v in engine_config.items():
                if v is not None and hasattr(detail_settings, k):
                    setattr(detail_settings, k, v)

        return settings

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
