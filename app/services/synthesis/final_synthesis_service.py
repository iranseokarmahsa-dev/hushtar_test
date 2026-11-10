from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.repositories.synthesis_repository import (
    get_next_stage_version,
    get_synthesis_run,
    list_stage_results,
    mark_stage_results_stale,
    save_stage_result,
    update_run_status,
)
from app.services.synthesis.final_synthesis_inputs import (
    FINAL_MODEL_COMPONENT_KEY,
    FINAL_MODEL_STAGE_KEY,
    PHENOMENON_STAGE_KEY,
    RELATIONSHIP_STAGE_KEY,
    final_phenomenon_node_id,
    load_final_synthesis_inputs,
)
from app.services.synthesis.final_synthesis_markdown_parser import (
    FinalSynthesisMarkdownError,
    parse_phenomenon_markdown,
    parse_relationship_markdown,
)
from app.services.synthesis.final_synthesis_validator import (
    validate_phenomenon_result,
    validate_relationship_result,
)
from app.services.synthesis.final_model_builder import build_final_model_data
from app.services.synthesis.final_model_validator import validate_final_model
from app.services.synthesis.normalization_service import stable_hash
from app.services.synthesis.phenomenon_prompt_builder import (
    FinalSynthesisPromptSizeLimitError,
    STAGE_COMPONENT_KEY as PHENOMENON_COMPONENT_KEY,
    build_phenomenon_prompt,
    latest_phenomenon_result,
)
from app.services.synthesis.relationship_prompt_builder import (
    STAGE_COMPONENT_KEY as RELATIONSHIP_COMPONENT_KEY,
    build_relationship_prompt,
    latest_relationship_result,
)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validation_text(stage_label: str, report: dict[str, Any]) -> str:
    lines = [f"# گزارش اعتبارسنجی {stage_label}", "", f"- معتبر: {'بله' if report.get('valid') else 'خیر'}", "", "## خطاها"]
    errors = report.get("errors") or []
    if not errors:
        lines.append("- موردی نیست.")
    for index, error in enumerate(errors, start=1):
        details = ", ".join(
            f"{key}={value}" for key, value in error.items() if key != "issue_type"
        )
        lines.append(f"- {index}. {error.get('issue_type', 'validation_error')}" + (f": {details}" if details else ""))
    return "\n".join(lines)


def _latest(run_id: str, stage_key: str, component_key: str) -> dict[str, Any] | None:
    rows = [
        row for row in list_stage_results(run_id)
        if row["stage_key"] == stage_key and row["component_key"] == component_key
    ]
    return max(rows, key=lambda row: row["version"]) if rows else None


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _with_phenomenon_ids(parsed: dict[str, Any], run_id: str) -> dict[str, Any]:
    result = deepcopy(parsed)
    central = result["central_phenomenon"]
    central["ai_phenomenon_id"] = central["phenomenon_id"]
    central["phenomenon_id"] = final_phenomenon_node_id(run_id)
    central["review_status"] = "pending"
    central["selected"] = True
    for alternative in result["alternatives"]:
        alternative["ai_alternative_id"] = alternative["alternative_id"]
        alternative["alternative_id"] = _id("alternative")
        alternative["review_status"] = "pending"
        alternative["selected"] = False
    for contradiction in result["contradictions"]:
        contradiction["ai_contradiction_id"] = contradiction["contradiction_id"]
        contradiction["contradiction_id"] = _id("phenomenon_contradiction")
        contradiction["review_status"] = "pending"
    result.pop("metadata", None)
    result.pop("quality_audit", None)
    return result


def submit_phenomenon_output(run_id: str, raw_output_text: str) -> dict[str, Any]:
    current = build_phenomenon_prompt(run_id)
    parsed = None
    try:
        parsed = parse_phenomenon_markdown(raw_output_text)
        report = validate_phenomenon_result(
            parsed,
            run_id=run_id,
            input_hash=current["input_hash"],
            source_phase_version=current["source_phase_version"],
            inputs=current["inputs"],
            raw_output_text=raw_output_text,
        )
    except FinalSynthesisMarkdownError as error:
        report = {
            "valid": False,
            "errors": [{"issue_type": "markdown_parse_error", "message": message} for message in error.errors],
            "warnings": [],
        }
    if parsed is not None and report["valid"]:
        parsed = _with_phenomenon_ids(parsed, run_id)
    version = get_next_stage_version(run_id, PHENOMENON_STAGE_KEY, PHENOMENON_COMPONENT_KEY)
    saved = save_stage_result(
        run_id,
        PHENOMENON_STAGE_KEY,
        PHENOMENON_COMPONENT_KEY,
        version=version,
        status="ready_for_researcher_review" if report["valid"] else "invalid",
        input_hash=current["input_hash"],
        prompt_text=current["prompt_text"],
        raw_output_text=raw_output_text,
        output_json=parsed if parsed is not None else {},
        validation_report_json=report,
        validation_report_text=_validation_text("پدیده محوری", report),
        source_phase_version=current["source_phase_version"],
        researcher_edits_json={"decisions": []},
        approval_status="pending",
    )
    mark_stage_results_stale(run_id, (RELATIONSHIP_STAGE_KEY, FINAL_MODEL_STAGE_KEY))
    if report["valid"]:
        update_run_status(run_id, status="in_progress", current_stage=PHENOMENON_STAGE_KEY)
    return {
        "stage_result_id": saved["stage_result_id"],
        "run_id": run_id,
        "version": version,
        "status": saved["status"],
        "valid": report["valid"],
        "parsed_result": parsed,
        "validation_report": report,
        "validation_report_text": saved["validation_report_text"],
        "input_hash": current["input_hash"],
        "source_phase_version": current["source_phase_version"],
        "size_report": current["size_report"],
    }


