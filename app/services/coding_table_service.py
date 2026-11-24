"""Comprehensive coding table (paradigm dimension -> category -> open concepts) and its exports."""

import csv
from io import BytesIO, StringIO
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
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
	from app.services.axial_coding_service import effective_categories

	analysis = project["analysis"]["interview_analysis"]
	edit = (project.get("researcher_edits") or {}).get("interview_analysis", {})
	category_edits = edit.get("categories", {})
	categories = {item["category_id"]: item for item in effective_categories(project)}
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
	axial_state = (project.get("researcher_edits") or {}).get("axial_coding") or {}
	placement_labels = {
		"causal_conditions": "شرایط علّی",
		"central_phenomenon": "مقوله یا پدیده محوری",
		"contextual_conditions": "شرایط زمینه‌ای",
		"intervening_conditions": "شرایط مداخله‌گر",
		"action_interaction_strategies": "راهبردهای کنش و تعامل",
		"consequences": "پیامدها",
		"unassigned": UNPLACED_LABEL,
	}
	for category_id, records in (axial_state.get("placement_decisions") or {}).items():
		for component_key, decision in records.items():
			if decision.get("researcher_status") in {"تأیید", "اصلاح و تأیید"}:
				placed.setdefault(category_id, placement_labels.get(component_key, UNPLACED_LABEL))
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


def build_final_model_hierarchy_rows(run_id: str, model: dict[str, Any]) -> list[dict[str, str]]:
	from app.services.synthesis.category_integration_service import (
		COMPONENT_LABELS,
		get_registered_category_integrations,
	)
	from app.services.synthesis.category_grouping_service import _approved_pool

	pool, _, _ = _approved_pool(run_id)
	source_items = {
		str(item["source_item_id"]): item
		for items in pool.get("pools", {}).values()
		for item in items
		if isinstance(item, dict) and item.get("source_item_id")
	}
	category_libraries = get_registered_category_integrations(run_id)
	rows: list[dict[str, str]] = []
	for node in model.get("nodes", []):
		component_key = str(node.get("component_key") or "")
		library = category_libraries.get(component_key, {})
		categories_by_id = {
			str(category["category_id"]): category
			for category in library.get("integrated_categories", [])
		}
		for unit_id in node.get("category_unit_ids", []):
			prefix = f"unit_{component_key}_"
			category_id = str(unit_id).removeprefix(prefix)
			category = categories_by_id.get(category_id)
			if category is None:
				continue
			member_ids = set(str(value) for value in category.get("source_item_ids", []))
			for source_item_id in sorted(member_ids.intersection(
				str(value) for value in node.get("source_item_ids", [])
			)):
				item = source_items.get(source_item_id, {})
				concepts = item.get("source_concepts") or []
				concept_rows = concepts or [{}]
				for concept in concept_rows:
					evidence_refs = item.get("evidence_refs") or []
					for evidence in evidence_refs or [{}]:
						rows.append({
							"component": COMPONENT_LABELS.get(component_key, component_key),
							"theme": str(node.get("title") or ""),
							"integrated_category": str(category.get("title") or ""),
							"initial_category": str(
								item.get("researcher_approved_category_name")
								or item.get("original_category_name")
								or ""
							),
							"concept": str(concept.get("text") or concept.get("concept_id") or ""),
							"interview": str(
								item.get("source_title")
								or item.get("source_interview_id")
								or source_item_id
							),
							"interview_id": str(item.get("source_interview_id") or ""),
							"paragraph_id": str(evidence.get("paragraph_id") or ""),
							"exact_quote": str(evidence.get("exact_quote") or ""),
						})
					if evidence_refs:
						continue
					rows.append({
						"component": COMPONENT_LABELS.get(component_key, component_key),
						"theme": str(node.get("title") or ""),
						"integrated_category": str(category.get("title") or ""),
						"initial_category": str(
							item.get("researcher_approved_category_name")
							or item.get("original_category_name")
							or ""
						),
						"concept": str(concept.get("text") or concept.get("concept_id") or ""),
						"interview": str(
							item.get("source_title")
							or item.get("source_interview_id")
							or source_item_id
						),
					})
	return rows


