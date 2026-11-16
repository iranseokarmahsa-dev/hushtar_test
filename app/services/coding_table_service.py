"""Comprehensive coding table (paradigm dimension -> category -> open concepts) and its exports."""

import csv
from io import BytesIO, StringIO
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

TABLE_TITLE = "جدول جامع کدگذاری (مفاهیم، مقولات و تم‌ها)"
HEADERS = ["بعد پارادایمی / تم اصلی", "مقوله محوری", "مفاهیم معادل (کدهای باز)", "کد ارجاع / شاهد"]
UNPLACED_LABEL = "بدون جایگاه پارادایمی"
DOCX_FONT = "B Nazanin"


def concept_final_title(concept: dict, review: dict, edit: dict) -> str:
	"""Researcher-corrected concept title from stage one, falling back to the original."""
	if edit.get("final_concept"):
		return edit["final_concept"]
	if edit.get("decision") == "approve_suggestion" and review.get("suggested_concept"):
		return review["suggested_concept"]
	return (
		review.get("original_concept")
		or concept.get("original_concept")
		or concept.get("concept_id", "")
	)


def _concept_lookup(snapshot: dict) -> dict[str, dict]:
	analysis = snapshot.get("analysis") or {}
	reviews = {item.get("concept_id"): item for item in analysis.get("concept_reviews", [])}
	edits = (snapshot.get("researcher_edits") or {}).get("concept_reviews", {})
	concepts = {item.get("concept_id"): item for item in snapshot.get("concepts", [])}
	lookup = {}
	for concept_id in dict.fromkeys([*concepts, *reviews]):
		if not concept_id:
			continue
		concept = concepts.get(concept_id, {})
		review = reviews.get(concept_id, {})
		paragraph_ids = list(dict.fromkeys(
			[*concept.get("paragraph_ids", []), *([review["paragraph_id"]] if review.get("paragraph_id") else [])]
		))
		lookup[concept_id] = {
			"title": concept_final_title(concept, review, edits.get(concept_id, {})),
			"paragraph_ids": paragraph_ids,
		}
	return lookup


