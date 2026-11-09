"""Build downloadable reports from validated analysis output."""

from io import BytesIO
from typing import Any

from docx import Document
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill


def create_excel(project: dict[str, Any]) -> bytes:
	analysis = _require_analysis(project)
	workbook = Workbook()
	concepts_sheet = workbook.active
	concepts_sheet.title = "بررسی مفاهیم"
	_write_sheet(
		concepts_sheet,
		["شناسه", "پاراگراف", "مفهوم اولیه", "مفهوم پیشنهادی", "کد اولیه", "کد پیشنهادی", "ارزیابی کد", "دلیل", "شاهد و تفسیر"],
		[
			[
				review.get("concept_id", ""), review.get("paragraph_id", ""), review.get("original_concept", ""),
				review.get("suggested_concept", ""), review.get("original_code", ""), review.get("suggested_code", ""),
				review.get("code_evaluation", ""), review.get("code_reason", ""),
				" | ".join(
					f"{item.get('paragraph_id', '')}: {item.get('exact_quote', '')} — {item.get('interpretation', '')}"
					for item in review.get("evidence", [])
				),
			]
			for review in analysis.get("concept_reviews", [])
		],
	)
	categories_sheet = workbook.create_sheet("مقوله‌ها")
	_write_sheet(
		categories_sheet,
		["شناسه مقوله", "عنوان", "تعریف", "مفاهیم پشتیبان", "دلیل"],
		[[item.get("category_id", ""), item.get("category_name", ""), item.get("definition", ""), ", ".join(item.get("concept_ids", [])), item.get("category_reason", "")] for item in analysis.get("categories", [])],
	)
	relationships_sheet = workbook.create_sheet("روابط")
	_write_sheet(
		relationships_sheet,
		["شناسه رابطه", "مبدأ", "مقصد", "نوع", "شرح", "مفاهیم پشتیبان", "نیازمند بازبینی"],
		[[item.get("relationship_id", ""), item.get("source_category_id", ""), item.get("target_category_id", ""), item.get("relationship_type", ""), item.get("description", ""), ", ".join(item.get("supporting_concept_ids", [])), item.get("needs_researcher_review", "")] for item in analysis.get("relationships", [])],
	)
	source_sheet = workbook.create_sheet("متن مصاحبه")
	_write_sheet(source_sheet, ["شناسه پاراگراف", "متن"], [[item["paragraph_id"], item["text"]] for item in project.get("paragraphs", [])])
	output = BytesIO()
	workbook.save(output)
	return output.getvalue()


def create_word_report(project: dict[str, Any]) -> bytes:
	analysis = _require_analysis(project)
	document = Document()
	document.add_heading(project.get("title", "گزارش تحلیل"), 0)
	if project.get("topic"):
		document.add_paragraph(f"موضوع پژوهش: {project['topic']}")
	phenomenon = analysis.get("central_phenomenon") or {}
	document.add_heading("پدیده محوری", level=1)
	document.add_heading(phenomenon.get("title") or "تعیین نشده", level=2)
	document.add_paragraph(phenomenon.get("definition") or "داده کافی برای تعریف ارائه نشده است.")
	document.add_paragraph(phenomenon.get("selection_reason", ""))
	for evidence in phenomenon.get("evidence", []):
		document.add_paragraph(
			f"{evidence.get('paragraph_id', '')}: «{evidence.get('exact_quote', '')}» | "
			f"{evidence.get('interpretation', '')}"
		)
	document.add_heading("بررسی مفاهیم", level=1)
	for review in analysis.get("concept_reviews", []):
		document.add_heading(f"{review.get('concept_id', '')} | {review.get('original_concept', '')}", level=2)
		document.add_paragraph(f"کد اولیه: {review.get('original_code', '')} | کد پیشنهادی: {review.get('suggested_code', '')}")
		document.add_paragraph(review.get("code_reason", ""))
		for evidence in review.get("evidence", []):
			document.add_paragraph(
				f"{evidence.get('paragraph_id', '')}: «{evidence.get('exact_quote', '')}» | "
				f"{evidence.get('interpretation', '')}"
			)
	document.add_heading("مقوله‌ها", level=1)
	for category in analysis.get("categories", []):
		document.add_heading(f"{category.get('category_id', '')} | {category.get('category_name', '')}", level=2)
		document.add_paragraph(category.get("definition", ""))
		document.add_paragraph(f"مفاهیم پشتیبان: {', '.join(category.get('concept_ids', []))}")
		for evidence in category.get("evidence", []):
			document.add_paragraph(
				f"{evidence.get('paragraph_id', '')}: «{evidence.get('exact_quote', '')}» | "
				f"{evidence.get('interpretation', '')}"
			)
	document.add_heading("مدل پارادایمی", level=1)
	for key, label in (
		("causal_conditions", "شرایط علّی"), ("contextual_conditions", "شرایط زمینه‌ای"),
		("intervening_conditions", "شرایط مداخله‌گر"), ("action_interaction_strategies", "راهبردهای کنش و تعامل"),
		("consequences", "پیامدها"),
	):
		document.add_heading(label, level=2)
		for item in analysis.get("paradigm_model", {}).get(key, []):
			if isinstance(item, dict):
				document.add_paragraph(item.get("title") or item.get("category_name") or item.get("definition", ""), style="List Bullet")
	output = BytesIO()
	document.save(output)
	return output.getvalue()


def _write_sheet(sheet, headers: list[str], rows: list[list[Any]]) -> None:
	sheet.sheet_view.rightToLeft = True
	sheet.freeze_panes = "A2"
	sheet.append(headers)
	for cell in sheet[1]:
		cell.font = Font(bold=True, color="FFFFFF")
		cell.fill = PatternFill("solid", fgColor="38554D")
	for row in rows:
		sheet.append(row)
	for column in sheet.columns:
		width = min(max(max((len(str(cell.value or "")) for cell in column), default=12) + 2, 12), 55)
		sheet.column_dimensions[column[0].column_letter].width = width


def _require_analysis(project: dict[str, Any]) -> dict[str, Any]:
	analysis = project.get("analysis")
	if not analysis:
		raise ValueError("ابتدا JSON تحلیل را وارد کنید.")
	return analysis
