from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from app.core import database as database_module
from app.core.database import create_project, init_db, update_project
from app.main import app
from app.routers import synthesis as synthesis_router
from app.repositories.synthesis_repository import (
    create_synthesis_run,
    save_run_sources,
)
from app.services.synthesis.category_review_service import record_category_review_decision
from app.services.synthesis.collector_service import collect_run_pools
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS
from app.services.synthesis.theme_review_service import record_theme_review_decision
from app.services.synthesis.theme_synthesis_prompt import build_theme_synthesis_prompt, latest_theme_result
from app.services.synthesis.theme_synthesis_service import submit_theme_synthesis_output
from app.services.synthesis.final_synthesis_service import (
    build_final_model,
    latest_final_model,
    record_phenomenon_review_decision,
    record_relationship_review_decision,
    submit_phenomenon_output,
    submit_relationship_output,
)
from app.services.synthesis.final_diagram_service import prepare_final_model_diagram
from app.services.synthesis.phenomenon_prompt_builder import (
    FinalSynthesisPromptSizeLimitError,
    build_phenomenon_prompt,
)
from app.services.synthesis.phenomenon_prompt_builder import latest_phenomenon_result
from app.services.synthesis.relationship_prompt_builder import build_relationship_prompt
from app.services.synthesis.category_grouping_prompt import build_category_grouping_prompt


