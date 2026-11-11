"""Analysis result, category, paradigm, and relationship views."""

import json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from graphviz.backend import ExecutableNotFound
from fastapi.templating import Jinja2Templates
from rapidfuzz import fuzz

from app.core.database import get_project, update_project
from app.repositories.synthesis_repository import mark_synthesis_runs_stale_for_source
from app.services.analysis_service import parse_stage_markdown, prepare_analysis_results
from app.services.paradigm_service import prepare_paradigm_diagram, render_paradigm_diagram

router = APIRouter(prefix="/results", tags=["results"])
templates = Jinja2Templates(directory="app/templates")


def _project_or_404(project_id: str):
	project = get_project(project_id)
	if project is None:
		raise HTTPException(status_code=404, detail="پروژه پیدا نشد.")
	if not project.get("analysis"):
		raise HTTPException(status_code=409, detail="هنوز تحلیل دو مرحله‌ای برای این پروژه ثبت نشده است.")
	return project


@router.get("/{project_id}")
def results_page(request: Request, project_id: str):
	project = _project_or_404(project_id)
	if project["analysis"].get("schema_version") == "2.0":
		return RedirectResponse(f"/results/{project_id}/concept-review", status_code=303)
	return templates.TemplateResponse(
		request=request,
		name="reports.html",
		context={"project": project, "results": prepare_analysis_results(project["analysis"])},
	)


def _v2_project_or_409(project_id: str):
	project = _project_or_404(project_id)
	if project["analysis"].get("schema_version") != "2.0":
		return RedirectResponse(f"/results/{project_id}", status_code=303)
	return project


def _recover_structural_record_errors(project: dict) -> list[dict]:
	recovered = []
	for stage in ("stage1", "stage2"):
		stage_output = (project.get("stage_outputs") or {}).get(stage) or {}
		raw_markdown = stage_output.get("raw_markdown")
		if not raw_markdown:
			continue
		try:
			payload = parse_stage_markdown(raw_markdown, project, stage)
		except (ValueError, TypeError):
			continue
		recovered.extend(
			item for item in payload.get("_record_errors", [])
			if item.get("resolution_id")
		)
	return recovered


def _ensure_repaired_record(error: dict):
	if isinstance(error.get("repaired_record"), dict):
		return
	raw_record = error.get("raw_record")
	auto_repair = error.get("auto_repair")
	if isinstance(raw_record, dict) and isinstance(auto_repair, dict):
		repaired = dict(raw_record)
		repaired.update(auto_repair)
		error["repaired_record"] = repaired


def _apply_repaired_record(analysis: dict, error: dict):
	repaired = error.get("repaired_record")
	if not isinstance(repaired, dict):
		return
	interview = analysis.get("interview_analysis", {})
	record_type = error.get("record_type")
	if error.get("stage") == "stage1":
		collection, id_field = analysis.setdefault("concept_reviews", []), "concept_id"
	elif record_type == "relationship":
		collection, id_field = interview.setdefault("relationships", []), "relationship_id"
	elif record_type == "researcher_review":
		collection, id_field = interview.setdefault("researcher_review", []), "item_id"
	elif record_type == "category":
		collection, id_field = interview.setdefault("categories", []), "category_id"
	elif record_type == "unclassified_concept":
		collection, id_field = interview.setdefault("unclassified_concepts", []), "concept_id"
	elif record_type in {
		"causal_conditions", "contextual_conditions", "intervening_conditions",
		"action_interaction_strategies", "consequences",
	}:
		collection = interview.setdefault("paradigm_model", {}).setdefault(record_type, [])
		id_field = "category_id"
	elif record_type in {"subcategory", "property", "dimension"}:
		parent = next((
			item for item in interview.get("categories", [])
			if item.get("category_id") == error.get("parent_category_id")
		), None)
		if parent is None:
			return
		collection_key = {"subcategory": "subcategories", "property": "properties", "dimension": "dimensions"}[record_type]
		collection = parent.setdefault(collection_key, [])
		id_field = "subcategory_id" if record_type == "subcategory" else "property_id"
	else:
		return
	item_id = repaired.get(id_field)
	if not any(item.get(id_field) == item_id for item in collection if isinstance(item, dict)):
		collection.append(repaired)


