"""Build independent Markdown prompts from a single paragraph-owned input."""

import re
from pathlib import Path
from typing import Any

import yaml

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"


class _PromptSafeDumper(yaml.SafeDumper):
	def ignore_aliases(self, data: Any) -> bool:
		return True


def build_prompts(project: dict[str, Any]) -> dict[str, str]:
	input_data = _project_input(project)
	return {
		"stage1": _render_prompt("grounded_theory_stage1.md", input_data),
		"stage2": _render_prompt(
			"grounded_theory_stage2.md", _project_input(project, include_initial_codes=False)
		),
	}


def build_prompt(project: dict[str, Any]) -> str:
	"""Compatibility alias for callers that request a single prompt."""
	return build_prompts(project)["stage1"]


def _render_prompt(filename: str, input_data: dict[str, Any]) -> str:
	prompt = (PROMPT_DIR / filename).read_text(encoding="utf-8")
	input_yaml = yaml.dump(
		input_data, Dumper=_PromptSafeDumper, allow_unicode=True, sort_keys=False, width=1000
	)
	return prompt.replace("{{INPUT_DATA_MARKDOWN}}", f"```yaml\n{input_yaml}```")


def _project_input(
	project: dict[str, Any], *, include_initial_codes: bool = True
) -> dict[str, Any]:
	if not project.get("paragraphs"):
		raise ValueError("ابتدا متن مصاحبه را بارگذاری کنید.")
	paragraph_ids = [item["paragraph_id"] for item in project["paragraphs"]]
	if len(paragraph_ids) != len(set(paragraph_ids)):
		raise ValueError("شناسه پاراگراف‌های ورودی باید یکتا باشد.")
	paragraphs_by_id: dict[str, dict[str, Any]] = {
		paragraph["paragraph_id"]: {
			"paragraph_id": paragraph["paragraph_id"],
			"text": paragraph["text"],
			"concepts": [],
		}
		for paragraph in project.get("paragraphs", [])
	}
	known_paragraph_ids = set(paragraphs_by_id)
	known_concept_ids: set[str] = set()
	for concept in project.get("concepts", []):
		concept_id = concept.get("concept_id")
		if not concept_id or concept_id in known_concept_ids:
			raise ValueError("شناسه مفاهیم ورودی باید غیرخالی و یکتا باشد.")
		known_concept_ids.add(concept_id)
		concept_item = {
			"concept_id": concept_id,
			"title": concept.get("original_concept", ""),
		}
		if include_initial_codes:
			concept_item["initial_code"] = concept.get("original_code", "")
		concept_paragraph_ids = concept.get("paragraph_ids", [])
		if not concept_paragraph_ids or not set(concept_paragraph_ids).issubset(known_paragraph_ids):
			raise ValueError(f"مفهوم {concept_id} باید به پاراگراف موجود متصل باشد.")
		if len(concept_paragraph_ids) != 1:
			raise ValueError(f"مفهوم {concept_id} باید فقط یک پاراگراف پشتیبان داشته باشد.")
		paragraph_id = concept_paragraph_ids[0]
		paragraphs_by_id[paragraph_id]["concepts"].append(dict(concept_item))

	return {
		"project_id": project["project_id"],
		"interview_id": project.get("interview_id") or project["project_id"],
		"title": project.get("title", ""),
		"topic": project.get("topic", ""),
		"paragraphs": list(paragraphs_by_id.values()),
	}


