"""Tests for prompt preparation and downloadable outputs."""

from io import BytesIO
import json

from docx import Document
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.main import app
from app.routers import results as results_router
from app.services.analysis_service import prepare_analysis_results
from app.services.export_service import create_excel, create_word_report
from app.services.prompt_builder import build_prompt


def test_prompt_contains_project_ids_and_interview_text():
	project = {
		"project_id": "project-1", "title": "پژوهش", "topic": "موضوع",
		"paragraphs": [{"paragraph_id": "P0001", "text": "شاهد مصاحبه"}],
		"concepts": [{"concept_id": "C0001", "paragraph_ids": ["P0001"], "original_concept": "مفهوم", "original_code": "کد"}],
	}
	prompt = build_prompt(project)
	assert "project-1" in prompt and "شاهد مصاحبه" in prompt and "C0001" in prompt
	assert "هیچ شیء evidence نباید بدون این سه فیلد باشد" in prompt
	input_data = json.loads(prompt.split("داده‌های پروژه (JSON):\n", 1)[1])
	assert input_data["concepts"][0]["supporting_paragraphs"][0]["text"] == "شاهد مصاحبه"
	assert input_data["paragraphs"][0]["concepts"] == [
		{"concept_id": "C0001", "title": "مفهوم", "initial_code": "کد"}
	]


def test_exports_are_valid_office_files():
	analysis = {
		"concept_reviews": [], "categories": [{"category_id": "CAT01", "category_name": "مقوله", "definition": "تعریف", "concept_ids": ["C0001"]}],
		"central_phenomenon": {"category_id": "CAT01", "title": "پدیده", "definition": "تعریف"},
		"paradigm_model": {}, "relationships": [],
	}
	project = {"project_id": "project-1", "title": "پژوهش", "topic": "موضوع", "paragraphs": [], "analysis": analysis}

	workbook = load_workbook(BytesIO(create_excel(project)))
	report = Document(BytesIO(create_word_report(project)))

	assert "بررسی مفاهیم" in workbook.sheetnames
	assert report.paragraphs[0].text == "پژوهش"


def test_results_keep_analysis_views_without_diagram_or_vector_exports(monkeypatch):
	project = {
		"project_id": "project-test",
		"title": "پژوهش آزمایشی",
		"concepts": [],
		"analysis": {
			"analysis_status": "complete",
			"analysis_metadata": {},
			"concept_reviews": [],
			"categories": [],
			"central_phenomenon": None,
			"paradigm_model": {},
			"relationships": [{
				"source_category_id": "CAT01", "target_category_id": "CAT02",
				"relationship_type": "علّی", "description": "رابطه ثبت‌شده",
				"confidence": 0.8, "needs_researcher_review": False,
			}],
		},
	}
	monkeypatch.setattr(results_router, "get_project", lambda _: project)

	client = TestClient(app)
	results_page = client.get("/results/project-test")
	relationships_page = client.get("/results/project-test/relationships")
	diagram_export = client.get("/exports/project-test/paradigm.svg")
	font = client.get("/static/fonts/Vazirmatn-Variable.woff2")

	assert results_page.status_code == 200
	assert "پدیده محوری پیشنهادی" in results_page.text
	assert "paradigm-diagram" not in results_page.text
	assert "data-download-png" not in results_page.text
	assert relationships_page.status_code == 200
	assert "رابطه ثبت‌شده" in relationships_page.text
	assert diagram_export.status_code == 404
	assert font.status_code == 200
	assert len(font.content) > 10000


def test_prepare_analysis_results_maps_review_sections_for_display():
	results = prepare_analysis_results({
		"concept_reviews": [{
			"concept_id": "C001", "paragraph_id": "P001",
			"original_concept": "عنوان اولیه", "suggested_concept": "عنوان بهتر",
			"concept_reason": "روشن‌تر", "original_code": "علّی", "suggested_code": "شرایط علّی",
			"code_evaluation": "revise", "code_reason": "شاهد",
		}],
		"categories": [{"category_id": "CAT01"}],
		"relationships": [{"relationship_id": "R01"}],
	})

	assert results["concept_rows"][0]["concept_reason"] == "روشن‌تر"
	assert results["categories"][0]["category_id"] == "CAT01"
	assert results["relationships"][0]["relationship_id"] == "R01"
	assert results["diagram"] == {"nodes": [], "edges": []}
	assert results["analysis_status"] == "incomplete"
