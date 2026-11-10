from __future__ import annotations

import re
from typing import Any


TEMP_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _issue(errors: list[dict[str, Any]], issue_type: str, **details: Any) -> None:
    errors.append({"issue_type": issue_type, **details})


def _check_bool(value: Any, context: str, errors: list[dict[str, Any]]) -> None:
    if _text(value).lower() not in {"true", "false"}:
        _issue(errors, "invalid_boolean", context=context)


def _check_confidence(value: Any, context: str, errors: list[dict[str, Any]]) -> None:
    try:
        score = float(_text(value))
    except (TypeError, ValueError):
        _issue(errors, "invalid_confidence", context=context)
        return
    if not 0 <= score <= 1:
        _issue(errors, "invalid_confidence", context=context)


def _check_ids(
    values: list[str],
    allowed: set[str],
    *,
    context: str,
    errors: list[dict[str, Any]],
) -> None:
    if len(values) != len(set(values)):
        _issue(errors, "duplicate_reference_id", context=context)
    for value in values:
        if value not in allowed:
            _issue(errors, "unknown_reference_id", context=context, reference_id=value)


def _check_quality_count(
    quality: dict[str, Any],
    key: str,
    expected: int,
    errors: list[dict[str, Any]],
) -> None:
    try:
        actual = int(_text(quality.get(key)))
    except (TypeError, ValueError):
        _issue(errors, "invalid_quality_count", field=key)
        return
    if actual != expected:
        _issue(errors, "quality_count_mismatch", field=key, expected=expected, actual=actual)


