from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from app.core import database as database_module
from app.core.database import create_project, delete_project, init_db, update_project
from app.main import app
from app.repositories.synthesis_repository import (
    create_synthesis_run,
    get_run_sources,
    list_stage_results,
    save_run_sources,
)
from app.services.synthesis.pool_builder_service import COMPONENT_KEYS
from app.services.synthesis.collector_service import collect_run_pools
from app.services.synthesis.pool_validator import validate_pool_output
from app.services.synthesis.review_service import record_pool_review_decision


def _reset_db(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("HOOSHYAR_DATA_DIR", str(data_dir))
    database_module.DATA_DIR = data_dir
    database_module.DATABASE_PATH = data_dir / "hooshyar.sqlite3"
    init_db()


def _project_payload(project_id: str, index: int, *, category_id: str = "CAT01") -> dict:
    paragraph_id = f"P{index:03d}"
    quote = f"synthetic interview evidence {index}"
    category = {
        "category_id": category_id,
        "category_name": f"Original {index}",
        "definition": f"Original definition {index}",
        "category_reason": "synthetic reason",
        "concept_ids": [f"C{index:03d}"],
        "subcategories": [{"subcategory_id": f"SUB{index:03d}", "subcategory_name": "sub", "definition": "sub definition"}],
        "properties": [{"name": "property", "value": "value"}],
        "dimensions": [{"name": "dimension", "value": "range"}],
        "evidence": [{"paragraph_id": paragraph_id, "exact_quote": quote, "interpretation": "synthetic evidence"}],
        "confidence": 0.8,
    }
    model = {key: [] for key in COMPONENT_KEYS[:-1]}
    model["causal_conditions"] = [{"category_id": category_id, "evidence": deepcopy(category["evidence"])}]
    return {
        "project_id": project_id,
        "title": f"Interview {index}",
        "paragraphs": [{"paragraph_id": paragraph_id, "text": f"context {quote} after"}],
        "analysis": {"interview_analysis": {
            "categories": [category],
            "paradigm_model": model,
            "central_phenomenon": {
                "title": f"Phenomenon {index}",
                "definition": "phenomenon definition",
                "evidence": deepcopy(category["evidence"]),
            },
            "relationships": [],
        }},
        "researcher_edits": {
            "interview_analysis": {
                "confirmed": True,
                "categories": {category_id: {"category_name": f"Approved {index}", "definition": f"Approved definition {index}"}},
            }
        },
    }


def _make_run(monkeypatch, tmp_path, count: int):
    _reset_db(monkeypatch, tmp_path)
    projects = []
    for index in range(1, count + 1):
        project = create_project(f"Interview {index}", "topic")
        payload = _project_payload(project["project_id"], index)
        project.update(payload)
        update_project(
            project["project_id"], paragraphs=project["paragraphs"],
            analysis=project["analysis"], researcher_edits=project["researcher_edits"],
        )
        projects.append(project)
    run = create_synthesis_run("Synthetic run", "topic", expected_source_count=count)
    save_run_sources(run["run_id"], [project["project_id"] for project in projects])
    return run, projects


def test_collects_all_snapshot_sources_and_six_pools(monkeypatch, tmp_path):
    run, projects = _make_run(monkeypatch, tmp_path, 5)
    from app.core.database import _connect

    with _connect() as connection:
        connection.execute(
            "UPDATE synthesis_runs SET selected_source_count = ? WHERE run_id = ?",
            (len(projects) + 1, run["run_id"]),
        )
    for project in projects:
        delete_project(project["project_id"])

    result = collect_run_pools(run["run_id"])

    assert result["metadata"]["source_count"] == 5
    assert result["metadata"]["registered_source_count"] == 5
    assert result["metadata"]["processed_source_count"] == 5
    assert set(result["pools"]) == set(COMPONENT_KEYS)
    assert len(result["pools"]["causal_conditions"]) == 5
    assert len(result["pools"]["central_phenomenon"]) == 5
    assert result["pools"]["contextual_conditions"] == []
    assert all(item["researcher_approved_category_name"].startswith("Approved") for item in result["pools"]["causal_conditions"])
    assert len(result["evidence"]) == 5
    assert validate_pool_output(result)["valid"] is True


def test_deterministic_global_ids_and_original_data_preserved(monkeypatch, tmp_path):
    run, _ = _make_run(monkeypatch, tmp_path, 2)

    first = collect_run_pools(run["run_id"], persist=False)
    second = collect_run_pools(run["run_id"], persist=False)

    ids_first = [item["source_item_id"] for item in first["pools"]["causal_conditions"]]
    ids_second = [item["source_item_id"] for item in second["pools"]["causal_conditions"]]
    assert ids_first == ids_second
    assert first["pools"]["causal_conditions"][0]["source_category_id"] == "CAT01"
    assert first["pools"]["causal_conditions"][0]["original_category_name"].startswith("Original")
    assert first["pools"]["causal_conditions"][0]["researcher_approved_category_name"].startswith("Approved")
    assert ids_first[0] != ids_first[1]
    assert "::causal_conditions::CAT01" in ids_first[0]


def test_invalid_and_multiple_references_and_unassigned_are_reported(monkeypatch, tmp_path):
    run, projects = _make_run(monkeypatch, tmp_path, 2)
    sources = get_run_sources(run["run_id"])
    first_snapshot = sources[0]["snapshot"]
    interview = first_snapshot["analysis"]["interview_analysis"]
    interview["paradigm_model"]["contextual_conditions"] = [{"category_ids": ["CAT01", "MISSING"]}]
    interview["categories"].append({"category_id": "UNASSIGNED", "category_name": "Unassigned", "concept_ids": [], "evidence": []})
    sources[0]["snapshot"] = first_snapshot
    _replace_snapshot(sources[0], first_snapshot)

    result = collect_run_pools(run["run_id"], persist=False)

    assert any(item["category_id"] == "UNASSIGNED" for item in result["unassigned_categories"])
    assert any(issue["category_id"] == "MISSING" for issue in result["reference_issues"])
    assert any(item["source_category_id"] == "CAT01" for item in result["pools"]["contextual_conditions"])
    assert any(item["source_category_id"] == "CAT01" for item in result["pools"]["causal_conditions"])
    assert any("multiple_components" == issue["issue_type"] for issue in result["review_items"])


def test_invalid_reference_can_be_excluded_with_a_versioned_decision(monkeypatch, tmp_path):
    run, _ = _make_run(monkeypatch, tmp_path, 2)
    source = get_run_sources(run["run_id"])[0]
    snapshot = source["snapshot"]
    snapshot["analysis"]["interview_analysis"]["paradigm_model"]["contextual_conditions"] = [{"category_id": "MISSING"}]
    _replace_snapshot(source, snapshot)
    pool = collect_run_pools(run["run_id"])
    item = next(item for item in pool["review_items"] if item["category_id"] == "MISSING")

    result = record_pool_review_decision(
        run["run_id"],
        category_id="MISSING",
        source_project_id=source["source_project_id"],
        review_item_id=item["review_item_id"],
        decision="exclude",
        reason="reference is not present in the source analysis",
        approved=True,
        pool_version=pool["version"],
    )

    assert result["version"] == pool["version"] + 1
    assert result["reference_issues"][0]["decision_status"] == "approved"
    assert result["excluded_references"][0]["category_id"] == "MISSING"
    assert result["validation_report"]["valid"] is True


def test_invalid_evidence_is_reported_not_dropped(monkeypatch, tmp_path):
    run, _ = _make_run(monkeypatch, tmp_path, 2)
    source = get_run_sources(run["run_id"])[0]
    snapshot = source["snapshot"]
    snapshot["analysis"]["interview_analysis"]["categories"][0]["evidence"][0]["paragraph_id"] = "NO_SUCH_PARAGRAPH"
    _replace_snapshot(source, snapshot)

    result = collect_run_pools(run["run_id"], persist=False)

    assert result["metadata"]["evidence_count"] == 3
    assert any(issue["issue_type"] == "invalid_evidence" for issue in result["extraction_errors"])
    assert any(evidence["paragraph_id"] == "NO_SUCH_PARAGRAPH" for evidence in result["evidence"])


def test_extracts_each_of_the_five_paradigm_components(monkeypatch, tmp_path):
    run, _ = _make_run(monkeypatch, tmp_path, 2)
    source = get_run_sources(run["run_id"])[0]
    snapshot = source["snapshot"]
    interview = snapshot["analysis"]["interview_analysis"]
    interview["paradigm_model"] = {
        key: [{"category_id": "CAT01", "evidence": []}]
        for key in COMPONENT_KEYS[:-1]
    }
    _replace_snapshot(source, snapshot)

    result = collect_run_pools(run["run_id"], persist=False)

    assert all(result["pools"][key] for key in COMPONENT_KEYS[:-1])
    assert len(result["pools"]["central_phenomenon"]) == 2
    assert result["metadata"]["assigned_count"] == 2
    assert any(issue["issue_type"] == "multiple_components" for issue in result["review_items"])


def test_unconfirmed_category_edits_are_not_applied(monkeypatch, tmp_path):
    run, _ = _make_run(monkeypatch, tmp_path, 2)
    source = get_run_sources(run["run_id"])[0]
    snapshot = source["snapshot"]
    snapshot["researcher_edits"]["interview_analysis"]["confirmed"] = False
    _replace_snapshot(source, snapshot)

    result = collect_run_pools(run["run_id"], persist=False)

    item = next(item for item in result["pools"]["causal_conditions"] if item["source_project_id"] == source["source_project_id"])
    assert item["original_category_name"] == "Original 1"
    assert item["researcher_approved_category_name"] == "Original 1"
    assert item["definition"] == "Original definition 1"


def test_invalid_snapshot_is_in_coverage_and_validation_report(monkeypatch, tmp_path):
    run, _ = _make_run(monkeypatch, tmp_path, 2)
    source = get_run_sources(run["run_id"])[0]
    from app.core.database import _connect

    with _connect() as connection:
        connection.execute(
            "UPDATE synthesis_run_sources SET source_hash = ? WHERE id = ?",
            ("bad-hash", source["id"]),
        )

    result = collect_run_pools(run["run_id"], persist=False)

    assert result["metadata"]["registered_source_count"] == 2
    assert result["metadata"]["valid_snapshot_count"] == 1
    assert len(result["source_coverage"]) == 2
    assert result["validation_report"]["valid"] is False
    assert any(issue["issue_type"] == "snapshot_hash_mismatch" for issue in result["extraction_errors"])


def test_relationships_and_their_evidence_are_preserved(monkeypatch, tmp_path):
    run, _ = _make_run(monkeypatch, tmp_path, 2)
    source = get_run_sources(run["run_id"])[0]
    snapshot = source["snapshot"]
    interview = snapshot["analysis"]["interview_analysis"]
    interview["relationships"] = [{
        "relationship_id": "REL01",
        "source_category_id": "CAT01",
        "target_category_id": "CAT01",
        "relationship_type": "direct",
        "description": "synthetic relation",
        "supporting_concept_ids": ["C001"],
        "evidence": deepcopy(interview["categories"][0]["evidence"]),
    }]
    _replace_snapshot(source, snapshot)

    result = collect_run_pools(run["run_id"], persist=False)

    relationship = result["relationships"][0]
    assert relationship["raw_relationship"]["description"] == "synthetic relation"
    assert relationship["source_relationship_id"] == "REL01"
    assert relationship["evidence_refs"]
    assert result["validation_report"]["valid"] is True


def test_review_decision_creates_new_version_and_preserves_old(monkeypatch, tmp_path):
    run, _ = _make_run(monkeypatch, tmp_path, 2)
    source = get_run_sources(run["run_id"])[0]
    snapshot = source["snapshot"]
    snapshot["analysis"]["interview_analysis"]["categories"].append({
        "category_id": "CAT-X",
        "category_name": "Independent category",
        "definition": "Synthetic definition",
        "concept_ids": [],
        "evidence": [],
    })
    _replace_snapshot(source, snapshot)
    pool = collect_run_pools(run["run_id"])

    result = record_pool_review_decision(
        run["run_id"],
        category_id="CAT-X",
        source_project_id=source["source_project_id"],
        decision="exclude",
        reason="outside scope",
        approved=True,
        pool_version=pool["version"],
    )

    versions = list_stage_results(run["run_id"])
    assert result["version"] == pool["version"] + 1
    assert len(versions) == 2
    assert versions[0]["output_json"]["unassigned_categories"]
    assert versions[1]["output_json"]["metadata"]["excluded_count"] == 1
    assert versions[1]["researcher_edits_json"]["decisions"][0]["reason"] == "outside scope"


def test_review_assignment_moves_only_pool_copy(monkeypatch, tmp_path):
    run, _ = _make_run(monkeypatch, tmp_path, 2)
    source = get_run_sources(run["run_id"])[0]
    snapshot = source["snapshot"]
    snapshot["analysis"]["interview_analysis"]["categories"].append({
        "category_id": "CAT-X",
        "category_name": "Independent category",
        "definition": "Synthetic definition",
        "concept_ids": [],
        "evidence": [],
    })
    _replace_snapshot(source, snapshot)
    pool = collect_run_pools(run["run_id"])

    result = record_pool_review_decision(
        run["run_id"],
        category_id="CAT-X",
        source_project_id=source["source_project_id"],
        decision="assign",
        component_key="contextual_conditions",
        reason="researcher classification",
        approved=True,
        pool_version=pool["version"],
    )

    assert not result["unassigned_categories"]
    assert result["pools"]["contextual_conditions"][0]["source_category_id"] == "CAT-X"
    assert result["pools"]["contextual_conditions"][0]["source_item_id"].endswith("::contextual_conditions::CAT-X")
    assert result["metadata"]["assigned_count"] == 3


def test_pool_routes_expose_extraction_and_review(monkeypatch, tmp_path):
    run, _ = _make_run(monkeypatch, tmp_path, 2)
    source = get_run_sources(run["run_id"])[0]
    snapshot = source["snapshot"]
    snapshot["analysis"]["interview_analysis"]["categories"].append({
        "category_id": "CAT-X",
        "category_name": "Independent category",
        "definition": "Synthetic definition",
        "concept_ids": [],
        "evidence": [],
    })
    _replace_snapshot(source, snapshot)
    client = TestClient(app)

    pool_response = client.get(f"/synthesis/runs/{run['run_id']}/pools")
    assert pool_response.status_code == 200
    pool = pool_response.json()
    review_response = client.post(
        f"/synthesis/runs/{run['run_id']}/pool-review",
        json={
            "category_id": "CAT-X",
            "source_project_id": source["source_project_id"],
            "review_item_id": next(item["review_item_id"] for item in pool["review_items"] if item["category_id"] == "CAT-X"),
            "decision": "assign",
            "component_key": "contextual_conditions",
            "reason": "researcher classification",
            "approved": True,
            "pool_version": pool["version"],
        },
    )

    assert review_response.status_code == 200
    assert review_response.json()["version"] == pool["version"] + 1


def _replace_snapshot(source, snapshot):
    import hashlib
    import json
    from app.core.database import _connect

    raw = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    with _connect() as connection:
        connection.execute(
            "UPDATE synthesis_run_sources SET snapshot_json = ?, source_hash = ? WHERE id = ?",
            (raw, digest, source["id"]),
        )
