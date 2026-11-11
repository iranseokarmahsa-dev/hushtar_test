"""Coordinate prompt preparation and imported AI results.

Qualitative interpretation is performed by the external AI workflow and reviewed
by the researcher; this service does not independently code interview data.
"""

import json
import html
import re
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from app.services.evidence_validation import collect_evidence_warnings
from app.services.json_validator import INTERVIEW_ANALYSIS_SCHEMA_PATH, validate_analysis
from app.services.prompt_builder import build_prompts

STAGE1_SCHEMA_PATH = INTERVIEW_ANALYSIS_SCHEMA_PATH.parent / "stage1_output.schema.json"


def prepare_prompts(project: dict[str, Any]) -> dict[str, str]:
	return build_prompts(project)


def prepare_prompt(project: dict[str, Any]) -> str:
	return prepare_prompts(project)["stage1"]


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
		"concept_validity": item.get("concept_validity", ""),
		"original_code": item.get("original_code", ""),
		"suggested_code": item.get("suggested_code", ""),
		"code_evaluation": item.get("code_evaluation", ""),
		"code_reason": item.get("code_reason", ""),
		"overlap": item.get("overlap", {}),
		"evidence": item.get("evidence", []),
		"confidence": item.get("confidence", 0),
		"needs_researcher_review": item.get("needs_researcher_review", True),
	}



def parse_analysis_markdown(raw_markdown: str, project: dict[str, Any]) -> dict[str, Any]:
	payload = load_analysis_markdown(raw_markdown)
	validate_analysis(payload, project)
	return payload


def parse_stage_markdown(raw_markdown: str, project: dict[str, Any], stage: str) -> dict[str, Any]:
	"""Parse and validate one independently generated Markdown stage."""
	if stage not in {"stage1", "stage2"}:
		raise ValueError("مرحله تحلیل نامعتبر است.")
	payload = load_analysis_markdown(raw_markdown)
	schema_path = STAGE1_SCHEMA_PATH if stage == "stage1" else INTERVIEW_ANALYSIS_SCHEMA_PATH
	if stage == "stage2":
		if set(payload) != {"schema_version", "project_id", "interview_id", "interview_analysis"}:
			raise ValueError("خروجی مرحله دوم فقط باید فراداده و interview_analysis داشته باشد.")
		if payload.get("schema_version") != "HOOSHYAR_STAGE2_1.0":
			raise ValueError("schema_version خروجی باید HOOSHYAR_STAGE2_1.0 باشد.")
		section = payload.get("interview_analysis")
		if not isinstance(section, dict):
			raise ValueError("بخش interview_analysis در خروجی مرحله دوم الزامی است.")
		if "quality_audit" in section or "analysis_status" in payload:
			raise ValueError("مرحله دوم نباید quality_audit یا analysis_status تولید کند.")
		model = section.get("paradigm_model")
		if isinstance(model, dict):
			model["central_phenomenon"] = section.get("central_phenomenon")
	else:
		section = payload
		_normalize_stage1_values(section)
	schema = json.loads(schema_path.read_text(encoding="utf-8"))
	record_errors = (
		_filter_stage1_records(section, schema)
		if stage == "stage1"
		else _filter_stage2_records(section, schema)
	)
	errors = sorted(Draft202012Validator(schema).iter_errors(section), key=lambda error: list(error.path))
	if errors:
		error = errors[0]
		location = ".".join(str(part) for part in error.path) or "root"
		raise ValueError(f"ساختار خروجی {stage} نامعتبر است ({location}): {error.message}")
	if stage == "stage2":
		_validate_paradigm_shape(section)
	if payload.get("project_id") != project["project_id"]:
		raise ValueError(f"شناسه پروژه در خروجی {stage} با پروژه انتخاب‌شده مطابقت ندارد.")
	expected_interview_id = project.get("interview_id") or project["project_id"]
	if payload.get("interview_id") != expected_interview_id:
		raise ValueError(f"شناسه مصاحبه در خروجی {stage} با مصاحبه انتخاب‌شده مطابقت ندارد.")
	if record_errors:
		payload["_record_errors"] = record_errors
	return payload


