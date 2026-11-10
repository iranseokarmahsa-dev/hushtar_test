from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.repositories.synthesis_repository import (
    get_next_stage_version,
    mark_stage_results_stale,
    save_stage_result,
    update_run_status,
)
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS
from app.services.synthesis.theme_synthesis_prompt import (
    STAGE_COMPONENT_KEY,
    STAGE_KEY,
    build_theme_synthesis_prompt,
    latest_theme_result,
)

DECISIONS = {
    "edit_theme",
    "approve_theme",
    "merge_themes",
    "split_theme",
    "move_category_unit",
    "mark_unthemed",
    "approve_unthemed",
    "edit_contradiction",
    "approve_contradiction",
    "dismiss_contradiction",
    "approve_final",
}


def _find_theme(components: dict[str, Any], theme_id: str) -> tuple[str, dict[str, Any]]:
    for component_key, component in components.items():
        for theme in component.get("themes", []):
            if theme.get("theme_id") == theme_id:
                return component_key, theme
    raise ValueError("تم موردنظر پیدا نشد.")


def _find_contradiction(
    components: dict[str, Any],
    contradiction_id: str,
) -> tuple[str, dict[str, Any]]:
    for component_key, component in components.items():
        for contradiction in component.get("contradictions", []):
            if contradiction.get("contradiction_id") == contradiction_id:
                return component_key, contradiction
    raise ValueError("تناقض موردنظر پیدا نشد.")


def _unit_index(
    components: dict[str, Any],
    category_unit_id: str,
) -> tuple[str, str, dict[str, Any]]:
    for component_key, component in components.items():
        for theme in component.get("themes", []):
            if category_unit_id in theme.get("constituent_category_unit_ids", []):
                return component_key, "theme", theme
        for item in component.get("unthemed_category_units", []):
            if item.get("category_unit_id") == category_unit_id:
                return component_key, "unthemed", item
    raise ValueError("Category Unit موردنظر پیدا نشد.")


