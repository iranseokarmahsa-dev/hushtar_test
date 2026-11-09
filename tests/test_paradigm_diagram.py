from fastapi.testclient import TestClient

from app.main import app
from app.routers import results as results_router
from app.services.paradigm_service import _build_paradigm_graph, prepare_paradigm_diagram


def make_project():
	interview = {
		"categories": [
			{
				"category_id": "CAT01",
				"category_name": "فشار کاری",
				"definition": "تعریف فشار کاری",
				"concept_ids": ["C001"],
				"evidence": [{
					"paragraph_id": "P001",
					"exact_quote": "فشار زیادی بود",
					"interpretation": "شاهد مقوله",
				}],
			},
			{
				"category_id": "CAT02",
				"category_name": "مواجهه",
				"definition": "تعریف مواجهه",
				"concept_ids": ["C002"],
				"evidence": [],
			},
			{
				"category_id": "CAT03",
				"category_name": "پیامد",
				"definition": "تعریف پیامد",
				"concept_ids": ["C003"],
				"evidence": [],
			},
		],
		"central_phenomenon": {
			"identified": True,
			"category_id": "CAT02",
			"title": "مواجهه با فشار",
			"definition": "توضیح پدیده",
			"supporting_category_ids": ["CAT01", "CAT02"],
			"supporting_concept_ids": ["C001", "C002"],
			"evidence": [{
				"paragraph_id": "P001",
				"exact_quote": "فشار زیادی بود",
				"interpretation": "شاهد پدیده",
			}],
		},
		"paradigm_model": {
			"causal_conditions": [{
				"category_id": "CAT01",
				"concept_ids": ["C001"],
				"explanation": "توضیح علّی",
				"evidence": [{
					"paragraph_id": "P001",
					"exact_quote": "فشار زیادی بود",
					"interpretation": "شاهد مؤلفه علّی",
				}],
			}],
			"central_phenomenon": {"category_id": "CAT02", "title": "پدیده"},
			"contextual_conditions": [],
			"intervening_conditions": [],
			"action_interaction_strategies": [{
				"category_id": "CAT02",
				"concept_ids": ["C002"],
				"explanation": "توضیح راهبرد",
				"evidence": [],
			}],
			"consequences": [{"category_id": "CAT03", "concept_ids": ["C003"]}],
		},
		"relationships": [{
			"relationship_id": "REL01",
			"source_category_id": "CAT01",
			"target_category_id": "CAT02",
			"relationship_type": "direct",
			"description": "فشار به مواجهه منجر می‌شود",
			"supporting_concept_ids": ["C001", "C002"],
			"evidence": [{
				"paragraph_id": "P001",
				"exact_quote": "فشار زیادی بود",
				"interpretation": "پیوند دارای شاهد",
			}],
			"needs_researcher_review": False,
		}],
	}
	return {
		"project_id": "project-diagram",
		"title": "مصاحبهٔ آزمون",
		"analysis": {
			"schema_version": "2.0",
			"interview_id": "interview-diagram",
			"interview_analysis": interview,
		},
		"researcher_edits": {},
	}


def test_diagram_resolves_saved_categories_edits_and_relationship_evidence():
	project = make_project()
	project["researcher_edits"]["interview_analysis"] = {
		"categories": {"CAT01": {"category_name": "فشار اصلاح‌شده"}},
		"central_phenomenon": {"title": "پدیدهٔ اصلاح‌شده", "definition": "تعریف اصلاح‌شده"},
		"relationships": {"REL01": {"description": "شرح رابطهٔ ویرایش‌شده"}},
	}

	diagram = prepare_paradigm_diagram(project)
	nodes = {node["id"]: node for node in diagram["nodes"]}

	assert nodes["causal"]["items"][0]["title"] == "فشار اصلاح‌شده"
	assert nodes["causal"]["items"][0]["concept_ids"] == ["C001"]
	assert nodes["causal"]["items"][0]["evidence"][0]["paragraph_id"] == "P001"
	assert nodes["phenomenon"]["items"][0]["title"] == "پدیدهٔ اصلاح‌شده"
	assert nodes["phenomenon"]["items"][0]["description"] == "تعریف اصلاح‌شده"
	assert diagram["relationships"][0]["description"] == "شرح رابطهٔ ویرایش‌شده"
	assert diagram["relationships"][0]["documented"] is True
	assert {
		(edge["source"], edge["target"])
		for edge in diagram["edges"]
	} == {
		("causal", "phenomenon"),
		("contextual", "strategies"),
		("intervening", "strategies"),
		("phenomenon", "strategies"),
		("strategies", "consequences"),
	}
	assert any(
		edge["source"] == "causal" and edge["target"] == "phenomenon"
		and edge["kind"] == "structural"
		for edge in diagram["edges"]
	)
	nodes = {node["id"]: node for node in diagram["nodes"]}
	assert nodes["categories"]["items"][0]["title"] == "فشار اصلاح‌شده"
	assert not any(edge["source"] == "categories" for edge in diagram["edges"])