def _flatten(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""groups: [{dimension, categories: [{category, concepts: [{title, ref}]}]}] -> rows with spans."""
	rows: list[dict[str, Any]] = []
	for group in groups:
		group_start = len(rows)
		for category in group["categories"]:
			category_start = len(rows)
			for concept in category["concepts"] or [{"title": "—", "ref": ""}]:
				rows.append({
					"dimension": group["dimension"], "dimension_span": 0,
					"category": category["category"], "category_span": 0,
					"concept": concept["title"], "ref": concept["ref"],
				})
			rows[category_start]["category_span"] = len(rows) - category_start
		if len(rows) > group_start:
			rows[group_start]["dimension_span"] = len(rows) - group_start
	return rows


def build_interview_coding_table(project: dict) -> dict[str, Any]:
	from app.services.paradigm_service import prepare_paradigm_diagram

	analysis = project["analysis"]["interview_analysis"]
	edit = (project.get("researcher_edits") or {}).get("interview_analysis", {})
	category_edits = edit.get("categories", {})
	categories = {item["category_id"]: item for item in analysis.get("categories", [])}
	concepts = _concept_lookup(project)
	ordered_nodes = [
		node for node in prepare_paradigm_diagram(project)["nodes"]
		if node["model_key"] != "categories"
	]
	nodes = sorted(ordered_nodes, key=lambda node: node["model_key"] == "central_phenomenon")
	placed: dict[str, str] = {}
	for node in nodes:
		for category_id in node["category_ids"]:
			placed.setdefault(category_id, node["label"])
	order = list(dict.fromkeys([node["label"] for node in ordered_nodes] + [UNPLACED_LABEL]))
	by_dimension: dict[str, list[dict]] = {label: [] for label in order}
	for category_id, category in categories.items():
		if category_edits.get(category_id, {}).get("decision") == "rejected":
			continue
		items = []
		for concept_id in category.get("concept_ids", []):
			info = concepts.get(concept_id, {"title": concept_id, "paragraph_ids": []})
			ref = " - ".join([concept_id, *info["paragraph_ids"][:1]])
			items.append({"title": info["title"], "ref": ref})
		by_dimension[placed.get(category_id, UNPLACED_LABEL)].append({
			"category": category_edits.get(category_id, {}).get("category_name") or category["category_name"],
			"concepts": items,
		})
	groups = [
		{"dimension": label, "categories": by_dimension[label]}
		for label in order if by_dimension[label]
	]
	return {"title": TABLE_TITLE, "headers": HEADERS, "rows": _flatten(groups), "interviews": []}


def build_synthesis_coding_table(run_id: str, model: dict) -> dict[str, Any]:
	from app.repositories.synthesis_repository import get_run_sources
	from app.services.synthesis.category_grouping_service import _approved_pool
	from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS

	sources = get_run_sources(run_id)
	number_by_project = {source["source_project_id"]: index for index, source in enumerate(sources, 1)}
	lookup_by_project = {source["source_project_id"]: _concept_lookup(source["snapshot"]) for source in sources}
	try:
		pool = _approved_pool(run_id)[0]
	except ValueError:
		pool = {}
	items_by_id = {
		str(item.get("source_item_id")): item
		for key in (*POOL_COMPONENT_KEYS, "central_phenomenon")
		for item in pool.get("pools", {}).get(key, [])
	}
	nodes_by_id = {node["node_id"]: node for node in model.get("nodes", [])}
	groups = []
	for component in sorted(model.get("components", []), key=lambda item: item.get("display_order", 0)):
		categories = []
		for node_id in component.get("node_ids", []):
			node = nodes_by_id.get(node_id)
			if node is None:
				continue
			seen, concepts = set(), []
			for source_item_id in node.get("source_item_ids", []):
				item = items_by_id.get(str(source_item_id)) or {}
				project_id = str(item.get("source_project_id") or "")
				for concept_id in item.get("source_concept_ids", []):
					if (project_id, concept_id) in seen:
						continue
					seen.add((project_id, concept_id))
					info = lookup_by_project.get(project_id, {}).get(concept_id, {"title": concept_id})
					number = number_by_project.get(project_id)
					concepts.append({
						"title": info["title"],
						"ref": f"{concept_id} - م{number}" if number else concept_id,
					})
			categories.append({"category": node["title"], "concepts": concepts})
		if categories:
			groups.append({"dimension": component["title"], "categories": categories})
	return {
		"title": TABLE_TITLE, "headers": HEADERS, "rows": _flatten(groups),
		"interviews": [
			{"number": number_by_project[source["source_project_id"]], "title": source["source_title"]}
			for source in sources
		],
	}


def _plain_rows(table: dict) -> list[list[str]]:
	return [[row["dimension"], row["category"], row["concept"], row["ref"]] for row in table["rows"]]


def table_to_csv(table: dict) -> bytes:
	buffer = StringIO()
	writer = csv.writer(buffer)
	writer.writerow(table["headers"])
	writer.writerows(_plain_rows(table))
	return buffer.getvalue().encode("utf-8-sig")


def table_to_xlsx(table: dict) -> bytes:
	workbook = Workbook()
	sheet = workbook.active
	sheet.title = "جدول کدگذاری"
	sheet.sheet_view.rightToLeft = True
	thin = Side(style="thin", color="999999")
	border = Border(left=thin, right=thin, top=thin, bottom=thin)
	sheet.append(table["headers"])
	for cell in sheet[1]:
		cell.font = Font(bold=True, color="FFFFFF")
		cell.fill = PatternFill("solid", fgColor="38554D")
		cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
	for row in _plain_rows(table):
		sheet.append(row)
	for index, row in enumerate(table["rows"], start=2):
		for column, span_key in ((1, "dimension_span"), (2, "category_span")):
			if row[span_key] > 1:
				sheet.merge_cells(start_row=index, end_row=index + row[span_key] - 1, start_column=column, end_column=column)
	for row in sheet.iter_rows(min_row=1):
		for cell in row:
			cell.border = border
			if cell.row > 1:
				cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)
	for letter, width in zip("ABCD", (28, 34, 48, 18)):
		sheet.column_dimensions[letter].width = width
	sheet.freeze_panes = "A2"
	output = BytesIO()
	workbook.save(output)
	return output.getvalue()


def _set_rtl_paragraph(paragraph, text: str, *, bold: bool = False, size: int = 11) -> None:
	paragraph.text = ""
	properties = paragraph._p.get_or_add_pPr()
	properties.append(OxmlElement("w:bidi"))
	run = paragraph.add_run(text)
	run.bold = bold
	run.font.size = Pt(size)
	run_properties = run._r.get_or_add_rPr()
	fonts = run_properties.find(qn("w:rFonts"))
	if fonts is None:
		fonts = OxmlElement("w:rFonts")
		run_properties.insert(0, fonts)
	for attribute in ("w:ascii", "w:hAnsi", "w:cs"):
		fonts.set(qn(attribute), DOCX_FONT)
	size_cs = OxmlElement("w:szCs")
	size_cs.set(qn("w:val"), str(size * 2))
	run_properties.find(qn("w:sz")).addnext(size_cs)
	run_properties.append(OxmlElement("w:rtl"))


def _shade(cell, color: str) -> None:
	shading = OxmlElement("w:shd")
	shading.set(qn("w:val"), "clear")
	shading.set(qn("w:fill"), color)
	cell._tc.get_or_add_tcPr().append(shading)


def table_to_docx(table: dict) -> bytes:
	document = Document()
	section = document.sections[0]
	section.orientation = WD_ORIENT.LANDSCAPE
	section.page_width, section.page_height = section.page_height, section.page_width
	title = document.add_paragraph()
	_set_rtl_paragraph(title, table["title"], bold=True, size=14)
	grid = document.add_table(rows=1, cols=4)
	grid.style = "Table Grid"
	grid.alignment = WD_TABLE_ALIGNMENT.CENTER
	grid._tbl.tblPr.find(qn("w:tblStyle")).addnext(OxmlElement("w:bidiVisual"))
	for cell, header in zip(grid.rows[0].cells, table["headers"]):
		_set_rtl_paragraph(cell.paragraphs[0], header, bold=True)
		_shade(cell, "D9E5DF")
	header_properties = grid.rows[0]._tr.get_or_add_trPr()
	header_properties.append(OxmlElement("w:tblHeader"))
	for row in table["rows"]:
		cells = grid.add_row().cells
		for cell, value in zip(cells, (row["dimension"], row["category"], row["concept"], row["ref"])):
			_set_rtl_paragraph(cell.paragraphs[0], value)
	first_body_row = 1
	for offset, row in enumerate(table["rows"]):
		for column, span_key in ((0, "dimension_span"), (1, "category_span")):
			if row[span_key] > 1:
				start = grid.cell(first_body_row + offset, column)
				end = grid.cell(first_body_row + offset + row[span_key] - 1, column)
				value = row["dimension"] if column == 0 else row["category"]
				merged = start.merge(end)
				for extra in merged.paragraphs[1:]:
					extra._p.getparent().remove(extra._p)
				_set_rtl_paragraph(merged.paragraphs[0], value)
	if table["interviews"]:
		legend = document.add_paragraph()
		_set_rtl_paragraph(
			legend,
			"راهنمای مصاحبه‌ها: " + "؛ ".join(f"م{item['number']} = {item['title']}" for item in table["interviews"]),
			size=10,
		)
	output = BytesIO()
	document.save(output)
	return output.getvalue()


EXPORT_FORMATS = {
	"csv": (table_to_csv, "text/csv; charset=utf-8"),
	"xlsx": (table_to_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
	"docx": (table_to_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
}
