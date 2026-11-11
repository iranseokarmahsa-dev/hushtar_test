"""Prompt generation and AI result import routes."""

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import urlencode

from app.core.database import get_project, update_project
from app.repositories.synthesis_repository import mark_synthesis_runs_stale_for_source
from app.services.docx_parser import build_quality_report
from app.services.analysis_service import (
	combine_stage_outputs,
	load_analysis_markdown,
	parse_analysis_markdown,
	parse_stage_markdown,
	prepare_prompts,
	prepare_prompt,
)

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
		prompts = prepare_prompts(project)
	except ValueError as prompt_error:
		prompts = {}
		error = error or str(prompt_error)
	quality_report = build_quality_report(
		project["paragraphs"], project["concepts"], project.get("processing_issues", [])
	)
	return templates.TemplateResponse(
		request=request,
		name="analysis.html",
		context={"project": project, "prompts": prompts, "error": error, "quality_report": quality_report},
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


@router.get("/{project_id}/prompt/{stage}.md", response_class=PlainTextResponse)
def download_stage_prompt(project_id: str, stage: str):
	project = _project_or_404(project_id)
	if stage not in {"stage1", "stage2"}:
		raise HTTPException(status_code=404, detail="مرحله پرامپت پیدا نشد.")
	try:
		prompts = prepare_prompts(project)
	except ValueError as error:
		raise HTTPException(status_code=409, detail=str(error)) from error
	return PlainTextResponse(
		prompts[stage],
		headers={"Content-Disposition": f'attachment; filename="hooshyar-{stage}-{project_id[:8]}.md"'},
	)


@router.post("/{project_id}/import")
async def import_analysis(
	project_id: str,
	analysis_file: UploadFile | None = File(None),
	analysis_markdown: str = Form(""),
):
	project = _project_or_404(project_id)
	raw_markdown = analysis_markdown
	if analysis_file and analysis_file.filename:
		content = await analysis_file.read(5 * 1024 * 1024 + 1)
		if len(content) > 5 * 1024 * 1024:
			raise HTTPException(status_code=413, detail="حجم فایل Markdown باید کمتر از ۵ مگابایت باشد.")
		try:
			raw_markdown = content.decode("utf-8-sig")
		except UnicodeDecodeError as error:
			return RedirectResponse(
				f"/analysis/{project_id}?{urlencode({'error': 'فایل Markdown باید با UTF-8 ذخیره شده باشد.'})}",
				status_code=303,
			)
	try:
		raw_result = load_analysis_markdown(raw_markdown)
		result = parse_analysis_markdown(raw_markdown, project)
	except (ValueError, UnicodeDecodeError) as error:
		return RedirectResponse(
			f"/analysis/{project_id}?{urlencode({'error': str(error)[:300]})}", status_code=303
		)
	update_project(project_id, analysis=result, raw_analysis=raw_result)
	mark_synthesis_runs_stale_for_source(project_id)
	if result.get("schema_version") == "2.0":
		return RedirectResponse(f"/results/{project_id}/concept-review", status_code=303)
	return RedirectResponse(f"/results/{project_id}", status_code=303)


@router.post("/{project_id}/import/{stage}")
async def import_stage(
	project_id: str,
	stage: str,
	analysis_file: UploadFile | None = File(None),
	analysis_markdown: str = Form(""),
):
	project = _project_or_404(project_id)
	if stage not in {"stage1", "stage2"}:
		raise HTTPException(status_code=404, detail="مرحله تحلیل پیدا نشد.")
	raw_markdown = analysis_markdown
	if analysis_file and analysis_file.filename:
		content = await analysis_file.read(5 * 1024 * 1024 + 1)
		if len(content) > 5 * 1024 * 1024:
			raise HTTPException(status_code=413, detail="حجم فایل Markdown باید کمتر از ۵ مگابایت باشد.")
		try:
			raw_markdown = content.decode("utf-8-sig")
		except UnicodeDecodeError as error:
			return RedirectResponse(
				f"/analysis/{project_id}?{urlencode({'error': 'فایل Markdown باید با UTF-8 ذخیره شده باشد.'})}",
				status_code=303,
			)
	try:
		payload = parse_stage_markdown(raw_markdown, project, stage)
	except ValueError as error:
		return RedirectResponse(
			f"/analysis/{project_id}?{urlencode({'error': str(error)[:300]})}", status_code=303
		)

	stage_outputs = dict(project.get("stage_outputs") or {})
	stage_outputs[stage] = {"raw_markdown": raw_markdown, "payload": payload}
	changes: dict[str, object] = {"stage_outputs": stage_outputs}
	if {"stage1", "stage2"}.issubset(stage_outputs):
		try:
			analysis = combine_stage_outputs(
				project, stage_outputs["stage1"]["payload"], stage_outputs["stage2"]["payload"]
			)
		except (KeyError, TypeError, ValueError) as error:
			return RedirectResponse(
				f"/analysis/{project_id}?{urlencode({'error': str(error)[:300]})}", status_code=303
			)
		changes.update(
			analysis=analysis,
			raw_analysis={key: item["payload"] for key, item in stage_outputs.items()},
		)
		updated = update_project(project_id, **changes)
		mark_synthesis_runs_stale_for_source(project_id)
		return RedirectResponse(f"/results/{updated['project_id']}/concept-review", status_code=303)
	update_project(project_id, **changes)
	return RedirectResponse(f"/analysis/{project_id}", status_code=303)
