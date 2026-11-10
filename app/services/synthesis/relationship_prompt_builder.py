from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.services.synthesis.final_synthesis_inputs import (
    final_phenomenon_node_id,
    load_final_synthesis_inputs,
)
from app.services.synthesis.normalization_service import stable_hash
from app.services.synthesis.phenomenon_prompt_builder import (
    FinalSynthesisPromptSizeLimitError,
    STAGE_COMPONENT_KEY as PHENOMENON_COMPONENT_KEY,
    STAGE_KEY as PHENOMENON_STAGE_KEY,
    _block,
    _list,
    _render_node,
    _render_reference,
    latest_phenomenon_result,
)
from app.core.config import SYNTHESIS_FINAL_SYNTHESIS_MAX_PROMPT_CHARS

STAGE_KEY = "relationship_synthesis"
STAGE_COMPONENT_KEY = "relationships"
ROOT = Path(__file__).resolve().parents[3]
PROMPT_PATH = ROOT / "prompts" / "synthesis" / "relationship_synthesis.txt"
FORMAT_PATH = ROOT / "prompts" / "synthesis" / "relationship_synthesis_format.md"


def _render_source_relation(relation: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> str:
    lines = [
        f"#### APPROVED SOURCE RELATIONSHIP: {relation['source_relationship_id']}",
        f"- source_relationship_id: {relation['source_relationship_id']}",
        f"- source_project_id: {relation['source_project_id']}",
        "- source_title:",
        _block(relation["source_title"]),
        f"- mapped_source_node_id: {relation['source_node_id']}",
        f"- mapped_target_node_id: {relation['target_node_id']}",
        f"- relationship_type: {relation['relationship_type']}",
        "- description:",
        _block(relation["description"]),
        "- reasoning:",
        _block(relation["reasoning"] or "NONE"),
        "- source_item_ids:",
        _list(relation["source_item_ids"]),
        "- evidence_ref_ids:",
        _list(relation["evidence_ref_ids"]),
    ]
    for evidence_id in relation["evidence_ref_ids"]:
        evidence = evidence_by_id.get(evidence_id)
        if evidence:
            lines.extend(("", _render_reference(evidence)))
    return "\n".join(lines)


def build_relationship_prompt(
    run_id: str,
    *,
    enforce_size: bool = True,
    max_prompt_chars: int | None = None,
) -> dict[str, Any]:
    inputs = load_final_synthesis_inputs(run_id)
    phenomenon_result = latest_phenomenon_result(run_id)
    if phenomenon_result is None or phenomenon_result.get("stale"):
        raise ValueError("خروجی پدیده محوری معتبر و به‌روز وجود ندارد.")
    if phenomenon_result.get("approval_status") != "approved" or phenomenon_result.get("status") != "approved":
        raise ValueError("ابتدا پدیده محوری نهایی را تأیید کنید.")
    if not (phenomenon_result.get("validation_report_json") or {}).get("valid"):
        raise ValueError("خروجی پدیده محوری اعتبارسنجی نشده است.")
    phenomenon = phenomenon_result.get("output_json", {}).get("central_phenomenon")
    if not isinstance(phenomenon, dict) or phenomenon.get("review_status") != "approved":
        raise ValueError("پدیده محوری فاز ششم تأیید پژوهشگر را ندارد.")
    central_node = {
        "node_id": final_phenomenon_node_id(run_id),
        "node_type": "central_phenomenon",
        "component_key": "central_phenomenon",
        "title": phenomenon.get("title"),
        "definition": phenomenon.get("definition"),
        "source_ids": phenomenon.get("supporting_source_phenomenon_ids", []),
        "source_item_ids": phenomenon.get("supporting_source_item_ids", []),
        "category_unit_ids": phenomenon.get("supporting_category_unit_ids", []),
        "theme_ids": phenomenon.get("supporting_theme_ids", []),
        "evidence_ref_ids": phenomenon.get("evidence_ref_ids", []),
        "approval_status": "approved",
        "source_phase": PHENOMENON_STAGE_KEY,
        "source_phase_version": phenomenon_result["version"],
        "unit_kind": None,
        "source_items": [],
    }
    model_nodes = [*inputs["nodes"], central_node]
    body_lines = [
        "## RUN CONTEXT",
        f"- run_id: {run_id}",
        "- research_title:",
        _block(inputs["project_metadata"]["title"] or "NONE"),
        "- research_topic:",
        _block(inputs["project_metadata"]["topic"] or "NONE"),
        f"- selected_interview_count: {inputs['source_count']}",
        f"- approved_phase_five_version: {inputs['source_phase_version']}",
        f"- approved_phenomenon_version: {phenomenon_result['version']}",
        "",
        "## MODEL NODES",
    ]
    body_lines.extend(_render_node(node, inputs["evidence_by_id"]) for node in model_nodes)
    body_lines.extend(("", "## APPROVED SOURCE INTERVIEW PHENOMENA"))
    body_lines.extend(
        "\n".join((
            f"#### SOURCE PHENOMENON: {item['node_id']}",
            f"- source_phenomenon_id: {item['node_id']}",
            f"- source_project_id: {item['source_project_id']}",
            "- source_title:",
            _block(item["source_title"]),
            "- title:",
            _block(item["title"]),
            "- definition:",
            _block(item["definition"]),
            "- evidence_ref_ids:",
            _list(item["evidence_ref_ids"]),
        ))
        for item in inputs["source_phenomena"]
    )
    body_lines.extend(("", "## APPROVED SOURCE INTERVIEW RELATIONSHIPS"))
    body_lines.extend(
        _render_source_relation(item, inputs["evidence_by_id"])
        for item in inputs["source_relationships"]
    )
    body = "\n\n".join(body_lines)
    template = PROMPT_PATH.read_text(encoding="utf-8")
    contract = FORMAT_PATH.read_text(encoding="utf-8")
    input_hash = stable_hash({
        "base_input_hash": inputs["base_input_hash"],
        "stage_key": STAGE_KEY,
        "phenomenon_version": phenomenon_result["version"],
        "approved_phenomenon": phenomenon,
        "rendered_inputs": body,
        "prompt_template": template,
        "format_contract": contract,
    })
    prompt = re.sub(
        r"\{\{(RUN_ID|INPUT_HASH|SOURCE_PHASE_VERSION|PHENOMENON_VERSION|INPUT_NODE_COUNT|INPUT_MARKDOWN|FORMAT_CONTRACT)\}\}",
        lambda match: {
            "RUN_ID": run_id,
            "INPUT_HASH": input_hash,
            "SOURCE_PHASE_VERSION": str(inputs["source_phase_version"]),
            "PHENOMENON_VERSION": str(phenomenon_result["version"]),
            "INPUT_NODE_COUNT": str(len(model_nodes)),
            "INPUT_MARKDOWN": body,
            "FORMAT_CONTRACT": contract,
        }[match.group(1)],
        template,
    )
    maximum = max_prompt_chars if max_prompt_chars is not None else SYNTHESIS_FINAL_SYNTHESIS_MAX_PROMPT_CHARS
    report = {
        "source_count": inputs["source_count"],
        "input_node_count": len(model_nodes),
        "source_relationship_count": len(inputs["source_relationships"]),
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
        "phenomenon_version": phenomenon_result["version"],
        "inputs": inputs,
        "model_nodes": model_nodes,
        "size_report": report,
    }


def latest_relationship_result(run_id: str) -> dict[str, Any] | None:
    from app.repositories.synthesis_repository import list_stage_results

    results = [
        item for item in list_stage_results(run_id)
        if item["stage_key"] == STAGE_KEY and item["component_key"] == STAGE_COMPONENT_KEY
    ]
    latest = max(results, key=lambda item: item["version"]) if results else None
    if latest is None:
        return None
    try:
        current = build_relationship_prompt(run_id, enforce_size=False)
        latest["stale"] = (
            latest.get("status") == "stale"
            or latest.get("approval_status") == "stale"
            or latest["input_hash"] != current["input_hash"]
            or latest.get("source_phase_version") != current["source_phase_version"]
        )
    except ValueError:
        latest["stale"] = True
    return latest
