from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import yaml

from app.main import app
from app.routers import analysis as analysis_router
from app.routers import projects as projects_router
from app.services.analysis_service import (
	combine_stage_outputs,
	parse_analysis_markdown,
	parse_stage_markdown,
)
from app.services.export_service import create_excel, create_markdown_report
from app.services.prompt_builder import build_prompts, parse_project_markdown


def _project():
	return {
		"project_id": "project-stage",
		"interview_id": "interview-stage",
		"title": "پژوهش آزمایشی",
		"topic": "موضوع",
		"paragraphs": [{"paragraph_id": "P001", "text": "مدیر گفت: فشار کاری زیاد است."}],
		"concepts": [{
			"concept_id": "C001",
			"paragraph_ids": ["P001"],
			"original_concept": "فشار کاری",
			"original_code": "شرایط زمینه‌ای",
		}],
		"stage_outputs": {},
	}


def _stage1(project):
	return {
		"schema_version": "HOOSHYAR_STAGE1_1.0",
		"project_id": project["project_id"],
		"interview_id": project["interview_id"],
		"source_scope": {
			"total_paragraphs": 1,
			"total_concepts_received": 1,
			"total_concepts_reviewed": 1,
		},
		"concept_reviews": [{
			"concept_id": "C001",
			"paragraph_id": "P001",
			"suggested_concept": "فشار کاری",
			"concept_validity": "valid",
			"concept_change_required": False,
			"concept_reason": "عنوان با متن سازگار است.",
			"suggested_code": "شرایط زمینه‌ای",
			"code_evaluation": "confirmed",
			"code_reason": "متن فشار در بافت کار را شرح می‌دهد.",
			"evidence": [{
				"paragraph_id": "P001",
				"exact_quote": "فشار کاری زیاد است",
				"interpretation": "شاهد بیان فشار کاری است.",
			}],
			"overlap": {
				"has_overlap": False,
				"related_concept_ids": [],
				"explanation": "هم‌پوشانی دیده نشد.",
			},
			"confidence": 0.8,
			"needs_researcher_review": False,
		}],
	}


def _stage2(project):
	return {
		"schema_version": "HOOSHYAR_STAGE2_1.0",
		"project_id": project["project_id"],
		"interview_id": project["interview_id"],
		"interview_analysis": {
			"categories": [{
				"category_id": "CAT01",
				"category_name": "فشارهای کاری",
				"definition": "تجربه فشار در محیط کار.",
				"category_reason": "مضمون در متن مصاحبه آمده است.",
				"concept_ids": ["C001"],
				"subcategories": [],
				"properties": [],
				"dimensions": [],
				"evidence": [{
					"paragraph_id": "P001",
					"exact_quote": "فشار کاری زیاد است",
					"interpretation": "شاهد مقوله است.",
				}],
				"confidence": 0.8,
				"needs_researcher_review": False,
			}],
			"unclassified_concepts": [],
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
		},
	}


def _markdown(payload, fenced=True):
	raw = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
	return f"# خروجی مرحله\n\n```yaml\n{raw}```" if fenced else raw


def test_prompts_are_independent_and_stage2_omits_initial_codes():
	project = _project()
	prompts = build_prompts(project)
	assert "initial_code: شرایط زمینه‌ای" in prompts["stage1"]
	assert "initial_code" not in prompts["stage2"]
	assert "کدهای اولیه پژوهشگر را مبنای طبقه‌بندی قرار ندهید" in prompts["stage2"]
	assert "concept_reviews" not in prompts["stage2"].split("## داده‌های پروژه", 1)[1]
	assert "paragraphs:" in prompts["stage1"]
	assert "\nconcepts:" not in prompts["stage1"].split("## داده‌های پروژه", 1)[1]


