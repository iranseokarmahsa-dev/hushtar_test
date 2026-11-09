"""Validate AI output shape and preserve traceability to source data."""

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
SCHEMA_PATH = SCHEMA_DIR / "ai_analysis.schema.json"
COMBINED_SCHEMA_PATH = SCHEMA_DIR / "ai_combined_output.schema.json"
CONCEPT_REVIEW_SCHEMA_PATH = SCHEMA_DIR / "concept_review.schema.json"
INTERVIEW_ANALYSIS_SCHEMA_PATH = SCHEMA_DIR / "interview_analysis.schema.json"


def validate_analysis(payload: dict[str, Any], project: dict[str, Any]) -> None:
	if payload.get("schema_version") == "2.0":
		_validate_combined_analysis(payload, project)
		return
	_validate_legacy_analysis(payload, project)


def _validate_combined_analysis(payload: dict[str, Any], project: dict[str, Any]) -> None:
	schema = json.loads(COMBINED_SCHEMA_PATH.read_text(encoding="utf-8"))
	errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda error: list(error.path))
	if errors:
		first_error = errors[0]
		location = ".".join(str(part) for part in first_error.path) or "root"
		raise ValueError(f"ساختار JSON نامعتبر است ({location}): {first_error.message}")
	for section, section_path in (
		(payload["concept_reviews"], CONCEPT_REVIEW_SCHEMA_PATH),
		(payload["interview_analysis"], INTERVIEW_ANALYSIS_SCHEMA_PATH),
	):
		section_schema = json.loads(section_path.read_text(encoding="utf-8"))
		section_errors = sorted(
			Draft202012Validator(section_schema).iter_errors(section),
			key=lambda error: list(error.path),
		)
		if section_errors:
			first_error = section_errors[0]
			location = ".".join(str(part) for part in first_error.path) or "section"
			raise ValueError(f"ساختار JSON نامعتبر است ({location}): {first_error.message}")

	if payload["project_id"] != project["project_id"]:
		raise ValueError("شناسه پروژه در JSON با پروژه انتخاب‌شده مطابقت ندارد.")
	expected_interview_id = project.get("interview_id") or project["project_id"]
	if payload["interview_id"] != expected_interview_id:
		raise ValueError("شناسه مصاحبه در JSON با مصاحبه انتخاب‌شده مطابقت ندارد.")

	concepts_by_id = {concept["concept_id"]: concept for concept in project["concepts"]}
	concept_ids = set(concepts_by_id)
	paragraph_text = {paragraph["paragraph_id"]: paragraph["text"] for paragraph in project["paragraphs"]}
	reviews = payload["concept_reviews"]
	reviewed_ids = [review["concept_id"] for review in reviews]
	if len(reviewed_ids) != len(set(reviewed_ids)):
		raise ValueError("شناسه مفهوم در concept_reviews تکراری است.")
	if not set(reviewed_ids).issubset(concept_ids):
		raise ValueError("شناسه مفهوم ناشناخته در concept_reviews وجود دارد.")
	source_scope = payload["source_scope"]
	counts = {
		"total_paragraphs": len(project["paragraphs"]),
		"total_concepts_received": len(concept_ids),
		"total_concepts_reviewed": len(reviewed_ids),
	}
	for field, expected in counts.items():
		if source_scope[field] != expected:
			raise ValueError(f"مقدار {field} در source_scope با داده‌های پروژه مطابقت ندارد.")

	known_paragraph_ids = set(paragraph_text)
	for review in reviews:
		source = concepts_by_id[review["concept_id"]]
		if review["original_concept"] != source["original_concept"]:
			raise ValueError(f"عنوان اولیه مفهوم {review['concept_id']} با داده پژوهشگر تغییر کرده است.")
		if review["original_code"] != source["original_code"]:
			raise ValueError(f"کد اولیه مفهوم {review['concept_id']} با داده پژوهشگر تغییر کرده است.")
		if review["paragraph_id"] not in source["paragraph_ids"]:
			raise ValueError(f"شناسه پاراگراف مفهوم {review['concept_id']} با منبع آن مطابقت ندارد.")
		for evidence in review["evidence"]:
			paragraph_id = evidence["paragraph_id"]
			if paragraph_id not in source["paragraph_ids"]:
				raise ValueError(f"شاهد مفهوم {review['concept_id']} از پاراگراف مرتبط با آن نیست.")
			if evidence["exact_quote"] not in paragraph_text[paragraph_id]:
				raise ValueError(f"نقل‌قول مفهوم {review['concept_id']} عیناً در متن پاراگراف پیدا نشد.")

	interview_analysis = payload["interview_analysis"]
	category_ids = [item["category_id"] for item in interview_analysis["categories"]]
	if len(category_ids) != len(set(category_ids)):
		raise ValueError("شناسه مقوله در interview_analysis تکراری است.")
	relationship_ids = [item["relationship_id"] for item in interview_analysis["relationships"]]
	if len(relationship_ids) != len(set(relationship_ids)):
		raise ValueError("شناسه رابطه در interview_analysis تکراری است.")
	_validate_subcategory_ids(interview_analysis["categories"])
	category_id_set = set(category_ids)
	quality_audit = interview_analysis["quality_audit"]
	missing_concept_ids = concept_ids - set(reviewed_ids)
	if missing_concept_ids:
		reported_missing_ids = {
			item.get("item_id")
			for item in interview_analysis["researcher_review"]
			if item.get("item_type") == "concept"
		}
		if not missing_concept_ids.issubset(reported_missing_ids):
			raise ValueError("مفاهیم بررسی‌نشده باید در researcher_review گزارش شوند.")
	_validate_references(reviews, concept_ids, known_paragraph_ids, category_id_set)
	_validate_references(interview_analysis, concept_ids, known_paragraph_ids, category_id_set)
	_validate_evidence(interview_analysis, paragraph_text)
	incomplete_flags = (
		missing_concept_ids
		or not quality_audit["all_concepts_reviewed"]
		or not quality_audit["all_ids_preserved"]
		or not quality_audit["all_evidence_verifiable"]
		or any(
			quality_audit[field]
			for field in ("unsupported_relationships_detected", "contradictions_detected", "missing_data_detected")
		)
	)
	status_disagrees = payload["analysis_status"] != quality_audit["final_status"]
	if incomplete_flags or status_disagrees or payload["analysis_status"] == "incomplete":
		was_marked_complete = payload["analysis_status"] == "complete" or quality_audit["final_status"] == "complete"
		payload["analysis_status"] = "incomplete"
		quality_audit["final_status"] = "incomplete"
		if missing_concept_ids:
			quality_audit["all_concepts_reviewed"] = False
			quality_audit["all_ids_preserved"] = False
		if was_marked_complete:
			limitation = "وضعیت به incomplete تغییر کرد، چون کنترل کیفیت یک هشدار یا ناسازگاری گزارش کرده است."
			if limitation not in quality_audit["analysis_limitations"]:
				quality_audit["analysis_limitations"].append(limitation)


