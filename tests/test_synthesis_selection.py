import json

from fastapi.testclient import TestClient

from app.core import database as database_module
from app.core.database import create_project, init_db
from app.main import app


def _reset_db(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("HOOSHYAR_DATA_DIR", str(data_dir))
    database_module.DATA_DIR = data_dir
    database_module.DATABASE_PATH = data_dir / "hooshyar.sqlite3"
    init_db()


def _make_complete_project(project_id: str, title: str, topic: str, *, concept_id: str = "C001") -> dict:
    return {
        "project_id": project_id,
        "title": title,
        "topic": topic,
        "source_filename": "sample.docx",
        "paragraphs": [{"paragraph_id": "P001", "text": "متن مصاحبه برای نشان دادن داده"}],
        "concepts": [{
            "concept_id": concept_id,
            "paragraph_ids": ["P001"],
            "original_concept": "فشار کاری",
            "original_code": "شرایط زمینه‌ای",
        }],
        "analysis": {
            "schema_version": "2.0",
            "analysis_status": "complete",
            "interview_analysis": {
                "categories": [{
                    "category_id": "CAT01",
                    "category_name": "فشار کاری",
                    "definition": "تعریف تستی",
                    "subcategories": [],
                    "concept_ids": [concept_id],
                    "evidence": [],
                    "confidence": 1.0,
                    "needs_researcher_review": False,
                }],
                "central_phenomenon": {
                    "title": "مدیریت فشار کاری",
                    "definition": "توضیح،",
                    "category_id": "CAT01",
                    "supporting_category_ids": ["CAT01"],
                    "supporting_concept_ids": [concept_id],
                    "evidence": [{"paragraph_id": "P001", "exact_quote": "متن مصاحبه برای نشان دادن داده", "interpretation": "شاهد"}],
                    "confidence": 1.0,
                    "needs_researcher_review": False,
                },
                "relationships": [],
                "paradigm_model": {
                    "causal_conditions": [{
                        "category_id": "CAT01",
                        "concept_ids": [concept_id],
                        "explanation": "توضیح مناسب",
                        "evidence": [{"paragraph_id": "P001", "exact_quote": "متن مصاحبه برای نشان دادن داده", "interpretation": "شاهد"}],
                    }],
                    "central_phenomenon": {"category_id": "CAT01", "title": "مدیریت فشار کاری"},
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
                    "final_status": "complete",
                },
            },
            "source_scope": {
                "total_paragraphs": 1,
                "total_concepts_received": 1,
                "total_concepts_reviewed": 1,
            },
            "concept_reviews": [{
                "concept_id": concept_id,
                "paragraph_id": "P001",
                "original_concept": "فشار کاری",
                "suggested_concept": "فشار کاری",
                "original_code": "شرایط زمینه‌ای",
                "suggested_code": "شرایط زمینه‌ای",
                "concept_reason": "تأیید",
                "code_evaluation": "confirmed",
                "code_reason": "تأیید",
                "confidence": 1.0,
                "needs_researcher_review": False,
                "evidence": [{"paragraph_id": "P001", "exact_quote": "متن مصاحبه برای نشان دادن داده", "interpretation": "شاهد"}],
            }],
            "analysis_metadata": {
                "total_concepts_received": 1,
                "total_concepts_reviewed": 1,
                "total_categories": 1,
                "total_relationships": 0,
            },
        },
        "researcher_edits": {
            "concept_reviews": {concept_id: {"confirmed": True}},
            "interview_analysis": {"confirmed": True},
        },
    }


def test_select_requires_at_least_two_eligible_sources(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    project = _make_complete_project("project-1", "مصاحبه اول", "پژوهش مشترک")
    create_project(project["title"], project["topic"])
    project_record = create_project("مصاحبه دوم", "پژوهش مشترک")
    project_record.update({
        "paragraphs": project["paragraphs"],
        "concepts": project["concepts"],
        "analysis": project["analysis"],
        "researcher_edits": project["researcher_edits"],
    })
    from app.core.database import update_project
    update_project(project_record["project_id"], paragraphs=project["paragraphs"], concepts=project["concepts"], analysis=project["analysis"], researcher_edits=project["researcher_edits"])

    client = TestClient(app)
    response = client.post(
        "/synthesis/runs",
        data={
            "title": "تحلیل تلفیقی",
            "study_topic": "پژوهش مشترک",
            "selected_ids": project_record["project_id"],
            "confirm_shared_topic": "on",
        },
    )
    assert response.status_code == 400
    assert "حداقل دو مصاحبه" in response.json()["detail"]


def test_valid_two_sources_create_run(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    first = create_project("مصاحبه اول", "پژوهش مشترک")
    second = create_project("مصاحبه دوم", "پژوهش مشترک")
    first.update(_make_complete_project(first["project_id"], "مصاحبه اول", "پژوهش مشترک"))
    second.update(_make_complete_project(second["project_id"], "مصاحبه دوم", "پژوهش مشترک"))
    from app.core.database import update_project
    update_project(first["project_id"], paragraphs=first["paragraphs"], concepts=first["concepts"], analysis=first["analysis"], researcher_edits=first["researcher_edits"])
    update_project(second["project_id"], paragraphs=second["paragraphs"], concepts=second["concepts"], analysis=second["analysis"], researcher_edits=second["researcher_edits"])

    client = TestClient(app)
    response = client.post(
        "/synthesis/runs",
        data={
            "title": "تحلیل تلفیقی دو مصاحبه",
            "study_topic": "پژوهش مشترک",
            "selected_ids": f"{first['project_id']},{second['project_id']}",
            "confirm_shared_topic": "on",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["selected_source_count"] == 2
    assert payload["status"] == "draft"
    assert len(payload["sources"]) == 2


def test_incomplete_single_interview_is_eligible_when_source_coverage_is_verified(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    project = create_project("مصاحبه نامعتبر", "پژوهش مشترک")
    project.update(_make_complete_project(project["project_id"], "مصاحبه نامعتبر", "پژوهش مشترک"))
    project["analysis"]["analysis_status"] = "incomplete"
    from app.core.database import update_project
    update_project(project["project_id"], paragraphs=project["paragraphs"], concepts=project["concepts"], analysis=project["analysis"], researcher_edits=project["researcher_edits"])
    other = create_project("مصاحبه دوم", "پژوهش مشترک")
    other.update(_make_complete_project(other["project_id"], "مصاحبه دوم", "پژوهش مشترک"))
    update_project(other["project_id"], paragraphs=other["paragraphs"], concepts=other["concepts"], analysis=other["analysis"], researcher_edits=other["researcher_edits"])

    client = TestClient(app)
    response = client.post(
        "/synthesis/runs",
        data={
            "title": "تحلیل",
            "study_topic": "پژوهش مشترک",
            "selected_ids": f"{project['project_id']},{other['project_id']}",
            "confirm_shared_topic": "on",
        },
    )
    assert response.status_code == 201


def test_source_with_unverified_evidence_stays_ineligible(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    project = create_project("مصاحبه با شاهد نامعتبر", "پژوهش مشترک")
    project.update(_make_complete_project(project["project_id"], "مصاحبه با شاهد نامعتبر", "پژوهش مشترک"))
    project["analysis"]["interview_analysis"]["quality_audit"]["all_evidence_verifiable"] = False
    other = create_project("مصاحبه دوم", "پژوهش مشترک")
    other.update(_make_complete_project(other["project_id"], "مصاحبه دوم", "پژوهش مشترک"))
    from app.core.database import update_project
    update_project(
        project["project_id"],
        paragraphs=project["paragraphs"],
        concepts=project["concepts"],
        analysis=project["analysis"],
        researcher_edits=project["researcher_edits"],
    )
    update_project(
        other["project_id"],
        paragraphs=other["paragraphs"],
        concepts=other["concepts"],
        analysis=other["analysis"],
        researcher_edits=other["researcher_edits"],
    )

    response = TestClient(app).post(
        "/synthesis/runs",
        data={
            "title": "تحلیل",
            "study_topic": "پژوهش مشترک",
            "selected_ids": f"{project['project_id']},{other['project_id']}",
            "confirm_shared_topic": "on",
        },
    )

    assert response.status_code == 400
    assert "all_evidence_verifiable" in response.json()["detail"]


def test_selection_page_shows_deferred_incomplete_analysis_as_eligible(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    project = create_project("مصاحبه نامشخص", "موضوع")
    source = _make_complete_project(project["project_id"], "مصاحبه نامشخص", "موضوع")
    source["analysis"]["analysis_status"] = "incomplete"
    source["analysis"]["interview_analysis"]["quality_audit"]["final_status"] = "incomplete"
    source["analysis"]["interview_analysis"]["quality_audit"]["missing_data_detected"] = True
    source["analysis"]["interview_analysis"]["central_phenomenon"] = None
    source["analysis"]["interview_analysis"]["paradigm_model"]["central_phenomenon"] = None
    from app.core.database import update_project
    update_project(
        project["project_id"],
        paragraphs=source["paragraphs"],
        concepts=source["concepts"],
        analysis=source["analysis"],
        researcher_edits=source["researcher_edits"],
    )

    response = TestClient(app).get("/synthesis/select")

    assert response.status_code == 200
    assert 'name="selected_ids"' in response.text
    assert "مجاز با هشدار" in response.text
    assert "ابهام نظری" in response.text


def test_different_topics_require_explicit_confirmation(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    first = create_project("مصاحبه الف", "موضوع الف")
    second = create_project("مصاحبه ب", "موضوع ب")
    first.update(_make_complete_project(first["project_id"], "مصاحبه الف", "موضوع الف", concept_id="C001"))
    second.update(_make_complete_project(second["project_id"], "مصاحبه ب", "موضوع ب", concept_id="C002"))
    from app.core.database import update_project
    update_project(first["project_id"], paragraphs=first["paragraphs"], concepts=first["concepts"], analysis=first["analysis"], researcher_edits=first["researcher_edits"])
    update_project(second["project_id"], paragraphs=second["paragraphs"], concepts=second["concepts"], analysis=second["analysis"], researcher_edits=second["researcher_edits"])

    client = TestClient(app)
    response = client.post(
        "/synthesis/runs",
        data={
            "title": "تحلیل",
            "study_topic": "پژوهش مشترک",
            "selected_ids": f"{first['project_id']},{second['project_id']}",
            "confirm_shared_topic": "off",
        },
    )
    assert response.status_code == 400
    assert "تفاوت موضوع" in response.json()["detail"]


def test_snapshot_count_matches_selected_sources(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    first = create_project("مصاحبه 1", "موضوع")
    second = create_project("مصاحبه 2", "موضوع")
    third = create_project("مصاحبه 3", "موضوع")
    for project in (first, second, third):
        project.update(_make_complete_project(project["project_id"], project["title"], "موضوع"))
        from app.core.database import update_project
        update_project(project["project_id"], paragraphs=project["paragraphs"], concepts=project["concepts"], analysis=project["analysis"], researcher_edits=project["researcher_edits"])

    client = TestClient(app)
    response = client.post(
        "/synthesis/runs",
        data={
            "title": "تحلیل",
            "study_topic": "موضوع",
            "selected_ids": f"{first['project_id']},{second['project_id']},{third['project_id']}",
            "confirm_shared_topic": "on",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["selected_source_count"] == 3
    assert len(payload["sources"]) == 3
    assert all("snapshot" in source for source in payload["sources"])


def test_snapshot_is_stable_after_project_update(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    project = create_project("مصاحبه پایه", "موضوع")
    project.update(_make_complete_project(project["project_id"], "مصاحبه پایه", "موضوع"))
    from app.core.database import update_project
    update_project(project["project_id"], paragraphs=project["paragraphs"], concepts=project["concepts"], analysis=project["analysis"], researcher_edits=project["researcher_edits"])

    client = TestClient(app)
    response = client.post(
        "/synthesis/runs",
        data={
            "title": "تحلیل",
            "study_topic": "موضوع",
            "selected_ids": project["project_id"],
            "confirm_shared_topic": "on",
        },
    )
    assert response.status_code == 400
    assert "حداقل دو مصاحبه" in response.json()["detail"]

    second_project = create_project("مصاحبه دوم", "موضوع")
    second_project.update(_make_complete_project(second_project["project_id"], "مصاحبه دوم", "موضوع"))
    update_project(second_project["project_id"], paragraphs=second_project["paragraphs"], concepts=second_project["concepts"], analysis=second_project["analysis"], researcher_edits=second_project["researcher_edits"])

    response = client.post(
        "/synthesis/runs",
        data={
            "title": "تحلیل",
            "study_topic": "موضوع",
            "selected_ids": f"{project['project_id']},{second_project['project_id']}",
            "confirm_shared_topic": "on",
        },
    )
    assert response.status_code == 201
    run_id = response.json()["run_id"]
    snapshot = response.json()["sources"][0]["snapshot"]
    assert snapshot["project_id"] == project["project_id"]

    update_project(project["project_id"], title="تغییر عنوان پس از Snapshot")
    source_rows = response.json()["sources"]
    source_rows_after = source_rows
    assert source_rows_after[0]["snapshot"]["title"] == "مصاحبه پایه"
    assert source_rows_after[0]["snapshot"]["project_id"] == project["project_id"]

    from app.repositories.synthesis_repository import get_run_sources
    persisted_sources = get_run_sources(run_id)
    assert persisted_sources[0]["snapshot"]["title"] == "مصاحبه پایه"
    assert persisted_sources[0]["snapshot"]["project_id"] == project["project_id"]


def test_run_creation_rejects_duplicate_and_missing_ids(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    first = create_project("مصاحبه 1", "موضوع")
    second = create_project("مصاحبه 2", "موضوع")
    for project in (first, second):
        project.update(_make_complete_project(project["project_id"], project["title"], "موضوع"))
        from app.core.database import update_project
        update_project(project["project_id"], paragraphs=project["paragraphs"], concepts=project["concepts"], analysis=project["analysis"], researcher_edits=project["researcher_edits"])

    client = TestClient(app)
    duplicate_response = client.post(
        "/synthesis/runs",
        data={
            "title": "تحلیل",
            "study_topic": "موضوع",
            "selected_ids": f"{first['project_id']},{first['project_id']}",
            "confirm_shared_topic": "on",
        },
    )
    assert duplicate_response.status_code == 400
    assert "تکراری" in duplicate_response.json()["detail"]

    missing_response = client.post(
        "/synthesis/runs",
        data={
            "title": "تحلیل",
            "study_topic": "موضوع",
            "selected_ids": f"{first['project_id']},missing-project",
            "confirm_shared_topic": "on",
        },
    )
    assert missing_response.status_code == 400
    assert "پیدا نشد" in missing_response.json()["detail"]
