import json
import sqlite3
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
import pytest

from app.core import database
from app.main import app
from app.routers import analysis as analysis_router
from app.routers import projects as projects_router
from app.routers import results as results_router
from app.services.analysis_service import parse_analysis_json
from app.services.prompt_builder import build_prompt


def make_project_and_output():
	project = {
		"project_id": "project-v2",
		"title": "پژوهش آزمایشی",
		"topic": "",
		"paragraphs": [{"paragraph_id": "P001", "text": "مدیر گفت: فشار کاری زیاد است."}],
		"concepts": [{
			"concept_id": "C001",
			"paragraph_ids": ["P001"],
			"original_concept": "فشار کاری",
			"original_code": "شرایط زمینه‌ای",
		}],
		"analysis": None,
		"researcher_edits": {},
	}
	output = {
		"schema_version": "2.0",
		"software": "HOOSHYAR",
		"analysis_method": "Strauss_Corbin_Grounded_Theory",
		"project_id": "project-v2",
		"interview_id": "project-v2",
		"analysis_status": "complete",
		"source_scope": {
			"total_paragraphs": 1,
			"total_concepts_received": 1,
			"total_concepts_reviewed": 1,
		},
		"concept_reviews": [{
			"concept_id": "C001",
			"paragraph_id": "P001",
			"original_concept": "فشار کاری",
			"suggested_concept": "تجربه فشار کاری",
			"concept_validity": "valid",
			"concept_change_required": True,
			"concept_reason": "عنوان پیشنهادی روشن‌تر است.",
			"original_code": "شرایط زمینه‌ای",
			"suggested_code": "شرایط زمینه‌ای",
			"code_evaluation": "confirmed",
			"code_reason": "به زمینه کار اشاره می‌کند.",
			"evidence": [{
				"paragraph_id": "P001",
				"exact_quote": "فشار کاری زیاد است",
				"interpretation": "شاهد مستقیم مفهوم است.",
			}],
			"overlap": {"has_overlap": False, "related_concept_ids": [], "explanation": ""},
			"confidence": 0.8,
			"needs_researcher_review": True,
		}],
		"interview_analysis": {
			"categories": [{
				"category_id": "CAT01",
				"category_name": "فشارهای کاری",
				"definition": "تجربه فشار در محیط کار.",
				"category_reason": "مفهوم ورودی به این مضمون اشاره دارد.",
				"subcategories": [],
				"concept_ids": ["C001"],
				"properties": [],
				"dimensions": [],
				"evidence": [{
					"paragraph_id": "P001",
					"exact_quote": "فشار کاری زیاد است",
					"interpretation": "شاهد مقوله است.",
				}],
				"confidence": 0.7,
				"needs_researcher_review": True,
			}],
			"central_phenomenon": None,
			"relationships": [],
			"paradigm_model": {
				"causal_conditions": [],
				"central_phenomenon": None,
				"contextual_conditions": [],
				"intervening_conditions": [],
				"action_interaction_strategies": [],
				"consequences": [],
			},
			"researcher_review": [],
			"quality_audit": {
				"all_concepts_reviewed": True,
				"all_ids_preserved": True,
				"all_evidence_verifiable": True,
				"unsupported_relationships_detected": False,
				"contradictions_detected": False,
				"missing_data_detected": False,
				"analysis_limitations": [],
				"final_status": "complete",
			},
		},
	}
	return project, output