def test_diagram_accepts_flat_paradigm_input_and_preserves_edge_mappings():
	diagram = prepare_paradigm_diagram({
		"causal_conditions": ["علت"],
		"central_phenomenon": "پدیده",
		"contextual_conditions": ["زمینه"],
		"intervening_conditions": ["مداخله‌گر"],
		"strategies": ["راهبرد"],
		"consequences": ["پیامد"],
		"categories": [
			{"category_id": "CAT01", "category_name": "مقوله اول"},
			{"category_id": "CAT02", "category_name": "مقوله دوم"},
		],
	})
	nodes = {node["id"]: node for node in diagram["nodes"]}
	assert nodes["phenomenon"]["items"][0]["title"] == "پدیده"
	assert [item["title"] for item in nodes["categories"]["items"]] == ["مقوله اول", "مقوله دوم"]
	assert {
		(edge["source"], edge["target"])
		for edge in diagram["edges"]
	} == {
		("causal", "phenomenon"),
		("phenomenon", "strategies"),
		("contextual", "strategies"),
		("intervening", "strategies"),
		("strategies", "consequences"),
	}
	assert diagram["layout"] == {"rankdir": "RL", "fontname": "Vazirmatn"}


def test_graphviz_output_has_rtl_font_and_all_paradigm_connections():
	diagram = prepare_paradigm_diagram({
		"causal_conditions": ["علت"],
		"central_phenomenon": "پدیده",
		"contextual_conditions": ["زمینه"],
		"intervening_conditions": ["مداخله"],
		"strategies": ["راهبرد"],
		"consequences": ["پیامد"],
		"categories": [{"category_id": "CAT01", "category_name": "مقوله نمونه"}],
	})
	source = _build_paradigm_graph(diagram, "svg").source

	assert "rankdir=RL" in source
	assert 'fontname=Vazirmatn' in source
	assert "category_0 -> causal" in source
	assert "causal -> phenomenon" in source
	assert "phenomenon -> strategies" in source
	assert "contextual -> strategies" in source
	assert "intervening -> strategies" in source
	assert "strategies -> consequences" in source
	assert "مقوله نمونه" in source
	assert "مقوله یا پدیده محوری" in source
	assert "زمینه" in source
	assert "شرایط علی" in source
	assert "راهبردها" in source
	assert "shape=ellipse" in source
	assert "category_0 -> phenomenon" not in source


def test_graphviz_accepts_sample_legacy_field_names():
	diagram = prepare_paradigm_diagram({
		"categories": [{"category_id": "CAT01", "category_name": "مقوله اولیه"}],
		"causal": ["علت"],
		"central": "پدیده",
		"context": ["زمینه"],
		"intervening": ["شرایط مداخله‌گر"],
		"strategies": ["راهبرد"],
		"consequences": ["پیامد"],
	})
	nodes = {node["id"]: node for node in diagram["nodes"]}

	assert nodes["causal"]["items"][0]["title"] == "علت"
	assert nodes["phenomenon"]["items"][0]["title"] == "پدیده"
	assert nodes["contextual"]["items"][0]["title"] == "زمینه"
	assert nodes["intervening"]["items"][0]["title"] == "شرایط مداخله‌گر"
	assert nodes["strategies"]["items"][0]["title"] == "راهبرد"
	assert nodes["consequences"]["items"][0]["title"] == "پیامد"
	assert nodes["categories"]["items"][0]["title"] == "مقوله اولیه"


def test_paradigm_image_endpoint_returns_download(monkeypatch):
	project = make_project()
	monkeypatch.setattr(results_router, "get_project", lambda _project_id: project)
	monkeypatch.setattr(results_router, "render_paradigm_diagram", lambda _diagram, _format: b"svg")
	client = TestClient(app, follow_redirects=False)

	response = client.get("/results/project-diagram/paradigm/export/svg")

	assert response.status_code == 200
	assert response.headers["content-type"] == "image/svg+xml"
	assert response.headers["content-disposition"] == 'attachment; filename="paradigm-diagram.svg"'
	assert response.content == b"svg"


def test_diagram_refresh_hash_changes_with_stage_two_edits():
	project = make_project()
	original = prepare_paradigm_diagram(project)["source_hash"]
	project["researcher_edits"]["interview_analysis"] = {
		"central_phenomenon": {"title": "پدیدهٔ جدید"},
	}
	assert prepare_paradigm_diagram(project)["source_hash"] != original


def test_paradigm_page_builds_and_rebuilds_without_analysis_execution(monkeypatch):
	project = make_project()
	monkeypatch.setattr(results_router, "get_project", lambda _project_id: project)

	def update_project(_project_id, **changes):
		project.update(changes)
		return project

	monkeypatch.setattr(results_router, "update_project", update_project)
	client = TestClient(app, follow_redirects=False)

	page = client.get("/results/project-diagram/paradigm")
	assert page.status_code == 200
	assert "نمودار نیازمند به‌روزرسانی است" in page.text
	assert "مقوله یا پدیده محوری" in page.text
	assert "فشار کاری" in page.text
	assert "paradigm-node-categories" in page.text
	assert "مقوله مرتبط" not in page.text
	assert "paradigm.js" in page.text
	assert "REL01" in page.text

	rebuild = client.post("/results/project-diagram/paradigm/rebuild")
	assert rebuild.status_code == 303
	assert rebuild.headers["location"] == "/results/project-diagram/paradigm?rebuilt=1"
	assert project["researcher_edits"]["paradigm_source_hash"]

	updated_page = client.get(rebuild.headers["location"])
	assert "نمودار با آخرین داده‌های ذخیره‌شده به‌روز شد" in updated_page.text

	project["researcher_edits"]["interview_analysis"] = {
		"central_phenomenon": {"title": "تغییر پس از نمودار"},
	}
	stale_page = client.get("/results/project-diagram/paradigm")
	assert "نمودار نیازمند به‌روزرسانی است" in stale_page.text
	assert "تغییر پس از نمودار" in stale_page.text
