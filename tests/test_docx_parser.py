"""Tests for DOCX paragraph and annotation extraction."""

from io import BytesIO

import pytest
from docx import Document

from app.services.docx_parser import build_quality_report, parse_docx


def test_extracts_persian_concept_table_and_paragraph_ids():
	document = Document()
	document.add_paragraph("متن مصاحبه")
	table = document.add_table(rows=2, cols=5)
	rows = [
		["شناسه پاراگراف", "متن پاراگراف", "شناسه مفهوم", "مفهوم معنادار", "کد اولیه"],
		["P0001", "متن مصاحبه", "C0001", "مفهوم پژوهشگر", "شرایط زمینه‌ای"],
	]
	for row, values in zip(table.rows, rows):
		for cell, value in zip(row.cells, values):
			cell.text = value
	output = BytesIO()
	document.save(output)

	result = parse_docx(output.getvalue())

	assert result["paragraphs"] == [{"paragraph_id": "P0001", "text": "متن مصاحبه"}]
	assert result["concepts"] == [
		{
			"concept_id": "C0001",
			"paragraph_ids": ["P0001"],
			"original_concept": "مفهوم پژوهشگر",
			"original_code": "شرایط زمینه‌ای",
		}
	]


def test_rejects_non_docx_bytes():
	with pytest.raises(ValueError, match="فایل Word قابل خواندن نیست"):
		parse_docx(b"not a docx")


def test_extracts_paragraphs_from_first_concept_rows_and_inherits_them():
	document = Document()
	table = document.add_table(rows=4, cols=3)
	rows = [
		["متن مصاحبه", "مفاهیم معنادار", "کد"],
		["متن کامل پاراگراف اول", "مفهوم اول", "شرایط علّی"],
		["", "مفهوم دوم", "شرایط علّی"],
		["متن کامل پاراگراف دوم", "مفهوم سوم", "پیامد"],
	]
	for row, values in zip(table.rows, rows):
		for cell, value in zip(row.cells, values):
			cell.text = value
	output = BytesIO()
	document.save(output)

	result = parse_docx(output.getvalue())

	assert result["paragraphs"] == [
		{"paragraph_id": "P001", "text": "متن کامل پاراگراف اول"},
		{"paragraph_id": "P002", "text": "متن کامل پاراگراف دوم"},
	]
	assert [concept["paragraph_ids"] for concept in result["concepts"]] == [
		["P001"], ["P001"], ["P002"],
	]
	assert [concept["concept_id"] for concept in result["concepts"]] == [
		"C001", "C002", "C003",
	]
	assert build_quality_report(result["paragraphs"], result["concepts"])["structural_error_count"] == 0


def test_groups_repeated_merged_witness_text_under_one_paragraph():
	document = Document()
	table = document.add_table(rows=3, cols=3)
	rows = [
		["متن مصاحبه", "مفاهیم معنادار", "کد"],
		["شاهد کامل اول", "مفهوم اول", "شرایط علّی"],
		["", "مفهوم دوم", "پیامد"],
	]
	for row, values in zip(table.rows, rows):
		for cell, value in zip(row.cells, values):
			cell.text = value
	table.cell(1, 0).merge(table.cell(2, 0))
	output = BytesIO()
	document.save(output)

	result = parse_docx(output.getvalue())

	assert result["paragraphs"] == [{"paragraph_id": "P001", "text": "شاهد کامل اول"}]
	assert [item["paragraph_ids"] for item in result["concepts"]] == [["P001"], ["P001"]]
	assert [item["concept_id"] for item in result["concepts"]] == ["C001", "C002"]


def test_finds_header_after_title_row_and_ignores_body_heading():
	document = Document()
	document.add_paragraph("عنوان سند، نه متن مصاحبه")
	table = document.add_table(rows=4, cols=3)
	rows = [
		["جدول مفاهیم مصاحبه", "", ""],
		["متن مصاحبه", "مفاهیم معنادار", "کد اولیه"],
		["شاهد اول", "مفهوم اول", "شرایط علّی"],
		["شاهد دوم", "مفهوم دوم", "پیامد"],
	]
	for row, values in zip(table.rows, rows):
		for cell, value in zip(row.cells, values):
			cell.text = value
	output = BytesIO()
	document.save(output)

	result = parse_docx(output.getvalue())

	assert [item["text"] for item in result["paragraphs"]] == ["شاهد اول", "شاهد دوم"]
	assert [item["paragraph_ids"] for item in result["concepts"]] == [["P001"], ["P002"]]


