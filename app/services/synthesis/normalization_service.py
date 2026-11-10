from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def string_ids(value: Any) -> list[str]:
    values = as_list(value)
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if item is None:
            continue
        normalized = str(item).strip()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


def stable_source_item_id(source_project_id: str, component_key: str, local_category_id: str) -> str:
    return f"{source_project_id}::{component_key}::{local_category_id}"


def quote_value(evidence: dict[str, Any]) -> str:
    for key in ("exact_quote", "quote", "text"):
        value = evidence.get(key)
        if isinstance(value, str):
            return value
    return ""


def make_evidence_record(
    evidence: dict[str, Any],
    *,
    source_project_id: str,
    source_interview_id: str,
    paragraphs_by_id: dict[str, str],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    raw_evidence = dict(evidence)
    paragraph_id = str(raw_evidence.get("paragraph_id") or "").strip()
    quote = quote_value(raw_evidence)
    quote_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    evidence_hash = stable_hash(raw_evidence)
    evidence_ref_id = f"{source_project_id}::{paragraph_id or 'missing-paragraph'}::{quote_hash}::{evidence_hash}"
    paragraph_text = paragraphs_by_id.get(paragraph_id)
    valid = bool(paragraph_id and paragraph_text is not None and quote and quote in paragraph_text)
    if not paragraph_id:
        issue = "missing_paragraph_id"
    elif paragraph_text is None:
        issue = "unknown_paragraph_id"
    elif not quote:
        issue = "missing_quote"
    elif quote not in paragraph_text:
        issue = "quote_not_in_snapshot"
    else:
        issue = None
    return {
        "evidence_ref_id": evidence_ref_id,
        "source_evidence_id": raw_evidence.get("evidence_id") or raw_evidence.get("id") or evidence_ref_id,
        "source_project_id": source_project_id,
        "source_interview_id": source_interview_id,
        "paragraph_id": paragraph_id or None,
        "quote_hash": quote_hash,
        "quote": quote,
        "interpretation": raw_evidence.get("interpretation"),
        "valid": valid,
        "validation_issue": issue,
        "provenance": [provenance],
        "raw_evidence": raw_evidence,
    }