def _phenomenon_view(run_id: str) -> dict[str, Any]:
    latest = latest_phenomenon_result(run_id)
    try:
        prompt = build_phenomenon_prompt(run_id)
    except (ValueError, FinalSynthesisPromptSizeLimitError) as error:
        return {
            "prompt": None,
            "latest": latest,
            "page_error": str(error),
            "size_error_report": getattr(error, "report", None),
        }
    return {
        "prompt": prompt,
        "latest": latest,
        "page_error": None,
        "size_error_report": None,
    }


def phenomenon_page_data(run_id: str) -> dict[str, Any]:
    return _phenomenon_view(run_id)


def phenomenon_correction_prompt(run_id: str) -> str:
    latest = latest_phenomenon_result(run_id)
    if latest is None:
        raise ValueError("هنوز خروجی پدیده محوری ثبت نشده است.")
    if latest.get("stale"):
        raise ValueError("ورودی پدیده محوری منقضی شده است؛ خروجی اصلاحی برای ورودی قدیمی مجاز نیست.")
    return (
        "# PHENOMENON SYNTHESIS CORRECTION REQUEST\n\n"
        "سند Markdown قبلی را فقط با توجه به خطاهای اعتبارسنجی زیر اصلاح کن. ورودی، شناسه‌ها و قرارداد را تغییر نده؛ "
        "خروجی کامل Markdown بده.\n\n## VALIDATION ERRORS\n\n"
        f"{latest.get('validation_report_text') or ''}"
    )


def _record_result(
    run_id: str,
    latest: dict[str, Any],
    *,
    stage_key: str,
    component_key: str,
    output: dict[str, Any],
    edits: dict[str, Any],
    decision: str,
    targets: list[str],
    before: Any,
    after: Any,
    reason: str,
    downstream: tuple[str, ...],
    approve: bool = False,
) -> dict[str, Any]:
    edits.setdefault("decisions", []).append({
        "operation": decision,
        "target_ids": targets,
        "before": before,
        "after": after,
        "reason": reason.strip(),
        "created_at": _now(),
    })
    version = get_next_stage_version(run_id, stage_key, component_key)
    saved = save_stage_result(
        run_id,
        stage_key,
        component_key,
        version=version,
        status="approved" if approve else "researcher_review",
        input_hash=latest["input_hash"],
        prompt_text=latest.get("prompt_text", ""),
        raw_output_text=latest.get("raw_output_text", ""),
        output_json=output,
        validation_report_json=latest.get("validation_report_json", {}),
        validation_report_text=latest.get("validation_report_text", ""),
        source_phase_version=latest.get("source_phase_version"),
        researcher_edits_json=edits,
        approval_status="approved" if approve else "pending",
    )
    mark_stage_results_stale(run_id, downstream)
    update_run_status(
        run_id,
        status="in_progress",
        current_stage=f"{stage_key}_approved" if approve else stage_key,
    )
    return saved


PHENOMENON_DECISIONS = {
    "edit_phenomenon",
    "approve_phenomenon",
    "select_alternative",
    "edit_alternative",
    "dismiss_alternative",
    "edit_contradiction",
    "approve_contradiction",
    "dismiss_contradiction",
    "approve_final",
}