def parse_project_markdown(raw_markdown: str) -> dict[str, Any]:
	"""Read project YAML specifically from the section titled «داده‌های پروژه»."""
	sections = re.split(r"^##\s+داده‌های پروژه\s*$", raw_markdown, flags=re.MULTILINE)
	if len(sections) != 2:
		raise ValueError("فایل Markdown باید دقیقاً یک بخش «## داده‌های پروژه» داشته باشد.")
	payload = _load_yaml_document(sections[1])
	if not isinstance(payload, dict):
		raise ValueError("ریشه داده‌های پروژه باید یک نگاشت YAML باشد.")
	required = {"project_id", "interview_id", "title", "topic", "paragraphs"}
	if not required.issubset(payload):
		raise ValueError("داده‌های پروژه باید project_id، interview_id، title، topic و paragraphs داشته باشند.")
	if "concepts" in payload:
		raise ValueError("فهرست مستقل concepts مجاز نیست؛ مفاهیم را زیر پاراگراف مربوط قرار دهید.")
	if not isinstance(payload["project_id"], str) or not payload["project_id"].strip():
		raise ValueError("project_id باید رشته‌ای غیرخالی باشد.")
	if not isinstance(payload["title"], str) or not payload["title"].strip():
		raise ValueError("title باید رشته‌ای غیرخالی باشد.")
	if not isinstance(payload["interview_id"], str) or not payload["interview_id"].strip():
		raise ValueError("interview_id باید رشته‌ای غیرخالی باشد.")
	if not isinstance(payload["topic"], str):
		raise ValueError("topic باید رشته باشد.")
	paragraphs = payload["paragraphs"]
	if not isinstance(paragraphs, list) or not paragraphs:
		raise ValueError("فیلد paragraphs باید آرایه باشد.")
	concepts: list[dict[str, Any]] = []
	paragraph_ids: set[str] = set()
	concept_ids: set[str] = set()
	for paragraph in paragraphs:
		if not isinstance(paragraph, dict) or not {"paragraph_id", "text"}.issubset(paragraph):
			raise ValueError("هر پاراگراف باید paragraph_id و text داشته باشد.")
		if not isinstance(paragraph["paragraph_id"], str) or not paragraph["paragraph_id"].strip() or paragraph["paragraph_id"] in paragraph_ids:
			raise ValueError("شناسه پاراگراف‌ها باید غیرخالی و یکتا باشد.")
		if not isinstance(paragraph["text"], str):
			raise ValueError("متن هر پاراگراف باید رشته باشد.")
		if not isinstance(paragraph.get("concepts", []), list):
			raise ValueError("فیلد concepts هر پاراگراف باید آرایه باشد.")
		paragraph_ids.add(paragraph["paragraph_id"])
		for concept in paragraph.get("concepts", []):
			if not isinstance(concept, dict) or not {"concept_id", "title", "initial_code"}.issubset(concept):
				raise ValueError("هر مفهوم باید concept_id، title و initial_code داشته باشد.")
			if not isinstance(concept["concept_id"], str) or not concept["concept_id"].strip() or concept["concept_id"] in concept_ids:
				raise ValueError("شناسه مفاهیم باید غیرخالی و یکتا باشد.")
			if not isinstance(concept["title"], str):
				raise ValueError("عنوان هر مفهوم باید رشته باشد.")
			if not isinstance(concept.get("initial_code", ""), str):
				raise ValueError("initial_code هر مفهوم باید رشته باشد.")
			concept_ids.add(concept["concept_id"])
			concepts.append({
				"concept_id": concept["concept_id"],
				"paragraph_ids": [paragraph["paragraph_id"]],
				"original_concept": concept["title"],
				"original_code": concept.get("initial_code", ""),
			})
	return {
		"project_id": payload["project_id"],
		"interview_id": payload.get("interview_id") or payload["project_id"],
		"title": payload["title"],
		"topic": payload.get("topic", ""),
		"paragraphs": [
			{"paragraph_id": item["paragraph_id"], "text": item["text"]}
			for item in paragraphs
		],
		"concepts": concepts,
	}


def _load_yaml_document(markdown: str) -> Any:
	blocks = re.findall(r"```yaml\s*\n(.*?)```", markdown, flags=re.IGNORECASE | re.DOTALL)
	if len(blocks) > 1:
		raise ValueError("فقط یک بلوک YAML داده‌های پروژه مجاز است.")
	document = blocks[0] if blocks else markdown.strip()
	try:
		return yaml.safe_load(document)
	except yaml.YAMLError as error:
		raise ValueError(f"ساختار YAML داده‌های پروژه معتبر نیست: {error}") from error
