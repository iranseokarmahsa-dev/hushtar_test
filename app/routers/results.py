"""Analysis result, category, paradigm, and relationship views."""

import json
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from graphviz.backend import ExecutableNotFound
from fastapi.templating import Jinja2Templates
from rapidfuzz import fuzz

from app.core.database import get_project, update_project
from app.repositories.synthesis_repository import mark_synthesis_runs_stale_for_source
from app.services.analysis_service import parse_stage_markdown, prepare_analysis_results
from app.services.evidence_validation import group_evidence_warnings
from app.services.paradigm_service import prepare_paradigm_diagram, render_paradigm_diagram

router = APIRouter(prefix="/results", tags=["results"])
templates = Jinja2Templates(directory="app/templates")
CONCEPT_REVIEW_STATUS_LABELS = {
	"pending": "نیازمند بررسی",
	"suggestion": "پیشنهاد اصلاح دارد",
	"approved": "تأیید شده",
	"rejected": "پیشنهاد رد شده · حفظ اطلاعات اولیه",
	"edited": "اصلاح‌شده توسط پژوهشگر",
	"in_progress": "در حال بررسی",
	"deferred": "به تعویق افتاده · همچنان حل‌نشده",
}


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


def _can_finalize_interview_analysis(project: dict, analysis: dict, edit: dict) -> bool:
	phenomenon = analysis.get("central_phenomenon") or {}
	phenomenon_edit = edit.get("central_phenomenon", {})
	quality = analysis.get("quality_audit", {})
	diagram = prepare_paradigm_diagram(project)
	placed_category_ids = {
		category_id
		for node in diagram["nodes"]
		if node["model_key"] not in {"central_phenomenon", "categories"}
		for category_id in node["category_ids"]
	}
	placements = edit.get("paradigm_placements", {})
	return (
		bool(phenomenon)
		and phenomenon_edit.get("decision") == "approved"
		and all(
			edit.get("categories", {}).get(item["category_id"], {}).get("decision")
			in {"approved", "rejected"}
			for item in analysis.get("categories", [])
		)
		and all(
			edit.get("relationships", {}).get(item["relationship_id"], {}).get("decision")
			in {"approved", "rejected"}
			for item in analysis.get("relationships", [])
		)
		and all(
			edit.get("categories", {}).get(item["category_id"], {}).get("decision") == "rejected"
			or item["category_id"] in placed_category_ids
			or placements.get(item["category_id"], {}).get("component_key") == "unassigned"
			for item in analysis.get("categories", [])
		)
		and all(
			edit.get("relationships", {}).get(item["relationship_id"], {}).get("decision") == "rejected"
			or bool(item.get("evidence"))
			for item in analysis.get("relationships", [])
		)
		and all(
			(project.get("researcher_edits") or {})
			.get("review_issue_lifecycle", {})
			.get(f"researcher-review-{index}", {})
			.get("status")
			in {"approved", "corrected", "rejected"}
			for index, item in enumerate(analysis.get("researcher_review", []))
			if item.get("issue") not in {"شاهد نیازمند بازبینی", "رکورد نیازمند بازبینی ساختاری"}
		)
		and all(
			item.get("confirmed")
			for item in (project.get("researcher_edits") or {}).get("concept_reviews", {}).values()
		)
		and not quality.get("unsupported_relationships_detected")
		and not quality.get("contradictions_detected")
		and not quality.get("missing_data_detected")
		and not quality.get("validation_issues")
		and not quality.get("evidence_warnings")
		and not quality.get("rejected_evidence_warnings")
		and not quality.get("record_errors")
		and quality.get("all_concepts_reviewed", False)
		and quality.get("all_ids_preserved", False)
		and quality.get("all_evidence_verifiable", False)
	)


def _finalized_v2_project_or_redirect(project_id: str):
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	analysis = project["analysis"]["interview_analysis"]
	edit = (project.get("researcher_edits") or {}).get("interview_analysis", {})
	if not edit.get("confirmed") or not _can_finalize_interview_analysis(project, analysis, edit):
		return RedirectResponse(
			f"/results/{project_id}/interview-analysis?show_quality=1",
			status_code=303,
		)
	return project


def _concept_review_status(review: dict, edit: dict) -> str:
	decision = edit.get("decision")
	if decision == "keep_original":
		return "rejected"
	if decision == "researcher_edit":
		return "edited"
	if decision == "approve_suggestion" or edit.get("confirmed"):
		return "approved"
	if edit.get("lifecycle_status") in {"in_progress", "deferred"}:
		return edit["lifecycle_status"]
	if (
		review.get("concept_change_required") is True
		or review.get("code_evaluation") == "revise"
	):
		return "suggestion"
	return "pending"


