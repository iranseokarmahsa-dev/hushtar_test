from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from tests.test_component_synthesis import _create_run
from app.main import app
from app.repositories.synthesis_repository import list_stage_results
from app.services.synthesis.category_integration_review_service import (
    record_category_integration_review_decision,
)
from app.services.synthesis.category_integration_service import (
    COMPONENT_LABELS,
    STAGE_KEY as CATEGORY_STAGE_KEY,
    build_category_integration_prompt,
    submit_category_integration_output,
)
from app.services.synthesis.theme_abstraction_service import (
    STAGE_KEY,
    build_theme_abstraction_prompt,
    get_approved_theme_abstractions,
    submit_theme_abstraction_output,
    theme_abstraction_page_data,
)
from app.services.synthesis.final_synthesis_inputs import load_final_synthesis_inputs
from app.services.synthesis.theme_abstraction_review_service import (
    record_theme_abstraction_review_decision,
)


def _category_output(prompt: dict) -> str:
    source_items = prompt["source_items"]
    lines = [
        "# COMPONENT SYNTHESIS RESULT",
        "",
        "## METADATA",
        f"- run_id: {prompt['run_id']}",
        f"- component_key: {prompt['component_key']}",
        f"- input_hash: {prompt['input_hash']}",
        "",
        "## INTEGRATED CATEGORIES",
    ]
    for index, item in enumerate(source_items, start=1):
        lines.extend([
            f"### CATEGORY: cat_{index}",
            f"- category_id: cat_{index}",
            f"- title: مقولهٔ تأییدشدهٔ {index}",
            f"- definition: تعریف مبتنی بر دادهٔ {index}",
            "- source_item_ids:",
            f"  - {item['source_item_id']}",
        ])
    if not source_items:
        lines.append("NONE")
    lines.extend([
        "",
        "## QUALITY AUDIT",
        f"- total_input_items: {len(source_items)}",
        f"- total_assigned_items: {len(source_items)}",
        "- duplicate_source_items: 0",
        "- missing_source_items: 0",
        "- unknown_source_items: 0",
        "- status: PASS",
        "- warnings:",
        "  - NONE",
        "",
        "## END OF RESULT",
    ])
    return "\n".join(lines)


def _submit_categories(run_id: str, component_key: str = "causal_conditions") -> dict:
    prompt = build_category_integration_prompt(run_id, component_key)
    submitted = submit_category_integration_output(
        run_id,
        component_key,
        _category_output(prompt),
    )
    assert submitted["status"] == "ready_for_researcher_review"
    return record_category_integration_review_decision(
        run_id,
        component_key=component_key,
        decision="approve_final",
        reason="مقوله‌های این مؤلفه بررسی و تأیید شدند.",
    )