def build_stage5_excel(model: dict[str, Any], hierarchy_rows: list[dict[str, str]]) -> bytes:
	workbook = Workbook()
	workbook.remove(workbook.active)

	def add_sheet(title: str, headers: list[str], rows: list[list[Any]]) -> None:
		sheet = workbook.create_sheet(title)
		sheet.sheet_view.rightToLeft = True
		sheet.freeze_panes = "A2"
		sheet.append(headers)
		for cell in sheet[1]:
			cell.font = Font(bold=True, color="FFFFFF")
			cell.fill = PatternFill("solid", fgColor="38554D")
			cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
		for row in rows:
			sheet.append([value if value is not None else "" for value in row])
		for row in sheet.iter_rows(min_row=1):
			thin = Side(style="thin", color="999999")
			for cell in row:
				cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
				if cell.row > 1:
					cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)
		for column in range(1, len(headers) + 1):
			sheet.column_dimensions[chr(64 + column)].width = 28

	traceability = model.get("source_traceability") or {}
	sources = traceability.get("sources") or []
	central_node = next(
		(node for node in model.get("nodes", []) if node.get("node_type") == "central_phenomenon"),
		{},
	)
	core = model.get("core_phenomenon") or {}
	comparison_rows = []
	for source in sources:
		phenomenon = source.get("central_phenomenon") or {}
		comparison_rows.append([
			source.get("source_interview_id") or source.get("source_project_id") or "",
			phenomenon.get("title") or "",
			phenomenon.get("definition") or "",
			core.get("title") or central_node.get("title") or "",
			"هم‌راستا" if phenomenon.get("title") else "نیازمند بازبینی",
			"؛ ".join(str(value) for value in phenomenon.get("evidence_ref_ids", [])),
		])
	add_sheet(
		"Core_Phenomenon_Comparison",
		["شناسه مصاحبه", "پدیده محوری مصاحبه", "تعریف", "پدیده محوری تلفیقی", "وضعیت هم‌راستایی", "شواهد پشتیبان"],
		comparison_rows,
	)
	divergence_rows = [
		[
			item.get("contradiction_id") or "",
			item.get("description") or "",
			item.get("reasoning") or "",
			item.get("review_status") or "",
			"؛ ".join(str(value) for value in item.get("evidence_ref_ids", [])),
		]
		for item in core.get("divergences", [])
	]
	add_sheet(
		"Core_Phenomenon_Divergences",
		["شناسه واگرایی", "توضیح واگرایی", "استدلال", "وضعیت بازبینی", "شناسه شواهد"],
		divergence_rows,
	)

	theme_rows = []
	register_status = {
		str(item.get("theme_id") or ""): str(item.get("status") or "")
		for item in model.get("theme_register", [])
	}
	for node in model.get("nodes", []):
		if node.get("node_type") not in {"theme", "independent_category"}:
			continue
		integrated_category_ids = [
			str(category.get("category_id") or "")
			for category in node.get("integrated_categories", [])
		]
		integrated_category_titles = [
			str(category.get("title") or "")
			for category in node.get("integrated_categories", [])
		]
		initial_category_ids = [
			str(source_item.get("source_category_id") or "")
			for category in node.get("integrated_categories", [])
			for source_item in category.get("source_items", [])
		]
		theme_rows.append([
			node.get("node_id") or "",
			node.get("title") or "",
			node.get("definition") or "",
			node.get("component_key") or "",
			"؛ ".join(integrated_category_ids),
			"؛ ".join(integrated_category_titles),
			"؛ ".join(initial_category_ids),
			"؛ ".join(str(value) for value in node.get("source_project_ids", [])),
			register_status.get(str(node.get("node_id") or ""), "ISOLATED" if node.get("node_type") == "independent_category" else "CONNECTED"),
		])
	add_sheet(
		"Themes_and_Categories_Matrix",
		["شناسه تم", "عنوان و تعریف تم", "تعریف", "مؤلفه پارادایمی", "شناسه مقوله تلفیقی", "عنوان مقوله تلفیقی", "شناسه مقوله اولیه", "مصاحبه‌های منبع", "وضعیت تم"],
		theme_rows,
	)

	relation_rows = []
	for edge in model.get("edges", []):
		relation_rows.append([
			edge.get("relationship_id") or edge.get("edge_id") or "",
			edge.get("source_node_id") or "",
			edge.get("target_node_id") or "",
			edge.get("relationship_type") or "",
			edge.get("description") or "",
			edge.get("reasoning") or edge.get("analytical_mechanism") or "",
			"؛ ".join(str(value) for value in edge.get("supporting_source_relationship_ids", [])),
			"؛ ".join(str(value) for value in edge.get("supporting_category_unit_ids", []) or edge.get("traceability", {}).get("category_unit_ids", [])),
			"؛ ".join(str(value) for value in edge.get("evidence_ref_ids", [])),
			"؛ ".join(str(value) for value in edge.get("supporting_interview_ids", [])),
			edge.get("evidence_level") or "CONCEPTUAL_INFERENCE",
			"APPROVED",
		])
	add_sheet(
		"Theoretical_Relations",
		["شناسه رابطه", "مبدأ", "مقصد", "نوع رابطه", "توضیح رابطه", "سازوکار و دلیل تحلیلی", "روابط پشتیبان", "مقولات پشتیبان", "شناسه شواهد", "مصاحبه‌های پشتیبان", "سطح شواهد", "وضعیت تأیید"],
		relation_rows,
	)

	isolated_rows = [
		[
			item.get("theme_id") or "",
			item.get("title") or "",
			item.get("definition") or "",
			item.get("component_key") or "",
			item.get("reason") or "",
			"؛ ".join(str(value) for value in item.get("supporting_category_ids", [])),
			"؛ ".join(str(value) for value in item.get("supporting_interview_ids", [])),
			item.get("status") or "NEEDS_REVIEW",
		]
		for item in model.get("isolated_themes", [])
	]
	add_sheet(
		"Isolated_Themes",
		["شناسه تم", "عنوان تم", "تعریف مفهومی", "مؤلفه پارادایمی", "علت استقلال", "مقولات پشتیبان", "مصاحبه‌های پشتیبان", "وضعیت"],
		isolated_rows,
	)

	trace_rows = [
		[
			row.get("interview") or "",
			row.get("concept") or "",
			row.get("initial_category") or "",
			row.get("integrated_category") or "",
			row.get("theme") or "",
			row.get("component") or "",
			row.get("interview_id") or "",
			row.get("paragraph_id") or "",
			row.get("exact_quote") or "",
		]
		for row in hierarchy_rows
	]
	add_sheet(
		"Full_Traceability_Interviews",
		["مصاحبه", "مفهوم یا کد اولیه", "مقوله اولیه", "مقوله تلفیقی", "تم نهایی", "مؤلفه پارادایمی", "شناسه مصاحبه", "شناسه پاراگراف", "نقل‌قول دقیق"],
		trace_rows,
	)

	output = BytesIO()
	workbook.save(output)
	return output.getvalue()


