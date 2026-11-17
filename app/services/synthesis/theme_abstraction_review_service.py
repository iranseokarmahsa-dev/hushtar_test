from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Any

from app.repositories.synthesis_repository import (
    get_next_stage_version,
    list_stage_results,
    save_stage_result,
)
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS
from app.services.synthesis.theme_abstraction_service import (
    STAGE_KEY,
    build_theme_abstraction_prompt,
)

DECISIONS = {
    "edit_theme",
    "approve_theme",
    "merge_themes",
    "split_theme",
    "move_category",
    "mark_unthemed",
    "approve_unthemed",
    "approve_final",
}


def _latest_result(run_id: str, component_key: str) -> dict[str, Any] | None:
    results = [
        item for item in list_stage_results(run_id)
        if item["stage_key"] == STAGE_KEY and item["component_key"] == component_key
    ]
    return max(results, key=lambda item: item["version"]) if results else None


def _find_theme(themes: list[dict[str, Any]], theme_id: str) -> dict[str, Any]:
    for theme in themes:
        if theme.get("theme_id") == theme_id:
            return theme
    raise ValueError("تم موردنظر پیدا نشد.")


def _validate_coverage(
    themes: list[dict[str, Any]],
    unthemed: list[dict[str, Any]],
    expected_ids: set[str],
) -> None:
    seen: list[str] = []
    theme_ids: set[str] = set()
    for theme in themes:
        theme_id = str(theme.get("theme_id") or "")
        if not theme_id or theme_id in theme_ids:
            raise ValueError("شناسهٔ تم خالی یا تکراری است.")
        theme_ids.add(theme_id)
        if not str(theme.get("title") or "").strip() or not str(theme.get("definition") or "").strip():
            raise ValueError("عنوان و تعریف همهٔ تم‌ها باید تکمیل شود.")
        category_ids = theme.get("integrated_category_ids")
        if not isinstance(category_ids, list) or not category_ids:
            raise ValueError("هر تم باید دست‌کم یک مقولهٔ تلفیقی داشته باشد.")
        if theme.get("review_status") not in {"pending", "approved"}:
            raise ValueError("وضعیت بازبینی تم معتبر نیست.")
        seen.extend(str(category_id) for category_id in category_ids)
    for category in unthemed:
        category_id = str(category.get("integrated_category_id") or "")
        if not category_id or not str(category.get("reason") or "").strip():
            raise ValueError("شناسه و دلیل هر مقولهٔ فاقد تم الزامی است.")
        if category.get("review_status") not in {"pending", "approved"}:
            raise ValueError("وضعیت بازبینی مقولهٔ فاقد تم معتبر نیست.")
        seen.append(category_id)
    if any(category_id not in expected_ids for category_id in seen):
        raise ValueError("شناسهٔ مقولهٔ ناشناخته در تم‌ها یا فهرست فاقد تم وجود دارد.")
    if len(seen) != len(set(seen)) or set(seen) != expected_ids:
        raise ValueError("پوشش مقوله‌ها کامل نیست یا یک شناسه بیش از یک بار آمده است.")