def record_phenomenon_review_decision(
    run_id: str,
    *,
    decision: str,
    reason: str,
    target_id: str | None = None,
    title: str | None = None,
    definition: str | None = None,
    selection_reason: str | None = None,
    description: str | None = None,
    reasoning: str | None = None,
) -> dict[str, Any]:
    if decision not in PHENOMENON_DECISIONS:
        raise ValueError("تصمیم بازبینی پدیده محوری شناخته‌شده نیست.")
    if not reason.strip():
        raise ValueError("دلیل تصمیم پژوهشگر الزامی است.")
    latest = latest_phenomenon_result(run_id)
    if latest is None or latest.get("status") == "invalid" or not latest.get("validation_report_json", {}).get("valid"):
        raise ValueError("خروجی معتبر پدیده محوری برای بازبینی وجود ندارد.")
    if latest.get("stale"):
        raise ValueError("خروجی پدیده محوری منقضی شده است؛ ورودی را دوباره تولید کنید.")
    current = build_phenomenon_prompt(run_id)
    if current["input_hash"] != latest["input_hash"]:
        raise ValueError("ورودی فاز پنجم پس از دریافت خروجی پدیده محوری تغییر کرده است.")
    output = deepcopy(latest.get("output_json") or {})
    central = output.get("central_phenomenon")
    alternatives = output.get("alternatives", [])
    contradictions = output.get("contradictions", [])
    if not isinstance(central, dict):
        raise ValueError("پدیده محوری اصلی برای بازبینی وجود ندارد.")
    edits = deepcopy(latest.get("researcher_edits_json") or {"decisions": []})
    before: Any = None
    after: Any = None
    targets: list[str] = []
    approve = decision == "approve_final"

    if decision in {"edit_phenomenon", "approve_phenomenon"}:
        before = deepcopy(central)
        if decision == "edit_phenomenon":
            for key, value in (
                ("title", title),
                ("definition", definition),
                ("selection_reason", selection_reason),
            ):
                if value is not None:
                    if not value.strip():
                        raise ValueError(f"{key} نمی‌تواند خالی باشد.")
                    central[key] = value.strip()
            central["review_status"] = "pending"
        else:
            central["review_status"] = "approved"
        after = deepcopy(central)
        targets = [str(central["phenomenon_id"])]

    elif decision == "select_alternative":
        alternative = next((item for item in alternatives if item.get("alternative_id") == target_id), None)
        if alternative is None:
            raise ValueError("تفسیر جایگزین انتخاب‌شده پیدا نشد.")
        before = {"central_phenomenon": deepcopy(central), "alternative": deepcopy(alternative)}
        previous_central = {
            "alternative_id": _id("alternative"),
            "ai_alternative_id": central.get("ai_phenomenon_id"),
            "title": central["title"],
            "definition": central["definition"],
            "supporting_node_ids": central.get("supporting_node_ids", []),
            "supporting_source_item_ids": central.get("supporting_source_item_ids", []),
            "supporting_category_unit_ids": central.get("supporting_category_unit_ids", []),
            "supporting_theme_ids": central.get("supporting_theme_ids", []),
            "supporting_source_phenomenon_ids": central.get("supporting_source_phenomenon_ids", []),
            "evidence_ref_ids": central.get("evidence_ref_ids", []),
            "strengths": [],
            "limitations": [],
            "selection_comparison": "پژوهشگر این تفسیر را به‌عنوان گزینه اصلی جایگزین کرد.",
            "confidence": central.get("confidence", "0"),
            "needs_researcher_review": "true",
            "review_status": "pending",
            "selected": False,
        }
        alternatives.remove(alternative)
        alternatives.append(previous_central)
        central.update({
            "ai_phenomenon_id": alternative.get("ai_alternative_id"),
            "title": alternative["title"],
            "definition": alternative["definition"],
            "selection_reason": alternative.get("selection_comparison", ""),
            "supporting_node_ids": alternative.get("supporting_node_ids", []),
            "supporting_source_item_ids": alternative.get("supporting_source_item_ids", []),
            "supporting_category_unit_ids": alternative.get("supporting_category_unit_ids", []),
            "supporting_theme_ids": alternative.get("supporting_theme_ids", []),
            "supporting_source_phenomenon_ids": alternative.get("supporting_source_phenomenon_ids", []),
            "evidence_ref_ids": alternative.get("evidence_ref_ids", []),
            "confidence": alternative.get("confidence", "0"),
            "needs_researcher_review": alternative.get("needs_researcher_review", "true"),
            "review_status": "pending",
            "selected": True,
        })
        targets = [str(target_id)]
        after = {"central_phenomenon": deepcopy(central), "alternatives": deepcopy(alternatives)}

    elif decision in {"edit_alternative", "dismiss_alternative"}:
        alternative = next((item for item in alternatives if item.get("alternative_id") == target_id), None)
        if alternative is None:
            raise ValueError("تفسیر جایگزین موردنظر پیدا نشد.")
        before = deepcopy(alternative)
        if decision == "dismiss_alternative":
            alternative["review_status"] = "dismissed"
        else:
            for key, value in (("title", title), ("definition", definition), ("selection_comparison", selection_reason)):
                if value is not None:
                    if not value.strip():
                        raise ValueError(f"{key} نمی‌تواند خالی باشد.")
                    alternative[key] = value.strip()
            alternative["review_status"] = "pending"
        after = deepcopy(alternative)
        targets = [str(target_id)]

    elif decision in {"edit_contradiction", "approve_contradiction", "dismiss_contradiction"}:
        contradiction = next(
            (item for item in contradictions if item.get("contradiction_id") == target_id),
            None,
        )
        if contradiction is None:
            raise ValueError("تناقض موردنظر پیدا نشد.")
        before = deepcopy(contradiction)
        if decision == "edit_contradiction":
            for key, value in (("description", description), ("reasoning", reasoning)):
                if value is not None:
                    if not value.strip():
                        raise ValueError(f"{key} نمی‌تواند خالی باشد.")
                    contradiction[key] = value.strip()
            contradiction["review_status"] = "pending"
        else:
            contradiction["review_status"] = "approved" if decision == "approve_contradiction" else "dismissed"
        after = deepcopy(contradiction)
        targets = [str(target_id)]

    elif decision == "approve_final":
        unresolved = []
        if central.get("review_status") != "approved":
            unresolved.append("central_phenomenon")
        unresolved.extend(
            f"alternative:{item.get('alternative_id')}"
            for item in alternatives
            if item.get("review_status") not in {"dismissed", "selected"}
        )
        unresolved.extend(
            f"contradiction:{item.get('contradiction_id')}"
            for item in contradictions
            if item.get("review_status") not in {"approved", "dismissed"}
        )
        if unresolved:
            raise ValueError("موارد پدیده محوری تعیین تکلیف نشده‌اند: " + "، ".join(unresolved))
        after = {"approval_status": "approved"}

    saved = _record_result(
        run_id,
        latest,
        stage_key=PHENOMENON_STAGE_KEY,
        component_key=PHENOMENON_COMPONENT_KEY,
        output=output,
        edits=edits,
        decision=decision,
        targets=targets,
        before=before,
        after=after,
        reason=reason,
        downstream=(RELATIONSHIP_STAGE_KEY, FINAL_MODEL_STAGE_KEY),
        approve=approve,
    )
    return saved


