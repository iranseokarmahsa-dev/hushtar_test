from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.core import database as database_module
from app.core.database import create_project, delete_project, init_db, update_project
from app.main import app
from app.repositories.synthesis_repository import create_synthesis_run, get_run_sources, save_run_sources
from app.services.synthesis.category_grouping_service import (
    build_grouping_prompt,
    submit_grouping_output,
)
from app.services.synthesis.category_grouping_markdown_parser import (
    CategoryGroupingMarkdownError,
    parse_category_grouping_markdown,
)
from app.services.synthesis.category_review_service import record_category_review_decision
from app.services.synthesis.category_review_service import get_approved_category_grouping
from app.services.synthesis.collector_service import collect_run_pools
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS
from app.services.synthesis.category_grouping_prompt import PromptSizeLimitError
from app.services.synthesis import category_grouping_prompt as grouping_prompt_module


def _reset_db(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("HOOSHYAR_DATA_DIR", str(data_dir))
    database_module.DATA_DIR = data_dir
    database_module.DATABASE_PATH = data_dir / "hooshyar.sqlite3"
    init_db()


def _create_run(monkeypatch, tmp_path, source_count: int = 3, component_keys=None):
    _reset_db(monkeypatch, tmp_path)
    projects = []
    for index in range(source_count):
        project = create_project(f"مصاحبه {index + 1}", "موضوع")
        quote = f"عبارت شاهد مصنوعی شماره {index + 1}"
        category = {
            "category_id": "CAT1",
            "category_name": f"مقوله {index + 1}",
            "definition": f"تعریف {index + 1}",
            "concept_ids": [f"CONCEPT{index + 1}"],
            "evidence": [{
                "paragraph_id": f"P{index + 1}",
                "exact_quote": quote,
                "interpretation": "شاهد آزمایشی",
            }],
        }
        component_key = (component_keys or ["causal_conditions"] * source_count)[index]
        paradigm_model = {key: [] for key in POOL_COMPONENT_KEYS}
        paradigm_model[component_key] = [{
            "category_id": "CAT1",
            "evidence": category["evidence"],
        }]
        project_data = {
            "paragraphs": [{"paragraph_id": f"P{index + 1}", "text": f"پیش از {quote} پس از"}],
            "analysis": {"interview_analysis": {
                "categories": [category],
                "paradigm_model": paradigm_model,
                "central_phenomenon": {"title": "پدیده مستقل"},
                "relationships": [],
            }},
        }
        update_project(project["project_id"], **project_data)
        projects.append(project)
    run = create_synthesis_run("اجرای آزمایشی", "موضوع", expected_source_count=source_count)
    save_run_sources(run["run_id"], [project["project_id"] for project in projects])
    pool = collect_run_pools(run["run_id"])
    assert pool["validation_report"]["valid"]
    return run, pool


def _make_markdown(run_id: str, input_hash: str, pool: dict) -> str:
    source_items = pool["pools"]["causal_conditions"]
    grouped = source_items[:2]
    grouped_ids = {item["source_item_id"] for item in grouped}
    total_source_items = sum(len(pool["pools"][key]) for key in POOL_COMPONENT_KEYS)
    evidence_ids = [
        reference["evidence_ref_id"]
        for item in grouped
        for reference in item["evidence_refs"]
    ]
    sections = [
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
        sections.extend(("", f"### COMPONENT: {component_key}", "", "#### GROUPS"))
        if component_key == "causal_conditions":
            sections.extend((
                "",
                "##### GROUP: AI_GROUP_001",
                "",
                "- group_id: AI_GROUP_001",
                f"- component_key: {component_key}",
                "- proposed_category_name: فشار کاری مشترک",
                "- definition: تجربه مشترک فشار کاری",
                "- source_item_ids:",
                *(f"  - {item['source_item_id']}" for item in grouped),
                "- reasoning: تعریف‌ها معنای مشترکی دارند.",
                "- similarities:",
                "  - هر دو به فشار کاری اشاره دارند.",
                "- differences:",
                "  - بافت مصاحبه متفاوت است.",
                "- evidence_ref_ids:",
                *(f"  - {evidence_id}" for evidence_id in evidence_ids),
                "- confidence: 0.85",
                "- needs_researcher_review: false",
            ))
        sections.extend(("", "#### STANDALONE SOURCE ITEMS", ""))
        standalone_ids = [
            item["source_item_id"]
            for item in pool["pools"][component_key]
            if item["source_item_id"] not in grouped_ids
        ]
        if standalone_ids:
            sections.extend(
                f"- source_item_id: {item_id}"
                for item_id in standalone_ids
            )
        else:
            sections.append("- source_item_id: NONE")
        sections.extend(("", "#### CONTRADICTIONS"))
    sections.extend(("", "## QUALITY AUDIT", ""))
    sections.extend((
        f"- total_source_items: {total_source_items}",
        f"- grouped_source_items: {len(grouped)}",
        f"- standalone_source_items: {total_source_items - len(grouped)}",
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
        "  - همه Source Itemها تعیین تکلیف شدند.",
    ))
    return "\n".join(sections)


def _append_group(markdown: str, pool: dict, component_key: str, items: list[dict], group_id: str) -> str:
    evidence_ids = [
        reference["evidence_ref_id"]
        for item in items
        for reference in item["evidence_refs"]
    ]
    group = [
        "",
        f"##### GROUP: {group_id}",
        "",
        "- group_id: AI_GROUP_002",
        f"- component_key: {component_key}",
        f"- proposed_category_name: مقوله گروه {group_id}",
        f"- definition: تعریف گروه {group_id}",
        "- source_item_ids:",
        *(f"  - {item['source_item_id']}" for item in items),
        "- reasoning: این دو مقوله معنای مشترکی دارند.",
        "- similarities:",
        "  - مفهوم مشترک دارند.",
        "- differences:",
        "  - زمینه آن‌ها متفاوت است.",
        "- evidence_ref_ids:",
        *(f"  - {evidence_id}" for evidence_id in evidence_ids),
        "- confidence: 0.8",
        "- needs_researcher_review: false",
    ]
    component_marker = f"### COMPONENT: {component_key}"
    before_component, component_section = markdown.split(component_marker, 1)
    marker = "\n#### STANDALONE SOURCE ITEMS"
    component_prefix, standalone_section = component_section.split(marker, 1)
    standalone_values, contradictions = standalone_section.split("#### CONTRADICTIONS", 1)
    standalone_values = "\n".join(
        line for line in standalone_values.splitlines()
        if not line.strip().startswith("- source_item_id:")
    )
    markdown = (
        before_component
        + component_marker
        + component_prefix
        + "\n".join(group)
        + marker
        + standalone_values
        + "\n- source_item_id: NONE\n#### CONTRADICTIONS"
        + contradictions
    )
    markdown = markdown.replace(
        "- grouped_source_items: 2",
        f"- grouped_source_items: {2 + len(items)}",
    )
    markdown = markdown.replace(
        "- standalone_source_items: 2",
        f"- standalone_source_items: {2 - len(items)}",
    )
    markdown = markdown.replace("- total_groups: 1", "- total_groups: 2")
    return markdown


def _append_second_group(markdown: str, pool: dict) -> str:
    return _append_group(
        markdown,
        pool,
        "causal_conditions",
        pool["pools"]["causal_conditions"][2:4],
        "AI_GROUP_002",
    )


def test_markdown_parser_rejects_missing_sections():
    with pytest.raises(CategoryGroupingMarkdownError):
        parse_category_grouping_markdown("# CATEGORY GROUPING RESULT\n\n## METADATA\n")


def test_grouping_output_is_validated_and_versioned(monkeypatch, tmp_path):
    run, pool = _create_run(monkeypatch, tmp_path)
    for source in get_run_sources(run["run_id"]):
        assert delete_project(source["source_project_id"])
    prompt = build_grouping_prompt(run["run_id"])
    raw_output = _make_markdown(run["run_id"], prompt["input_hash"], pool)

    result = submit_grouping_output(run["run_id"], raw_output)

    assert result["valid"] is True
    assert result["version"] == 1
    assert result["parsed_result"]["metadata"]["run_id"] == run["run_id"]
    assert len(result["parsed_result"]["component_results"]["causal_conditions"]["groups"]) == 1
    assert result["parsed_result"]["component_results"]["contextual_conditions"]["groups"] == []


def test_repeated_ai_output_keeps_persistent_group_id_and_new_raw_version(monkeypatch, tmp_path):
    run, pool = _create_run(monkeypatch, tmp_path)
    prompt = build_grouping_prompt(run["run_id"])
    raw_output = _make_markdown(run["run_id"], prompt["input_hash"], pool)

    first = submit_grouping_output(run["run_id"], raw_output)
    second = submit_grouping_output(run["run_id"], raw_output)

    first_group = first["parsed_result"]["component_results"]["causal_conditions"]["groups"][0]
    second_group = second["parsed_result"]["component_results"]["causal_conditions"]["groups"][0]
    assert first["version"] == 1
    assert second["version"] == 2
    assert first_group["group_id"] == second_group["group_id"]
    from app.repositories.synthesis_repository import list_stage_results
    versions = [
        item for item in list_stage_results(run["run_id"])
        if item["stage_key"] == "category_grouping"
    ]
    assert len(versions) == 2
    assert all(item["raw_output_text"] == raw_output for item in versions)


def test_researcher_decisions_preserve_coverage_and_approve_final(monkeypatch, tmp_path):
    run, pool = _create_run(monkeypatch, tmp_path)
    prompt = build_grouping_prompt(run["run_id"])
    raw_output = _make_markdown(run["run_id"], prompt["input_hash"], pool)
    grouping = submit_grouping_output(run["run_id"], raw_output)
    group = grouping["parsed_result"]["component_results"]["causal_conditions"]["groups"][0]

    approved_group = record_category_review_decision(
        run["run_id"],
        decision="approve_group",
        group_id=group["group_id"],
        reason="معنا و شواهد گروه بازبینی شد.",
    )
    standalone_id = approved_group["output_json"]["component_results"]["causal_conditions"]["standalone_source_items"][0]["source_item_id"]
    approved_standalone = record_category_review_decision(
        run["run_id"],
        decision="approve_standalone",
        source_item_id=standalone_id,
        reason="مفهوم به‌صورت مستقل حفظ شود.",
    )
    final = record_category_review_decision(
        run["run_id"],
        decision="approve_final",
        reason="پوشش کامل ورودی بررسی شد.",
    )

    assert approved_group["version"] == 2
    assert approved_standalone["version"] == 3
    assert final["version"] == 4
    assert final["approval_status"] == "approved"
    assert final["output_json"]["component_results"]["causal_conditions"]["groups"][0]["review_status"] == "approved"
    assert final["researcher_edits_json"]["decisions"][-1]["operation"] == "approve_final"
    assert get_approved_category_grouping(run["run_id"])["metadata"]["run_id"] == run["run_id"]


def test_excluded_source_item_is_not_returned_for_downstream_use(monkeypatch, tmp_path):
    run, pool = _create_run(monkeypatch, tmp_path)
    prompt = build_grouping_prompt(run["run_id"])
    grouping = submit_grouping_output(
        run["run_id"],
        _make_markdown(run["run_id"], prompt["input_hash"], pool),
    )
    component = grouping["parsed_result"]["component_results"]["causal_conditions"]
    group = component["groups"][0]
    standalone_id = component["standalone_source_items"][0]["source_item_id"]
    record_category_review_decision(
        run["run_id"],
        decision="approve_group",
        group_id=group["group_id"],
        reason="گروه تأیید شد.",
    )
    record_category_review_decision(
        run["run_id"],
        decision="exclude_source_item",
        source_item_id=standalone_id,
        reason="این مورد با دامنه تحلیل هم‌خوان نیست.",
    )
    record_category_review_decision(
        run["run_id"],
        decision="approve_final",
        reason="گروه‌های تأییدشده نهایی شدند.",
    )

    downstream = get_approved_category_grouping(run["run_id"])

    assert downstream["component_results"]["causal_conditions"]["standalone_source_items"] == []
    assert len(downstream["component_results"]["causal_conditions"]["groups"]) == 1


def test_group_rejection_keeps_members_and_requires_standalone_decisions(monkeypatch, tmp_path):
    run, pool = _create_run(monkeypatch, tmp_path, source_count=2)
    prompt = build_grouping_prompt(run["run_id"])
    grouping = submit_grouping_output(
        run["run_id"],
        _make_markdown(run["run_id"], prompt["input_hash"], pool),
    )
    group = grouping["parsed_result"]["component_results"]["causal_conditions"]["groups"][0]

    rejected = record_category_review_decision(
        run["run_id"],
        decision="reject_group",
        group_id=group["group_id"],
        reason="مفهوم اعضا یکسان نیست.",
    )
    component = rejected["output_json"]["component_results"]["causal_conditions"]
    assert component["groups"] == []
    assert len(component["standalone_source_items"]) == 2

    for standalone in component["standalone_source_items"]:
        record_category_review_decision(
            run["run_id"],
            decision="approve_standalone",
            source_item_id=standalone["source_item_id"],
            reason="به‌صورت مستقل حفظ شود.",
        )


def test_merge_and_split_preserve_source_item_coverage(monkeypatch, tmp_path):
    run, pool = _create_run(monkeypatch, tmp_path, source_count=4)
    prompt = build_grouping_prompt(run["run_id"])
    raw_output = _append_second_group(
        _make_markdown(run["run_id"], prompt["input_hash"], pool),
        pool,
    )
    grouping = submit_grouping_output(run["run_id"], raw_output)
    assert grouping["valid"]
    groups = grouping["parsed_result"]["component_results"]["causal_conditions"]["groups"]

    merged = record_category_review_decision(
        run["run_id"],
        decision="merge_groups",
        group_id=groups[0]["group_id"],
        target_group_id=groups[1]["group_id"],
        reason="هم‌پوشانی مفهومی پس از بازبینی تأیید شد.",
    )
    merged_component = merged["output_json"]["component_results"]["causal_conditions"]
    merged_group = merged_component["groups"][0]
    assert len(merged_component["groups"]) == 1
    assert len(merged_group["source_item_ids"]) == 4

    split = record_category_review_decision(
        run["run_id"],
        decision="split_group",
        group_id=merged_group["group_id"],
        partitions=[
            {"source_item_ids": merged_group["source_item_ids"][:2]},
            {"source_item_ids": merged_group["source_item_ids"][2:]},
        ],
        reason="اعضا به دو مفهوم مجزا تعلق دارند.",
    )
    split_component = split["output_json"]["component_results"]["causal_conditions"]
    assert len(split_component["groups"]) == 2
    assert len(split_component["standalone_source_items"]) == 0


def test_moving_item_preserves_group_minimum_and_full_coverage(monkeypatch, tmp_path):
    run, pool = _create_run(monkeypatch, tmp_path, source_count=4)
    prompt = build_grouping_prompt(run["run_id"])
    raw_output = _append_second_group(
        _make_markdown(run["run_id"], prompt["input_hash"], pool),
        pool,
    )
    grouping = submit_grouping_output(run["run_id"], raw_output)
    groups = grouping["parsed_result"]["component_results"]["causal_conditions"]["groups"]
    moved_item_id = groups[0]["source_item_ids"][0]

    result = record_category_review_decision(
        run["run_id"],
        decision="move_item",
        source_item_id=moved_item_id,
        target_group_id=groups[1]["group_id"],
        reason="مقوله به گروه مقصد نزدیک‌تر است.",
    )

    component = result["output_json"]["component_results"]["causal_conditions"]
    assert len(component["groups"]) == 1
    assert len(component["groups"][0]["source_item_ids"]) == 3
    assert [item["source_item_id"] for item in component["standalone_source_items"]] == [
        groups[0]["source_item_ids"][1]
    ]


def test_ai_and_researcher_cannot_merge_different_components(monkeypatch, tmp_path):
    run, pool = _create_run(
        monkeypatch,
        tmp_path,
        source_count=4,
        component_keys=[
            "causal_conditions",
            "causal_conditions",
            "contextual_conditions",
            "contextual_conditions",
        ],
    )
    prompt = build_grouping_prompt(run["run_id"])
    raw_output = _append_group(
        _make_markdown(run["run_id"], prompt["input_hash"], pool),
        pool,
        "contextual_conditions",
        pool["pools"]["contextual_conditions"],
        "AI_GROUP_002",
    )
    grouping = submit_grouping_output(run["run_id"], raw_output)
    assert grouping["valid"]
    causal_group = grouping["parsed_result"]["component_results"]["causal_conditions"]["groups"][0]
    contextual_group = grouping["parsed_result"]["component_results"]["contextual_conditions"]["groups"][0]

    with pytest.raises(ValueError, match="مؤلفه‌های پارادایمی متفاوت"):
        record_category_review_decision(
            run["run_id"],
            decision="merge_groups",
            group_id=causal_group["group_id"],
            target_group_id=contextual_group["group_id"],
            reason="این تصمیم باید رد شود.",
        )


def test_grouping_ui_exposes_prompt_and_output_submission(monkeypatch, tmp_path):
    run, pool = _create_run(monkeypatch, tmp_path)
    client = TestClient(app)
    response = client.get(f"/synthesis/runs/{run['run_id']}/category-grouping")
    assert response.status_code == 200
    assert "Prompt مستقل Markdown" in response.text
    assert "central_phenomenon" not in response.text

    prompt = build_grouping_prompt(run["run_id"])
    response = client.post(
        f"/synthesis/runs/{run['run_id']}/category-grouping/results",
        data={"raw_output_text": _make_markdown(run["run_id"], prompt["input_hash"], pool)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    review_page = client.get(response.headers["location"])
    assert review_page.status_code == 200
    assert "بازبینی پژوهشگر" in review_page.text


def test_prompt_limit_stops_without_truncating_or_persisting(monkeypatch, tmp_path):
    run, pool = _create_run(monkeypatch, tmp_path)
    monkeypatch.setattr(grouping_prompt_module, "SYNTHESIS_CATEGORY_GROUPING_MAX_PROMPT_CHARS", 100)

    with pytest.raises(PromptSizeLimitError) as raised:
        build_grouping_prompt(run["run_id"])

    assert raised.value.report["allowed"] is False
    assert raised.value.report["source_item_count"] == 3
    assert raised.value.report["component_chars"]["causal_conditions"] > 0
    assert len(collect_run_pools(run["run_id"])["pools"]["causal_conditions"]) == 3


def test_unknown_evidence_is_rejected_and_raw_markdown_is_preserved(monkeypatch, tmp_path):
    run, pool = _create_run(monkeypatch, tmp_path)
    prompt = build_grouping_prompt(run["run_id"])
    raw_output = _make_markdown(run["run_id"], prompt["input_hash"], pool)
    valid_evidence = pool["pools"]["causal_conditions"][0]["evidence_refs"][0]["evidence_ref_id"]
    invalid_output = raw_output.replace(valid_evidence, "invented-evidence-id", 1)

    result = submit_grouping_output(run["run_id"], invalid_output)

    assert result["valid"] is False
    assert any(error["issue_type"] == "unknown_evidence_ref_id" for error in result["validation_report"]["errors"])
    from app.repositories.synthesis_repository import list_stage_results
    saved = list_stage_results(run["run_id"])[-1]
    assert saved["raw_output_text"] == invalid_output


def test_singleton_or_duplicate_source_item_group_is_rejected(monkeypatch, tmp_path):
    run, pool = _create_run(monkeypatch, tmp_path, source_count=2)
    prompt = build_grouping_prompt(run["run_id"])
    raw_output = _make_markdown(run["run_id"], prompt["input_hash"], pool)
    first_id, second_id = [
        item["source_item_id"]
        for item in pool["pools"]["causal_conditions"]
    ]
    invalid_output = raw_output.replace(f"  - {second_id}", f"  - {first_id}", 1)

    result = submit_grouping_output(run["run_id"], invalid_output)

    assert result["valid"] is False
    issues = {error["issue_type"] for error in result["validation_report"]["errors"]}
    assert "duplicate_source_item_in_group" in issues
    assert "missing_source_item_id" in issues


def test_repeated_evidence_quote_is_rejected(monkeypatch, tmp_path):
    run, pool = _create_run(monkeypatch, tmp_path, source_count=2)
    prompt = build_grouping_prompt(run["run_id"])
    raw_output = _make_markdown(run["run_id"], prompt["input_hash"], pool)
    quote = pool["evidence"][0]["quote"]
    invalid_output = raw_output.replace(
        "تعریف‌ها معنای مشترکی دارند.",
        quote,
        1,
    )

    result = submit_grouping_output(run["run_id"], invalid_output)

    assert result["valid"] is False
    assert any(
        error["issue_type"] == "evidence_quote_repeated_in_output"
        for error in result["validation_report"]["errors"]
    )