def _filter_invalid_records(
	items: Any,
	item_schema: dict[str, Any],
	stage: str,
	record_type: str,
	id_field: str,
	definitions: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
	if not isinstance(items, list):
		return [], [{
			"stage": stage,
			"record_type": record_type,
			"record_id": "نامشخص",
			"reason": "فهرست رکوردها باید آرایه باشد؛ این فهرست خالی جایگزین شد.",
		}]
	valid: list[dict[str, Any]] = []
	issues: list[dict[str, str]] = []
	validator = Draft202012Validator({**item_schema, "$defs": definitions or {}})
	for index, item in enumerate(items):
		errors = sorted(validator.iter_errors(item), key=lambda error: list(error.path))
		if not errors:
			valid.append(item)
			continue
		error = errors[0]
		record_id = str(item.get(id_field, f"ردیف {index + 1}")) if isinstance(item, dict) else f"ردیف {index + 1}"
		location = ".".join(str(part) for part in error.path)
		issues.append({
			"stage": stage,
			"record_type": record_type,
			"record_id": record_id,
			"reason": f"رکورد از پردازش ساختاری کنار گذاشته شد ({location or 'root'}): {error.message}",
		})
	return valid, issues


def _filter_invalid_evidence(
	value: Any, stage: str, evidence_schema: dict[str, Any]
) -> list[dict[str, str]]:
	issues: list[dict[str, str]] = []
	validator = Draft202012Validator(evidence_schema)

	def visit(
		item: Any,
		record_type: str = "analysis",
		record_id: str = "interview",
		parent_category_id: str = "",
		review_owner: dict[str, Any] | None = None,
	) -> None:
		if isinstance(item, dict):
			next_type, next_id = record_type, record_id
			next_category_id = parent_category_id
			for key, kind in (
				("concept_id", "concept"),
				("relationship_id", "relationship"),
				("subcategory_id", "subcategory"),
				("property_id", "property"),
				("category_id", "category"),
			):
				if item.get(key):
					next_type, next_id = kind, str(item[key])
					break
			if item.get("category_id"):
				next_category_id = str(item["category_id"])
			next_owner = item if "needs_researcher_review" in item else review_owner
			if "evidence" in item:
				if not isinstance(item["evidence"], list):
					item["evidence"] = []
					issues.append({
						"stage": stage,
						"record_type": next_type,
						"record_id": next_id,
						"parent_category_id": next_category_id,
						"reason": "فهرست evidence آرایه نبود و خالی شد.",
					})
					if next_owner is not None:
						next_owner["needs_researcher_review"] = True
				else:
					valid_evidence = []
					for index, evidence in enumerate(item["evidence"]):
						errors = sorted(
							validator.iter_errors(evidence),
							key=lambda error: list(error.path),
						)
						if not errors:
							valid_evidence.append(evidence)
							continue
						error = errors[0]
						issues.append({
							"stage": stage,
							"record_type": next_type,
							"record_id": next_id,
							"parent_category_id": next_category_id,
							"reason": (
								f"شاهد شماره {index + 1} کنار گذاشته شد: "
								f"{error.message}"
							),
						})
						if next_owner is not None:
							next_owner["needs_researcher_review"] = True
					item["evidence"] = valid_evidence
			for key, child in item.items():
				if key != "evidence":
					visit(child, next_type, next_id, next_category_id, next_owner)
		elif isinstance(item, list):
			for child in item:
				visit(child, record_type, record_id, parent_category_id, review_owner)

	visit(value)
	return issues


def _filter_stage1_records(section: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, str]]:
	evidence_issues = _filter_invalid_evidence(
		section, "stage1", schema["$defs"]["evidence"]
	)
	raw_items = section.get("concept_reviews")
	section["concept_reviews"] = []
	envelope_errors = sorted(
		Draft202012Validator(schema).iter_errors(section),
		key=lambda error: list(error.path),
	)
	if envelope_errors:
		return evidence_issues
	valid, issues = _filter_invalid_records(
		raw_items,
		schema["properties"]["concept_reviews"]["items"],
		"stage1",
		"concept",
		"concept_id",
		schema.get("$defs", {}),
	)
	section["concept_reviews"] = valid
	return evidence_issues + issues