def _with_relationship_ids(parsed: dict[str, Any]) -> dict[str, Any]:
    output = deepcopy(parsed)
    for relation in output["relationships"]:
        relation["ai_relationship_id"] = relation["relationship_id"]
        relation["relationship_id"] = _id("relationship")
        relation["review_status"] = "pending"
    for item in output["unlinked_nodes"]:
        item["review_status"] = "pending"
    for contradiction in output["contradictions"]:
        contradiction["ai_contradiction_id"] = contradiction["contradiction_id"]
        contradiction["contradiction_id"] = _id("relationship_contradiction")
        contradiction["review_status"] = "pending"
    output.pop("metadata", None)
    output.pop("quality_audit", None)
    return output


def submit_relationship_output(run_id: str, raw_output_text: str) -> dict[str, Any]:
    current = build_relationship_prompt(run_id)
    parsed = None
    try:
        parsed = parse_relationship_markdown(raw_output_text)
        report = validate_relationship_result(
            parsed,
            run_id=run_id,
            input_hash=current["input_hash"],
            source_phase_version=current["source_phase_version"],
            phenomenon_version=current["phenomenon_version"],
            inputs=current["inputs"],
            model_nodes=current["model_nodes"],
            raw_output_text=raw_output_text,
        )
    except FinalSynthesisMarkdownError as error:
        report = {
            "valid": False,
            "errors": [{"issue_type": "markdown_parse_error", "message": message} for message in error.errors],
            "warnings": [],
        }
    if parsed is not None and report["valid"]:
        parsed = _with_relationship_ids(parsed)
    version = get_next_stage_version(run_id, RELATIONSHIP_STAGE_KEY, RELATIONSHIP_COMPONENT_KEY)
    saved = save_stage_result(
        run_id,
        RELATIONSHIP_STAGE_KEY,
        RELATIONSHIP_COMPONENT_KEY,
        version=version,
        status="ready_for_researcher_review" if report["valid"] else "invalid",
        input_hash=current["input_hash"],
        prompt_text=current["prompt_text"],
        raw_output_text=raw_output_text,
        output_json=parsed if parsed is not None else {},
        validation_report_json=report,
        validation_report_text=_validation_text("روابط", report),
        source_phase_version=current["source_phase_version"],
        researcher_edits_json={"decisions": []},
        approval_status="pending",
    )
    mark_stage_results_stale(run_id, (FINAL_MODEL_STAGE_KEY,))
    if report["valid"]:
        update_run_status(run_id, status="in_progress", current_stage=RELATIONSHIP_STAGE_KEY)
    return {
        "stage_result_id": saved["stage_result_id"],
        "run_id": run_id,
        "version": version,
        "status": saved["status"],
        "valid": report["valid"],
        "parsed_result": parsed,
        "validation_report": report,
        "validation_report_text": saved["validation_report_text"],
        "input_hash": current["input_hash"],
        "source_phase_version": current["source_phase_version"],
        "phenomenon_version": current["phenomenon_version"],
        "size_report": current["size_report"],
    }


