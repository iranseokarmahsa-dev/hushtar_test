"""Tests for AI JSON validation and source-ID matching."""

import json

import pytest

from app.services.analysis_service import parse_analysis_json


@pytest.fixture
def project():
	return {
		"project_id": "project-1",
		"title": "پژوهش",
		"topic": "موضوع",
		"paragraphs": [{"paragraph_id": "P0001", "text": "مدیر از فشارهای مستقیم گفت."}],
		"concepts": [{"concept_id": "C0001", "paragraph_ids": ["P0001"], "original_concept": "فشار به مدیر", "original_code": "زمینه‌ای"}],
	}


@pytest.fixture
def analysis():
	return {
		"schema_version": "1.0",
		"software": "HOOSHYAR",
		"analysis_method": "Strauss_Corbin_Grounded_Theory",
		"project_id": "project-1",
		"analysis_status": "complete",
		"analysis_metadata": {
			"total_paragraphs": 1,
			"total_concepts_received": 1,
			"total_concepts_analyzed": 1,
			"total_categories": 0,
			"total_relationships": 0,
		},
		"concept_reviews": [{
			"concept_id": "C0001", "paragraph_id": "P0001", "original_concept": "فشار به مدیر",
			"suggested_concept": "فشار بر مدیر", "concept_validity": "valid", "concept_change_required": True,
			"concept_reason": "عنوان موجزتر است", "original_code": "زمینه‌ای", "suggested_code": "شرایط علّی",
			"code_evaluation": "revise", "code_reason": "بر متن تکیه دارد",
			"evidence": [{"paragraph_id": "P0001", "exact_quote": "فشارهای مستقیم", "interpretation": "شاهد"}],
			"confidence": 0.8, "needs_researcher_review": False,
		}],
		"categories": [],
		"central_phenomenon": None,
		"paradigm_model": {
			"causal_conditions": [],
			"central_phenomenon": None,
			"contextual_conditions": [],
			"intervening_conditions": [],
			"action_interaction_strategies": [],
			"consequences": [],
		},
		"relationships": [],
		"diagram": {
			"nodes": [
				{
					"node_id": "N01", "node_type": "category", "title": "گره اول",
					"description": "شرح", "category_ids": [], "concept_ids": ["C0001"],
				},
				{
					"node_id": "N02", "node_type": "category", "title": "گره دوم",
					"description": "شرح", "category_ids": [], "concept_ids": [],
				},
			],
			"edges": [{
				"edge_id": "E01", "source": "N01", "target": "N02",
				"relationship_type": "interpretive", "label": "رابطه",
				"concept_ids": ["C0001"], "needs_researcher_review": True,
			}],
		},
		"researcher_review": [],
		"quality_audit": {
			"all_concepts_processed": True,
			"all_ids_preserved": True,
			"all_evidence_verifiable": True,
			"unsupported_relationships_detected": False,
			"contradictions_detected": False,
			"missing_data_detected": False,
			"analysis_limitations": [],
			"final_status": "complete",
		},
	}


def test_accepts_valid_json_and_source_evidence(project, analysis):
	parsed = parse_analysis_json(json.dumps(analysis, ensure_ascii=False), project)
	assert parsed["concept_reviews"][0]["concept_id"] == "C0001"


def test_rejects_modified_original_title(project, analysis):
	analysis["concept_reviews"][0]["original_concept"] = "عنوان دستکاری‌شده"
	with pytest.raises(ValueError, match="عنوان اولیه مفهوم"):
		parse_analysis_json(json.dumps(analysis, ensure_ascii=False), project)


def test_rejects_quote_not_found_in_transcript(project, analysis):
	analysis["concept_reviews"][0]["evidence"][0]["exact_quote"] = "عبارت ساختگی"
	with pytest.raises(ValueError, match="عیناً در متن پاراگراف پیدا نشد"):
		parse_analysis_json(json.dumps(analysis, ensure_ascii=False), project)


def test_evidence_interpretation_is_required(project, analysis):
	del analysis["concept_reviews"][0]["evidence"][0]["interpretation"]

	with pytest.raises(ValueError, match="ساختار JSON نامعتبر است"):
		parse_analysis_json(json.dumps(analysis, ensure_ascii=False), project)


def test_category_evidence_is_checked_against_the_transcript(project, analysis):
	analysis["categories"] = [{
		"category_id": "CAT01",
		"category_name": "مقوله",
		"definition": "تعریف",
		"category_reason": "دلیل",
		"concept_ids": ["C0001"],
		"properties": [],
		"dimensions": [],
		"evidence": [{
			"paragraph_id": "P0001",
			"exact_quote": "نقل‌قول ساختگی",
			"interpretation": "ارتباط شاهد با مقوله",
		}],
		"confidence": 0.8,
		"needs_researcher_review": False,
	}]
	analysis["analysis_metadata"]["total_categories"] = 1

	with pytest.raises(ValueError, match="عیناً در متن پاراگراف پیدا نشد"):
		parse_analysis_json(json.dumps(analysis, ensure_ascii=False), project)


def test_evidence_arrays_anywhere_require_all_three_fields(project, analysis):
	analysis["researcher_review"] = [{
		"item_type": "relationship",
		"item_id": "R01",
		"issue": "بررسی شاهد",
		"reason": "شاهد نیازمند توضیح است.",
		"suggested_action": "بازبینی",
		"evidence": [{"paragraph_id": "P0001", "exact_quote": "فشارهای مستقیم"}],
	}]

	with pytest.raises(ValueError, match="هر شاهد باید paragraph_id"):
		parse_analysis_json(json.dumps(analysis, ensure_ascii=False), project)


def test_rejects_diagram_edges_that_reference_unknown_nodes(project, analysis):
	analysis["diagram"]["edges"][0]["target"] = "N99"
	with pytest.raises(ValueError, match="به گره ناموجود"):
		parse_analysis_json(json.dumps(analysis, ensure_ascii=False), project)


def test_requires_paradigm_model_and_explicit_diagram(project, analysis):
	del analysis["diagram"]
	del analysis["paradigm_model"]["consequences"]
	with pytest.raises(ValueError, match="ساختار JSON نامعتبر است"):
		parse_analysis_json(json.dumps(analysis, ensure_ascii=False), project)


def test_rejects_inconsistent_analysis_counts(project, analysis):
	analysis["analysis_metadata"]["total_concepts_analyzed"] = 2
	with pytest.raises(ValueError, match="total_concepts_analyzed"):
		parse_analysis_json(json.dumps(analysis, ensure_ascii=False), project)


def test_rejects_complete_status_with_quality_warnings(project, analysis):
	analysis["quality_audit"]["missing_data_detected"] = True
	with pytest.raises(ValueError, match="نمی‌تواند کامل باشد"):
		parse_analysis_json(json.dumps(analysis, ensure_ascii=False), project)


def test_accepts_incomplete_status_when_quality_audit_is_consistent(project, analysis):
	analysis["analysis_status"] = "incomplete"
	analysis["quality_audit"]["final_status"] = "incomplete"
	analysis["quality_audit"]["missing_data_detected"] = True
	analysis["quality_audit"]["analysis_limitations"] = ["موضوع پژوهش مبهم است."]

	parsed = parse_analysis_json(json.dumps(analysis, ensure_ascii=False), project)

	assert parsed["analysis_status"] == "incomplete"


def test_rejects_reviews_without_title_or_code_reason(project, analysis):
	analysis["concept_reviews"][0]["suggested_concept"] = ""
	with pytest.raises(ValueError, match="عنوان پیشنهادی مفهوم"):
		parse_analysis_json(json.dumps(analysis, ensure_ascii=False), project)
