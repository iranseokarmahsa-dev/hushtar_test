"""Extract interview witnesses, researcher concepts, and processing quality checks."""

from collections import Counter
from io import BytesIO
import re
import unicodedata
from typing import Any

from docx import Document


PARAGRAPH_ID_HEADERS = {"paragraphid", "شناسهپاراگراف", "شناسه"}
PARAGRAPH_TEXT_HEADERS = {
	"paragraph", "paragraphtext", "text", "متنپاراگراف", "متنمصاحبه", "متنکامل",
	"متنکاملپاراگراف", "متنمصاحبهشونده", "متن",
}
CONCEPT_ID_HEADERS = {"conceptid", "شناسه مفهوم"}
CONCEPT_HEADERS = {
	"concept", "meaningfulconcept", "مفهوم", "مفهوممعنادار", "مفاهیممعنادار",
	"مفهوممعنادارپژوهشگر", "مفاهیممعنادارپژوهشگر", "عنوانمفهوم", "عنوانمفهوممعنادار",
	"مفهومپیشنهادی", "مفهوممصاحبه",
}
CODE_HEADERS = {
	"code", "initialcode", "originalcode", "کد", "کداولیه", "کداولیهپژوهشگر",
	"کدگذاری", "کدگذاری اولیه", "کدگذاریباز", "کدمفهوم",
}
EXPECTED_CODES = {
	"شرایط علّی",
	"شرایط زمینه‌ای",
	"شرایط مداخله‌گر",
	"استراتژی",
	"پیامد",
}