def _evidence_for_units(
    category_unit_ids: list[str],
    units_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    return sorted({
        evidence_id
        for unit_id in category_unit_ids
        for evidence_id in units_by_id.get(unit_id, {}).get("evidence_ref_ids", [])
    })


def _validate_review_coverage(
    components: dict[str, Any],
    units: list[dict[str, Any]],
) -> None:
    expected = {str(unit["category_unit_id"]): str(unit["component_key"]) for unit in units}
    seen: list[str] = []
    for component_key in POOL_COMPONENT_KEYS:
        component = components.get(component_key)
        if not isinstance(component, dict):
            raise ValueError("ساختار پنج مؤلفه فاز پنجم کامل نیست.")
        for theme in component.get("themes", []):
            ids = theme.get("constituent_category_unit_ids") or []
            if not ids:
                raise ValueError("تم پژوهشگر باید دست‌کم یک Category Unit داشته باشد.")
            if theme.get("component_key") != component_key:
                raise ValueError("مؤلفه تم با مؤلفه Category Unitها مطابقت ندارد.")
            for unit_id in ids:
                if unit_id not in expected:
                    raise ValueError(f"Category Unit ناشناخته است: {unit_id}")
                if expected[unit_id] != component_key:
                    raise ValueError("انتقال Category Unit میان مؤلفه‌های پارادایمی ممنوع است.")
                seen.append(unit_id)
        for item in component.get("unthemed_category_units", []):
            unit_id = str(item.get("category_unit_id") or "")
            if unit_id not in expected:
                raise ValueError(f"Category Unit ناشناخته است: {unit_id}")
            if expected[unit_id] != component_key:
                raise ValueError("Category Unit بدون تم در مؤلفه نادرستی قرار دارد.")
            if not str(item.get("reason") or "").strip():
                raise ValueError("دلیل تعیین Category Unit بدون تم الزامی است.")
            if item.get("review_status") not in {"pending", "approved"}:
                raise ValueError("وضعیت بازبینی Category Unit بدون تم معتبر نیست.")
            seen.append(unit_id)
        for contradiction in component.get("contradictions", []):
            if contradiction.get("component_key") != component_key:
                raise ValueError("مؤلفه تناقض معتبر نیست.")
            ids = contradiction.get("category_unit_ids") or []
            if not ids or any(unit_id not in expected or expected[unit_id] != component_key for unit_id in ids):
                raise ValueError("تناقض باید به Category Unitهای واقعی همان مؤلفه ارجاع دهد.")
    if len(seen) != len(set(seen)) or set(seen) != set(expected):
        raise ValueError("پوشش Category Unitها کامل نیست یا یک Category Unit بیش از یک بار تعیین تکلیف شده است.")
    units_by_id = {str(unit["category_unit_id"]): unit for unit in units}
    for component in components.values():
        for theme in component.get("themes", []):
            allowed = set(_evidence_for_units(theme["constituent_category_unit_ids"], units_by_id))
            if not set(theme.get("evidence_ref_ids") or []).issubset(allowed):
                raise ValueError("شاهد تم به Category Unitهای همان تم تعلق ندارد.")


def _record(
    run_id: str,
    latest: dict[str, Any],
    output: dict[str, Any],
    edits: dict[str, Any],
    units: list[dict[str, Any]],
    *,
    decision: str,
    targets: list[str],
    before: Any,
    after: Any,
    reason: str,
    approve: bool = False,
) -> dict[str, Any]:
    _validate_review_coverage(output["component_results"], units)
    if approve:
        unresolved = [
            f"theme:{theme['theme_id']}"
            for component in output["component_results"].values()
            for theme in component["themes"]
            if theme.get("review_status") != "approved"
        ]
        unresolved.extend(
            f"category_unit:{item['category_unit_id']}"
            for component in output["component_results"].values()
            for item in component["unthemed_category_units"]
            if item.get("review_status") != "approved"
        )
        unresolved.extend(
            f"contradiction:{item['contradiction_id']}"
            for component in output["component_results"].values()
            for item in component["contradictions"]
            if item.get("review_status") not in {"approved", "dismissed"}
        )
        if unresolved:
            raise ValueError("همه تم‌ها، واحدهای بدون تم و تناقض‌ها باید تعیین تکلیف شوند: " + "، ".join(unresolved))
    edits["decisions"].append({
        "operation": decision,
        "target_ids": targets,
        "before": before,
        "after": after,
        "reason": reason.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    version = get_next_stage_version(run_id, STAGE_KEY, STAGE_COMPONENT_KEY)
    saved = save_stage_result(
        run_id,
        STAGE_KEY,
        STAGE_COMPONENT_KEY,
        version=version,
        status="approved" if approve else "researcher_review",
        input_hash=latest["input_hash"],
        prompt_text=latest.get("prompt_text", ""),
        raw_output_text=latest.get("raw_output_text", ""),
        output_json=output,
        validation_report_json=latest.get("validation_report_json", {}),
        validation_report_text=latest.get("validation_report_text", ""),
        source_phase_version=latest.get("source_phase_version"),
        researcher_edits_json=edits,
        approval_status="approved" if approve else "pending",
    )
    mark_stage_results_stale(
        run_id,
        ("phenomenon_synthesis", "relationship_synthesis", "final_model"),
    )
    if approve:
        update_run_status(
            run_id,
            status="completed",
            current_stage="theme_synthesis_approved",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
    return saved


def record_theme_review_decision(
    run_id: str,
    *,
    decision: str,
    reason: str,
    theme_id: str | None = None,
    target_theme_id: str | None = None,
    contradiction_id: str | None = None,
    category_unit_id: str | None = None,
    proposed_theme_name: str | None = None,
    definition: str | None = None,
    reasoning: str | None = None,
    conceptual_scope: str | None = None,
    partitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise ValueError("تصمیم بازبینی شناخته‌شده نیست.")
    if not reason.strip():
        raise ValueError("دلیل تصمیم پژوهشگر الزامی است.")
    latest = latest_theme_result(run_id)
    if latest is None or latest.get("status") == "invalid" or not latest.get("validation_report_json", {}).get("valid"):
        raise ValueError("خروجی معتبر استخراج تم برای بازبینی وجود ندارد.")
    if latest.get("stale"):
        raise ValueError("خروجی استخراج تم منقضی شده است؛ ورودی را دوباره تولید کنید.")
    current = build_theme_synthesis_prompt(run_id)
    if current["input_hash"] != latest["input_hash"]:
        raise ValueError("نسخه فاز چهارم پس از دریافت خروجی تم تغییر کرده است.")
    output = deepcopy(latest.get("output_json") or {})
    components = output.get("component_results")
    if not isinstance(components, dict):
        raise ValueError("ساختار خروجی تم برای بازبینی معتبر نیست.")
    edits = deepcopy(latest.get("researcher_edits_json") or {"decisions": []})
    edits.setdefault("decisions", [])
    units = current["category_units"]
    units_by_id = {str(unit["category_unit_id"]): unit for unit in units}
    before: Any = None
    after: Any = None
    targets: list[str] = []
    approve = decision == "approve_final"

    if decision in {"edit_theme", "approve_theme"}:
        if not theme_id:
            raise ValueError("شناسه تم الزامی است.")
        component_key, theme = _find_theme(components, theme_id)
        before = deepcopy(theme)
        if decision == "edit_theme":
            for key, value in (
                ("proposed_theme_name", proposed_theme_name),
                ("definition", definition),
                ("reasoning", reasoning),
                ("conceptual_scope", conceptual_scope),
            ):
                if value is not None:
                    if not value.strip():
                        raise ValueError(f"فیلد {key} نمی‌تواند خالی باشد.")
                    theme[key] = value.strip()
            theme["review_status"] = "pending"
        else:
            theme["review_status"] = "approved"
        after = deepcopy(theme)
        targets = [theme_id]

    elif decision == "merge_themes":
        if not theme_id or not target_theme_id or theme_id == target_theme_id:
            raise ValueError("دو شناسه متفاوت تم برای ادغام الزامی است.")
        source_component, source = _find_theme(components, theme_id)
        target_component, target = _find_theme(components, target_theme_id)
        if source_component != target_component:
            raise ValueError("ادغام تم میان مؤلفه‌های پارادایمی ممنوع است.")
        before = {"source": deepcopy(source), "target": deepcopy(target)}
        merged_ids = list(dict.fromkeys(target["constituent_category_unit_ids"] + source["constituent_category_unit_ids"]))
        target["constituent_category_unit_ids"] = merged_ids
        target["evidence_ref_ids"] = _evidence_for_units(merged_ids, units_by_id)
        if proposed_theme_name is not None:
            if not proposed_theme_name.strip():
                raise ValueError("عنوان نهایی تم نمی‌تواند خالی باشد.")
            target["proposed_theme_name"] = proposed_theme_name.strip()
        if definition is not None:
            if not definition.strip():
                raise ValueError("تعریف نهایی تم نمی‌تواند خالی باشد.")
            target["definition"] = definition.strip()
        target["review_status"] = "pending"
        components[source_component]["themes"].remove(source)
        after = deepcopy(target)
        targets = [theme_id, target_theme_id]

    elif decision == "split_theme":
        if not theme_id or not partitions or len(partitions) < 2:
            raise ValueError("برای تقسیم، تم و دست‌کم دو پارتیشن الزامی است.")
        component_key, theme = _find_theme(components, theme_id)
        before = deepcopy(theme)
        original_ids = set(theme["constituent_category_unit_ids"])
        partition_ids = [
            str(item_id)
            for partition in partitions
            for item_id in partition.get("category_unit_ids", [])
        ]
        if set(partition_ids) != original_ids or len(partition_ids) != len(original_ids):
            raise ValueError("پارتیشن‌ها باید همه Category Unitهای تم را دقیقاً یک‌بار پوشش دهند.")
        replacements = []
        for partition in partitions:
            member_ids = [str(value) for value in partition.get("category_unit_ids", [])]
            name = str(partition.get("proposed_theme_name") or "").strip()
            new_definition = str(partition.get("definition") or "").strip()
            if not member_ids or not name or not new_definition:
                raise ValueError("هر پارتیشن باید اعضا، عنوان و تعریف داشته باشد.")
            replacements.append({
                **deepcopy(theme),
                "theme_id": f"theme_{uuid4().hex}",
                "ai_theme_id": None,
                "constituent_category_unit_ids": member_ids,
                "proposed_theme_name": name,
                "definition": new_definition,
                "evidence_ref_ids": _evidence_for_units(member_ids, units_by_id),
                "supersedes_theme_ids": [theme_id],
                "review_status": "pending",
            })
        theme_index = components[component_key]["themes"].index(theme)
        components[component_key]["themes"][theme_index:theme_index + 1] = replacements
        after = deepcopy(replacements)
        targets = [theme_id]

    elif decision in {"move_category_unit", "mark_unthemed", "approve_unthemed"}:
        if not category_unit_id:
            raise ValueError("شناسه Category Unit الزامی است.")
        component_key, placement, current_item = _unit_index(components, category_unit_id)
        before = deepcopy(current_item)
        if decision == "approve_unthemed":
            if placement != "unthemed":
                raise ValueError("Category Unit انتخاب‌شده در فهرست بدون تم نیست.")
            current_item["review_status"] = "approved"
            after = deepcopy(current_item)
        elif decision == "mark_unthemed":
            if placement == "unthemed":
                raise ValueError("Category Unit از قبل بدون تم است.")
            if placement == "theme":
                current_item["constituent_category_unit_ids"].remove(category_unit_id)
                current_item["evidence_ref_ids"] = _evidence_for_units(
                    current_item["constituent_category_unit_ids"], units_by_id
                )
                current_item["review_status"] = "pending"
                if not current_item["constituent_category_unit_ids"]:
                    components[component_key]["themes"].remove(current_item)
            components[component_key]["unthemed_category_units"].append({
                "category_unit_id": category_unit_id,
                "reason": reason.strip(),
                "review_status": "pending",
            })
            after = deepcopy(components[component_key]["unthemed_category_units"][-1])
        else:
            if target_theme_id:
                target_component, target = _find_theme(components, target_theme_id)
                if component_key != target_component:
                    raise ValueError("انتقال Category Unit میان مؤلفه‌های پارادایمی ممنوع است.")
                if placement == "theme":
                    if current_item.get("theme_id") == target_theme_id:
                        raise ValueError("Category Unit از قبل عضو تم مقصد است.")
                    current_item["constituent_category_unit_ids"].remove(category_unit_id)
                    current_item["evidence_ref_ids"] = _evidence_for_units(
                        current_item["constituent_category_unit_ids"], units_by_id
                    )
                    current_item["review_status"] = "pending"
                    if not current_item["constituent_category_unit_ids"]:
                        components[component_key]["themes"].remove(current_item)
                else:
                    components[component_key]["unthemed_category_units"].remove(current_item)
                target["constituent_category_unit_ids"].append(category_unit_id)
                target["evidence_ref_ids"] = _evidence_for_units(
                    target["constituent_category_unit_ids"], units_by_id
                )
                target["review_status"] = "pending"
                after = deepcopy(target)
                targets = [category_unit_id, target_theme_id]
            else:
                raise ValueError("برای انتقال، شناسه تم مقصد الزامی است.")
        targets = targets or [category_unit_id]

    elif decision in {"edit_contradiction", "approve_contradiction", "dismiss_contradiction"}:
        if not contradiction_id:
            raise ValueError("شناسه تناقض الزامی است.")
        _, contradiction = _find_contradiction(components, contradiction_id)
        before = deepcopy(contradiction)
        if decision == "edit_contradiction":
            if not reasoning or not reasoning.strip():
                raise ValueError("شرح پژوهشگر برای اصلاح تناقض الزامی است.")
            if definition is not None:
                if not definition.strip():
                    raise ValueError("شرح تناقض نمی‌تواند خالی باشد.")
                contradiction["description"] = definition.strip()
            contradiction["reasoning"] = reasoning.strip()
            contradiction["review_status"] = "pending"
        else:
            contradiction["review_status"] = "approved" if decision == "approve_contradiction" else "dismissed"
        after = deepcopy(contradiction)
        targets = [contradiction_id]

    elif decision == "approve_final":
        before = {"approval_status": latest.get("approval_status")}
        after = {"approval_status": "approved"}
        targets = []

    saved = _record(
        run_id,
        latest,
        output,
        edits,
        units,
        decision=decision,
        targets=targets,
        before=before,
        after=after,
        reason=reason,
        approve=approve,
    )
    return saved