def _reset_db(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("HOOSHYAR_DATA_DIR", str(data_dir))
    database_module.DATA_DIR = data_dir
    database_module.DATABASE_PATH = data_dir / "hooshyar.sqlite3"
    init_db()


def _make_project(index: int):
    paragraph_id = f"P{index}"
    quotes = {
        "causal": f"cause evidence {index}",
        "outcome": f"outcome evidence {index}",
        "phenomenon": f"phenomenon evidence {index}",
        "relationship": f"relation evidence {index}",
    }
    categories = [
        {
            "category_id": "CAT_CAUSE",
            "category_name": "شرایط آغازگر",
            "definition": "تعریف شرایط آغازگر",
            "concept_ids": [f"CAUSE{index}"],
            "evidence": [{
                "paragraph_id": paragraph_id,
                "exact_quote": quotes["causal"],
                "interpretation": "زمینه رخداد",
            }],
        },
        {
            "category_id": "CAT_OUTCOME",
            "category_name": "پیامد",
            "definition": "تعریف پیامد",
            "concept_ids": [f"OUTCOME{index}"],
            "evidence": [{
                "paragraph_id": paragraph_id,
                "exact_quote": quotes["outcome"],
                "interpretation": "پیامد رخداد",
            }],
        },
    ]
    paradigm_model = {key: [] for key in POOL_COMPONENT_KEYS}
    paradigm_model["causal_conditions"] = [{
        "category_id": "CAT_CAUSE",
        "evidence": deepcopy(categories[0]["evidence"]),
    }]
    paradigm_model["consequences"] = [{
        "category_id": "CAT_OUTCOME",
        "evidence": deepcopy(categories[1]["evidence"]),
    }]
    quote_text = " ".join(quotes.values())
    return {
        "paragraphs": [{"paragraph_id": paragraph_id, "text": quote_text}],
        "concepts": [
            {"concept_id": f"CAUSE{index}", "paragraph_ids": [paragraph_id]},
            {"concept_id": f"OUTCOME{index}", "paragraph_ids": [paragraph_id]},
        ],
        "analysis": {
            "schema_version": "2.0",
            "analysis_status": "complete",
            "source_scope": {
                "total_paragraphs": 1,
                "total_concepts_received": 2,
                "total_concepts_reviewed": 2,
            },
            "concept_reviews": [
                {"concept_id": f"CAUSE{index}"},
                {"concept_id": f"OUTCOME{index}"},
            ],
            "interview_analysis": {
                "categories": categories,
                "central_phenomenon": {
                    "title": "هماهنگی کنش‌ها",
                    "definition": "سازمان‌دهی پاسخ‌ها در موقعیت مشترک",
                    "category_id": "CAT_CAUSE",
                    "supporting_category_ids": ["CAT_CAUSE", "CAT_OUTCOME"],
                    "evidence": [{
                        "paragraph_id": paragraph_id,
                        "exact_quote": quotes["phenomenon"],
                        "interpretation": "شاهد پدیده",
                    }],
                    "needs_researcher_review": False,
                },
                "relationships": [{
                    "relationship_id": f"REL{index}",
                    "source_category_id": "CAT_CAUSE",
                    "target_category_id": "CAT_OUTCOME",
                    "relationship_type": "influences",
                    "description": "شرایط به پیامد مرتبط است",
                    "reasoning": "پیوند ثبت‌شده در مصاحبه",
                    "evidence": [{
                        "paragraph_id": paragraph_id,
                        "exact_quote": quotes["relationship"],
                        "interpretation": "شاهد پیوند",
                    }],
                    "needs_researcher_review": False,
                }],
                "paradigm_model": paradigm_model,
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
        },
        "researcher_edits": {
            "concept_reviews": {
                f"CAUSE{index}": {"confirmed": True},
                f"OUTCOME{index}": {"confirmed": True},
            },
            "interview_analysis": {"confirmed": True},
        },
    }


def _make_run(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    projects = []
    for index in (1, 2):
        project = create_project(f"مصاحبه {index}", "پژوهش کنش‌ها")
        payload = _make_project(index)
        update_project(
            project["project_id"],
            paragraphs=payload["paragraphs"],
            concepts=payload["concepts"],
            analysis=payload["analysis"],
            researcher_edits=payload["researcher_edits"],
        )
        projects.append(project)
    run = create_synthesis_run("مدل آزمون", "پژوهش کنش‌ها", expected_source_count=2)
    save_run_sources(run["run_id"], [item["project_id"] for item in projects])
    pool = collect_run_pools(run["run_id"])
    assert pool["validation_report"]["valid"]
    return run, pool


def _grouping_markdown(run_id: str, prompt: dict, pool: dict) -> str:
    lines = [
        "# CATEGORY GROUPING RESULT", "", "## METADATA", "",
        "- schema_version: 1.0", f"- run_id: {run_id}",
        "- stage_key: category_grouping", f"- input_hash: {prompt['input_hash']}",
        "", "## COMPONENT RESULTS",
    ]
    total_items = 0
    group_count = 0
    for component_key in POOL_COMPONENT_KEYS:
        items = pool["pools"][component_key]
        lines.extend(("", f"### COMPONENT: {component_key}", "", "#### GROUPS"))
        if items:
            group_count += 1
            total_items += len(items)
            evidence_ids = sorted({
                ref["evidence_ref_id"]
                for item in items
                for ref in item["evidence_refs"]
            })
            lines.extend((
                "", f"##### GROUP: AI_GROUP_{component_key}", "",
                f"- group_id: AI_GROUP_{component_key}",
                f"- component_key: {component_key}",
                "- proposed_category_name: مفهوم مشترک",
                "- definition: تعریف مفهوم مشترک",
                "- source_item_ids:",
                *(f"  - {item['source_item_id']}" for item in items),
                "- reasoning: واحدها معنای مشترکی دارند.",
                "- similarities:", "  - مفهوم مشترک",
                "- differences:", "  - زمینه منبع متفاوت است.",
                "- evidence_ref_ids:", *(f"  - {item}" for item in evidence_ids),
                "- confidence: 0.9",
                "- needs_researcher_review: true",
            ))
        lines.extend(("", "#### STANDALONE SOURCE ITEMS", "- source_item_id: NONE", "", "#### CONTRADICTIONS"))
    lines.extend((
        "", "## QUALITY AUDIT", "",
        f"- total_source_items: {total_items}",
        f"- grouped_source_items: {total_items}",
        "- standalone_source_items: 0",
        f"- total_groups: {group_count}",
        "- total_contradictions: 0",
        "- missing_source_item_ids:", "  - NONE",
        "- duplicate_source_item_ids:", "  - NONE",
        "- unknown_source_item_ids:", "  - NONE",
        "- unknown_evidence_ref_ids:", "  - NONE",
        "- notes:", "  - تمام منابع پوشش داده شدند.",
    ))
    return "\n".join(lines)


def _approve_phase_four(run_id: str, pool: dict) -> None:
    prompt = build_category_grouping_prompt(
        run_id=run_id,
        pool_result=pool,
        source_count=pool["metadata"]["source_count"],
    )
    result = submit_grouping_result_for_test(run_id, prompt, pool)
    assert result["valid"], result["validation_report"]
    for component in result["parsed_result"]["component_results"].values():
        for group in component["groups"]:
            record_category_review_decision(
                run_id,
                decision="approve_group",
                group_id=group["group_id"],
                reason="گروه مفهومی بررسی شد.",
            )
    record_category_review_decision(
        run_id,
        decision="approve_final",
        reason="پوشش فاز چهارم تأیید شد.",
    )


def submit_grouping_result_for_test(run_id: str, prompt: dict, pool: dict):
    from app.services.synthesis.category_grouping_service import submit_grouping_output

    return submit_grouping_output(run_id, _grouping_markdown(run_id, prompt, pool))


def _theme_markdown(prompt: dict) -> str:
    lines = [
        "# THEME SYNTHESIS RESULT", "", "## METADATA", "",
        "- schema_version: 1.0", f"- run_id: {prompt['run_id']}",
        "- stage_key: theme_synthesis", f"- input_hash: {prompt['input_hash']}",
        f"- source_phase_version: {prompt['source_phase_version']}",
        "", "## COMPONENT RESULTS",
    ]
    theme_count = len(prompt["category_units"])
    for component_key in POOL_COMPONENT_KEYS:
        units = [item for item in prompt["category_units"] if item["component_key"] == component_key]
        lines.extend(("", f"### COMPONENT: {component_key}", "", "#### THEMES"))
        for index, unit in enumerate(units):
            lines.extend((
                "", f"##### THEME: AI_THEME_{component_key}_{index}", "",
                f"- theme_id: AI_THEME_{component_key}_{index}",
                f"- component_key: {component_key}",
                "- proposed_theme_name: مفهوم تلفیقی",
                "- definition: تعریف مستند تم تلفیقی",
                "- constituent_category_unit_ids:", f"  - {unit['category_unit_id']}",
                "- reasoning: این واحد با شواهد خود مفهوم را پشتیبانی می‌کند.",
                "- conceptual_scope: محدوده محدود به داده مستند است.",
                "- similarities:", "  - مفهوم در منابع مشترک است.",
                "- differences:", "  - زمینه مصاحبه‌ها متفاوت است.",
                "- evidence_ref_ids:", *(f"  - {item}" for item in unit["evidence_ref_ids"]),
                "- confidence: 0.9",
                "- needs_researcher_review: true",
            ))
        lines.extend(("", "#### UNTHEMED CATEGORY UNITS", "- NONE", "", "#### CONTRADICTIONS"))
    lines.extend((
        "", "## QUALITY AUDIT", "",
        f"- total_category_units: {len(prompt['category_units'])}",
        f"- total_themes: {theme_count}",
        f"- themed_category_units: {theme_count}",
        "- unthemed_category_units: 0",
        "- total_contradictions: 0",
        "- missing_category_unit_ids:", "  - NONE",
        "- duplicate_category_unit_ids:", "  - NONE",
        "- unknown_category_unit_ids:", "  - NONE",
        "- unknown_evidence_ref_ids:", "  - NONE",
        "- notes:", "  - همه Category Unitها پوشش داده شدند.",
    ))
    return "\n".join(lines)


def _approve_phase_five(run_id: str):
    prompt = build_theme_synthesis_prompt(run_id)
    submitted = submit_theme_synthesis_output(run_id, _theme_markdown(prompt))
    assert submitted["valid"], submitted["validation_report"]
    for component in submitted["parsed_result"]["component_results"].values():
        for theme in component["themes"]:
            record_theme_review_decision(
                run_id,
                decision="approve_theme",
                theme_id=theme["theme_id"],
                reason="تم پس از بررسی تعریف و شواهد تأیید شد.",
            )
    result = record_theme_review_decision(
        run_id,
        decision="approve_final",
        reason="پوشش فاز پنجم کامل است.",
    )
    latest = latest_theme_result(run_id)
    assert latest and not latest["stale"], {
        "latest": latest,
        "current": build_theme_synthesis_prompt(run_id, enforce_size=False)["input_hash"],
    }
    return result


def _phenomenon_markdown(prompt: dict) -> str:
    inputs = prompt["inputs"]
    node = inputs["nodes"][0]
    source_phenomenon = inputs["source_phenomena"][0]
    evidence_id = node["evidence_ref_ids"][0]
    reference_ids = [
        node["node_id"],
        node["source_item_ids"][0],
        node["category_unit_ids"][0],
        node["theme_ids"][0],
        source_phenomenon["node_id"],
        evidence_id,
    ]
    lines = [
        "# FINAL PHENOMENON RESULT", "", "## METADATA", "",
        "- schema_version: 1.0", f"- run_id: {prompt['run_id']}",
        "- stage_key: phenomenon_synthesis", f"- input_hash: {prompt['input_hash']}",
        f"- source_phase_version: {prompt['source_phase_version']}",
        "", "## CENTRAL PHENOMENON", "",
        "- phenomenon_id: AI_PHENOMENON_001",
        "- title: هماهنگی کنش‌ها در موقعیت مشترک",
        "- definition: سازمان‌دهی پاسخ‌ها بر پایه شرایط و پیامدهای مشترک",
        "- selection_reason: این مفهوم منابع مستقل و تم‌های تأییدشده را به هم پیوند می‌دهد.",
        "- supporting_node_ids:", f"  - {node['node_id']}",
        "- supporting_source_item_ids:", f"  - {node['source_item_ids'][0]}",
        "- supporting_category_unit_ids:", f"  - {node['category_unit_ids'][0]}",
        "- supporting_theme_ids:", f"  - {node['theme_ids'][0]}",
        "- supporting_source_phenomenon_ids:", f"  - {source_phenomenon['node_id']}",
        "- evidence_ref_ids:", f"  - {evidence_id}",
        "- confidence: 0.85", "- needs_researcher_review: true",
        "", "## ALTERNATIVE INTERPRETATIONS", "- NONE",
        "", "## CONTRADICTIONS", "- NONE",
        "", "## QUALITY AUDIT", "",
        f"- input_source_count: {inputs['source_count']}",
        f"- input_phenomenon_count: {len(inputs['source_phenomena'])}",
        "- reviewed_source_phenomenon_ids:",
        *(f"  - {item['node_id']}" for item in inputs["source_phenomena"]),
        "- reviewed_node_ids:",
        *(f"  - {item['node_id']}" for item in inputs["nodes"]),
        f"- input_theme_count: {sum(item['node_type'] == 'theme' for item in inputs['nodes'])}",
        f"- input_category_unit_count: {sum(len(item['category_unit_ids']) for item in inputs['nodes'])}",
        f"- supporting_reference_count: {len(set(reference_ids))}",
        "- missing_supporting_references:", "  - NONE",
        "- unsupported_claims:", "  - NONE",
        "- contradictions: 0",
        "- analysis_limitations:", "  - NONE",
        "- final_status: complete",
    ]
    return "\n".join(lines)


def _relationship_markdown(prompt: dict) -> str:
    inputs = prompt["inputs"]
    source_relation = inputs["source_relationships"][0]
    node_ids = {item["node_id"] for item in prompt["model_nodes"]}
    source_node = source_relation["source_node_id"]
    target_node = source_relation["target_node_id"]
    linked = {source_node, target_node}
    unlinked = [
        node for node in prompt["model_nodes"]
        if node["node_id"] not in linked
    ]
    evidence_id = source_relation["evidence_ref_ids"][0]
    lines = [
        "# FINAL RELATIONSHIP RESULT", "", "## METADATA", "",
        "- schema_version: 1.0", f"- run_id: {prompt['run_id']}",
        "- stage_key: relationship_synthesis", f"- input_hash: {prompt['input_hash']}",
        f"- source_phase_version: {prompt['source_phase_version']}",
        f"- phenomenon_version: {prompt['phenomenon_version']}",
        "", "## RELATIONSHIPS", "",
        "##### RELATIONSHIP: AI_RELATIONSHIP_001", "",
        "- relationship_id: AI_RELATIONSHIP_001",
        f"- source_node_id: {source_node}", f"- target_node_id: {target_node}",
        "- relationship_type: direct", "- direction: source_to_target",
        "- description: شرایط آغازگر با پیامد در ارتباط است.",
        "- reasoning: رابطه تأییدشده مصاحبه و شاهد آن جهت را پشتیبانی می‌کنند.",
        "- supporting_source_relationship_ids:", f"  - {source_relation['source_relationship_id']}",
        "- supporting_source_phenomenon_ids:", "  - NONE",
        "- supporting_source_item_ids:", *(f"  - {item}" for item in source_relation["source_item_ids"]),
        "- supporting_category_unit_ids:", "  - NONE",
        "- evidence_ref_ids:", f"  - {evidence_id}",
        "- confidence: 0.9", "- needs_researcher_review: true",
        "", "## UNLINKED NODES",
    ]
    for node in unlinked:
        lines.extend((
            "", f"##### UNLINKED: {node['node_id']}", "",
            f"- node_id: {node['node_id']}",
            f"- component_key: {node['component_key']}",
            "- reason: شواهد برای ایجاد رابطه مستقل با سایر نودها کافی نیست.",
            "- needs_researcher_review: true",
        ))
    if not unlinked:
        lines.append("- NONE")
    lines.extend(("", "## CONTRADICTIONS", "- NONE"))
    lines.extend((
        "", "## QUALITY AUDIT", "",
        f"- input_node_count: {len(node_ids)}",
        "- proposed_relationship_count: 1",
        "- direct_relationship_count: 1",
        "- interpretive_relationship_count: 0",
        "- hypothetical_relationship_count: 0",
        f"- unlinked_node_count: {len(unlinked)}",
        "- missing_supporting_references:", "  - NONE",
        "- unsupported_relationships:", "  - NONE",
        "- duplicate_relationships:", "  - NONE",
        "- invalid_node_references:", "  - NONE",
        "- invalid_evidence_references:", "  - NONE",
        "- contradictions: 0",
        "- analysis_limitations:", "  - NONE",
        "- final_status: complete",
    ))
    return "\n".join(lines)


def test_final_synthesis_review_and_model_traceability(monkeypatch, tmp_path):
    run, pool = _make_run(monkeypatch, tmp_path)
    _approve_phase_four(run["run_id"], pool)
    _approve_phase_five(run["run_id"])

    phenomenon_prompt = build_phenomenon_prompt(run["run_id"])
    with pytest.raises(FinalSynthesisPromptSizeLimitError):
        build_phenomenon_prompt(run["run_id"], max_prompt_chars=1)
    phenomenon = submit_phenomenon_output(
        run["run_id"], _phenomenon_markdown(phenomenon_prompt)
    )
    assert phenomenon["valid"], phenomenon["validation_report"]
    assert TestClient(app).get(
        f"/synthesis/runs/{run['run_id']}/phenomenon-synthesis"
    ).status_code == 200
    latest_phenomenon = latest_phenomenon_result(run["run_id"])
    assert latest_phenomenon["stale"] is False, {
        "saved_hash": latest_phenomenon["input_hash"],
        "current": build_phenomenon_prompt(run["run_id"], enforce_size=False)["input_hash"],
        "saved_phase": latest_phenomenon["source_phase_version"],
        "current_phase": build_phenomenon_prompt(run["run_id"], enforce_size=False)["source_phase_version"],
    }
    assert phenomenon["parsed_result"]["central_phenomenon"]["phenomenon_id"].startswith("fphen_")
    approved_phenomenon = record_phenomenon_review_decision(
        run["run_id"],
        decision="approve_phenomenon",
        reason="تعریف و شواهد پدیده بررسی شد.",
    )
    final_phenomenon = record_phenomenon_review_decision(
        run["run_id"],
        decision="approve_final",
        reason="پدیده اصلی تأیید و گزینه‌های جایگزین تعیین تکلیف شدند.",
    )
    assert final_phenomenon["approval_status"] == "approved"
    assert approved_phenomenon["version"] + 1 == final_phenomenon["version"]

    relationship_prompt = build_relationship_prompt(run["run_id"])
    with pytest.raises(FinalSynthesisPromptSizeLimitError):
        build_relationship_prompt(run["run_id"], max_prompt_chars=1)
    relationship = submit_relationship_output(
        run["run_id"], _relationship_markdown(relationship_prompt)
    )
    assert relationship["valid"], relationship["validation_report"]
    assert TestClient(app).get(
        f"/synthesis/runs/{run['run_id']}/relationship-synthesis"
    ).status_code == 200
    relation = relationship["parsed_result"]["relationships"][0]
    record_relationship_review_decision(
        run["run_id"],
        decision="approve_relationship",
        target_id=relation["relationship_id"],
        reason="رابطه و زنجیره شواهد تأیید شد.",
    )
    unlinked = relationship["parsed_result"]["unlinked_nodes"][0]
    record_relationship_review_decision(
        run["run_id"],
        decision="approve_unlinked",
        target_id=unlinked["node_id"],
        reason="نبود رابطه قابل دفاع برای این نود تأیید شد.",
    )
    final_relationships = record_relationship_review_decision(
        run["run_id"],
        decision="approve_final",
        reason="همه روابط و نودهای بدون رابطه تعیین تکلیف شدند.",
    )
    assert final_relationships["approval_status"] == "approved"

    model_result = build_final_model(run["run_id"])
    model = model_result["output_json"]
    assert model_result["approval_status"] == "approved"
    assert len(model["nodes"]) == 3
    assert len(model["edges"]) == 1
    assert len(model["components"]) == 6
    assert model["quality_audit"]["status"] == "valid"
    assert model["quality_audit"]["unlinked_node_count"] == 1
    assert model["quality_audit"]["input_source_count"] == 2
    assert model["quality_audit"]["unresolved_contradiction_count"] == 0
    assert model["source_interview_ids"] == sorted(
        source["source_interview_id"]
        for source in model["source_traceability"]["sources"]
    )
    assert model["source_phase_versions"]["phenomenon_synthesis"] == final_phenomenon["version"]
    assert model["source_phase_versions"]["relationship_synthesis"] == final_relationships["version"]
    assert model["edges"][0]["researcher_approved"] is True
    assert model["edges"][0]["relationship_type"] == "direct"
    assert model["edges"][0]["direction"] == "source_to_target"
    assert model["edges"][0]["evidence_ref_ids"]
    assert model["evidence_index"]
    assert len(model["source_traceability"]["sources"]) == 2
    diagram = prepare_final_model_diagram(model)
    assert len(diagram["nodes"]) == 6
    assert {
        node["id"] for node in diagram["entity_nodes"]
    } == {
        node["node_id"] for node in model["nodes"]
    }
    assert {
        edge["relationship_id"] for edge in diagram["relationships"]
    } == {
        edge["relationship_id"] for edge in model["edges"]
    }
    assert all(
        edge["structural"] and "relationship_id" not in edge
        for edge in diagram["edges"]
        if edge["kind"] == "structural"
    )
    page = TestClient(app).get(f"/synthesis/runs/{run['run_id']}/final-model")
    assert page.status_code == 200
    assert "نمودار مدل نهایی" in page.text
    assert "final-model/export/svg" in page.text
    monkeypatch.setattr(synthesis_router, "render_paradigm_diagram", lambda _diagram, _format: b"image")
    svg = TestClient(app).get(
        f"/synthesis/runs/{run['run_id']}/final-model/export/svg"
    )
    assert svg.status_code == 200
    assert svg.headers["content-type"] == "image/svg+xml"
    assert svg.content == b"image"
    phase_five = latest_theme_result(run["run_id"])
    theme = next(
        theme
        for component in phase_five["output_json"]["component_results"].values()
        for theme in component["themes"]
    )
    record_theme_review_decision(
        run["run_id"],
        decision="edit_theme",
        theme_id=theme["theme_id"],
        proposed_theme_name="تم ویرایش‌شده پس از مدل",
        reason="تغییر فاز پنجم باید خروجی‌های پایین‌دست را منقضی کند.",
    )
    assert latest_final_model(run["run_id"])["stale"] is True


def test_relationship_manual_add_rejects_unknown_nodes(monkeypatch, tmp_path):
    run, pool = _make_run(monkeypatch, tmp_path)
    _approve_phase_four(run["run_id"], pool)
    _approve_phase_five(run["run_id"])
    phenomenon_prompt = build_phenomenon_prompt(run["run_id"])
    submit_phenomenon_output(run["run_id"], _phenomenon_markdown(phenomenon_prompt))
    record_phenomenon_review_decision(
        run["run_id"], decision="approve_phenomenon", reason="تأیید"
    )
    record_phenomenon_review_decision(
        run["run_id"], decision="approve_final", reason="تأیید نهایی"
    )
    prompt = build_relationship_prompt(run["run_id"])
    submitted = submit_relationship_output(run["run_id"], _relationship_markdown(prompt))
    assert submitted["valid"]

    try:
        record_relationship_review_decision(
            run["run_id"],
            decision="add_relationship",
            reason="افزودن آزمایشی",
            source_node_id="unknown-node",
            target_node_id=prompt["model_nodes"][0]["node_id"],
            relationship_type="interpretive",
            direction="source_to_target",
            description="رابطه",
            reasoning="دلیل",
            supporting_source_item_ids=prompt["inputs"]["source_relationships"][0]["source_item_ids"],
        )
    except ValueError as error:
        assert "نودهای معتبر" in str(error)
    else:
        raise AssertionError("manual relationships must reject unknown node ids")


def test_invalid_markdown_evidence_reference_is_saved_as_invalid(monkeypatch, tmp_path):
    run, pool = _make_run(monkeypatch, tmp_path)
    _approve_phase_four(run["run_id"], pool)
    _approve_phase_five(run["run_id"])
    phenomenon_prompt = build_phenomenon_prompt(run["run_id"])
    submit_phenomenon_output(run["run_id"], _phenomenon_markdown(phenomenon_prompt))
    record_phenomenon_review_decision(
        run["run_id"], decision="approve_phenomenon", reason="تأیید"
    )
    record_phenomenon_review_decision(
        run["run_id"], decision="approve_final", reason="تأیید نهایی"
    )
    relationship_prompt = build_relationship_prompt(run["run_id"])
    markdown = _relationship_markdown(relationship_prompt).replace(
        relationship_prompt["inputs"]["source_relationships"][0]["evidence_ref_ids"][0],
        "unknown_evidence",
    )

    result = submit_relationship_output(run["run_id"], markdown)

    assert result["valid"] is False
    assert "unknown_reference_id" in {
        error["issue_type"] for error in result["validation_report"]["errors"]
    }
