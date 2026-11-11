"""Non-destructive evidence checks used by staged and combined imports."""

import re
from typing import Any

from rapidfuzz import fuzz


def normalize_quote_for_matching(value: str) -> str:
	value = value.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
	value = re.sub(r"[^\w\s]", " ", value.casefold())
	return " ".join(value.split())


def collect_evidence_warnings(
	stage1: dict[str, Any], interview: dict[str, Any], project: dict[str, Any]
) -> list[dict[str, Any]]:
	paragraph_text = {
		item["paragraph_id"]: item["text"] for item in project.get("paragraphs", [])
	}
	concept_paragraphs = {
		item["concept_id"]: set(item.get("paragraph_ids", []))
		for item in project.get("concepts", [])
	}
	warnings: list[dict[str, Any]] = []
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
		if allowed_paragraphs is not None and paragraph_id not in allowed_paragraphs:
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
			status = "fuzzy_match" if score >= 90 else "unmatched"

		key = (stage, record_id, str(paragraph_id), str(quote))
		if key in seen:
			return
		seen.add(key)
		warnings.append({
			"stage": stage,
			"record_type": record_type,
			"record_id": record_id,
			"paragraph_id": str(paragraph_id),
			"exact_quote": str(quote),
			"status": status,
			"similarity": score,
		})

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
	return warnings