def _filter_stage2_records(section: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, str]]:
	issues: list[dict[str, str]] = []
	defs = schema["$defs"]
	evidence_issues = _filter_invalid_evidence(
		section, "stage2", defs["evidence"]["items"]
	)
	raw_categories = section.get("categories")
	raw_unclassified = section.get("unclassified_concepts")
	raw_relationships = section.get("relationships")
	raw_researcher_review = section.get("researcher_review")
	raw_model = section.get("paradigm_model")
	raw_phenomenon = section.get("central_phenomenon")
	section["categories"] = []
	section["unclassified_concepts"] = []
	section["relationships"] = []
	section["researcher_review"] = []
	section["central_phenomenon"] = None
	if isinstance(raw_model, dict):
		raw_components = {
			key: raw_model.get(key)
			for key in (
				"causal_conditions", "contextual_conditions", "intervening_conditions",
				"action_interaction_strategies", "consequences",
			)
		}
		section["paradigm_model"] = {
			**raw_model,
			**{key: [] for key in raw_components},
			"central_phenomenon": None,
		}
	else:
		raw_components = {}
		issues.append({
			"stage": "stage2",
			"record_type": "paradigm_model",
			"record_id": "paradigm_model",
			"reason": "مدل پارادایمی ساختار شیء نداشت؛ مؤلفه‌های آن خالی شدند.",
		})
		section["paradigm_model"] = {
			key: [] for key in (
				"causal_conditions", "contextual_conditions", "intervening_conditions",
				"action_interaction_strategies", "consequences",
			)
		} | {"central_phenomenon": None}
	envelope_errors = sorted(
		Draft202012Validator(schema).iter_errors(section),
		key=lambda error: list(error.path),
	)
	if envelope_errors:
		return evidence_issues + issues

	if not isinstance(raw_categories, list):
		issues.append({
			"stage": "stage2",
			"record_type": "category",
			"record_id": "نامشخص",
			"reason": "فهرست مقوله‌ها باید آرایه باشد؛ این فهرست خالی جایگزین شد.",
		})
		raw_categories = []
	categories = []
	for index, category in enumerate(raw_categories):
		record_id = str(category.get("category_id", f"ردیف {index + 1}")) if isinstance(category, dict) else f"ردیف {index + 1}"
		if not isinstance(category, dict):
			issues.append({
				"stage": "stage2",
				"record_type": "category",
				"record_id": record_id,
				"reason": "مقوله باید یک نگاشت باشد؛ این رکورد کنار گذاشته شد.",
			})
			continue
		for key, record_type, id_field, definition_key in (
			("subcategories", "subcategory", "subcategory_id", "subcategory"),
			("properties", "property", "property_id", "property"),
			("dimensions", "dimension", "property_id", "dimension"),
		):
			filtered, nested_issues = _filter_invalid_records(
				category.get(key),
				defs[definition_key],
				"stage2",
				record_type,
				id_field,
				defs,
			)
			category[key] = filtered
			for issue in nested_issues:
				issue["parent_category_id"] = record_id
			issues.extend(nested_issues)
		filtered, category_issues = _filter_invalid_records(
			[category], defs["category"], "stage2", "category", "category_id", defs
		)
		if filtered:
			categories.extend(filtered)
		else:
			issues.extend(category_issues)
	section["categories"] = categories

	for key, record_type, id_field in (
		("unclassified_concepts", "unclassified_concept", "concept_id"),
		("relationships", "relationship", "relationship_id"),
		("researcher_review", "researcher_review", "item_id"),
	):
		raw_items = {
			"unclassified_concepts": raw_unclassified,
			"relationships": raw_relationships,
			"researcher_review": raw_researcher_review,
		}[key]
		filtered, record_issues = _filter_invalid_records(
			raw_items,
			schema["properties"][key]["items"],
			"stage2",
			record_type,
			id_field,
			defs,
		)
		section[key] = filtered
		issues.extend(record_issues)

	model = section["paradigm_model"]
	if raw_components:
		for key in (
			"causal_conditions", "contextual_conditions", "intervening_conditions",
			"action_interaction_strategies", "consequences",
		):
			filtered, component_issues = _filter_invalid_records(
				raw_components[key],
				schema["$defs"]["paradigm_component"]["items"],
				"stage2",
				key,
				"category_id",
				defs,
			)
			model[key] = filtered
			issues.extend(component_issues)

	if isinstance(raw_phenomenon, dict):
		phenomenon_errors = list(
			Draft202012Validator({**defs["phenomenon"], "$defs": defs}).iter_errors(raw_phenomenon)
		)
		if phenomenon_errors:
			issue = phenomenon_errors[0]
			issues.append({
				"stage": "stage2",
				"record_type": "central_phenomenon",
				"record_id": "central_phenomenon",
				"reason": f"رکورد پدیده محوری نامعتبر بود و null شد: {issue.message}",
			})
		else:
			section["central_phenomenon"] = raw_phenomenon
			model["central_phenomenon"] = raw_phenomenon
	elif raw_phenomenon is not None:
		issues.append({
			"stage": "stage2",
			"record_type": "central_phenomenon",
			"record_id": "central_phenomenon",
			"reason": "پدیده محوری باید یک شیء یا null باشد؛ این رکورد null شد.",
		})
	return evidence_issues + issues


