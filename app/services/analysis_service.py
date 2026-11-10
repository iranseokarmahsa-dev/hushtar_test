"""Coordinate prompt preparation and imported AI results.

Qualitative interpretation is performed by the external AI workflow and reviewed
by the researcher; this service does not independently code interview data.
"""

import re
from typing import Any

import yaml

from app.services.json_validator import validate_analysis
from app.services.prompt_builder import build_prompt


def prepare_prompt(project: dict[str, Any]) -> str:
	return build_prompt(project)


def prepare_analysis_results(data: dict[str, Any]) -> dict[str, Any]:
	"""Map validated AI output into explicit sections consumed by result pages."""
	if data.get("schema_version") == "2.0":
		interview_analysis = data["interview_analysis"]
		source_scope = data["source_scope"]
		concept_rows = _map_concept_reviews(data.get("concept_reviews", []))
		return {
			"concept_rows": concept_rows,
			"categories": interview_analysis.get("categories", []),
			"central_phenomenon": interview_analysis.get("central_phenomenon"),
			"paradigm_model": interview_analysis.get("paradigm_model", {}),
			"relationships": interview_analysis.get("relationships", []),
			"diagram": {"nodes": [], "edges": []},
			"researcher_review": interview_analysis.get("researcher_review", []),
			"quality_audit": interview_analysis.get("quality_audit", {}),
			"analysis_metadata": {
				"total_concepts_received": source_scope["total_concepts_received"],
				"total_concepts_analyzed": source_scope["total_concepts_reviewed"],
				"total_categories": len(interview_analysis.get("categories", [])),
				"total_relationships": len(interview_analysis.get("relationships", [])),
			},
			"analysis_status": data.get("analysis_status", "incomplete"),
		}
	concept_rows = [
		_map_concept_review(item)
		for item in data.get("concept_reviews", [])
	]
	return {
		"concept_rows": concept_rows,
		"categories": data.get("categories", []),
		"central_phenomenon": data.get("central_phenomenon"),
		"paradigm_model": data.get("paradigm_model", {}),
		"relationships": data.get("relationships", []),
		"diagram": data.get("diagram", {"nodes": [], "edges": []}),
		"researcher_review": data.get("researcher_review", []),
		"quality_audit": data.get("quality_audit", {}),
		"analysis_metadata": data.get("analysis_metadata", {}),
		"analysis_status": data.get("analysis_status", "incomplete"),
	}


def _map_concept_reviews(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
	return [_map_concept_review(item) for item in items]


def _map_concept_review(item: dict[str, Any]) -> dict[str, Any]:
	return {
		"concept_id": item.get("concept_id", ""),
		"paragraph_id": item.get("paragraph_id", ""),
		"original_concept": item.get("original_concept", ""),
		"suggested_concept": item.get("suggested_concept", ""),
		"concept_change_required": item.get("concept_change_required", False),
		"concept_reason": item.get("concept_reason", ""),
		"original_code": item.get("original_code", ""),
		"suggested_code": item.get("suggested_code", ""),
		"code_evaluation": item.get("code_evaluation", ""),
		"code_reason": item.get("code_reason", ""),
		"evidence": item.get("evidence", []),
		"confidence": item.get("confidence", 0),
		"needs_researcher_review": item.get("needs_researcher_review", True),
	}



def parse_analysis_markdown(raw_markdown: str, project: dict[str, Any]) -> dict[str, Any]:
	payload = load_analysis_markdown(raw_markdown)
	validate_analysis(payload, project)
	return payload


def load_analysis_markdown(raw_markdown: str) -> dict[str, Any]:
	blocks = re.findall(r"```yaml\s*\n(.*?)```", raw_markdown, flags=re.IGNORECASE | re.DOTALL)
	if len(blocks) != 1:
		raise ValueError("پاسخ باید دقیقاً یک بلوک ```yaml داشته باشد.")
	try:
		payload = yaml.safe_load(blocks[0])
	except yaml.YAMLError as error:
		raise ValueError(f"ساختار YAML داخل Markdown معتبر نیست: {error}") from error
	if not isinstance(payload, dict):
		raise ValueError("ریشه‌ی بلوک YAML باید یک نگاشت باشد.")
	return payload