def relationship_page_data(run_id: str) -> dict[str, Any]:
    latest = latest_relationship_result(run_id)
    try:
        prompt = build_relationship_prompt(run_id)
    except (ValueError, FinalSynthesisPromptSizeLimitError) as error:
        try:
            current = build_relationship_prompt(run_id, enforce_size=False)
            model_nodes = current["model_nodes"]
        except ValueError:
            model_nodes = []
        return {
            "prompt": None,
            "latest": latest,
            "model_nodes": model_nodes,
            "page_error": str(error),
            "size_error_report": getattr(error, "report", None),
        }
    return {
        "prompt": prompt,
        "latest": latest,
        "model_nodes": prompt["model_nodes"],
        "page_error": None,
        "size_error_report": None,
    }


def relationship_correction_prompt(run_id: str) -> str:
    latest = latest_relationship_result(run_id)
    if latest is None:
        raise ValueError("هنوز خروجی روابط ثبت نشده است.")
    if latest.get("stale"):
        raise ValueError("ورودی روابط منقضی شده است؛ خروجی اصلاحی برای ورودی قدیمی مجاز نیست.")
    return (
        "# RELATIONSHIP SYNTHESIS CORRECTION REQUEST\n\n"
        "سند Markdown قبلی را فقط با توجه به خطاهای اعتبارسنجی زیر اصلاح کن. ورودی، شناسه‌ها و قرارداد را تغییر نده؛ "
        "خروجی کامل Markdown بده.\n\n## VALIDATION ERRORS\n\n"
        f"{latest.get('validation_report_text') or ''}"
    )


RELATIONSHIP_DECISIONS = {
    "edit_relationship",
    "approve_relationship",
    "reject_relationship",
    "add_relationship",
    "approve_unlinked",
    "edit_contradiction",
    "approve_contradiction",
    "dismiss_contradiction",
    "approve_final",
}


def _sync_unlinked_nodes(output: dict[str, Any], model_nodes: list[dict[str, Any]]) -> None:
    linked = {
        str(endpoint)
        for relation in output.get("relationships", [])
        if relation.get("review_status") != "dismissed"
        for endpoint in (relation.get("source_node_id"), relation.get("target_node_id"))
    }
    existing = {str(item["node_id"]): item for item in output.get("unlinked_nodes", [])}
    output["unlinked_nodes"] = [
        existing.get(
            str(node["node_id"]),
            {
                "node_id": str(node["node_id"]),
                "component_key": str(node["component_key"]),
                "reason": "پس از بازبینی روابط، نود پیوند تأییدشده‌ای ندارد.",
                "needs_researcher_review": "true",
                "review_status": "pending",
            },
        )
        for node in model_nodes
        if str(node["node_id"]) not in linked
    ]


