"""Research project and interview routes."""

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.database import create_project, delete_project, get_project, list_projects, update_project
from app.services.docx_parser import build_quality_report, parse_docx

router = APIRouter(prefix="/projects", tags=["projects"])
templates = Jinja2Templates(directory="app/templates")


def _project_or_404(project_id: str):
	project = get_project(project_id)
	if project is None:
		raise HTTPException(status_code=404, detail="پروژه پیدا نشد.")
	return project


@router.get("")
def projects_page(request: Request):
	return templates.TemplateResponse(
		request=request,
		name="projects.html",
		context={"projects": list_projects()},
	)


@router.post("")
def create_project_route(title: str = Form(...), topic: str = Form("")):
	if not title.strip():
		raise HTTPException(status_code=422, detail="عنوان پروژه الزامی است.")
	project = create_project(title, topic)
	return RedirectResponse(f"/projects/{project['project_id']}", status_code=303)


@router.get("/{project_id}")
def project_detail(request: Request, project_id: str):
	project = _project_or_404(project_id)
	quality_report = build_quality_report(
		project["paragraphs"], project["concepts"], project.get("processing_issues", [])
	)
	return templates.TemplateResponse(
		request=request,
		name="analysis.html",
		context={"project": project, "quality_report": quality_report},
	)


@router.post("/{project_id}/details")
def update_project_details(project_id: str, title: str = Form(...), topic: str = Form("")):
	_project_or_404(project_id)
	if not title.strip():
		raise HTTPException(status_code=422, detail="عنوان پژوهش الزامی است.")
	update_project(project_id, title=title.strip(), topic=topic.strip())
	return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/{project_id}/interview")
async def upload_interview(project_id: str, interview: UploadFile = File(...)):
	project = _project_or_404(project_id)
	if not interview.filename or not interview.filename.lower().endswith(".docx"):
		raise HTTPException(status_code=400, detail="فقط فایل Word با پسوند DOCX پذیرفته می‌شود.")
	content = await interview.read(20 * 1024 * 1024 + 1)
	if len(content) > 20 * 1024 * 1024:
		raise HTTPException(status_code=413, detail="حجم فایل باید کمتر از ۲۰ مگابایت باشد.")
	try:
		parsed = parse_docx(content)
	except ValueError as error:
		raise HTTPException(status_code=400, detail=str(error)) from error
	updated = update_project(
		project_id,
		paragraphs=parsed["paragraphs"],
		concepts=parsed["concepts"],
		processing_issues=parsed["processing_issues"],
		analysis=None,
		source_filename=interview.filename,
	)
	return RedirectResponse(f"/projects/{updated['project_id']}", status_code=303)


@router.post("/{project_id}/concepts")
def save_concepts(
	project_id: str,
	paragraph_ids: list[str] = Form([]),
	concept_ids: list[str] = Form([]),
	concepts: list[str] = Form([]),
	codes: list[str] = Form([]),
):
	project = _project_or_404(project_id)
	if len({len(paragraph_ids), len(concept_ids), len(concepts), len(codes)}) != 1:
		raise HTTPException(status_code=422, detail="اطلاعات هر ردیف مفهوم باید کامل ارسال شود.")
	valid_paragraph_ids = {paragraph["paragraph_id"] for paragraph in project["paragraphs"]}
	saved = []
	reserved_ids = {concept_id.strip() for concept_id in concept_ids if concept_id.strip()}
	used_ids: set[str] = set()
	next_id = 1
	for paragraph_id, supplied_id, concept, code in zip(paragraph_ids, concept_ids, concepts, codes):
		concept_id = supplied_id.strip()
		if not concept_id and not concept.strip() and not code.strip():
			continue
		if paragraph_id and paragraph_id not in valid_paragraph_ids:
			raise HTTPException(status_code=422, detail=f"شناسه پاراگراف نامعتبر است: {paragraph_id}")
		if not concept_id:
			while f"C{next_id:03d}" in reserved_ids or f"C{next_id:03d}" in used_ids:
				next_id += 1
			concept_id = f"C{next_id:03d}"
			next_id += 1
		if concept_id in used_ids:
			raise HTTPException(status_code=422, detail="شناسه مفاهیم باید یکتا باشد.")
		used_ids.add(concept_id)
		saved.append(
			{
				"concept_id": concept_id,
				"paragraph_ids": [paragraph_id] if paragraph_id else [],
				"original_concept": concept.strip(),
				"original_code": code.strip(),
			}
		)
	update_project(project_id, concepts=saved, analysis=None)
	return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/{project_id}/delete")
def remove_project(project_id: str):
	if not delete_project(project_id):
		raise HTTPException(status_code=404, detail="پروژه پیدا نشد.")
	return RedirectResponse("/projects", status_code=303)
