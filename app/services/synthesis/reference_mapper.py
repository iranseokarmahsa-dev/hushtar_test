from __future__ import annotations

from typing import Any

from app.services.synthesis.normalization_service import as_list, string_ids

POOL_COMPONENT_KEYS = (
    "causal_conditions",
    "contextual_conditions",
    "intervening_conditions",
    "action_interaction_strategies",
    "consequences",
)


def _reference_nodes(value: Any, path: str = ""):
    if isinstance(value, dict):
        if "category_id" in value or "category_ids" in value:
            yield path, value
        for key, child in value.items():
            if key == "evidence":
                continue
            yield from _reference_nodes(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _reference_nodes(child, f"{path}[{index}]")


def map_paradigm_references(
    paradigm_model: Any,
    categories_by_id: dict[str, dict[str, Any]],
    *,
    source_project_id: str,
) -> dict[str, Any]:
    assignments: dict[str, list[str]] = {}
    references_by_component: dict[str, dict[str, list[dict[str, Any]]]] = {
        key: {} for key in POOL_COMPONENT_KEYS
    }
    reference_issues: list[dict[str, Any]] = []
    incomplete_classifications: list[dict[str, Any]] = []

    model = paradigm_model if isinstance(paradigm_model, dict) else {}
    for component_key in POOL_COMPONENT_KEYS:
        component_value = model.get(component_key)
        if component_value is None:
            continue
        members = as_list(component_value)
        for member_index, member in enumerate(members):
            member_path = f"{component_key}[{member_index}]"
            nodes = list(_reference_nodes(member, member_path))
            if not nodes:
                incomplete_classifications.append({
                    "source_project_id": source_project_id,
                    "component_key": component_key,
                    "model_path": member_path,
                    "raw_model_item": member,
                    "issue_type": "classification_without_category_reference",
                })
                continue
            for path, node in nodes:
                raw_ids = node.get("category_ids")
                single_id = node.get("category_id")
                category_ids = string_ids(raw_ids) + string_ids(single_id)
                category_ids = list(dict.fromkeys(category_ids))
                if not category_ids:
                    incomplete_classifications.append({
                        "source_project_id": source_project_id,
                        "component_key": component_key,
                        "model_path": path,
                        "raw_model_item": node,
                        "issue_type": "classification_without_category_reference",
                    })
                    continue
                for category_id in category_ids:
                    reference = {
                        "component_key": component_key,
                        "model_path": path,
                        "raw_model_item": node,
                    }
                    if category_id not in categories_by_id:
                        reference_issues.append({
                            "source_project_id": source_project_id,
                            "category_id": category_id,
                            "component_key": component_key,
                            "model_path": path,
                            "issue_type": "invalid_category_reference",
                            "raw_model_item": node,
                        })
                        continue
                    assignments.setdefault(category_id, [])
                    if component_key not in assignments[category_id]:
                        assignments[category_id].append(component_key)
                    references_by_component[component_key].setdefault(category_id, []).append(reference)

    multi_component_references = [
        {
            "source_project_id": source_project_id,
            "category_id": category_id,
            "component_keys": component_keys,
            "issue_type": "multiple_components",
        }
        for category_id, component_keys in assignments.items()
        if len(component_keys) > 1
    ]
    unassigned_categories = [
        category_id for category_id in categories_by_id if category_id not in assignments
    ]
    return {
        "assignments": assignments,
        "references_by_component": references_by_component,
        "reference_issues": reference_issues,
        "incomplete_classifications": incomplete_classifications,
        "multi_component_references": multi_component_references,
        "unassigned_category_ids": unassigned_categories,
    }