def _validate_manual_relationship(
    relation: dict[str, Any],
    *,
    inputs: dict[str, Any],
    model_nodes: list[dict[str, Any]],
) -> None:
    node_ids = {str(item["node_id"]) for item in model_nodes}
    if relation.get("source_node_id") not in node_ids or relation.get("target_node_id") not in node_ids:
        raise ValueError("مبدأ و مقصد باید نودهای معتبر مدل باشند.")
    if relation.get("source_node_id") == relation.get("target_node_id"):
        raise ValueError("مبدأ و مقصد رابطه باید متفاوت باشند.")
    if relation.get("relationship_type") not in {"direct", "interpretive", "hypothetical"}:
        raise ValueError("نوع رابطه باید direct، interpretive یا hypothetical باشد.")
    if relation.get("direction") not in {"source_to_target", "target_to_source", "bidirectional"}:
        raise ValueError("جهت رابطه معتبر نیست.")
    if not str(relation.get("description") or "").strip() or not str(relation.get("reasoning") or "").strip():
        raise ValueError("شرح و دلیل رابطه الزامی است.")
    indexes = {
        "supporting_source_relationship_ids": {
            str(item["source_relationship_id"]) for item in inputs["source_relationships"]
        },
        "supporting_source_phenomenon_ids": {
            str(item["node_id"]) for item in inputs["source_phenomena"]
        },
        "supporting_source_item_ids": set(inputs["source_item_to_node"]),
        "supporting_category_unit_ids": set(inputs["unit_to_node"]),
        "evidence_ref_ids": set(inputs["evidence_by_id"]),
    }
    refs = {
        field: set(relation.get(field) or [])
        for field in indexes
    }
    for field, values in refs.items():
        if values - indexes[field]:
            raise ValueError(f"ارجاع نامعتبر در {field}.")
    if not any(refs[field] for field in refs):
        raise ValueError("رابطه باید دست‌کم یک ارجاع پشتیبان داشته باشد.")
    if relation["relationship_type"] == "direct" and not refs["supporting_source_relationship_ids"]:
        raise ValueError("رابطه direct باید به یک رابطه تأییدشده مصاحبه ارجاع دهد.")
    endpoint_ids = {str(relation["source_node_id"]), str(relation["target_node_id"])}
    endpoint_nodes = {
        str(item["node_id"]): item
        for item in model_nodes
        if str(item["node_id"]) in endpoint_ids
    }
    endpoint_items = {
        str(source_item_id)
        for node in endpoint_nodes.values()
        for source_item_id in node.get("source_item_ids", [])
    }
    endpoint_categories = {
        str(category_unit_id)
        for node in endpoint_nodes.values()
        for category_unit_id in node.get("category_unit_ids", [])
    }
    if refs["supporting_source_item_ids"] - endpoint_items:
        raise ValueError("Source Item رابطه به نودهای مبدأ یا مقصد تعلق ندارد.")
    if refs["supporting_category_unit_ids"] - endpoint_categories:
        raise ValueError("Category Unit رابطه به نودهای مبدأ یا مقصد تعلق ندارد.")
    owned_evidence = set()
    for source_relation in inputs["source_relationships"]:
        if source_relation["source_relationship_id"] in refs["supporting_source_relationship_ids"]:
            if {
                str(source_relation["source_node_id"]),
                str(source_relation["target_node_id"]),
            } != endpoint_ids:
                raise ValueError("رابطه مستقل به همان دو نود مبدأ و مقصد تعلق ندارد.")
            owned_evidence.update(source_relation.get("evidence_ref_ids", []))
    for phenomenon in inputs["source_phenomena"]:
        if phenomenon["node_id"] in refs["supporting_source_phenomenon_ids"]:
            owned_evidence.update(phenomenon.get("evidence_ref_ids", []))
    for evidence_id, source_item_ids in inputs["evidence_to_source_items"].items():
        if set(source_item_ids).intersection(refs["supporting_source_item_ids"]):
            owned_evidence.add(evidence_id)
    for node in inputs["nodes"]:
        if set(node.get("category_unit_ids", [])).intersection(refs["supporting_category_unit_ids"]):
            owned_evidence.update(node.get("evidence_ref_ids", []))
    if refs["evidence_ref_ids"] - owned_evidence:
        raise ValueError("شواهد رابطه به ارجاع‌های پشتیبان تعلق ندارند.")