def _human_record_error(item: dict) -> dict:
	record_type = item.get("record_type", "")
	record_id = item.get("record_id", "")
	record_labels = {
		"relationship": "رابطه",
		"researcher_review": "مورد بازبینی پژوهشگر",
		"category": "مقوله",
		"subcategory": "زیرمقوله",
		"property": "ویژگی",
		"dimension": "بُعد",
	}
	message = item.get("reason", "")
	if "source_category_id" in message:
		friendly = "رابطه، مقولهٔ مبدأ ندارد و برای جلوگیری از ساخت رابطهٔ نادرست باید بازبینی شود."
	elif "target_category_id" in message:
		friendly = "رابطه، مقولهٔ مقصد ندارد و برای جلوگیری از ساخت رابطهٔ نادرست باید بازبینی شود."
	elif "item_type" in message:
		friendly = "نوع این مورد در خروجی مشخص نشده است؛ پیشنهاد «پارادایم» آمادهٔ تأیید است."
	elif "confidence" in message:
		friendly = "مقدار اطمینان در رکورد ثبت نشده؛ مقدار پیشنهادی ۰٫۹ است."
	else:
		friendly = "اطلاعات این مورد کامل نیست و برای تأیید یا اصلاح به تصمیم پژوهشگر نیاز دارد."
	return {
		**item,
		"display_title": f"{record_labels.get(record_type, 'مورد تحلیلی')} {record_id}".strip(),
		"display_message": friendly,
	}


def _human_validation_issue(issue: str) -> str:
	if "source_category_id" in issue:
		return "یک رابطه به مقولهٔ مبدأ متصل نشده و نیازمند بررسی است."
	if "target_category_id" in issue:
		return "یک رابطه به مقولهٔ مقصد متصل نشده و نیازمند بررسی است."
	if "item_type" in issue:
		return "نوع یکی از موارد بازبینی مشخص نشده است؛ پیشنهاد پیش‌فرض برای تأیید آماده است."
	if "confidence" in issue:
		return "مقدار اطمینان یکی از رکوردها ثبت نشده است؛ پیشنهاد اصلاح در بخش موارد ساختاری آمده است."
	if "required property" in issue or "ValidationError" in issue:
		return "ساختار یکی از موارد کامل نیست و برای تأیید یا اصلاح به تصمیم پژوهشگر نیاز دارد."
	return issue


def _find_evidence_target(
	value, target_type: str, target_id: str, paragraph_id: str, quote: str
):
	if isinstance(value, dict):
		item_type, item_id = "", ""
		for key, kind in (
			("concept_id", "concept"), ("relationship_id", "relationship"),
			("subcategory_id", "subcategory"), ("property_id", "property"),
			("category_id", "category"),
		):
			if value.get(key):
				item_type, item_id = kind, str(value[key])
				break
		evidence = value.get("evidence")
		if item_type == target_type and item_id == target_id and isinstance(evidence, list):
			for item in evidence:
				if (
					isinstance(item, dict)
					and str(item.get("paragraph_id") or "") == paragraph_id
					and str(item.get("exact_quote") or "") == quote
				):
					return item
		for key, child in value.items():
			if key != "evidence":
				found = _find_evidence_target(child, target_type, target_id, paragraph_id, quote)
				if found is not None:
					return found
	elif isinstance(value, list):
		for child in value:
			found = _find_evidence_target(child, target_type, target_id, paragraph_id, quote)
			if found is not None:
				return found
	return None


def _source_sentence_for_span(source_text: str, start: int, end: int) -> str:
	boundaries = ".!?؟؛\n"
	left = max((source_text.rfind(mark, 0, start) for mark in boundaries), default=-1) + 1
	right_candidates = [position for mark in boundaries if (position := source_text.find(mark, end)) >= 0]
	right = min(right_candidates) + 1 if right_candidates else len(source_text)
	return source_text[left:right].strip()


