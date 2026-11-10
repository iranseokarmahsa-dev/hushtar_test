import pytest

from app.services.synthesis.final_model_validator import validate_final_model
from app.services.synthesis.final_quality_audit import COMPONENTS


def _validation_case():
    source_rows = [
        {
            "source_project_id": f"project-{index}",
            "snapshot": {
                "interview_id": f"interview-{index}",
                "paragraphs": [
                    {
                        "paragraph_id": f"P{index}",
                        "text": "a verified quote from the interview",
                    }
                ],
            },
        }
        for index in range(13)
    ]
    pool_keys = (
        "causal_conditions",
        "contextual_conditions",
        "intervening_conditions",
        "action_interaction_strategies",
        "consequences",
        "central_phenomenon",
    )
    inputs = {
        "source_rows": source_rows,
        "nodes": [
            {"node_id": "theme-1", "node_type": "theme"},
            {"node_id": "unit-independent", "node_type": "category_unit"},
        ],
        "evidence_by_id": {
            "evidence-1": {
                "quote": "a verified quote",
                "source_project_id": "project-0",
                "source_interview_id": "interview-0",
                "paragraph_id": "P0",
            }
        },
        "evidence_to_source_items": {"evidence-1": {"item-theme"}},
        "unit_to_node": {
            "unit-theme": "theme-1",
            "unit-independent": "unit-independent",
        },
        "pool_result": {
            "output_json": {
                "pools": {
                    key: (
                        [{"source_item_id": "item-theme"}]
                        if key == "causal_conditions"
                        else [{"source_item_id": "item-independent"}]
                        if key == "consequences"
                        else [{"source_item_id": "item-central"}]
                        if key == "central_phenomenon"
                        else []
                    )
                    for key in pool_keys
                }
            }
        },
        "phase_five_result": {"version": 2, "source_phase_version": 1},
        "pool_version": 3,
        "source_relationships": [],
    }
    components = [
        {
            "component_key": component_key,
            "title": title,
            "display_order": index,
            "node_ids": members,
        }
        for index, (component_key, title, members) in enumerate((
            ("causal_conditions", "شرایط علّی", ["theme-1"]),
            ("central_phenomenon", "پدیده محوری", ["central-1"]),
            ("contextual_conditions", "شرایط زمینه‌ای", []),
            ("intervening_conditions", "شرایط مداخله‌گر", []),
            ("action_interaction_strategies", "راهبردها", []),
            ("consequences", "پیامدها", ["unit-independent"]),
        ))
    ]
    nodes = [
        {
            "node_id": "theme-1",
            "node_type": "theme",
            "component_key": "causal_conditions",
            "title": "تم تأییدشده",
            "definition": "تعریف تم",
            "theme_id": "theme-1",
            "researcher_approved": True,
            "source_project_ids": ["project-0"],
            "source_item_ids": ["item-theme"],
            "category_unit_ids": ["unit-theme"],
            "source_concept_ids": ["concept-1"],
            "evidence_ref_ids": ["evidence-1"],
        },
        {
            "node_id": "central-1",
            "node_type": "central_phenomenon",
            "component_key": "central_phenomenon",
            "title": "پدیده محوری",
            "definition": "تعریف پدیده",
            "researcher_approved": True,
            "source_project_ids": ["project-0"],
            "source_item_ids": ["item-central"],
            "category_unit_ids": ["unit-theme"],
            "source_concept_ids": [],
            "evidence_ref_ids": [],
        },
        {
            "node_id": "unit-independent",
            "node_type": "independent_category",
            "component_key": "consequences",
            "title": "مقوله مستقل",
            "definition": "تعریف مقوله",
            "researcher_approved": True,
            "source_project_ids": ["project-0"],
            "source_item_ids": ["item-independent"],
            "category_unit_ids": ["unit-independent"],
            "source_concept_ids": [],
            "evidence_ref_ids": [],
        },
    ]
    model = {
        "run_id": "run-1",
        "source_interview_ids": [f"interview-{index}" for index in range(13)],
        "nodes": nodes,
        "edges": [],
        "edge_source_relationship_ids": [],
        "components": components,
        "evidence_index": {
            "evidence-1": {
                "evidence_ref_id": "evidence-1",
                "source_project_id": "project-0",
                "source_interview_id": "interview-0",
                "paragraph_id": "P0",
                "evidence_text": "a verified quote",
                "source_item_ids": ["item-theme"],
                "source_concept_ids": ["concept-1"],
                "valid": True,
            }
        },
        "source_traceability": {
            "sources": [
                {"source_project_id": f"project-{index}"}
                for index in range(13)
            ],
            "contradictions": [],
        },
        "quality_audit": {"input_source_count": 13, "errors": []},
        "central_phenomenon_node_id": "central-1",
        "source_phase_versions": {
            "pool_extraction": 3,
            "category_grouping": 1,
            "theme_synthesis": 2,
            "phenomenon_synthesis": 4,
            "relationship_synthesis": 5,
        },
    }
    phenomenon_result = {
        "version": 4,
        "output_json": {
            "central_phenomenon": {
                "review_status": "approved",
                "selected": True,
            }
        },
    }
    relationship_result = {
        "version": 5,
        "output_json": {"relationships": []},
    }
    return model, inputs, phenomenon_result, relationship_result


def _validate(model, inputs, phenomenon, relationships):
    return validate_final_model(
        model,
        inputs=inputs,
        phenomenon_result=phenomenon,
        relationship_result=relationships,
    )