def combine_stage_outputs(
	project: dict[str, Any], stage1: dict[str, Any], stage2: dict[str, Any]
) -> dict[str, Any]:
	"""Combine stage outputs and produce all audit/status fields in Python."""
	concepts_by_id = {item["concept_id"]: item for item in project.get("concepts", [])}
	paragraph_text = {
		item["paragraph_id"]: item["text"] for item in project.get("paragraphs", [])
	}
	concept_reviews = []
	for item in stage1.get("concept_reviews", []):
		concept = concepts_by_id.get(item.get("concept_id"), {})
		concept_reviews.append({
			**item,
			"original_concept": concept.get("original_concept", ""),
			"original_code": concept.get("original_code", ""),
		})

	interview = stage2["interview_analysis"]
	interview = dict(interview)
	model = interview.get("paradigm_model")
	if isinstance(model, dict):
		model["central_phenomenon"] = interview.get("central_phenomenon")
	audit_issues: list[str] = []
	concept_ids = set(concepts_by_id)
	scope = stage1.get("source_scope", {})
	expected_counts = {
		"total_paragraphs": len(paragraph_text),
		"total_concepts_received": len(concept_ids),
		"total_concepts_reviewed": len(stage1.get("concept_reviews", [])),
	}
	if any(scope.get(key) != value for key, value in expected_counts.items()):
		audit_issues.append("شمارنده‌های source_scope مرحله اول با ورودی و رکوردهای خروجی تطبیق ندارند.")
	review_ids = [item.get("concept_id") for item in concept_reviews]
	duplicate_reviews = len(review_ids) != len(set(review_ids))
	if duplicate_reviews:
		audit_issues.append("مرحله اول برای بعضی شناسه‌های مفهوم رکورد تکراری دارد.")
	missing_reviews = concept_ids - set(review_ids)
	extra_reviews = set(review_ids) - concept_ids
	if missing_reviews:
		audit_issues.append("بازبینی مرحله اول برای این مفاهیم جا افتاده است: " + ", ".join(sorted(missing_reviews)))
	if extra_reviews:
		audit_issues.append("مرحله اول شناسه مفهوم ناموجود دارد: " + ", ".join(sorted(extra_reviews)))

	assignments: list[str] = []
	for category in interview.get("categories", []):
		assignments.extend(category.get("concept_ids", []))
	for item in interview.get("unclassified_concepts", []):
		assignments.append(item.get("concept_id"))
	assigned_set = set(assignments)
	missing_classifications = concept_ids - assigned_set
	extra_classifications = assigned_set - concept_ids
	duplicate_classifications = {
		concept_id for concept_id in assignments if assignments.count(concept_id) > 1
	}
	if missing_classifications:
		audit_issues.append("مفاهیم بدون مقوله یا ثبت در unclassified_concepts: " + ", ".join(sorted(missing_classifications)))
	if extra_classifications:
		audit_issues.append("تحلیل مرحله دوم به شناسه مفهوم ناموجود ارجاع می‌دهد: " + ", ".join(sorted(extra_classifications)))
	if duplicate_classifications:
		audit_issues.append("این مفاهیم بیش از یک بار طبقه‌بندی شده‌اند: " + ", ".join(sorted(duplicate_classifications)))

	category_ids = {item.get("category_id") for item in interview.get("categories", [])}
	reference_issues = _find_reference_issues(interview, concept_ids, set(paragraph_text), category_ids)
	reference_issues.extend(
		_find_reference_issues(stage1.get("concept_reviews", []), concept_ids, set(paragraph_text), category_ids)
	)
	if len(category_ids) != len(interview.get("categories", [])):
		reference_issues.append("شناسه مقوله در مرحله دوم تکراری یا خالی است.")
	relationship_ids = [item.get("relationship_id") for item in interview.get("relationships", [])]
	if len(relationship_ids) != len(set(relationship_ids)):
		reference_issues.append("شناسه رابطه در مرحله دوم تکراری است.")
	subcategory_ids = [
		subcategory.get("subcategory_id")
		for category in interview.get("categories", [])
		for subcategory in category.get("subcategories", [])
	]
	if len(subcategory_ids) != len(set(subcategory_ids)):
		reference_issues.append("شناسه زیرمقوله در مرحله دوم تکراری است.")
	for category in interview.get("categories", []):
		parent_ids = set(category.get("concept_ids", []))
		for subcategory in category.get("subcategories", []):
			if not set(subcategory.get("concept_ids", [])).issubset(parent_ids):
				reference_issues.append(
					f"مفاهیم زیرمقوله {subcategory.get('subcategory_id', '')} زیرمجموعه مقوله والد نیستند."
				)
	audit_issues.extend(reference_issues)
	structural_issues = _find_structural_issues(interview)
	audit_issues.extend(structural_issues)
	evidence_warnings = collect_evidence_warnings(stage1, interview, project)
	for warning in evidence_warnings:
		audit_issues.append(
			f"شاهد {warning['record_type']} {warning['record_id']} نیازمند بازبینی است: "
			f"{warning['status']} (پاراگراف {warning['paragraph_id']})."
		)
		_add_evidence_researcher_review(interview, warning)
	for review in concept_reviews:
		if any(
			warning["stage"] == "stage1" and warning["record_id"] == review["concept_id"]
			for warning in evidence_warnings
		):
			review["needs_researcher_review"] = True
	record_errors = list(stage1.get("_record_errors", [])) + list(stage2.get("_record_errors", []))
	for error in record_errors:
		audit_issues.append(
			f"رکورد {error.get('record_type')} {error.get('record_id')} از مرحله "
			f"{error.get('stage')} پردازش نشد: {error.get('reason')}"
		)
		_add_record_researcher_review(interview, error)
	mismatches = _find_code_model_mismatches(concept_reviews, interview)
	if mismatches:
		audit_issues.extend(
			f"کد پیشنهادی مفهوم {item['concept_id']} با مؤلفه مدل پارادایمی آن هم‌خوان نیست."
			for item in mismatches
		)
	unsupported_relationship = any(
		not item.get("evidence") or not item.get("supporting_concept_ids")
		for item in interview.get("relationships", [])
	)
	count_mismatch = any(scope.get(key) != value for key, value in expected_counts.items())
	missing_data = bool(
		missing_reviews or extra_reviews or missing_classifications or extra_classifications
		or duplicate_classifications or reference_issues or structural_issues
		or evidence_warnings or record_errors or count_mismatch
	)
	all_reviews_complete = (
		len(review_ids) == len(concept_ids)
		and set(review_ids) == concept_ids
		and len(review_ids) == len(set(review_ids))
	)
	all_ids_preserved = (
		not missing_reviews and not extra_reviews and not duplicate_reviews
		and not extra_classifications and not duplicate_classifications
		and not reference_issues and not missing_classifications
	)
	quality_audit = {
		"all_concepts_reviewed": all_reviews_complete,
		"all_ids_preserved": all_ids_preserved,
		"all_evidence_verifiable": not evidence_warnings,
		"unsupported_relationships_detected": unsupported_relationship,
		"contradictions_detected": bool(mismatches),
		"missing_data_detected": missing_data,
		"analysis_limitations": [],
		"final_status": "incomplete",
		"validation_issues": audit_issues,
		"evidence_warnings": evidence_warnings,
		"record_errors": record_errors,
		"code_model_mismatches": mismatches,
		"missing_concept_review_ids": sorted(missing_reviews),
		"missing_category_assignment_ids": sorted(missing_classifications),
	}
	is_complete = (
		all_reviews_complete and all_ids_preserved and not unsupported_relationship
		and not mismatches and not missing_data and not audit_issues
	)
	quality_audit["final_status"] = "complete" if is_complete else "incomplete"
	return {
		"schema_version": "2.0",
		"software": "HOOSHYAR",
		"analysis_method": "Strauss_Corbin_Grounded_Theory",
		"project_id": project["project_id"],
		"interview_id": project.get("interview_id") or project["project_id"],
		"analysis_status": quality_audit["final_status"],
		"source_scope": {
			"total_paragraphs": len(paragraph_text),
			"total_concepts_received": len(concept_ids),
			"total_concepts_reviewed": len(review_ids),
		},
		"concept_reviews": concept_reviews,
		"interview_analysis": {**interview, "quality_audit": quality_audit},
	}


