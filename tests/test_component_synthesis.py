from __future__ import annotations

import yaml
from fastapi.testclient import TestClient

from app.core import database as database_module
from app.core.database import create_project, init_db, update_project
from app.main import app
from app.repositories.synthesis_repository import (
    create_synthesis_run,
    list_stage_results,
    save_run_sources,
    save_stage_result,
)
from app.services.synthesis.collector_service import collect_run_pools
from app.services.synthesis.component_synthesis_service import (
    COMPONENTS,
    build_component_prompt,
    component_synthesis_final_data,
)


def _create_run(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("HOOSHYAR_DATA_DIR", str(data_dir))
    database_module.DATA_DIR = data_dir
    database_module.DATABASE_PATH = data_dir / "hooshyar.sqlite3"
    init_db()

    projects = []
    for index in range(1, 3):
        project = create_project(f"مصاحبه {index}", "موضوع")
        concept_id = f"C{index:03d}"
        paragraph_id = f"P{index:03d}"
        quote = f"عبارت شاهد واقعی {index}"
        analysis = {
            "schema_version": "2.0",
            "interview_analysis": {
                "categories": [{
                    "category_id": f"CAT{index:03d}",
                    "category_name": f"مقوله {index}",
                    "definition": f"تعریف مقوله {index}",
                    "concept_ids": [concept_id],
                    "evidence": [{
                        "paragraph_id": paragraph_id,
                        "exact_quote": quote,
                        "interpretation": "شاهد پشتیبان",
                    }],
                }],
                "paradigm_model": {
                    "causal_conditions": [{
                        "category_id": f"CAT{index:03d}",
                    }],
                    "contextual_conditions": [],
                    "intervening_conditions": [],
                    "action_interaction_strategies": [],
                    "consequences": [],
                },
                "central_phenomenon": {
                    "title": f"پدیده {index}",
                    "definition": f"تعریف پدیده {index}",
                    "category_id": f"CAT{index:03d}",
                    "supporting_concept_ids": [concept_id],
                    "evidence": [],
                },
                "relationships": [],
                "concept_reviews": [{
                    "concept_id": concept_id,
                    "original_concept": f"مفهوم {index}",
                    "suggested_concept": f"مفهوم {index}",
                }],
            },
        }
        update_project(
            project["project_id"],
            paragraphs=[{"paragraph_id": paragraph_id, "text": f"پیش از {quote} پس از"}],
            concepts=[{
                "concept_id": concept_id,
                "paragraph_ids": [paragraph_id],
                "original_concept": f"مفهوم {index}",
            }],
            analysis=analysis,
            researcher_edits={"concept_reviews": {concept_id: {"confirmed": True}}},
        )
        projects.append(project)

    run = create_synthesis_run("اجرای مرحله‌ای", "موضوع", expected_source_count=2)
    save_run_sources(run["run_id"], [project["project_id"] for project in projects])
    collect_run_pools(run["run_id"])
    return run


def _valid_yaml(prompt: dict) -> str:
    source_ids = [item["source_item_id"] for item in prompt["source_items"]]
    categories = []
    themes = []
    if source_ids:
        categories = [{
            "category_id": "integrated_1",
            "title": "مقوله تلفیقی",
            "definition": "تعریف تلفیقی مبتنی بر داده",
            "source_item_ids": source_ids,
        }]
        themes = [{
            "theme_id": "theme_1",
            "title": "تم کلان",
            "definition": "تعریف تم کلان",
            "integrated_category_ids": ["integrated_1"],
        }]
    return yaml.safe_dump({
        "run_id": prompt["run_id"],
        "component_key": prompt["component_key"],
        "input_hash": prompt["input_hash"],
        "integrated_categories": categories,
        "themes": themes,
    }, allow_unicode=True, sort_keys=False)


def test_six_component_steps_advance_and_build_final_model_and_table(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    client = TestClient(app, follow_redirects=False)
    preview = client.get(f"/synthesis/runs/{run['run_id']}/pool-preview")

    assert preview.status_code == 200
    assert "عبارت شاهد واقعی 1" in preview.text
    assert "c297226001df" not in preview.text
    assert f"/synthesis/runs/{run['run_id']}/component-synthesis" in preview.text

    step_page = client.get(f"/synthesis/runs/{run['run_id']}/component-synthesis")
    assert step_page.status_code == 200
    assert "گام ۱ از ۶" in step_page.text
    assert "مفهوم 1" in step_page.text
    assert "عبارت شاهد واقعی 1" in step_page.text

    for component_key, _ in COMPONENTS:
        prompt = build_component_prompt(run["run_id"], component_key)
        response = client.post(
            f"/synthesis/runs/{run['run_id']}/component-synthesis/results",
            data={
                "component_key": component_key,
                "raw_output_text": _valid_yaml(prompt),
            },
        )
        assert response.status_code == 303

    final_page = client.get(f"/synthesis/runs/{run['run_id']}/component-synthesis/final")
    assert final_page.status_code == 200
    assert "مدل پارادایمی و جدول جامع کدگذاری" in final_page.text
    assert "تم کلان" in final_page.text
    assert "مفهوم 1" in final_page.text
    final_data = component_synthesis_final_data(run["run_id"])
    assert "تم کلان" in final_data["coding_table"]["rows"][0]["dimension"]
    assert all(
        result["approval_status"] == "approved"
        for result in list_stage_results(run["run_id"])
        if result["stage_key"] == "component_synthesis"
    )

    export = client.get(
        f"/synthesis/runs/{run['run_id']}/component-synthesis/final/coding-table.csv"
    )
    assert export.status_code == 200
    assert "مفهوم" in export.content.decode("utf-8-sig")


def test_incomplete_classification_does_not_block_component_prompt(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    pool = collect_run_pools(run["run_id"])
    pool["validation_report"] = {
        "valid": False,
        "errors": [{
            "issue_type": "incomplete_classification",
            "source_project_id": pool["source_coverage"][0]["source_project_id"],
        }],
        "warnings": [],
    }
    save_stage_result(
        run["run_id"],
        "pool_extraction",
        "interview_pools",
        version=pool["version"] + 1,
        status="ready",
        input_hash=pool["input_hash"],
        output_json=pool,
        validation_report_json=pool["validation_report"],
        approval_status="approved",
    )
    client = TestClient(app)
    preview = client.get(f"/synthesis/runs/{run['run_id']}/pool-preview")
    assert "خطای یکپارچگی داده‌ها" not in preview.text
    assert "مانع ادامه نیستند" in preview.text
    assert f"/synthesis/runs/{run['run_id']}/component-synthesis" in preview.text
    assert client.get(f"/synthesis/runs/{run['run_id']}/component-synthesis").status_code == 200
    assert build_component_prompt(run["run_id"], "causal_conditions")["source_item_count"] == 2


def test_invalid_yaml_stays_on_current_step(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    prompt = build_component_prompt(run["run_id"], "causal_conditions")
    client = TestClient(app, follow_redirects=False)

    response = client.post(
        f"/synthesis/runs/{run['run_id']}/component-synthesis/results",
        data={
            "component_key": "causal_conditions",
            "raw_output_text": yaml.safe_dump({
                "run_id": run["run_id"],
                "component_key": "causal_conditions",
                "input_hash": prompt["input_hash"],
                "integrated_categories": [],
                "themes": [],
            }),
        },
    )

    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert "گام ۱ از ۶" in page.text
    assert "خروجی نیازمند اصلاح است" in page.text