def test_stage_markdown_accepts_raw_yaml_and_preserves_unverifiable_quotes_for_audit():
	project = _project()
	first = _stage1(project)
	second = _stage2(project)
	assert parse_stage_markdown(_markdown(first, fenced=False), project, "stage1") == first
	assert parse_stage_markdown(_markdown(second), project, "stage2") == second
	first["concept_reviews"][0]["evidence"][0]["exact_quote"] = "فشار کاری زیادی است"
	parsed = parse_stage_markdown(_markdown(first), project, "stage1")
	assert parsed["concept_reviews"][0]["evidence"][0]["exact_quote"] == "فشار کاری زیادی است"
	result = combine_stage_outputs(project, parsed, second)
	warning = result["interview_analysis"]["quality_audit"]["evidence_warnings"][0]
	assert warning["status"] == "fuzzy_match"
	assert warning["similarity"] >= 90
	assert result["concept_reviews"][0]["needs_researcher_review"] is True
	assert result["analysis_status"] == "incomplete"
	assert result["concept_reviews"][0]["evidence"][0]["exact_quote"] == "فشار کاری زیادی است"


def test_stage1_accepts_renderer_escaped_text_and_normalizes_revised_enum():
	project = _project()
	payload = _stage1(project)
	payload["concept_reviews"][0]["code_evaluation"] = "revised"
	raw_yaml = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
	escaped = raw_yaml.replace(" ", "&#x20;").replace("-", "\\-")
	escaped = escaped.replace("\n", "\\\n")
	parsed = parse_stage_markdown(escaped, project, "stage1")
	assert parsed["concept_reviews"][0]["code_evaluation"] == "revise"


def test_yaml_parser_reports_empty_input_and_structural_line_column():
	project = _project()
	for text, expected in (("", "متن ورودی خالی است"), ("```yaml\ninvalid: [\n```", "خط 2، ستون")):
		try:
			parse_stage_markdown(text, project, "stage1")
		except ValueError as error:
			assert expected in str(error)
		else:
			raise AssertionError("Invalid or empty YAML input must return a user-facing error.")


def test_invalid_stage1_record_is_isolated_and_reported():
	project = _project()
	project["concepts"].append({
		"concept_id": "C002",
		"paragraph_ids": ["P001"],
		"original_concept": "مدیریت فشار",
		"original_code": "راهبردهای کنش و تعامل",
	})
	stage1 = _stage1(project)
	stage1["source_scope"]["total_concepts_received"] = 2
	stage1["source_scope"]["total_concepts_reviewed"] = 2
	bad_review = dict(stage1["concept_reviews"][0])
	del bad_review["code_reason"]
	good_review = {**stage1["concept_reviews"][0], "concept_id": "C002"}
	stage1["concept_reviews"] = [bad_review, good_review]
	parsed = parse_stage_markdown(_markdown(stage1), project, "stage1")
	assert [item["concept_id"] for item in parsed["concept_reviews"]] == ["C002"]
	assert parsed["_record_errors"][0]["record_id"] == "C001"
	stage2 = _stage2(project)
	stage2["interview_analysis"]["categories"][0]["concept_ids"] = ["C001", "C002"]
	result = combine_stage_outputs(project, parsed, stage2)
	assert result["interview_analysis"]["categories"][0]["category_id"] == "CAT01"
	assert result["interview_analysis"]["quality_audit"]["record_errors"]
	assert result["analysis_status"] == "incomplete"


def test_invalid_evidence_item_isolated_without_dropping_its_concept():
	project = _project()
	stage1 = _stage1(project)
	stage1["concept_reviews"][0]["evidence"].append({
		"paragraph_id": "P001",
		"exact_quote": "نقل‌قول بدون تفسیر",
	})
	parsed = parse_stage_markdown(_markdown(stage1), project, "stage1")
	assert [item["concept_id"] for item in parsed["concept_reviews"]] == ["C001"]
	assert len(parsed["concept_reviews"][0]["evidence"]) == 1
	assert parsed["concept_reviews"][0]["needs_researcher_review"] is True
	assert parsed["_record_errors"][0]["record_id"] == "C001"
	result = combine_stage_outputs(project, parsed, _stage2(project))
	assert result["interview_analysis"]["quality_audit"]["record_errors"]
	assert result["analysis_status"] == "incomplete"


