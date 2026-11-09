"""Prompt generation and AI result import routes."""

import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import urlencode

from app.core.database import get_project, update_project
from app.services.docx_parser import build_quality_report
from app.services.analysis_service import parse_analysis_json, prepare_prompt

router = APIRouter(prefix="/analysis", tags=["analysis"])
templates = Jinja2Templates(directory="app/templates")


def _project_or_404(project_id: str):
	project = get_project(project_id)
	if project is None:
		raise HTTPException(status_code=404, detail="پروژه پیدا نشد.")
	return project


@router.get("/{project_id}")
def analysis_page(request: Request, project_id: str, error: str = ""):
	project = _project_or_404(project_id)
	try:
		prompt = prepare_prompt(project)
	except ValueError:
		prompt = ""
	quality_report = build_quality_report(
		project["paragraphs"], project["concepts"], project.get("processing_issues", [])
	)
	return templates.TemplateResponse(
		request=request,
		name="analysis.html",
		context={"project": project, "prompt": prompt, "error": error, "quality_report": quality_report},
	)


@router.get("/{project_id}/prompt.txt", response_class=PlainTextResponse)
def download_prompt(project_id: str):
	project = _project_or_404(project_id)
	try:
		prompt = prepare_prompt(project)
	except ValueError as error:
		raise HTTPException(status_code=409, detail=str(error)) from error
	return PlainTextResponse(
		prompt,
		headers={"Content-Disposition": f'attachment; filename="hooshyar-prompt-{project_id[:8]}.txt"'},
	)


@router.post("/{project_id}/import")
async def import_analysis(
	project_id: str,
	analysis_file: UploadFile | None = File(None),
	analysis_json: str = Form(""),
):
	project = _project_or_404(project_id)
	raw_json = analysis_json
	if analysis_file and analysis_file.filename:
		content = await analysis_file.read(5 * 1024 * 1024 + 1)
		if len(content) > 5 * 1024 * 1024:
			raise HTTPException(status_code=413, detail="حجم JSON باید کمتر از ۵ مگابایت باشد.")
		raw_json = content.decode("utf-8-sig")
	try:
		raw_result = json.loads(raw_json)
		result = parse_analysis_json(raw_json, project)
	except (ValueError, UnicodeDecodeError) as error:
		return RedirectResponse(
			f"/analysis/{project_id}?{urlencode({'error': str(error)[:300]})}", status_code=303
		)
	update_project(project_id, analysis=result, raw_analysis=raw_result)
	if result.get("schema_version") == "2.0":
		return RedirectResponse(f"/results/{project_id}/concept-review", status_code=303)
	return RedirectResponse(f"/results/{project_id}", status_code=303)