def load_analysis_markdown(raw_markdown: str) -> dict[str, Any]:
	if not raw_markdown or not raw_markdown.strip():
		raise ValueError("متن ورودی خالی است.")
	raw_markdown = _normalize_pasted_yaml(raw_markdown)
	blocks = re.findall(
		r"```(?:yaml|yml)?[ \t]*\r?\n(.*?)```",
		raw_markdown,
		flags=re.IGNORECASE | re.DOTALL,
	)
	if len(blocks) > 1:
		raise ValueError("پاسخ باید فقط یک بلوک کد YAML داشته باشد.")
	document = blocks[0] if blocks else raw_markdown.strip()
	try:
		payload = yaml.safe_load(document)
	except yaml.YAMLError as error:
		mark = getattr(error, "problem_mark", None)
		problem = getattr(error, "problem", None)
		if mark is not None:
			detail = f"خط {mark.line + 1}، ستون {mark.column + 1}: {problem or error}"
		else:
			detail = str(error)
		raise ValueError(f"ساختار YAML داخل Markdown معتبر نیست ({detail}).") from error
	if not isinstance(payload, dict):
		raise ValueError("ریشه‌ی بلوک YAML باید یک نگاشت باشد.")
	return payload


def _normalize_pasted_yaml(raw_markdown: str) -> str:
	"""Recover YAML copied from renderers that escape spaces and line endings."""
	if "&#" not in raw_markdown and not re.search(r"\\\r?\n", raw_markdown):
		return raw_markdown
	normalized = html.unescape(raw_markdown)
	normalized = re.sub(r"\\\r?\n", "\n", normalized)
	normalized = normalized.replace("\\-", "-")
	return normalized