def final_model_to_docx(
	table: dict[str, Any],
	model: dict[str, Any],
	diagram_png: bytes,
	hierarchy_rows: list[dict[str, str]],
) -> bytes:
	document = Document()
	section = document.sections[0]
	section.orientation = WD_ORIENT.LANDSCAPE
	section.page_width, section.page_height = section.page_height, section.page_width

	title = document.add_heading("مدل نهایی نظریه داده‌بنیاد", level=0)
	_set_rtl_paragraph(title, "مدل نهایی نظریه داده‌بنیاد", bold=True, size=16)
	document.add_heading("نمودار مدل پارادایمی", level=1)
	document.add_picture(BytesIO(diagram_png), width=Inches(9))

	document.add_heading("روایت نظری تبیین‌کنندهٔ مدل", level=1)
	storyline = document.add_paragraph()
	_set_rtl_paragraph(
		storyline,
		str(model.get("theoretical_storyline") or "روایت نظری ثبت نشده است."),
		size=11,
	)

	central = next(
		(node for node in model.get("nodes", []) if node.get("node_type") == "central_phenomenon"),
		{},
	)
	document.add_heading("پدیده محوری جامع", level=1)
	central_paragraph = document.add_paragraph()
	_set_rtl_paragraph(
		central_paragraph,
		f"{central.get('title') or 'ثبت نشده'}: {central.get('definition') or 'تعریف ثبت نشده است.'}",
		size=11,
	)
	core = model.get("core_phenomenon") or {}
	if core.get("supporting_interview_ids"):
		support = document.add_paragraph()
		_set_rtl_paragraph(
			support,
			"مصاحبه‌های پشتیبان: " + "، ".join(str(value) for value in core["supporting_interview_ids"]),
			size=10,
		)
	document.add_heading("وجوه اشتراک، تفاوت‌ها و واگرایی‌ها", level=2)
	divergences = core.get("divergences") or []
	if divergences:
		for divergence in divergences:
			paragraph = document.add_paragraph()
			_set_rtl_paragraph(
				paragraph,
				f"{divergence.get('description') or ''} — استدلال: {divergence.get('reasoning') or ''}",
				size=10,
			)
	else:
		paragraph = document.add_paragraph()
		_set_rtl_paragraph(paragraph, "واگرایی جدی ثبت‌شده‌ای گزارش نشده است.", size=10)

	document.add_heading("جدول جامع تم‌های تأییدشده", level=1)
	headers = ["مؤلفه پارادایمی", "تم نهایی", "تعریف مفهومی", "مقولات پشتیبان", "مصاحبه‌های پشتیبان", "وضعیت"]
	grid = document.add_table(rows=1, cols=len(headers))
	grid.style = "Table Grid"
	grid.alignment = WD_TABLE_ALIGNMENT.CENTER
	grid._tbl.tblPr.find(qn("w:tblStyle")).addnext(OxmlElement("w:bidiVisual"))
	for cell, header in zip(grid.rows[0].cells, headers):
		_set_rtl_paragraph(cell.paragraphs[0], header, bold=True)
		_shade(cell, "D9E5DF")
	grid.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
	register_status = {
		str(item.get("theme_id") or ""): str(item.get("status") or "")
		for item in model.get("theme_register", [])
	}
	for node in model.get("nodes", []):
		if node.get("node_type") not in {"theme", "independent_category"}:
			continue
		cells = grid.add_row().cells
		values = (
			node.get("component_key") or "",
			node.get("title") or "",
			node.get("definition") or "",
			"، ".join(str(value) for value in node.get("category_unit_ids", [])),
			"، ".join(str(value) for value in node.get("source_project_ids", [])),
			register_status.get(str(node.get("node_id") or ""), "ISOLATED"),
		)
		for cell, value in zip(cells, values):
			_set_rtl_paragraph(cell.paragraphs[0], str(value), size=9)

	document.add_heading("تم‌های مستقل", level=1)
	independent_nodes = [
		node for node in model.get("nodes", [])
		if node.get("node_type") == "independent_category"
	]
	if independent_nodes:
		for node in independent_nodes:
			paragraph = document.add_paragraph()
			_set_rtl_paragraph(paragraph, f"{node.get('title') or node.get('node_id')}: {node.get('definition') or ''}", size=10)
	else:
		paragraph = document.add_paragraph()
		_set_rtl_paragraph(paragraph, "تم مستقل فاقد رابطه ثبت نشده است.", size=10)

	document.add_heading("روابط تأییدشده", level=1)
	for relation in model.get("edges", []):
		paragraph = document.add_paragraph()
		_set_rtl_paragraph(
			paragraph,
			f"{relation.get('source_node_id', '')} ← {relation.get('target_node_id', '')}: "
			f"{relation.get('description', '')} ({relation.get('direction', '')}). "
			f"سازوکار تحلیلی: {relation.get('reasoning') or relation.get('analytical_mechanism') or 'ثبت نشده'} "
			f"سطح شواهد: {relation.get('evidence_level') or 'CONCEPTUAL_INFERENCE'}. "
			f"مصاحبه‌های پشتیبان: {('، '.join(relation.get('supporting_interview_ids', [])) or 'ثبت نشده')} "
			f"مقولات پشتیبان: {('، '.join(relation.get('supporting_category_unit_ids', [])) or 'ثبت نشده')}",
		)

	document.add_heading("جدول جامع کدگذاری", level=1)
	headers = ["مؤلفه", "تم", "مقولهٔ تلفیقی", "مقولهٔ اولیه", "مفهوم", "مصاحبه", "شناسه پاراگراف", "نقل‌قول دقیق"]
	grid = document.add_table(rows=1, cols=len(headers))
	grid.style = "Table Grid"
	grid.alignment = WD_TABLE_ALIGNMENT.CENTER
	grid._tbl.tblPr.find(qn("w:tblStyle")).addnext(OxmlElement("w:bidiVisual"))
	for cell, header in zip(grid.rows[0].cells, headers):
		_set_rtl_paragraph(cell.paragraphs[0], header, bold=True)
		_shade(cell, "D9E5DF")
	grid.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
	for row in hierarchy_rows or [{}]:
		cells = grid.add_row().cells
		for cell, key in zip(cells, (
			"component", "theme", "integrated_category", "initial_category", "concept", "interview",
			"paragraph_id", "exact_quote",
		)):
			_set_rtl_paragraph(cell.paragraphs[0], row.get(key, "—") or "—", size=9)

	if table.get("interviews"):
		document.add_heading("مصاحبه‌های منبع", level=1)
		for interview in table["interviews"]:
			paragraph = document.add_paragraph()
			_set_rtl_paragraph(
				paragraph,
				f"م{interview['number']} = {interview['title']}",
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
