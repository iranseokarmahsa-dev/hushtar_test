from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.repositories.synthesis_repository import list_stage_results, mark_stage_results_stale, save_stage_result
from app.services.synthesis.normalization_service import stable_hash, stable_source_item_id
from app.services.synthesis.pool_builder_service import COMPONENT_KEYS
from app.services.synthesis.pool_validator import validate_pool_output

VALID_DECISIONS = {"assign", "exclude", "keep_multiple", "resolve_reference"}


def _latest_result(run_id: str) -> dict[str, Any] | None:
    results = [
        item for item in list_stage_results(run_id)
        if item["stage_key"] == "pool_extraction" and item["component_key"] == "interview_pools"
    ]
    return max(results, key=lambda item: item["version"]) if results else None


def record_pool_review_decision(
    run_id: str,
    *,
    category_id: str,
    source_project_id: str,
    review_item_id: str | None = None,
    decision: str,
    reason: str,
    approved: bool,
    pool_version: int,
    component_key: str | None = None,
    replacement_category_id: str | None = None,
) -> dict[str, Any]:
    if decision not in VALID_DECISIONS:
        raise ValueError("Unsupported Pool Review decision.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A reason is required for every Pool Review decision.")
    if decision in {"assign", "resolve_reference"} and component_key not in COMPONENT_KEYS[:-1]:
        raise ValueError("A valid paradigm component is required for this decision.")
    if decision == "resolve_reference" and not replacement_category_id:
        raise ValueError("A replacement category ID is required to resolve an invalid reference.")

    latest = _latest_result(run_id)
    if latest is None:
        raise ValueError("Pool extraction must be run before Pool Review.")
    if latest["version"] != pool_version:
        raise ValueError("Pool version is stale; reload the latest Pool before reviewing.")

    output = deepcopy(latest["output_json"])
    review_item = next((
        item for item in output.get("review_items", [])
        if item.get("source_project_id") == source_project_id
        and item.get("category_id", "") == category_id
        and (review_item_id is None or item.get("review_item_id") == review_item_id)
        and item.get("decision_status") != "approved"
        and _decision_target_matches(item, decision)
    ), None)
    if review_item is None:
        raise ValueError("No pending review item matches this source and category.")

    recorded_at = datetime.now(timezone.utc).isoformat()
    decision_record = {
        "review_item_id": review_item["review_item_id"],
        "source_project_id": source_project_id,
        "category_id": category_id,
        "decision": decision,
        "component_key": component_key,
        "replacement_category_id": replacement_category_id,
        "reason": reason.strip(),
        "recorded_at": recorded_at,
        "decided_at": recorded_at,
        "approval_status": "approved" if approved else "pending",
        "pool_version": pool_version,
    }

    if approved:
        _apply_approved_decision(output, review_item, decision_record)
        review_item["decision_status"] = "approved"
    else:
        review_item["decision_status"] = "pending"
    review_item["decision"] = decision_record

    edits = deepcopy(latest["researcher_edits_json"] or {})
    decisions = list(edits.get("decisions") or [])
    decisions.append(decision_record)
    edits["decisions"] = decisions

    metadata = output.setdefault("metadata", {})
    metadata["assigned_count"] = len({
        (item.get("source_project_id"), item.get("source_category_id"))
        for key in COMPONENT_KEYS[:-1] for item in output["pools"].get(key, [])
    })
    metadata["independent_count"] = len(output.get("unassigned_categories", []))
    metadata["excluded_count"] = len(output.get("excluded_categories", []))
    metadata["needs_review_count"] = sum(item.get("decision_status") != "approved" for item in output.get("review_items", []))
    decision_input_hash = stable_hash({
        "source_input_hash": metadata.get("source_input_hash"),
        "decisions": decisions,
    })
    output["version"] = latest["version"] + 1
    output["input_hash"] = decision_input_hash
    output["validation_report"] = validate_pool_output(output)
    saved = save_stage_result(
        run_id,
        "pool_extraction",
        "interview_pools",
        version=output["version"],
        status="review_required" if metadata["needs_review_count"] or not output["validation_report"]["valid"] else "ready",
        input_hash=decision_input_hash,
        output_json=output,
        validation_report_json=output["validation_report"],
        researcher_edits_json=edits,
        approval_status="pending" if metadata["needs_review_count"] or not output["validation_report"]["valid"] else "approved",
    )
    mark_stage_results_stale(
        run_id,
        ("category_grouping", "theme_synthesis", "phenomenon_synthesis", "relationship_synthesis", "final_model"),
    )
    return saved["output_json"]


def _decision_target_matches(item: dict[str, Any], decision: str) -> bool:
    issue_type = item.get("issue_type")
    if decision == "assign":
        return issue_type in {"unassigned_category", "multiple_components"}
    if decision == "exclude":
        return issue_type in {
            "unassigned_category",
            "multiple_components",
            "invalid_category_reference",
            "invalid_central_phenomenon_category_reference",
            "invalid_relationship_category_reference",
            "classification_without_category_reference",
        }
    if decision == "keep_multiple":
        return issue_type == "multiple_components"
    if decision == "resolve_reference":
        return issue_type == "invalid_category_reference"
    return False