def test_final_model_audit_accepts_six_components_empty_ones_and_all_13_interviews():
    model, inputs, phenomenon, relationships = _validation_case()

    report = _validate(model, inputs, phenomenon, relationships)

    assert report["valid"], report["errors"]
    assert [item["component_key"] for item in model["components"]] == [
        key for key, _title in COMPONENTS
    ]
    assert len(model["source_interview_ids"]) == 13
    assert model["components"][2]["node_ids"] == []
    assert model["components"][3]["node_ids"] == []
    assert model["components"][4]["node_ids"] == []
    assert {node["node_type"] for node in model["nodes"]} == {
        "theme",
        "independent_category",
        "central_phenomenon",
    }
    assert model["quality_audit"]["status"] == "valid"


def test_final_model_validator_rejects_unapproved_central_phenomenon():
    model, inputs, phenomenon, relationships = _validation_case()
    phenomenon["output_json"]["central_phenomenon"]["review_status"] = "pending"

    report = _validate(model, inputs, phenomenon, relationships)

    assert not report["valid"]
    assert "central_phenomenon_not_selected_and_approved" in {
        item["issue_type"] for item in report["errors"]
    }


def test_final_model_validator_rejects_unapproved_relationship():
    model, inputs, phenomenon, relationships = _validation_case()
    relationships["output_json"]["relationships"] = [{
        "relationship_id": "relationship-1",
        "review_status": "pending",
        "source_node_id": "theme-1",
        "target_node_id": "central-1",
        "relationship_type": "direct",
        "direction": "source_to_target",
        "description": "رابطه تأییدنشده",
        "reasoning": "استدلال",
        "supporting_source_relationship_ids": [],
        "source_item_ids": ["item-theme"],
        "evidence_ref_ids": ["evidence-1"],
        "researcher_approved": False,
        "source_phase": "relationship_synthesis",
    }]
    model["edges"] = [{
        "edge_id": "edge-1",
        "relationship_id": "relationship-1",
        "source_node_id": "theme-1",
        "target_node_id": "central-1",
        "relationship_type": "direct",
        "direction": "source_to_target",
        "description": "رابطه تأییدنشده",
        "reasoning": "استدلال",
        "supporting_source_relationship_ids": [],
        "source_item_ids": ["item-theme"],
        "evidence_ref_ids": ["evidence-1"],
        "researcher_approved": False,
        "source_phase": "relationship_synthesis",
    }]

    report = _validate(model, inputs, phenomenon, relationships)

    assert not report["valid"]
    assert "approved_relationship_coverage_mismatch" in {
        item["issue_type"] for item in report["errors"]
    }
    assert "unapproved_edge" in {item["issue_type"] for item in report["errors"]}


def test_final_model_validator_rejects_invalid_edge_id():
    model, inputs, phenomenon, relationships = _validation_case()
    relation = {
        "relationship_id": "relationship-1",
        "review_status": "approved",
        "source_node_id": "theme-1",
        "target_node_id": "central-1",
        "relationship_type": "direct",
        "direction": "source_to_target",
        "description": "رابطه تأییدشده",
        "reasoning": "استدلال",
        "supporting_source_relationship_ids": ["source-rel-1"],
    }
    relationships["output_json"]["relationships"] = [relation]
    inputs["source_relationships"] = [{
        "source_relationship_id": "source-rel-1",
        "evidence_ref_ids": ["evidence-1"],
    }]
    model["edges"] = [{
        "edge_id": "",
        "relationship_id": "relationship-1",
        "source_node_id": "theme-1",
        "target_node_id": "central-1",
        "relationship_type": "direct",
        "direction": "source_to_target",
        "description": "رابطه تأییدشده",
        "reasoning": "استدلال",
        "supporting_source_relationship_ids": ["source-rel-1"],
        "source_item_ids": ["item-theme"],
        "evidence_ref_ids": ["evidence-1"],
        "researcher_approved": True,
        "source_phase": "relationship_synthesis",
    }]
    model["edge_source_relationship_ids"] = ["source-rel-1"]

    report = _validate(model, inputs, phenomenon, relationships)

    assert not report["valid"]
    assert "invalid_or_duplicate_edge_id" in {
        item["issue_type"] for item in report["errors"]
    }


def test_final_model_validator_rejects_missing_evidence_and_untraceable_quotes():
    model, inputs, phenomenon, relationships = _validation_case()
    del model["evidence_index"]["evidence-1"]
    report = _validate(model, inputs, phenomenon, relationships)
    assert not report["valid"]
    assert "evidence_coverage_mismatch" in {
        item["issue_type"] for item in report["errors"]
    }

    model, inputs, phenomenon, relationships = _validation_case()
    model["evidence_index"]["evidence-1"]["evidence_text"] = "invented quote"
    report = _validate(model, inputs, phenomenon, relationships)
    assert not report["valid"]
    assert "evidence_index_snapshot_mismatch" in {
        item["issue_type"] for item in report["errors"]
    }
    assert "evidence_not_traceable_to_source_interview" in {
        item["issue_type"] for item in report["errors"]
    }


def test_final_model_validator_rejects_source_phase_version_mismatch():
    model, inputs, phenomenon, relationships = _validation_case()
    model["source_phase_versions"]["pool_extraction"] = 99

    report = _validate(model, inputs, phenomenon, relationships)

    assert not report["valid"]
    assert "source_phase_version_mismatch" in {
        item["issue_type"] for item in report["errors"]
    }
