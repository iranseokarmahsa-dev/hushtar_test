from __future__ import annotations

import re
from typing import Any

from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS

GROUP_FIELDS = {
    "group_id",
    "component_key",
    "proposed_category_name",
    "definition",
    "source_item_ids",
    "reasoning",
    "similarities",
    "differences",
    "evidence_ref_ids",
    "confidence",
    "needs_researcher_review",
}
CONTRADICTION_FIELDS = {
    "contradiction_id",
    "source_item_ids",
    "evidence_ref_ids",
    "component_key",
    "description",
    "reasoning",
    "needs_researcher_review",
}
QUALITY_FIELDS = {
    "total_source_items",
    "grouped_source_items",
    "standalone_source_items",
    "total_groups",
    "total_contradictions",
    "missing_source_item_ids",
    "duplicate_source_item_ids",
    "unknown_source_item_ids",
    "unknown_evidence_ref_ids",
    "notes",
}


def _error(errors: list[dict[str, Any]], issue_type: str, **details: Any) -> None:
    errors.append({"issue_type": issue_type, **details})


def _required_fields(
    value: dict[str, Any],
    required: set[str],
    *,
    errors: list[dict[str, Any]],
    context: str,
) -> None:
    for key in sorted(required - value.keys()):
        _error(errors, "missing_required_field", context=context, field=key)
    for key in sorted(value.keys() - required - {"_heading_id", "group_review_status"}):
        _error(errors, "unknown_field", context=context, field=key)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _bool_value(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in {"true", "false"}


def _validate_count(
    quality: dict[str, Any],
    key: str,
    expected: int,
    errors: list[dict[str, Any]],
) -> None:
    value = quality.get(key)
    try:
        actual = int(str(value).strip())
    except (TypeError, ValueError):
        _error(errors, "invalid_quality_count", field=key, value=value)
        return
    if actual != expected:
        _error(errors, "quality_count_mismatch", field=key, expected=expected, actual=actual)


def validate_category_grouping_result(
    parsed: dict[str, Any],
    *,
    run_id: str,
    input_hash: str,
    source_items_by_component: dict[str, list[dict[str, Any]]],
    evidence: list[dict[str, Any]],
    raw_output_text: str,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    if set(metadata) != {"schema_version", "run_id", "stage_key", "input_hash"}:
        _error(errors, "invalid_metadata_fields", fields=sorted(metadata))
    expected_metadata = {
        "schema_version": "1.0",
        "run_id": run_id,
        "stage_key": "category_grouping",
        "input_hash": input_hash,
    }
    for field, expected in expected_metadata.items():
        if str(metadata.get(field, "")).strip() != expected:
            _error(errors, "metadata_mismatch", field=field, expected=expected, actual=metadata.get(field))

    component_results = parsed.get("component_results")
    if not isinstance(component_results, dict) or set(component_results) != set(POOL_COMPONENT_KEYS):
        _error(errors, "component_set_mismatch", expected=list(POOL_COMPONENT_KEYS), actual=sorted(component_results) if isinstance(component_results, dict) else None)
        component_results = component_results if isinstance(component_results, dict) else {}

    source_item_component: dict[str, str] = {}
    evidence_by_id = {
        str(item.get("evidence_ref_id")): item
        for item in evidence
        if item.get("evidence_ref_id")
    }
    evidence_by_source_item: dict[str, set[str]] = {}
    for component_key in POOL_COMPONENT_KEYS:
        for item in source_items_by_component.get(component_key, []):
            item_id = str(item.get("source_item_id") or "")
            if item_id in source_item_component:
                _error(errors, "duplicate_input_source_item_id", source_item_id=item_id)
            source_item_component[item_id] = component_key
            evidence_by_source_item[item_id] = {
                str(reference.get("evidence_ref_id"))
                for reference in item.get("evidence_refs", [])
                if reference.get("evidence_ref_id")
            }

    seen_items: dict[str, int] = {}
    group_ids: set[str] = set()
    contradiction_ids: set[str] = set()
    grouped_count = 0
    standalone_count = 0
    groups_count = 0
    contradiction_count = 0
    output_strings: list[str] = []

    for component_key in POOL_COMPONENT_KEYS:
        result = component_results.get(component_key)
        if not isinstance(result, dict):
            _error(errors, "invalid_component_result", component_key=component_key)
            continue
        groups = result.get("groups")
        standalone = result.get("standalone_source_items")
        contradictions = result.get("contradictions")
        if not isinstance(groups, list) or not isinstance(standalone, list) or not isinstance(contradictions, list):
            _error(errors, "invalid_component_sections", component_key=component_key)
            continue

        for group_index, group in enumerate(groups):
            groups_count += 1
            context = f"{component_key}.groups[{group_index}]"
            if not isinstance(group, dict):
                _error(errors, "invalid_group", context=context)
                continue
            _required_fields(group, GROUP_FIELDS, errors=errors, context=context)
            group_id = str(group.get("group_id") or "").strip()
            heading_id = str(group.get("_heading_id") or "").strip()
            if not group_id or group_id != heading_id:
                _error(errors, "group_heading_id_mismatch", context=context, heading_id=heading_id, group_id=group_id)
            if group_id in group_ids:
                _error(errors, "duplicate_group_id", group_id=group_id)
            group_ids.add(group_id)
            if str(group.get("component_key") or "") != component_key:
                _error(errors, "group_component_mismatch", context=context, expected=component_key, actual=group.get("component_key"))
            member_ids = _string_list(group.get("source_item_ids"))
            if len(member_ids) < 2:
                _error(errors, "group_has_fewer_than_two_members", context=context, source_item_ids=member_ids)
            if len(member_ids) != len(set(member_ids)):
                _error(errors, "duplicate_source_item_in_group", context=context)
            for item_id in member_ids:
                seen_items[item_id] = seen_items.get(item_id, 0) + 1
                if item_id not in source_item_component:
                    _error(errors, "unknown_source_item_id", context=context, source_item_id=item_id)
                elif source_item_component[item_id] != component_key:
                    _error(errors, "source_item_component_mismatch", source_item_id=item_id, expected=source_item_component[item_id], actual=component_key)
            grouped_count += len(member_ids)
            evidence_ids = _string_list(group.get("evidence_ref_ids"))
            if len(evidence_ids) != len(set(evidence_ids)):
                _error(errors, "duplicate_evidence_ref_id", context=context)
            allowed_evidence = set().union(*(evidence_by_source_item.get(item_id, set()) for item_id in member_ids))
            for evidence_id in evidence_ids:
                if evidence_id not in evidence_by_id:
                    _error(errors, "unknown_evidence_ref_id", context=context, evidence_ref_id=evidence_id)
                elif evidence_id not in allowed_evidence:
                    _error(errors, "evidence_not_owned_by_group_member", context=context, evidence_ref_id=evidence_id)
            try:
                confidence = float(str(group.get("confidence")).strip())
                if not 0 <= confidence <= 1:
                    raise ValueError
            except (TypeError, ValueError):
                _error(errors, "invalid_confidence", context=context, value=group.get("confidence"))
            if not _bool_value(group.get("needs_researcher_review")):
                _error(errors, "invalid_boolean", context=context, field="needs_researcher_review")
            for field in ("similarities", "differences"):
                if not _string_list(group.get(field)):
                    _error(errors, "empty_group_analysis", context=context, field=field)
            for field in ("proposed_category_name", "definition", "reasoning"):
                if not str(group.get(field) or "").strip():
                    _error(errors, "empty_required_value", context=context, field=field)
            for field in ("proposed_category_name", "definition", "reasoning", "similarities", "differences"):
                value = group.get(field)
                output_strings.extend(value if isinstance(value, list) else [value])

        for item_id in standalone:
            value = str(item_id).strip()
            standalone_count += 1
            seen_items[value] = seen_items.get(value, 0) + 1
            if value not in source_item_component:
                _error(errors, "unknown_source_item_id", context=f"{component_key}.standalone", source_item_id=value)
            elif source_item_component[value] != component_key:
                _error(errors, "source_item_component_mismatch", source_item_id=value, expected=source_item_component[value], actual=component_key)

        for contradiction_index, contradiction in enumerate(contradictions):
            contradiction_count += 1
            context = f"{component_key}.contradictions[{contradiction_index}]"
            if not isinstance(contradiction, dict):
                _error(errors, "invalid_contradiction", context=context)
                continue
            _required_fields(contradiction, CONTRADICTION_FIELDS, errors=errors, context=context)
            contradiction_id = str(contradiction.get("contradiction_id") or "").strip()
            if not contradiction_id or contradiction_id != str(contradiction.get("_heading_id") or "").strip():
                _error(errors, "contradiction_heading_id_mismatch", context=context)
            if contradiction_id in contradiction_ids:
                _error(errors, "duplicate_contradiction_id", contradiction_id=contradiction_id)
            contradiction_ids.add(contradiction_id)
            if str(contradiction.get("component_key") or "") != component_key:
                _error(errors, "contradiction_component_mismatch", context=context, expected=component_key, actual=contradiction.get("component_key"))
            item_ids = _string_list(contradiction.get("source_item_ids"))
            if not item_ids:
                _error(errors, "contradiction_without_source_items", context=context)
            if len(item_ids) != len(set(item_ids)):
                _error(errors, "duplicate_contradiction_source_item_id", context=context)
            for item_id in item_ids:
                if item_id not in source_item_component:
                    _error(errors, "unknown_source_item_id", context=context, source_item_id=item_id)
                elif source_item_component[item_id] != component_key:
                    _error(errors, "source_item_component_mismatch", source_item_id=item_id, expected=source_item_component[item_id], actual=component_key)
            contradiction_evidence_ids = _string_list(contradiction.get("evidence_ref_ids"))
            if len(contradiction_evidence_ids) != len(set(contradiction_evidence_ids)):
                _error(errors, "duplicate_evidence_ref_id", context=context)
            for evidence_id in contradiction_evidence_ids:
                if evidence_id not in evidence_by_id:
                    _error(errors, "unknown_evidence_ref_id", context=context, evidence_ref_id=evidence_id)
                elif not any(evidence_id in evidence_by_source_item.get(item_id, set()) for item_id in item_ids):
                    _error(errors, "evidence_not_owned_by_contradiction_item", context=context, evidence_ref_id=evidence_id)
            if not _bool_value(contradiction.get("needs_researcher_review")):
                _error(errors, "invalid_boolean", context=context, field="needs_researcher_review")
            for field in ("description", "reasoning"):
                if not str(contradiction.get(field) or "").strip():
                    _error(errors, "empty_required_value", context=context, field=field)
                output_strings.append(contradiction.get(field))

    for source_item_id in sorted(source_item_component):
        count = seen_items.get(source_item_id, 0)
        if count == 0:
            _error(errors, "missing_source_item_id", source_item_id=source_item_id)
        elif count > 1:
            _error(errors, "duplicate_source_item_id", source_item_id=source_item_id, occurrences=count)

    quality = parsed.get("quality_audit") if isinstance(parsed.get("quality_audit"), dict) else {}
    _required_fields(quality, QUALITY_FIELDS, errors=errors, context="quality_audit")
    if str(quality.get("notes") or "").strip() == "":
        _error(errors, "empty_required_value", context="quality_audit", field="notes")
    input_item_count = len(source_item_component)
    _validate_count(quality, "total_source_items", input_item_count, errors)
    _validate_count(quality, "grouped_source_items", grouped_count, errors)
    _validate_count(quality, "standalone_source_items", standalone_count, errors)
    _validate_count(quality, "total_groups", groups_count, errors)
    _validate_count(quality, "total_contradictions", contradiction_count, errors)
    for field in (
        "missing_source_item_ids",
        "duplicate_source_item_ids",
        "unknown_source_item_ids",
        "unknown_evidence_ref_ids",
    ):
        values = _string_list(quality.get(field))
        if values:
            _error(errors, "quality_audit_reports_issues", field=field, values=values)

    quote_texts = {
        str(item.get("quote") or "").strip()
        for item in evidence
        if str(item.get("quote") or "").strip()
    }
    for quote in quote_texts:
        quote_pattern = rf"(?<!\w){re.escape(quote)}(?!\w)"
        if any(re.search(quote_pattern, str(value)) for value in output_strings if value is not None) or re.search(
            quote_pattern, raw_output_text
        ):
            _error(errors, "evidence_quote_repeated_in_output", evidence_ref_id=next(
                (str(item.get("evidence_ref_id")) for item in evidence if item.get("quote") == quote), ""
            ))

    if not errors:
        warnings.extend({
            "issue_type": "group_requires_researcher_review",
            "group_id": str(group.get("group_id")),
        } for result in component_results.values() for group in result["groups"]
          if str(group.get("needs_researcher_review", "")).lower() == "true")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checked_source_item_count": input_item_count,
        "checked_evidence_count": len(evidence_by_id),
        "checked_group_count": groups_count,
        "checked_contradiction_count": contradiction_count,
    }