def _refresh_evidence_audit(analysis: dict, resolved_warning: dict):
	interview = analysis["interview_analysis"]
	quality = interview["quality_audit"]
	warnings = quality.get("evidence_warnings") or []
	record_type = resolved_warning.get("record_type")
	record_id = resolved_warning.get("record_id")
	if not any(item.get("record_type") == record_type and item.get("record_id") == record_id for item in warnings):
		item_type = record_type if record_type in {"concept", "category", "relationship"} else "paradigm"
		interview["researcher_review"] = [
			item for item in interview.get("researcher_review", [])
			if not (
				item.get("item_type") == item_type
				and item.get("item_id") == record_id
				and item.get("issue") == "شاهد نیازمند بازبینی"
			)
		]
	quality["all_evidence_verifiable"] = not warnings
	issues = quality.get("validation_issues") or []
	quality["validation_issues"] = [
		issue for issue in issues
		if f"شاهد {record_type} {record_id} نیازمند بازبینی است:" not in issue
		or any(item.get("record_type") == record_type and item.get("record_id") == record_id for item in warnings)
	]
	quality["missing_data_detected"] = bool(
		quality.get("record_errors")
		or warnings
		or quality["validation_issues"]
		or quality.get("missing_concept_review_ids")
		or quality.get("missing_category_assignment_ids")
	)
	complete = (
		quality.get("all_concepts_reviewed") is True
		and quality.get("all_ids_preserved") is True
		and quality["all_evidence_verifiable"]
		and not quality.get("unsupported_relationships_detected")
		and not quality.get("contradictions_detected")
		and not quality["missing_data_detected"]
	)
	quality["final_status"] = "complete" if complete else "incomplete"
	analysis["analysis_status"] = quality["final_status"]


def _refresh_quality_status(analysis: dict):
	quality = analysis["interview_analysis"]["quality_audit"]
	quality["missing_data_detected"] = bool(
		quality.get("record_errors")
		or quality.get("evidence_warnings")
		or quality.get("validation_issues")
		or quality.get("missing_concept_review_ids")
		or quality.get("missing_category_assignment_ids")
	)
	complete = (
		quality.get("all_concepts_reviewed") is True
		and quality.get("all_ids_preserved") is True
		and quality.get("all_evidence_verifiable") is True
		and not quality.get("unsupported_relationships_detected")
		and not quality.get("contradictions_detected")
		and not quality["missing_data_detected"]
	)
	quality["final_status"] = "complete" if complete else "incomplete"
	analysis["analysis_status"] = quality["final_status"]


@router.post("/{project_id}/evidence-resolution")
def resolve_evidence_warning(
	project_id: str,
	warning_index: int = Form(...),
	action: str = Form(...),
	replacement_quote: str = Form(""),
):
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	analysis = project["analysis"]
	interview = analysis["interview_analysis"]
	quality = interview["quality_audit"]
	warnings = quality.get("evidence_warnings") or []
	if warning_index < 0 or warning_index >= len(warnings):
		raise HTTPException(status_code=404, detail="هشدار شاهد پیدا نشد یا قبلاً حل شده است.")
	warning = warnings[warning_index]
	paragraph = next((
		item for item in project.get("paragraphs", [])
		if item.get("paragraph_id") == warning.get("paragraph_id")
	), None)
	if paragraph is None:
		raise HTTPException(status_code=409, detail="پاراگراف مرجع در مصاحبه پیدا نشد؛ این مورد خودکار قابل تأیید نیست.")
	source_text = paragraph.get("text") or ""
	if action == "approve_auto":
		if warning.get("status") != "fuzzy_match" or (warning.get("similarity") or 0) < 90:
			raise HTTPException(status_code=409, detail="تأیید خودکار فقط برای تطبیق فازی با شباهت حداقل ۹۰٪ مجاز است.")
		alignment = fuzz.partial_ratio_alignment(warning.get("exact_quote", ""), source_text)
		if alignment is None or alignment.score < 90 or alignment.dest_end <= alignment.dest_start:
			raise HTTPException(status_code=409, detail="بازهٔ دقیق نقل‌قول در متن اصلی پیدا نشد؛ متن را دستی وارد کنید.")
		original_quote = warning.get("exact_quote", "").strip()
		if original_quote and original_quote in source_text:
			resolved_quote = original_quote
		else:
			resolved_quote = _source_sentence_for_span(
				source_text, alignment.dest_start, alignment.dest_end
			)
	elif action == "save_manual":
		resolved_quote = replacement_quote.strip()
		if not resolved_quote or resolved_quote not in source_text:
			raise HTTPException(status_code=422, detail="متن اصلاح‌شده باید عیناً و پیوسته در پاراگراف اصلی وجود داشته باشد.")
	else:
		raise HTTPException(status_code=422, detail="نوع اصلاح شاهد نامعتبر است.")
	root = analysis["concept_reviews"] if warning.get("stage") == "stage1" else interview
	target = _find_evidence_target(
		root,
		warning.get("record_type", ""),
		warning.get("record_id", ""),
		warning.get("paragraph_id", ""),
		warning.get("exact_quote", ""),
	)
	if target is None:
		raise HTTPException(status_code=409, detail="رکورد شاهد با نسخهٔ فعلی تحلیل تطبیق ندارد؛ خروجی مرحله را دوباره بررسی کنید.")
	original_quote = target["exact_quote"]
	target["exact_quote"] = resolved_quote
	quality["evidence_warnings"] = [item for index, item in enumerate(warnings) if index != warning_index]
	_refresh_evidence_audit(analysis, warning)
	updated_edits = dict(project.get("researcher_edits") or {})
	resolutions = list(updated_edits.get("evidence_resolutions") or [])
	resolutions.append({
		"stage": warning.get("stage"),
		"record_type": warning.get("record_type"),
		"record_id": warning.get("record_id"),
		"paragraph_id": warning.get("paragraph_id"),
		"original_quote": original_quote,
		"resolved_quote": resolved_quote,
		"action": action,
		"confirmed": True,
	})
	updated_edits["evidence_resolutions"] = resolutions
	update_project(project_id, analysis=analysis, researcher_edits=updated_edits)
	mark_synthesis_runs_stale_for_source(project_id)
	return RedirectResponse(f"/results/{project_id}/interview-analysis?show_quality=1#quality-audit", status_code=303)


