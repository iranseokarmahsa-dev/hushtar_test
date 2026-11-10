from __future__ import annotations

from typing import Any

from app.services.synthesis.final_quality_audit import COMPONENTS
from app.services.synthesis.normalization_service import stable_hash
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS


def build_final_model_data(
    *,
    inputs: dict[str, Any],
    phenomenon_result: dict[str, Any],
    relationship_result: dict[str, Any],
) -> dict[str, Any]:
    source_rows = inputs["source_rows"]
    source_ids = {str(source["source_project_id"]) for source in source_rows}
    source_item_by_id = {
        str(item["source_item_id"]): item
        for source_node in inputs["nodes"]
        for item in source_node.get("source_items", [])
        if item.get("source_item_id")
    }
    source_item_by_id.update({
        str(item["source_item_id"]): item
        for item in inputs["pool_result"]["output_json"].get("pools", {}).get("central_phenomenon", [])
        if item.get("source_item_id")
    })
    source_project_by_item = {
        source_item_id: str(item.get("source_project_id") or "")
        for source_item_id, item in source_item_by_id.items()
    }
    nodes: list[dict[str, Any]] = []
    for source_node in inputs["nodes"]:
        member_items = [
            source_item_by_id[item_id]
            for item_id in source_node.get("source_item_ids", [])
            if item_id in source_item_by_id
        ]
        node_type = (
            "independent_category"
            if source_node.get("node_type") == "category_unit"
            else "theme"
        )
        nodes.append({
            "node_id": str(source_node["node_id"]),
            "node_type": node_type,
            "component_key": str(source_node["component_key"]),
            "title": str(source_node["title"]),
            "definition": str(source_node["definition"]),
            "theme_id": str(source_node["node_id"]) if node_type == "theme" else None,
            "category_unit_ids": list(dict.fromkeys(source_node.get("category_unit_ids", []))),
            "source_item_ids": list(dict.fromkeys(source_node.get("source_item_ids", []))),
            "source_project_ids": sorted({
                source_project_by_item[item_id]
                for item_id in source_node.get("source_item_ids", [])
                if source_project_by_item.get(item_id) in source_ids
            }),
            "source_concept_ids": sorted({
                str(concept_id)
                for item in member_items
                for concept_id in item.get("source_concept_ids", [])
                if concept_id
            }),
            "source_subcategory_ids": sorted({
                str(subcategory_id)
                for item in member_items
                for subcategory_id in item.get("source_subcategory_ids", [])
                if subcategory_id
            }),
            "evidence_ref_ids": list(dict.fromkeys(source_node.get("evidence_ref_ids", []))),
            "researcher_approved": source_node.get("approval_status") == "approved",
            "source_phase": source_node.get("source_phase"),
            "source_phase_version": source_node.get("source_phase_version"),
            "traceability": {
                "source_group_ids": source_node.get("source_ids", []),
                "category_unit_ids": source_node.get("category_unit_ids", []),
            },
        })

    phenomenon = phenomenon_result["output_json"]["central_phenomenon"]
    source_phenomena_by_id = {
        str(item["node_id"]): item for item in inputs["source_phenomena"]
    }
    phenomenon_source_ids = [
        str(value) for value in phenomenon.get("supporting_source_phenomenon_ids", [])
    ]
    selected_sources = [
        source_phenomena_by_id[source_id]
        for source_id in phenomenon_source_ids
        if source_id in source_phenomena_by_id
    ]
    central_id = str(phenomenon["phenomenon_id"])
    nodes.append({
        "node_id": central_id,
        "node_type": "central_phenomenon",
        "component_key": "central_phenomenon",
        "title": str(phenomenon["title"]),
        "definition": str(phenomenon["definition"]),
        "selection_reason": str(phenomenon.get("selection_reason") or ""),
        "theme_id": None,
        "category_unit_ids": list(dict.fromkeys(phenomenon.get("supporting_category_unit_ids", []))),
        "source_item_ids": list(dict.fromkeys(phenomenon.get("supporting_source_item_ids", []))),
        "source_project_ids": sorted({
            str(item["source_project_id"]) for item in selected_sources
        }),
        "source_concept_ids": sorted({
            str(concept_id)
            for source_id in phenomenon_source_ids
            for source in [source_phenomena_by_id.get(source_id, {})]
            for concept_id in source.get("supporting_concept_ids", [])
            if concept_id
        }),
        "source_subcategory_ids": [],
        "evidence_ref_ids": list(dict.fromkeys(phenomenon.get("evidence_ref_ids", []))),
        "researcher_approved": phenomenon.get("review_status") == "approved" and phenomenon.get("selected") is True,
        "source_phase": "phenomenon_synthesis",
        "source_phase_version": phenomenon_result["version"],
        "traceability": {
            "source_phenomenon_ids": phenomenon_source_ids,
            "alternative_decisions": [
                {
                    "alternative_id": item.get("alternative_id"),
                    "ai_alternative_id": item.get("ai_alternative_id"),
                    "title": item.get("title"),
                    "review_status": item.get("review_status"),
                    "selected": item.get("selected") is True,
                }
                for item in phenomenon_result["output_json"].get("alternatives", [])
            ],
            "researcher_decisions": phenomenon_result.get("researcher_edits_json", {}).get("decisions", []),
        },
    })

    relation_output = relationship_result.get("output_json") or {}
    edges = []
    for relation in relation_output.get("relationships", []):
        if relation.get("review_status") != "approved":
            continue
        relation_id = str(relation["relationship_id"])
        edges.append({
            "edge_id": f"fedge_{stable_hash([inputs['run']['run_id'], relation_id])[:24]}",
            "relationship_id": relation_id,
            "source_node_id": str(relation["source_node_id"]),
            "target_node_id": str(relation["target_node_id"]),
            "relationship_type": str(relation["relationship_type"]),
            "direction": str(relation["direction"]),
            "description": str(relation["description"]),
            "reasoning": str(relation["reasoning"]),
            "supporting_source_relationship_ids": list(dict.fromkeys(
                relation.get("supporting_source_relationship_ids", [])
            )),
            "source_item_ids": list(dict.fromkeys(relation.get("supporting_source_item_ids", []))),
            "evidence_ref_ids": list(dict.fromkeys(relation.get("evidence_ref_ids", []))),
            "researcher_approved": True,
            "source_phase": "relationship_synthesis",
            "source_phase_version": relationship_result["version"],
            "traceability": {
                "source_phenomenon_ids": relation.get("supporting_source_phenomenon_ids", []),
                "category_unit_ids": relation.get("supporting_category_unit_ids", []),
            },
        })

    evidence_index = {}
    for evidence_id, evidence in inputs["evidence_by_id"].items():
        source_item_ids = list(dict.fromkeys(
            inputs["evidence_to_source_items"].get(evidence_id, set())
        ))
        source_items = [source_item_by_id[item_id] for item_id in source_item_ids if item_id in source_item_by_id]
        evidence_index[str(evidence_id)] = {
            "evidence_ref_id": str(evidence_id),
            "source_project_id": str(evidence.get("source_project_id") or ""),
            "source_interview_id": str(evidence.get("source_interview_id") or ""),
            "source_title": str(evidence.get("source_title") or ""),
            "source_item_id": source_item_ids[0] if source_item_ids else None,
            "source_item_ids": source_item_ids,
            "source_concept_ids": sorted({
                str(value) for item in source_items
                for value in item.get("source_concept_ids", []) if value
            }),
            "source_subcategory_ids": sorted({
                str(value) for item in source_items
                for value in item.get("source_subcategory_ids", []) if value
            }),
            "evidence_text": str(evidence.get("quote") or ""),
            "interpretation": str(evidence.get("interpretation") or ""),
            "paragraph_id": evidence.get("paragraph_id"),
            "component_key": (evidence.get("source_component_keys") or [""])[0],
            "component_keys": evidence.get("source_component_keys", []),
            "provenance": evidence.get("provenance", []),
            "valid": evidence.get("valid") is True,
        }

    components = []
    for display_order, (component_key, title) in enumerate(COMPONENTS):
        components.append({
            "component_key": component_key,
            "title": title,
            "description": (
                "جایگاه پدیده محوری نهایی پژوهش."
                if component_key == "central_phenomenon"
                else "نودهای تأییدشده این مؤلفه در مدل تلفیقی."
            ),
            "node_ids": [
                node["node_id"] for node in nodes
                if node["component_key"] == component_key
            ],
            "display_order": display_order,
        })

    sources = []
    for source in source_rows:
        snapshot = source["snapshot"]
        analysis = snapshot.get("analysis") or {}
        interview = analysis.get("interview_analysis") or {}
        source_phenomenon = next(
            (item for item in inputs["source_phenomena"] if item["source_project_id"] == source["source_project_id"]),
            None,
        )
        sources.append({
            "source_project_id": source["source_project_id"],
            "source_interview_id": str(snapshot.get("interview_id") or source["source_project_id"]),
            "source_title": source["source_title"],
            "snapshot_hash": source["source_hash"],
            "approval_status": "approved",
            "central_phenomenon": source_phenomenon,
            "confirmed_relationships": [
                item for item in inputs["source_relationships"]
                if item["source_project_id"] == source["source_project_id"]
            ],
            "category_ids": [
                str(item.get("category_id"))
                for item in interview.get("categories", [])
                if item.get("category_id")
            ],
        })

    source_phase_versions = {
        "pool_extraction": inputs["pool_version"],
        "category_grouping": inputs["phase_five_result"].get("source_phase_version"),
        "theme_synthesis": inputs["phase_five_result"]["version"],
        "phenomenon_synthesis": phenomenon_result["version"],
        "relationship_synthesis": relationship_result["version"],
    }
    contradictions = [
        {
            "stage_key": stage_key,
            "contradiction_id": item.get("contradiction_id"),
            "description": item.get("description"),
            "reasoning": item.get("reasoning"),
            "review_status": item.get("review_status"),
            "source_node_ids": item.get("node_ids", []),
            "source_item_ids": item.get("source_item_ids", []),
            "evidence_ref_ids": item.get("evidence_ref_ids", []),
        }
        for stage_key, stage_result in (
            ("phenomenon_synthesis", phenomenon_result),
            ("relationship_synthesis", relationship_result),
        )
        for item in (stage_result.get("output_json") or {}).get("contradictions", [])
    ]
    model = {
        "run_id": inputs["run"]["run_id"],
        "source_interview_ids": sorted({
            str(source["snapshot"].get("interview_id") or source["source_project_id"])
            for source in source_rows
        }),
        "nodes": nodes,
        "edges": edges,
        "edge_source_relationship_ids": sorted({
            source_relationship_id
            for edge in edges
            for source_relationship_id in edge["supporting_source_relationship_ids"]
        }),
        "components": components,
        "evidence_index": evidence_index,
        "source_traceability": {
            "sources": sources,
            "central_phenomenon_source_ids": phenomenon_source_ids,
            "source_relationships": inputs["source_relationships"],
            "contradictions": contradictions,
            "researcher_decisions": {
                "theme": inputs["phase_five_result"].get("researcher_edits_json", {}).get("decisions", []),
                "phenomenon": phenomenon_result.get("researcher_edits_json", {}).get("decisions", []),
                "relationships": relationship_result.get("researcher_edits_json", {}).get("decisions", []),
            },
        },
        "quality_audit": {
            "input_source_count": len(source_ids),
            "unresolved_contradiction_count": 0,
            "unresolved_contradictions": [],
            "status": "pending",
            "errors": [],
        },
        "model_version": None,
        "source_phase_versions": source_phase_versions,
        "input_hash": "",
        "approval_status": "approved",
        "created_at": None,
        "updated_at": None,
        "central_phenomenon_node_id": central_id,
    }
    model["input_hash"] = stable_hash({
        "base_input_hash": inputs["base_input_hash"],
        "phase_five_hash": inputs["phase_five_result"]["input_hash"],
        "phenomenon_hash": phenomenon_result["input_hash"],
        "relationship_hash": relationship_result["input_hash"],
        "nodes": nodes,
        "edges": edges,
        "components": components,
        "evidence_index": evidence_index,
        "source_traceability": model["source_traceability"],
        "source_phase_versions": source_phase_versions,
    })
    return model
