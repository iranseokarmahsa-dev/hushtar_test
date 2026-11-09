"""Build the versioned grounded-theory prompt from a project record."""

import json
from pathlib import Path
from typing import Any

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "grounded_theory_v2.txt"


def build_prompt(project: dict[str, Any]) -> str:
	if not project.get("paragraphs"):
		raise ValueError("ابتدا متن مصاحبه را بارگذاری کنید.")
	concepts_by_paragraph: dict[str, list[dict[str, str]]] = {
		paragraph["paragraph_id"]: [] for paragraph in project["paragraphs"]
	}
	for concept in project["concepts"]:
		for paragraph_id in concept.get("paragraph_ids", []):
			if paragraph_id in concepts_by_paragraph:
				concepts_by_paragraph[paragraph_id].append(
					{
						"concept_id": concept["concept_id"],
						"title": concept["original_concept"],
						"initial_code": concept["original_code"],
					}
				)
	paragraphs = [
		{**paragraph, "concepts": concepts_by_paragraph[paragraph["paragraph_id"]]}
		for paragraph in project["paragraphs"]
	]
	paragraphs_by_id = {item["paragraph_id"]: item for item in paragraphs}
	concepts = [
		{
			**concept,
			"supporting_paragraphs": [
				paragraphs_by_id[paragraph_id]
				for paragraph_id in concept.get("paragraph_ids", [])
				if paragraph_id in paragraphs_by_id
			],
		}
		for concept in project["concepts"]
	]
	input_data = {
		"project_id": project["project_id"],
		"interview_id": project.get("interview_id") or project["project_id"],
		"title": project["title"],
		"topic": project["topic"],
		"paragraphs": paragraphs,
		"concepts": concepts,
	}
	prompt = PROMPT_PATH.read_text(encoding="utf-8")
	return prompt.replace("{{INPUT_DATA_JSON}}", json.dumps(input_data, ensure_ascii=False, indent=2))