def _theme_markdown(
    prompt: dict,
    *,
    assign_all: bool = True,
    theme_groups: list[list[str]] | None = None,
) -> str:
    category_ids = [
        category["integrated_category_id"]
        for category in prompt["source_categories"]
    ]
    if theme_groups is None:
        theme_groups = [category_ids] if assign_all and category_ids else []
    themed_ids = [category_id for group in theme_groups for category_id in group]
    unthemed_ids = [category_id for category_id in category_ids if category_id not in themed_ids]
    categories_by_id = {
        category["integrated_category_id"]: category
        for category in prompt["source_categories"]
    }
    lines = [
        "# THEME ABSTRACTION RESULT",
        "",
        "## METADATA",
        f"- run_id: {prompt['run_id']}",
        f"- component_key: {prompt['component_key']}",
        f"- input_hash: {prompt['input_hash']}",
        f"- source_phase_version: {prompt['source_phase_version']}",
        "",
        "## THEMES",
    ]
    if theme_groups:
        for index, group in enumerate(theme_groups, start=1):
            source_item_ids = sorted({
                source_item_id
                for category_id in group
                for source_item_id in categories_by_id[category_id]["source_item_ids"]
            })
            theme_id = f"theme_{index}"
            lines.extend([
                f"### THEME: {theme_id}",
                f"- theme_id: {theme_id}",
                f"- theme_type: {'standalone' if len(group) == 1 else 'shared'}",
                "- title: تم مفهومی",
                "- definition: تعریف تم مبتنی بر مقوله‌های عضو",
                "- rationale: مقوله‌های عضو در معنای مفهومی و جایگاه نظری هم‌راستا هستند.",
                "- abstraction_level: تم معنای کلان مقوله‌های عضو را پوشش می‌دهد.",
                "- preserved_dimensions: تفاوت‌های مقوله‌ها در تعریف حفظ شده‌اند.",
                "- integrated_category_ids:",
                *[f"  - {category_id}" for category_id in group],
                "- source_item_ids:",
                *[f"  - {source_item_id}" for source_item_id in source_item_ids],
            ])
    else:
        lines.append("NONE")
    lines.extend(["", "## CATEGORIES WITHOUT A THEME"])
    if unthemed_ids:
        for category_id in unthemed_ids:
            lines.extend([
                f"### CATEGORY: {category_id}",
                f"- integrated_category_id: {category_id}",
                "- source_item_ids:",
                *[
                    f"  - {source_item_id}"
                    for source_item_id in categories_by_id[category_id]["source_item_ids"]
                ],
                "- reason: اشتراک مفهومی کافی برای ساخت تم وجود ندارد.",
            ])
    else:
        lines.append("NONE")
    assigned = len(themed_ids) + len(unthemed_ids)
    lines.extend([
        "",
        "## QUALITY AUDIT",
        f"- total_input_categories: {len(category_ids)}",
        f"- total_assigned_categories: {assigned}",
        "- duplicate_category_ids: 0",
        f"- missing_category_ids: {len(set(category_ids) - set(themed_ids + unthemed_ids))}",
        "- unknown_category_ids: 0",
        "- status: PASS",
        "- warnings:",
        "  - NONE",
        "",
        "## END OF RESULT",
    ])
    return "\n".join(lines)


def _component_themes_markdown(
    prompt: dict,
    *,
    theme_groups: list[list[str]] | None = None,
) -> str:
    category_ids = [
        category["integrated_category_id"]
        for category in prompt["source_categories"]
    ]
    if theme_groups is None:
        theme_groups = [category_ids] if category_ids else []
    lines = [
        "# COMPONENT THEMES RESULT",
        "",
        "## METADATA",
        f"- run_id: {prompt['run_id']}",
        f"- component_key: {prompt['component_key']}",
        f"- input_hash: {prompt['input_hash']}",
        "",
        "## THEMES",
    ]
    categories_by_id = {
        category["integrated_category_id"]: category
        for category in prompt["source_categories"]
    }
    for index, group in enumerate(theme_groups, start=1):
        lines.extend([
            f"### THEME: theme_{index}",
            f"- theme_id: theme_{index}",
            f"- title: تم مفهومی {index}",
            "- definition: تعریف تحلیلی مبتنی بر مقوله‌های عضو",
            "- integrated_category_ids:",
            *[f"  - {category_id}" for category_id in group],
            "",
        ])
    if not theme_groups:
        lines.append("NONE")
    lines.extend(["", "## END OF RESULT"])
    return "```markdown\n" + "\n".join(lines) + "\n```"