def test_recognizes_researcher_specific_concept_header_and_extended_witness_header():
	document = Document()
	table = document.add_table(rows=2, cols=3)
	rows = [
		["متن کامل مصاحبه", "مفهوم معنادار پژوهشگر", "کد اولیه پژوهشگر"],
		["شاهد اول", "مفهوم اول", "شرایط علّی"],
	]
	for row, values in zip(table.rows, rows):
		for cell, value in zip(row.cells, values):
			cell.text = value
	output = BytesIO()
	document.save(output)

	result = parse_docx(output.getvalue())

	assert result["paragraphs"] == [{"paragraph_id": "P001", "text": "شاهد اول"}]
	assert result["concepts"][0]["original_concept"] == "مفهوم اول"
	assert result["concepts"][0]["original_code"] == "شرایط علّی"


def test_reports_when_table_headers_are_not_recognized():
	document = Document()
	table = document.add_table(rows=2, cols=3)
	rows = [
		["گفتگو", "مفهوم استخراج‌شده", "مقوله"],
		["شاهد اول", "مفهوم اول", "زمینه"],
	]
	for row, values in zip(table.rows, rows):
		for cell, value in zip(row.cells, values):
			cell.text = value
	output = BytesIO()
	document.save(output)

	result = parse_docx(output.getvalue())

	assert result["concepts"] == []
	assert any(issue["severity"] == "error" for issue in result["processing_issues"])
	assert "مفاهیم استخراج نشده‌اند" in result["processing_issues"][0]["message"]


def test_groups_identical_text_from_unmerged_rows_under_one_witness():
	document = Document()
	table = document.add_table(rows=3, cols=3)
	rows = [
		["متن مصاحبه", "مفاهیم معنادار", "کد"],
		["شاهد یکسان", "مفهوم اول", "شرایط علّی"],
		[" شاهد  یکسان ", "مفهوم دوم", "پیامد"],
	]
	for row, values in zip(table.rows, rows):
		for cell, value in zip(row.cells, values):
			cell.text = value
	output = BytesIO()
	document.save(output)

	result = parse_docx(output.getvalue())

	assert result["paragraphs"] == [{"paragraph_id": "P001", "text": "شاهد یکسان"}]
	assert [item["paragraph_ids"] for item in result["concepts"]] == [["P001"], ["P001"]]


def test_retains_concept_with_missing_title_and_reports_all_counts():
	document = Document()
	table = document.add_table(rows=3, cols=3)
	rows = [
		["متن مصاحبه", "مفاهیم معنادار", "کد"],
		["شاهد اول", "", "شرایط علّی"],
		["شاهد بدون مفهوم", "", ""],
	]
	for row, values in zip(table.rows, rows):
		for cell, value in zip(row.cells, values):
			cell.text = value
	output = BytesIO()
	document.save(output)

	result = parse_docx(output.getvalue())
	report = build_quality_report(result["paragraphs"], result["concepts"])

	assert len(result["concepts"]) == 1
	assert result["concepts"][0]["original_concept"] == ""
	assert report["concepts_with_code"] == 1
	assert report["concepts_without_title"] == 1
	assert report["paragraphs_without_concepts"] == 1


def test_quality_report_flags_missing_code_and_unknown_code_without_dropping_data():
	paragraphs = [{"paragraph_id": "P0001", "text": "متن مصاحبه"}]
	concepts = [
		{"concept_id": "C0001", "paragraph_ids": ["P0001"], "original_concept": "اول", "original_code": ""},
		{"concept_id": "C0002", "paragraph_ids": ["P0001"], "original_concept": "دوم", "original_code": "کد ناشناخته"},
	]

	report = build_quality_report(paragraphs, concepts)

	assert report["concept_count"] == 2
	assert report["structural_error_count"] == 1
	assert any("کد ناشناخته" in issue["message"] for issue in report["issues"])
