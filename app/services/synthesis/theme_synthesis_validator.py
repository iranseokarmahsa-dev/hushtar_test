from __future__ import annotations

import re
from typing import Any


THEME_FIELDS = {
    "theme_id", "component_key", "proposed_theme_name", "definition",
    "constituent_category_unit_ids", "reasoning", "conceptual_scope",
    "similarities", "differences", "evidence_ref_ids", "confidence",
    "needs_researcher_review", "_heading_id",
}
CONTRADICTION_FIELDS = {
    "contradiction_id", "component_key", "category_unit_ids",
    "evidence_ref_ids", "description", "reasoning", "needs_researcher_review",
    "_heading_id",
}


def _issue(errors: list[dict[str, Any]], issue_type: str, **details: Any) -> None:
    errors.append({"issue_type": issue_type, **details})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ids(value: Any) -> list[str]:
    return [str(item).strip() for item in value or [] if str(item).strip()]


def _bool(value: Any) -> bool:
    return isinstance(value, str) and value.lower() in {"true", "false"}


def _count(quality: dict[str, Any], key: str, expected: int, errors: list[dict[str, Any]]) -> None:
    try:
        actual = int(str(quality.get(key)).strip())
    except (TypeError, ValueError):
        _issue(errors, "invalid_quality_count", field=key, value=quality.get(key))
        return
    if actual != expected:
        _issue(errors, "quality_count_mismatch", field=key, expected=expected, actual=actual)


