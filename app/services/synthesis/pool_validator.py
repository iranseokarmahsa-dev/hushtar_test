from __future__ import annotations

from typing import Any

from app.services.synthesis.pool_builder_service import COMPONENT_KEYS


def validate_pool_output(pool: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    pools = pool.get("pools") if isinstance(pool.get("pools"), dict) else {}
    categories = pool.get("category_index") if isinstance(pool.get("category_index"), dict) else {}
    evidence = pool.get("evidence") if isinstance(pool.get("evidence"), list) else []
    metadata = pool.get("metadata") if isinstance(pool.get("metadata"), dict) else {}
    coverage = pool.get("source_coverage") if isinstance(pool.get("source_coverage"), list) else []
    coverage_by_project = {row.get("source_project_id"): row for row in coverage}

    for component_key in COMPONENT_KEYS:
        if not isinstance(pools.get(component_key), list):
            errors.append({"issue_type": "pool_not_array", "component_key": component_key})

    source_count = metadata.get("source_count")
    valid_snapshot_count = metadata.get("valid_snapshot_count")
    registered_source_count = metadata.get("registered_source_count")
    processed_source_count = metadata.get("processed_source_count")
    if (
        valid_snapshot_count != registered_source_count
        or processed_source_count != registered_source_count
        or source_count != registered_source_count
    ):
        errors.append({
            "issue_type": "snapshot_count_mismatch",
            "registered_source_count": registered_source_count,
            "valid_snapshot_count": valid_snapshot_count,
            "processed_source_count": processed_source_count,
            "source_count": source_count,
        })
    covered_ids = {row.get("source_project_id") for row in coverage}
    if len(covered_ids) != registered_source_count or any(not row.get("processed") for row in coverage):
        errors.append({"issue_type": "source_coverage_incomplete"})

    item_ids: set[str] = set()
    category_ref_map: dict[tuple[str, str], set[str]] = {}
    for component_key in COMPONENT_KEYS:
        for item in pools.get(component_key, []) if isinstance(pools.get(component_key), list) else []:
            item_id = item.get("source_item_id")
            if item_id in item_ids:
                errors.append({"issue_type": "duplicate_source_item_id", "source_item_id": item_id})
            item_ids.add(item_id)
            source_row = coverage_by_project.get(item.get("source_project_id"))
            if source_row and item.get("source_interview_id") != source_row.get("source_interview_id"):
                errors.append({"issue_type": "item_interview_source_mismatch", "source_item_id": item_id})
            if component_key != "central_phenomenon":
                project_id = item.get("source_project_id")
                category_id = item.get("source_category_id")
                if category_id not in categories.get(project_id, {}):
                    errors.append({"issue_type": "invalid_item_category_reference", "source_project_id": project_id, "category_id": category_id})
                category_ref_map.setdefault((project_id, category_id), set()).add(component_key)
            for reference in item.get("evidence_refs", []):
                if reference.get("evidence_ref_id") not in {entry.get("evidence_ref_id") for entry in evidence}:
                    errors.append({"issue_type": "missing_evidence_reference", "source_item_id": item_id})

    evidence_by_id = {entry.get("evidence_ref_id"): entry for entry in evidence}
    for entry in evidence:
        source_id = entry.get("source_project_id")
        if not entry.get("valid"):
            errors.append({"issue_type": "invalid_evidence", "evidence_ref_id": entry.get("evidence_ref_id"), "source_project_id": source_id})
        if not source_id or not entry.get("evidence_ref_id"):
            errors.append({"issue_type": "evidence_missing_source_or_id"})
        source_row = coverage_by_project.get(source_id)
        if source_row and entry.get("source_interview_id") != source_row.get("source_interview_id"):
            errors.append({"issue_type": "evidence_interview_source_mismatch", "evidence_ref_id": entry.get("evidence_ref_id")})
    for component_key in COMPONENT_KEYS:
        for item in pools.get(component_key, []) if isinstance(pools.get(component_key), list) else []:
            for reference in item.get("evidence_refs", []):
                evidence_item = evidence_by_id.get(reference.get("evidence_ref_id"))
                if evidence_item and evidence_item.get("source_project_id") != item.get("source_project_id"):
                    errors.append({"issue_type": "cross_source_evidence_reference", "source_item_id": item.get("source_item_id")})
    for relationship in pool.get("relationships", []):
        for reference in relationship.get("evidence_refs", []):
            evidence_item = evidence_by_id.get(reference.get("evidence_ref_id"))
            if evidence_item is None:
                errors.append({"issue_type": "missing_relationship_evidence_reference", "source_relationship_id": relationship.get("source_relationship_id")})
            elif evidence_item.get("source_project_id") != relationship.get("source_project_id"):
                errors.append({"issue_type": "cross_source_relationship_evidence", "source_relationship_id": relationship.get("source_relationship_id")})

    for project_id, category_ids in categories.items():
        assigned_ids = {category_id for source_id, category_id in category_ref_map if source_id == project_id}
        unassigned_ids = {
            item.get("category_id") for item in pool.get("unassigned_categories", [])
            if item.get("source_project_id") == project_id
        }
        excluded_ids = {
            item.get("source_category_id") for item in pool.get("excluded_categories", [])
            if item.get("source_project_id") == project_id
        }
        if set(category_ids) != assigned_ids | unassigned_ids | excluded_ids:
            errors.append({"issue_type": "category_coverage_mismatch", "source_project_id": project_id})

    for issue in pool.get("reference_issues", []):
        if issue.get("decision_status") != "approved":
            errors.append({"issue_type": "unresolved_category_reference", "source_project_id": issue.get("source_project_id"), "category_id": issue.get("category_id")})
    for issue in pool.get("incomplete_classifications", []):
        if issue.get("decision_status") != "approved":
            errors.append({"issue_type": "incomplete_classification", "source_project_id": issue.get("source_project_id"), "model_path": issue.get("model_path")})
    for issue in pool.get("extraction_errors", []):
        if issue.get("issue_type") != "invalid_evidence":
            errors.append({
                "issue_type": "extraction_error",
                "source_project_id": issue.get("source_project_id"),
                "cause": issue.get("issue_type"),
            })

    unresolved = [item for item in pool.get("review_items", []) if item.get("decision_status") != "approved"]
    if unresolved:
        warnings.append({"issue_type": "review_required", "count": len(unresolved)})

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checked_source_count": registered_source_count,
        "checked_category_count": sum(len(items) for items in categories.values()),
        "checked_evidence_count": len(evidence),
    }