def test_invalid_stage2_category_does_not_discard_valid_categories():
	project = _project()
	stage2 = _stage2(project)
	stage2["interview_analysis"]["categories"].append({"category_id": "BROKEN"})
	parsed = parse_stage_markdown(_markdown(stage2), project, "stage2")
	assert [item["category_id"] for item in parsed["interview_analysis"]["categories"]] == ["CAT01"]
	assert any(item["record_id"] == "BROKEN" for item in parsed["_record_errors"])
	result = combine_stage_outputs(project, _stage1(project), parsed)
	assert result["interview_analysis"]["categories"][0]["category_id"] == "CAT01"
	assert result["interview_analysis"]["quality_audit"]["record_errors"]
	assert result["analysis_status"] == "incomplete"


def test_python_audit_checks_concept_id_set_not_only_record_count():
	project = _project()
	stage1 = _stage1(project)
	stage1["concept_reviews"][0]["concept_id"] = "C999"
	result = combine_stage_outputs(project, stage1, _stage2(project))
	audit = result["interview_analysis"]["quality_audit"]
	assert result["source_scope"]["total_concepts_reviewed"] == 1
	assert audit["all_concepts_reviewed"] is False
	assert audit["all_ids_preserved"] is False
	assert audit["missing_concept_review_ids"] == ["C001"]
	assert audit["validation_issues"]


def test_parent_and_subcategory_concept_repetition_is_not_a_duplicate_assignment():
	project = _project()
	stage2 = _stage2(project)
	stage2["interview_analysis"]["categories"][0]["subcategories"] = [{
		"subcategory_id": "CAT01_S01",
		"subcategory_name": "فشار محیط کار",
		"definition": "تجربه فشار در محیط کار.",
		"concept_ids": ["C001"],
		"evidence": [{
			"paragraph_id": "P001",
			"exact_quote": "فشار کاری زیاد است",
			"interpretation": "شاهد زیرمقوله است.",
		}],
	}]
	result = combine_stage_outputs(project, _stage1(project), stage2)
	audit = result["interview_analysis"]["quality_audit"]
	assert audit["all_ids_preserved"] is True
	assert audit["validation_issues"] == []


def test_python_audit_reports_assignment_to_multiple_categories():
	project = _project()
	stage2 = _stage2(project)
	stage2["interview_analysis"]["categories"].append({
		**stage2["interview_analysis"]["categories"][0],
		"category_id": "CAT02",
		"category_name": "مقوله دوم",
	})
	result = combine_stage_outputs(project, _stage1(project), stage2)
	audit = result["interview_analysis"]["quality_audit"]
	assert audit["all_ids_preserved"] is False
	assert any("C001" in issue and "بیش از یک بار" in issue for issue in audit["validation_issues"])


def test_python_audit_checks_property_dimension_traceability():
	project = _project()
	stage2 = _stage2(project)
	category = stage2["interview_analysis"]["categories"][0]
	evidence = [{
		"paragraph_id": "P001",
		"exact_quote": "فشار کاری زیاد است",
		"interpretation": "شاهد ویژگی است.",
	}]
	category["properties"] = [{
		"property_id": "CAT01_PROP01",
		"property_name": "شدت فشار",
		"description": "میزان فشار کاری.",
		"concept_ids": ["C001"],
		"evidence": evidence,
	}]
	category["dimensions"] = [{
		"property_id": "CAT01_PROP01",
		"property_name": "شدت فشار",
		"dimension_range": "زیاد",
		"concept_ids": ["C001"],
		"evidence": evidence,
	}]
	assert parse_stage_markdown(_markdown(stage2), project, "stage2") == stage2
	result = combine_stage_outputs(project, _stage1(project), stage2)
	assert result["interview_analysis"]["quality_audit"]["validation_issues"] == []
	category["dimensions"][0]["property_id"] = "MISSING"
	result = combine_stage_outputs(project, _stage1(project), stage2)
	assert any(
		"به شناسه ویژگی موجودی متصل نیست" in issue
		for issue in result["interview_analysis"]["quality_audit"]["validation_issues"]
	)