def _normalize_stage1_values(payload: dict[str, Any]) -> None:
	for review in payload.get("concept_reviews", []):
		if isinstance(review, dict) and review.get("code_evaluation") == "revised":
			review["code_evaluation"] = "revise"


def _add_evidence_researcher_review(
	interview: dict[str, Any], warning: dict[str, Any]
) -> None:
	record_type = warning["record_type"]
	item_type = record_type if record_type in {"concept", "category", "relationship"} else "paradigm"
	item_id = warning["record_id"]
	if warning["stage"] == "stage2":
		_mark_evidence_owner_for_review(interview, record_type, item_id)
	message = (
		f"شاهد در {warning['paragraph_id']} وضعیت {warning['status']} دارد"
		+ (
			f" (شباهت {warning['similarity']}٪)."
			if warning["similarity"] is not None
			else "."
		)
	)
	reviews = interview.setdefault("researcher_review", [])
	if not any(
		item.get("item_type") == item_type
		and item.get("item_id") == item_id
		and item.get("issue") == "شاهد نیازمند بازبینی"
		for item in reviews
	):
		reviews.append({
			"item_type": item_type,
			"item_id": item_id,
			"issue": "شاهد نیازمند بازبینی",
			"reason": message,
			"suggested_action": "نقل‌قول را با متن پاراگراف اصلی بررسی کنید؛ Python آن را اصلاح نکرده است.",
		})


def _add_record_researcher_review(
	interview: dict[str, Any], error: dict[str, str]
) -> None:
	record_type = error.get("record_type", "")
	if record_type in {"concept", "unclassified_concept"}:
		item_type = "concept"
	elif record_type == "relationship":
		item_type = "relationship"
	elif record_type == "category" or error.get("parent_category_id"):
		item_type = "category"
	elif record_type == "central_phenomenon":
		item_type = "central_phenomenon"
	else:
		item_type = "paradigm"
	item_id = error.get("parent_category_id") or error.get("record_id", "نامشخص")
	for category in interview.get("categories", []):
		if category.get("category_id") == item_id:
			category["needs_researcher_review"] = True
	reviews = interview.setdefault("researcher_review", [])
	if not any(
		item.get("item_type") == item_type
		and item.get("item_id") == item_id
		and item.get("issue") == "رکورد نیازمند بازبینی ساختاری"
		for item in reviews
	):
		reviews.append({
			"item_type": item_type,
			"item_id": item_id,
			"issue": "رکورد نیازمند بازبینی ساختاری",
			"reason": error.get("reason", "ساختار رکورد نامعتبر بود."),
			"suggested_action": "رکورد حذف‌شده را با خروجی اصلی بررسی و در صورت نیاز اصلاح کنید.",
		})