def _normalize(value: str) -> str:
	value = value.translate(str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"}))
	return "".join(
		char for char in value.lower()
		if not unicodedata.category(char).startswith("M") and char.isalnum()
	)


def _clean_text(value: str) -> str:
	return re.sub(r"\s+", " ", value).strip()


def _normalize_witness(value: str) -> str:
	value = _clean_text(value).translate(str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"}))
	return re.sub(r"\s+", " ", value.replace("\u200c", " ")).strip().casefold()


def _next_id(prefix: str, used_ids: set[str], reserved_ids: set[str] | None = None) -> str:
	reserved = reserved_ids or set()
	index = 1
	while f"{prefix}{index:03d}" in used_ids or f"{prefix}{index:03d}" in reserved:
		index += 1
	return f"{prefix}{index:03d}"


def _issue(severity: str, item_id: str, message: str) -> dict[str, str]:
	return {"severity": severity, "item_id": item_id, "message": message}


def _find_header(headers: list[str], aliases: set[str]) -> int | None:
	normalized_aliases = {_normalize(alias) for alias in aliases}
	for index, header in enumerate(headers):
		normalized_header = _normalize(header)
		if normalized_header in normalized_aliases:
			return index
	for index, header in enumerate(headers):
		normalized_header = _normalize(header)
		if any(
			len(alias) >= 6 and alias in normalized_header
			for alias in normalized_aliases
		):
			return index
	return None


def _table_headers(table: Any) -> tuple[int, dict[str, int | None]] | None:
	"""Find the labeled header row even if a document has a title row above it."""
	for row_index, row in enumerate(table.rows[:8]):
		headers = [_normalize(cell.text) for cell in row.cells]
		indices = {
			"paragraph_id": _find_header(headers, PARAGRAPH_ID_HEADERS),
			"paragraph_text": _find_header(headers, PARAGRAPH_TEXT_HEADERS),
			"concept": _find_header(headers, CONCEPT_HEADERS),
			"code": _find_header(headers, CODE_HEADERS),
			"concept_id": _find_header(headers, CONCEPT_ID_HEADERS),
		}
		core_indices = [indices[name] for name in ("paragraph_text", "concept", "code")]
		if all(index is not None for index in core_indices) and len(set(core_indices)) == 3:
			return row_index, indices
	return None


def parse_docx(content: bytes) -> dict[str, Any]:
	"""Parse a three-column transcript table or fall back to document paragraphs."""
	try:
		document = Document(BytesIO(content))
	except Exception as error:
		raise ValueError("فایل Word قابل خواندن نیست؛ فایل DOCX معتبر بارگذاری کنید.") from error

	paragraphs: list[dict[str, str]] = []
	paragraphs_by_id: dict[str, dict[str, str]] = {}
	paragraph_ids_by_text: dict[str, str] = {}
	concepts: list[dict[str, Any]] = []
	processing_issues: list[dict[str, str]] = []
	used_paragraph_ids: set[str] = set()
	used_concept_ids: set[str] = set()
	recognized_tables = []
	for table in document.tables:
		header = _table_headers(table)
		if header is not None:
			recognized_tables.append((table, *header))

	if recognized_tables:
		for table, header_row_index, indices in recognized_tables:
			current_paragraph_id = ""
			rows = table.rows[header_row_index + 1:]
			reserved_paragraph_ids = {
				_cell([_clean_text(cell.text) for cell in row.cells], indices["paragraph_id"])
				for row in rows
			} - {""}
			reserved_concept_ids = {
				_cell([_clean_text(cell.text) for cell in row.cells], indices["concept_id"])
				for row in rows
			} - {""}
			for row in rows:
				cells = [_clean_text(cell.text) for cell in row.cells]
				if not any(cells):
					continue

				paragraph_text = _cell(cells, indices["paragraph_text"])
				supplied_paragraph_id = _cell(cells, indices["paragraph_id"])
				title = _cell(cells, indices["concept"])
				initial_code = _cell(cells, indices["code"])
				supplied_concept_id = _cell(cells, indices["concept_id"])

				if paragraph_text or supplied_paragraph_id:
					normalized_text = _normalize_witness(paragraph_text) if paragraph_text else ""
					matching_paragraph_id = paragraph_ids_by_text.get(normalized_text) if normalized_text else None
					if matching_paragraph_id:
						current_paragraph_id = matching_paragraph_id
						if supplied_paragraph_id and supplied_paragraph_id != matching_paragraph_id:
							processing_issues.append(
								_issue(
									"warning",
									supplied_paragraph_id,
									f"متن شاهد تکراری بود و به شاهد {matching_paragraph_id} متصل شد.",
								)
							)
					elif supplied_paragraph_id:
						current_paragraph_id = supplied_paragraph_id
						existing = paragraphs_by_id.get(current_paragraph_id)
						if existing and paragraph_text and _normalize_witness(existing["text"]) != normalized_text:
							new_id = _next_id("P", used_paragraph_ids, reserved_paragraph_ids)
							processing_issues.append(
								_issue(
									"warning",
									supplied_paragraph_id,
									f"شناسه پاراگراف برای متن متفاوت تکرار شد؛ شناسه {new_id} ساخته شد.",
								)
							)
							current_paragraph_id = new_id
						if current_paragraph_id not in paragraphs_by_id and paragraph_text:
							paragraph = {"paragraph_id": current_paragraph_id, "text": paragraph_text}
							paragraphs.append(paragraph)
							paragraphs_by_id[current_paragraph_id] = paragraph
							used_paragraph_ids.add(current_paragraph_id)
							if normalized_text:
								paragraph_ids_by_text[normalized_text] = current_paragraph_id
						elif current_paragraph_id not in paragraphs_by_id:
							processing_issues.append(
								_issue("warning", current_paragraph_id, "شناسه شاهد ارسال شده اما متن شاهد در ردیف موجود نیست.")
							)
					elif paragraph_text:
						current_paragraph_id = _next_id("P", used_paragraph_ids, reserved_paragraph_ids)
						paragraph = {"paragraph_id": current_paragraph_id, "text": paragraph_text}
						paragraphs.append(paragraph)
						paragraphs_by_id[current_paragraph_id] = paragraph
						paragraph_ids_by_text[normalized_text] = current_paragraph_id
						used_paragraph_ids.add(current_paragraph_id)

				# A row with a code or supplied ID is still retained as an incomplete
				# concept when its title cell is empty.
				if not (title or initial_code or supplied_concept_id):
					continue

				concept_id = supplied_concept_id
				if not concept_id or concept_id in used_concept_ids:
					if concept_id:
						processing_issues.append(
							_issue("warning", concept_id, "شناسه مفهوم تکراری بود؛ شناسه یکتای جدید ساخته شد.")
						)
					concept_id = _next_id("C", used_concept_ids, reserved_concept_ids)
				used_concept_ids.add(concept_id)
				concepts.append(
					{
						"concept_id": concept_id,
						"paragraph_ids": [current_paragraph_id] if current_paragraph_id else [],
						"original_concept": title,
						"original_code": initial_code,
					}
				)
	else:
		if document.tables:
			processing_issues.append(
				_issue(
					"error",
					"جدول",
					"جدول مفاهیم پیدا شد اما سرستون‌های متن، مفهوم و کد شناسایی نشدند؛ مفاهیم استخراج نشده‌اند. سرستون‌های فایل را بررسی کنید.",
				)
			)
		for paragraph in document.paragraphs:
			text = _clean_text(paragraph.text)
			if text:
				paragraph_id = _next_id("P", used_paragraph_ids)
				paragraphs.append({"paragraph_id": paragraph_id, "text": text})
				used_paragraph_ids.add(paragraph_id)

		# Preserve the legacy plain-table fallback when no body paragraphs exist.
		if not paragraphs:
			for table in document.tables:
				for row in table.rows:
					text = _clean_text(" ".join(cell.text for cell in row.cells))
					if text:
						paragraph_id = _next_id("P", used_paragraph_ids)
						paragraphs.append({"paragraph_id": paragraph_id, "text": text})
						used_paragraph_ids.add(paragraph_id)

	if not paragraphs:
		raise ValueError("در فایل Word متن پاراگرافی پیدا نشد.")
	return {
		"paragraphs": paragraphs,
		"concepts": concepts,
		"processing_issues": processing_issues,
	}


def build_quality_report(
	paragraphs: list[dict[str, Any]],
	concepts: list[dict[str, Any]],
	processing_issues: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
	"""Summarize extraction problems without altering or removing source data."""
	issues = list(processing_issues or [])
	paragraphs_by_id = {paragraph["paragraph_id"]: paragraph for paragraph in paragraphs}
	concepts_by_paragraph = Counter(
		paragraph_id
		for concept in concepts
		for paragraph_id in concept.get("paragraph_ids", [])
	)

	for paragraph in paragraphs:
		if not paragraph.get("text", "").strip():
			issues.append(_issue("error", paragraph["paragraph_id"], "متن شاهد خالی است."))
		if not concepts_by_paragraph[paragraph["paragraph_id"]]:
			issues.append(_issue("error", paragraph["paragraph_id"], "شاهد بدون مفهوم است."))

	for concept in concepts:
		concept_id = concept.get("concept_id", "مفهوم بدون شناسه")
		if not concept.get("original_concept", "").strip():
			issues.append(_issue("error", concept_id, "عنوان مفهوم خالی است."))
		if not concept.get("original_code", "").strip():
			issues.append(_issue("error", concept_id, "مفهوم بدون کد اولیه است."))
		if not concept.get("paragraph_ids") or not any(
			paragraph_id in paragraphs_by_id and paragraphs_by_id[paragraph_id].get("text", "").strip()
			for paragraph_id in concept.get("paragraph_ids", [])
		):
			issues.append(_issue("error", concept_id, "مفهوم متن پشتیبان معتبر ندارد."))
		code = _normalize(concept.get("original_code", ""))
		if code and code not in {_normalize(item) for item in EXPECTED_CODES}:
			issues.append(
				_issue("warning", concept_id, f"کد ناشناخته است و برای بررسی گزارش شد: {concept['original_code']}")
			)

	concept_keys = [
		(_normalize(item.get("original_concept", "")), tuple(item.get("paragraph_ids", [])))
		for item in concepts
	]
	for (concept_text, paragraph_ids_for_concept), count in Counter(concept_keys).items():
		if concept_text and count > 1:
			issues.append(
				_issue(
					"warning",
					", ".join(paragraph_ids_for_concept) or "مفاهیم",
					"مفهوم تکراری شناسایی شد؛ هیچ موردی حذف نشده است.",
				)
			)

	paragraph_texts = Counter(_normalize_witness(item.get("text", "")) for item in paragraphs)
	for paragraph in paragraphs:
		if paragraph_texts[_normalize_witness(paragraph.get("text", ""))] > 1:
			issues.append(
				_issue("warning", paragraph["paragraph_id"], "متن شاهد تکراری است؛ موردی حذف نشده است.")
			)

	code_types = {
		code
		for code in (item.get("original_code", "").strip() for item in concepts)
		if code
	}
	return {
		"paragraph_count": len(paragraphs),
		"paragraphs_with_concepts": sum(
			1 for paragraph in paragraphs if concepts_by_paragraph[paragraph["paragraph_id"]]
		),
		"concept_count": len(concepts),
		"concepts_with_code": sum(bool(item.get("original_code", "").strip()) for item in concepts),
		"concepts_without_title": sum(not bool(item.get("original_concept", "").strip()) for item in concepts),
		"concepts_without_code": sum(not bool(item.get("original_code", "").strip()) for item in concepts),
		"paragraphs_without_concepts": sum(
			not bool(concepts_by_paragraph[paragraph["paragraph_id"]]) for paragraph in paragraphs
		),
		"code_type_count": len(code_types),
		"structural_error_count": sum(issue["severity"] == "error" for issue in issues),
		"issues": issues,
		"operations": [
			"استخراج و شناسه‌گذاری شاهدها",
			"استخراج مفاهیم و کدگذاری اولیه",
			"اتصال مفاهیم به متن کامل شاهد",
			"بررسی ساختار و کیفیت داده‌ها",
		],
	}


def _cell(cells: list[str], index: int | None) -> str:
	return cells[index] if index is not None and index < len(cells) else ""