def _metadata_errors(
    parsed: dict[str, Any],
    *,
    run_id: str,
    input_hash: str,
    source_phase_version: int,
    stage_key: str,
    phenomenon_version: int | None = None,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    expected = {
        "schema_version": "1.0",
        "run_id": run_id,
        "stage_key": stage_key,
        "input_hash": input_hash,
        "source_phase_version": str(source_phase_version),
    }
    if phenomenon_version is not None:
        expected["phenomenon_version"] = str(phenomenon_version)
    if set(metadata) != set(expected):
        _issue(errors, "invalid_metadata_fields", fields=sorted(metadata))
    for key, value in expected.items():
        if _text(metadata.get(key)) != value:
            _issue(errors, "metadata_mismatch", field=key)
    return errors


def _source_indexes(inputs: dict[str, Any]) -> dict[str, Any]:
    nodes = inputs.get("nodes", [])
    source_phenomena = inputs.get("source_phenomena", [])
    source_relationships = inputs.get("source_relationships", [])
    node_by_id = {str(item["node_id"]): item for item in nodes}
    phenomenon_by_id = {str(item["node_id"]): item for item in source_phenomena}
    relation_by_id = {
        str(item["source_relationship_id"]): item for item in source_relationships
    }
    source_item_ids = set(inputs.get("source_item_to_node", {}))
    category_unit_ids = set(inputs.get("unit_to_node", {}))
    theme_ids = {
        str(item["node_id"]) for item in nodes if item.get("node_type") == "theme"
    }
    evidence_by_id = inputs.get("evidence_by_id", {})
    evidence_to_source_items = inputs.get("evidence_to_source_items", {})
    evidence_by_node: dict[str, set[str]] = {
        node_id: set(item.get("evidence_ref_ids", []))
        for node_id, item in node_by_id.items()
    }
    evidence_by_phenomenon = {
        phenomenon_id: set(item.get("evidence_ref_ids", []))
        for phenomenon_id, item in phenomenon_by_id.items()
    }
    evidence_by_relation = {
        relation_id: set(item.get("evidence_ref_ids", []))
        for relation_id, item in relation_by_id.items()
    }
    evidence_by_source_item: dict[str, set[str]] = {}
    for evidence_id, owners in evidence_to_source_items.items():
        for source_item_id in owners:
            evidence_by_source_item.setdefault(str(source_item_id), set()).add(str(evidence_id))
    evidence_by_category_unit: dict[str, set[str]] = {}
    for node in nodes:
        for category_unit_id in node.get("category_unit_ids", []):
            evidence_by_category_unit.setdefault(str(category_unit_id), set()).update(
                str(evidence_id) for evidence_id in node.get("evidence_ref_ids", [])
            )
    return {
        "nodes": node_by_id,
        "source_phenomena": phenomenon_by_id,
        "source_relationships": relation_by_id,
        "node_ids": set(node_by_id),
        "source_phenomenon_ids": set(phenomenon_by_id),
        "source_relationship_ids": set(relation_by_id),
        "source_item_ids": source_item_ids,
        "category_unit_ids": category_unit_ids,
        "theme_ids": theme_ids,
        "evidence_by_id": evidence_by_id,
        "evidence_ids": set(evidence_by_id),
        "evidence_by_node": evidence_by_node,
        "evidence_by_phenomenon": evidence_by_phenomenon,
        "evidence_by_relation": evidence_by_relation,
        "evidence_by_source_item": evidence_by_source_item,
        "evidence_by_category_unit": evidence_by_category_unit,
    }


def _validate_phenomenon_record(
    record: dict[str, Any],
    *,
    context: str,
    indexes: dict[str, Any],
    errors: list[dict[str, Any]],
) -> set[str]:
    record_id = _text(record.get("phenomenon_id") or record.get("alternative_id"))
    if not TEMP_ID_RE.fullmatch(record_id):
        _issue(errors, "invalid_temporary_id", context=context)
    if record_id != _text(record.get("_heading_id")):
        _issue(errors, "heading_id_mismatch", context=context)
    required_text = (
        ("title", "definition", "selection_reason")
        if context == "central_phenomenon"
        else ("title", "definition", "selection_comparison")
    )
    for field in required_text:
        if not _text(record.get(field)):
            _issue(errors, "empty_required_value", context=context, field=field)

    refs = {
        "supporting_node_ids": indexes["node_ids"],
        "supporting_source_item_ids": indexes["source_item_ids"],
        "supporting_category_unit_ids": indexes["category_unit_ids"],
        "supporting_theme_ids": indexes["theme_ids"],
        "supporting_source_phenomenon_ids": indexes["source_phenomenon_ids"],
        "evidence_ref_ids": indexes["evidence_ids"],
    }
    support_ids: set[str] = set()
    for field, allowed in refs.items():
        values = _ids(record.get(field))
        _check_ids(values, allowed, context=f"{context}.{field}", errors=errors)
        support_ids.update(values)
    for field in ("strengths", "limitations"):
        if context != "central_phenomenon" and not _ids(record.get(field)):
            _issue(errors, "empty_required_list", context=context, field=field)
    evidence_ids = set(_ids(record.get("evidence_ref_ids")))
    allowed_evidence: set[str] = set()
    for node_id in _ids(record.get("supporting_node_ids")):
        allowed_evidence.update(indexes["evidence_by_node"].get(node_id, set()))
    for source_item_id in _ids(record.get("supporting_source_item_ids")):
        allowed_evidence.update(indexes["evidence_by_source_item"].get(source_item_id, set()))
    for category_unit_id in _ids(record.get("supporting_category_unit_ids")):
        allowed_evidence.update(indexes["evidence_by_category_unit"].get(category_unit_id, set()))
    for phenomenon_id in _ids(record.get("supporting_source_phenomenon_ids")):
        allowed_evidence.update(indexes["evidence_by_phenomenon"].get(phenomenon_id, set()))
    for evidence_id in evidence_ids:
        if evidence_id not in allowed_evidence:
            _issue(errors, "evidence_not_owned_by_supporting_reference", context=context, evidence_ref_id=evidence_id)
    if not support_ids or not evidence_ids:
        _issue(errors, "missing_supporting_references", context=context)
    _check_confidence(record.get("confidence"), context, errors)
    _check_bool(record.get("needs_researcher_review"), context, errors)
    return support_ids | evidence_ids


def validate_phenomenon_result(
    parsed: dict[str, Any],
    *,
    run_id: str,
    input_hash: str,
    source_phase_version: int,
    inputs: dict[str, Any],
    raw_output_text: str,
) -> dict[str, Any]:
    errors = _metadata_errors(
        parsed,
        run_id=run_id,
        input_hash=input_hash,
        source_phase_version=source_phase_version,
        stage_key="phenomenon_synthesis",
    )
    indexes = _source_indexes(inputs)
    central = parsed.get("central_phenomenon")
    if not isinstance(central, dict):
        central = {}
        _issue(errors, "missing_central_phenomenon")
    all_supporting_refs = _validate_phenomenon_record(
        central, context="central_phenomenon", indexes=indexes, errors=errors
    )
    alternatives = parsed.get("alternatives")
    contradictions = parsed.get("contradictions")
    if not isinstance(alternatives, list):
        alternatives = []
        _issue(errors, "invalid_alternatives")
    if not isinstance(contradictions, list):
        contradictions = []
        _issue(errors, "invalid_contradictions")
    record_ids = [_text(central.get("phenomenon_id"))]
    for index, alternative in enumerate(alternatives):
        if not isinstance(alternative, dict):
            _issue(errors, "invalid_alternative", index=index)
            continue
        record_ids.append(_text(alternative.get("alternative_id")))
        all_supporting_refs.update(_validate_phenomenon_record(
            alternative, context=f"alternative[{index}]", indexes=indexes, errors=errors
        ))
    if len(record_ids) != len(set(record_ids)):
        _issue(errors, "duplicate_temporary_id")

    contradiction_ids: set[str] = set()
    for index, item in enumerate(contradictions):
        context = f"contradiction[{index}]"
        if not isinstance(item, dict):
            _issue(errors, "invalid_contradiction", index=index)
            continue
        contradiction_id = _text(item.get("contradiction_id"))
        if not TEMP_ID_RE.fullmatch(contradiction_id) or contradiction_id != _text(item.get("_heading_id")):
            _issue(errors, "invalid_contradiction_id", context=context)
        if contradiction_id in contradiction_ids:
            _issue(errors, "duplicate_contradiction_id", contradiction_id=contradiction_id)
        contradiction_ids.add(contradiction_id)
        for field, allowed in (
            ("source_phenomenon_ids", indexes["source_phenomenon_ids"]),
            ("node_ids", indexes["node_ids"]),
            ("source_item_ids", indexes["source_item_ids"]),
            ("evidence_ref_ids", indexes["evidence_ids"]),
        ):
            values = _ids(item.get(field))
            _check_ids(values, allowed, context=f"{context}.{field}", errors=errors)
            all_supporting_refs.update(values)
        if not _text(item.get("description")) or not _text(item.get("reasoning")):
            _issue(errors, "empty_required_value", context=context)
        evidence_ids = set(_ids(item.get("evidence_ref_ids")))
        owned_evidence = set()
        for phenomenon_id in _ids(item.get("source_phenomenon_ids")):
            owned_evidence.update(indexes["evidence_by_phenomenon"].get(phenomenon_id, set()))
        for node_id in _ids(item.get("node_ids")):
            owned_evidence.update(indexes["evidence_by_node"].get(node_id, set()))
        for source_item_id in _ids(item.get("source_item_ids")):
            owned_evidence.update(indexes["evidence_by_source_item"].get(source_item_id, set()))
        if evidence_ids - owned_evidence:
            _issue(errors, "evidence_not_owned_by_supporting_reference", context=context)
        if not evidence_ids:
            _issue(errors, "missing_contradiction_evidence", context=context)
        _check_bool(item.get("needs_researcher_review"), context, errors)

    quality = parsed.get("quality_audit") if isinstance(parsed.get("quality_audit"), dict) else {}
    expected_quality = {
        "input_source_count", "input_phenomenon_count", "input_theme_count",
        "reviewed_source_phenomenon_ids", "reviewed_node_ids",
        "input_category_unit_count", "supporting_reference_count",
        "missing_supporting_references", "unsupported_claims", "contradictions",
        "analysis_limitations", "final_status",
    }
    if set(quality) != expected_quality:
        _issue(errors, "invalid_quality_fields")
    _check_quality_count(quality, "input_source_count", inputs["source_count"], errors)
    _check_quality_count(quality, "input_phenomenon_count", len(inputs["source_phenomena"]), errors)
    reviewed_phenomena = _ids(quality.get("reviewed_source_phenomenon_ids"))
    _check_ids(
        reviewed_phenomena,
        indexes["source_phenomenon_ids"],
        context="quality_audit.reviewed_source_phenomenon_ids",
        errors=errors,
    )
    if set(reviewed_phenomena) != indexes["source_phenomenon_ids"]:
        _issue(errors, "source_phenomenon_review_coverage_mismatch")
    reviewed_nodes = _ids(quality.get("reviewed_node_ids"))
    _check_ids(
        reviewed_nodes,
        indexes["node_ids"],
        context="quality_audit.reviewed_node_ids",
        errors=errors,
    )
    if set(reviewed_nodes) != indexes["node_ids"]:
        _issue(errors, "model_node_review_coverage_mismatch")
    _check_quality_count(
        quality,
        "input_theme_count",
        sum(node.get("node_type") == "theme" for node in inputs["nodes"]),
        errors,
    )
    _check_quality_count(
        quality,
        "input_category_unit_count",
        sum(len(node.get("category_unit_ids", [])) for node in inputs["nodes"]),
        errors,
    )
    _check_quality_count(quality, "supporting_reference_count", len(all_supporting_refs), errors)
    _check_quality_count(quality, "contradictions", len(contradictions), errors)
    for field in ("missing_supporting_references", "unsupported_claims"):
        if _ids(quality.get(field)):
            _issue(errors, "quality_audit_reports_issues", field=field)
    if _text(quality.get("final_status")) not in {"complete", "needs_review"}:
        _issue(errors, "invalid_final_status")
    quotes = {
        _text(item.get("quote"))
        for item in indexes["evidence_by_id"].values()
        if _text(item.get("quote"))
    }
    if any(quote in raw_output_text for quote in quotes):
        _issue(errors, "evidence_quote_repeated_in_output")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": [],
        "checked_source_count": inputs["source_count"],
        "checked_phenomenon_count": len(inputs["source_phenomena"]),
        "checked_node_count": len(inputs["nodes"]),
        "checked_evidence_count": len(indexes["evidence_ids"]),
        "checked_alternative_count": len(alternatives),
        "checked_contradiction_count": len(contradictions),
    }