def record_relationship_review_decision(
    run_id: str,
    *,
    decision: str,
    reason: str,
    target_id: str | None = None,
    source_node_id: str | None = None,
    target_node_id: str | None = None,
    relationship_type: str | None = None,
    direction: str | None = None,
    description: str | None = None,
    reasoning: str | None = None,
    supporting_source_relationship_ids: list[str] | None = None,
    supporting_source_phenomenon_ids: list[str] | None = None,
    supporting_source_item_ids: list[str] | None = None,
    supporting_category_unit_ids: list[str] | None = None,
    evidence_ref_ids: list[str] | None = None,
) -> dict[str, Any]:
    if decision not in RELATIONSHIP_DECISIONS:
        raise ValueError("تصمیم بازبینی رابطه شناخته‌شده نیست.")
    if not reason.strip():
        raise ValueError("دلیل تصمیم پژوهشگر الزامی است.")
    latest = latest_relationship_result(run_id)
    if latest is None or latest.get("status") == "invalid" or not latest.get("validation_report_json", {}).get("valid"):
        raise ValueError("خروجی معتبر روابط برای بازبینی وجود ندارد.")
    if latest.get("stale"):
        raise ValueError("خروجی روابط منقضی شده است؛ ورودی را دوباره تولید کنید.")
    current = build_relationship_prompt(run_id)
    if current["input_hash"] != latest["input_hash"]:
        raise ValueError("ورودی روابط پس از دریافت خروجی تغییر کرده است.")
    output = deepcopy(latest.get("output_json") or {})
    relationships = output.get("relationships", [])
    contradictions = output.get("contradictions", [])
    unlinked = output.get("unlinked_nodes", [])
    edits = deepcopy(latest.get("researcher_edits_json") or {"decisions": []})
    before: Any = None
    after: Any = None
    targets: list[str] = []
    approve = decision == "approve_final"

    if decision in {"edit_relationship", "approve_relationship", "reject_relationship"}:
        relation = next((item for item in relationships if item.get("relationship_id") == target_id), None)
        if relation is None:
            raise ValueError("رابطه موردنظر پیدا نشد.")
        before = deepcopy(relation)
        if decision == "approve_relationship":
            relation["review_status"] = "approved"
        elif decision == "reject_relationship":
            relation["review_status"] = "dismissed"
        else:
            for key, value in (
                ("source_node_id", source_node_id),
                ("target_node_id", target_node_id),
                ("relationship_type", relationship_type),
                ("direction", direction),
                ("description", description),
                ("reasoning", reasoning),
                ("supporting_source_relationship_ids", supporting_source_relationship_ids),
                ("supporting_source_phenomenon_ids", supporting_source_phenomenon_ids),
                ("supporting_source_item_ids", supporting_source_item_ids),
                ("supporting_category_unit_ids", supporting_category_unit_ids),
                ("evidence_ref_ids", evidence_ref_ids),
            ):
                if value is not None:
                    relation[key] = value.strip() if isinstance(value, str) else value
            relation["review_status"] = "pending"
            _validate_manual_relationship(
                relation,
                inputs=current["inputs"],
                model_nodes=current["model_nodes"],
            )
        after = deepcopy(relation)
        targets = [str(target_id)]

    elif decision == "add_relationship":
        relation = {
            "relationship_id": _id("relationship"),
            "ai_relationship_id": None,
            "source_node_id": source_node_id or "",
            "target_node_id": target_node_id or "",
            "relationship_type": relationship_type or "",
            "direction": direction or "",
            "description": description or "",
            "reasoning": reasoning or "",
            "supporting_source_relationship_ids": supporting_source_relationship_ids or [],
            "supporting_source_phenomenon_ids": supporting_source_phenomenon_ids or [],
            "supporting_source_item_ids": supporting_source_item_ids or [],
            "supporting_category_unit_ids": supporting_category_unit_ids or [],
            "evidence_ref_ids": evidence_ref_ids or [],
            "confidence": "1.0",
            "needs_researcher_review": "true",
            "review_status": "pending",
        }
        _validate_manual_relationship(
            relation,
            inputs=current["inputs"],
            model_nodes=current["model_nodes"],
        )
        relationships.append(relation)
        before = None
        after = deepcopy(relation)
        targets = [relation["relationship_id"]]

    elif decision == "approve_unlinked":
        item = next((node for node in unlinked if node.get("node_id") == target_id), None)
        if item is None:
            raise ValueError("نود بدون رابطه موردنظر پیدا نشد.")
        before = deepcopy(item)
        item["review_status"] = "approved"
        after = deepcopy(item)
        targets = [str(target_id)]

    elif decision in {"edit_contradiction", "approve_contradiction", "dismiss_contradiction"}:
        contradiction = next(
            (item for item in contradictions if item.get("contradiction_id") == target_id),
            None,
        )
        if contradiction is None:
            raise ValueError("تناقض موردنظر پیدا نشد.")
        before = deepcopy(contradiction)
        if decision == "edit_contradiction":
            for key, value in (("description", description), ("reasoning", reasoning)):
                if value is not None:
                    if not value.strip():
                        raise ValueError(f"{key} نمی‌تواند خالی باشد.")
                    contradiction[key] = value.strip()
            contradiction["review_status"] = "pending"
        else:
            contradiction["review_status"] = (
                "approved" if decision == "approve_contradiction" else "dismissed"
            )
        after = deepcopy(contradiction)
        targets = [str(target_id)]

    elif decision == "approve_final":
        unresolved = [
            f"relationship:{item.get('relationship_id')}"
            for item in relationships
            if item.get("review_status") not in {"approved", "dismissed"}
        ]
        unresolved.extend(
            f"unlinked:{item.get('node_id')}"
            for item in unlinked
            if item.get("review_status") != "approved"
        )
        unresolved.extend(
            f"contradiction:{item.get('contradiction_id')}"
            for item in contradictions
            if item.get("review_status") not in {"approved", "dismissed"}
        )
        if unresolved:
            raise ValueError("موارد روابط تعیین تکلیف نشده‌اند: " + "، ".join(unresolved))
        after = {"approval_status": "approved"}

    _sync_unlinked_nodes(output, current["model_nodes"])
    before = before if before is not None else {}
    saved = _record_result(
        run_id,
        latest,
        stage_key=RELATIONSHIP_STAGE_KEY,
        component_key=RELATIONSHIP_COMPONENT_KEY,
        output=output,
        edits=edits,
        decision=decision,
        targets=targets,
        before=before,
        after=after,
        reason=reason,
        downstream=(FINAL_MODEL_STAGE_KEY,),
        approve=approve,
    )
    return saved