@router.post("/{project_id}/record-resolution")
def confirm_structural_repair(
	project_id: str,
	resolution_id: str = Form(...),
	repair_json: str = Form(""),
	repair_confidence: float | None = Form(None),
	repair_item_type: str = Form(""),
):
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	analysis = project["analysis"]
	interview = analysis["interview_analysis"]
	quality = interview["quality_audit"]
	errors = quality.get("record_errors") or []
	error = next((item for item in errors if item.get("resolution_id") == resolution_id), None)
	if error is None:
		recovered = next((
			item for item in _recover_structural_record_errors(project)
			if item.get("resolution_id") == resolution_id
		), None)
		if recovered:
			error = next((
				item for item in errors
				if item.get("stage") == recovered.get("stage")
				and item.get("record_type") == recovered.get("record_type")
				and item.get("record_id") == recovered.get("record_id")
			), None)
			if error is not None:
				error.update(recovered)
	if error is None:
		raise HTTPException(status_code=404, detail="مورد بررسی پیدا نشد یا قبلاً تأیید شده است.")
	decision = "skipped"
	if error.get("auto_repair"):
		_ensure_repaired_record(error)
		decision = "repaired"
	elif repair_json.strip():
		raise HTTPException(status_code=422, detail="برای این مورد پیشنهاد اصلاح امنی وجود ندارد.")
	if repair_confidence is not None or repair_item_type.strip():
		overrides = {}
		if repair_confidence is not None:
			overrides["confidence"] = repair_confidence
		if repair_item_type.strip():
			overrides["item_type"] = repair_item_type.strip()
	elif repair_json.strip():
		try:
			overrides = json.loads(repair_json)
		except json.JSONDecodeError as parse_error:
			raise HTTPException(status_code=422, detail="مقادیر اصلاح‌شده باید JSON معتبر باشند.") from parse_error
		if not isinstance(overrides, dict) or not set(overrides).issubset(error.get("auto_repair", {})):
			raise HTTPException(status_code=422, detail="فقط فیلدهای پیشنهادی قابل اصلاح هستند.")
		for key, value in overrides.items():
			if key == "confidence" and (
				isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1
			):
				raise HTTPException(status_code=422, detail="confidence باید عددی بین صفر و یک باشد.")
			if key == "item_type" and value not in {"concept", "category", "relationship", "paradigm", "central_phenomenon"}:
				raise HTTPException(status_code=422, detail="item_type انتخاب‌شده معتبر نیست.")
			if key == "needs_researcher_review" and not isinstance(value, bool):
				raise HTTPException(status_code=422, detail="needs_researcher_review باید true یا false باشد.")
			error["repaired_record"][key] = value
		error["auto_repair"] = overrides
	if decision == "repaired":
		_apply_repaired_record(analysis, error)
	quality["record_errors"] = [item for item in errors if item is not error]
	record_type = error.get("record_type", "")
	record_id = error.get("parent_category_id") or error.get("record_id", "")
	stage = error.get("stage", "stage2")
	issue_prefix = f"رکورد {error.get('record_type')} {error.get('record_id')} از مرحله {stage} پردازش نشد:"
	quality["validation_issues"] = [
		item for item in quality.get("validation_issues", [])
		if not item.startswith(issue_prefix)
	]
	item_type = (
		"relationship" if record_type == "relationship"
		else "category" if record_type == "category" or error.get("parent_category_id")
		else "concept" if record_type in {"concept", "unclassified_concept"}
		else "paradigm"
	)
	interview["researcher_review"] = [
		item for item in interview.get("researcher_review", [])
		if not (
			item.get("item_type") == item_type
			and item.get("item_id") == record_id
			and item.get("issue") == "رکورد نیازمند بازبینی ساختاری"
		)
	]
	_refresh_quality_status(analysis)
	if decision == "skipped":
		quality["missing_data_detected"] = True
		quality["final_status"] = "incomplete"
		analysis["analysis_status"] = "incomplete"
		limitations = quality.setdefault("analysis_limitations", [])
		limitations.append(
			f"رکورد {record_type} {error.get('record_id')} به تصمیم پژوهشگر از تحلیل فعال کنار گذاشته شد؛ متن خام حفظ شده است."
		)
	updated_edits = dict(project.get("researcher_edits") or {})
	resolutions = list(updated_edits.get("structural_resolutions") or [])
	resolutions.append({
		"resolution_id": resolution_id,
		"record_type": error.get("record_type"),
		"record_id": error.get("record_id"),
		"decision": decision,
		"auto_repair": error.get("auto_repair"),
		"confirmed": True,
	})
	updated_edits["structural_resolutions"] = resolutions
	update_project(project_id, analysis=analysis, researcher_edits=updated_edits)
	mark_synthesis_runs_stale_for_source(project_id)
	return RedirectResponse(f"/results/{project_id}/interview-analysis?show_quality=1#quality-audit", status_code=303)


