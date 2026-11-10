from __future__ import annotations

from typing import Any

from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS

COMPONENTS = (
    ("causal_conditions", "شرایط علّی"),
    ("central_phenomenon", "پدیده محوری"),
    ("contextual_conditions", "شرایط زمینه‌ای"),
    ("intervening_conditions", "شرایط مداخله‌گر"),
    ("action_interaction_strategies", "راهبردهای کنش و تعامل"),
    ("consequences", "پیامدها"),
)


def audit_final_model(
    model: dict[str, Any],
    *,
    expected_source_ids: set[str],
    expected_node_ids: set[str],
    expected_evidence_ids: set[str],
    expected_source_item_ids: set[str],
    expected_category_unit_ids: set[str],
    expected_theme_ids: set[str],
    expected_source_relationship_ids: set[str],
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    nodes = model.get("nodes") if isinstance(model.get("nodes"), list) else []
    edges = model.get("edges") if isinstance(model.get("edges"), list) else []
    components = model.get("components") if isinstance(model.get("components"), list) else []
    evidence_index = model.get("evidence_index")
    evidence_index = evidence_index if isinstance(evidence_index, dict) else {}
    source_traceability = model.get("source_traceability")
    source_traceability = source_traceability if isinstance(source_traceability, dict) else {}

    source_ids = [str(item.get("source_project_id") or "") for item in source_traceability.get("sources", [])]
    if len(source_ids) != len(set(source_ids)) or set(source_ids) != expected_source_ids:
        errors.append({"issue_type": "source_coverage_mismatch"})
    if len(source_ids) != model.get("quality_audit", {}).get("input_source_count"):
        errors.append({"issue_type": "source_count_mismatch"})

    node_ids = [str(node.get("node_id") or "") for node in nodes]
    if not all(node_ids) or len(node_ids) != len(set(node_ids)):
        errors.append({"issue_type": "invalid_or_duplicate_node_id"})
    if set(node_ids) != expected_node_ids:
        errors.append({
            "issue_type": "node_coverage_mismatch",
            "missing_node_ids": sorted(expected_node_ids - set(node_ids)),
            "unknown_node_ids": sorted(set(node_ids) - expected_node_ids),
        })
    node_by_id = {str(node.get("node_id")): node for node in nodes}
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        component_key = node.get("component_key")
        allowed_components = set(POOL_COMPONENT_KEYS) | {"central_phenomenon"}
        if component_key not in allowed_components:
            errors.append({"issue_type": "invalid_node_component", "node_id": node_id})
        if node.get("researcher_approved") is not True:
            errors.append({"issue_type": "unapproved_node", "node_id": node_id})
        if not str(node.get("title") or "").strip() or not str(node.get("definition") or "").strip():
            errors.append({"issue_type": "missing_node_content", "node_id": node_id})
        if set(node.get("source_project_ids") or []) - expected_source_ids:
            errors.append({"issue_type": "unknown_node_source", "node_id": node_id})
        if set(node.get("source_item_ids") or []) - expected_source_item_ids:
            errors.append({"issue_type": "unknown_node_source_item", "node_id": node_id})
        if set(node.get("category_unit_ids") or []) - expected_category_unit_ids:
            errors.append({"issue_type": "unknown_node_category_unit", "node_id": node_id})
        if set(node.get("evidence_ref_ids") or []) - expected_evidence_ids:
            errors.append({"issue_type": "unknown_node_evidence", "node_id": node_id})
        if node.get("node_type") == "theme" and node.get("theme_id") not in expected_theme_ids:
            errors.append({"issue_type": "unknown_theme_id", "node_id": node_id})
        if node.get("node_type") not in {"theme", "independent_category", "central_phenomenon"}:
            errors.append({"issue_type": "invalid_node_type", "node_id": node_id})

    central_nodes = [node for node in nodes if node.get("node_type") == "central_phenomenon"]
    if len(central_nodes) != 1 or central_nodes[0].get("component_key") != "central_phenomenon":
        errors.append({"issue_type": "central_phenomenon_cardinality"})

    expected_component_order = [key for key, _title in COMPONENTS]
    component_order = [str(item.get("component_key") or "") for item in components]
    if component_order != expected_component_order:
        errors.append({"issue_type": "invalid_component_order"})
    component_members: list[str] = []
    for expected_index, component in enumerate(components):
        key = component.get("component_key")
        if component.get("display_order") != expected_index:
            errors.append({"issue_type": "invalid_component_display_order", "component_key": key})
        member_ids = [str(value) for value in component.get("node_ids", [])]
        component_members.extend(member_ids)
        for node_id in member_ids:
            if node_id not in node_by_id or node_by_id[node_id].get("component_key") != key:
                errors.append({"issue_type": "component_node_mismatch", "component_key": key, "node_id": node_id})
    if len(component_members) != len(set(component_members)) or set(component_members) != set(node_ids):
        errors.append({"issue_type": "component_node_coverage_mismatch"})

    edge_ids = [str(edge.get("edge_id") or "") for edge in edges]
    if not all(edge_ids) or len(edge_ids) != len(set(edge_ids)):
        errors.append({"issue_type": "invalid_or_duplicate_edge_id"})
    canonical_edges: set[tuple[str, str, str, str]] = set()
    linked_nodes: set[str] = set()
    edge_counts = {"direct": 0, "interpretive": 0, "hypothetical": 0}
    for edge in edges:
        edge_id = str(edge.get("edge_id") or "")
        source_id = str(edge.get("source_node_id") or "")
        target_id = str(edge.get("target_node_id") or "")
        relation_type = edge.get("relationship_type")
        direction = edge.get("direction")
        if source_id not in node_by_id or target_id not in node_by_id or source_id == target_id:
            errors.append({"issue_type": "invalid_edge_endpoint", "edge_id": edge_id})
        else:
            linked_nodes.update((source_id, target_id))
        if relation_type not in edge_counts:
            errors.append({"issue_type": "invalid_edge_type", "edge_id": edge_id})
        else:
            edge_counts[relation_type] += 1
        if direction not in {"source_to_target", "target_to_source", "bidirectional"}:
            errors.append({"issue_type": "invalid_edge_direction", "edge_id": edge_id})
        if edge.get("researcher_approved") is not True:
            errors.append({"issue_type": "unapproved_edge", "edge_id": edge_id})
        if edge.get("source_phase") != "relationship_synthesis":
            errors.append({"issue_type": "edge_from_unapproved_source_phase", "edge_id": edge_id})
        if set(edge.get("supporting_source_relationship_ids") or []) - expected_source_relationship_ids:
            errors.append({"issue_type": "unknown_source_relationship", "edge_id": edge_id})
        if set(edge.get("source_item_ids") or []) - expected_source_item_ids:
            errors.append({"issue_type": "unknown_edge_source_item", "edge_id": edge_id})
        if set(edge.get("evidence_ref_ids") or []) - expected_evidence_ids:
            errors.append({"issue_type": "unknown_edge_evidence", "edge_id": edge_id})
        if not edge.get("relationship_id"):
            errors.append({"issue_type": "missing_edge_relationship_id", "edge_id": edge_id})
        if not edge.get("description") or not edge.get("evidence_ref_ids"):
            errors.append({"issue_type": "missing_edge_description_or_evidence", "edge_id": edge_id})
        source, target = source_id, target_id
        if direction == "target_to_source":
            source, target = target, source
        if direction == "bidirectional" and target < source:
            source, target = target, source
        canonical = (source, target, str(relation_type), str(direction))
        if canonical in canonical_edges:
            errors.append({"issue_type": "duplicate_edge", "edge_id": edge_id})
        canonical_edges.add(canonical)
    if set(model.get("edge_source_relationship_ids") or []) != {
        str(value) for edge in edges for value in edge.get("supporting_source_relationship_ids", [])
    }:
        errors.append({"issue_type": "edge_source_relationship_coverage_mismatch"})

    evidence_ids = set(evidence_index)
    if evidence_ids != expected_evidence_ids:
        errors.append({
            "issue_type": "evidence_coverage_mismatch",
            "missing_evidence_ids": sorted(expected_evidence_ids - evidence_ids),
            "unknown_evidence_ids": sorted(evidence_ids - expected_evidence_ids),
        })
    for evidence_id, evidence in evidence_index.items():
        if evidence.get("evidence_ref_id") != evidence_id:
            errors.append({"issue_type": "evidence_index_key_mismatch", "evidence_ref_id": evidence_id})
        if evidence.get("valid") is not True or not evidence.get("evidence_text"):
            errors.append({"issue_type": "invalid_or_missing_evidence_text", "evidence_ref_id": evidence_id})
        if evidence.get("source_project_id") not in expected_source_ids:
            errors.append({"issue_type": "unknown_evidence_source", "evidence_ref_id": evidence_id})
        if set(evidence.get("source_item_ids") or []) - expected_source_item_ids:
            errors.append({"issue_type": "unknown_evidence_source_item", "evidence_ref_id": evidence_id})
        if set(evidence.get("source_concept_ids") or []) - {
            str(value) for node in nodes for value in node.get("source_concept_ids", [])
        }:
            errors.append({"issue_type": "unknown_evidence_concept", "evidence_ref_id": evidence_id})

    audit = model.get("quality_audit") if isinstance(model.get("quality_audit"), dict) else {}
    status = "valid" if not errors else "invalid"
    audit.update({
        "input_source_count": len(source_ids),
        "approved_theme_count": sum(node.get("node_type") == "theme" for node in nodes),
        "independent_category_unit_count": sum(
            node.get("node_type") == "independent_category" for node in nodes
        ),
        "node_count": len(nodes),
        "direct_relationship_count": edge_counts["direct"],
        "interpretive_relationship_count": edge_counts["interpretive"],
        "hypothetical_relationship_count": edge_counts["hypothetical"],
        "edge_count": len(edges),
        "evidence_count": len(evidence_index),
        "invalid_reference_count": sum(
            issue["issue_type"].startswith(("unknown_", "invalid_", "missing_"))
            for issue in errors
        ),
        "unlinked_node_ids": sorted(set(node_ids) - linked_nodes),
        "unlinked_node_count": len(set(node_ids) - linked_nodes),
        "unresolved_issue_count": len(errors),
        "unresolved_contradiction_count": sum(
            item.get("review_status") not in {"approved", "dismissed"}
            for item in source_traceability.get("contradictions", [])
        ),
        "unresolved_contradictions": [
            item for item in source_traceability.get("contradictions", [])
            if item.get("review_status") not in {"approved", "dismissed"}
        ],
        "version_status": "current",
        "status": status,
        "errors": errors,
    })
    model["quality_audit"] = audit
    return {"valid": not errors, "errors": errors, "quality_audit": audit}
