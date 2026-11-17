from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Any

from app.repositories.synthesis_repository import (
    get_next_stage_version,
    list_stage_results,
    mark_stage_results_stale,
    save_stage_result,
)
from app.services.synthesis.category_integration_service import (
    DEPENDENT_STAGE_KEY,
    STAGE_KEY,
    build_category_integration_prompt,
)
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS

DECISIONS = {
    "approve_final",
    "edit_category",
    "merge_categories",
    "split_category",
    "move_source_item",
}


def _latest_result(run_id: str, component_key: str) -> dict[str, Any] | None:
    results = [
        item for item in list_stage_results(run_id)
        if item["stage_key"] == STAGE_KEY and item["component_key"] == component_key
    ]
    return max(results, key=lambda item: item["version"]) if results else None


def _find_category(categories: list[dict[str, Any]], category_id: str) -> dict[str, Any]:
    for category in categories:
        if category.get("category_id") == category_id:
            return category
    raise ValueError("مقولهٔ تلفیقی موردنظر پیدا نشد.")


def _validate_coverage(
    categories: list[dict[str, Any]],
    expected_ids: set[str],
) -> None:
    seen: list[str] = []
    category_ids: set[str] = set()
    for category in categories:
        category_id = str(category.get("category_id") or "")
        if not category_id or category_id in category_ids:
            raise ValueError("شناسهٔ مقوله خالی یا تکراری است.")
        category_ids.add(category_id)
        if not str(category.get("title") or "").strip() or not str(category.get("definition") or "").strip():
            raise ValueError("عنوان و تعریف همهٔ مقوله‌ها باید تکمیل شود.")
        item_ids = category.get("source_item_ids")
        if not isinstance(item_ids, list) or not item_ids:
            raise ValueError("هر مقولهٔ تلفیقی باید دست‌کم یک Source Item داشته باشد.")
        seen.extend(str(item_id) for item_id in item_ids)
    if any(item_id not in expected_ids for item_id in seen):
        raise ValueError("Source Item ناشناخته در مقوله‌ها وجود دارد.")
    if set(seen) != expected_ids:
        raise ValueError("پوشش Source Itemها کامل نیست.")


