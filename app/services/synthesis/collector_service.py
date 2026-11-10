from __future__ import annotations

from typing import Any

from app.repositories.synthesis_repository import list_stage_results, mark_stage_results_stale, save_stage_result
from app.services.synthesis.normalization_service import as_list, make_evidence_record, stable_hash
from app.services.synthesis.pool_builder_service import (
    COMPONENT_KEYS,
    build_category_source_item,
    build_phenomenon_source_item,
    stable_review_item_id,
)
from app.services.synthesis.pool_validator import validate_pool_output
from app.services.synthesis.reference_mapper import map_paradigm_references
from app.services.synthesis.source_snapshot_service import load_run_snapshot_context


def _get_interview(snapshot: dict[str, Any]) -> dict[str, Any]:
    analysis = snapshot.get("analysis") or {}
    if not isinstance(analysis, dict):
        return {}
    interview = analysis.get("interview_analysis", analysis)
    return interview if isinstance(interview, dict) else {}


def _walk_evidence(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key == "evidence":
                for index, evidence in enumerate(as_list(child)):
                    if isinstance(evidence, dict):
                        yield f"{child_path}[{index}]", evidence
            else:
                yield from _walk_evidence(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_evidence(child, f"{path}[{index}]")


def _collect_source_evidence(source: dict[str, Any], interview: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = source["snapshot"]
    project_id = source["source_project_id"]
    interview_id = str(snapshot.get("interview_id") or project_id)
    paragraphs = {
        str(item.get("paragraph_id")): str(item.get("text") or "")
        for item in as_list(snapshot.get("paragraphs"))
        if isinstance(item, dict) and item.get("paragraph_id")
    }
    records: dict[str, dict[str, Any]] = {}
    for path, evidence in _walk_evidence(interview):
        record = make_evidence_record(
            evidence,
            source_project_id=project_id,
            source_interview_id=interview_id,
            paragraphs_by_id=paragraphs,
            provenance={"path": path},
        )
        existing = records.get(record["evidence_ref_id"])
        if existing is None:
            records[record["evidence_ref_id"]] = record
        else:
            existing["provenance"].extend(record["provenance"])
    return list(records.values())


def _review_item(source_project_id: str, issue_type: str, category_id: str, detail: Any) -> dict[str, Any]:
    return {
        "review_item_id": stable_review_item_id(source_project_id, issue_type, category_id, detail),
        "source_project_id": source_project_id,
        "category_id": category_id,
        "issue_type": issue_type,
        "detail": detail,
        "decision_status": "pending",
    }


def _latest_pool_result(run_id: str) -> dict[str, Any] | None:
    results = [
        item for item in list_stage_results(run_id)
        if item["stage_key"] == "pool_extraction" and item["component_key"] == "interview_pools"
    ]
    return max(results, key=lambda item: item["version"]) if results else None


def collect_run_pools(run_id: str, *, persist: bool = True) -> dict[str, Any]:
    context = load_run_snapshot_context(run_id)
    if context["run"].get("status") == "source_stale":
        raise ValueError("یکی از مصاحبه‌های ثبت‌شده پس از Snapshot تغییر کرده است؛ برای تحلیل جدید یک Run تازه بسازید.")
    source_input_hash = stable_hash({
        "run_id": run_id,
        "registered_source_count": context["registered_source_count"],
        "sources": [
            {"source_project_id": item["source_project_id"], "source_hash": item["source_hash"]}
            for item in context["snapshots"]
        ],
        "source_issues": context["source_issues"],
    })
    existing = _latest_pool_result(run_id) if persist else None
    if existing and existing["output_json"].get("metadata", {}).get("source_input_hash") == source_input_hash:
        return existing["output_json"]
    if existing and persist:
        mark_stage_results_stale(
            run_id,
            ("category_grouping", "theme_synthesis", "phenomenon_synthesis", "relationship_synthesis", "final_model"),
        )

    pools: dict[str, list[dict[str, Any]]] = {key: [] for key in COMPONENT_KEYS}
    evidence: list[dict[str, Any]] = []
    category_index: dict[str, dict[str, dict[str, Any]]] = {}
    source_categories: dict[str, list[dict[str, Any]]] = {}
    source_metadata: dict[str, dict[str, Any]] = {}
    unassigned_categories: list[dict[str, Any]] = []
    reference_issues: list[dict[str, Any]] = []
    extraction_errors: list[dict[str, Any]] = []
    incomplete_classifications: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    source_coverage: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []

    for source_issue in context["source_issues"]:
        extraction_errors.append({**source_issue, "stage": "snapshot_loading"})
    for source in context["snapshots"]:
        project_id = source["source_project_id"]
        snapshot = source["snapshot"]
        interview = _get_interview(snapshot)
        source_metadata[project_id] = {
            "project_id": project_id,
            "interview_id": snapshot.get("interview_id") or project_id,
            "title": snapshot.get("title") or source.get("source_title"),
            "researcher_edits": snapshot.get("researcher_edits") or {},
        }
        if not interview:
            extraction_errors.append({"source_project_id": project_id, "issue_type": "missing_interview_analysis"})
        raw_categories = as_list(interview.get("categories"))
        valid_categories = [item for item in raw_categories if isinstance(item, dict) and item.get("category_id")]
        if len(valid_categories) != len(raw_categories):
            extraction_errors.append({
                "source_project_id": project_id,
                "issue_type": "category_missing_id_or_invalid_record",
                "count": len(raw_categories) - len(valid_categories),
            })
        categories_by_id: dict[str, dict[str, Any]] = {}
        for category in valid_categories:
            category_id = str(category["category_id"])
            if category_id in categories_by_id:
                extraction_errors.append({"source_project_id": project_id, "category_id": category_id, "issue_type": "duplicate_category_id"})
                continue
            categories_by_id[category_id] = category
        category_index[project_id] = categories_by_id
        source_categories[project_id] = raw_categories

        source["evidence"] = _collect_source_evidence(source, interview)
        evidence.extend(source["evidence"])
        extraction_errors.extend({
            "source_project_id": project_id,
            "evidence_ref_id": item["evidence_ref_id"],
            "issue_type": "invalid_evidence",
            "validation_issue": item["validation_issue"],
        } for item in source["evidence"] if not item["valid"])
        mapping = map_paradigm_references(
            interview.get("paradigm_model"),
            categories_by_id,
            source_project_id=project_id,
        )
        reference_issues.extend(mapping["reference_issues"])
        incomplete_classifications.extend(mapping["incomplete_classifications"])
        for issue in mapping["reference_issues"]:
            review_items.append(_review_item(project_id, issue["issue_type"], issue["category_id"], issue))
        for issue in mapping["incomplete_classifications"]:
            review_items.append(_review_item(project_id, issue["issue_type"], "", issue))
        for issue in mapping["multi_component_references"]:
            review_items.append(_review_item(project_id, issue["issue_type"], issue["category_id"], issue))

        central_phenomenon = interview.get("central_phenomenon")
        central_ids = []
        if isinstance(central_phenomenon, dict):
            central_ids.extend(as_list(central_phenomenon.get("supporting_category_ids")))
            if central_phenomenon.get("category_id"):
                central_ids.append(central_phenomenon["category_id"])
        model_phenomenon = (interview.get("paradigm_model") or {}).get("central_phenomenon")
        if isinstance(model_phenomenon, dict):
            central_ids.extend(as_list(model_phenomenon.get("category_ids")))
            if model_phenomenon.get("category_id"):
                central_ids.append(model_phenomenon["category_id"])
        for category_id in dict.fromkeys(str(value) for value in central_ids if value):
            if category_id not in categories_by_id:
                issue = {
                    "source_project_id": project_id,
                    "category_id": category_id,
                    "component_key": "central_phenomenon",
                    "issue_type": "invalid_central_phenomenon_category_reference",
                }
                reference_issues.append(issue)
                review_items.append(_review_item(project_id, issue["issue_type"], category_id, issue))

        for relationship_index, relationship in enumerate(as_list(interview.get("relationships"))):
            if not isinstance(relationship, dict):
                extraction_errors.append({
                    "source_project_id": project_id,
                    "issue_type": "invalid_relationship_record",
                    "relationship_index": relationship_index,
                })
                continue
            relationship_id = str(relationship.get("relationship_id") or f"relationship-{relationship_index + 1}")
            relation_category_ids = {
                "source_category_id": relationship.get("source_category_id"),
                "target_category_id": relationship.get("target_category_id"),
            }
            for field, raw_category_id in relation_category_ids.items():
                if raw_category_id and str(raw_category_id) not in categories_by_id:
                    issue = {
                        "source_project_id": project_id,
                        "category_id": str(raw_category_id),
                        "relationship_id": relationship_id,
                        "field": field,
                        "issue_type": "invalid_relationship_category_reference",
                    }
                    reference_issues.append(issue)
                    review_items.append(_review_item(project_id, issue["issue_type"], str(raw_category_id), issue))
            relationships.append({
                "source_project_id": project_id,
                "source_interview_id": snapshot.get("interview_id") or project_id,
                "source_title": snapshot.get("title") or source.get("source_title"),
                "source_relationship_id": relationship_id,
                "source_category_ids": [str(value) for value in relation_category_ids.values() if value],
                "source_concept_ids": list(dict.fromkeys(
                    str(value) for value in as_list(relationship.get("supporting_concept_ids")) if value
                )),
                "evidence_refs": [
                    {"evidence_ref_id": value}
                    for value in _evidence_refs_for_value(relationship, source["evidence"])
                ],
                "raw_relationship": relationship,
            })

        for category_id, component_keys in mapping["assignments"].items():
            category = categories_by_id[category_id]
            for component_key in component_keys:
                references = mapping["references_by_component"][component_key].get(category_id, [])
                evidence_ids = _evidence_refs_for_category(category, references, source["evidence"])
                pools[component_key].append(build_category_source_item(
                    source, category, component_key, references, evidence_ids,
                ))

        for category_id in mapping["unassigned_category_ids"]:
            category = categories_by_id[category_id]
            evidence_ids = _evidence_refs_for_category(category, [], source["evidence"])
            item = build_category_source_item(source, category, "unassigned_categories", [], evidence_ids)
            unassigned_categories.append({
                "source_project_id": project_id,
                "source_interview_id": item["source_interview_id"],
                "category_id": category_id,
                "source_item_id": item["source_item_id"],
                "category": item,
            })
            review_items.append(_review_item(project_id, "unassigned_category", category_id, item))

        phenomenon = interview.get("central_phenomenon")
        phenomenon_evidence_ids = _evidence_refs_for_value(phenomenon, source["evidence"])
        phenomenon_item = build_phenomenon_source_item(
            source, phenomenon, categories_by_id, phenomenon_evidence_ids,
        )
        phenomenon_item["raw_paradigm_phenomenon"] = model_phenomenon
        pools["central_phenomenon"].append(phenomenon_item)
        source_coverage.append({
            "source_project_id": project_id,
            "source_interview_id": snapshot.get("interview_id") or project_id,
            "source_title": snapshot.get("title") or source.get("source_title"),
            "source_order": source["source_order"],
            "source_hash": source["source_hash"],
            "processed": True,
            "category_count": len(raw_categories),
            "evidence_count": len(source["evidence"]),
        })

    for issue in context["source_issues"]:
        source_coverage.append({
            "source_project_id": issue.get("source_project_id"),
            "source_title": issue.get("source_title"),
            "source_order": issue.get("source_order"),
            "processed": False,
            "issue_type": issue["issue_type"],
        })
    source_coverage.sort(key=lambda item: (item.get("source_order") or 0, item.get("source_project_id") or ""))

    source_count = context["registered_source_count"]
    extracted_category_count = sum(len(items) for items in source_categories.values())
    unique_assigned = {
        (item["source_project_id"], item["source_category_id"])
        for key in COMPONENT_KEYS[:-1] for item in pools[key]
    }
    unique_excluded = set()
    metadata = {
        "run_id": run_id,
        "source_count": source_count,
        "registered_source_count": source_count,
        "valid_snapshot_count": len(context["snapshots"]),
        "processed_source_count": len(source_categories),
        "extracted_category_count": extracted_category_count,
        "relationship_count": len(relationships),
        "evidence_count": len(evidence),
        "assigned_count": len(unique_assigned),
        "independent_count": len(unassigned_categories),
        "excluded_count": len(unique_excluded),
        "needs_review_count": len(review_items),
        "source_input_hash": source_input_hash,
    }
    result: dict[str, Any] = {
        "run_id": run_id,
        "stage_key": "pool_extraction",
        "component_key": "interview_pools",
        "version": (existing["version"] + 1) if existing else 1,
        "input_hash": source_input_hash,
        "pools": pools,
        "relationships": relationships,
        "evidence": evidence,
        "category_index": category_index,
        "source_categories": source_categories,
        "source_metadata": source_metadata,
        "source_coverage": source_coverage,
        "unassigned_categories": unassigned_categories,
        "reference_issues": reference_issues,
        "incomplete_classifications": incomplete_classifications,
        "extraction_errors": extraction_errors,
        "review_items": review_items,
        "excluded_categories": [],
        "metadata": metadata,
    }
    result["validation_report"] = validate_pool_output(result)
    if persist:
        saved = save_stage_result(
            run_id,
            "pool_extraction",
            "interview_pools",
            version=result["version"],
            status="review_required" if review_items or extraction_errors or not result["validation_report"]["valid"] else "ready",
            input_hash=source_input_hash,
            output_json=result,
            validation_report_json=result["validation_report"],
            researcher_edits_json={"decisions": []},
            approval_status="pending" if review_items or extraction_errors or not result["validation_report"]["valid"] else "approved",
        )
        result = saved["output_json"]
    return result


def _evidence_refs_for_value(value: Any, evidence: list[dict[str, Any]]) -> list[str]:
    raw_hashes = {stable_hash(item) for _, item in _walk_evidence(value)}
    return [
        item["evidence_ref_id"] for item in evidence
        if stable_hash(item.get("raw_evidence")) in raw_hashes
    ]


def _evidence_refs_for_category(
    category: dict[str, Any],
    references: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> list[str]:
    raw_values: list[Any] = [category]
    raw_values.extend(reference.get("raw_model_item") for reference in references)
    raw_hashes = {
        stable_hash(raw_evidence)
        for value in raw_values
        for _, raw_evidence in _walk_evidence(value)
    }
    return [
        item["evidence_ref_id"] for item in evidence
        if stable_hash(item.get("raw_evidence")) in raw_hashes
    ]