def test_v2_json_upload_separates_pages_and_persists_researcher_edits(monkeypatch):
	project, output = make_project_and_output()
	category = output["interview_analysis"]["categories"][0]
	category["subcategories"] = [{
		"subcategory_id": "SUB01",
		"subcategory_name": "فشار زمانی",
		"definition": "فشار مرتبط با زمان کار.",
		"concept_ids": ["C001"],
		"evidence": [{
			"paragraph_id": "P001",
			"exact_quote": "فشار کاری زیاد است",
			"interpretation": "شاهد زیرمقوله است.",
		}],
	}]
	category["properties"] = [{"property_name": "شدت", "description": "میزان فشار گزارش‌شده."}]
	category["dimensions"] = [{"property_name": "شدت", "dimension_range": "کم تا زیاد", "evidence": []}]
	output["interview_analysis"]["paradigm_model"]["causal_conditions"] = [{
		"category_id": "CAT01",
		"concept_ids": ["C001"],
		"explanation": "توضیح مؤلفهٔ علّی",
		"evidence": [{
			"paragraph_id": "P001",
			"exact_quote": "فشار کاری زیاد است",
			"interpretation": "شاهد مؤلفه است.",
		}],
	}]
	output["interview_analysis"]["relationships"] = [{
		"relationship_id": "REL01",
		"source_category_id": "CAT01",
		"target_category_id": "CAT01",
		"relationship_type": "interpretive",
		"description": "رابطهٔ قابل بازبینی",
		"supporting_concept_ids": ["C001"],
		"evidence": [],
		"confidence": 0.6,
		"needs_researcher_review": True,
	}]
	output["interview_analysis"]["central_phenomenon"] = {
		"identified": True,
		"category_id": "CAT01",
		"title": "مدیریت فشار کاری",
		"definition": "فرایند مواجهه با فشارهای کاری.",
		"selection_reason": "این مقوله پیوند چند مضمون را نشان می‌دهد.",
		"supporting_category_ids": ["CAT01"],
		"supporting_concept_ids": ["C001"],
		"evidence": [{
			"paragraph_id": "P001",
			"exact_quote": "فشار کاری زیاد است",
			"interpretation": "شاهد پدیده محوری است.",
		}],
		"alternative_interpretations": [{"title": "فشار محیطی", "description": "تفسیر جایگزین"}],
		"confidence": 0.75,
		"needs_researcher_review": True,
	}
	output["interview_analysis"]["paradigm_model"]["central_phenomenon"] = {
		"category_id": "CAT01",
		"title": "مدیریت فشار کاری",
	}
	monkeypatch.setattr(analysis_router, "get_project", lambda _: project)
	monkeypatch.setattr(results_router, "get_project", lambda _: project)
	monkeypatch.setattr(projects_router, "get_project", lambda _: project)

	def update_project(_project_id, **changes):
		project.update(changes)
		return project

	monkeypatch.setattr(analysis_router, "update_project", update_project)
	monkeypatch.setattr(results_router, "update_project", update_project)
	client = TestClient(app, follow_redirects=False)

	response = client.post(
		"/analysis/project-v2/import",
		files={"analysis_file": ("answer.json", json.dumps(output, ensure_ascii=False).encode(), "application/json")},
	)
	assert response.status_code == 303
	assert response.headers["location"] == "/results/project-v2/concept-review"

	concept_page = client.get(response.headers["location"])
	assert concept_page.status_code == 200
	assert "بازبینی مفاهیم پژوهشگر" in concept_page.text
	assert "تحلیل مستقل مصاحبه" not in concept_page.text
	assert "فشار کاری زیاد است" in concept_page.text

	saved_concepts = client.post(
		"/results/project-v2/concept-review",
		data={
			"concept_ids": ["C001"],
			"suggested_concepts": ["فشار کاری مستمر"],
			"suggested_codes": ["شرایط زمینه‌ای"],
			"researcher_notes": ["عنوان پس از بررسی اصلاح شد."],
			"confirmed_concept_ids": ["C001"],
		},
	)
	assert saved_concepts.status_code == 303
	assert project["analysis"]["concept_reviews"] == output["concept_reviews"]
	assert project["analysis"]["interview_analysis"]["categories"][0]["subcategories"] == output["interview_analysis"]["categories"][0]["subcategories"]
	assert project["analysis"]["interview_analysis"]["categories"][0]["subcategory_ids"] == ["SUB01"]
	assert project["researcher_edits"]["concept_reviews"]["C001"]["suggested_concept"] == "فشار کاری مستمر"

	analysis_page = client.get("/results/project-v2/interview-analysis")
	assert analysis_page.status_code == 200
	assert "فشارهای کاری" in analysis_page.text
	assert "مقوله‌های اصلی" in analysis_page.text
	assert "شش مؤلفهٔ پارادایمی" in analysis_page.text
	assert "SUB01" in analysis_page.text
	assert "شدت" in analysis_page.text
	assert "کم تا زیاد" in analysis_page.text
	assert "توضیح مؤلفهٔ علّی" in analysis_page.text
	assert "REL01" in analysis_page.text
	assert "مدیریت فشار کاری" in analysis_page.text
	assert "تفسیر جایگزین" in analysis_page.text
	assert "/results/project-v2/paradigm" in analysis_page.text

	saved_analysis = client.post(
		"/results/project-v2/interview-analysis",
		data={
			"category_ids": ["CAT01"],
			"category_names": ["فشار کاری سازمانی"],
			"category_definitions": ["تعریف اصلاح‌شده توسط پژوهشگر."],
			"relationship_ids": ["REL01"],
			"relationship_descriptions": ["رابطه پس از بازبینی پژوهشگر"],
			"central_phenomenon_title": "",
			"central_phenomenon_definition": "",
			"researcher_note": "نیازمند نمونه بیشتر",
			"confirmed": "true",
		},
	)
	assert saved_analysis.status_code == 303
	assert project["analysis"]["concept_reviews"] == output["concept_reviews"]
	assert project["analysis"]["interview_analysis"]["categories"][0]["subcategories"] == output["interview_analysis"]["categories"][0]["subcategories"]
	assert project["researcher_edits"]["interview_analysis"]["categories"]["CAT01"]["category_name"] == "فشار کاری سازمانی"
	assert project["researcher_edits"]["interview_analysis"]["confirmed"] is True

	updated_page = client.get("/results/project-v2/interview-analysis")
	assert "فشار کاری سازمانی" in updated_page.text
	project_page = client.get("/projects/project-v2")
	assert project_page.status_code == 200
	assert "/results/project-v2/concept-review" in project_page.text
	assert "/results/project-v2/interview-analysis" in project_page.text