def _validate_subcategory_ids(categories: list[dict[str, Any]]) -> None:
	owners: dict[str, str] = {}
	ids_by_category: dict[str, list[str]] = {}
	for category in categories:
		category_id = category["category_id"]
		subcategory_ids = [item["subcategory_id"] for item in category["subcategories"]]
		if len(subcategory_ids) != len(set(subcategory_ids)):
			raise ValueError(f"شناسه زیرمقوله در مقوله {category_id} تکراری است.")
		for subcategory_id in subcategory_ids:
			if subcategory_id in owners:
				raise ValueError(
					f"شناسه زیرمقوله {subcategory_id} بین مقوله‌های {owners[subcategory_id]} و {category_id} تکراری است."
				)
			owners[subcategory_id] = category_id
		ids_by_category[category_id] = subcategory_ids

	for category in categories:
		category_id = category["category_id"]
		subcategory_ids = ids_by_category[category_id]
		supplied_ids = category.get("subcategory_ids")
		if supplied_ids is None:
			category["subcategory_ids"] = list(subcategory_ids)
			continue
		if len(supplied_ids) != len(set(supplied_ids)):
			raise ValueError(f"فهرست subcategory_ids در مقوله {category_id} شناسه تکراری دارد.")
		for subcategory_id in supplied_ids:
			owner = owners.get(subcategory_id)
			if owner is not None and owner != category_id:
				raise ValueError(
					f"شناسه زیرمقوله {subcategory_id} به مقوله {owner} تعلق دارد، نه مقوله {category_id}."
				)
		if set(supplied_ids) != set(subcategory_ids):
			raise ValueError(
				f"شناسه‌های subcategory_ids در مقوله {category_id} با شناسه‌های subcategories همان مقوله مطابقت ندارند."
			)


