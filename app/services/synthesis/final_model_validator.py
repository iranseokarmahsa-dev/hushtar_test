from __future__ import annotations

from typing import Any

from app.services.synthesis.final_quality_audit import audit_final_model


def validate_final_model(
    model: dict[str, Any],
    *,
    inputs: dict[str, Any],
    phenomenon_result: dict[str, Any],
    relationship_result: dict[str, Any],
) -> dict[str, Any]:
    expected_source_ids = {
        str(source["source_project_id"]) for source in inputs.get("source_rows", [])
    }
    expected_node_ids = {
        str(node["node_id"]) for node in inputs.get("nodes", [])
    } | {str(model.get("central_phenomenon_node_id") or "")}
    expected_evidence_ids = set(inputs.get("evidence_by_id", {}))
    expected_source_item_ids = {
        str(item.get("source_item_id"))
        for component_items in inputs.get("pool_result", {}).get("output_json", {}).get("pools", {}).values()
        for item in component_items
        if item.get("source_item_id")
    }
    expected_category_unit_ids = set(inputs.get("unit_to_node", {}))
    expected_theme_ids = {
        str(node["node_id"]) for node in inputs.get("nodes", [])
        if node.get("node_type") == "theme"
    }
    relationship_output = relationship_result.get("output_json") or {}
    expected_source_relationship_ids = {
        str(value)
        for item in relationship_output.get("relationships", [])
        if item.get("review_status") == "approved"
        for value in item.get("supporting_source_relationship_ids", [])
    }
    expected_edges = {
        str(item["relationship_id"])
        for item in relationship_output.get("relationships", [])
        if item.get("review_status") == "approved"
    }
    actual_edge_relationship_ids = [
        str(edge.get("relationship_id") or "")
        for edge in model.get("edges", [])
    ]
    actual_edges = set(actual_edge_relationship_ids)
    if actual_edges != expected_edges:
        model.setdefault("quality_audit", {}).setdefault("errors", []).append({
            "issue_type": "approved_relationship_coverage_mismatch",
            "missing_relationship_ids": sorted(expected_edges - actual_edges),
            "unknown_relationship_ids": sorted(actual_edges - expected_edges),
        })
        model["quality_audit"]["status"] = "invalid"
    if len(actual_edge_relationship_ids) != len(actual_edges):
        model.setdefault("quality_audit", {}).setdefault("errors", []).append({
            "issue_type": "duplicate_relationship_id",
        })
        model["quality_audit"]["status"] = "invalid"

    phenomenon = phenomenon_result.get("output_json", {}).get("central_phenomenon", {})
    if phenomenon.get("review_status") != "approved" or phenomenon.get("selected") is not True:
        model.setdefault("quality_audit", {}).setdefault("errors", []).append({
            "issue_type": "central_phenomenon_not_selected_and_approved"
        })
        model["quality_audit"]["status"] = "invalid"

    expected_versions = {
        "pool_extraction": inputs.get("pool_version"),
        "category_grouping": inputs.get("phase_five_result", {}).get("source_phase_version"),
        "theme_synthesis": inputs.get("phase_five_result", {}).get("version"),
        "phenomenon_synthesis": phenomenon_result.get("version"),
        "relationship_synthesis": relationship_result.get("version"),
    }
    source_versions = model.get("source_phase_versions", {})
    if any(source_versions.get(key) != value for key, value in expected_versions.items()):
        model.setdefault("quality_audit", {}).setdefault("errors", []).append({
            "issue_type": "source_phase_version_mismatch"
        })
        model["quality_audit"]["status"] = "invalid"
    expected_interview_id_list = [
        str(source["snapshot"].get("interview_id") or source["source_project_id"])
        for source in inputs.get("source_rows", [])
    ]
    expected_interview_ids = set(expected_interview_id_list)
    actual_interview_ids = [str(value) for value in model.get("source_interview_ids", [])]
    if (
        len(expected_interview_id_list) != len(expected_interview_ids)
        or len(actual_interview_ids) != len(expected_interview_ids)
        or len(actual_interview_ids) != len(set(actual_interview_ids))
        or set(actual_interview_ids) != expected_interview_ids
    ):
        model.setdefault("quality_audit", {}).setdefault("errors", []).append({
            "issue_type": "source_interview_coverage_mismatch"
        })
        model["quality_audit"]["status"] = "invalid"
    unresolved_contradictions = [
        item for item in model.get("source_traceability", {}).get("contradictions", [])
        if item.get("review_status") not in {"approved", "dismissed"}
    ]
    if unresolved_contradictions:
        model.setdefault("quality_audit", {}).setdefault("errors", []).append({
            "issue_type": "unresolved_contradictions",
            "contradiction_ids": [
                item.get("contradiction_id") for item in unresolved_contradictions
            ],
        })
        model["quality_audit"]["status"] = "invalid"

    evidence_index = model.get("evidence_index", {})
    evidence_source_items = inputs.get("evidence_to_source_items", {})
    source_relationships = {
        str(item["source_relationship_id"]): item
        for item in inputs.get("source_relationships", [])
    }
    source_interview_ids = {
        str(source["source_project_id"]): str(
            source.get("snapshot", {}).get("interview_id")
            or source["source_project_id"]
        )
        for source in inputs.get("source_rows", [])
    }
    paragraph_text_by_source: dict[tuple[str, str], str] = {}
    for source in inputs.get("source_rows", []):
        project_id = str(source.get("source_project_id") or "")
        for paragraph in (source.get("snapshot") or {}).get("paragraphs", []):
            if isinstance(paragraph, dict) and paragraph.get("paragraph_id"):
                paragraph_text_by_source[
                    (project_id, str(paragraph["paragraph_id"]))
                ] = str(paragraph.get("text") or "")
    for evidence_id, indexed_evidence in evidence_index.items():
        source_evidence = inputs.get("evidence_by_id", {}).get(evidence_id)
        if source_evidence is None:
            continue
        if (
            indexed_evidence.get("evidence_text") != source_evidence.get("quote")
            or indexed_evidence.get("source_project_id") != source_evidence.get("source_project_id")
            or indexed_evidence.get("paragraph_id") != source_evidence.get("paragraph_id")
        ):
            model.setdefault("quality_audit", {}).setdefault("errors", []).append({
                "issue_type": "evidence_index_snapshot_mismatch",
                "evidence_ref_id": evidence_id,
            })
        project_id = str(indexed_evidence.get("source_project_id") or "")
        paragraph_id = str(indexed_evidence.get("paragraph_id") or "")
        paragraph_text = paragraph_text_by_source.get((project_id, paragraph_id))
        quote = str(indexed_evidence.get("evidence_text") or "")
        if (
            paragraph_text is None
            or not quote
            or quote not in paragraph_text
            or indexed_evidence.get("source_interview_id")
            != source_interview_ids.get(project_id)
        ):
            model.setdefault("quality_audit", {}).setdefault("errors", []).append({
                "issue_type": "evidence_not_traceable_to_source_interview",
                "evidence_ref_id": evidence_id,
            })
        expected_owners = set(evidence_source_items.get(evidence_id, set()))
        if set(indexed_evidence.get("source_item_ids", [])) != expected_owners:
            model.setdefault("quality_audit", {}).setdefault("errors", []).append({
                "issue_type": "evidence_source_item_ownership_mismatch",
                "evidence_ref_id": evidence_id,
            })

    for node in model.get("nodes", []):
        source_item_ids = set(node.get("source_item_ids", []))
        for evidence_id in node.get("evidence_ref_ids", []):
            owners = set(evidence_source_items.get(evidence_id, set()))
            if not owners.intersection(source_item_ids):
                model.setdefault("quality_audit", {}).setdefault("errors", []).append({
                    "issue_type": "node_evidence_ownership_mismatch",
                    "node_id": node.get("node_id"),
                    "evidence_ref_id": evidence_id,
                })

    for edge in model.get("edges", []):
        supported_evidence = {
            str(evidence_id)
            for source_relationship_id in edge.get("supporting_source_relationship_ids", [])
            for evidence_id in source_relationships.get(source_relationship_id, {}).get("evidence_ref_ids", [])
        }
        source_item_ids = set(edge.get("source_item_ids", []))
        supported_evidence.update(
            evidence_id
            for evidence_id in edge.get("evidence_ref_ids", [])
            if set(evidence_source_items.get(evidence_id, set())).intersection(source_item_ids)
        )
        if set(edge.get("evidence_ref_ids", [])) - supported_evidence:
            model.setdefault("quality_audit", {}).setdefault("errors", []).append({
                "issue_type": "edge_evidence_ownership_mismatch",
                "edge_id": edge.get("edge_id"),
            })

    pre_audit_errors = list(model.get("quality_audit", {}).get("errors", []))
    audit = audit_final_model(
        model,
        expected_source_ids=expected_source_ids,
        expected_node_ids=expected_node_ids,
        expected_evidence_ids=expected_evidence_ids,
        expected_source_item_ids=expected_source_item_ids,
        expected_category_unit_ids=expected_category_unit_ids,
        expected_theme_ids=expected_theme_ids,
        expected_source_relationship_ids=expected_source_relationship_ids,
    )
    all_errors = pre_audit_errors + audit["errors"]
    model["quality_audit"]["errors"] = all_errors
    model["quality_audit"]["unresolved_issue_count"] = len(all_errors)
    model["quality_audit"]["status"] = "valid" if not all_errors else "invalid"
    return {
        "valid": not all_errors,
        "errors": all_errors,
        "quality_audit": model["quality_audit"],
    }
