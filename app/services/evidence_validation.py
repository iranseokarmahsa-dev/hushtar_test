"""Non-destructive evidence checks used by staged and combined imports."""

import re
import hashlib
import json
from collections import Counter
from typing import Any

from rapidfuzz import fuzz


def normalize_quote_for_matching(value: str) -> str:
	value = value.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
	value = re.sub(r"[^\w\s]", " ", value.casefold())
	return " ".join(value.split())


def collect_evidence_warnings(
	stage1: dict[str, Any], interview: dict[str, Any], project: dict[str, Any]
) -> list[dict[str, Any]]:
	warnings, _ = audit_evidence(stage1, interview, project)
	return warnings


def audit_evidence(
	stage1: dict[str, Any], interview: dict[str, Any], project: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
	paragraph_text = {
		item["paragraph_id"]: item["text"] for item in project.get("paragraphs", [])
	}
	paragraph_ids = Counter(item.get("paragraph_id") for item in project.get("paragraphs", []))
	duplicate_paragraph_ids = {
		paragraph_id for paragraph_id, count in paragraph_ids.items()
		if paragraph_id and count > 1
	}
	concept_paragraphs = {
		item["concept_id"]: set(item.get("paragraph_ids", []))
		for item in project.get("concepts", [])
	}
	warnings: list[dict[str, Any]] = []
	auto_matches: list[dict[str, Any]] = []
	seen: set[tuple[str, str, str, str]] = set()

	def inspect_evidence(
		evidence: dict[str, Any],
		stage: str,
		record_type: str,
		record_id: str,
		allowed_paragraphs: set[str] | None = None,
	) -> None:
		paragraph_id = evidence.get("paragraph_id", "")
		quote = evidence.get("exact_quote", "")
		if paragraph_id in duplicate_paragraph_ids:
			status = "duplicate"
			score = None
		elif allowed_paragraphs is not None and paragraph_id not in allowed_paragraphs:
			status = "paragraph_not_assigned"
			score = None
		elif paragraph_id not in paragraph_text:
			status = "paragraph_not_found"
			score = None
		elif quote in paragraph_text[paragraph_id]:
			return
		else:
			normalized_quote = normalize_quote_for_matching(str(quote))
			normalized_source = normalize_quote_for_matching(paragraph_text[paragraph_id])
			score = round(fuzz.partial_ratio(normalized_quote, normalized_source)) if normalized_quote else 0
			status = "auto_matched" if score >= 90 else "fuzzy_match" if score >= 70 else "unmatched"

		key = (stage, record_id, str(paragraph_id), str(quote))
		if key in seen:
			warnings.append({
				"stage": stage,
				"record_type": record_type,
				"record_id": record_id,
				"paragraph_id": str(paragraph_id),
				"exact_quote": str(quote),
				"status": "duplicate",
				"similarity": None,
			})
			return
		seen.add(key)
		result = {
			"stage": stage,
			"record_type": record_type,
			"record_id": record_id,
			"paragraph_id": str(paragraph_id),
			"exact_quote": str(quote),
			"similarity": score,
		}
		if status == "auto_matched":
			source = paragraph_text[paragraph_id]
			alignment = fuzz.partial_ratio_alignment(str(quote), source)
			if alignment and alignment.score >= 90 and alignment.dest_end > alignment.dest_start:
				auto_matches.append({
					**result,
					"status": "auto_matched",
					"matched_quote": source[alignment.dest_start:alignment.dest_end],
				})
				return
			status = "fuzzy_match"
		warnings.append({**result, "status": status})

	for review in stage1.get("concept_reviews", []):
		concept_id = review.get("concept_id", "نامشخص")
		allowed = concept_paragraphs.get(concept_id, set())
		if review.get("paragraph_id") not in allowed:
			warnings.append({
				"stage": "stage1",
				"record_type": "concept",
				"record_id": concept_id,
				"paragraph_id": str(review.get("paragraph_id", "")),
				"exact_quote": "",
				"status": "paragraph_not_assigned",
				"similarity": None,
			})
		for evidence in review.get("evidence", []):
			inspect_evidence(evidence, "stage1", "concept", concept_id, allowed)

	def visit(value: Any, context_type: str = "analysis", context_id: str = "interview") -> None:
		if isinstance(value, dict):
			next_type, next_id = context_type, context_id
			for key, kind in (
				("concept_id", "concept"), ("relationship_id", "relationship"),
				("subcategory_id", "subcategory"), ("property_id", "property"),
				("category_id", "category"),
			):
				if value.get(key):
					next_type, next_id = kind, str(value[key])
					break
			if isinstance(value.get("evidence"), list):
				for evidence in value["evidence"]:
					inspect_evidence(evidence, "stage2", next_type, next_id)
			for key, child in value.items():
				if key != "evidence":
					visit(child, next_type, next_id)
		elif isinstance(value, list):
			for child in value:
				visit(child, context_type, context_id)

	visit(interview)
	return warnings, auto_matches


def group_evidence_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
	groups: dict[str, dict[str, Any]] = {}
	for warning_index, warning in enumerate(warnings):
		key = json.dumps(
			[
				warning.get("status", ""),
				str(warning.get("paragraph_id", "")),
				normalize_quote_for_matching(str(warning.get("exact_quote", ""))),
			],
			ensure_ascii=False,
			separators=(",", ":"),
		)
		issue_id = "evidence-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
		group = groups.setdefault(issue_id, {
			"issue_id": issue_id,
			"status": warning.get("status", ""),
			"paragraph_id": warning.get("paragraph_id", ""),
			"exact_quote": warning.get("exact_quote", ""),
			"similarity": warning.get("similarity"),
			"references": [],
			"warning_indices": [],
		})
		group["warning_indices"].append(warning_index)
		reference = {
			"stage": warning.get("stage", ""),
			"record_type": warning.get("record_type", ""),
			"record_id": warning.get("record_id", ""),
		}
		if reference not in group["references"]:
			group["references"].append(reference)
		if warning.get("similarity") is not None:
			group["similarity"] = min(
				group["similarity"] if group["similarity"] is not None else 100,
				warning["similarity"],
			)
	return list(groups.values())


def apply_auto_evidence_matches(
	stage1: dict[str, Any],
	interview: dict[str, Any],
	auto_matches: list[dict[str, Any]],
) -> None:
	replacements = {
		(
			item["stage"], item["record_type"], item["record_id"],
			item["paragraph_id"], item["exact_quote"],
		): item["matched_quote"]
		for item in auto_matches
	}

	def update_record(record: dict[str, Any], stage: str, record_type: str, record_id: str) -> None:
		for evidence in record.get("evidence", []):
			key = (
				stage, record_type, record_id,
				str(evidence.get("paragraph_id", "")),
				str(evidence.get("exact_quote", "")),
			)
			if key in replacements:
				evidence["exact_quote"] = replacements[key]

	for review in stage1.get("concept_reviews", []):
		update_record(review, "stage1", "concept", str(review.get("concept_id", "نامشخص")))

	def visit(value: Any, context_type: str = "analysis", context_id: str = "interview") -> None:
		if isinstance(value, dict):
			next_type, next_id = context_type, context_id
			for key, kind in (
				("concept_id", "concept"), ("relationship_id", "relationship"),
				("subcategory_id", "subcategory"), ("property_id", "property"),
				("category_id", "category"),
			):
				if value.get(key):
					next_type, next_id = kind, str(value[key])
					break
			update_record(value, "stage2", next_type, next_id)
			for key, child in value.items():
				if key != "evidence":
					visit(child, next_type, next_id)
		elif isinstance(value, list):
			for child in value:
				visit(child, context_type, context_id)

	visit(interview)


def merge_auto_evidence_matches(
	previous: list[dict[str, Any]], current: list[dict[str, Any]]
) -> list[dict[str, Any]]:
	merged: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
	for item in [*previous, *current]:
		key = (
			item.get("stage", ""), item.get("record_type", ""), item.get("record_id", ""),
			item.get("paragraph_id", ""), item.get("exact_quote", ""),
		)
		merged[key] = item
	return list(merged.values())