def _concept_review_row(
	project: dict, review: dict, edit: dict, *, include_source: bool = False
) -> dict:
	status = _concept_review_status(review, edit)
	row = {
		"review": review,
		"edit": edit,
		"status": status,
		"status_label": CONCEPT_REVIEW_STATUS_LABELS[status],
		"search_text": " ".join(
			str(value or "")
			for value in (
				review.get("concept_id"),
				review.get("original_concept"),
				review.get("suggested_concept"),
				*[item.get("exact_quote", "") for item in review.get("evidence", [])],
			)
		).casefold(),
	}
	if include_source:
		paragraphs_by_id = {
			item["paragraph_id"]: item for item in project.get("paragraphs", [])
			if item.get("paragraph_id")
		}
		paragraph_ids = dict.fromkeys([
			*next(
				(
					item.get("paragraph_ids", [])
					for item in project.get("concepts", [])
					if item.get("concept_id") == review["concept_id"]
				),
				[],
			),
			*[
				item.get("paragraph_id")
				for item in review.get("evidence", [])
				if item.get("paragraph_id")
			],
		])
		row["source_paragraphs"] = [
			paragraphs_by_id[paragraph_id]
			for paragraph_id in paragraph_ids
			if paragraph_id in paragraphs_by_id
		]
	return row


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
	rejected_warnings = quality.get("rejected_evidence_warnings") or []
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
	quality["all_evidence_verifiable"] = not warnings and not rejected_warnings
	issues = quality.get("validation_issues") or []
	quality["validation_issues"] = [
		issue for issue in issues
		if f"شاهد {record_type} {record_id} نیازمند بازبینی است:" not in issue
		or any(item.get("record_type") == record_type and item.get("record_id") == record_id for item in warnings)
	]
	quality["missing_data_detected"] = bool(
		quality.get("record_errors")
		or warnings
		or rejected_warnings
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
		or quality.get("rejected_evidence_warnings")
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
	warning_index: int | None = Form(None),
	issue_id: str = Form(""),
	action: str = Form(...),
	replacement_quote: str = Form(""),
	decision_note: str = Form(""),
):
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	analysis = project["analysis"]
	interview = analysis["interview_analysis"]
	quality = interview["quality_audit"]
	warnings = quality.get("evidence_warnings") or []
	if issue_id:
		matching_group = next(
			(
				group for group in group_evidence_warnings(warnings)
				if group["issue_id"] == issue_id
			),
			None,
		)
		if matching_group is None:
			raise HTTPException(status_code=404, detail="مورد تجمیع‌شده پیدا نشد یا قبلاً حل شده است.")
		warning_indices = matching_group["warning_indices"]
	elif warning_index is not None:
		warning_indices = [warning_index]
	else:
		warning_indices = []
	if not warning_indices or any(index < 0 or index >= len(warnings) for index in warning_indices):
		raise HTTPException(status_code=404, detail="هشدار شاهد پیدا نشد یا قبلاً حل شده است.")
	selected_warnings = [warnings[index] for index in warning_indices]
	warning = selected_warnings[0]
	if any(item.get("status") not in {"fuzzy_match", "unmatched"} for item in selected_warnings):
		raise HTTPException(
			status_code=409,
			detail="این هشدار فنی است و از مسیر تصمیم پژوهشگر قابل اصلاح یا رد نیست.",
		)
	if action == "reject":
		if not decision_note.strip():
			raise HTTPException(status_code=422, detail="برای رد پیشنهاد، یادداشت پژوهشگر الزامی است.")
		now = datetime.now(timezone.utc).isoformat()
		rejected = list(quality.get("rejected_evidence_warnings") or [])
		quality["rejected_evidence_warnings"] = [
			*rejected,
			*[
				{
					**item,
					"decision_note": decision_note.strip(),
					"decided_at": now,
					"decision": "rejected",
				}
				for item in selected_warnings
			],
		]
		quality["evidence_warnings"] = [
			item for index, item in enumerate(warnings)
			if index not in set(warning_indices)
		]
		for item in selected_warnings:
			_refresh_evidence_audit(analysis, item)
		updated_edits = dict(project.get("researcher_edits") or {})
		resolutions = list(updated_edits.get("evidence_resolutions") or [])
		resolutions.append({
			"issue_id": issue_id,
			"decision": "rejected",
			"status": "rejected",
			"note": decision_note.strip(),
			"decided_at": now,
			"references": [
				{"stage": item.get("stage"), "record_type": item.get("record_type"), "record_id": item.get("record_id")}
				for item in selected_warnings
			],
		})
		updated_edits["evidence_resolutions"] = resolutions
		update_project(project_id, analysis=analysis, researcher_edits=updated_edits)
		mark_synthesis_runs_stale_for_source(project_id)
		return RedirectResponse(f"/results/{project_id}/interview-analysis?show_quality=1#analysis-review", status_code=303)
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
		if not decision_note.strip():
			raise HTTPException(status_code=422, detail="برای ثبت اصلاح شاهد، یادداشت پژوهشگر الزامی است.")
		resolved_quote = replacement_quote.strip()
		if not resolved_quote or resolved_quote not in source_text:
			raise HTTPException(status_code=422, detail="متن اصلاح‌شده باید عیناً و پیوسته در پاراگراف اصلی وجود داشته باشد.")
	else:
		raise HTTPException(status_code=422, detail="نوع اصلاح شاهد نامعتبر است.")
	targets = []
	for item in selected_warnings:
		root = analysis["concept_reviews"] if item.get("stage") == "stage1" else interview
		target = _find_evidence_target(
			root,
			item.get("record_type", ""),
			item.get("record_id", ""),
			item.get("paragraph_id", ""),
			item.get("exact_quote", ""),
		)
		if target is None:
			raise HTTPException(status_code=409, detail="یکی از ارجاع‌های تجمیع‌شده با نسخهٔ فعلی تحلیل تطبیق ندارد؛ خروجی مرحله را دوباره بررسی کنید.")
		targets.append(target)
	original_quotes = [target["exact_quote"] for target in targets]
	for target in targets:
		target["exact_quote"] = resolved_quote
	removed_indices = set(warning_indices)
	quality["evidence_warnings"] = [
		item for index, item in enumerate(warnings) if index not in removed_indices
	]
	for item in selected_warnings:
		_refresh_evidence_audit(analysis, item)
	updated_edits = dict(project.get("researcher_edits") or {})
	resolutions = list(updated_edits.get("evidence_resolutions") or [])
	now = datetime.now(timezone.utc).isoformat()
	resolutions.append({
		"issue_id": issue_id,
		"references": [
			{
				"stage": item.get("stage"),
				"record_type": item.get("record_type"),
				"record_id": item.get("record_id"),
				"paragraph_id": item.get("paragraph_id"),
				"original_quote": original_quote,
				"resolved_quote": resolved_quote,
			}
			for item, original_quote in zip(selected_warnings, original_quotes)
		],
		"action": action,
		"status": "corrected",
		"note": decision_note.strip(),
		"decided_at": now,
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
	decision_note: str = Form(""),
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
	if not decision_note.strip():
		raise HTTPException(status_code=422, detail="برای ثبت اصلاح یا کنارگذاشتن رکورد، یادداشت پژوهشگر الزامی است.")
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
		"status": "corrected" if decision == "repaired" else "rejected",
		"note": decision_note.strip(),
		"decided_at": datetime.now(timezone.utc).isoformat(),
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
	reviews = [
		_concept_review_row(project, review, review_edits.get(review["concept_id"], {}))
		for review in project["analysis"]["concept_reviews"]
	]
	total_count = len(reviews)
	approved_count = sum(row["status"] in {"approved", "edited"} for row in reviews)
	reviewed_count = sum(row["status"] in {"approved", "edited", "rejected"} for row in reviews)
	remaining_count = total_count - reviewed_count
	progress = round(reviewed_count / total_count * 100) if total_count else 0
	status_counts = {
		status: sum(row["status"] == status for row in reviews)
		for status in CONCEPT_REVIEW_STATUS_LABELS
	}
	return templates.TemplateResponse(
		request=request,
		name="concept_review.html",
		context={
			"project": project,
			"reviews": reviews,
			"status_counts": status_counts,
			"total_count": total_count,
			"approved_count": approved_count,
			"remaining_count": remaining_count,
			"progress": progress,
			"open_concept_id": request.query_params.get("open", ""),
		},
	)


@router.get(
	"/{project_id}/concept-review/{concept_id}",
	response_class=HTMLResponse,
)
def concept_review_detail(request: Request, project_id: str, concept_id: str):
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	review = next(
		(
			item for item in project["analysis"]["concept_reviews"]
			if item["concept_id"] == concept_id
		),
		None,
	)
	if review is None:
		raise HTTPException(status_code=404, detail="مفهوم برای بازبینی پیدا نشد.")
	edit = (project.get("researcher_edits") or {}).get("concept_reviews", {}).get(concept_id, {})
	return templates.TemplateResponse(
		request=request,
		name="concept_review_detail.html",
		context={
			"project": project,
			"row": _concept_review_row(project, review, edit, include_source=True),
		},
	)


@router.post("/{project_id}/concept-review")
def save_concept_review(
	project_id: str,
	concept_ids: list[str] = Form([]),
	suggested_concepts: list[str] = Form([]),
	suggested_codes: list[str] = Form([]),
	researcher_notes: list[str] = Form([]),
	confirmed_concept_ids: list[str] = Form([]),
	review_decisions: list[str] = Form([]),
	review_statuses: list[str] = Form([]),
	decision_action: str = Form(""),
):
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	reviews_by_id = {
		item["concept_id"]: item for item in project["analysis"]["concept_reviews"]
	}
	expected_ids = set(reviews_by_id)
	if len(concept_ids) != len(suggested_concepts) or len(concept_ids) != len(suggested_codes) or len(concept_ids) != len(researcher_notes):
		raise HTTPException(status_code=422, detail="اطلاعات بازبینی مفاهیم کامل ارسال نشده است.")
	if not concept_ids or len(concept_ids) != len(set(concept_ids)) or not set(concept_ids).issubset(expected_ids):
		raise HTTPException(status_code=422, detail="شناسه مفاهیم فرم با خروجی تحلیل مطابقت ندارد.")
	if review_decisions and len(review_decisions) != len(concept_ids):
		raise HTTPException(status_code=422, detail="وضعیت تصمیم‌گیری مفاهیم کامل ارسال نشده است.")
	if review_statuses and len(review_statuses) != len(concept_ids):
		raise HTTPException(status_code=422, detail="وضعیت چرخه عمر مفاهیم کامل ارسال نشده است.")
	action_concept_id, action_decision = "", ""
	if decision_action:
		action_parts = decision_action.split("|", 1)
		if len(action_parts) != 2 or action_parts[0] not in concept_ids or action_parts[1] not in {
			"approve_suggestion", "keep_original", "researcher_edit",
		}:
			raise HTTPException(status_code=422, detail="تصمیم بازبینی مفاهیم معتبر نیست.")
		action_concept_id, action_decision = action_parts
	updated_edits = dict(project.get("researcher_edits") or {})
	previous_edits = updated_edits.get("concept_reviews", {})
	confirmed_ids = set(confirmed_concept_ids)
	now = datetime.now(timezone.utc).isoformat()
	next_edits = dict(previous_edits)
	for index, (concept_id, title, code, note) in enumerate(
		zip(concept_ids, suggested_concepts, suggested_codes, researcher_notes)
	):
		review = reviews_by_id[concept_id]
		old_edit = previous_edits.get(concept_id, {})
		decision = review_decisions[index] if review_decisions else old_edit.get("decision", "")
		old_lifecycle_status = old_edit.get("lifecycle_status") or {
			"approve_suggestion": "approved",
			"keep_original": "rejected",
			"researcher_edit": "corrected",
		}.get(old_edit.get("decision"), "open")
		lifecycle_status = (
			review_statuses[index]
			if review_statuses
			else old_lifecycle_status
		)
		if lifecycle_status not in {"open", "in_progress", "deferred", "approved", "corrected", "rejected"}:
			raise HTTPException(status_code=422, detail="وضعیت چرخه عمر مفهوم معتبر نیست.")
		if decision not in {"", "approve_suggestion", "keep_original", "researcher_edit"}:
			raise HTTPException(status_code=422, detail="وضعیت تصمیم‌گیری مفاهیم معتبر نیست.")
		if concept_id == action_concept_id:
			decision = action_decision
			lifecycle_status = {
				"approve_suggestion": "approved",
				"keep_original": "rejected",
				"researcher_edit": "edited",
			}[action_decision]
		if review_statuses and lifecycle_status != old_lifecycle_status:
			if not note.strip():
				raise HTTPException(status_code=422, detail="برای تغییر وضعیت چرخه عمر، یادداشت پژوهشگر الزامی است.")
			if lifecycle_status in {"in_progress", "deferred", "open"}:
				decision = ""
		expected_terminal_decisions = {
			"approved": "approve_suggestion",
			"corrected": "researcher_edit",
			"rejected": "keep_original",
		}
		if (
			review_statuses
			and lifecycle_status in expected_terminal_decisions
			and decision != expected_terminal_decisions[lifecycle_status]
		):
			raise HTTPException(status_code=422, detail="وضعیت نهایی باید با تصمیم ثبت‌شده برای مفهوم یکسان باشد.")

		if decision == "approve_suggestion":
			final_title = review.get("suggested_concept", "").strip()
			final_code = review.get("suggested_code", "").strip()
		elif decision == "keep_original":
			final_title = review.get("original_concept", "").strip()
			final_code = review.get("original_code", "").strip()
		else:
			final_title, final_code = title.strip(), code.strip()
		if concept_id == action_concept_id and decision == "researcher_edit" and not (final_title and final_code):
			raise HTTPException(status_code=422, detail="عنوان و کد نهایی پژوهشگر نباید خالی باشند.")

		previously_confirmed = old_edit.get("confirmed", False)
		confirmed = decision in {"approve_suggestion", "keep_original", "researcher_edit"}
		if not review_decisions and not decision_action:
			if not (review_statuses and lifecycle_status in {"in_progress", "deferred", "open"}):
				confirmed = concept_id in confirmed_ids
				decision = "approve_suggestion" if confirmed else ""
			final_title = title.strip()
			final_code = code.strip()
		elif not decision:
			confirmed = previously_confirmed

		if decision in {"approve_suggestion", "keep_original", "researcher_edit"} and not note.strip():
			raise HTTPException(status_code=422, detail="برای ثبت تصمیم مفهوم، یادداشت پژوهشگر الزامی است.")
		if review_statuses and lifecycle_status in {"in_progress", "deferred", "open"}:
			confirmed = False
		updated_edit = {
			**old_edit,
			"suggested_concept": final_title,
			"suggested_code": final_code,
			"final_concept": final_title,
			"final_code": final_code,
			"researcher_note": note.strip(),
			"confirmed": confirmed,
			"decision": decision,
			"lifecycle_status": lifecycle_status,
		}
		status_history = list(old_edit.get("status_history") or [])
		if lifecycle_status != old_lifecycle_status or (
			decision != old_edit.get("decision", "") and decision
		):
			status_history.append({
				"status": lifecycle_status,
				"note": note.strip(),
				"changed_at": now,
			})
		updated_edit["status_history"] = status_history
		if decision:
			updated_edit["decided_at"] = (
				now if concept_id == action_concept_id
				else old_edit.get("decided_at", now)
			)
		elif old_edit.get("decided_at") and (review_decisions or decision_action):
			updated_edit["decided_at"] = old_edit["decided_at"]
		next_edits[concept_id] = updated_edit
	updated_edits["concept_reviews"] = next_edits
	update_project(project_id, researcher_edits=updated_edits)
	mark_synthesis_runs_stale_for_source(project_id)
	redirect_query = {"saved": "1"}
	if action_concept_id:
		ordered_ids = list(reviews_by_id)
		start = ordered_ids.index(action_concept_id)
		for offset in range(1, len(ordered_ids)):
			candidate = ordered_ids[(start + offset) % len(ordered_ids)]
			if not next_edits.get(candidate, {}).get("confirmed"):
				redirect_query["open"] = candidate
				break
	return RedirectResponse(
		f"/results/{project_id}/concept-review?{urlencode(redirect_query)}",
		status_code=303,
	)


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
	placement_edits = edit.get("paradigm_placements", {})
	categories = [
		{
			"item": item,
			"edit": category_edits.get(item["category_id"], {}),
			"placement": placement_edits.get(item["category_id"], {}),
		}
		for item in analysis["categories"]
	]
	relationships = [
		{"item": item, "edit": relationship_edits.get(item["relationship_id"], {})}
		for item in analysis["relationships"]
	]
	paragraphs_by_id = {
		item["paragraph_id"]: item
		for item in project.get("paragraphs", [])
		if item.get("paragraph_id")
	}
	concept_reviews_by_id = {
		item["concept_id"]: item
		for item in project["analysis"].get("concept_reviews", [])
		if item.get("concept_id")
	}
	concepts_by_id = {}
	for concept in project.get("concepts", []):
		concept_id = concept.get("concept_id")
		if not concept_id:
			continue
		review = concept_reviews_by_id.get(concept_id, {})
		paragraph_ids = dict.fromkeys([
			*concept.get("paragraph_ids", []),
			*[
				item.get("paragraph_id")
				for item in review.get("evidence", [])
				if item.get("paragraph_id")
			],
		])
		concepts_by_id[concept_id] = {
			"item": concept,
			"review": review,
			"source_paragraphs": [
				paragraphs_by_id[paragraph_id]
				for paragraph_id in paragraph_ids
				if paragraph_id in paragraphs_by_id
			],
		}
	for row in categories:
		row["concepts"] = [
			concepts_by_id[concept_id]
			for concept_id in row["item"].get("concept_ids", [])
			if concept_id in concepts_by_id
		]
	concept_anchor_by_id = {}
	for row in categories:
		for concept in row["concepts"]:
			concept_id = concept["item"]["concept_id"]
			concept_anchor_by_id.setdefault(
				concept_id,
				f"concept-{concept_id}-{row['item']['category_id']}",
			)
	for row in categories:
		row["concept_anchors"] = {
			concept_id: concept_anchor_by_id.get(concept_id)
			for concept_id in row["item"].get("concept_ids", [])
		}
	for row in relationships:
		row["concept_anchors"] = {
			concept_id: concept_anchor_by_id.get(concept_id)
			for concept_id in row["item"].get("supporting_concept_ids", [])
		}
	phenomenon = analysis.get("central_phenomenon") or {}
	phenomenon_edit = edit.get("central_phenomenon", {})
	category_decisions = {
		item["category_id"]: category_edits.get(item["category_id"], {}).get("decision", "")
		for item in analysis["categories"]
	}
	relationship_decisions = {
		item["relationship_id"]: relationship_edits.get(item["relationship_id"], {}).get("decision", "")
		for item in analysis["relationships"]
	}
	pending_category_count = sum(
		decision not in {"approved", "rejected"}
		for decision in category_decisions.values()
	)
	pending_relationship_count = sum(
		decision not in {"approved", "rejected"}
		for decision in relationship_decisions.values()
	)
	pending_decisions = pending_category_count + pending_relationship_count
	if phenomenon:
		pending_decisions += phenomenon_edit.get("decision") != "approved"
	review_issues = []
	review_lifecycle = stored_edits.get("review_issue_lifecycle", {})
	paradigm_diagram = prepare_paradigm_diagram(project)
	placed_category_ids = {
		category_id
		for node in paradigm_diagram["nodes"]
		if node["model_key"] not in {"central_phenomenon", "categories"}
		for category_id in node["category_ids"]
	}

	def add_review_issue(anchor, label, detail, kind="quality", severity="medium", issue_id=None):
		lifecycle = review_lifecycle.get(issue_id or anchor, {})
		if lifecycle.get("status") in {"approved", "corrected", "rejected"}:
			return
		review_issues.append({
			"issue_id": issue_id or anchor,
			"anchor": anchor,
			"label": label,
			"detail": detail,
			"kind": kind,
			"severity": severity,
			"status": lifecycle.get("status", "open"),
			"note": lifecycle.get("note", ""),
			"allow_terminal": kind == "quality",
		})

	for row in categories:
		if (
			row["edit"].get("decision") != "rejected"
			and not row["placement"].get("component_key")
			and row["item"]["category_id"] not in placed_category_ids
		):
			add_review_issue(
				f"category-{row['item']['category_id']}",
				f"جایگاه مقوله {row['item']['category_id']}",
				"این مقوله در هیچ‌یک از مؤلفه‌های پارادایمی قرار نگرفته است.",
				"placement",
				issue_id=f"placement-{row['item']['category_id']}",
			)
		if row["edit"].get("decision") not in {"approved", "rejected"}:
			add_review_issue(
				f"category-{row['item']['category_id']}",
				f"مقوله {row['item']['category_id']}",
				row["item"].get("category_name", ""),
				"category",
			)
	for row in relationships:
		if (
			row["edit"].get("decision") not in {"approved", "rejected"}
			or (
				row["edit"].get("decision") == "approved"
				and not row["item"].get("evidence")
			)
		):
			add_review_issue(
				f"relationship-{row['item']['relationship_id']}",
				f"رابطه {row['item']['relationship_id']}",
				row["item"].get("description", ""),
				"relationship",
				"high" if not row["item"].get("evidence") else "medium",
			)
	if phenomenon and phenomenon_edit.get("decision") not in {"approved", "rejected"}:
		add_review_issue(
			"central-phenomenon",
			"پدیده محوری",
			phenomenon.get("title", "نیازمند تعیین تکلیف"),
			"category",
			"high" if phenomenon_edit.get("decision") != "approved" else "medium",
		)
	if not phenomenon:
		add_review_issue(
			"central-phenomenon",
			"پدیده محوری تعیین نشده",
			"پدیده محوری هنوز مشخص نشده است.",
			"category",
			"high",
		)
	for index, item in enumerate(analysis.get("researcher_review", [])):
		if item.get("issue") in {"شاهد نیازمند بازبینی", "رکورد نیازمند بازبینی ساختاری"}:
			continue
		add_review_issue(
			f"researcher-review-{index}",
			item.get("item_id") or item.get("item_type", "تحلیل"),
			" · ".join(filter(None, [
				item.get("issue", ""),
				item.get("reason", ""),
				item.get("suggested_action", ""),
			])),
			"quality",
			"high",
		)
	for review in project["analysis"].get("concept_reviews", []):
		concept_id = review.get("concept_id", "")
		if not review.get("needs_researcher_review") or (
			stored_edits.get("concept_reviews", {}).get(concept_id, {}).get("confirmed")
		):
			continue
		category_id = next(
			(
				row["item"]["category_id"]
				for row in categories
				if concept_id in row["item"].get("concept_ids", [])
			),
			"",
		)
		add_review_issue(
			concept_anchor_by_id.get(concept_id, "analysis-review"),
			f"مفهوم {concept_id}",
			"بازبینی مفهوم مرحله قبل هنوز تأیید نشده است.",
			"concept",
		)
	subcategory_count = sum(len(item.get("subcategories", [])) for item in analysis["categories"])
	reviewed_count = (
		sum(decision in {"approved", "rejected"} for decision in category_decisions.values())
		+ sum(decision in {"approved", "rejected"} for decision in relationship_decisions.values())
		+ int(bool(phenomenon_edit.get("decision")))
	)
	review_total = len(categories) + len(relationships) + int(bool(phenomenon))
	review_progress = round(reviewed_count / review_total * 100) if review_total else 0
	can_finalize = _can_finalize_interview_analysis(project, analysis, edit)
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
	decision_evidence_warnings = [
		item for item in evidence_warnings
		if item.get("status") in {"fuzzy_match", "unmatched"}
	]
	evidence_groups = group_evidence_warnings(decision_evidence_warnings)
	for group in evidence_groups:
		group["warning_indices"] = [
			decision_evidence_warnings[index]["warning_index"]
			for index in group["warning_indices"]
		]
		group["anchor"] = f"quality-evidence-{group['warning_indices'][0]}"
		group["source_text"] = paragraphs_by_id.get(group.get("paragraph_id"), "")
		group["can_auto_accept"] = (
			group.get("status") == "fuzzy_match"
			and (group.get("similarity") or 0) >= 90
		)
		add_review_issue(
			group["anchor"],
			f"تطبیق شاهد · {group.get('paragraph_id', '')}",
			f"{len(group['references'])} ارجاع · {group.get('status', 'نیازمند تطبیق')}",
			"evidence",
			"high",
			issue_id=f"quality-evidence-{group['issue_id']}",
		)
		for warning_index in group["warning_indices"]:
			evidence_warnings[warning_index]["issue_id"] = group["issue_id"]
	system_logs = [
		{
			"kind": "validation",
			"label": "کنترل کیفیت",
			"message": _human_validation_issue(issue),
			"status": "system_log",
		}
		for issue in analysis.get("quality_audit", {}).get("validation_issues", [])
		if not issue.startswith("شاهد ") or "نیازمند بازبینی است:" not in issue
	]
	system_logs.extend({
		"kind": "evidence",
		"label": f"تطبیق خودکار · {item.get('record_id', '')}",
		"message": (
			f"{item.get('paragraph_id', '')} · شباهت {item.get('similarity', 0)}٪ · "
			f"«{item.get('matched_quote', '')}»"
		),
		"status": "auto_matched",
	} for item in analysis.get("quality_audit", {}).get("auto_matched_evidence", []))
	system_logs.extend({
		"kind": "evidence",
		"label": f"ردشده · {item.get('record_id', '')}",
		"message": (
			f"{item.get('paragraph_id', '')} · {item.get('decision_note', '')} · "
			f"{item.get('decided_at', '')}"
		),
		"status": "rejected",
	} for item in analysis.get("quality_audit", {}).get("rejected_evidence_warnings", []))
	technical_evidence_groups = group_evidence_warnings([
		item for item in evidence_warnings
		if item.get("status") not in {"fuzzy_match", "unmatched"}
	])
	system_logs.extend({
		"kind": "evidence",
		"label": f"تطبیق شاهد · {group.get('paragraph_id', '')}",
		"message": (
			f"{group.get('status', '')} · {len(group['references'])} ارجاع · "
			"؛ ".join(
				f"{item['record_type']} {item['record_id']}"
				for item in group["references"]
			)
		),
		"status": group.get("status", "system_log"),
	} for group in technical_evidence_groups)
	system_logs.extend({
		"kind": "record",
		"label": item.get("display_title", "رکورد پردازش‌نشده"),
		"message": item.get("display_message", ""),
		"status": "invalid",
	} for item in record_errors)
	review_issues.sort(key=lambda item: item["severity"] != "high")
	category_rows_by_id = {row["item"]["category_id"]: row for row in categories}
	paradigm_components = []
	for node in paradigm_diagram["nodes"]:
		if node["model_key"] == "categories":
			continue
		component_categories = [
			category_rows_by_id[category_id]
			for category_id in node["category_ids"]
			if category_id in category_rows_by_id
		]
		reviewed = (
			phenomenon_edit.get("decision") in {"approved", "rejected"}
			if node["model_key"] == "central_phenomenon"
			else bool(component_categories)
			and all(
				row["edit"].get("decision") in {"approved", "rejected"}
				for row in component_categories
			)
		)
		paradigm_components.append({
			**node,
			"categories": component_categories,
			"concept_anchors": {
				concept_id: concept_anchor_by_id.get(concept_id)
				for concept_id in node["concept_ids"]
			},
			"reviewed": reviewed,
		})
	return templates.TemplateResponse(
		request=request,
		name="interview_analysis_review.html",
		context={
			"project": project,
			"analysis": analysis,
			"categories": categories,
			"relationships": relationships,
			"evidence_warnings": evidence_warnings,
			"decision_evidence_groups": evidence_groups,
			"system_logs": system_logs,
			"record_errors": record_errors,
			"validation_issues": validation_issues,
			"edit": edit,
			"phenomenon": phenomenon,
			"phenomenon_edit": phenomenon_edit,
			"review_issues": review_issues,
			"researcher_decisions": review_issues,
			"review_issue_history": [
				{"issue_id": issue_id, **entry}
				for issue_id, lifecycle in review_lifecycle.items()
				for entry in lifecycle.get("history", [])
			],
			"resolution_history": [
				{
					"issue_id": item.get("issue_id") or item.get("resolution_id", "شاهد / رکورد"),
					"status": item.get("status", item.get("decision", item.get("action", ""))),
					"note": item.get("note", ""),
					"changed_at": item.get("decided_at", ""),
				}
				for key in ("evidence_resolutions", "structural_resolutions")
				for item in stored_edits.get(key, [])
			],
			"pending_decisions": pending_decisions,
			"review_progress": review_progress,
			"reviewed_count": reviewed_count,
			"review_total": review_total,
			"subcategory_count": subcategory_count,
			"can_finalize": can_finalize,
			"paradigm_components": paradigm_components,
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


@router.post("/{project_id}/review-issue")
def update_review_issue_lifecycle(
	project_id: str,
	issue_id: str = Form(...),
	status: str = Form(...),
	note: str = Form(""),
):
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	if status not in {"open", "in_progress", "approved", "corrected", "rejected", "deferred"}:
		raise HTTPException(status_code=422, detail="وضعیت چرخه عمر مورد معتبر نیست.")
	analysis = project["analysis"]["interview_analysis"]
	edit = project.get("researcher_edits") or {}
	analysis_edit = edit.get("interview_analysis", {})
	category_ids = {item.get("category_id") for item in analysis.get("categories", [])}
	relationship_ids = {item.get("relationship_id") for item in analysis.get("relationships", [])}
	valid = (
		issue_id in {f"category-{item_id}" for item_id in category_ids}
		or issue_id in {f"placement-{item_id}" for item_id in category_ids}
		or issue_id in {f"relationship-{item_id}" for item_id in relationship_ids}
		or issue_id == "central-phenomenon"
	)
	if issue_id.startswith("quality-evidence-"):
		evidence_issue_id = issue_id.removeprefix("quality-evidence-")
		valid = any(
			group["issue_id"] == evidence_issue_id
			for group in group_evidence_warnings(analysis.get("quality_audit", {}).get("evidence_warnings", []))
		)
	review_index = None
	if issue_id.startswith("researcher-review-"):
		try:
			review_index = int(issue_id.rsplit("-", 1)[1])
		except ValueError:
			review_index = None
		reviews = analysis.get("researcher_review", [])
		valid = (
			review_index is not None
			and 0 <= review_index < len(reviews)
			and reviews[review_index].get("issue")
			not in {"شاهد نیازمند بازبینی", "رکورد نیازمند بازبینی ساختاری"}
		)
	if not valid:
		raise HTTPException(status_code=404, detail="مورد بررسی پیدا نشد یا قبلاً حذف شده است.")
	if status in {"approved", "corrected", "rejected"} and review_index is None:
		raise HTTPException(
			status_code=422,
			detail="تصمیم نهایی این مورد باید از فرم تأیید/اصلاح/رد مربوط به خودش ثبت شود.",
		)
	if status in {"approved", "corrected", "rejected"} and not note.strip():
		raise HTTPException(status_code=422, detail="برای ثبت تصمیم نهایی، یادداشت پژوهشگر الزامی است.")
	now = datetime.now(timezone.utc).isoformat()
	updated_edits = dict(edit)
	lifecycle = dict(updated_edits.get("review_issue_lifecycle") or {})
	old = lifecycle.get(issue_id, {})
	entry = {"status": status, "note": note.strip(), "changed_at": now}
	history = list(old.get("history") or [])
	history.append(entry)
	lifecycle[issue_id] = {**entry, "history": history}
	updated_edits["review_issue_lifecycle"] = lifecycle
	update_project(project_id, researcher_edits=updated_edits)
	mark_synthesis_runs_stale_for_source(project_id)
	return RedirectResponse(
		f"/results/{project_id}/interview-analysis?show_quality=1#analysis-review",
		status_code=303,
	)


@router.post("/{project_id}/interview-analysis")
def save_interview_analysis(
	project_id: str,
	category_ids: list[str] = Form([]),
	category_names: list[str] = Form([]),
	category_definitions: list[str] = Form([]),
	category_decisions: list[str] = Form([]),
	category_decision_reasons: list[str] = Form([]),
	paradigm_placements: list[str] = Form([]),
	placement_reasons: list[str] = Form([]),
	relationship_ids: list[str] = Form([]),
	relationship_descriptions: list[str] = Form([]),
	relationship_types: list[str] = Form([]),
	relationship_decisions: list[str] = Form([]),
	relationship_decision_reasons: list[str] = Form([]),
	central_phenomenon_title: str = Form(""),
	central_phenomenon_definition: str = Form(""),
	phenomenon_decision: str = Form(""),
	phenomenon_decision_reason: str = Form(""),
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
	if category_decisions and len(category_decisions) != len(category_ids):
		raise HTTPException(status_code=422, detail="وضعیت بازبینی مقوله‌ها کامل ارسال نشده است.")
	if category_decision_reasons and len(category_decision_reasons) != len(category_ids):
		raise HTTPException(status_code=422, detail="دلیل تصمیم مقوله‌ها کامل ارسال نشده است.")
	if paradigm_placements and len(paradigm_placements) != len(category_ids):
		raise HTTPException(status_code=422, detail="جایگاه پارادایمی مقوله‌ها کامل ارسال نشده است.")
	if placement_reasons and len(placement_reasons) != len(category_ids):
		raise HTTPException(status_code=422, detail="دلیل جایگاه مقوله‌ها کامل ارسال نشده است.")
	if len(relationship_ids) != len(relationship_descriptions):
		raise HTTPException(status_code=422, detail="اطلاعات روابط کامل ارسال نشده است.")
	if relationship_types and len(relationship_types) != len(relationship_ids):
		raise HTTPException(status_code=422, detail="نوع روابط کامل ارسال نشده است.")
	if relationship_decisions and len(relationship_decisions) != len(relationship_ids):
		raise HTTPException(status_code=422, detail="وضعیت بازبینی روابط کامل ارسال نشده است.")
	if relationship_decision_reasons and len(relationship_decision_reasons) != len(relationship_ids):
		raise HTTPException(status_code=422, detail="دلیل تصمیم روابط کامل ارسال نشده است.")
	if set(category_ids) != expected_categories or len(category_ids) != len(expected_categories):
		raise HTTPException(status_code=422, detail="شناسه مقوله‌ها با خروجی تحلیل مطابقت ندارد.")
	if set(relationship_ids) != expected_relationships or len(relationship_ids) != len(expected_relationships):
		raise HTTPException(status_code=422, detail="شناسه روابط با خروجی تحلیل مطابقت ندارد.")
	allowed_decisions = {"", "approved", "rejected"}
	allowed_placements = {
		"", "unassigned", "causal_conditions", "contextual_conditions", "intervening_conditions",
		"action_interaction_strategies", "consequences",
	}
	allowed_relationship_types = {"direct", "interpretive", "hypothetical"}
	if category_decisions and not set(category_decisions).issubset(allowed_decisions):
		raise HTTPException(status_code=422, detail="وضعیت بازبینی مقوله معتبر نیست.")
	if relationship_decisions and not set(relationship_decisions).issubset(allowed_decisions):
		raise HTTPException(status_code=422, detail="وضعیت بازبینی رابطه معتبر نیست.")
	if paradigm_placements and not set(paradigm_placements).issubset(allowed_placements):
		raise HTTPException(status_code=422, detail="جایگاه پارادایمی مقوله معتبر نیست.")
	if relationship_types and not set(relationship_types).issubset(allowed_relationship_types):
		raise HTTPException(status_code=422, detail="نوع رابطه معتبر نیست.")
	if phenomenon_decision not in {"", "approved", "rejected"}:
		raise HTTPException(status_code=422, detail="وضعیت پدیده محوری معتبر نیست.")
	updated_edits = dict(project.get("researcher_edits") or {})
	previous_edit = updated_edits.get("interview_analysis", {})
	previous_categories = previous_edit.get("categories", {})
	previous_relationships = previous_edit.get("relationships", {})
	previous_placements = previous_edit.get("paradigm_placements", {})
	history = list(previous_edit.get("change_history", []))
	now = datetime.now(timezone.utc).isoformat()

	def record_change(record_type: str, record_id: str, field: str, old_value, new_value, reason: str):
		if old_value == new_value:
			return
		history.append({
			"record_type": record_type,
			"record_id": record_id,
			"field": field,
			"old_value": old_value,
			"new_value": new_value,
			"reason": reason.strip(),
			"changed_at": now,
		})

	categories_edit = {}
	placements_edit = {}
	changed_category_ids = set()
	for index, (category_id, name, definition) in enumerate(
		zip(category_ids, category_names, category_definitions)
	):
		old = previous_categories.get(category_id, {})
		original_category = next(item for item in analysis["categories"] if item["category_id"] == category_id)
		decision = category_decisions[index] if category_decisions else old.get("decision", "")
		reason = category_decision_reasons[index].strip() if category_decision_reasons else old.get("decision_reason", "")
		placement = paradigm_placements[index] if paradigm_placements else previous_placements.get(category_id, {}).get("component_key", "")
		placement_reason = placement_reasons[index].strip() if placement_reasons else previous_placements.get(category_id, {}).get("reason", "")
		name, definition = name.strip(), definition.strip()
		previous_name = old.get("category_name", original_category["category_name"])
		previous_definition = old.get("definition", original_category["definition"])
		previous_placement = previous_placements.get(category_id, {})
		category_content_changed = name != previous_name or definition != previous_definition
		if category_content_changed and old.get("decision") in {"approved", "rejected"}:
			decision = ""
			reason = "محتوای مقوله اصلاح شد؛ تصمیم قبلی برای جلوگیری از تأیید خودکار بازنشانی شد."
		if decision in {"approved", "rejected"} and decision != old.get("decision", "") and not reason:
			raise HTTPException(status_code=422, detail="برای تصمیم مقوله، یادداشت پژوهشگر الزامی است.")
		if (
			placement != previous_placement.get("component_key", "")
			and not placement_reason
		):
			raise HTTPException(status_code=422, detail="برای تغییر جایگاه پارادایمی، دلیل پژوهشگر الزامی است.")
		if (
			category_content_changed
			or placement != previous_placement.get("component_key", "")
		):
			changed_category_ids.add(category_id)
		record_change("category", category_id, "category_name", previous_name, name, reason)
		record_change("category", category_id, "definition", previous_definition, definition, reason)
		record_change("category", category_id, "decision", old.get("decision", ""), decision, reason)
		decision_history = list(old.get("decision_history") or [])
		if category_content_changed and old.get("decision") in {"approved", "rejected"}:
			decision_history.append({
				"status": "needs_reverification",
				"note": reason,
				"decided_at": now,
			})
		elif decision != old.get("decision", "") and decision:
			decision_history.append({
				"status": (
					"corrected"
					if decision == "approved"
					and (name != original_category["category_name"] or definition != original_category["definition"])
					else "approved" if decision == "approved" else "rejected"
				),
				"note": reason,
				"decided_at": now,
			})
		categories_edit[category_id] = {
			**old,
			"category_name": name,
			"definition": definition,
			"decision": decision,
			"decision_reason": reason,
			"review_status": (
				decision_history[-1]["status"] if decision_history else old.get("review_status", "")
			),
			"decision_history": decision_history,
		}
		record_change("paradigm_placement", category_id, "component_key", previous_placement.get("component_key", ""), placement, placement_reason)
		if placement:
			placements_edit[category_id] = {
				"component_key": placement,
				"reason": placement_reason,
			}

	relationships_edit = {}
	for index, (relationship_id, description) in enumerate(zip(relationship_ids, relationship_descriptions)):
		old = previous_relationships.get(relationship_id, {})
		original = next(item for item in analysis["relationships"] if item["relationship_id"] == relationship_id)
		description = description.strip()
		relation_type = relationship_types[index] if relationship_types else old.get("relationship_type", original.get("relationship_type", "interpretive"))
		decision = relationship_decisions[index] if relationship_decisions else old.get("decision", "")
		reason = relationship_decision_reasons[index].strip() if relationship_decision_reasons else old.get("decision_reason", "")
		relationship_content_changed = (
			description != old.get("description", original.get("description", ""))
			or relation_type != old.get("relationship_type", original.get("relationship_type", "interpretive"))
		)
		dependencies_changed = bool(
			{original.get("source_category_id"), original.get("target_category_id")}
			& changed_category_ids
		)
		decision_reset = (
			(dependencies_changed or relationship_content_changed)
			and old.get("decision") in {"approved", "rejected"}
		)
		if decision_reset:
			decision = ""
			reason = (
				"یکی از مقوله‌های وابسته تغییر کرده است؛ بازبینی مجدد رابطه لازم است."
				if dependencies_changed
				else "شرح یا نوع رابطه اصلاح شد؛ تصمیم قبلی برای جلوگیری از تأیید خودکار بازنشانی شد."
			)
			record_change("relationship", relationship_id, "decision", old.get("decision", ""), "", reason)
		if decision in {"approved", "rejected"} and decision != old.get("decision", "") and not reason:
			raise HTTPException(status_code=422, detail="برای تصمیم رابطه، یادداشت پژوهشگر الزامی است.")
		decision_history = list(old.get("decision_history") or [])
		if decision_reset:
			decision_history.append({
				"status": "needs_reverification",
				"note": reason,
				"decided_at": now,
			})
		record_change("relationship", relationship_id, "description", old.get("description", original.get("description", "")), description, reason)
		record_change("relationship", relationship_id, "relationship_type", old.get("relationship_type", original.get("relationship_type", "")), relation_type, reason)
		if not decision_reset:
			record_change("relationship", relationship_id, "decision", old.get("decision", ""), decision, reason)
			if decision != old.get("decision", "") and decision:
				decision_history.append({
					"status": (
						"corrected"
						if decision == "approved"
						and (
							description != original.get("description", "")
							or relation_type != original.get("relationship_type", "")
						)
						else "approved" if decision == "approved" else "rejected"
					),
					"note": reason,
					"decided_at": now,
				})
		relationships_edit[relationship_id] = {
			**old,
			"description": description,
			"relationship_type": relation_type,
			"decision": decision,
			"decision_reason": reason,
			"review_status": (
				"needs_reverification" if decision_reset
				else decision_history[-1]["status"] if decision_history
				else old.get("review_status", "")
			),
			"decision_history": decision_history,
		}

	old_phenomenon = previous_edit.get("central_phenomenon", {})
	phenomenon = analysis.get("central_phenomenon") or {}
	phenomenon_decision = phenomenon_decision or old_phenomenon.get("decision", "")
	phenomenon_title = central_phenomenon_title.strip()
	phenomenon_definition = central_phenomenon_definition.strip()
	previous_phenomenon_title = old_phenomenon.get("title", phenomenon.get("title", ""))
	previous_phenomenon_definition = old_phenomenon.get("definition", phenomenon.get("definition", ""))
	phenomenon_content_changed = (
		phenomenon_title != previous_phenomenon_title
		or phenomenon_definition != previous_phenomenon_definition
	)
	phenomenon_decision_reset = (
		(changed_category_ids or phenomenon_content_changed)
		and old_phenomenon.get("decision") in {"approved", "rejected"}
	)
	phenomenon_reason = phenomenon_decision_reason.strip() or old_phenomenon.get("decision_reason", "")
	if phenomenon_decision_reset:
		phenomenon_decision = ""
		old_phenomenon = {
			**old_phenomenon,
			"decision_reason": (
				"یکی از مقوله‌های وابسته تغییر کرده است؛ بازبینی مجدد پدیده لازم است."
				if changed_category_ids
				else "عنوان یا تعریف پدیده اصلاح شد؛ تصمیم قبلی برای جلوگیری از تأیید خودکار بازنشانی شد."
			),
			"review_status": "needs_reverification",
		}
		phenomenon_reason = old_phenomenon["decision_reason"]
	if (
		phenomenon_decision in {"approved", "rejected"}
		and phenomenon_decision != old_phenomenon.get("decision", "")
		and not phenomenon_reason
	):
		raise HTTPException(status_code=422, detail="برای تصمیم پدیده محوری، یادداشت پژوهشگر الزامی است.")
	phenomenon_history = list(old_phenomenon.get("decision_history") or [])
	if phenomenon_decision_reset:
		phenomenon_history.append({
			"status": "needs_reverification",
			"note": phenomenon_reason,
			"decided_at": now,
		})
	elif phenomenon_decision != old_phenomenon.get("decision", "") and phenomenon_decision:
		phenomenon_history.append({
			"status": (
				"corrected"
				if phenomenon_decision == "approved"
				and (
					phenomenon_title != phenomenon.get("title", "")
					or phenomenon_definition != phenomenon.get("definition", "")
				)
				else "approved" if phenomenon_decision == "approved" else "rejected"
			),
			"note": phenomenon_reason,
			"decided_at": now,
		})
	if phenomenon_decision == "approved" and not phenomenon:
		raise HTTPException(status_code=422, detail="پدیده محوری در تحلیل وجود ندارد و قابل تأیید نیست.")
	record_change("central_phenomenon", "central_phenomenon", "title", previous_phenomenon_title, phenomenon_title, phenomenon_reason)
	record_change("central_phenomenon", "central_phenomenon", "definition", previous_phenomenon_definition, phenomenon_definition, phenomenon_reason)
	record_change("central_phenomenon", "central_phenomenon", "decision", old_phenomenon.get("decision", ""), phenomenon_decision, phenomenon_reason)
	record_change("interview_analysis", project_id, "researcher_note", previous_edit.get("researcher_note", ""), researcher_note.strip(), researcher_note)
	record_change("interview_analysis", project_id, "confirmed", previous_edit.get("confirmed", False), confirmed, researcher_note)
	if confirmed:
		candidate_edit = {
			**previous_edit,
			"categories": categories_edit,
			"paradigm_placements": placements_edit,
			"relationships": relationships_edit,
			"central_phenomenon": {"decision": phenomenon_decision},
		}
		candidate_edits = dict(updated_edits)
		candidate_edits["interview_analysis"] = candidate_edit
		candidate_project = {**project, "researcher_edits": candidate_edits}
		if not _can_finalize_interview_analysis(candidate_project, analysis, candidate_edit):
			raise HTTPException(
				status_code=422,
				detail="برای تأیید نهایی، پدیده، مقوله‌ها، جایگاه‌های پارادایمی، روابط و موارد مهم کنترل کیفیت را تعیین تکلیف کنید.",
			)

	updated_edits["interview_analysis"] = {
		**previous_edit,
		"categories": categories_edit,
		"paradigm_placements": placements_edit,
		"relationships": relationships_edit,
		"central_phenomenon": {
			"title": phenomenon_title,
			"definition": phenomenon_definition,
			"decision": phenomenon_decision,
			"decision_reason": phenomenon_reason,
			"review_status": (
				"needs_reverification"
				if phenomenon_decision_reset
				else phenomenon_history[-1]["status"] if phenomenon_history
				else old_phenomenon.get("review_status", "")
			),
			"decision_history": phenomenon_history,
		},
		"researcher_note": researcher_note.strip(),
		"confirmed": confirmed,
		"change_history": history,
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
		project = _finalized_v2_project_or_redirect(project_id)
		if isinstance(project, RedirectResponse):
			return project
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
	project = _finalized_v2_project_or_redirect(project_id)
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
	project = _finalized_v2_project_or_redirect(project_id)
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