def test_v2_prompt_includes_interview_id_and_two_part_contract():
	project, output = make_project_and_output()
	prompt = build_prompt(project)
	assert '"interview_id": "project-v2"' in prompt
	assert '"schema_version": "2.0"' in prompt
	assert "concept_reviews" in prompt and "interview_analysis" in prompt
	assert '"subcategory_ids": ["SUB01"]' in prompt
	assert '"subcategories": [{"subcategory_id": "SUB01"' in prompt
	assert parse_analysis_json(json.dumps(output, ensure_ascii=False), project)


def test_v2_rejects_quotes_not_found_in_the_source():
	project, output = make_project_and_output()
	output["concept_reviews"][0]["evidence"][0]["exact_quote"] = "نقل‌قول ساختگی"
	with pytest.raises(ValueError, match="عیناً در متن پاراگراف پیدا نشد"):
		parse_analysis_json(json.dumps(output, ensure_ascii=False), project)


def test_v2_rejects_unknown_relationship_categories():
	project, output = make_project_and_output()
	output["interview_analysis"]["relationships"] = [{
		"relationship_id": "REL01",
		"source_category_id": "CAT99",
		"target_category_id": "CAT01",
		"relationship_type": "interpretive",
		"description": "رابطه",
		"supporting_concept_ids": ["C001"],
		"evidence": [],
		"confidence": 0.5,
		"needs_researcher_review": True,
	}]
	with pytest.raises(ValueError, match="شناسه‌ی ارجاع‌شده وجود ندارد"):
		parse_analysis_json(json.dumps(output, ensure_ascii=False), project)


def test_v2_incomplete_output_reports_unreviewed_concepts():
	project, output = make_project_and_output()
	output["analysis_status"] = "incomplete"
	output["source_scope"]["total_concepts_reviewed"] = 0
	output["concept_reviews"] = []
	analysis = output["interview_analysis"]
	analysis["researcher_review"] = [{
		"item_type": "concept",
		"item_id": "C001",
		"issue": "بازبینی نشده",
		"reason": "داده کافی نیست.",
		"suggested_action": "بازبینی دستی",
	}]
	analysis["quality_audit"].update({
		"all_concepts_reviewed": False,
		"all_ids_preserved": False,
		"missing_data_detected": True,
		"final_status": "incomplete",
	})
	parsed = parse_analysis_json(json.dumps(output, ensure_ascii=False), project)
	assert parsed["analysis_status"] == "incomplete"