def test_python_injects_the_single_source_central_phenomenon_into_the_model():
	project = _project()
	stage2 = _stage2(project)
	phenomenon = {
		"identified": True,
		"category_id": "CAT01",
		"title": "مواجهه با فشار کاری",
		"definition": "فرایند مواجهه با فشار کاری.",
		"selection_reason": "این مضمون نقش محوری دارد.",
		"supporting_category_ids": ["CAT01"],
		"supporting_concept_ids": ["C001"],
		"evidence": [{
			"paragraph_id": "P001",
			"exact_quote": "فشار کاری زیاد است",
			"interpretation": "شاهد پدیده محوری است.",
		}],
		"alternative_interpretations": [],
		"confidence": 0.8,
		"needs_researcher_review": False,
	}
	stage2["interview_analysis"]["central_phenomenon"] = phenomenon
	stage2["interview_analysis"]["paradigm_model"]["central_phenomenon"] = None
	parsed = parse_stage_markdown(_markdown(stage2), project, "stage2")
	assert parsed["interview_analysis"]["paradigm_model"]["central_phenomenon"] == phenomenon


def test_stage1_isolates_a_record_with_values_outside_allowed_enums():
	project = _project()
	payload = _stage1(project)
	payload["concept_reviews"][0]["concept_validity"] = "invalid"
	parsed = parse_stage_markdown(_markdown(payload), project, "stage1")
	assert parsed["concept_reviews"] == []
	assert parsed["_record_errors"][0]["record_id"] == "C001"
	assert "concept_validity" in parsed["_record_errors"][0]["reason"]


def test_combination_adds_original_fields_and_python_audit_only():
	project = _project()
	result = combine_stage_outputs(project, _stage1(project), _stage2(project))
	assert result["concept_reviews"][0]["original_concept"] == "فشار کاری"
	assert result["concept_reviews"][0]["original_code"] == "شرایط زمینه‌ای"
	audit = result["interview_analysis"]["quality_audit"]
	assert audit["final_status"] == result["analysis_status"] == "complete"
	assert audit["all_concepts_reviewed"] is True
	assert audit["validation_issues"] == []
	project["analysis"] = result
	markdown = create_markdown_report(project)
	assert parse_analysis_markdown(markdown, project)["analysis_status"] == "complete"
	workbook = load_workbook(BytesIO(create_excel(project)))
	assert {"ممیزی کیفیت", "هشدار شواهد", "خطاهای رکوردی", "مدل پارادایمی"} <= set(workbook.sheetnames)
	assert workbook["مقوله‌ها"]["B2"].value == "فشارهای کاری"


def test_unmatched_evidence_is_reported_without_discarding_healthy_records():
	project = _project()
	stage1 = _stage1(project)
	stage2 = _stage2(project)
	stage1["concept_reviews"][0]["evidence"][0]["exact_quote"] = "عبارت کاملاً ساختگی"
	result = combine_stage_outputs(project, stage1, stage2)
	audit = result["interview_analysis"]["quality_audit"]
	assert audit["all_evidence_verifiable"] is False
	assert audit["evidence_warnings"][0]["status"] == "unmatched"
	assert result["interview_analysis"]["categories"][0]["category_id"] == "CAT01"
	assert result["concept_reviews"][0]["evidence"][0]["exact_quote"] == "عبارت کاملاً ساختگی"
	assert result["analysis_status"] == "incomplete"
	project["analysis"] = result
	report = parse_analysis_markdown(create_markdown_report(project), project)
	assert report["interview_analysis"]["quality_audit"]["evidence_warnings"][0]["status"] == "unmatched"


def test_python_audit_reports_missing_ids_and_code_model_mismatch():
	project = _project()
	stage1 = _stage1(project)
	stage2 = _stage2(project)
	stage1["concept_reviews"] = []
	stage1["source_scope"]["total_concepts_reviewed"] = 0
	stage2["interview_analysis"]["categories"][0]["concept_ids"] = []
	stage2["interview_analysis"]["paradigm_model"]["causal_conditions"] = [{
		"category_id": "CAT01",
		"concept_ids": ["C001"],
		"explanation": "مؤلفه نمونه",
		"evidence": [],
	}]
	result = combine_stage_outputs(project, stage1, stage2)
	audit = result["interview_analysis"]["quality_audit"]
	assert result["analysis_status"] == "incomplete"
	assert audit["missing_concept_review_ids"] == ["C001"]
	assert audit["missing_category_assignment_ids"] == ["C001"]
	second_result = combine_stage_outputs(project, _stage1(project), stage2)
	assert second_result["interview_analysis"]["quality_audit"]["code_model_mismatches"][0]["concept_id"] == "C001"


