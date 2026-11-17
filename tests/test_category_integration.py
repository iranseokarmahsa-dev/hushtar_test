from __future__ import annotations

from tests.test_component_synthesis import _create_run

from app.main import app
from app.repositories.synthesis_repository import list_stage_results, save_stage_result
from app.services.synthesis.category_integration_service import (
    COMPONENT_LABELS,
    STAGE_KEY,
    build_category_integration_prompt,
    category_integration_page_data,
    get_approved_category_integrations,
    submit_category_integration_output,
)
from fastapi.testclient import TestClient


def _valid_markdown(prompt: dict, source_item_ids: list[str] | None = None) -> str:
    source_item_ids = source_item_ids if source_item_ids is not None else [
        item["source_item_id"] for item in prompt["source_items"]
    ]
    duplicate_count = len(source_item_ids) - len(set(source_item_ids))
    expected_ids = {item["source_item_id"] for item in prompt["source_items"]}
    missing_count = len(expected_ids - set(source_item_ids))
    unknown_count = len([item for item in source_item_ids if item not in expected_ids])
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
    if source_item_ids:
        lines.extend([
            "### CATEGORY: cat_1",
            "- category_id: cat_1",
            "- title: مقوله تلفیقی",
            "- definition: تعریف تلفیقی مبتنی بر داده",
            "- source_item_ids:",
            *[f"  - {source_id}" for source_id in source_item_ids],
        ])
    else:
        lines.append("NONE")
    lines.extend([
        "",
        "## QUALITY AUDIT",
        f"- total_input_items: {len(prompt['source_items'])}",
        f"- total_assigned_items: {len(source_item_ids)}",
        f"- duplicate_source_items: {duplicate_count}",
        f"- missing_source_items: {missing_count}",
        f"- unknown_source_items: {unknown_count}",
        f"- status: {'FAIL' if missing_count or unknown_count else 'PASS'}",
        "- warnings:",
        "  - NONE",
        "",
        "## END OF RESULT",
    ])
    return "\n".join(lines)