def test_v2_import_accepts_central_phenomenon_researcher_review_item(monkeypatch):
	project, output = make_project_and_output()
	output["interview_analysis"]["researcher_review"] = [{
		"item_type": "central_phenomenon",
		"item_id": "central_phenomenon",
		"issue": "نیازمند بازبینی پژوهشگر",
		"reason": "شواهد برای تعیین قطعی کافی نیست.",
		"suggested_action": "بازبینی شواهد مصاحبه",
	}]
	monkeypatch.setattr(analysis_router, "get_project", lambda _: project)
	monkeypatch.setattr(analysis_router, "update_project", lambda _project_id, **changes: project.update(changes) or project)
	response = TestClient(app, follow_redirects=False).post(
		"/analysis/project-v2/import",
		files={"analysis_file": ("answer.json", json.dumps(output, ensure_ascii=False).encode(), "application/json")},
	)
	assert response.status_code == 303
	assert response.headers["location"] == "/results/project-v2/concept-review"
	assert project["analysis"]["interview_analysis"]["researcher_review"][0]["item_type"] == "central_phenomenon"


def test_v2_import_downgrades_quality_warnings_and_preserves_raw_output(monkeypatch):
	project, output = make_project_and_output()
	output["interview_analysis"]["quality_audit"]["missing_data_detected"] = True
	monkeypatch.setattr(analysis_router, "get_project", lambda _: project)
	monkeypatch.setattr(results_router, "get_project", lambda _: project)
	monkeypatch.setattr(analysis_router, "update_project", lambda _project_id, **changes: project.update(changes) or project)
	response = TestClient(app, follow_redirects=False).post(
		"/analysis/project-v2/import",
		files={"analysis_file": ("answer.json", json.dumps(output, ensure_ascii=False).encode(), "application/json")},
	)
	assert response.status_code == 303
	assert project["analysis"]["analysis_status"] == "incomplete"
	assert project["analysis"]["interview_analysis"]["quality_audit"]["final_status"] == "incomplete"
	assert project["raw_analysis"] == output
	assert project["raw_analysis"]["analysis_status"] == "complete"
	assert any("وضعیت به incomplete تغییر کرد" in item for item in project["analysis"]["interview_analysis"]["quality_audit"]["analysis_limitations"])
	page = TestClient(app).get("/results/project-v2/concept-review")
	assert "خروجی برای بازبینی ذخیره شد، اما کامل نیست" in page.text
	assert '<th scope="col">مفهوم</th>' in page.text
	assert '<th scope="col">کدگذاری</th>' in page.text
	assert "عنوان پیشنهادی · قابل ویرایش" in page.text
	assert 'name="suggested_concepts"' in page.text
	assert 'name="suggested_codes"' in page.text


def test_v2_accepts_matching_subcategory_ids():
	project, output = make_project_and_output()
	category = output["interview_analysis"]["categories"][0]
	category["subcategories"] = [{
		"subcategory_id": "SUB01",
		"subcategory_name": "فشار زمانی",
		"definition": "فشار مرتبط با زمان کار.",
		"concept_ids": ["C001"],
		"evidence": [],
	}]
	category["subcategory_ids"] = ["SUB01"]
	parsed = parse_analysis_json(json.dumps(output, ensure_ascii=False), project)
	assert parsed["interview_analysis"]["categories"][0]["subcategory_ids"] == ["SUB01"]
	assert parsed["interview_analysis"]["categories"][0]["subcategories"] == category["subcategories"]


def test_v2_derives_subcategory_ids_when_the_optional_field_is_missing():
	project, output = make_project_and_output()
	category = output["interview_analysis"]["categories"][0]
	category["subcategories"] = [{
		"subcategory_id": "SUB01",
		"subcategory_name": "فشار زمانی",
		"definition": "فشار مرتبط با زمان کار.",
		"concept_ids": ["C001"],
		"evidence": [],
	}]
	parsed = parse_analysis_json(json.dumps(output, ensure_ascii=False), project)
	assert parsed["interview_analysis"]["categories"][0]["subcategory_ids"] == ["SUB01"]


def test_v2_rejects_mismatched_subcategory_ids_without_dropping_source_data():
	project, output = make_project_and_output()
	category = output["interview_analysis"]["categories"][0]
	category["subcategories"] = [{
		"subcategory_id": "SUB01",
		"subcategory_name": "فشار زمانی",
		"definition": "فشار مرتبط با زمان کار.",
		"concept_ids": ["C001"],
		"evidence": [],
	}]
	category["subcategory_ids"] = ["SUB02"]
	with pytest.raises(ValueError, match="subcategory_ids.*با شناسه‌های subcategories"):
		parse_analysis_json(json.dumps(output, ensure_ascii=False), project)
	assert category["subcategories"][0]["subcategory_id"] == "SUB01"


