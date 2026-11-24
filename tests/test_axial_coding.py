from fastapi.testclient import TestClient

from app.main import app
from app.routers import results as results_router


def make_axial_project():
	return {
		"project_id": "axial-project",
		"title": "مصاحبه آزمایشی",
		"interview_id": "INT-1",
		"paragraphs": [{"paragraph_id": "P001", "text": "متن مصاحبه"}],
		"concepts": [{"concept_id": "C001", "paragraph_ids": ["P001"], "original_concept": "مفهوم اول"}],
		"analysis": {
			"schema_version": "2.0",
			"concept_reviews": [],
			"interview_analysis": {
				"categories": [{
					"category_id": "CAT01",
					"category_name": "مقوله اصلی",
					"definition": "تعریف مقوله",
					"concept_ids": ["C001"],
					"subcategories": [],
					"properties": [],
					"dimensions": [],
					"evidence": [{"paragraph_id": "P001", "exact_quote": "متن مصاحبه"}],
				}],
				"relationships": [],
				"central_phenomenon": None,
				"quality_audit": {"final_status": "complete"},
				"researcher_review": [],
				"paradigm_model": {},
			},
		},
		"researcher_edits": {},
	}


def install_project(monkeypatch, project):
	monkeypatch.setattr(results_router, "get_project", lambda _id: project)

	def update_project(_id, **changes):
		project.update(changes)
		return project

	monkeypatch.setattr(results_router, "update_project", update_project)
	monkeypatch.setattr(results_router, "mark_synthesis_runs_stale_for_source", lambda _id: None)


def test_axial_dashboard_and_all_five_pages_are_available(monkeypatch):
	project = make_axial_project()
	install_project(monkeypatch, project)
	client = TestClient(app, follow_redirects=False)

	dashboard = client.get("/results/axial-project/axial-coding")
	assert dashboard.status_code == 200
	assert "داشبورد مرحله دوم" in dashboard.text
	assert dashboard.text.count("گام ") >= 5

	for step in ("categories", "paradigm", "relationships", "phenomenon", "final"):
		response = client.get(f"/results/axial-project/axial-coding/{step}")
		assert response.status_code == 200


def test_axial_category_edit_move_and_history_preserve_ids(monkeypatch):
	project = make_axial_project()
	project["analysis"]["interview_analysis"]["categories"].append({
		"category_id": "CAT02",
		"category_name": "مقوله دوم",
		"definition": "تعریف دوم",
		"concept_ids": [],
		"subcategories": [],
		"properties": [],
		"dimensions": [],
		"evidence": [],
	})
	install_project(monkeypatch, project)
	client = TestClient(app, follow_redirects=False)

	response = client.post(
		"/results/axial-project/axial-coding/categories/edit",
		data={"category_id": "CAT01", "title": "عنوان اصلاح‌شده", "definition": "تعریف اصلاح‌شده", "reason": "بازبینی شواهد"},
	)
	assert response.status_code == 303

	response = client.post(
		"/results/axial-project/axial-coding/categories/move-concept",
		data={"concept_id": "C001", "source_category_id": "CAT01", "target_category_id": "CAT02", "reason": "نسبت تحلیلی روشن‌تر است"},
	)
	assert response.status_code == 303

	state = project["researcher_edits"]["axial_coding"]
	assert state["category_overrides"]["CAT01"]["category_name"] == "عنوان اصلاح‌شده"
	assert state["concept_assignments"]["C001"]["previous_category_id"] == "CAT01"
	assert state["concept_assignments"]["C001"]["category_id"] == "CAT02"
	assert state["concept_assignments"]["C001"]["history"]
	assert state["change_history"]


def test_axial_final_audit_flags_missing_phenomenon_and_reports_saved_changes(monkeypatch):
	project = make_axial_project()
	install_project(monkeypatch, project)
	client = TestClient(app, follow_redirects=False)

	response = client.get("/results/axial-project/axial-coding/final")
	assert response.status_code == 200
	assert "کنترل کیفیت Python" in response.text
	assert "پدیده محوری مصاحبه" in response.text


def test_axial_create_category_and_logical_delete_preserve_history(monkeypatch):
	project = make_axial_project()
	install_project(monkeypatch, project)
	client = TestClient(app, follow_redirects=False)

	response = client.post(
		"/results/axial-project/axial-coding/categories/create",
		data={"title": "مقوله جدید", "definition": "تعریف جدید", "reason": "شواهد پشتیبان جدید"},
	)
	assert response.status_code == 303
	state = project["researcher_edits"]["axial_coding"]
	created_ids = [
		category_id
		for category_id, category in state["category_overrides"].items()
		if category.get("existence_status") == "created"
	]
	assert len(created_ids) == 1
	created_id = created_ids[0]

	response = client.post(
		"/results/axial-project/axial-coding/categories/delete",
		data={"entity_type": "category", "entity_id": created_id, "reason": "پس از بازبینی حذف شد"},
	)
	assert response.status_code == 303
	assert state["logical_deletions"][created_id]["status"] == "deleted"
	assert any(item["record_id"] == created_id for item in state["change_history"])


def test_axial_paradigm_decision_closes_to_result_card(monkeypatch):
	project = make_axial_project()
	install_project(monkeypatch, project)
	client = TestClient(app, follow_redirects=False)

	response = client.post(
		"/results/axial-project/axial-coding/paradigm/placement",
		data={
			"category_id": "CAT01",
			"component_key": "causal_conditions",
			"researcher_status": "تأیید",
			"reason": "با شواهد مصاحبه سازگار است",
		},
	)
	assert response.status_code == 303

	page = client.get("/results/axial-project/axial-coding/paradigm")
	assert page.status_code == 200
	assert "جایگاه ثبت‌شده" in page.text
	assert "شرایط علّی" in page.text
	assert "مقوله اصلی" in page.text
	assert "ثبت تصمیم این پیشنهاد" not in page.text
	assert "مقوله‌های ثبت‌شده در مؤلفه‌های پارادایمی" in page.text


def test_axial_relationship_statuses_are_saved_separately(monkeypatch):
	project = make_axial_project()
	project["analysis"]["interview_analysis"]["relationships"] = [{
		"relationship_id": "REL01",
		"source_category_id": "CAT01",
		"target_category_id": "CAT01",
		"relationship_type": "interpretive",
		"description": "رابطه آزمایشی",
		"supporting_concept_ids": ["C001"],
		"evidence": [],
	}]
	install_project(monkeypatch, project)
	client = TestClient(app, follow_redirects=False)

	response = client.post(
		"/results/axial-project/axial-coding/relationships/decision",
		data={
			"relationship_id": "REL01",
			"researcher_status": "رد",
			"validity_status": "شواهد ناکافی",
			"description": "رابطه حفظ شود",
			"reason": "داده کافی نبود",
		},
	)
	assert response.status_code == 303
	decision = project["researcher_edits"]["axial_coding"]["relationship_decisions"]["REL01"]
	assert decision["researcher_status"] == "رد"
	assert decision["validity_status"] == "شواهد ناکافی"
	assert project["analysis"]["interview_analysis"]["relationships"][0]["relationship_id"] == "REL01"
