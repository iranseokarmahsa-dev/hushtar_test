from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.core import database as database_module
from app.core.database import create_project, delete_project, init_db, update_project
from app.main import app
from app.repositories.synthesis_repository import (
    create_synthesis_run,
    get_next_stage_version,
    get_run_sources,
    list_stage_results,
    save_run_sources,
    save_stage_result,
    update_run_status,
)
from app.services.synthesis.category_grouping_service import build_grouping_prompt, submit_grouping_output
from app.services.synthesis.category_review_service import record_category_review_decision
from app.services.synthesis.collector_service import collect_run_pools
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS
from app.services.synthesis.theme_review_service import record_theme_review_decision
from app.services.synthesis.theme_synthesis_prompt import (
    ThemePromptSizeLimitError,
    build_theme_synthesis_prompt,
    latest_theme_result,
)
from app.services.synthesis.theme_synthesis_service import submit_theme_synthesis_output


def _create_run(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("HOOSHYAR_DATA_DIR", str(data_dir))
    database_module.DATA_DIR = data_dir
    database_module.DATABASE_PATH = data_dir / "hooshyar.sqlite3"
    init_db()
    projects = []
    for index in range(3):
        project = create_project(f"مصاحبه {index + 1}", "موضوع")
        quote = f"عبارت شاهد فاز پنجم {index + 1}"
        category = {
            "category_id": "CAT1",
            "category_name": f"مقوله {index + 1}",
            "definition": f"تعریف مقوله {index + 1}",
            "concept_ids": [f"CONCEPT{index + 1}"],
            "evidence": [{
                "paragraph_id": f"P{index + 1}",
                "exact_quote": quote,
                "interpretation": "شاهد آزمایشی",
            }],
        }
        paradigm_model = {key: [] for key in POOL_COMPONENT_KEYS}
        paradigm_model["causal_conditions"] = [{"category_id": "CAT1", "evidence": category["evidence"]}]
        update_project(
            project["project_id"],
            paragraphs=[{"paragraph_id": f"P{index + 1}", "text": f"قبل {quote} بعد"}],
            analysis={"interview_analysis": {
                "categories": [category],
                "paradigm_model": paradigm_model,
                "central_phenomenon": {"title": "نباید وارد سنتز شود"},
                "relationships": [],
            }},
        )
        projects.append(project)
    run = create_synthesis_run("اجرای تم", "موضوع", expected_source_count=3)
    save_run_sources(run["run_id"], [project["project_id"] for project in projects])
    pool = collect_run_pools(run["run_id"])
    assert pool["validation_report"]["valid"]
    return run, pool, projects


def _grouping_markdown(run_id: str, input_hash: str, pool: dict) -> str:
    items = pool["pools"]["causal_conditions"]
    grouped = items[:2]
    evidence_ids = [
        reference["evidence_ref_id"]
        for item in grouped
        for reference in item["evidence_refs"]
    ]
    lines = [
        "# CATEGORY GROUPING RESULT",
        "",
        "## METADATA",
        "",
        "- schema_version: 1.0",
        f"- run_id: {run_id}",
        "- stage_key: category_grouping",
        f"- input_hash: {input_hash}",
        "",
        "## COMPONENT RESULTS",
    ]
    for component_key in POOL_COMPONENT_KEYS:
        lines.extend(("", f"### COMPONENT: {component_key}", "", "#### GROUPS"))
        if component_key == "causal_conditions":
            lines.extend((
                "", "##### GROUP: AI_GROUP_01", "",
                "- group_id: AI_GROUP_01",
                f"- component_key: {component_key}",
                "- proposed_category_name: مفهوم مشترک",
                "- definition: تعریف مفهوم مشترک",
                "- source_item_ids:",
                *(f"  - {item['source_item_id']}" for item in grouped),
                "- reasoning: واحدها به مفهوم مشترکی اشاره دارند.",
                "- similarities:",
                "  - مفهوم پایه میان دو مقوله مشترک است.",
                "- differences:",
                "  - زمینه منبع متفاوت است.",
                "- evidence_ref_ids:",
                *(f"  - {evidence_id}" for evidence_id in evidence_ids),
                "- confidence: 0.9",
                "- needs_researcher_review: true",
            ))
        lines.extend(("", "#### STANDALONE SOURCE ITEMS", ""))
        standalone_ids = [item["source_item_id"] for item in pool["pools"][component_key] if item not in grouped]
        lines.extend([f"- source_item_id: {item_id}" for item_id in standalone_ids] or ["- source_item_id: NONE"])
        lines.extend(("", "#### CONTRADICTIONS"))
    total = sum(len(pool["pools"][key]) for key in POOL_COMPONENT_KEYS)
    lines.extend((
        "", "## QUALITY AUDIT", "",
        f"- total_source_items: {total}",
        f"- grouped_source_items: {len(grouped)}",
        f"- standalone_source_items: {total - len(grouped)}",
        "- total_groups: 1",
        "- total_contradictions: 0",
        "- missing_source_item_ids:",
        "  - NONE",
        "- duplicate_source_item_ids:",
        "  - NONE",
        "- unknown_source_item_ids:",
        "  - NONE",
        "- unknown_evidence_ref_ids:",
        "  - NONE",
        "- notes:",
        "  - پوشش همه Source Itemها کامل است.",
    ))
    return "\n".join(lines)


def _approved_grouping(run_id: str, pool: dict, *, approve_standalone: bool = False) -> dict:
    prompt = build_grouping_prompt(run_id)
    submitted = submit_grouping_output(run_id, _grouping_markdown(run_id, prompt["input_hash"], pool))
    assert submitted["valid"]
    component = submitted["parsed_result"]["component_results"]["causal_conditions"]
    group = component["groups"][0]
    record_category_review_decision(
        run_id,
        decision="approve_group",
        group_id=group["group_id"],
        reason="گروه مفهومی بازبینی شد.",
    )
    standalone_id = component["standalone_source_items"][0]["source_item_id"]
    record_category_review_decision(
        run_id,
        decision="approve_standalone" if approve_standalone else "exclude_source_item",
        source_item_id=standalone_id,
        reason="مقوله مستقل بررسی و تعیین تکلیف شد.",
    )
    return record_category_review_decision(
        run_id,
        decision="approve_final",
        reason="پوشش فاز چهارم نهایی شد.",
    )


def _theme_markdown(prompt: dict) -> str:
    units = prompt["category_units"]
    assert len(units) in {1, 2}
    unit = units[0]
    evidence_ids = unit["evidence_ref_ids"]
    lines = [
        "# THEME SYNTHESIS RESULT",
        "",
        "## METADATA",
        "",
        "- schema_version: 1.0",
        f"- run_id: {prompt['run_id']}",
        "- stage_key: theme_synthesis",
        f"- input_hash: {prompt['input_hash']}",
        f"- source_phase_version: {prompt['source_phase_version']}",
        "",
        "## COMPONENT RESULTS",
    ]
    for component_key in POOL_COMPONENT_KEYS:
        lines.extend(("", f"### COMPONENT: {component_key}", "", "#### THEMES"))
        if component_key == unit["component_key"]:
            lines.extend((
                "", "##### THEME: AI_THEME_01", "",
                "- theme_id: AI_THEME_01",
                f"- component_key: {component_key}",
                "- proposed_theme_name: مفهوم تلفیقی",
                "- definition: تعریف مفهوم تلفیقی بر پایه ورودی تأییدشده.",
                "- constituent_category_unit_ids:",
                f"  - {unit['category_unit_id']}",
                "- reasoning: این واحد معنای مفهومی مستقل دارد.",
                "- conceptual_scope: محدوده به همان معنای مستند محدود است.",
                "- similarities:",
                "  - وحدت مفهومی در واحد تأییدشده.",
                "- differences:",
                "  - واحد، گروه مقوله تأییدشده است.",
                "- evidence_ref_ids:",
                *(f"  - {evidence_id}" for evidence_id in evidence_ids),
                "- confidence: 0.88",
                "- needs_researcher_review: true",
            ))
        lines.extend(("", "#### UNTHEMED CATEGORY UNITS"))
        if component_key == unit["component_key"] and len(units) == 2:
            unthemed = units[1]
            lines.extend((
                "", f"##### UNTHEMED: {unthemed['category_unit_id']}", "",
                f"- category_unit_id: {unthemed['category_unit_id']}",
                "- reason: این واحد با تم پیشنهادی اشتراک کافی ندارد.",
            ))
        lines.extend(("", "#### CONTRADICTIONS"))
    unthemed_count = len(units) - 1
    lines.extend((
        "", "## QUALITY AUDIT", "",
        f"- total_category_units: {len(units)}",
        "- total_themes: 1",
        "- themed_category_units: 1",
        f"- unthemed_category_units: {unthemed_count}",
        "- total_contradictions: 0",
        "- missing_category_unit_ids:",
        "  - NONE",
        "- duplicate_category_unit_ids:",
        "  - NONE",
        "- unknown_category_unit_ids:",
        "  - NONE",
        "- unknown_evidence_ref_ids:",
        "  - NONE",
        "- notes:",
        "  - همه Category Unitها پوشش داده شدند.",
    ))
    return "\n".join(lines)


def test_theme_prompt_uses_only_approved_phase_four_category_units(monkeypatch, tmp_path):
    run, pool, _projects = _create_run(monkeypatch, tmp_path)
    approved_grouping = _approved_grouping(run["run_id"], pool)
    for source in get_run_sources(run["run_id"]):
        assert delete_project(source["source_project_id"])

    prompt = build_theme_synthesis_prompt(run["run_id"])

    assert prompt["source_phase_version"] == approved_grouping["version"]
    assert len(prompt["category_units"]) == 1
    assert prompt["category_units"][0]["unit_kind"] == "approved_category_group"
    assert "نباید وارد سنتز شود" not in prompt["prompt_text"]
    assert prompt["category_units"][0]["evidence_ref_ids"]


def test_theme_output_review_history_and_source_phase_staleness(monkeypatch, tmp_path):
    run, pool, _ = _create_run(monkeypatch, tmp_path)
    approved_grouping = _approved_grouping(run["run_id"], pool, approve_standalone=True)
    prompt = build_theme_synthesis_prompt(run["run_id"])
    submitted = submit_theme_synthesis_output(run["run_id"], _theme_markdown(prompt))

    assert submitted["valid"] is True, str(submitted["validation_report"]["errors"])
    page_response = TestClient(app).get(f"/synthesis/runs/{run['run_id']}/theme-synthesis")
    assert page_response.status_code == 200
    assert "بازبینی پژوهشگر" in page_response.text
    assert submitted["source_phase_version"] == approved_grouping["version"]
    initial = submitted["parsed_result"]["component_results"]["causal_conditions"]["themes"][0]
    theme_id = initial["theme_id"]
    unthemed_id = submitted["parsed_result"]["component_results"]["causal_conditions"]["unthemed_category_units"][0]["category_unit_id"]
    moved = record_theme_review_decision(
        run["run_id"],
        decision="move_category_unit",
        category_unit_id=unthemed_id,
        target_theme_id=theme_id,
        reason="پس از مقایسه، واحد مستقل در محدوده همین تم قرار گرفت.",
    )
    edited = record_theme_review_decision(
        run["run_id"],
        decision="edit_theme",
        theme_id=theme_id,
        proposed_theme_name="عنوان ویرایش‌شده",
        reason="عنوان برای وضوح مفهومی اصلاح شد.",
    )
    approved = record_theme_review_decision(
        run["run_id"],
        decision="approve_theme",
        theme_id=theme_id,
        reason="تعریف و شواهد بررسی شد.",
    )
    final = record_theme_review_decision(
        run["run_id"],
        decision="approve_final",
        reason="پوشش کامل و مستقل بودن تحلیل تأیید شد.",
    )

    assert moved["version"] == 2
    assert edited["version"] == 3
    assert approved["version"] == 4
    assert final["version"] == 5
    assert final["approval_status"] == "approved"
    assert final["output_json"]["component_results"]["causal_conditions"]["themes"][0]["proposed_theme_name"] == "عنوان ویرایش‌شده"
    assert len(final["researcher_edits_json"]["decisions"]) == 4
    assert final["source_phase_version"] == approved_grouping["version"]

    next_phase_four_version = get_next_stage_version(run["run_id"], "category_grouping", "semantic_groups")
    save_stage_result(
        run["run_id"],
        "category_grouping",
        "semantic_groups",
        version=next_phase_four_version,
        status="approved",
        input_hash=approved_grouping["input_hash"],
        prompt_text=approved_grouping["prompt_text"],
        raw_output_text=approved_grouping["raw_output_text"],
        output_json=approved_grouping["output_json"],
        validation_report_json=approved_grouping["validation_report_json"],
        validation_report_text=approved_grouping["validation_report_text"],
        researcher_edits_json=approved_grouping["researcher_edits_json"],
        approval_status="approved",
    )
    update_run_status(run["run_id"], current_stage="theme_synthesis_approved")

    stale = latest_theme_result(run["run_id"])
    assert stale["stale"] is True
    stored_theme_versions = [
        item for item in list_stage_results(run["run_id"])
        if item["stage_key"] == "theme_synthesis"
    ]
    assert stored_theme_versions[-1]["source_phase_version"] == approved_grouping["version"]


def test_theme_synthesis_page_and_prompt_size_guard(monkeypatch, tmp_path):
    run, pool, _ = _create_run(monkeypatch, tmp_path)
    _approved_grouping(run["run_id"], pool)
    client = TestClient(app)

    response = client.get(f"/synthesis/runs/{run['run_id']}/theme-synthesis")

    assert response.status_code == 200
    assert "فاز پنجم" in response.text
    assert "theme_synthesis" in response.text
    with pytest.raises(ThemePromptSizeLimitError):
        build_theme_synthesis_prompt(run["run_id"], max_prompt_chars=1)
