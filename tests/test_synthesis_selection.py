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


def test_direct_grouping_entry_redirects_to_pool_preview_when_pool_is_missing(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    first = create_project("مصاحبه اول", "موضوع مشترک")
    second = create_project("مصاحبه دوم", "موضوع مشترک")
    for project in (first, second):
        project.update(_make_complete_project(project["project_id"], project["title"], "موضوع مشترک"))
        from app.core.database import update_project
        update_project(
            project["project_id"],
            paragraphs=project["paragraphs"],
            concepts=project["concepts"],
            analysis=project["analysis"],
            researcher_edits=project["researcher_edits"],
        )
    client = TestClient(app, follow_redirects=False)
    created = client.post(
        "/synthesis/runs",
        data={
            "title": "تحلیل تلفیقی",
            "study_topic": "موضوع مشترک",
            "selected_ids": [first["project_id"], second["project_id"]],
        },
    )
    run_id = created.json()["run_id"]

    response = client.get(f"/synthesis/runs/{run_id}/category-grouping")

    assert response.status_code == 303
    assert response.headers["location"].split("?", 1)[0].endswith("/pool-preview")
    preview = client.get(response.headers["location"])
    assert preview.status_code == 200
    assert "محتوای شش مؤلفه" in preview.text
    assert "Pool" not in preview.text

def test_force_proceed_is_audited_and_allows_only_intermediate_grouping(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    from app.repositories.synthesis_repository import list_stage_results
    projects = [create_project(f"مصاحبه {index}", "موضوع مشترک") for index in (1, 2)]
    from app.core.database import update_project
    for project in projects:
        project.update(_make_complete_project(project["project_id"], project["title"], "موضوع مشترک"))
        project["analysis"]["interview_analysis"]["paradigm_model"]["causal_conditions"] = []
        project["analysis"]["interview_analysis"]["categories"][0]["evidence"] = [{
            "paragraph_id": "P001",
            "exact_quote": "شاهد موجود نیست",
            "interpretation": "شاهد نیازمند بازبینی پژوهشگر است.",
        }]
        update_project(
            project["project_id"],
            paragraphs=project["paragraphs"],
            concepts=project["concepts"],
            analysis=project["analysis"],
            researcher_edits=project["researcher_edits"],
        )

    client = TestClient(app, follow_redirects=False)
    created = client.post(
        "/synthesis/runs",
        data={
            "title": "تحلیل آزمایشی",
            "study_topic": "موضوع مشترک",
            "selected_ids": [item["project_id"] for item in projects],
        },
    )
    run_id = created.json()["run_id"]
    preview = client.get(f"/synthesis/runs/{run_id}/pool-preview")
    assert preview.status_code == 200
    assert "category_coverage_mismatch" not in preview.text
    assert "ادامه اجباری با مسئولیت پژوهشگر" in preview.text
    assert "یکی از شاهدهای این مصاحبه" in preview.text

    response = client.post(
        f"/synthesis/runs/{run_id}/pool-review/force-proceed",
        data={"reason": "ادامه برای مقایسه اکتشافی", "confirmed": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    latest = max(
        (item for item in list_stage_results(run_id) if item["stage_key"] == "pool_extraction"),
        key=lambda item: item["version"],
    )
    assert latest["status"] == "approved_with_warnings"
    assert latest["approval_status"] == "approved"
    assert latest["output_json"]["metadata"]["researcher_force_proceed"]["reason"] == "ادامه برای مقایسه اکتشافی"
    assert latest["validation_report_json"]["valid"] is False

    grouping = client.get(f"/synthesis/runs/{run_id}/category-grouping")
    assert grouping.status_code == 200
    assert "Prompt مستقل Markdown" in grouping.text


def test_pool_item_can_be_confirmed_without_researcher_reason(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    first = create_project("مصاحبه اول", "موضوع مشترک")
    second = create_project("مصاحبه دوم", "موضوع مشترک")
    from app.core.database import update_project
    for project in (first, second):
        project.update(_make_complete_project(project["project_id"], project["title"], "موضوع مشترک"))
        project["analysis"]["interview_analysis"]["paradigm_model"]["causal_conditions"] = []
        update_project(
            project["project_id"],
            paragraphs=project["paragraphs"],
            concepts=project["concepts"],
            analysis=project["analysis"],
            researcher_edits=project["researcher_edits"],
        )
    client = TestClient(app, follow_redirects=False)
    created = client.post(
        "/synthesis/runs",
        data={
            "title": "تحلیل آزمایشی",
            "study_topic": "موضوع مشترک",
            "selected_ids": [first["project_id"], second["project_id"]],
        },
    )
    run_id = created.json()["run_id"]
    client.get(f"/synthesis/runs/{run_id}/pool-preview")
    from app.repositories.synthesis_repository import list_stage_results
    latest = max(
        (item for item in list_stage_results(run_id) if item["stage_key"] == "pool_extraction"),
        key=lambda item: item["version"],
    )
    review_item = next(item for item in latest["output_json"]["review_items"] if item["decision_status"] != "approved")

    response = client.post(
        f"/synthesis/runs/{run_id}/pool-review/decision",
        data={
            "source_project_id": review_item["source_project_id"],
            "category_id": review_item["category_id"],
            "review_item_id": review_item["review_item_id"],
            "pool_version": latest["version"],
            "decision": "assign",
            "component_key": "causal_conditions",
        },
    )

    assert response.status_code == 303
    updated = max(
        (item for item in list_stage_results(run_id) if item["stage_key"] == "pool_extraction"),
        key=lambda item: item["version"],
    )
    decision = updated["researcher_edits_json"]["decisions"][-1]
    assert decision["reason"] == "تخصیص با تأیید پژوهشگر"
    assert decision["approval_status"] == "approved"


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


def test_source_with_unverified_evidence_is_selectable_with_warning(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    project = create_project("مصاحبه با شاهد نامعتبر", "پژوهش مشترک")
    project.update(_make_complete_project(project["project_id"], "مصاحبه با شاهد نامعتبر", "پژوهش مشترک"))
    project["analysis"]["interview_analysis"]["quality_audit"]["all_evidence_verifiable"] = False
    project["analysis"]["interview_analysis"]["quality_audit"]["evidence_warnings"] = [{
        "record_type": "concept",
        "record_id": "C001",
        "paragraph_id": "P001",
        "status": "fuzzy_match",
    }]
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
    assert response.json()["selected_source_count"] == 2
    assert len(response.json()["eligibility_warnings"]) == 1
    assert "concept C001 در P001 (fuzzy_match)" in response.json()["eligibility_warnings"][0]["reason"]

    selection_page = client.get("/synthesis/select")
    assert f'href="/results/{project["project_id"]}/interview-analysis?show_quality=1#quality-audit"' in selection_page.text
    review_page = client.get(f"/results/{project['project_id']}/interview-analysis?show_quality=1")
    assert 'id="quality-audit"' in review_page.text
    assert 'id="quality-evidence-0"' in review_page.text
    assert 'id="stage-import-stage1"' in client.get(f"/analysis/{project['project_id']}").text


def test_incomplete_interview_checkbox_is_not_disabled(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    project = create_project("مصاحبه نیازمند بازبینی", "موضوع")
    source = _make_complete_project(project["project_id"], project["title"], "موضوع")
    source["analysis"]["interview_analysis"]["quality_audit"]["all_evidence_verifiable"] = False
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
    row_input = next(line for line in response.text.splitlines() if project["project_id"] in line and 'name="selected_ids"' in line)
    assert "disabled" not in row_input
    assert "مجاز با هشدار" in response.text


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
    assert "پدیده محوری این مصاحبه جداگانه تعیین نشده" in response.text
    assert "فاز ششم تحلیل تلفیقی" in response.text
    assert f'href="/results/{project["project_id"]}/interview-analysis?show_quality=1#quality-audit"' in response.text


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

    confirmed_response = client.post(
        "/synthesis/runs",
        data={
            "title": "تحلیل",
            "study_topic": "پژوهش مشترک",
            "selected_ids": f"{first['project_id']},{second['project_id']}",
            "confirm_shared_topic": "on",
        },
    )
    assert confirmed_response.status_code == 201
    assert confirmed_response.json()["selected_source_count"] == 2


def test_browser_selection_preserves_form_then_redirects_to_pool_preview(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    first = create_project("مصاحبه الف", "موضوع الف")
    second = create_project("مصاحبه ب", "موضوع ب")
    first.update(_make_complete_project(first["project_id"], "مصاحبه الف", "موضوع الف", concept_id="C001"))
    second.update(_make_complete_project(second["project_id"], "مصاحبه ب", "موضوع ب", concept_id="C002"))
    from app.core.database import update_project
    for project in (first, second):
        update_project(
            project["project_id"],
            paragraphs=project["paragraphs"],
            concepts=project["concepts"],
            analysis=project["analysis"],
            researcher_edits=project["researcher_edits"],
        )

    client = TestClient(app)
    form_data = {
        "title": "تحلیل تلفیقی",
        "study_topic": "پژوهش مشترک",
        "selected_ids": [first["project_id"], second["project_id"]],
    }
    warning_response = client.post(
        "/synthesis/runs",
        data=form_data,
        headers={"accept": "text/html"},
    )
    assert warning_response.status_code == 400
    assert "موضوع مصاحبه‌های انتخاب‌شده متفاوت است" in warning_response.text
    assert 'name="confirm_shared_topic"' in warning_response.text
    assert 'value="تحلیل تلفیقی"' in warning_response.text
    assert warning_response.text.count("checked") >= 2

    confirmed_response = client.post(
        "/synthesis/runs",
        data={**form_data, "confirm_shared_topic": "on"},
        headers={"accept": "text/html"},
        follow_redirects=False,
    )
    assert confirmed_response.status_code == 303
    location = confirmed_response.headers["location"]
    assert location.endswith("/pool-preview")
    preview = client.get(location)
    assert preview.status_code == 200
    assert "محتوای شش مؤلفه" in preview.text
    assert "شرایط علّی" in preview.text
    assert "اجرای مقوله‌سازی و تم‌سازی" in preview.text
    run_id = location.rstrip("/").split("/")[-2]
    assert f'href="/synthesis/runs/{run_id}/category-grouping"' in preview.text
    grouping = client.get(f"/synthesis/runs/{run_id}/category-grouping")
    assert grouping.status_code == 200
    assert "Prompt مستقل Markdown" in grouping.text


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