@router.get("/{project_id}/concept-review")
def concept_review_page(request: Request, project_id: str):
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	stored_edits = project.get("researcher_edits") or {}
	review_edits = stored_edits.get("concept_reviews", {})
	concepts_by_id = {
		item["concept_id"]: item for item in project.get("concepts", [])
		if item.get("concept_id")
	}
	paragraphs_by_id = {
		item["paragraph_id"]: item for item in project.get("paragraphs", [])
		if item.get("paragraph_id")
	}
	reviews = [
		{
			"review": review,
			"edit": review_edits.get(review["concept_id"], {}),
			"source_paragraphs": [
				paragraphs_by_id[paragraph_id]
				for paragraph_id in concepts_by_id.get(review["concept_id"], {}).get("paragraph_ids", [])
				if paragraph_id in paragraphs_by_id
			],
		}
		for review in project["analysis"]["concept_reviews"]
	]
	return templates.TemplateResponse(
		request=request,
		name="concept_review.html",
		context={"project": project, "reviews": reviews},
	)


@router.post("/{project_id}/concept-review")
def save_concept_review(
	project_id: str,
	concept_ids: list[str] = Form([]),
	suggested_concepts: list[str] = Form([]),
	suggested_codes: list[str] = Form([]),
	researcher_notes: list[str] = Form([]),
	confirmed_concept_ids: list[str] = Form([]),
):
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	expected_ids = {item["concept_id"] for item in project["analysis"]["concept_reviews"]}
	if len(concept_ids) != len(suggested_concepts) or len(concept_ids) != len(suggested_codes) or len(concept_ids) != len(researcher_notes):
		raise HTTPException(status_code=422, detail="اطلاعات بازبینی مفاهیم کامل ارسال نشده است.")
	if len(concept_ids) != len(set(concept_ids)) or set(concept_ids) != expected_ids:
		raise HTTPException(status_code=422, detail="شناسه مفاهیم فرم با خروجی تحلیل مطابقت ندارد.")
	updated_edits = dict(project.get("researcher_edits") or {})
	updated_edits["concept_reviews"] = {
		concept_id: {
			"suggested_concept": title.strip(),
			"suggested_code": code.strip(),
			"researcher_note": note.strip(),
			"confirmed": concept_id in set(confirmed_concept_ids),
		}
		for concept_id, title, code, note in zip(concept_ids, suggested_concepts, suggested_codes, researcher_notes)
	}
	update_project(project_id, researcher_edits=updated_edits)
	mark_synthesis_runs_stale_for_source(project_id)
	return RedirectResponse(f"/results/{project_id}/concept-review?saved=1", status_code=303)