def _validate_legacy_analysis(payload: dict[str, Any], project: dict[str, Any]) -> None:
	schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
	errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda error: list(error.path))
	if errors:
		first_error = errors[0]
		location = ".".join(str(part) for part in first_error.path) or "root"
		raise ValueError(f"ساختار JSON نامعتبر است ({location}): {first_error.message}")

	if payload["project_id"] != project["project_id"]:
		raise ValueError("شناسه پروژه در JSON با پروژه انتخاب‌شده مطابقت ندارد.")

	concepts_by_id = {concept["concept_id"]: concept for concept in project["concepts"]}
	concept_ids = set(concepts_by_id)
	paragraph_text = {paragraph["paragraph_id"]: paragraph["text"] for paragraph in project["paragraphs"]}
	reviewed_ids = [review.get("concept_id") for review in payload["concept_reviews"]]
	if len(reviewed_ids) != len(set(reviewed_ids)):
		raise ValueError("شناسه مفهوم در concept_reviews تکراری است.")
	if set(reviewed_ids) != concept_ids:
		raise ValueError("شناسه مفاهیم JSON باید دقیقاً با مفاهیم ثبت‌شده در پروژه برابر باشد.")
	metadata = payload["analysis_metadata"]
	expected_counts = {
		"total_paragraphs": len(project["paragraphs"]),
		"total_concepts_received": len(concept_ids),
		"total_concepts_analyzed": len(reviewed_ids),
		"total_categories": len(payload["categories"]),
		"total_relationships": len(payload["relationships"]),
	}
	for field, expected in expected_counts.items():
		if metadata[field] != expected:
			raise ValueError(f"مقدار {field} در analysis_metadata با داده‌های پروژه مطابقت ندارد.")

	known_paragraph_ids = set(paragraph_text)
	for concept in project["concepts"]:
		if not set(concept["paragraph_ids"]).issubset(known_paragraph_ids):
			raise ValueError(f"مفهوم {concept['concept_id']} به شناسه پاراگراف ناموجود اشاره می‌کند.")

	for review in payload["concept_reviews"]:
		source_concept = concepts_by_id[review["concept_id"]]
		if not review.get("suggested_concept", "").strip():
			raise ValueError(f"عنوان پیشنهادی مفهوم {review['concept_id']} خالی است.")
		if not review.get("concept_reason", "").strip():
			raise ValueError(f"دلیل بازبینی عنوان مفهوم {review['concept_id']} خالی است.")
		if review.get("code_evaluation") not in {"confirmed", "revise", "uncertain"}:
			raise ValueError(f"وضعیت ارزیابی کد مفهوم {review['concept_id']} نامعتبر است.")
		if not review.get("code_reason", "").strip():
			raise ValueError(f"دلیل ارزیابی کد مفهوم {review['concept_id']} خالی است.")
		if review.get("original_concept") != source_concept["original_concept"]:
			raise ValueError(f"عنوان اولیه مفهوم {review['concept_id']} با داده پژوهشگر تغییر کرده است.")
		if review.get("original_code") != source_concept["original_code"]:
			raise ValueError(f"کد اولیه مفهوم {review['concept_id']} با داده پژوهشگر تغییر کرده است.")
		concept_paragraph_ids = set(source_concept["paragraph_ids"])
		if review.get("paragraph_id") not in concept_paragraph_ids:
			raise ValueError(f"شناسه پاراگراف مفهوم {review['concept_id']} با منبع آن مطابقت ندارد.")
		if not review.get("evidence"):
			raise ValueError(f"برای مفهوم {review['concept_id']} شاهد متنی ارائه نشده است.")
		for evidence in review.get("evidence", []):
			paragraph_id = evidence.get("paragraph_id")
			quote = evidence.get("exact_quote", "")
			if paragraph_id not in paragraph_text:
				raise ValueError(f"شاهد مفهوم {review['concept_id']} به پاراگراف ناموجود اشاره می‌کند.")
			if paragraph_id not in concept_paragraph_ids:
				raise ValueError(f"شاهد مفهوم {review['concept_id']} از پاراگراف مرتبط با آن نیست.")
			if not quote or quote not in paragraph_text[paragraph_id]:
				raise ValueError(f"نقل‌قول مفهوم {review['concept_id']} عیناً در متن پاراگراف پیدا نشد.")

	category_ids = {
		item.get("category_id") for item in payload["categories"] if item.get("category_id")
	}
	diagram = payload["diagram"]
	node_ids = [node["node_id"] for node in diagram["nodes"]]
	if len(node_ids) != len(set(node_ids)):
		raise ValueError("شناسه گره در diagram تکراری است.")
	node_id_set = set(node_ids)
	edge_ids = [edge["edge_id"] for edge in diagram["edges"]]
	if len(edge_ids) != len(set(edge_ids)):
		raise ValueError("شناسه یال در diagram تکراری است.")
	for node in diagram["nodes"]:
		if not set(node["category_ids"]).issubset(category_ids):
			raise ValueError(f"گره نمودار {node['node_id']} به شناسه مقوله ناموجود اشاره می‌کند.")
	for edge in diagram["edges"]:
		if edge["source"] not in node_id_set or edge["target"] not in node_id_set:
			raise ValueError(f"یال نمودار {edge['edge_id']} به گره ناموجود اشاره می‌کند.")

	quality_audit = payload["quality_audit"]
	if payload["analysis_status"] != quality_audit["final_status"]:
		raise ValueError("analysis_status با quality_audit.final_status مطابقت ندارد.")
	if not quality_audit["all_concepts_processed"] or not quality_audit["all_ids_preserved"]:
		raise ValueError("خروجی همه مفاهیم ورودی را با شناسه‌های اصلی پوشش نمی‌دهد.")
	if not quality_audit["all_evidence_verifiable"]:
		raise ValueError("خروجی اعلام می‌کند که بخشی از شواهد قابل راستی‌آزمایی نیست.")
	if payload["analysis_status"] == "complete" and any(
		quality_audit[field]
		for field in ("unsupported_relationships_detected", "contradictions_detected", "missing_data_detected")
	):
		raise ValueError("تحلیل دارای داده ناقص، تناقض یا رابطه پشتیبانی‌نشده است و نمی‌تواند کامل باشد.")

	_validate_references(payload, concept_ids, known_paragraph_ids, category_ids)
	_validate_evidence(payload, paragraph_text)