def validate_theme_synthesis_result(
    parsed: dict[str, Any],
    *,
    run_id: str,
    input_hash: str,
    source_phase_version: int,
    category_units: list[dict[str, Any]],
    raw_output_text: str,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    expected_metadata = {
        "schema_version": "1.0",
        "run_id": run_id,
        "stage_key": "theme_synthesis",
        "input_hash": input_hash,
        "source_phase_version": str(source_phase_version),
    }
    if set(metadata) != set(expected_metadata):
        _issue(errors, "invalid_metadata_fields", fields=sorted(metadata))
    for field, expected in expected_metadata.items():
        if _text(metadata.get(field)) != expected:
            _issue(errors, "metadata_mismatch", field=field, expected=expected, actual=metadata.get(field))

    units_by_id = {str(unit["category_unit_id"]): unit for unit in category_units}
    if len(units_by_id) != len(category_units):
        _issue(errors, "duplicate_input_category_unit_id")
    evidence_by_id = {
        evidence_id
        for unit in category_units
        for evidence_id in _ids(unit.get("evidence_ref_ids"))
    }
    evidence_by_unit = {
        str(unit["category_unit_id"]): set(_ids(unit.get("evidence_ref_ids")))
        for unit in category_units
    }
    components = parsed.get("component_results")
    if not isinstance(components, dict):
        _issue(errors, "invalid_component_results")
        components = {}
    if not isinstance(components, dict):
        components = {}

    from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS
    if set(components) != set(POOL_COMPONENT_KEYS):
        _issue(errors, "component_set_mismatch", expected=list(POOL_COMPONENT_KEYS), actual=sorted(components))

    seen_units: dict[str, int] = {}
    theme_ids: set[str] = set()
    contradiction_ids: set[str] = set()
    theme_count = contradiction_count = themed_count = unthemed_count = 0
    for component_key in POOL_COMPONENT_KEYS:
        component = components.get(component_key)
        if not isinstance(component, dict):
            _issue(errors, "invalid_component_result", component_key=component_key)
            continue
        themes = component.get("themes")
        unthemed = component.get("unthemed_category_units")
        contradictions = component.get("contradictions")
        if not isinstance(themes, list) or not isinstance(unthemed, list) or not isinstance(contradictions, list):
            _issue(errors, "invalid_component_sections", component_key=component_key)
            continue
        for theme_index, theme in enumerate(themes):
            theme_count += 1
            context = f"{component_key}.themes[{theme_index}]"
            if not isinstance(theme, dict):
                _issue(errors, "invalid_theme", context=context)
                continue
            for key in sorted(THEME_FIELDS - theme.keys()):
                _issue(errors, "missing_required_field", context=context, field=key)
            for key in sorted(theme.keys() - THEME_FIELDS):
                _issue(errors, "unknown_field", context=context, field=key)
            theme_id = _text(theme.get("theme_id"))
            if not theme_id or theme_id != _text(theme.get("_heading_id")):
                _issue(errors, "theme_heading_id_mismatch", context=context)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", theme_id):
                _issue(errors, "invalid_theme_id", context=context)
            if theme_id in theme_ids:
                _issue(errors, "duplicate_theme_id", theme_id=theme_id)
            theme_ids.add(theme_id)
            if _text(theme.get("component_key")) != component_key:
                _issue(errors, "theme_component_mismatch", theme_id=theme_id)
            member_ids = _ids(theme.get("constituent_category_unit_ids"))
            if not member_ids:
                _issue(errors, "theme_without_category_units", theme_id=theme_id)
            if len(member_ids) != len(set(member_ids)):
                _issue(errors, "duplicate_category_unit_in_theme", theme_id=theme_id)
            themed_count += len(member_ids)
            for unit_id in member_ids:
                seen_units[unit_id] = seen_units.get(unit_id, 0) + 1
                unit = units_by_id.get(unit_id)
                if unit is None:
                    _issue(errors, "unknown_category_unit_id", theme_id=theme_id, category_unit_id=unit_id)
                elif unit.get("component_key") != component_key:
                    _issue(errors, "category_unit_component_mismatch", category_unit_id=unit_id)
            evidence_ids = _ids(theme.get("evidence_ref_ids"))
            if len(evidence_ids) != len(set(evidence_ids)):
                _issue(errors, "duplicate_evidence_ref_id", context=context)
            allowed = set().union(*(evidence_by_unit.get(unit_id, set()) for unit_id in member_ids))
            for evidence_id in evidence_ids:
                if evidence_id not in evidence_by_id:
                    _issue(errors, "unknown_evidence_ref_id", context=context, evidence_ref_id=evidence_id)
                elif evidence_id not in allowed:
                    _issue(errors, "evidence_not_owned_by_theme_unit", context=context, evidence_ref_id=evidence_id)
            if not evidence_ids and allowed:
                _issue(errors, "theme_without_evidence_refs", theme_id=theme_id)
            try:
                confidence = float(_text(theme.get("confidence")))
                if not 0 <= confidence <= 1:
                    raise ValueError
            except (TypeError, ValueError):
                _issue(errors, "invalid_confidence", theme_id=theme_id)
            if not _bool(theme.get("needs_researcher_review")):
                _issue(errors, "invalid_boolean", theme_id=theme_id)
            for field in ("proposed_theme_name", "definition", "reasoning", "conceptual_scope"):
                value = _text(theme.get(field))
                if not value:
                    _issue(errors, "empty_required_value", context=context, field=field)

        for item_index, item in enumerate(unthemed):
            unthemed_count += 1
            context = f"{component_key}.unthemed_category_units[{item_index}]"
            if not isinstance(item, dict):
                _issue(errors, "invalid_unthemed_category_unit", context=context)
                continue
            if set(item) != {"category_unit_id", "reason", "_heading_id"}:
                _issue(errors, "invalid_unthemed_fields", context=context, fields=sorted(item))
            unit_id = _text(item.get("category_unit_id"))
            if unit_id != _text(item.get("_heading_id")):
                _issue(errors, "unthemed_heading_id_mismatch", context=context)
            seen_units[unit_id] = seen_units.get(unit_id, 0) + 1
            unit = units_by_id.get(unit_id)
            if unit is None:
                _issue(errors, "unknown_category_unit_id", category_unit_id=unit_id)
            elif unit.get("component_key") != component_key:
                _issue(errors, "category_unit_component_mismatch", category_unit_id=unit_id)
            if not _text(item.get("reason")):
                _issue(errors, "empty_required_value", context=context, field="reason")

        for contradiction_index, contradiction in enumerate(contradictions):
            contradiction_count += 1
            context = f"{component_key}.contradictions[{contradiction_index}]"
            if not isinstance(contradiction, dict):
                _issue(errors, "invalid_contradiction", context=context)
                continue
            for key in sorted(CONTRADICTION_FIELDS - contradiction.keys()):
                _issue(errors, "missing_required_field", context=context, field=key)
            for key in sorted(contradiction.keys() - CONTRADICTION_FIELDS):
                _issue(errors, "unknown_field", context=context, field=key)
            contradiction_id = _text(contradiction.get("contradiction_id"))
            if not contradiction_id or contradiction_id != _text(contradiction.get("_heading_id")):
                _issue(errors, "contradiction_heading_id_mismatch", context=context)
            if contradiction_id in contradiction_ids:
                _issue(errors, "duplicate_contradiction_id", contradiction_id=contradiction_id)
            contradiction_ids.add(contradiction_id)
            if _text(contradiction.get("component_key")) != component_key:
                _issue(errors, "contradiction_component_mismatch", context=context)
            member_ids = _ids(contradiction.get("category_unit_ids"))
            if not member_ids:
                _issue(errors, "contradiction_without_category_units", context=context)
            for unit_id in member_ids:
                unit = units_by_id.get(unit_id)
                if unit is None:
                    _issue(errors, "unknown_category_unit_id", category_unit_id=unit_id)
                elif unit.get("component_key") != component_key:
                    _issue(errors, "category_unit_component_mismatch", category_unit_id=unit_id)
            allowed = set().union(*(evidence_by_unit.get(unit_id, set()) for unit_id in member_ids))
            for evidence_id in _ids(contradiction.get("evidence_ref_ids")):
                if evidence_id not in evidence_by_id:
                    _issue(errors, "unknown_evidence_ref_id", context=context, evidence_ref_id=evidence_id)
                elif evidence_id not in allowed:
                    _issue(errors, "evidence_not_owned_by_contradiction_unit", context=context, evidence_ref_id=evidence_id)
            if not _bool(contradiction.get("needs_researcher_review")):
                _issue(errors, "invalid_boolean", context=context)
            for field in ("description", "reasoning"):
                value = _text(contradiction.get(field))
                if not value:
                    _issue(errors, "empty_required_value", context=context, field=field)

    for unit_id in units_by_id:
        count = seen_units.get(unit_id, 0)
        if count == 0:
            _issue(errors, "missing_category_unit_id", category_unit_id=unit_id)
        elif count > 1:
            _issue(errors, "duplicate_category_unit_id", category_unit_id=unit_id, occurrences=count)

    quality = parsed.get("quality_audit") if isinstance(parsed.get("quality_audit"), dict) else {}
    expected_quality = {
        "total_category_units", "total_themes", "themed_category_units",
        "unthemed_category_units", "total_contradictions", "missing_category_unit_ids",
        "duplicate_category_unit_ids", "unknown_category_unit_ids", "unknown_evidence_ref_ids", "notes",
    }
    if set(quality) != expected_quality:
        _issue(errors, "invalid_quality_fields", fields=sorted(quality))
    _count(quality, "total_category_units", len(units_by_id), errors)
    _count(quality, "total_themes", theme_count, errors)
    _count(quality, "themed_category_units", themed_count, errors)
    _count(quality, "unthemed_category_units", unthemed_count, errors)
    _count(quality, "total_contradictions", contradiction_count, errors)
    for field in (
        "missing_category_unit_ids", "duplicate_category_unit_ids",
        "unknown_category_unit_ids", "unknown_evidence_ref_ids",
    ):
        ids = _ids(quality.get(field))
        if ids:
            _issue(errors, "quality_audit_reports_issues", field=field, values=ids)

    quotes = {
        str(reference.get("quote") or "").strip()
        for unit in category_units
        for source_item in unit.get("source_items", [])
        for reference in source_item.get("evidence_refs", [])
        if str(reference.get("quote") or "").strip()
    }
    for quote in quotes:
        if re.search(rf"(?<!\w){re.escape(quote)}(?!\w)", raw_output_text):
            _issue(errors, "evidence_quote_repeated_in_output")
            break
    warnings.extend(
        {"issue_type": "theme_requires_researcher_review", "theme_id": _text(theme.get("theme_id"))}
        for component in components.values()
        if isinstance(component, dict)
        for theme in component.get("themes", [])
        if isinstance(theme, dict) and _text(theme.get("needs_researcher_review")).lower() == "true"
    )
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checked_category_unit_count": len(units_by_id),
        "checked_evidence_count": len(evidence_by_id),
        "checked_theme_count": theme_count,
        "checked_contradiction_count": contradiction_count,
    }