def validate_relationship_result(
    parsed: dict[str, Any],
    *,
    run_id: str,
    input_hash: str,
    source_phase_version: int,
    phenomenon_version: int,
    inputs: dict[str, Any],
    model_nodes: list[dict[str, Any]],
    raw_output_text: str,
) -> dict[str, Any]:
    errors = _metadata_errors(
        parsed,
        run_id=run_id,
        input_hash=input_hash,
        source_phase_version=source_phase_version,
        stage_key="relationship_synthesis",
        phenomenon_version=phenomenon_version,
    )
    indexes = _source_indexes(inputs)
    node_by_id = {str(item["node_id"]): item for item in model_nodes}
    node_ids = set(node_by_id)
    relationships = parsed.get("relationships")
    unlinked = parsed.get("unlinked_nodes")
    contradictions = parsed.get("contradictions")
    for field_name, value in (
        ("relationships", relationships),
        ("unlinked_nodes", unlinked),
        ("contradictions", contradictions),
    ):
        if not isinstance(value, list):
            _issue(errors, f"invalid_{field_name}")
    relationships = relationships if isinstance(relationships, list) else []
    unlinked = unlinked if isinstance(unlinked, list) else []
    contradictions = contradictions if isinstance(contradictions, list) else []

    relation_ids: set[str] = set()
    canonical_relations: set[tuple[str, str, str, str]] = set()
    linked_nodes: set[str] = set()
    relation_counts = {"direct": 0, "interpretive": 0, "hypothetical": 0}
    for index, relation in enumerate(relationships):
        context = f"relationship[{index}]"
        if not isinstance(relation, dict):
            _issue(errors, "invalid_relationship", index=index)
            continue
        relation_id = _text(relation.get("relationship_id"))
        if not TEMP_ID_RE.fullmatch(relation_id) or relation_id != _text(relation.get("_heading_id")):
            _issue(errors, "invalid_relationship_id", context=context)
        if relation_id in relation_ids:
            _issue(errors, "duplicate_relationship_id", relationship_id=relation_id)
        relation_ids.add(relation_id)
        source_id = _text(relation.get("source_node_id"))
        target_id = _text(relation.get("target_node_id"))
        if source_id not in node_ids or target_id not in node_ids or source_id == target_id:
            _issue(errors, "invalid_node_reference", context=context)
        else:
            linked_nodes.update((source_id, target_id))
        relationship_type = _text(relation.get("relationship_type"))
        direction = _text(relation.get("direction"))
        if relationship_type not in relation_counts:
            _issue(errors, "invalid_relationship_type", context=context)
        else:
            relation_counts[relationship_type] += 1
        if direction not in {"source_to_target", "target_to_source", "bidirectional"}:
            _issue(errors, "invalid_relationship_direction", context=context)
        if not _text(relation.get("description")) or not _text(relation.get("reasoning")):
            _issue(errors, "empty_required_value", context=context)
        _check_confidence(relation.get("confidence"), context, errors)
        _check_bool(relation.get("needs_researcher_review"), context, errors)
        if direction == "target_to_source":
            canonical_source, canonical_target = target_id, source_id
        else:
            canonical_source, canonical_target = source_id, target_id
        if direction == "bidirectional" and canonical_target < canonical_source:
            canonical_source, canonical_target = canonical_target, canonical_source
        canonical = (canonical_source, canonical_target, relationship_type)
        if canonical in canonical_relations:
            _issue(errors, "duplicate_relationship", context=context)
        canonical_relations.add(canonical)

        refs = {
            "supporting_source_relationship_ids": indexes["source_relationship_ids"],
            "supporting_source_phenomenon_ids": indexes["source_phenomenon_ids"],
            "supporting_source_item_ids": indexes["source_item_ids"],
            "supporting_category_unit_ids": indexes["category_unit_ids"],
            "evidence_ref_ids": indexes["evidence_ids"],
        }
        reference_values: set[str] = set()
        for field, allowed in refs.items():
            values = _ids(relation.get(field))
            _check_ids(values, allowed, context=f"{context}.{field}", errors=errors)
            reference_values.update(values)
        if not reference_values:
            _issue(errors, "missing_relationship_support", context=context)
        source_relation_ids = set(_ids(relation.get("supporting_source_relationship_ids")))
        if relationship_type == "direct" and not source_relation_ids:
            _issue(errors, "direct_relationship_without_source_relation", context=context)
        endpoint_set = {source_id, target_id}
        endpoint_source_items = {
            str(item_id)
            for node_id in endpoint_set
            for item_id in node_by_id.get(node_id, {}).get("source_item_ids", [])
        }
        endpoint_category_units = {
            str(item_id)
            for node_id in endpoint_set
            for item_id in node_by_id.get(node_id, {}).get("category_unit_ids", [])
        }
        if set(_ids(relation.get("supporting_source_item_ids"))) - endpoint_source_items:
            _issue(errors, "relationship_source_item_not_owned_by_endpoints", context=context)
        if set(_ids(relation.get("supporting_category_unit_ids"))) - endpoint_category_units:
            _issue(errors, "relationship_category_unit_not_owned_by_endpoints", context=context)
        for source_relation_id in source_relation_ids:
            source_relation = indexes["source_relationships"].get(source_relation_id, {})
            if {
                str(source_relation.get("source_node_id")),
                str(source_relation.get("target_node_id")),
            } != endpoint_set:
                _issue(errors, "source_relationship_endpoint_mismatch", context=context)
        owned_evidence: set[str] = set()
        for source_relation_id in source_relation_ids:
            owned_evidence.update(indexes["evidence_by_relation"].get(source_relation_id, set()))
        for phenomenon_id in _ids(relation.get("supporting_source_phenomenon_ids")):
            owned_evidence.update(indexes["evidence_by_phenomenon"].get(phenomenon_id, set()))
        for source_item_id in _ids(relation.get("supporting_source_item_ids")):
            owned_evidence.update(indexes["evidence_by_source_item"].get(source_item_id, set()))
        for category_unit_id in _ids(relation.get("supporting_category_unit_ids")):
            owned_evidence.update(indexes["evidence_by_category_unit"].get(category_unit_id, set()))
        evidence_ids = set(_ids(relation.get("evidence_ref_ids")))
        if evidence_ids - owned_evidence:
            _issue(errors, "evidence_not_owned_by_relationship_support", context=context)

    expected_unlinked = node_ids - linked_nodes
    unlinked_ids: list[str] = []
    for index, item in enumerate(unlinked):
        context = f"unlinked_node[{index}]"
        if not isinstance(item, dict):
            _issue(errors, "invalid_unlinked_node", index=index)
            continue
        node_id = _text(item.get("node_id"))
        unlinked_ids.append(node_id)
        if node_id not in node_ids or node_id in linked_nodes:
            _issue(errors, "invalid_unlinked_node_reference", node_id=node_id)
        node = node_by_id.get(node_id, {})
        if _text(item.get("component_key")) != _text(node.get("component_key")):
            _issue(errors, "unlinked_component_mismatch", node_id=node_id)
        if not _text(item.get("reason")):
            _issue(errors, "empty_required_value", context=context, field="reason")
        _check_bool(item.get("needs_researcher_review"), context, errors)
    if len(unlinked_ids) != len(set(unlinked_ids)) or set(unlinked_ids) != expected_unlinked:
        _issue(errors, "unlinked_node_coverage_mismatch")

    contradiction_ids: set[str] = set()
    for index, item in enumerate(contradictions):
        context = f"contradiction[{index}]"
        if not isinstance(item, dict):
            _issue(errors, "invalid_contradiction", index=index)
            continue
        contradiction_id = _text(item.get("contradiction_id"))
        if not TEMP_ID_RE.fullmatch(contradiction_id) or contradiction_id != _text(item.get("_heading_id")):
            _issue(errors, "invalid_contradiction_id", context=context)
        if contradiction_id in contradiction_ids:
            _issue(errors, "duplicate_contradiction_id", contradiction_id=contradiction_id)
        contradiction_ids.add(contradiction_id)
        for field, allowed in (
            ("source_relationship_ids", indexes["source_relationship_ids"]),
            ("source_item_ids", indexes["source_item_ids"]),
            ("evidence_ref_ids", indexes["evidence_ids"]),
        ):
            _check_ids(_ids(item.get(field)), allowed, context=f"{context}.{field}", errors=errors)
        source_id = _text(item.get("source_node_id"))
        target_id = _text(item.get("target_node_id"))
        if source_id not in node_ids or target_id not in node_ids:
            _issue(errors, "invalid_node_reference", context=context)
        endpoint_set = {source_id, target_id}
        for source_relation_id in _ids(item.get("source_relationship_ids")):
            source_relation = indexes["source_relationships"].get(source_relation_id, {})
            if {
                str(source_relation.get("source_node_id")),
                str(source_relation.get("target_node_id")),
            } != endpoint_set:
                _issue(errors, "source_relationship_endpoint_mismatch", context=context)
        if not _text(item.get("description")) or not _text(item.get("reasoning")):
            _issue(errors, "empty_required_value", context=context)
        owned_evidence = set()
        for source_relation_id in _ids(item.get("source_relationship_ids")):
            owned_evidence.update(indexes["evidence_by_relation"].get(source_relation_id, set()))
        for source_item_id in _ids(item.get("source_item_ids")):
            owned_evidence.update(indexes["evidence_by_source_item"].get(source_item_id, set()))
        if set(_ids(item.get("evidence_ref_ids"))) - owned_evidence:
            _issue(errors, "evidence_not_owned_by_contradiction_support", context=context)
        if not _ids(item.get("evidence_ref_ids")):
            _issue(errors, "missing_contradiction_evidence", context=context)
        _check_bool(item.get("needs_researcher_review"), context, errors)

    quality = parsed.get("quality_audit") if isinstance(parsed.get("quality_audit"), dict) else {}
    expected_quality = {
        "input_node_count", "proposed_relationship_count", "direct_relationship_count",
        "interpretive_relationship_count", "hypothetical_relationship_count",
        "unlinked_node_count", "missing_supporting_references", "unsupported_relationships",
        "duplicate_relationships", "invalid_node_references", "invalid_evidence_references",
        "contradictions", "analysis_limitations", "final_status",
    }
    if set(quality) != expected_quality:
        _issue(errors, "invalid_quality_fields")
    _check_quality_count(quality, "input_node_count", len(model_nodes), errors)
    _check_quality_count(quality, "proposed_relationship_count", len(relationships), errors)
    _check_quality_count(quality, "direct_relationship_count", relation_counts["direct"], errors)
    _check_quality_count(quality, "interpretive_relationship_count", relation_counts["interpretive"], errors)
    _check_quality_count(quality, "hypothetical_relationship_count", relation_counts["hypothetical"], errors)
    _check_quality_count(quality, "unlinked_node_count", len(unlinked), errors)
    _check_quality_count(quality, "contradictions", len(contradictions), errors)
    for field in (
        "missing_supporting_references",
        "unsupported_relationships",
        "duplicate_relationships",
        "invalid_node_references",
        "invalid_evidence_references",
    ):
        if _ids(quality.get(field)):
            _issue(errors, "quality_audit_reports_issues", field=field)
    if _text(quality.get("final_status")) not in {"complete", "needs_review"}:
        _issue(errors, "invalid_final_status")
    quotes = {
        _text(item.get("quote"))
        for item in indexes["evidence_by_id"].values()
        if _text(item.get("quote"))
    }
    if any(quote in raw_output_text for quote in quotes):
        _issue(errors, "evidence_quote_repeated_in_output")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": [],
        "checked_node_count": len(model_nodes),
        "checked_relationship_count": len(relationships),
        "checked_source_relationship_count": len(inputs["source_relationships"]),
        "checked_evidence_count": len(indexes["evidence_ids"]),
        "checked_contradiction_count": len(contradictions),
    }
