"""Analysis result, category, paradigm, and relationship views."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from graphviz.backend import ExecutableNotFound
from fastapi.templating import Jinja2Templates

from app.core.database import get_project, update_project
from app.services.analysis_service import prepare_analysis_results
from app.services.paradigm_service import prepare_paradigm_diagram, render_paradigm_diagram

router = APIRouter(prefix="/results", tags=["results"])
templates = Jinja2Templates(directory="app/templates")


def _project_or_404(project_id: str):
	project = get_project(project_id)
	if project is None:
		raise HTTPException(status_code=404, detail="پروژه پیدا نشد.")
	if not project.get("analysis"):
		raise HTTPException(status_code=409, detail="هنوز تحلیل JSON برای این پروژه ثبت نشده است.")
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


@router.get("/{project_id}/concept-review")
def concept_review_page(request: Request, project_id: str):
	project = _v2_project_or_409(project_id)
	if isinstance(project, RedirectResponse):
		return project
	stored_edits = project.get("researcher_edits") or {}
	review_edits = stored_edits.get("concept_reviews", {})
	reviews = [
		{"review": review, "edit": review_edits.get(review["concept_id"], {})}
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
	return templates.TemplateResponse(
		request=request,
		name="interview_analysis_review.html",
		context={
			"project": project,
			"analysis": analysis,
			"categories": categories,
			"relationships": relationships,
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
