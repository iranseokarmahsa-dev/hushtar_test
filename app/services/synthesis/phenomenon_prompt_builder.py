from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.config import SYNTHESIS_FINAL_SYNTHESIS_MAX_PROMPT_CHARS
from app.services.synthesis.final_synthesis_inputs import load_final_synthesis_inputs
from app.services.synthesis.normalization_service import stable_hash

STAGE_KEY = "phenomenon_synthesis"
STAGE_COMPONENT_KEY = "central_phenomenon"
ROOT = Path(__file__).resolve().parents[3]
PROMPT_PATH = ROOT / "prompts" / "synthesis" / "phenomenon_synthesis.txt"
FORMAT_PATH = ROOT / "prompts" / "synthesis" / "phenomenon_synthesis_format.md"


class FinalSynthesisPromptSizeLimitError(ValueError):
    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__(
            f"حجم Prompt فاز ششم {report['prompt_chars']} نویسه است و از سقف "
            f"{report['max_prompt_chars']} نویسه بیشتر است؛ هیچ ورودی حذف یا کوتاه نشده است."
        )


def _block(value: Any, indentation: int = 2) -> str:
    text = "" if value is None else str(value)
    if not text:
        return " " * indentation + "NONE"
    fence_length = max(3, max((len(run) for run in re.findall(r"~+", text)), default=0) + 1)
    fence = "~" * fence_length
    content = "\n".join(" " * indentation + line for line in text.splitlines())
    return f"{' ' * indentation}{fence}text\n{content}\n{' ' * indentation}{fence}"


def _list(values: Any) -> str:
    if not values:
        return "  - NONE"
    lines = []
    for value in values:
        text = "" if value is None else str(value)
        if "\n" not in text:
            lines.append(f"  - {text or 'NONE'}")
            continue
        lines.extend(("  -", _block(text, 4)))
    return "\n".join(lines)


def _render_reference(evidence: dict[str, Any]) -> str:
    return "\n".join((
        f"##### EVIDENCE: {evidence['evidence_ref_id']}",
        f"- evidence_ref_id: {evidence['evidence_ref_id']}",
        f"- source_project_id: {evidence.get('source_project_id') or 'NONE'}",
        "- source_title:",
        _block(evidence.get("source_title") or "NONE"),
        "- source_item_ids:",
        _list(evidence.get("source_item_ids")),
        "- source_component_keys:",
        _list(evidence.get("source_component_keys")),
        "- evidence_type:",
        _block(evidence.get("evidence_type") or "NONE"),
        "- evidence_text:",
        _block(evidence.get("quote"), 2),
        "- interpretation:",
        _block(evidence.get("interpretation"), 2),
    ))