def _apply_approved_decision(
    output: dict[str, Any],
    review_item: dict[str, Any],
    decision_record: dict[str, Any],
) -> None:
    decision = decision_record["decision"]
    category_id = decision_record["category_id"]
    source_project_id = decision_record["source_project_id"]
    component_key = decision_record["component_key"]

    if decision == "assign":
        if review_item["issue_type"] == "unassigned_category":
            record = next((
                item for item in output["unassigned_categories"]
                if item.get("source_project_id") == source_project_id and item.get("category_id") == category_id
            ), None)
            if record is None:
                raise ValueError("Unassigned category record no longer exists.")
            source_item = deepcopy(record["category"])
            source_item["component_key"] = component_key
            source_item["source_item_id"] = stable_source_item_id(source_project_id, component_key, category_id)
            output["pools"][component_key].append(source_item)
            output["unassigned_categories"].remove(record)
        elif review_item["issue_type"] == "multiple_components":
            for key in COMPONENT_KEYS[:-1]:
                output["pools"][key] = [
                    item for item in output["pools"].get(key, [])
                    if not (item.get("source_project_id") == source_project_id and item.get("source_category_id") == category_id and key != component_key)
                ]
    elif decision == "exclude":
        is_category = category_id in output.get("category_index", {}).get(source_project_id, {})
        if is_category:
            output["unassigned_categories"] = [
                item for item in output.get("unassigned_categories", [])
                if not (item.get("source_project_id") == source_project_id and item.get("category_id") == category_id)
            ]
            output.setdefault("excluded_categories", []).append({
                "source_project_id": source_project_id,
                "source_category_id": category_id,
                "issue_type": review_item.get("issue_type"),
                "reason": decision_record["reason"],
                "source_item_id": review_item.get("detail", {}).get("source_item_id") if isinstance(review_item.get("detail"), dict) else None,
            })
            for key in COMPONENT_KEYS[:-1]:
                output["pools"][key] = [
                    item for item in output["pools"].get(key, [])
                    if not (item.get("source_project_id") == source_project_id and item.get("source_category_id") == category_id)
                ]
        else:
            output.setdefault("excluded_references", []).append({
                "source_project_id": source_project_id,
                "category_id": category_id or None,
                "issue_type": review_item.get("issue_type"),
                "reason": decision_record["reason"],
                "review_item_id": review_item.get("review_item_id"),
            })
        for field in ("reference_issues", "incomplete_classifications"):
            for issue in output.get(field, []):
                if _matches_review_issue(issue, review_item):
                    issue["decision_status"] = "approved"
                    issue["decision"] = decision_record
    elif decision == "resolve_reference":
        replacement_id = decision_record.get("replacement_category_id")
        replacement = output.get("category_index", {}).get(source_project_id, {}).get(replacement_id)
        if not replacement:
            raise ValueError("Replacement category does not exist in this source snapshot.")
        raw_reference = review_item.get("detail", {}).get("raw_model_item", {})
        references = [{
            "component_key": component_key,
            "model_path": review_item.get("detail", {}).get("model_path"),
            "raw_model_item": raw_reference,
        }]
        from app.services.synthesis.pool_builder_service import build_category_source_item
        category = output.get("category_index", {}).get(source_project_id, {}).get(replacement_id)
        item = build_category_source_item(
            {"source_project_id": source_project_id, "source_title": next((row.get("source_title") for row in output.get("source_coverage", []) if row.get("source_project_id") == source_project_id), ""), "snapshot": _snapshot_metadata_for_source(output, source_project_id), "evidence": _evidence_for_reference(output, source_project_id, review_item.get("detail", {}).get("model_path"))},
            category,
            component_key,
            references,
            [
                entry["evidence_ref_id"] for entry in output.get("evidence", [])
                if entry["source_project_id"] == source_project_id
                and any(provenance.get("path", "").startswith(review_item.get("detail", {}).get("model_path", "")) for provenance in entry.get("provenance", []))
            ],
        )
        if not any(existing.get("source_item_id") == item["source_item_id"] for existing in output["pools"][component_key]):
            output["pools"][component_key].append(item)
        replacement_unassigned = next((
            row for row in output.get("unassigned_categories", [])
            if row.get("source_project_id") == source_project_id and row.get("category_id") == replacement_id
        ), None)
        if replacement_unassigned:
            output["unassigned_categories"].remove(replacement_unassigned)
            for candidate in output.get("review_items", []):
                if candidate.get("source_project_id") == source_project_id and candidate.get("category_id") == replacement_id and candidate.get("issue_type") == "unassigned_category":
                    candidate["decision_status"] = "approved"
                    candidate["decision"] = decision_record
        for candidate in output.get("review_items", []):
            if candidate.get("source_project_id") == source_project_id and candidate.get("category_id") == replacement_id and candidate.get("issue_type") == "multiple_components":
                candidate["decision_status"] = "approved"
                candidate["decision"] = decision_record
        for issue in output.get("reference_issues", []):
            if _matches_review_issue(issue, review_item):
                issue["decision_status"] = "approved"
                issue["decision"] = decision_record
    elif decision == "keep_multiple":
        return


def _snapshot_metadata_for_source(output: dict[str, Any], source_project_id: str) -> dict[str, Any]:
    return output.get("source_metadata", {}).get(source_project_id, {"project_id": source_project_id})


def _matches_review_issue(issue: dict[str, Any], review_item: dict[str, Any]) -> bool:
    detail = review_item.get("detail") or {}
    return all(
        issue.get(key) == detail.get(key)
        for key in (
            "source_project_id",
            "category_id",
            "issue_type",
            "model_path",
            "relationship_id",
            "field",
            "component_key",
        )
    )


def _evidence_for_reference(output: dict[str, Any], source_project_id: str, model_path: str | None) -> list[dict[str, Any]]:
    prefix = model_path or ""
    evidence_ids = {
        item["evidence_ref_id"] for item in output.get("evidence", [])
        if item.get("source_project_id") == source_project_id
        and any(provenance.get("path", "").startswith(prefix) for provenance in item.get("provenance", []))
    }
    return [item for item in output.get("evidence", []) if item.get("evidence_ref_id") in evidence_ids]