def record_theme_abstraction_review_decision(
    run_id: str,
    *,
    component_key: str,
    decision: str,
    reason: str,
    theme_id: str | None = None,
    target_theme_id: str | None = None,
    integrated_category_id: str | None = None,
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
    if latest.get("approval_status") == "approved":
        raise ValueError("این مؤلفه قبلاً تأیید نهایی شده است؛ برای اصلاح نسخهٔ تازه ثبت کنید.")
    current = build_theme_abstraction_prompt(run_id, component_key)
    if latest.get("input_hash") != current["input_hash"] or latest.get("status") == "stale":
        raise ValueError("ورودی این مؤلفه تغییر کرده است؛ خروجی قدیمی قابل بازبینی نیست.")
    if not (latest.get("validation_report_json") or {}).get("valid"):
        raise ValueError("خروجی Markdown نامعتبر است و بازبینی پژوهشگر روی آن مجاز نیست.")

    output = deepcopy(latest.get("output_json") or {})
    themes = output.get("themes")
    unthemed = output.get("categories_without_a_theme")
    if not isinstance(themes, list) or not isinstance(unthemed, list):
        raise ValueError("ساختار تم‌ها و مقوله‌های فاقد تم معتبر نیست.")
    expected_ids = {
        str(category["integrated_category_id"])
        for category in current["source_categories"]
    }
    edits = deepcopy(latest.get("researcher_edits_json") or {"decisions": []})
    edits.setdefault("decisions", [])
    before: Any = None
    after: Any = None
    targets: list[str] = []

    if decision in {"edit_theme", "approve_theme"}:
        if not theme_id:
            raise ValueError("شناسهٔ تم الزامی است.")
        theme = _find_theme(themes, theme_id)
        before = deepcopy(theme)
        targets = [theme_id]
        if decision == "edit_theme":
            if title is None and definition is None:
                raise ValueError("عنوان یا تعریف جدید الزامی است.")
            if title is not None:
                if not title.strip():
                    raise ValueError("عنوان تم نمی‌تواند خالی باشد.")
                theme["title"] = title.strip()
            if definition is not None:
                if not definition.strip():
                    raise ValueError("تعریف تم نمی‌تواند خالی باشد.")
                theme["definition"] = definition.strip()
            theme["review_status"] = "pending"
        else:
            theme["review_status"] = "approved"
        after = deepcopy(theme)

    elif decision == "merge_themes":
        if not theme_id or not target_theme_id or theme_id == target_theme_id:
            raise ValueError("برای ادغام باید دو شناسهٔ تم متفاوت مشخص شوند.")
        source = _find_theme(themes, theme_id)
        target = _find_theme(themes, target_theme_id)
        before = {"source": deepcopy(source), "target": deepcopy(target)}
        target["integrated_category_ids"] = list(dict.fromkeys(
            target["integrated_category_ids"] + source["integrated_category_ids"]
        ))
        if title is not None:
            if not title.strip():
                raise ValueError("عنوان نهایی تم نمی‌تواند خالی باشد.")
            target["title"] = title.strip()
        if definition is not None:
            if not definition.strip():
                raise ValueError("تعریف نهایی تم نمی‌تواند خالی باشد.")
            target["definition"] = definition.strip()
        target["review_status"] = "pending"
        themes.remove(source)
        targets = [theme_id, target_theme_id]
        after = deepcopy(target)

    elif decision == "split_theme":
        if not theme_id or not partitions or len(partitions) < 2:
            raise ValueError("برای تقسیم باید تم و بخش‌های جدید مشخص شوند.")
        theme = _find_theme(themes, theme_id)
        original_ids = set(theme.get("integrated_category_ids") or [])
        flattened = [
            str(category_id)
            for partition in partitions
            for category_id in partition.get("integrated_category_ids", [])
        ]
        if set(flattened) != original_ids or len(flattened) != len(original_ids):
            raise ValueError("اعضای تم باید دقیقاً یک‌بار میان بخش‌های جدید تقسیم شوند.")
        if any(
            not str(partition.get("title") or "").strip()
            or not str(partition.get("definition") or "").strip()
            for partition in partitions
        ):
            raise ValueError("عنوان و تعریف هر بخش جدید الزامی است.")
        before = deepcopy(theme)
        themes.remove(theme)
        theme_numbers = [
            int(match.group(1))
            for existing_id in [
                *(item.get("theme_id") for item in themes),
                theme_id,
            ]
            if (match := re.fullmatch(r"theme_(\d+)", str(existing_id or "")))
        ]
        next_theme_number = max(theme_numbers, default=0) + 1
        created = []
        for index, partition in enumerate(partitions):
            new_theme_id = theme_id
            if index:
                new_theme_id = f"theme_{next_theme_number}"
                next_theme_number += 1
            created.append({
                **theme,
                "theme_id": new_theme_id,
                "title": str(partition["title"]).strip(),
                "definition": str(partition["definition"]).strip(),
                "integrated_category_ids": [
                    str(category_id) for category_id in partition["integrated_category_ids"]
                ],
                "review_status": "pending",
            })
        themes.extend(created)
        targets = [theme_id, *[item["theme_id"] for item in created[1:]]]
        after = deepcopy(created)

    elif decision == "move_category":
        if not integrated_category_id or not target_theme_id:
            raise ValueError("شناسهٔ مقوله و تم مقصد الزامی است.")
        target = _find_theme(themes, target_theme_id)
        source = next(
            (
                theme for theme in themes
                if integrated_category_id in theme.get("integrated_category_ids", [])
            ),
            None,
        )
        unthemed_source = next(
            (
                item for item in unthemed
                if item.get("integrated_category_id") == integrated_category_id
            ),
            None,
        )
        if source is target or (source is None and unthemed_source is None):
            raise ValueError("مقولهٔ مبدأ یا تم مقصد معتبر نیست.")
        before = {
            "source": deepcopy(source) if source else deepcopy(unthemed_source),
            "target": deepcopy(target),
        }
        if source:
            source["integrated_category_ids"].remove(integrated_category_id)
            if not source["integrated_category_ids"]:
                themes.remove(source)
            else:
                source["review_status"] = "pending"
        if unthemed_source:
            unthemed.remove(unthemed_source)
        target["integrated_category_ids"].append(integrated_category_id)
        target["review_status"] = "pending"
        targets = [integrated_category_id, target_theme_id]
        after = deepcopy(target)

    elif decision == "mark_unthemed":
        if not integrated_category_id:
            raise ValueError("شناسهٔ مقوله الزامی است.")
        source = next(
            (
                theme for theme in themes
                if integrated_category_id in theme.get("integrated_category_ids", [])
            ),
            None,
        )
        if source is None:
            raise ValueError("مقولهٔ انتخاب‌شده عضو هیچ تمی نیست.")
        before = deepcopy(source)
        source["integrated_category_ids"].remove(integrated_category_id)
        if source["integrated_category_ids"]:
            source["review_status"] = "pending"
        else:
            themes.remove(source)
        unthemed.append({
            "integrated_category_id": integrated_category_id,
            "reason": reason.strip(),
            "review_status": "pending",
        })
        targets = [integrated_category_id]
        after = deepcopy(unthemed[-1])

    elif decision == "approve_unthemed":
        if not integrated_category_id:
            raise ValueError("شناسهٔ مقوله الزامی است.")
        category = next(
            (
                item for item in unthemed
                if item.get("integrated_category_id") == integrated_category_id
            ),
            None,
        )
        if category is None:
            raise ValueError("مقولهٔ فاقد تم موردنظر پیدا نشد.")
        before = deepcopy(category)
        category["review_status"] = "approved"
        targets = [integrated_category_id]
        after = deepcopy(category)

    elif decision == "approve_final":
        unresolved = [
            theme["theme_id"] for theme in themes
            if theme.get("review_status") != "approved"
        ]
        unresolved.extend(
            category["integrated_category_id"] for category in unthemed
            if category.get("review_status") != "approved"
        )
        if unresolved:
            raise ValueError("همهٔ تم‌ها و مقوله‌های فاقد تم باید جداگانه تأیید شوند: " + "، ".join(unresolved))
        before = {"approval_status": latest.get("approval_status")}
        after = {"approval_status": "approved"}

    _validate_coverage(themes, unthemed, expected_ids)
    edits["decisions"].append({
        "operation": decision,
        "target_ids": targets,
        "before": before,
        "after": after,
        "reason": reason.strip(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })
    version = get_next_stage_version(run_id, STAGE_KEY, component_key)
    is_approved = decision == "approve_final"
    return save_stage_result(
        run_id,
        STAGE_KEY,
        component_key,
        version=version,
        status="approved" if is_approved else "researcher_review",
        input_hash=latest["input_hash"],
        prompt_text=latest.get("prompt_text", ""),
        raw_output_text=latest.get("raw_output_text", ""),
        output_json=output,
        validation_report_json=latest.get("validation_report_json", {}),
        validation_report_text=latest.get("validation_report_text", ""),
        source_phase_version=latest.get("source_phase_version"),
        researcher_edits_json=edits,
        approval_status="approved" if is_approved else "pending",
    )