@router.get("/{project_id}/interview-analysis")
def interview_analysis_page(request: Request, project_id: str):
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	analysis = project["analysis"]["interview_analysis"]
	stored_edits = project.get("researcher_edits") or {}
	edit = stored_edits.get("interview_analysis", {})
	category_edits = edit.get("categories", {})
	relationship_edits = edit.get("relationships", {})
	categories = [
		{"item": item, "edit": category_edits.get(item["category_id"], {})}
		for item in analysis["categories"]
	]
	relationships = [
		{"item": item, "edit": relationship_edits.get(item["relationship_id"], {})}
		for item in analysis["relationships"]
	]
	phenomenon = analysis.get("central_phenomenon") or {}
	paragraphs_by_id = {item.get("paragraph_id"): item.get("text", "") for item in project.get("paragraphs", [])}
	evidence_warnings = [
		{
			**warning,
			"warning_index": index,
			"source_text": paragraphs_by_id.get(warning.get("paragraph_id"), ""),
			"can_auto_accept": warning.get("status") == "fuzzy_match" and (warning.get("similarity") or 0) >= 90,
		}
		for index, warning in enumerate(analysis.get("quality_audit", {}).get("evidence_warnings", []))
	]
	record_errors = analysis.get("quality_audit", {}).get("record_errors", [])
	recovered_by_key = {
		(item.get("stage"), item.get("record_type"), item.get("record_id")): item
		for item in _recover_structural_record_errors(project)
	}
	resolved_record_errors = []
	for item in record_errors:
		recovered = recovered_by_key.get((item.get("stage"), item.get("record_type"), item.get("record_id")))
		if not item.get("auto_repair") and recovered:
			item = recovered
		_ensure_repaired_record(item)
		resolved_record_errors.append(item)
	record_errors = [_human_record_error(item) for item in resolved_record_errors]
	validation_issues = [
		{"message": _human_validation_issue(issue), "issue_id": index}
		for index, issue in enumerate(analysis.get("quality_audit", {}).get("validation_issues", []))
	]
	return templates.TemplateResponse(
		request=request,
		name="interview_analysis_review.html",
		context={
			"project": project,
			"analysis": analysis,
			"categories": categories,
			"relationships": relationships,
			"evidence_warnings": evidence_warnings,
			"record_errors": record_errors,
			"validation_issues": validation_issues,
			"edit": edit,
			"phenomenon": phenomenon,
			"paradigm_labels": [
				("causal_conditions", "شرایط علّی"),
				("central_phenomenon", "پدیده محوری"),
				("contextual_conditions", "شرایط زمینه‌ای"),
				("intervening_conditions", "شرایط مداخله‌گر"),
				("action_interaction_strategies", "راهبردهای کنش و تعامل"),
				("consequences", "پیامدها"),
			],
		},
	)