def test_v2_rejects_subcategory_id_owned_by_another_category():
	project, output = make_project_and_output()
	category = output["interview_analysis"]["categories"][0]
	category["subcategory_ids"] = ["SUB02"]
	category["subcategories"] = [{
		"subcategory_id": "SUB01",
		"subcategory_name": "فشار زمانی",
		"definition": "فشار مرتبط با زمان کار.",
		"concept_ids": ["C001"],
		"evidence": [],
	}]
	other_category = dict(category)
	other_category.update({
		"category_id": "CAT02",
		"category_name": "مقوله دیگر",
		"subcategory_ids": ["SUB02"],
		"subcategories": [{
			"subcategory_id": "SUB02",
			"subcategory_name": "مقوله دیگر",
			"definition": "تعریف دیگر.",
			"concept_ids": [],
			"evidence": [],
		}],
		"concept_ids": [],
		"evidence": [],
	})
	output["interview_analysis"]["categories"].append(other_category)
	with pytest.raises(ValueError, match="تعلق دارد، نه مقوله"):
		parse_analysis_json(json.dumps(output, ensure_ascii=False), project)


def test_v2_import_mismatch_preserves_previously_saved_analysis(monkeypatch):
	project, output = make_project_and_output()
	category = output["interview_analysis"]["categories"][0]
	category["subcategory_ids"] = ["SUB02"]
	category["subcategories"] = [{
		"subcategory_id": "SUB01",
		"subcategory_name": "فشار زمانی",
		"definition": "فشار مرتبط با زمان کار.",
		"concept_ids": ["C001"],
		"evidence": [],
	}]
	previous_analysis = {"schema_version": "1.0", "saved": True}
	project["analysis"] = previous_analysis
	monkeypatch.setattr(analysis_router, "get_project", lambda _: project)
	monkeypatch.setattr(analysis_router, "update_project", lambda *_args, **_kwargs: pytest.fail("Rejected JSON must not be stored"))
	response = TestClient(app, follow_redirects=False).post(
		"/analysis/project-v2/import",
		files={"analysis_file": ("mismatch.json", json.dumps(output, ensure_ascii=False).encode(), "application/json")},
	)
	message = parse_qs(urlparse(response.headers["location"]).query)["error"][0]
	assert response.status_code == 303
	assert "subcategory_ids" in message and "مطابقت ندارند" in message
	assert project["analysis"] is previous_analysis
	assert category["subcategories"][0]["subcategory_id"] == "SUB01"


def test_existing_database_migrates_researcher_edits_separately(tmp_path, monkeypatch):
	database_path = tmp_path / "legacy.sqlite3"
	connection = sqlite3.connect(database_path)
	connection.execute(
		"""CREATE TABLE projects (
			project_id TEXT PRIMARY KEY, title TEXT NOT NULL, topic TEXT NOT NULL DEFAULT '',
			created_at TEXT NOT NULL, updated_at TEXT NOT NULL, source_filename TEXT NOT NULL DEFAULT '',
			paragraphs_json TEXT NOT NULL DEFAULT '[]', concepts_json TEXT NOT NULL DEFAULT '[]', analysis_json TEXT
		)"""
	)
	connection.execute(
		"INSERT INTO projects (project_id, title, created_at, updated_at, analysis_json) VALUES (?, ?, ?, ?, ?)",
		("legacy", "پروژه", "now", "now", '{"schema_version":"2.0"}'),
	)
	connection.commit()
	connection.close()
	monkeypatch.setattr(database, "DATA_DIR", tmp_path)
	monkeypatch.setattr(database, "DATABASE_PATH", database_path)

	project = database.get_project("legacy")
	assert project["researcher_edits"] == {}
	database.update_project(
		"legacy",
		researcher_edits={"concept_reviews": {"C1": {"confirmed": True}}},
		raw_analysis={"schema_version": "2.0", "raw": True},
	)
	updated = database.get_project("legacy")
	assert updated["analysis"] == {"schema_version": "2.0"}
	assert updated["researcher_edits"]["concept_reviews"]["C1"]["confirmed"] is True
	assert updated["raw_analysis"]["raw"] is True