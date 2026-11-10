from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.config import SYNTHESIS_CATEGORY_GROUPING_MAX_PROMPT_CHARS
from app.services.synthesis.normalization_service import stable_hash
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS

PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "synthesis" / "category_grouping.txt"
FORMAT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "synthesis" / "category_grouping_format.md"
STAGE_KEY = "category_grouping"


class PromptSizeLimitError(ValueError):
    def __init__(self, report: dict[str, Any]):
        self.report = report
        sizes = "، ".join(f"{key}: {value} نویسه" for key, value in report["component_chars"].items())
        super().__init__(
            f"تولید پرامپت متوقف شد؛ حجم کل {report['prompt_chars']} نویسه و سقف مجاز "
            f"{report['max_prompt_chars']} نویسه است. اندازه مؤلفه‌ها: {sizes}."
        )


def _text_block(value: Any, *, indentation: int = 2) -> str:
    text = "" if value is None else str(value)
    prefix = " " * indentation
    if not text:
        return f"{prefix}NONE"
    fence_length = max(3, max((len(run) for run in re.findall(r"~+", text)), default=0) + 1)
    fence = "~" * fence_length
    content = "\n".join(f"{prefix}{line}" for line in text.splitlines())
    return f"{prefix}{fence}text\n{content}\n{prefix}{fence}"


def _render_values(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "  - NONE"
    return "\n".join(f"  - value:\n{_text_block(value, indentation=4)}" for value in values)


def render_source_item(item: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> str:
    lines = [f"#### SOURCE ITEM: {item['source_item_id']}"]
    scalar_fields = (
        ("component_key", "component_key"),
        ("source_project_id", "source_project_id"),
        ("source_title", "source_title"),
        ("source_category_id", "source_category_id"),
        ("original_category_name", "original_category_name"),
        ("researcher_approved_category_name", "researcher_approved_category_name"),
        ("definition", "definition"),
        ("source_confidence", "source_confidence"),
        ("source_review_status", "source_review_status"),
    )
    for label, key in scalar_fields:
        lines.append(f"- {label}:")
        lines.append(_text_block(item.get(key)))
    list_fields = (
        ("source_concept_ids", "source_concept_ids"),
        ("source_subcategory_ids", "source_subcategory_ids"),
        ("source_properties", "source_properties"),
        ("source_dimensions", "source_dimensions"),
    )
    for label, key in list_fields:
        lines.append(f"- {label}:")
        lines.append(_render_values(item.get(key)))
    lines.append("- category_reason:")
    lines.append(_text_block(item.get("category_reason")))
    lines.append("- evidence_refs:")
    references = item.get("evidence_refs") or []
    if not references:
        lines.append("  - NONE")
    for reference in references:
        evidence_id = str(reference.get("evidence_ref_id") or "")
        evidence = evidence_by_id.get(evidence_id, {})
        lines.extend((
            f"  - evidence_ref_id: {evidence_id}",
            "    - source quote:",
            _text_block(evidence.get("quote"), indentation=6),
            "    - interpretation:",
            _text_block(evidence.get("interpretation"), indentation=6),
        ))
    return "\n".join(lines)


def build_category_grouping_prompt(
    *,
    run_id: str,
    pool_result: dict[str, Any],
    source_count: int,
    max_prompt_chars: int | None = None,
    enforce_size: bool = True,
) -> dict[str, Any]:
    items_by_component = {
        key: list(pool_result.get("pools", {}).get(key, []))
        for key in POOL_COMPONENT_KEYS
    }
    evidence_by_id = {
        str(item.get("evidence_ref_id")): item
        for item in pool_result.get("evidence", [])
        if item.get("evidence_ref_id")
    }
    component_blocks = {
        key: "\n\n".join(render_source_item(item, evidence_by_id) for item in items_by_component[key])
        or "NONE"
        for key in POOL_COMPONENT_KEYS
    }
    input_markdown = "\n\n".join(
        f"### COMPONENT: {key}\n\n{component_blocks[key]}"
        for key in POOL_COMPONENT_KEYS
    )
    template = PROMPT_PATH.read_text(encoding="utf-8")
    contract = FORMAT_PATH.read_text(encoding="utf-8")
    input_hash = stable_hash({
        "run_id": run_id,
        "source_count": source_count,
        "pool_version": pool_result.get("version"),
        "pool_input_hash": pool_result.get("input_hash"),
        "input_markdown": input_markdown,
        "prompt_template": template,
        "format_contract": contract,
    })
    substitutions = {
        "RUN_ID": run_id,
        "INPUT_HASH": input_hash,
        "SOURCE_ITEMS_MARKDOWN": input_markdown,
        "FORMAT_CONTRACT": contract,
    }
    prompt = re.sub(
        r"\{\{(RUN_ID|INPUT_HASH|SOURCE_ITEMS_MARKDOWN|FORMAT_CONTRACT)\}\}",
        lambda match: substitutions[match.group(1)],
        template,
    )
    component_chars = {
        key: len(component_blocks[key])
        for key in POOL_COMPONENT_KEYS
    }
    source_item_count = sum(len(items) for items in items_by_component.values())
    estimated_response_chars = max(1200, source_item_count * 700 + sum(
        len(items) for items in items_by_component.values()
    ) * 150)
    maximum = max_prompt_chars if max_prompt_chars is not None else SYNTHESIS_CATEGORY_GROUPING_MAX_PROMPT_CHARS
    size_report = {
        "component_chars": component_chars,
        "source_item_count": source_item_count,
        "source_count": source_count,
        "input_chars": len(input_markdown),
        "instruction_and_format_chars": len(prompt) - len(input_markdown),
        "prompt_chars": len(prompt),
        "estimated_response_chars": estimated_response_chars,
        "max_prompt_chars": maximum,
        "allowed": len(prompt) <= maximum,
    }
    if enforce_size and not size_report["allowed"]:
        raise PromptSizeLimitError(size_report)
    return {
        "prompt_text": prompt,
        "input_hash": input_hash,
        "size_report": size_report,
        "items_by_component": items_by_component,
    }