def _render_node(node: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> str:
    lines = [
        f"#### MODEL NODE: {node['node_id']}",
        f"- node_id: {node['node_id']}",
        f"- node_type: {node['node_type']}",
        f"- component_key: {node['component_key']}",
        "- title:",
        _block(node["title"]),
        "- definition:",
        _block(node["definition"]),
        "- source_item_ids:",
        _list(node.get("source_item_ids")),
        "- category_unit_ids:",
        _list(node.get("category_unit_ids")),
        "- theme_ids:",
        _list(node.get("theme_ids")),
        "- evidence_ref_ids:",
        _list(node.get("evidence_ref_ids")),
    ]
    for evidence_id in node.get("evidence_ref_ids", []):
        evidence = evidence_by_id.get(evidence_id)
        if evidence:
            lines.extend(("", _render_reference(evidence)))
    return "\n".join(lines)


def _render_source_phenomenon(item: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> str:
    lines = [
        f"#### APPROVED INTERVIEW PHENOMENON: {item['node_id']}",
        f"- source_phenomenon_id: {item['node_id']}",
        f"- source_project_id: {item['source_project_id']}",
        f"- source_interview_id: {item['source_interview_id']}",
        "- source_title:",
        _block(item["source_title"]),
        "- title:",
        _block(item["title"]),
        "- definition:",
        _block(item["definition"]),
        "- supporting_node_ids:",
        _list(item.get("supporting_node_ids")),
        "- supporting_source_item_ids:",
        _list(item.get("supporting_source_item_ids")),
        "- evidence_ref_ids:",
        _list(item.get("evidence_ref_ids")),
    ]
    for evidence_id in item.get("evidence_ref_ids", []):
        evidence = evidence_by_id.get(evidence_id)
        if evidence:
            lines.extend(("", _render_reference(evidence)))
    return "\n".join(lines)


def render_phenomenon_inputs(inputs: dict[str, Any]) -> str:
    lines = [
        "## RUN CONTEXT",
        f"- run_id: {inputs['run']['run_id']}",
        "- research_title:",
        _block(inputs["project_metadata"]["title"] or "NONE"),
        "- research_topic:",
        _block(inputs["project_metadata"]["topic"] or "NONE"),
        "- research_problem: NONE",
        "- research_question: NONE",
        "- research_objectives: NONE",
        f"- selected_interview_count: {inputs['source_count']}",
        f"- approved_theme_count: {sum(node['node_type'] == 'theme' for node in inputs['nodes'])}",
        f"- approved_independent_category_unit_count: {sum(node['node_type'] == 'category_unit' for node in inputs['nodes'])}",
        "",
        "## APPROVED MODEL NODES",
    ]
    lines.extend(_render_node(node, inputs["evidence_by_id"]) for node in inputs["nodes"])
    lines.extend(("", "## APPROVED INTERVIEW PHENOMENA"))
    lines.extend(
        _render_source_phenomenon(item, inputs["evidence_by_id"])
        for item in inputs["source_phenomena"]
    )
    return "\n\n".join(lines)


def build_phenomenon_prompt(
    run_id: str,
    *,
    enforce_size: bool = True,
    max_prompt_chars: int | None = None,
) -> dict[str, Any]:
    inputs = load_final_synthesis_inputs(run_id)
    body = render_phenomenon_inputs(inputs)
    template = PROMPT_PATH.read_text(encoding="utf-8")
    contract = FORMAT_PATH.read_text(encoding="utf-8")
    input_hash = stable_hash({
        "base_input_hash": inputs["base_input_hash"],
        "stage_key": STAGE_KEY,
        "rendered_inputs": body,
        "prompt_template": template,
        "format_contract": contract,
    })
    prompt = re.sub(
        r"\{\{(RUN_ID|INPUT_HASH|SOURCE_PHASE_VERSION|INPUT_MARKDOWN|FORMAT_CONTRACT)\}\}",
        lambda match: {
            "RUN_ID": run_id,
            "INPUT_HASH": input_hash,
            "SOURCE_PHASE_VERSION": str(inputs["source_phase_version"]),
            "INPUT_MARKDOWN": body,
            "FORMAT_CONTRACT": contract,
        }[match.group(1)],
        template,
    )
    maximum = max_prompt_chars if max_prompt_chars is not None else SYNTHESIS_FINAL_SYNTHESIS_MAX_PROMPT_CHARS
    report = {
        "source_count": inputs["source_count"],
        "input_phenomenon_count": len(inputs["source_phenomena"]),
        "input_theme_count": sum(node["node_type"] == "theme" for node in inputs["nodes"]),
        "input_category_unit_count": sum(node["node_type"] == "category_unit" for node in inputs["nodes"]),
        "evidence_count": len(inputs["evidence_by_id"]),
        "input_chars": len(body),
        "prompt_chars": len(prompt),
        "max_prompt_chars": maximum,
        "allowed": len(prompt) <= maximum,
    }
    if enforce_size and not report["allowed"]:
        raise FinalSynthesisPromptSizeLimitError(report)
    return {
        "run_id": run_id,
        "prompt_text": prompt,
        "input_hash": input_hash,
        "source_phase_version": inputs["source_phase_version"],
        "inputs": inputs,
        "size_report": report,
    }


def latest_phenomenon_result(run_id: str) -> dict[str, Any] | None:
    from app.repositories.synthesis_repository import list_stage_results

    results = [
        item for item in list_stage_results(run_id)
        if item["stage_key"] == STAGE_KEY and item["component_key"] == STAGE_COMPONENT_KEY
    ]
    latest = max(results, key=lambda item: item["version"]) if results else None
    if latest is None:
        return None
    try:
        current = build_phenomenon_prompt(run_id, enforce_size=False)
        latest["stale"] = (
            latest.get("status") == "stale"
            or latest.get("approval_status") == "stale"
            or latest["input_hash"] != current["input_hash"]
            or latest.get("source_phase_version") != current["source_phase_version"]
        )
    except ValueError:
        latest["stale"] = True
    return latest