def _mark_evidence_owner_for_review(
	interview: dict[str, Any], record_type: str, record_id: str
) -> None:
	for category in interview.get("categories", []):
		matches = record_type == "category" and category.get("category_id") == record_id
		if record_type == "subcategory":
			matches = any(
				item.get("subcategory_id") == record_id for item in category.get("subcategories", [])
			)
		elif record_type == "property":
			matches = any(item.get("property_id") == record_id for item in category.get("properties", []))
			matches = matches or any(item.get("property_id") == record_id for item in category.get("dimensions", []))
		if matches:
			category["needs_researcher_review"] = True
	for relationship in interview.get("relationships", []):
		if record_type == "relationship" and relationship.get("relationship_id") == record_id:
			relationship["needs_researcher_review"] = True


def _validate_paradigm_shape(interview: dict[str, Any]) -> None:
	if interview.get("paradigm_model", {}).get("central_phenomenon") != interview.get(
		"central_phenomenon"
	):
		raise ValueError(
			"central_phenomenon در paradigm_model باید عیناً همان مقدار بخش مرکزی تحلیل باشد."
		)


def _find_structural_issues(interview: dict[str, Any]) -> list[str]:
	issues: list[str] = []
	if interview.get("central_phenomenon") != interview.get("paradigm_model", {}).get(
		"central_phenomenon"
	):
		issues.append("central_phenomenon در دو بخش تحلیل و مدل پارادایمی یکسان نیست.")
	categories = {
		item.get("category_id"): item for item in interview.get("categories", [])
	}
	for category in interview.get("categories", []):
		category_id = category.get("category_id", "")
		concept_ids = set(category.get("concept_ids", []))
		properties = {
			item.get("property_id"): item
			for item in category.get("properties", [])
			if item.get("property_id")
		}
		if len(properties) != len(category.get("properties", [])):
			issues.append(f"مقوله {category_id} شناسه ویژگی تکراری یا خالی دارد.")
		for prop in category.get("properties", []):
			property_id = prop.get("property_id", "")
			if not prop.get("concept_ids"):
				issues.append(f"ویژگی {property_id} مفهوم پشتیبان مشخص نکرده است.")
			if not set(prop.get("concept_ids", [])).issubset(concept_ids):
				issues.append(f"مفاهیم ویژگی {property_id} خارج از مقوله {category_id} هستند.")
			if not prop.get("evidence"):
				issues.append(f"ویژگی {property_id} در مقوله {category_id} شاهد ندارد.")
		for dimension in category.get("dimensions", []):
			property_id = dimension.get("property_id")
			prop = properties.get(property_id)
			if prop is None:
				issues.append(
					f"بُعد مقوله {category_id} به شناسه ویژگی موجودی متصل نیست: {property_id or 'نامشخص'}."
				)
			elif dimension.get("property_name") != prop.get("property_name"):
				issues.append(f"نام ویژگی بُعد {property_id} با ویژگی مرجع مطابقت ندارد.")
			if not set(dimension.get("concept_ids", [])).issubset(concept_ids):
				issues.append(f"مفاهیم بُعد {property_id or ''} خارج از مقوله {category_id} هستند.")
			if not dimension.get("concept_ids"):
				issues.append(f"بُعد ویژگی {property_id or 'نامشخص'} مفهوم پشتیبان مشخص نکرده است.")
			if prop is not None and not set(dimension.get("concept_ids", [])).issubset(
				set(prop.get("concept_ids", []))
			):
				issues.append(f"مفاهیم بُعد از مفاهیم پشتیبان ویژگی {property_id} نیستند.")
			if not dimension.get("evidence"):
				issues.append(f"بُعد ویژگی {property_id or 'نامشخص'} شاهد ندارد.")

	model = interview.get("paradigm_model", {})
	component_keys = (
		"causal_conditions",
		"contextual_conditions",
		"intervening_conditions",
		"action_interaction_strategies",
		"consequences",
	)
	placements: dict[str, set[str]] = {}
	for component in component_keys:
		for item in model.get(component, []):
			category_id = item.get("category_id")
			category = categories.get(category_id)
			if category is not None and not set(item.get("concept_ids", [])).issubset(
				set(category.get("concept_ids", []))
			):
				issues.append(
					f"مفاهیم مؤلفه {component} زیرمجموعه مقوله {category_id} نیستند."
				)
			if not item.get("evidence"):
				issues.append(f"عضو مؤلفه {component} برای {category_id} شاهد ندارد.")
			if not item.get("concept_ids"):
				issues.append(f"عضو مؤلفه {component} برای {category_id} مفهوم پشتیبان ندارد.")
			for concept_id in item.get("concept_ids", []):
				placements.setdefault(concept_id, set()).add(component)
	duplicates = sorted(concept_id for concept_id, components in placements.items() if len(components) > 1)
	if duplicates:
		issues.append(
			"این مفاهیم در بیش از یک مؤلفه مدل پارادایمی آمده‌اند: " + ", ".join(duplicates)
		)
	for category in interview.get("categories", []):
		subcategory_placements: dict[str, str] = {}
		for subcategory in category.get("subcategories", []):
			for concept_id in subcategory.get("concept_ids", []):
				if concept_id in subcategory_placements:
					issues.append(
						f"مفهوم {concept_id} در چند زیرمقوله از مقوله {category.get('category_id', '')} آمده است."
					)
				subcategory_placements[concept_id] = subcategory.get("subcategory_id", "")
	for category_id, category in categories.items():
		if category_id and not category.get("evidence"):
			issues.append(f"مقوله {category_id} شاهد ندارد.")
	center = interview.get("central_phenomenon")
	if isinstance(center, dict) and not center.get("evidence"):
		issues.append("پدیده محوریِ شناسایی‌شده شاهد ندارد.")
	return list(dict.fromkeys(issues))