def test_five_component_prompts_use_short_private_item_aliases(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    prompts = [
        build_category_integration_prompt(run["run_id"], component_key)
        for component_key in COMPONENT_LABELS
    ]

    assert len({prompt["component_key"] for prompt in prompts}) == 5
    assert len({prompt["input_hash"] for prompt in prompts}) == 5
    for prompt in prompts:
        assert "ITEM_1" in prompt["prompt_text"] or not prompt["source_items"]
        assert all(item["source_item_id"] not in prompt["prompt_text"] for item in prompt["source_items"])
        assert "METADATA" not in prompt["prompt_text"]
        assert "QUALITY AUDIT" not in prompt["prompt_text"]
        assert "input_hash" not in prompt["prompt_text"]
        assert "## THEMES" not in prompt["prompt_text"]
        assert "ITEM_1" in prompt["item_id_map"] or not prompt["source_items"]
    assert len(prompts[0]["source_items"]) == 2
    assert all(not prompt["source_items"] for prompt in prompts[1:])
    empty_result = submit_category_integration_output(
        run["run_id"],
        prompts[1]["component_key"],
        _valid_markdown(prompts[1]),
    )
    assert empty_result["status"] == "ready_for_researcher_review"

    client = TestClient(app, follow_redirects=False)
    page = client.get(
        f"/synthesis/runs/{run['run_id']}/category-integration",
        params={"component_key": "causal_conditions"},
    )
    assert page.status_code == 200
    assert "راهنمای تلفیق مقوله‌ها" in page.text
    assert "متن پرامپت را کپی کنید و به هوش مصنوعی بدهید" in page.text
    assert "data-copy-category-integration-prompt" in page.text
    assert "integration-items" not in page.text
    assert "اصلاح اختیاری مقوله‌ها" not in page.text
    assert "تأیید نهایی مقوله‌های تلفیقی" not in page.text
    assert 'dir="ltr" spellcheck="false"' in page.text
    downloaded = client.get(
        f"/synthesis/runs/{run['run_id']}/category-integration/prompt.md",
        params={"component_key": "causal_conditions"},
    )
    assert downloaded.status_code == 200
    assert "# COMPONENT SYNTHESIS RESULT" in downloaded.text


def test_markdown_file_upload_is_supported(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    prompt = build_category_integration_prompt(run["run_id"], "causal_conditions")
    client = TestClient(app, follow_redirects=False)
    response = client.post(
        f"/synthesis/runs/{run['run_id']}/category-integration/results",
        data={"component_key": "causal_conditions"},
        files={
            "markdown_file": (
                "answer.md",
                _valid_markdown(prompt).encode("utf-8"),
                "text/markdown",
            )
        },
    )
    assert response.status_code == 303


def test_short_item_aliases_are_mapped_and_duplicate_membership_is_kept(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    prompt = build_category_integration_prompt(run["run_id"], "causal_conditions")
    source_ids = [item["source_item_id"] for item in prompt["source_items"]]
    markdown = """```markdown
COMPONENT SYNTHESIS RESULT
INTEGRATED CATEGORIES
CATEGORY: category_a
- title: مقولهٔ نخست
- definition: تعریف نخست
- items:
  - ITEM_1
  - ITEM_2
CATEGORY: category_b
- title: مقولهٔ دوم
- definition: تعریف دوم
- items:
  - ITEM_1
CATEGORY: broken
این بخش قالب نامعتبر دارد
END OF RESULT
```"""

    result = submit_category_integration_output(
        run["run_id"], "causal_conditions", markdown
    )

    assert result["status"] == "ready_for_researcher_review"
    categories = result["output_json"]["integrated_categories"]
    assert categories[0]["source_item_ids"] == source_ids
    assert categories[1]["source_item_ids"] == [source_ids[0]]
    assert result["validation_report_json"]["valid"] is True


def test_sanitizer_repairs_missing_headings_and_truncates_duplicate_output(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    prompt = build_category_integration_prompt(run["run_id"], "causal_conditions")
    markdown = _valid_markdown(prompt)
    markdown = "\n".join(
        line.lstrip("#").strip() if line.startswith("#") else line
        for line in markdown.splitlines()
    )
    markdown = markdown.replace(
        "COMPONENT SYNTHESIS RESULT",
        "\u200f\\# COMPONENT&#x20;SYNTHESIS RESULT",
        1,
    )
    raw_output = "\ufeff\u200b\u200f```markdown\n" + markdown + "\n\n" + markdown + "\n```"

    result = submit_category_integration_output(
        run["run_id"], "causal_conditions", raw_output
    )

    assert result["status"] == "ready_for_researcher_review"
    assert result["raw_output_text"] == raw_output
    assert result["output_json"]["integrated_categories"][0]["source_item_ids"] == [
        item["source_item_id"] for item in prompt["source_items"]
    ]


def test_researcher_split_preserves_source_coverage_and_uses_sequential_ids(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    component_key = "causal_conditions"
    prompt = build_category_integration_prompt(run["run_id"], component_key)
    source_ids = [item["source_item_id"] for item in prompt["source_items"]]
    submit_category_integration_output(
        run["run_id"], component_key, _valid_markdown(prompt)
    )

    client = TestClient(app)
    response = client.post(
        f"/synthesis/runs/{run['run_id']}/category-integration/review",
        json={
            "component_key": component_key,
            "decision": "split_category",
            "category_id": "cat_1",
            "reason": "تفکیک مفهومی در بازبینی",
            "partitions": [
                {
                    "source_item_ids": [source_ids[0]],
                    "title": "مقوله نخست",
                    "definition": "تعریف نخست",
                },
                {
                    "source_item_ids": [source_ids[1]],
                    "title": "مقوله دوم",
                    "definition": "تعریف دوم",
                },
            ],
        },
    )
    assert response.status_code == 200
    latest = max(
        (
            result for result in list_stage_results(run["run_id"])
            if result["stage_key"] == STAGE_KEY and result["component_key"] == component_key
        ),
        key=lambda result: result["version"],
    )
    categories = latest["output_json"]["integrated_categories"]
    assert [category["category_id"] for category in categories] == ["cat_1", "cat_2"]
    assert sorted(item_id for category in categories for item_id in category["source_item_ids"]) == sorted(source_ids)


def test_duplicate_unknown_and_missing_items_are_reconciled_automatically(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    component_key = "causal_conditions"
    prompt = build_category_integration_prompt(run["run_id"], component_key)
    source_ids = [item["source_item_id"] for item in prompt["source_items"]]
    invalid_coverage = submit_category_integration_output(
        run["run_id"],
        component_key,
        _valid_markdown(prompt, [source_ids[0], source_ids[0], "unknown-source-id"]),
    )
    report = invalid_coverage["validation_report_json"]
    assert invalid_coverage["status"] == "ready_for_researcher_review"
    assert report["valid"] is True
    assert report["missing_source_item_count"] == 0
    assert report["unknown_source_item_count"] == 0
    assert len(invalid_coverage["output_json"]["integrated_categories"]) == 2
    assert invalid_coverage["output_json"]["integrated_categories"][1]["source_item_ids"] == [
        source_ids[1]
    ]

    nonsequential = _valid_markdown(prompt).replace(
        "CATEGORY: cat_1", "CATEGORY: cat_2"
    ).replace("- category_id: cat_1", "- category_id: cat_2")
    invalid_categories = submit_category_integration_output(
        run["run_id"], component_key, nonsequential
    )
    assert invalid_categories["status"] == "ready_for_researcher_review"
    assert invalid_categories["output_json"]["integrated_categories"][0]["category_id"] == "cat_1"


def test_duplicate_source_items_across_categories_are_accepted_with_warning(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    component_key = "causal_conditions"
    prompt = build_category_integration_prompt(run["run_id"], component_key)
    source_ids = [item["source_item_id"] for item in prompt["source_items"]]
    markdown = _valid_markdown(prompt).replace(
        "\n## QUALITY AUDIT",
        "\n### CATEGORY: cat_2\n"
        "- category_id: cat_2\n"
        "- title: وجه دیگر\n"
        "- definition: تعریف وجه دیگر\n"
        "- source_item_ids:\n"
        f"  - {source_ids[0]}\n"
        "\n## QUALITY AUDIT",
    )
    markdown = markdown.replace(
        "- total_assigned_items: 2", "- total_assigned_items: 3"
    ).replace(
        "- duplicate_source_items: 0", "- duplicate_source_items: 1"
    ).replace(
        "  - NONE\n\n## END OF RESULT",
        "  - Source Item در بیش از یک مقوله ثبت شد.\n\n## END OF RESULT",
    )

    result = submit_category_integration_output(run["run_id"], component_key, markdown)
    report = result["validation_report_json"]

    assert result["status"] == "ready_for_researcher_review"
    assert result["output_json"]["integrated_categories"][1]["source_item_ids"] == [
        source_ids[0]
    ]
    assert report["valid"] is True
    assert report["duplicate_source_item_count"] == 1
    assert report["errors"] == []
    review = TestClient(app).post(
        f"/synthesis/runs/{run['run_id']}/category-integration/review",
        json={
            "component_key": component_key,
            "decision": "edit_category",
            "category_id": "cat_1",
            "reason": "تکمیل عنوان",
            "title": "عنوان بازبینی‌شده",
        },
    )
    assert review.status_code == 200


def test_missing_items_are_added_and_versions_are_preserved(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    component_key = "causal_conditions"
    prompt = build_category_integration_prompt(run["run_id"], component_key)
    source_ids = [item["source_item_id"] for item in prompt["source_items"]]
    completed = submit_category_integration_output(
        run["run_id"],
        component_key,
        _valid_markdown(prompt, source_ids[:-1]),
    )
    assert completed["status"] == "ready_for_researcher_review"
    assert completed["validation_report_json"]["missing_source_item_count"] == 0

    client = TestClient(app, follow_redirects=False)
    recovered_response = client.post(
        f"/synthesis/runs/{run['run_id']}/category-integration/results",
        data={
            "component_key": component_key,
            "raw_output_text": _valid_markdown(prompt, source_ids[:-1]),
        },
    )
    assert recovered_response.headers["location"] == (
        f"/synthesis/runs/{run['run_id']}/category-integration"
        "?component_key=contextual_conditions&received=1"
    )
    recovered_page = client.get(recovered_response.headers["location"])
    assert "با موفقیت ثبت شد" in recovered_page.text
    response = client.post(
        f"/synthesis/runs/{run['run_id']}/category-integration/results",
        data={
            "component_key": component_key,
            "raw_output_text": _valid_markdown(prompt),
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/synthesis/runs/{run['run_id']}/category-integration"
        "?component_key=contextual_conditions&received=1"
    )
    next_page = client.get(response.headers["location"])
    assert "با موفقیت ثبت شد" in next_page.text
    assert "شرایط زمینه‌ای" in next_page.text
    latest = max(
        (
            result for result in list_stage_results(run["run_id"])
            if result["stage_key"] == STAGE_KEY and result["component_key"] == component_key
        ),
        key=lambda result: result["version"],
    )
    assert latest["status"] == "ready_for_researcher_review"
    assert latest["approval_status"] == "pending"
    assert component_key not in get_approved_category_integrations(run["run_id"])
    approval = client.post(
        f"/synthesis/runs/{run['run_id']}/category-integration/review",
        json={"component_key": component_key, "decision": "approve_final", "reason": "مقوله‌ها بررسی شدند."},
    )
    assert approval.status_code == 200

    approved = get_approved_category_integrations(run["run_id"])[component_key]
    assert approved["integrated_categories"][0]["review_status"] == "approved"
    library_version = approved["version"]

    next_prompt = build_category_integration_prompt(run["run_id"], component_key)
    next_result = submit_category_integration_output(
        run["run_id"],
        component_key,
        _valid_markdown(next_prompt),
    )
    assert next_result["approval_status"] == "pending"
    assert get_approved_category_integrations(run["run_id"])[component_key]["version"] == library_version

    page_data = category_integration_page_data(run["run_id"], component_key)
    component = next(row for row in page_data["components"] if row["key"] == component_key)
    assert component["approved"] is True
    assert component["latest"]["approval_status"] == "pending"
    assert len(page_data["history"]) == 5


def test_valid_category_outputs_advance_through_all_components(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    client = TestClient(app, follow_redirects=False)
    component_keys = (
        "causal_conditions",
        "contextual_conditions",
        "intervening_conditions",
        "action_interaction_strategies",
        "consequences",
    )

    for index, component_key in enumerate(component_keys):
        prompt = build_category_integration_prompt(run["run_id"], component_key)
        response = client.post(
            f"/synthesis/runs/{run['run_id']}/category-integration/results",
            data={
                "component_key": component_key,
                "raw_output_text": _valid_markdown(prompt),
            },
        )
        assert response.status_code == 303
        if index + 1 < len(component_keys):
            assert response.headers["location"] == (
                f"/synthesis/runs/{run['run_id']}/category-integration"
                f"?component_key={component_keys[index + 1]}&received=1"
            )
        else:
            assert response.headers["location"] == (
                f"/synthesis/runs/{run['run_id']}/theme-abstraction"
                "?component_key=causal_conditions&received=1"
            )


def test_category_edit_stales_only_same_component_dependent_results(monkeypatch, tmp_path):
    run = _create_run(monkeypatch, tmp_path)
    component_key = "causal_conditions"
    prompt = build_category_integration_prompt(run["run_id"], component_key)
    submit_category_integration_output(
        run["run_id"], component_key, _valid_markdown(prompt)
    )
    for key in ("causal_conditions", "contextual_conditions"):
        save_stage_result(
            run["run_id"],
            "theme_abstraction",
            key,
            status="approved",
            approval_status="approved",
            output_json={"themes": []},
        )

    client = TestClient(app)
    response = client.post(
        f"/synthesis/runs/{run['run_id']}/category-integration/review",
        json={
            "component_key": component_key,
            "decision": "edit_category",
            "category_id": "cat_1",
            "title": "عنوان اصلاح‌شده",
            "reason": "اصلاح وابستگی",
        },
    )
    assert response.status_code == 200

    dependent = {
        result["component_key"]: result
        for result in list_stage_results(run["run_id"])
        if result["stage_key"] == "theme_abstraction"
    }
    assert dependent["causal_conditions"]["status"] == "stale"
    assert dependent["contextual_conditions"]["status"] == "approved"