def _validate_references(
	value: Any,
	concept_ids: set[str],
	paragraph_ids: set[str],
	category_ids: set[str],
	key: str = "",
) -> None:
	concept_keys = {"concept_id", "concept_ids", "supporting_concept_ids", "related_concept_ids"}
	paragraph_keys = {"paragraph_id", "paragraph_ids"}
	category_keys = {
		"category_id",
		"category_ids",
		"source_category_id",
		"target_category_id",
		"supporting_category_ids",
	}
	if isinstance(value, dict):
		for child_key, child in value.items():
			_validate_references(child, concept_ids, paragraph_ids, category_ids, child_key)
	elif isinstance(value, list):
		for child in value:
			_validate_references(child, concept_ids, paragraph_ids, category_ids, key)
	elif isinstance(value, str):
		allowed = (
			concept_ids
			if key in concept_keys
			else paragraph_ids
			if key in paragraph_keys
			else category_ids
			if key in category_keys
			else None
		)
		if allowed is not None and value not in allowed:
			raise ValueError(f"شناسه‌ی ارجاع‌شده وجود ندارد: {value}")


def _validate_evidence(value: Any, paragraph_text: dict[str, str]) -> None:
	if isinstance(value, dict):
		for key, child in value.items():
			if key == "evidence":
				if not isinstance(child, list):
					raise ValueError("فیلد evidence باید آرایه‌ای از شواهد باشد.")
				for item in child:
					if not isinstance(item, dict):
						raise ValueError("هر عضو آرایه evidence باید شیء JSON باشد.")
					if not {"paragraph_id", "exact_quote", "interpretation"}.issubset(item):
						raise ValueError("هر شاهد باید paragraph_id، exact_quote و interpretation داشته باشد.")
					paragraph_id = item.get("paragraph_id")
					quote = item.get("exact_quote", "")
					interpretation = item.get("interpretation")
					if not isinstance(interpretation, str) or not interpretation.strip():
						raise ValueError("فیلد interpretation در تمام شواهد الزامی است.")
					if not isinstance(paragraph_id, str) or paragraph_id not in paragraph_text:
						raise ValueError("شاهد به شناسه پاراگراف ناموجود اشاره می‌کند.")
					if not isinstance(quote, str) or not quote or quote not in paragraph_text[paragraph_id]:
						raise ValueError("نقل‌قول شاهد عیناً در متن پاراگراف پیدا نشد.")
			_validate_evidence(child, paragraph_text)
	elif isinstance(value, list):
		for child in value:
			_validate_evidence(child, paragraph_text)