def record_category_integration_review_decision(
    run_id: str,
    *,
    component_key: str,
    decision: str,
    reason: str,
    category_id: str | None = None,
    target_category_id: str | None = None,
    source_item_id: str | None = None,
    title: str | None = None,
    definition: str | None = None,
    partitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if component_key not in POOL_COMPONENT_KEYS:
        raise ValueError("مؤلفهٔ درخواستی معتبر نیست.")
    if decision not in DECISIONS:
        raise ValueError("عملیات بازبینی پشتیبانی نمی‌شود.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("ثبت دلیل برای هر تصمیم پژوهشگر الزامی است.")

    latest = _latest_result(run_id, component_key)
    if latest is None or latest.get("status") == "invalid":
        raise ValueError("خروجی معتبر برای بازبینی این مؤلفه وجود ندارد.")
    current = build_category_integration_prompt(run_id, component_key)
    if latest["input_hash"] != current["input_hash"] or latest.get("status") == "stale":
        raise ValueError("ورودی این مؤلفه تغییر کرده است؛ خروجی قدیمی قابل بازبینی نیست.")
    if not (latest.get("validation_report_json") or {}).get("valid"):
        raise ValueError("خروجی Markdown نامعتبر است و بازبینی پژوهشگر روی آن مجاز نیست.")

    output = deepcopy(latest.get("output_json") or {})
    categories = output.get("integrated_categories")
    if not isinstance(categories, list):
        raise ValueError("ساختار کتابخانهٔ مقوله‌های تلفیقی معتبر نیست.")
    expected_ids = {str(item["source_item_id"]) for item in current["source_items"]}
    edits = deepcopy(latest.get("researcher_edits_json") or {"decisions": []})
    edits.setdefault("decisions", [])
    before: Any = None
    after: Any = None
    targets: list[str] = []

    if decision == "approve_final":
        before = {"approval_status": latest.get("approval_status")}
        for category in categories:
            category["review_status"] = "approved"
        after = {"approval_status": "approved"}

    elif decision == "edit_category":
        if not category_id:
            raise ValueError("شناسهٔ مقوله الزامی است.")
        category = _find_category(categories, category_id)
        before = deepcopy(category)
        targets = [category_id]
        if title is None and definition is None:
            raise ValueError("عنوان یا تعریف جدید الزامی است.")
        if title is not None:
            if not title.strip():
                raise ValueError("عنوان مقوله نمی‌تواند خالی باشد.")
            category["title"] = title.strip()
        if definition is not None:
            if not definition.strip():
                raise ValueError("تعریف مقوله نمی‌تواند خالی باشد.")
            category["definition"] = definition.strip()
        category["review_status"] = "approved"
        after = deepcopy(category)

    elif decision == "merge_categories":
        if not category_id or not target_category_id or category_id == target_category_id:
            raise ValueError("برای ادغام باید دو شناسهٔ مقولهٔ متفاوت مشخص شوند.")
        first = _find_category(categories, category_id)
        second = _find_category(categories, target_category_id)
        before = {"first": deepcopy(first), "second": deepcopy(second)}
        first["source_item_ids"] = list(dict.fromkeys(first["source_item_ids"] + second["source_item_ids"]))
        if title is not None:
            if not title.strip():
                raise ValueError("عنوان مقوله نمی‌تواند خالی باشد.")
            first["title"] = title.strip()
        if definition is not None:
            if not definition.strip():
                raise ValueError("تعریف مقوله نمی‌تواند خالی باشد.")
            first["definition"] = definition.strip()
        first["review_status"] = "approved"
        categories.remove(second)
        targets = [category_id, target_category_id]
        after = deepcopy(first)

    elif decision == "split_category":
        if not category_id or not partitions:
            raise ValueError("برای تقسیم باید مقوله و بخش‌های جدید مشخص شوند.")
        category = _find_category(categories, category_id)
        original_ids = set(category.get("source_item_ids") or [])
        flattened = [
            str(item_id)
            for partition in partitions
            for item_id in partition.get("source_item_ids", [])
        ]
        if set(flattened) != original_ids or len(flattened) != len(original_ids):
            raise ValueError("اعضای مقوله باید دقیقاً یک‌بار میان بخش‌های جدید تقسیم شوند.")
        if any(not str(partition.get("title") or "").strip() or not str(partition.get("definition") or "").strip() for partition in partitions):
            raise ValueError("عنوان و تعریف هر بخش جدید الزامی است.")
        before = deepcopy(category)
        categories.remove(category)
        created = []
        category_numbers = [
            int(match.group(1))
            for existing_id in [
                *(item.get("category_id") for item in categories),
                category_id,
            ]
            if (match := re.fullmatch(r"cat_(\d+)", str(existing_id or "")))
        ]
        next_category_number = max(category_numbers, default=0) + 1
        for index, partition in enumerate(partitions):
            new_category_id = category_id
            if index:
                new_category_id = f"cat_{next_category_number}"
                next_category_number += 1
            new_category = {
                "category_id": new_category_id,
                "title": str(partition["title"]).strip(),
                "definition": str(partition["definition"]).strip(),
                "source_item_ids": [str(item_id) for item_id in partition["source_item_ids"]],
                "review_status": "approved",
            }
            created.append(new_category)
        categories.extend(created)
        targets = [category_id, *[item["category_id"] for item in created[1:]]]
        after = deepcopy(created)

    elif decision == "move_source_item":
        if not source_item_id or not target_category_id:
            raise ValueError("شناسهٔ Source Item و مقولهٔ مقصد الزامی است.")
        target = _find_category(categories, target_category_id)
        source = next(
            (item for item in categories if source_item_id in item.get("source_item_ids", [])),
            None,
        )
        if source is None or source is target:
            raise ValueError("Source Item یا مقولهٔ مبدأ معتبر نیست.")
        if source_item_id in target.get("source_item_ids", []):
            raise ValueError("Source Item از قبل در مقولهٔ مقصد قرار دارد.")
        before = {
            "source_category": deepcopy(source),
            "target_category": deepcopy(target),
        }
        source["source_item_ids"].remove(source_item_id)
        if not source["source_item_ids"]:
            categories.remove(source)
        else:
            source["review_status"] = "approved"
        target["source_item_ids"].append(source_item_id)
        target["review_status"] = "approved"
        targets = [source_item_id, target_category_id]
        after = {
            "source_category": deepcopy(source) if source in categories else None,
            "target_category": deepcopy(target),
        }

    if decision != "approve_final":
        for category in categories:
            category["review_status"] = "pending"
    _validate_coverage(categories, expected_ids)
    decision_record = {
        "operation": decision,
        "target_ids": targets,
        "before": before,
        "after": after,
        "reason": reason.strip(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    edits["decisions"].append(decision_record)
    version = get_next_stage_version(run_id, STAGE_KEY, component_key)
    saved = save_stage_result(
        run_id,
        STAGE_KEY,
        component_key,
        version=version,
        status="approved" if decision == "approve_final" else "ready_for_researcher_review",
        input_hash=latest["input_hash"],
        prompt_text=latest.get("prompt_text", ""),
        raw_output_text=latest.get("raw_output_text", ""),
        output_json=output,
        validation_report_json=latest.get("validation_report_json", {}),
        validation_report_text=latest.get("validation_report_text", ""),
        source_phase_version=latest.get("source_phase_version"),
        researcher_edits_json=edits,
        approval_status="approved" if decision == "approve_final" else "pending",
    )
    mark_stage_results_stale(
        run_id,
        (DEPENDENT_STAGE_KEY,),
        component_key=component_key,
    )
    return saved
