"""Download analysis JSON, spreadsheets, and reports."""

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.core.database import get_project
from app.services.coding_table_service import EXPORT_FORMATS, build_interview_coding_table
from app.services.export_service import create_excel, create_markdown_report, create_word_report

router = APIRouter(prefix="/exports", tags=["exports"])


def _project_or_404(project_id: str):
	project = get_project(project_id)
	if project is None:
		raise HTTPException(status_code=404, detail="پروژه پیدا نشد.")
	if not project.get("analysis"):
		raise HTTPException(status_code=409, detail="ابتدا خروجی هر دو مرحله تحلیل را وارد کنید.")
	return project


@router.get("/{project_id}/analysis.json")
def export_json(project_id: str):
	project = _project_or_404(project_id)
	content = json.dumps(project["analysis"], ensure_ascii=False, indent=2)
	return Response(content, media_type="application/json; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="analysis-{project_id[:8]}.json"'})


@router.get("/{project_id}/concepts.xlsx")
def export_excel(project_id: str):
	project = _project_or_404(project_id)
	return Response(create_excel(project), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="concepts-{project_id[:8]}.xlsx"'})


@router.get("/{project_id}/report.docx")
def export_word(project_id: str):
	project = _project_or_404(project_id)
	return Response(create_word_report(project), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition": f'attachment; filename="report-{project_id[:8]}.docx"'})


@router.get("/{project_id}/coding-table.{fmt}")
def export_coding_table(project_id: str, fmt: str):
	project = _project_or_404(project_id)
	if project["analysis"].get("schema_version") != "2.0" or fmt not in EXPORT_FORMATS:
		raise HTTPException(status_code=404, detail="جدول کدگذاری برای این پروژه یا فرمت وجود ندارد.")
	build, media_type = EXPORT_FORMATS[fmt]
	return Response(
		build(build_interview_coding_table(project)),
		media_type=media_type,
		headers={"Content-Disposition": f'attachment; filename="coding-table-{project_id[:8]}.{fmt}"'},
	)


@router.get("/{project_id}/analysis.md")
def export_markdown(project_id: str):
	project = _project_or_404(project_id)
	return Response(
		create_markdown_report(project),
		media_type="text/markdown; charset=utf-8",
		headers={"Content-Disposition": f'attachment; filename="analysis-{project_id[:8]}.md"'},
	)
