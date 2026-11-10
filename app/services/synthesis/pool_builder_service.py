from __future__ import annotations

from typing import Any

from app.services.synthesis.normalization_service import as_list, stable_hash, stable_source_item_id, string_ids
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS

COMPONENT_KEYS = (*POOL_COMPONENT_KEYS, "central_phenomenon")


def _get_approved_edit(snapshot: dict[str, Any], category_id: str) -> dict[str, Any]:
    edits = (snapshot.get("researcher_edits") or {}).get("interview_analysis") or {}
    if not edits.get("confirmed"):
        return {}
    category_edits = edits.get("categories") or {}
    value = category_edits.get(category_id, {})
    return value if isinstance(value, dict) else {}


def _reference_values(references: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    concepts: list[str] = []
    raw_items: list[dict[str, Any]] = []
    for reference in references:
        raw = reference.get("raw_model_item") or {}
        concepts.extend(string_ids(raw.get("concept_ids")))
        raw_items.append(raw)
    return list(dict.fromkeys(concepts)), raw_items


def build_category_source_item(
    source: dict[str, Any],
    category: dict[str, Any],
    component_key: str,
    references: list[dict[str, Any]],
    evidence_ref_ids: list[str],
) -> dict[str, Any]:
    snapshot = source["snapshot"]
    source_project_id = source["source_project_id"]
    category_id = str(category.get("category_id") or "")
    edit = _get_approved_edit(snapshot, category_id)
    model_concept_ids, raw_model_items = _reference_values(references)
    concept_ids = list(dict.fromkeys(string_ids(category.get("concept_ids")) + model_concept_ids))
    subcategories = as_list(category.get("subcategories"))
    subcategory_ids = [
        str(item.get("subcategory_id"))
        for item in subcategories
        if isinstance(item, dict) and item.get("subcategory_id")
    ]
    source_evidence_ids = list(dict.fromkeys(
        [str(item.get("source_evidence_id")) for item in source.get("evidence", []) if item["evidence_ref_id"] in evidence_ref_ids]
    ))
    paragraph_ids = list(dict.fromkeys(
        [str(item.get("paragraph_id")) for item in source.get("evidence", [])
         if item["evidence_ref_id"] in evidence_ref_ids and item.get("paragraph_id")]
    ))
    return {
        "source_item_id": stable_source_item_id(source_project_id, component_key, category_id),
        "component_key": component_key,
        "source_project_id": source_project_id,
        "source_interview_id": snapshot.get("interview_id") or source_project_id,
        "source_title": snapshot.get("title") or source.get("source_title"),
        "source_category_id": category_id,
        "original_category_name": category.get("category_name") or category.get("name") or "",
        "researcher_approved_category_name": edit.get("category_name") or category.get("category_name") or category.get("name") or "",
        "definition": edit.get("definition") if edit.get("definition") is not None else category.get("definition", ""),
        "original_definition": category.get("definition", ""),
        "category_reason": category.get("category_reason", ""),
        "source_concept_ids": concept_ids,
        "source_subcategory_ids": subcategory_ids,
        "source_properties": as_list(category.get("properties")),
        "source_dimensions": as_list(category.get("dimensions")),
        "source_paragraph_ids": paragraph_ids,
        "source_evidence_ids": source_evidence_ids,
        "evidence_refs": [{"evidence_ref_id": item} for item in evidence_ref_ids],
        "source_confidence": category.get("confidence"),
        "source_review_status": "researcher_confirmed" if (snapshot.get("researcher_edits") or {}).get("interview_analysis", {}).get("confirmed") else ("needs_review" if category.get("needs_researcher_review") else "unreviewed"),
        "raw_category": category,
        "raw_paradigm_references": raw_model_items,
    }


def build_phenomenon_source_item(
    source: dict[str, Any],
    phenomenon: Any,
    categories_by_id: dict[str, dict[str, Any]],
    evidence_ref_ids: list[str],
) -> dict[str, Any]:
    snapshot = source["snapshot"]
    source_project_id = source["source_project_id"]
    phenomenon = phenomenon if isinstance(phenomenon, dict) else {}
    category_ids = string_ids(phenomenon.get("supporting_category_ids"))
    if phenomenon.get("category_id"):
        category_ids = list(dict.fromkeys([str(phenomenon["category_id"])] + category_ids))
    first_category = categories_by_id.get(category_ids[0], {}) if category_ids else {}
    local_id = str(phenomenon.get("category_id") or "central_phenomenon")
    edits = (snapshot.get("researcher_edits") or {}).get("interview_analysis") or {}
    phenomenon_edit = edits.get("central_phenomenon") if edits.get("confirmed") else None
    phenomenon_edit = phenomenon_edit if isinstance(phenomenon_edit, dict) else {}
    evidence = [item for item in source.get("evidence", []) if item["evidence_ref_id"] in evidence_ref_ids]
    concepts = string_ids(phenomenon.get("supporting_concept_ids"))
    return {
        "source_item_id": stable_source_item_id(source_project_id, "central_phenomenon", local_id),
        "component_key": "central_phenomenon",
        "source_project_id": source_project_id,
        "source_interview_id": snapshot.get("interview_id") or source_project_id,
        "source_title": snapshot.get("title") or source.get("source_title"),
        "source_category_id": phenomenon.get("category_id"),
        "original_category_name": first_category.get("category_name") or phenomenon.get("title") or "",
        "researcher_approved_category_name": first_category.get("category_name") or phenomenon_edit.get("title") or phenomenon.get("title") or "",
        "definition": phenomenon_edit.get("definition") if phenomenon_edit.get("definition") is not None else phenomenon.get("definition", ""),
        "original_definition": phenomenon.get("definition", ""),
        "category_reason": phenomenon.get("selection_reason", ""),
        "source_concept_ids": concepts,
        "source_subcategory_ids": [],
        "source_properties": [],
        "source_dimensions": [],
        "source_paragraph_ids": list(dict.fromkeys(item["paragraph_id"] for item in evidence if item.get("paragraph_id"))),
        "source_evidence_ids": list(dict.fromkeys(str(item["source_evidence_id"]) for item in evidence)),
        "evidence_refs": [{"evidence_ref_id": item} for item in evidence_ref_ids],
        "source_confidence": phenomenon.get("confidence"),
        "source_review_status": "researcher_confirmed" if edits.get("confirmed") else ("needs_review" if phenomenon.get("needs_researcher_review") else "unreviewed"),
        "phenomenon_title": phenomenon_edit.get("title") or phenomenon.get("title") or "",
        "supporting_category_ids": category_ids,
        "raw_phenomenon": phenomenon,
    }


def stable_review_item_id(source_project_id: str, issue_type: str, local_id: str, detail: Any = None) -> str:
    return f"{source_project_id}::review::{issue_type}::{local_id}::{stable_hash(detail)[:12]}"
