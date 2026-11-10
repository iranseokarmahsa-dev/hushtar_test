from fastapi.testclient import TestClient

from app.main import app
from app.routers import results as results_router
from app.services.paradigm_service import (
    _build_paradigm_graph,
    prepare_paradigm_diagram,
)
from app.services.synthesis.final_diagram_service import (
    _build_final_model_graph,
    prepare_final_model_diagram,
    render_final_model_diagram,
)


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


def test_final_model_diagram_keeps_empirical_edges_separate_from_layout_edges():
	model = {
		"model_version": 3,
		"components": [
			{"component_key": "causal_conditions", "node_ids": ["cause"]},
			{"component_key": "central_phenomenon", "node_ids": ["central"]},
			{"component_key": "contextual_conditions", "node_ids": []},
			{"component_key": "intervening_conditions", "node_ids": []},
			{"component_key": "action_interaction_strategies", "node_ids": []},
			{"component_key": "consequences", "node_ids": ["outcome"]},
		],
		"nodes": [
			{
				"node_id": "cause",
				"node_type": "theme",
				"component_key": "causal_conditions",
				"title": "شرایط آغازگر",
				"definition": "تعریف علت",
				"source_item_ids": ["source-1"],
				"source_project_ids": ["project-1"],
				"source_concept_ids": ["concept-1"],
				"source_subcategory_ids": [],
				"category_unit_ids": ["unit-1"],
				"theme_id": "theme-1",
				"evidence_ref_ids": ["evidence-1"],
			},
			{
				"node_id": "central",
				"node_type": "central_phenomenon",
				"component_key": "central_phenomenon",
				"title": "پدیده محوری",
				"definition": "تعریف پدیده",
				"source_item_ids": [],
				"source_project_ids": ["project-1"],
				"source_concept_ids": [],
				"source_subcategory_ids": [],
				"category_unit_ids": [],
				"evidence_ref_ids": [],
			},
			{
				"node_id": "outcome",
				"node_type": "independent_category",
				"component_key": "consequences",
				"title": "پیامد",
				"definition": "تعریف پیامد",
				"source_item_ids": [],
				"source_project_ids": ["project-1"],
				"source_concept_ids": [],
				"source_subcategory_ids": [],
				"category_unit_ids": ["unit-2"],
				"evidence_ref_ids": [],
			},
		],
		"evidence_index": {
			"evidence-1": {
				"paragraph_id": "P1",
				"evidence_text": "نقل‌قول",
				"interpretation": "تفسیر",
				"source_title": "مصاحبه ۱",
			},
		},
		"edges": [{
			"edge_id": "edge-1",
			"relationship_id": "relation-1",
			"source_node_id": "cause",
			"target_node_id": "central",
			"relationship_type": "hypothetical",
			"direction": "target_to_source",
			"description": "پیوند فرضی",
			"reasoning": "استدلال",
			"supporting_source_relationship_ids": ["source-relation-1"],
			"evidence_ref_ids": ["evidence-1"],
		}],
	}

	diagram = prepare_final_model_diagram(model)
	assert len(diagram["nodes"]) == 6
	assert len(diagram["entity_nodes"]) == 3
	assert len(diagram["edges"]) == len(diagram["layout_edges"]) + 1
	empirical = next(edge for edge in diagram["edges"] if not edge["structural"])
	assert (empirical["source"], empirical["target"]) == ("central", "cause")
	assert empirical["kind"] == "hypothetical"
	assert empirical["evidence"][0]["exact_quote"] == "نقل‌قول"

	source = _build_final_model_graph(diagram, "svg").source
	assert "cluster_causal_conditions" in source
	assert "model_node_0" in source
	assert "style=dotted" in source
	assert "anchor_causal_conditions -> anchor_central_phenomenon" in source
	assert "anchor_causal_conditions -> anchor_central_phenomenon [constraint=true style=invis" in source
	assert "model_node_1 -> model_node_0" in source
	assert "hypothetical: پیوند فرضی" in source
	assert "ساختاری / چیدمان" in source
	assert "Vazirmatn" in source


def test_final_diagram_exports_svg_and_png_from_the_model():
	model = {
		"model_version": 1,
		"components": [
			{"component_key": "causal_conditions", "title": "شرایط علّی", "node_ids": ["theme-1"]},
			{"component_key": "central_phenomenon", "title": "پدیده محوری", "node_ids": ["central-1"]},
			{"component_key": "contextual_conditions", "title": "شرایط زمینه‌ای", "node_ids": []},
			{"component_key": "intervening_conditions", "title": "شرایط مداخله‌گر", "node_ids": []},
			{"component_key": "action_interaction_strategies", "title": "راهبردها", "node_ids": []},
			{"component_key": "consequences", "title": "پیامدها", "node_ids": ["unit-1"]},
		],
		"nodes": [
			{
				"node_id": "theme-1",
				"node_type": "theme",
				"component_key": "causal_conditions",
				"title": "تم فارسی",
				"definition": "تعریف فارسی تم",
				"evidence_ref_ids": [],
			},
			{
				"node_id": "central-1",
				"node_type": "central_phenomenon",
				"component_key": "central_phenomenon",
				"title": "پدیده فارسی",
				"definition": "تعریف پدیده",
				"evidence_ref_ids": [],
			},
			{
				"node_id": "unit-1",
				"node_type": "independent_category",
				"component_key": "consequences",
				"title": "مقوله مستقل فارسی",
				"definition": "تعریف مقوله",
				"evidence_ref_ids": [],
			},
		],
		"edges": [],
		"evidence_index": {},
	}
	diagram = prepare_final_model_diagram(model)
	assert len(diagram["nodes"]) == 6
	assert [item["title"] for item in diagram["nodes"][0]["items"]] == ["تم فارسی"]
	assert [item["title"] for item in diagram["nodes"][5]["items"]] == ["مقوله مستقل فارسی"]
	assert all(not node["items"] for node in diagram["nodes"][2:5])
	assert not [edge for edge in diagram["edges"] if not edge["structural"]]

	svg = render_final_model_diagram(diagram, "svg")
	png = render_final_model_diagram(diagram, "png")
	assert b"<svg" in svg
	assert "Vazirmatn".encode() in svg
	assert "تم فارسی".encode() in svg
	assert "مقوله مستقل فارسی".encode() in svg
	assert png.startswith(b"\x89PNG\r\n\x1a\n")


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