@router.post("/{project_id}/interview-analysis")
def save_interview_analysis(
	project_id: str,
	category_ids: list[str] = Form([]),
	category_names: list[str] = Form([]),
	category_definitions: list[str] = Form([]),
	relationship_ids: list[str] = Form([]),
	relationship_descriptions: list[str] = Form([]),
	central_phenomenon_title: str = Form(""),
	central_phenomenon_definition: str = Form(""),
	researcher_note: str = Form(""),
	confirmed: bool = Form(False),
):
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	analysis = project["analysis"]["interview_analysis"]
	expected_categories = {item["category_id"] for item in analysis["categories"]}
	expected_relationships = {item["relationship_id"] for item in analysis["relationships"]}
	if len(category_ids) != len(category_names) or len(category_ids) != len(category_definitions):
		raise HTTPException(status_code=422, detail="اطلاعات مقوله‌ها کامل ارسال نشده است.")
	if len(relationship_ids) != len(relationship_descriptions):
		raise HTTPException(status_code=422, detail="اطلاعات روابط کامل ارسال نشده است.")
	if set(category_ids) != expected_categories or len(category_ids) != len(expected_categories):
		raise HTTPException(status_code=422, detail="شناسه مقوله‌ها با خروجی تحلیل مطابقت ندارد.")
	if set(relationship_ids) != expected_relationships or len(relationship_ids) != len(expected_relationships):
		raise HTTPException(status_code=422, detail="شناسه روابط با خروجی تحلیل مطابقت ندارد.")
	updated_edits = dict(project.get("researcher_edits") or {})
	updated_edits["interview_analysis"] = {
		"categories": {
			category_id: {"category_name": name.strip(), "definition": definition.strip()}
			for category_id, name, definition in zip(category_ids, category_names, category_definitions)
		},
		"relationships": {
			relationship_id: {"description": description.strip()}
			for relationship_id, description in zip(relationship_ids, relationship_descriptions)
		},
		"central_phenomenon": {
			"title": central_phenomenon_title.strip(),
			"definition": central_phenomenon_definition.strip(),
		},
		"researcher_note": researcher_note.strip(),
		"confirmed": confirmed,
	}
	update_project(project_id, researcher_edits=updated_edits)
	mark_synthesis_runs_stale_for_source(project_id)
	return RedirectResponse(f"/results/{project_id}/interview-analysis?saved=1", status_code=303)


@router.get("/{project_id}/categories")
def categories_page(request: Request, project_id: str):
	project = _project_or_404(project_id)
	return templates.TemplateResponse(
		request=request,
		name="categories.html",
		context={"project": project, "analysis": project["analysis"]},
	)


@router.get("/{project_id}/paradigm")
def paradigm_page(request: Request, project_id: str):
	project = _project_or_404(project_id)
	if project["analysis"].get("schema_version") == "2.0":
		diagram = prepare_paradigm_diagram(project)
		stored_hash = (project.get("researcher_edits") or {}).get("paradigm_source_hash")
		return templates.TemplateResponse(
			request=request,
			name="paradigm.html",
			context={
				"project": project,
				"diagram": diagram,
				"diagram_needs_update": stored_hash != diagram["source_hash"],
			},
		)
	return templates.TemplateResponse(
		request=request,
		name="paradigm.html",
		context={
			"project": project,
			"analysis": project["analysis"],
			"results": prepare_analysis_results(project["analysis"]),
		},
	)


@router.post("/{project_id}/paradigm/rebuild")
def rebuild_paradigm(project_id: str):
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	diagram = prepare_paradigm_diagram(project)
	updated_edits = dict(project.get("researcher_edits") or {})
	updated_edits["paradigm_source_hash"] = diagram["source_hash"]
	if update_project(project_id, researcher_edits=updated_edits) is None:
		raise HTTPException(status_code=404, detail="پروژه پیدا نشد.")
	return RedirectResponse(f"/results/{project_id}/paradigm?rebuilt=1", status_code=303)


@router.get("/{project_id}/paradigm/export/{output_format}")
def export_paradigm_diagram(project_id: str, output_format: str):
	if output_format not in {"png", "svg"}:
		raise HTTPException(status_code=422, detail="فرمت خروجی باید PNG یا SVG باشد.")
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	diagram = prepare_paradigm_diagram(project)
	try:
		image = render_paradigm_diagram(diagram, output_format)
	except ExecutableNotFound as error:
		raise HTTPException(
			status_code=503,
			detail="Graphviz روی سرور نصب نیست؛ امکان ساخت فایل نمودار وجود ندارد.",
		) from error
	return Response(
		content=image,
		media_type="image/svg+xml" if output_format == "svg" else "image/png",
		headers={"Content-Disposition": f'attachment; filename="paradigm-diagram.{output_format}"'},
	)


@router.get("/{project_id}/relationships")
def relationships_page(request: Request, project_id: str):
	project = _project_or_404(project_id)
	return templates.TemplateResponse(
		request=request,
		name="relationships.html",
		context={"project": project},
	)