def test_project_markdown_reads_data_section_not_earlier_yaml_block():
	raw = """# نمونه

```yaml
project_id: wrong
```

## داده‌های پروژه
```yaml
project_id: project-stage
interview_id: interview-stage
title: پژوهش
topic: موضوع
paragraphs:
  - paragraph_id: P001
    text: متن اصلی
    concepts:
      - concept_id: C001
        title: عنوان
        initial_code: کد
```
"""
	project = parse_project_markdown(raw)
	assert project["project_id"] == "project-stage"
	assert project["concepts"] == [{
		"concept_id": "C001",
		"paragraph_ids": ["P001"],
		"original_concept": "عنوان",
		"original_code": "کد",
	}]


def test_stage_import_stores_independent_outputs_then_combines(monkeypatch):
	project = _project()
	monkeypatch.setattr(analysis_router, "get_project", lambda _: project)
	monkeypatch.setattr(
		analysis_router,
		"update_project",
		lambda _project_id, **changes: project.update(changes) or project,
	)
	monkeypatch.setattr(analysis_router, "mark_synthesis_runs_stale_for_source", lambda _: None)
	client = TestClient(app, follow_redirects=False)
	prompt_page = client.get("/analysis/project-stage")
	assert prompt_page.status_code == 200
	assert "دو پرامپت مستقل" in prompt_page.text
	assert "/prompt/stage1.md" in prompt_page.text
	assert "/prompt/stage2.md" in prompt_page.text
	first = client.post(
		"/analysis/project-stage/import/stage1",
		data={"analysis_markdown": _markdown(_stage1(project))},
	)
	assert first.status_code == 303
	assert "stage1" in project["stage_outputs"] and "stage2" not in project["stage_outputs"]
	assert project.get("analysis") is None

	second = client.post(
		"/analysis/project-stage/import/stage2",
		data={"analysis_markdown": _markdown(_stage2(project))},
	)
	assert second.status_code == 303
	assert second.headers["location"] == "/results/project-stage/concept-review"
	assert project["analysis"]["analysis_status"] == "complete"
	assert set(project["raw_analysis"]) == {"stage1", "stage2"}


def test_markdown_input_endpoint_rejects_other_project_id(monkeypatch):
	project = _project()
	monkeypatch.setattr(projects_router, "get_project", lambda _: project)
	content = """## داده‌های پروژه
```yaml
project_id: other
interview_id: i
title: title
paragraphs: []
```
""".encode()
	response = TestClient(app, follow_redirects=False).post(
		"/projects/project-stage/markdown",
		files={"input_file": ("input.md", content, "text/markdown")},
	)
	assert response.status_code == 400


def test_markdown_input_endpoint_imports_paragraph_owned_concepts(monkeypatch):
	project = _project()
	monkeypatch.setattr(projects_router, "get_project", lambda _: project)
	monkeypatch.setattr(
		projects_router,
		"update_project",
		lambda _project_id, **changes: project.update(changes) or project,
	)
	content = """## داده‌های پروژه
```yaml
project_id: project-stage
interview_id: interview-markdown
title: پژوهش تازه
topic: موضوع تازه
paragraphs:
  - paragraph_id: P900
    text: متن شاهد
    concepts:
      - concept_id: C900
        title: مفهوم تازه
        initial_code: کد اولیه
```
""".encode()
	response = TestClient(app, follow_redirects=False).post(
		"/projects/project-stage/markdown",
		files={"input_file": ("input.md", content, "text/markdown")},
	)
	assert response.status_code == 303
	assert project["interview_id"] == "interview-markdown"
	assert project["concepts"][0]["concept_id"] == "C900"
	assert project["concepts"][0]["paragraph_ids"] == ["P900"]
