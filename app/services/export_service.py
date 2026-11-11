"""Build downloadable reports from validated analysis output."""

from io import BytesIO
from typing import Any

from docx import Document
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
import yaml


def create_excel(project: dict[str, Any]) -> bytes:
	analysis = _require_analysis(project)
	interview = analysis.get("interview_analysis", analysis)
	workbook = Workbook()
	concepts_sheet = workbook.active
	concepts_sheet.title = "بررسی مفاهیم"
	_write_sheet(
		concepts_sheet,
		["شناسه", "پاراگراف", "مفهوم اولیه", "مفهوم پیشنهادی", "اعتبار مفهوم", "کد اولیه", "کد پیشنهادی", "ارزیابی کد", "هم‌پوشانی", "دلیل عنوان", "دلیل کد", "شاهد و تفسیر"],
		[
			[
				review.get("concept_id", ""), review.get("paragraph_id", ""), review.get("original_concept", ""),
				review.get("suggested_concept", ""), review.get("concept_validity", ""),
				review.get("original_code", ""), review.get("suggested_code", ""),
				review.get("code_evaluation", ""),
				", ".join(review.get("overlap", {}).get("related_concept_ids", []))
				+ " " + review.get("overlap", {}).get("explanation", ""),
				review.get("concept_reason", ""),
				review.get("code_reason", ""),
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
		["شناسه مقوله", "عنوان", "تعریف", "مفاهیم پشتیبان", "دلیل", "ویژگی‌ها", "ابعاد"],
		[
			[
				item.get("category_id", ""), item.get("category_name", ""), item.get("definition", ""),
				", ".join(item.get("concept_ids", [])), item.get("category_reason", ""),
				yaml.safe_dump(item.get("properties", []), allow_unicode=True, sort_keys=False),
				yaml.safe_dump(item.get("dimensions", []), allow_unicode=True, sort_keys=False),
			]
			for item in interview.get("categories", analysis.get("categories", []))
		],
	)
	subcategories_sheet = workbook.create_sheet("زیرمقوله‌ها")
	_write_sheet(
		subcategories_sheet,
		["شناسه زیرمقوله", "شناسه مقوله والد", "عنوان", "تعریف", "مفاهیم پشتیبان", "شواهد"],
		[
			[
				subcategory.get("subcategory_id", ""), category.get("category_id", ""),
				subcategory.get("subcategory_name", ""), subcategory.get("definition", ""),
				", ".join(subcategory.get("concept_ids", [])), _format_evidence(subcategory.get("evidence", [])),
			]
			for category in interview.get("categories", analysis.get("categories", []))
			for subcategory in category.get("subcategories", [])
		],
	)
	relationships_sheet = workbook.create_sheet("روابط")
	_write_sheet(
		relationships_sheet,
		["شناسه رابطه", "مبدأ", "مقصد", "نوع", "شرح", "مفاهیم پشتیبان", "نیازمند بازبینی"],
		[[item.get("relationship_id", ""), item.get("source_category_id", ""), item.get("target_category_id", ""), item.get("relationship_type", ""), item.get("description", ""), ", ".join(item.get("supporting_concept_ids", [])), item.get("needs_researcher_review", "")] for item in interview.get("relationships", analysis.get("relationships", []))],
	)
	paradigm_sheet = workbook.create_sheet("مدل پارادایمی")
	paradigm_rows = []
	for key, label in (
		("causal_conditions", "شرایط علّی"),
		("central_phenomenon", "پدیده محوری"),
		("contextual_conditions", "شرایط زمینه‌ای"),
		("intervening_conditions", "شرایط مداخله‌گر"),
		("action_interaction_strategies", "راهبردهای کنش و تعامل"),
		("consequences", "پیامدها"),
	):
		items = interview.get("paradigm_model", analysis.get("paradigm_model", {})).get(key)
		if isinstance(items, dict):
			items = [items]
		for item in items or []:
			if isinstance(item, dict):
				paradigm_rows.append([
					label, item.get("category_id", ""), ", ".join(item.get("concept_ids", [])),
					item.get("explanation", ""), _format_evidence(item.get("evidence", [])),
				])
	_write_sheet(paradigm_sheet, ["مؤلفه", "شناسه مقوله", "مفاهیم", "توضیح", "شواهد"], paradigm_rows)
	audit = interview.get("quality_audit", analysis.get("quality_audit", {}))
	audit_sheet = workbook.create_sheet("ممیزی کیفیت")
	audit_rows = [
		[key, yaml.safe_dump(value, allow_unicode=True, sort_keys=False) if isinstance(value, (dict, list)) else value]
		for key, value in audit.items()
	]
	_write_sheet(audit_sheet, ["کنترل", "نتیجه Python"], audit_rows)
	_write_sheet(
		workbook.create_sheet("هشدار شواهد"),
		["مرحله", "نوع رکورد", "شناسه رکورد", "پاراگراف", "نقل‌قول دریافتی", "وضعیت", "شباهت ٪"],
		[
			[
				item.get("stage", ""), item.get("record_type", ""), item.get("record_id", ""),
				item.get("paragraph_id", ""), item.get("exact_quote", ""), item.get("status", ""),
				item.get("similarity", ""),
			]
			for item in audit.get("evidence_warnings", [])
		],
	)
	_write_sheet(
		workbook.create_sheet("خطاهای رکوردی"),
		["مرحله", "نوع رکورد", "شناسه رکورد", "مقوله والد", "دلیل"],
		[
			[
				item.get("stage", ""), item.get("record_type", ""), item.get("record_id", ""),
				item.get("parent_category_id", ""), item.get("reason", ""),
			]
			for item in audit.get("record_errors", [])
		],
	)
	source_sheet = workbook.create_sheet("متن مصاحبه")
	_write_sheet(source_sheet, ["شناسه پاراگراف", "متن"], [[item["paragraph_id"], item["text"]] for item in project.get("paragraphs", [])])
	output = BytesIO()
	workbook.save(output)
	return output.getvalue()


def create_markdown_report(project: dict[str, Any]) -> str:
	"""Export the Python-validated combined contract as a Markdown YAML document."""
	analysis = _require_analysis(project)
	document = yaml.safe_dump(analysis, allow_unicode=True, sort_keys=False, width=1000)
	return f"# {project.get('title', 'گزارش تحلیل')}\n\n```yaml\n{document}```\n"


def create_word_report(project: dict[str, Any]) -> bytes:
	analysis = _require_analysis(project)
	interview = analysis.get("interview_analysis", analysis)
	document = Document()
	document.add_heading(project.get("title", "گزارش تحلیل"), 0)
	if project.get("topic"):
		document.add_paragraph(f"موضوع پژوهش: {project['topic']}")
	phenomenon = interview.get("central_phenomenon") or {}
	if phenomenon.get("identified") is False:
		phenomenon = {}
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
	for category in interview.get("categories", analysis.get("categories", [])):
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
		items = interview.get("paradigm_model", analysis.get("paradigm_model", {})).get(key, [])
		if isinstance(items, dict):
			items = [items]
		for item in items:
			if isinstance(item, dict):
				document.add_paragraph(
					item.get("title") or item.get("category_name") or item.get("explanation")
					or item.get("definition", ""),
					style="List Bullet",
				)
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


def _format_evidence(evidence: list[dict[str, Any]]) -> str:
	return " | ".join(
		f"{item.get('paragraph_id', '')}: {item.get('exact_quote', '')} — {item.get('interpretation', '')}"
		for item in evidence
	)


def _require_analysis(project: dict[str, Any]) -> dict[str, Any]:
	analysis = project.get("analysis")
	if not analysis:
		raise ValueError("ابتدا تحلیل را وارد کنید.")
	return analysis
