from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.core.config import SYNTHESIS_THEME_SYNTHESIS_MAX_PROMPT_CHARS
from app.repositories.synthesis_repository import list_stage_results
from app.services.synthesis.category_review_service import (
    _approved_pool,
    get_approved_category_grouping,
)
from app.services.synthesis.normalization_service import stable_hash
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS
from app.services.synthesis.category_grouping_service import _latest_result

STAGE_KEY = "theme_synthesis"
STAGE_COMPONENT_KEY = "themes"
PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "synthesis" / "theme_synthesis.txt"
FORMAT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "synthesis" / "theme_synthesis_format.md"


class ThemePromptSizeLimitError(ValueError):
    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__(
            f"حجم Prompt تم {report['prompt_chars']} نویسه است و از سقف "
            f"{report['max_prompt_chars']} نویسه بیشتر است؛ هیچ داده‌ای حذف نشده است."
        )


def _category_unit_id(kind: str, stable_source_id: str) -> str:
    suffix = stable_hash([kind, stable_source_id])[:24]
    return f"catunit_{suffix}"


def _source_item_view(item: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    references = []
    for reference in item.get("evidence_refs", []):
        evidence_id = str(reference.get("evidence_ref_id") or "")
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            continue
        references.append({
            "evidence_ref_id": evidence_id,
            "source_title": evidence.get("source_title"),
            "paragraph_id": evidence.get("paragraph_id"),
            "quote": evidence.get("quote"),
            "interpretation": evidence.get("interpretation"),
        })
    return {
        "source_item_id": item.get("source_item_id"),
        "source_title": item.get("source_title"),
        "category_name": item.get("researcher_approved_category_name") or item.get("original_category_name"),
        "definition": item.get("definition"),
        "category_reason": item.get("category_reason"),
        "source_concept_ids": item.get("source_concept_ids", []),
        "source_subcategory_ids": item.get("source_subcategory_ids", []),
        "source_properties": item.get("source_properties", []),
        "source_dimensions": item.get("source_dimensions", []),
        "evidence_refs": references,
    }


def _make_category_units(run_id: str) -> tuple[list[dict[str, Any]], int, int]:
    approved_grouping = get_approved_category_grouping(run_id)
    pool, pool_version, _ = _approved_pool(run_id)
    grouping_result = _latest_result(run_id, "category_grouping", "semantic_groups")
    if grouping_result is None or grouping_result.get("approval_status") != "approved":
        raise ValueError("نسخه تأییدشده فاز چهارم پیدا نشد.")
    evidence_by_id = {
        str(item.get("evidence_ref_id")): item
        for item in pool.get("evidence", [])
        if item.get("evidence_ref_id")
    }
    source_items_by_id = {
        str(item.get("source_item_id")): item
        for component_key in POOL_COMPONENT_KEYS
        for item in pool.get("pools", {}).get(component_key, [])
    }
    units = []
    for component_key in POOL_COMPONENT_KEYS:
        component = approved_grouping.get("component_results", {}).get(component_key, {})
        for group in component.get("groups", []):
            source_item_ids = sorted(str(value) for value in group.get("source_item_ids", []))
            units.append({
                "category_unit_id": _category_unit_id("group", str(group["group_id"])),
                "unit_kind": "approved_category_group",
                "component_key": component_key,
                "category_name": group.get("proposed_category_name"),
                "definition": group.get("definition"),
                "group_reasoning": group.get("reasoning"),
                "group_similarities": group.get("similarities", []),
                "group_differences": group.get("differences", []),
                "source_group_id": group.get("group_id"),
                "source_item_ids": source_item_ids,
                "evidence_ref_ids": sorted(str(value) for value in group.get("evidence_ref_ids", [])),
                "source_items": [
                    _source_item_view(source_items_by_id[item_id], evidence_by_id)
                    for item_id in source_item_ids
                ],
            })
        for standalone in component.get("standalone_source_items", []):
            source_item_id = str(standalone["source_item_id"])
            item = source_items_by_id[source_item_id]
            item_view = _source_item_view(item, evidence_by_id)
            units.append({
                "category_unit_id": _category_unit_id("standalone", source_item_id),
                "unit_kind": "approved_standalone_category",
                "component_key": component_key,
                "category_name": item_view["category_name"],
                "definition": item_view["definition"],
                "group_reasoning": standalone.get("reason"),
                "source_group_id": None,
                "source_item_ids": [source_item_id],
                "evidence_ref_ids": sorted(
                    reference["evidence_ref_id"] for reference in item_view["evidence_refs"]
                ),
                "source_items": [item_view],
            })
    return units, int(grouping_result["version"]), pool_version


def _render_unit(unit: dict[str, Any]) -> str:
    return json.dumps(unit, ensure_ascii=False, indent=2, sort_keys=True)


def build_theme_synthesis_prompt(
    run_id: str,
    *,
    enforce_size: bool = True,
    max_prompt_chars: int | None = None,
) -> dict[str, Any]:
    units, source_phase_version, pool_version = _make_category_units(run_id)
    component_blocks = {
        component_key: "\n\n".join(
            _render_unit(unit)
            for unit in units
            if unit["component_key"] == component_key
        ) or "NONE"
        for component_key in POOL_COMPONENT_KEYS
    }
    unit_markdown = "\n\n".join(
        f"### COMPONENT: {component_key}\n\n{component_blocks[component_key]}"
        for component_key in POOL_COMPONENT_KEYS
    )
    template = PROMPT_PATH.read_text(encoding="utf-8")
    contract = FORMAT_PATH.read_text(encoding="utf-8")
    input_hash = stable_hash({
        "run_id": run_id,
        "source_phase_version": source_phase_version,
        "pool_version": pool_version,
        "units": units,
        "prompt_template": template,
        "format_contract": contract,
    })
    substitutions = {
        "COMPONENT_KEYS": ", ".join(POOL_COMPONENT_KEYS),
        "RUN_ID": run_id,
        "INPUT_HASH": input_hash,
        "SOURCE_PHASE_VERSION": str(source_phase_version),
        "TOTAL_CATEGORY_UNITS": str(len(units)),
        "CATEGORY_UNITS_MARKDOWN": unit_markdown,
        "FORMAT_CONTRACT": contract,
    }
    prompt = re.sub(
        r"\{\{(COMPONENT_KEYS|RUN_ID|INPUT_HASH|SOURCE_PHASE_VERSION|TOTAL_CATEGORY_UNITS|CATEGORY_UNITS_MARKDOWN|FORMAT_CONTRACT)\}\}",
        lambda match: substitutions[match.group(1)],
        template,
    )
    maximum = max_prompt_chars if max_prompt_chars is not None else SYNTHESIS_THEME_SYNTHESIS_MAX_PROMPT_CHARS
    report = {
        "category_unit_count": len(units),
        "component_chars": {key: len(value) for key, value in component_blocks.items()},
        "prompt_chars": len(prompt),
        "max_prompt_chars": maximum,
        "allowed": len(prompt) <= maximum,
    }
    if enforce_size and not report["allowed"]:
        raise ThemePromptSizeLimitError(report)
    return {
        "run_id": run_id,
        "prompt_text": prompt,
        "input_hash": input_hash,
        "source_phase_version": source_phase_version,
        "category_units": units,
        "size_report": report,
    }


def latest_theme_result(run_id: str) -> dict[str, Any] | None:
    results = [
        result
        for result in list_stage_results(run_id)
        if result["stage_key"] == STAGE_KEY and result["component_key"] == STAGE_COMPONENT_KEY
    ]
    latest = max(results, key=lambda result: result["version"]) if results else None
    if latest is None:
        return None
    try:
        current = build_theme_synthesis_prompt(run_id, enforce_size=False)
        latest["stale"] = (
            latest.get("status") == "stale"
            or latest.get("approval_status") == "stale"
            or latest["input_hash"] != current["input_hash"]
            or latest.get("source_phase_version") != current["source_phase_version"]
        )
    except ValueError:
        latest["stale"] = True
    return latest