def _find_reference_issues(
	value: Any, concept_ids: set[str], paragraph_ids: set[str], category_ids: set[str]
) -> list[str]:
	concept_keys = {"concept_id", "concept_ids", "supporting_concept_ids", "related_concept_ids"}
	paragraph_keys = {"paragraph_id", "paragraph_ids"}
	category_keys = {"category_id", "source_category_id", "target_category_id", "supporting_category_ids"}
	issues: list[str] = []

	def visit(item: Any, key: str = "") -> None:
		if isinstance(item, dict):
			for child_key, child in item.items():
				visit(child, child_key)
		elif isinstance(item, list):
			for child in item:
				visit(child, key)
		elif isinstance(item, str):
			known = concept_ids if key in concept_keys else paragraph_ids if key in paragraph_keys else category_ids if key in category_keys else None
			if known is not None and item not in known:
				issues.append(f"شناسه ارجاع‌شده در مرحله دوم وجود ندارد: {item}")

	visit(value)
	return list(dict.fromkeys(issues))


def _find_code_model_mismatches(
	reviews: list[dict[str, Any]], interview: dict[str, Any]
) -> list[dict[str, str]]:
	code_to_component = {
		"شرایط علّی": "causal_conditions",
		"شرایط زمینه‌ای": "contextual_conditions",
		"شرایط مداخله‌گر": "intervening_conditions",
		"راهبردهای کنش و تعامل": "action_interaction_strategies",
		"پیامدها": "consequences",
		"پدیده محوری": "central_phenomenon",
	}
	placements: dict[str, set[str]] = {}
	model = interview.get("paradigm_model", {})
	for component, items in model.items():
		if component == "central_phenomenon":
			items = [items] if isinstance(items, dict) else []
		if isinstance(items, list):
			for item in items:
				if isinstance(item, dict):
					for concept_id in item.get("concept_ids", []):
						placements.setdefault(concept_id, set()).add(component)
					for concept_id in item.get("supporting_concept_ids", []):
						placements.setdefault(concept_id, set()).add(component)
	return [
		{
			"concept_id": review["concept_id"],
			"suggested_code": review.get("suggested_code", ""),
			"model_components": sorted(placements.get(review["concept_id"], set())),
			"expected_component": code_to_component[review["suggested_code"]],
		}
		for review in reviews
		if review.get("suggested_code") in code_to_component
		and review["concept_id"] in placements
		and code_to_component[review["suggested_code"]] not in placements[review["concept_id"]]
	]