def test_prompt_uses_current_system_validated_categories_from_same_component(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    causal_category_prompt = build_category_integration_prompt(
        run["run_id"], "causal_conditions"
    )
    causal_result = submit_category_integration_output(
        run["run_id"],
        "causal_conditions",
        _category_output(causal_category_prompt),
    )
    assert causal_result["approval_status"] == "pending"
    contextual_prompt = build_category_integration_prompt(
        run["run_id"], "contextual_conditions"
    )
    contextual_result = submit_category_integration_output(
        run["run_id"],
        "contextual_conditions",
        _category_output(contextual_prompt).replace(
            "مقولهٔ تأییدشدهٔ", "مقولهٔ زمینه‌ای"
        ),
    )
    assert contextual_result["approval_status"] == "pending"

    prompt = build_theme_abstraction_prompt(run["run_id"], "causal_conditions")
    assert prompt["source_phase_version"] > 0
    assert len(prompt["source_categories"]) == 2
    assert "مقولهٔ تأییدشدهٔ 1" in prompt["prompt_text"]
    assert "category_id: cat_1" in prompt["prompt_text"]
    assert "مقولهٔ زمینه‌ای" not in prompt["prompt_text"]
    assert "source_origins_json" not in prompt["prompt_text"]
    assert "source_item_ids" not in prompt["prompt_text"]
    assert "عبارت شاهد واقعی" not in prompt["prompt_text"]
    contextual_theme_prompt = build_theme_abstraction_prompt(
        run["run_id"], "contextual_conditions"
    )
    assert contextual_theme_prompt["source_categories"] == []


def test_component_themes_contract_reconstructs_internal_lineage(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    category_prompt = build_category_integration_prompt(run["run_id"], "causal_conditions")
    stage_three = submit_category_integration_output(
        run["run_id"], "causal_conditions", _category_output(category_prompt)
    )
    prompt = build_theme_abstraction_prompt(run["run_id"], "causal_conditions")

    submitted = submit_theme_abstraction_output(
        run["run_id"], "causal_conditions", _component_themes_markdown(prompt)
    )

    assert submitted["status"] == "ready_for_researcher_review"
    assert submitted["source_phase_version"] == stage_three["version"]
    assert submitted["output_json"]["metadata"] == {
        "run_id": run["run_id"],
        "component_key": "causal_conditions",
        "input_hash": prompt["input_hash"],
    }
    theme = submitted["output_json"]["themes"][0]
    assert theme["theme_type"] == "shared"
    assert len(theme["integrated_category_ids"]) == 2
    assert theme["source_item_ids"]
    assert theme["source_origins"]


def test_component_themes_empty_component_accepts_none(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    category_prompt = build_category_integration_prompt(run["run_id"], "contextual_conditions")
    submit_category_integration_output(
        run["run_id"], "contextual_conditions", _category_output(category_prompt)
    )
    prompt = build_theme_abstraction_prompt(run["run_id"], "contextual_conditions")

    submitted = submit_theme_abstraction_output(
        run["run_id"], "contextual_conditions", _component_themes_markdown(prompt)
    )

    assert submitted["status"] == "ready_for_researcher_review"
    assert submitted["output_json"]["themes"] == []


def test_component_themes_contract_feeds_approved_stage_five_library(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    _submit_categories(run["run_id"])
    prompt = build_theme_abstraction_prompt(run["run_id"], "causal_conditions")
    submitted = submit_theme_abstraction_output(
        run["run_id"], "causal_conditions", _component_themes_markdown(prompt)
    )
    record_theme_abstraction_review_decision(
        run["run_id"],
        component_key="causal_conditions",
        decision="approve_theme",
        theme_id="theme_1",
        reason="عنوان و تعریف تم با مقوله‌های عضو سازگار است.",
    )
    approved_result = record_theme_abstraction_review_decision(
        run["run_id"],
        component_key="causal_conditions",
        decision="approve_final",
        reason="کتابخانهٔ مرحلهٔ چهارم تأیید شد.",
    )

    library = get_approved_theme_abstractions(run["run_id"])["causal_conditions"]

    assert library["source_stage_result_id"] == prompt["source_stage_result_id"]
    assert library["stage_result_id"] == approved_result["stage_result_id"]
    assert submitted["output_json"]["source_stage_result_id"] == prompt["source_stage_result_id"]
    assert library["themes"][0]["source_item_ids"]


def test_final_synthesis_inputs_snapshot_all_five_approved_theme_libraries(
    monkeypatch, tmp_path
):
    from tests.test_final_synthesis import (
        _make_run,
        _phenomenon_markdown,
        _relationship_markdown,
    )
    from app.services.synthesis.final_synthesis_service import (
        build_final_model,
        record_phenomenon_review_decision,
        record_relationship_review_decision,
        submit_phenomenon_output,
        submit_relationship_output,
    )
    from app.services.synthesis.phenomenon_prompt_builder import build_phenomenon_prompt
    from app.services.synthesis.relationship_prompt_builder import build_relationship_prompt

    run, _pool = _make_run(monkeypatch, tmp_path)
    for component_key in COMPONENT_LABELS:
        category_prompt = build_category_integration_prompt(run["run_id"], component_key)
        submit_category_integration_output(
            run["run_id"], component_key, _category_output(category_prompt)
        )
        record_category_integration_review_decision(
            run["run_id"], component_key=component_key,
            decision="approve_final", reason="کتابخانه بررسی شد.",
        )
        prompt = build_theme_abstraction_prompt(run["run_id"], component_key)
        submitted = submit_theme_abstraction_output(
            run["run_id"], component_key, _theme_markdown(prompt)
        )
        for theme in submitted["output_json"]["themes"]:
            record_theme_abstraction_review_decision(
                run["run_id"],
                component_key=component_key,
                decision="approve_theme",
                theme_id=theme["theme_id"],
                reason="تم در مرحلهٔ چهارم تأیید شد.",
            )
        for category in submitted["output_json"]["categories_without_a_theme"]:
            record_theme_abstraction_review_decision(
                run["run_id"],
                component_key=component_key,
                decision="approve_unthemed",
                integrated_category_id=category["integrated_category_id"],
                reason="مقوله به‌تنهایی در مدل حفظ شد.",
            )
        record_theme_abstraction_review_decision(
            run["run_id"],
            component_key=component_key,
            decision="approve_final",
            reason="کتابخانهٔ مرحلهٔ چهارم تأیید شد.",
        )

    inputs = load_final_synthesis_inputs(run["run_id"])
    snapshot = inputs["phase_five_result"]["stage_versions"]

    assert inputs["phase_five_result"]["stage_key"] == "theme_abstraction"
    assert {item["component_key"] for item in snapshot} == set(COMPONENT_LABELS)
    assert {node["component_key"] for node in inputs["nodes"]} == {
        "causal_conditions",
        "consequences",
    }
    assert all(node["node_type"] == "theme" for node in inputs["nodes"])

    phenomenon_prompt = build_phenomenon_prompt(run["run_id"])
    phenomenon = submit_phenomenon_output(
        run["run_id"], _phenomenon_markdown(phenomenon_prompt)
    )
    assert phenomenon["valid"], phenomenon["validation_report"]
    record_phenomenon_review_decision(
        run["run_id"],
        decision="approve_phenomenon",
        reason="پدیده محوری بررسی شد.",
    )
    record_phenomenon_review_decision(
        run["run_id"],
        decision="approve_final",
        reason="پدیده محوری تأیید شد.",
    )
    relationship_prompt = build_relationship_prompt(run["run_id"])
    assert all(
        node["node_id"] in relationship_prompt["prompt_text"]
        for node in inputs["nodes"]
    )
    relationships = submit_relationship_output(
        run["run_id"], _relationship_markdown(relationship_prompt)
    )
    assert relationships["valid"], relationships["validation_report"]
    for relation in relationships["parsed_result"]["relationships"]:
        record_relationship_review_decision(
            run["run_id"],
            decision="approve_relationship",
            target_id=relation["relationship_id"],
            reason="رابطه با شواهد پشتیبان تأیید شد.",
        )
    for node in relationships["parsed_result"]["unlinked_nodes"]:
        record_relationship_review_decision(
            run["run_id"],
            decision="approve_unlinked",
            target_id=node["node_id"],
            reason="نبود رابطهٔ قابل دفاع تأیید شد.",
        )
    record_relationship_review_decision(
        run["run_id"],
        decision="approve_final",
        reason="مدل روابط تأیید نهایی شد.",
    )
    model = build_final_model(run["run_id"])["output_json"]
    assert model["quality_audit"]["status"] == "valid"
    assert set(model["source_phase_versions"]["theme_abstraction"]) == set(COMPONENT_LABELS)
    final_page = TestClient(app).get(
        f"/synthesis/runs/{run['run_id']}/final-model"
    )
    assert final_page.status_code == 200
    assert "مرحلهٔ ۵ · مدل پارادایمی نهایی" in final_page.text
    assert "ویرایش چیدمان" in final_page.text


def test_markdown_validation_review_library_and_history(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    stage3_approved = _submit_categories(run["run_id"])
    prompt = build_theme_abstraction_prompt(run["run_id"], "causal_conditions")
    submitted = submit_theme_abstraction_output(
        run["run_id"], "causal_conditions", _theme_markdown(prompt)
    )
    assert submitted["status"] == "ready_for_researcher_review"
    assert submitted["source_phase_version"] == stage3_approved["version"]
    assert get_approved_theme_abstractions(run["run_id"]) == {}

    record_theme_abstraction_review_decision(
        run["run_id"],
        component_key="causal_conditions",
        decision="approve_theme",
        theme_id="theme_1",
        reason="تم با تعریف مقوله‌های عضو سازگار است.",
    )
    approved_theme = record_theme_abstraction_review_decision(
        run["run_id"],
        component_key="causal_conditions",
        decision="approve_final",
        reason="تأیید نهایی پژوهشگر",
    )
    stage3_results = [
        result for result in list_stage_results(run["run_id"])
        if result["stage_key"] == CATEGORY_STAGE_KEY
    ]
    assert stage3_results[-1]["status"] == "approved"
    library = get_approved_theme_abstractions(run["run_id"])["causal_conditions"]
    assert library["stage_result_id"] == approved_theme["stage_result_id"]
    assert library["source_stage_result_id"] == stage3_approved["stage_result_id"]
    assert len(library["themes"][0]["integrated_category_ids"]) == 2

    pending = submit_theme_abstraction_output(
        run["run_id"],
        "causal_conditions",
        "```markdown\n"
        + _theme_markdown(build_theme_abstraction_prompt(run["run_id"], "causal_conditions"))
        + "\n```",
    )
    assert pending["approval_status"] == "pending"
    assert get_approved_theme_abstractions(run["run_id"])["causal_conditions"]["version"] == approved_theme["version"]
    page_data = theme_abstraction_page_data(run["run_id"], "causal_conditions")
    assert len(page_data["history"]) == 4


def test_unthemed_categories_can_be_approved_without_changing_stage_three(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    stage3_approved = _submit_categories(run["run_id"])
    stage3_history_before = [
        (result["version"], result["status"], result["approval_status"])
        for result in list_stage_results(run["run_id"])
        if result["stage_key"] == CATEGORY_STAGE_KEY
    ]
    prompt = build_theme_abstraction_prompt(run["run_id"], "causal_conditions")
    submitted = submit_theme_abstraction_output(
        run["run_id"],
        "causal_conditions",
        _theme_markdown(prompt, assign_all=False),
    )
    assert submitted["status"] == "ready_for_researcher_review"

    for category in submitted["output_json"]["categories_without_a_theme"]:
        record_theme_abstraction_review_decision(
            run["run_id"],
            component_key="causal_conditions",
            decision="approve_unthemed",
            integrated_category_id=category["integrated_category_id"],
            reason="فاقد تم مستقل باقی می‌ماند.",
        )
    final = record_theme_abstraction_review_decision(
        run["run_id"],
        component_key="causal_conditions",
        decision="approve_final",
        reason="تأیید نهایی کتابخانه",
    )
    library = get_approved_theme_abstractions(run["run_id"])["causal_conditions"]
    assert library["themes"] == []
    assert len(library["categories_without_a_theme"]) == 2
    assert library["source_phase_version"] == stage3_approved["version"]
    stage3_history_after = [
        (result["version"], result["status"], result["approval_status"])
        for result in list_stage_results(run["run_id"])
        if result["stage_key"] == CATEGORY_STAGE_KEY
    ]
    assert stage3_history_after == stage3_history_before
    assert final["status"] == "approved"


def test_reviewer_membership_change_recomputes_theme_type_and_lineage(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    _submit_categories(run["run_id"])
    prompt = build_theme_abstraction_prompt(run["run_id"], "causal_conditions")
    category_ids = [
        category["integrated_category_id"]
        for category in prompt["source_categories"]
    ]
    submit_theme_abstraction_output(
        run["run_id"], "causal_conditions", _theme_markdown(prompt)
    )

    reviewed = record_theme_abstraction_review_decision(
        run["run_id"],
        component_key="causal_conditions",
        decision="mark_unthemed",
        integrated_category_id=category_ids[0],
        reason="این مقوله معنای مستقل دارد و با عضو باقی‌مانده هم‌پوشانی مفهومی ندارد.",
    )
    theme = reviewed["output_json"]["themes"][0]
    unresolved = reviewed["output_json"]["categories_without_a_theme"][0]
    categories_by_id = {
        category["integrated_category_id"]: category
        for category in prompt["source_categories"]
    }

    assert theme["theme_type"] == "standalone"
    assert theme["integrated_category_ids"] == [category_ids[1]]
    assert theme["source_item_ids"] == categories_by_id[category_ids[1]]["source_item_ids"]
    assert unresolved["source_item_ids"] == categories_by_id[category_ids[0]]["source_item_ids"]


def test_duplicate_missing_and_unknown_category_ids_fail_audit(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    _submit_categories(run["run_id"])
    prompt = build_theme_abstraction_prompt(run["run_id"], "causal_conditions")
    malformed = _theme_markdown(prompt).replace(
        "  - cat_2",
        "  - cat_1\n  - unknown-category",
    )
    result = submit_theme_abstraction_output(
        run["run_id"], "causal_conditions", malformed
    )
    report = result["validation_report_json"]
    assert result["status"] == "invalid"
    assert report["duplicate_category_count"] == 1
    assert report["missing_category_count"] == 1
    assert report["unknown_category_count"] == 1


def test_empty_approved_component_accepts_empty_theme_result(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    _submit_categories(run["run_id"], "contextual_conditions")
    prompt = build_theme_abstraction_prompt(
        run["run_id"], "contextual_conditions"
    )
    assert prompt["source_categories"] == []
    submitted = submit_theme_abstraction_output(
        run["run_id"],
        "contextual_conditions",
        _theme_markdown(prompt),
    )
    assert submitted["status"] == "ready_for_researcher_review"
    final = record_theme_abstraction_review_decision(
        run["run_id"],
        component_key="contextual_conditions",
        decision="approve_final",
        reason="تأیید کتابخانهٔ خالی",
    )
    assert final["status"] == "approved"
    assert get_approved_theme_abstractions(run["run_id"])["contextual_conditions"]["themes"] == []


def test_single_category_is_a_standalone_theme_with_lineage(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    _submit_categories(run["run_id"])
    prompt = build_theme_abstraction_prompt(run["run_id"], "causal_conditions")
    category_id = prompt["source_categories"][0]["integrated_category_id"]
    result = submit_theme_abstraction_output(
        run["run_id"],
        "causal_conditions",
        _theme_markdown(prompt, theme_groups=[[category_id]]),
    )

    assert result["status"] == "ready_for_researcher_review"
    theme = result["output_json"]["themes"][0]
    category = prompt["source_categories"][0]
    assert theme["theme_type"] == "standalone"
    assert theme["integrated_category_ids"] == [category_id]
    assert theme["source_item_ids"] == category["source_item_ids"]
    assert theme["source_origins"] == category["source_origins"]


def test_theme_source_lineage_must_match_member_categories(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    _submit_categories(run["run_id"])
    prompt = build_theme_abstraction_prompt(run["run_id"], "causal_conditions")
    source_item_id = prompt["source_categories"][0]["source_item_ids"][0]
    malformed = _theme_markdown(prompt).replace(
        f"  - {source_item_id}",
        "  - fabricated-source-item",
        1,
    )

    result = submit_theme_abstraction_output(
        run["run_id"], "causal_conditions", malformed
    )

    assert result["status"] == "invalid"
    assert any(
        "Source Itemهای تم" in error
        for error in result["validation_report_json"]["errors"]
    )


def test_evidence_warning_preserves_lineage_without_blocking_stage_four(monkeypatch, tmp_path):
    from app.services.synthesis import theme_abstraction_service
    from app.services.synthesis.category_grouping_service import _approved_pool

    run = _create_run(monkeypatch, tmp_path)
    _submit_categories(run["run_id"])
    pool, version, count = _approved_pool(run["run_id"], allow_partial_pool=True)
    pool = deepcopy(pool)
    source = pool["pools"]["causal_conditions"][0]
    source["evidence_refs"].append({"evidence_ref_id": "warning-evidence"})
    pool["evidence"].append({
        "evidence_ref_id": "warning-evidence",
        "source_project_id": source["source_project_id"],
        "paragraph_id": "P014",
        "valid": False,
        "validation_issue": "quote_not_in_snapshot",
    })
    pool["validation_report"] = {
        "valid": False,
        "errors": [{"issue_type": "invalid_evidence", "evidence_ref_id": "warning-evidence"}],
    }
    monkeypatch.setattr(theme_abstraction_service, "_approved_pool", lambda *args, **kwargs: (pool, version, count))

    prompt = build_theme_abstraction_prompt(run["run_id"], "causal_conditions")
    result = submit_theme_abstraction_output(run["run_id"], "causal_conditions", _theme_markdown(prompt))

    assert result["status"] == "ready_for_researcher_review"
    assert result["output_json"]["inherited_warnings"][0]["issue_type"] == "quote_not_in_snapshot"
    origins = result["output_json"]["themes"][0]["source_origins"]
    assert any(origin["evidence_warnings"] for origin in origins)
    pool["validation_report"]["errors"].append({"issue_type": "missing_evidence_reference"})
    with pytest.raises(ValueError):
        build_theme_abstraction_prompt(run["run_id"], "causal_conditions")


def test_all_stage_three_outputs_can_feed_stage_four_without_provider_api(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    for component_key in COMPONENT_LABELS:
        category_prompt = build_category_integration_prompt(run["run_id"], component_key)
        submitted = submit_category_integration_output(
            run["run_id"], component_key, _category_output(category_prompt)
        )
        assert submitted["status"] == "ready_for_researcher_review"
        assert submitted["approval_status"] == "pending"
    prompts = {
        component_key: build_theme_abstraction_prompt(run["run_id"], component_key)
        for component_key in COMPONENT_LABELS
    }
    assert set(prompts) == set(COMPONENT_LABELS)
    assert get_approved_theme_abstractions(run["run_id"]) == {}
    response = TestClient(app).post(
        f"/synthesis/runs/{run['run_id']}/theme-abstraction/run"
    )
    assert response.status_code == 404


@pytest.mark.parametrize("field", ["abstraction_level", "preserved_dimensions"])
def test_theme_semantic_fields_are_required(monkeypatch, tmp_path, field):
    run = _create_run(monkeypatch, tmp_path)
    _submit_categories(run["run_id"])
    prompt = build_theme_abstraction_prompt(run["run_id"], "causal_conditions")
    markdown = "\n".join(
        line for line in _theme_markdown(prompt).splitlines()
        if not line.startswith(f"- {field}:")
    )
    result = submit_theme_abstraction_output(run["run_id"], "causal_conditions", markdown)
    assert result["status"] == "invalid"


def test_stage_three_edit_invalidates_only_matching_theme_component(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    _submit_categories(run["run_id"])
    prompt = build_theme_abstraction_prompt(run["run_id"], "causal_conditions")
    submit_theme_abstraction_output(
        run["run_id"], "causal_conditions", _theme_markdown(prompt)
    )

    new_prompt = build_category_integration_prompt(run["run_id"], "causal_conditions")
    submit_category_integration_output(
        run["run_id"], "causal_conditions", _category_output(new_prompt)
    )
    latest_categories = [
        result for result in list_stage_results(run["run_id"])
        if result["stage_key"] == CATEGORY_STAGE_KEY
        and result["component_key"] == "causal_conditions"
    ][-1]
    record_category_integration_review_decision(
        run["run_id"],
        component_key="causal_conditions",
        decision="edit_category",
        category_id=latest_categories["output_json"]["integrated_categories"][0]["category_id"],
        title="عنوان اصلاح‌شده",
        reason="اصلاح نسخهٔ جدید",
    )

    stage4 = [
        result for result in list_stage_results(run["run_id"])
        if result["stage_key"] == STAGE_KEY
    ]
    assert stage4[0]["status"] == "stale"
    assert get_approved_theme_abstractions(run["run_id"]) == {}


def test_theme_abstraction_routes_render_and_accept_markdown(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    for component_key in COMPONENT_LABELS:
        _submit_categories(run["run_id"], component_key)
    prompt = build_theme_abstraction_prompt(run["run_id"], "causal_conditions")
    client = TestClient(app, follow_redirects=False)
    page = client.get(
        f"/synthesis/runs/{run['run_id']}/theme-abstraction",
        params={"component_key": "causal_conditions"},
    )
    assert page.status_code == 200
    assert "مرحله ۴" in page.text
    download = client.get(
        f"/synthesis/runs/{run['run_id']}/theme-abstraction/prompt.md",
        params={"component_key": "causal_conditions"},
    )
    assert download.status_code == 200
    assert "# THEME ABSTRACTION RESULT" in download.text
    response = client.post(
        f"/synthesis/runs/{run['run_id']}/theme-abstraction/results",
        data={
            "component_key": "causal_conditions",
            "raw_output_text": _theme_markdown(prompt),
        },
    )
    assert response.status_code == 303
    assert "component_key=contextual_conditions" in response.headers["location"]
    assert "received=causal_conditions" in response.headers["location"]


def test_fifth_stage_four_approval_enters_six_step_workflow(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    component_keys = list(COMPONENT_LABELS)
    client = TestClient(app)

    for component_key in component_keys:
        _submit_categories(run["run_id"], component_key)
        prompt = build_theme_abstraction_prompt(run["run_id"], component_key)
        submitted = submit_theme_abstraction_output(
            run["run_id"], component_key, _theme_markdown(prompt)
        )
        for theme in submitted["output_json"]["themes"]:
            record_theme_abstraction_review_decision(
                run["run_id"],
                component_key=component_key,
                decision="approve_theme",
                theme_id=theme["theme_id"],
                reason="تم با تعریف مقوله‌های عضو سازگار است.",
            )
        if component_key == component_keys[-1]:
            response = client.post(
                f"/synthesis/runs/{run['run_id']}/theme-abstraction/review",
                json={
                    "component_key": component_key,
                    "decision": "approve_final",
                    "reason": "پنجمین کتابخانهٔ مرحلهٔ ۴ تأیید شد.",
                },
            )
            assert response.status_code == 200
            assert response.json()["redirect_url"] == (
                f"/synthesis/runs/{run['run_id']}/component-synthesis"
            )
        else:
            record_theme_abstraction_review_decision(
                run["run_id"],
                component_key=component_key,
                decision="approve_final",
                reason="کتابخانهٔ مرحلهٔ ۴ تأیید شد.",
            )

    component_page = client.get(
        f"/synthesis/runs/{run['run_id']}/component-synthesis"
    )
    final_page = client.get(
        f"/synthesis/runs/{run['run_id']}/component-synthesis/final"
    )
    assert component_page.status_code == 200
    assert final_page.status_code == 409