def _model_input(
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    phenomenon = latest_phenomenon_result(run_id)
    relationships = latest_relationship_result(run_id)
    if phenomenon is None or phenomenon.get("stale") or phenomenon.get("approval_status") != "approved":
        raise ValueError("پدیده محوری تأییدشده و به‌روز لازم است.")
    if relationships is None or relationships.get("stale") or relationships.get("approval_status") != "approved":
        raise ValueError("روابط تأییدشده و به‌روز لازم است.")
    inputs = load_final_synthesis_inputs(run_id)
    current_relations = build_relationship_prompt(run_id)
    if current_relations["input_hash"] != relationships["input_hash"]:
        raise ValueError("ورودی روابط پس از تأیید تغییر کرده است.")
    model = build_final_model_data(
        inputs=inputs,
        phenomenon_result=phenomenon,
        relationship_result=relationships,
    )
    validation = validate_final_model(
        model,
        inputs=inputs,
        phenomenon_result=phenomenon,
        relationship_result=relationships,
    )
    if not validation["valid"]:
        raise ValueError(
            "ممیزی مدل نهایی ناموفق است: "
            + _validation_text("مدل نهایی", validation)
        )
    return inputs, phenomenon, relationships, model


def build_final_model(run_id: str) -> dict[str, Any]:
    run = get_synthesis_run(run_id)
    if run is None:
        raise ValueError("اجرای تلفیقی پیدا نشد.")
    inputs, phenomenon, relationships, model = _model_input(run_id)
    input_hash = model["input_hash"]
    existing = _latest(run_id, FINAL_MODEL_STAGE_KEY, FINAL_MODEL_COMPONENT_KEY)
    if existing is not None and existing.get("input_hash") == input_hash and existing.get("approval_status") == "approved":
        return existing
    version = get_next_stage_version(run_id, FINAL_MODEL_STAGE_KEY, FINAL_MODEL_COMPONENT_KEY)
    model["model_version"] = version
    model["created_at"] = _now()
    model["updated_at"] = model["created_at"]
    saved = save_stage_result(
        run_id,
        FINAL_MODEL_STAGE_KEY,
        FINAL_MODEL_COMPONENT_KEY,
        version=version,
        status="approved",
        input_hash=input_hash,
        prompt_text="",
        raw_output_text="",
        output_json=model,
        validation_report_json={
            "valid": True,
            "checked_node_count": len(model["nodes"]),
            "checked_edge_count": len(model["edges"]),
            "quality_audit": model["quality_audit"],
        },
        validation_report_text="مدل نهایی از نسخه‌های تأییدشده ساخته و ممیزی شد.",
        source_phase_version=inputs["phase_five_result"]["version"],
        researcher_edits_json={"operation": "build_final_model", "created_at": model["created_at"]},
        approval_status="approved",
    )
    update_run_status(
        run_id,
        status="completed",
        current_stage=FINAL_MODEL_STAGE_KEY,
        completed_at=_now(),
    )
    return saved


def latest_final_model(run_id: str) -> dict[str, Any] | None:
    latest = _latest(run_id, FINAL_MODEL_STAGE_KEY, FINAL_MODEL_COMPONENT_KEY)
    if latest is None:
        return None
    try:
        _, _, _, current_model = _model_input(run_id)
        latest["stale"] = (
            latest.get("status") == "stale"
            or latest.get("approval_status") == "stale"
            or latest.get("input_hash") != current_model["input_hash"]
        )
    except ValueError:
        latest["stale"] = True
    if latest["stale"]:
        quality_audit = (latest.get("output_json") or {}).get("quality_audit")
        if isinstance(quality_audit, dict):
            quality_audit["version_status"] = "stale"
            quality_audit["status"] = "stale"
    return latest


def final_model_page_data(run_id: str) -> dict[str, Any]:
    try:
        inputs, phenomenon, relationships, model = _model_input(run_id)
        can_build = True
        page_error = None
    except ValueError as error:
        inputs = phenomenon = relationships = None
        model = None
        can_build = False
        page_error = str(error)
    latest = latest_final_model(run_id)
    latest_is_current = bool(latest and not latest.get("stale"))
    return {
        "run_id": run_id,
        "inputs": inputs,
        "phenomenon": phenomenon,
        "relationships": relationships,
        "latest": latest,
        "model": model,
        "latest_is_current": latest_is_current,
        "can_build": can_build,
        "input_hash": model.get("input_hash", "") if model else "",
        "page_error": page_error,
    }
