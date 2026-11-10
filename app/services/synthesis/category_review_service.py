from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.repositories.synthesis_repository import (
    get_next_stage_version,
    list_stage_results,
    mark_stage_results_stale,
    save_stage_result,
    update_run_status,
)
from app.services.synthesis.category_grouping_service import (
    STAGE_COMPONENT_KEY,
    STAGE_KEY,
    _approved_pool,
)
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS

DECISIONS = {
    "edit_group",
    "approve_group",
    "reject_group",
    "merge_groups",
    "split_group",
    "move_item",
    "approve_standalone",
    "exclude_source_item",
    "approve_final",
}


def _latest_result(run_id: str) -> dict[str, Any] | None:
    results = [
        item for item in list_stage_results(run_id)
        if item["stage_key"] == STAGE_KEY and item["component_key"] == STAGE_COMPONENT_KEY
    ]
    return max(results, key=lambda item: item["version"]) if results else None


def _component_for_group(result: dict[str, Any], group_id: str) -> tuple[str, dict[str, Any]]:
    for component_key, component in result["component_results"].items():
        for group in component["groups"]:
            if group.get("group_id") == group_id:
                return component_key, group
    raise ValueError("گروه موردنظر پیدا نشد.")


def _find_standalone(component_results: dict[str, Any], source_item_id: str) -> tuple[str, dict[str, Any]] | None:
    for component_key, component in component_results.items():
        for item in component["standalone_source_items"]:
            if item.get("source_item_id") == source_item_id:
                return component_key, item
    return None


def _source_items_by_id(pool: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["source_item_id"]): item
        for key in POOL_COMPONENT_KEYS
        for item in pool.get("pools", {}).get(key, [])
    }


def _evidence_for_items(
    source_item_ids: list[str],
    source_items: dict[str, dict[str, Any]],
) -> list[str]:
    values = {
        str(reference["evidence_ref_id"])
        for source_item_id in source_item_ids
        for reference in source_items.get(source_item_id, {}).get("evidence_refs", [])
        if reference.get("evidence_ref_id")
    }
    return sorted(values)


def _validate_review_coverage(result: dict[str, Any], source_items: dict[str, dict[str, Any]]) -> None:
    expected_ids = set(source_items)
    seen: list[str] = []
    for component_key, component in result["component_results"].items():
        for group in component["groups"]:
            item_ids = group.get("source_item_ids") or []
            if len(item_ids) < 2:
                raise ValueError("گروه پژوهشگر باید حداقل دو Source Item داشته باشد.")
            if group.get("component_key") != component_key:
                raise ValueError("مؤلفه گروه با مؤلفه Source Itemها مطابقت ندارد.")
            for item_id in item_ids:
                if item_id not in expected_ids:
                    raise ValueError(f"Source Item ناشناخته است: {item_id}")
                if source_items[item_id].get("component_key") != component_key:
                    raise ValueError("انتقال Source Item میان مؤلفه‌های پارادایمی مجاز نیست.")
                seen.append(item_id)
            allowed = set(_evidence_for_items(item_ids, source_items))
            if not set(group.get("evidence_ref_ids") or []).issubset(allowed):
                raise ValueError("ارجاع شاهد گروه دیگر متعلق به اعضای گروه نیست.")
        for standalone in component["standalone_source_items"]:
            item_id = standalone.get("source_item_id")
            if item_id not in expected_ids:
                raise ValueError(f"Source Item مستقل ناشناخته است: {item_id}")
            if source_items[item_id].get("component_key") != component_key:
                raise ValueError("Source Item مستقل در مؤلفه نادرست قرار دارد.")
            seen.append(item_id)
    if set(seen) != expected_ids or len(seen) != len(expected_ids):
        raise ValueError("پوشش Source Itemها کامل نیست یا یک Source Item بیش از یک بار تعیین تکلیف شده است.")


def _record_decision(
    run_id: str,
    latest: dict[str, Any],
    result: dict[str, Any],
    edits: dict[str, Any],
    *,
    decision: str,
    target_ids: list[str],
    before: Any,
    after: Any,
    reason: str,
    source_items: dict[str, dict[str, Any]],
    approval_status: str = "pending",
    result_status: str = "researcher_review",
) -> dict[str, Any]:
    _validate_review_coverage(result, source_items)
    version = get_next_stage_version(run_id, STAGE_KEY, STAGE_COMPONENT_KEY)
    record = {
        "operation": decision,
        "target_ids": target_ids,
        "before": before,
        "after": after,
        "reason": reason.strip(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "version": version,
    }
    decision_history = list(edits.get("decisions") or [])
    decision_history.append(record)
    edits["decisions"] = decision_history
    report = deepcopy(latest.get("validation_report_json") or {})
    report["researcher_review_coverage"] = {
        "valid": True,
        "source_item_count": len(source_items),
        "decision_count": len(decision_history),
    }
    return save_stage_result(
        run_id,
        STAGE_KEY,
        STAGE_COMPONENT_KEY,
        version=version,
        status=result_status,
        input_hash=latest["input_hash"],
        prompt_text=latest["prompt_text"],
        raw_output_text=latest["raw_output_text"],
        output_json=result,
        validation_report_json=report,
        validation_report_text=latest.get("validation_report_text", ""),
        researcher_edits_json=edits,
        approval_status=approval_status,
    )


def record_category_review_decision(
    run_id: str,
    *,
    decision: str,
    reason: str,
    group_id: str | None = None,
    target_group_id: str | None = None,
    source_item_id: str | None = None,
    proposed_category_name: str | None = None,
    definition: str | None = None,
    partitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise ValueError("عملیات بازبینی پشتیبانی نمی‌شود.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("ثبت دلیل برای هر تصمیم پژوهشگر الزامی است.")
    latest = _latest_result(run_id)
    if latest is None:
        raise ValueError("ابتدا خروجی گروه‌بندی را ثبت کنید.")
    if latest["approval_status"] == "approved":
        raise ValueError("این نسخه قبلاً تأیید نهایی شده است؛ برای تصمیم جدید نسخه تازه ایجاد کنید.")
    pool, _, _ = _approved_pool(run_id)
    from app.services.synthesis.category_grouping_service import build_grouping_prompt
    current_input_hash = build_grouping_prompt(run_id, enforce_size=False)["input_hash"]
    if latest["input_hash"] != current_input_hash:
        raise ValueError("Pool فاز سوم تغییر کرده است؛ خروجی گروه‌بندی منقضی شده و بازبینی آن مجاز نیست.")
    if not (latest.get("validation_report_json") or {}).get("valid"):
        raise ValueError("خروجی Markdown نامعتبر است و بازبینی پژوهشگر روی آن مجاز نیست.")

    result = deepcopy(latest["output_json"])
    component_results = result["component_results"]
    edits = deepcopy(latest.get("researcher_edits_json") or {})
    source_items = _source_items_by_id(pool)
    before: Any = None
    after: Any = None
    targets: list[str] = []

    if decision in {"edit_group", "approve_group", "reject_group"}:
        if not group_id:
            raise ValueError("شناسه گروه الزامی است.")
        component_key, group = _component_for_group(result, group_id)
        before = deepcopy(group)
        targets = [group_id]
        if decision == "edit_group":
            if proposed_category_name is None and definition is None:
                raise ValueError("عنوان یا تعریف جدید الزامی است.")
            if proposed_category_name is not None:
                if not proposed_category_name.strip():
                    raise ValueError("عنوان مقوله نمی‌تواند خالی باشد.")
                group["proposed_category_name"] = proposed_category_name.strip()
            if definition is not None:
                if not definition.strip():
                    raise ValueError("تعریف مقوله نمی‌تواند خالی باشد.")
                group["definition"] = definition.strip()
            group["review_status"] = "pending"
        elif decision == "approve_group":
            group["review_status"] = "approved"
        else:
            component_results[component_key]["standalone_source_items"].extend(
                {"source_item_id": item_id, "review_status": "pending", "reason": ""}
                for item_id in group["source_item_ids"]
            )
            component_results[component_key]["groups"].remove(group)
        after = deepcopy(group) if decision != "reject_group" else {"status": "rejected_to_standalone"}

    elif decision == "merge_groups":
        if not group_id or not target_group_id or group_id == target_group_id:
            raise ValueError("برای ادغام باید دو شناسه گروه متفاوت مشخص شوند.")
        first_component, first = _component_for_group(result, group_id)
        second_component, second = _component_for_group(result, target_group_id)
        if first_component != second_component:
            raise ValueError("ادغام گروه‌های متعلق به مؤلفه‌های پارادایمی متفاوت ممنوع است.")
        before = {"first": deepcopy(first), "second": deepcopy(second)}
        first["source_item_ids"] = list(dict.fromkeys(first["source_item_ids"] + second["source_item_ids"]))
        first["evidence_ref_ids"] = list(dict.fromkeys(first["evidence_ref_ids"] + second["evidence_ref_ids"]))
        first["similarities"] = list(dict.fromkeys(first.get("similarities", []) + second.get("similarities", [])))
        first["differences"] = list(dict.fromkeys(first.get("differences", []) + second.get("differences", [])))
        if proposed_category_name is not None:
            if not proposed_category_name.strip():
                raise ValueError("عنوان مقوله نمی‌تواند خالی باشد.")
            first["proposed_category_name"] = proposed_category_name.strip()
        if definition is not None:
            if not definition.strip():
                raise ValueError("تعریف مقوله نمی‌تواند خالی باشد.")
            first["definition"] = definition.strip()
        first["review_status"] = "pending"
        component_results[first_component]["groups"].remove(second)
        targets = [group_id, target_group_id]
        after = deepcopy(first)

    elif decision == "split_group":
        if not group_id or not partitions:
            raise ValueError("برای تقسیم باید گروه و فهرست بخش‌های جدید مشخص شوند.")
        if any(not partition.get("source_item_ids") for partition in partitions):
            raise ValueError("بخش خالی در تقسیم گروه مجاز نیست.")
        component_key, group = _component_for_group(result, group_id)
        before = deepcopy(group)
        original_ids = set(group["source_item_ids"])
        flattened = [
            str(item_id)
            for partition in partitions
            for item_id in partition.get("source_item_ids", [])
        ]
        if set(flattened) != original_ids or len(flattened) != len(original_ids):
            raise ValueError("تمام اعضای گروه باید دقیقاً یک‌بار در گروه‌های جدید یا به‌صورت مستقل تعیین تکلیف شوند.")
        component_results[component_key]["groups"].remove(group)
        created_groups: list[dict[str, Any]] = []
        standalones: list[dict[str, Any]] = []
        for partition in partitions:
            item_ids = [str(value) for value in partition.get("source_item_ids", [])]
            if len(item_ids) == 1:
                standalones.append({"source_item_id": item_ids[0], "review_status": "pending", "reason": ""})
                continue
            if len(item_ids) < 2:
                raise ValueError("هر گروه جدید باید حداقل دو عضو داشته باشد.")
            created = deepcopy(group)
            created["group_id"] = f"cgrp_{uuid4().hex}"
            created["ai_group_id"] = f"researcher_split_{group.get('ai_group_id', group_id)}"
            created["source_item_ids"] = item_ids
            created["evidence_ref_ids"] = _evidence_for_items(item_ids, source_items)
            created["proposed_category_name"] = str(partition.get("proposed_category_name") or group["proposed_category_name"]).strip()
            created["definition"] = str(partition.get("definition") or group["definition"]).strip()
            created["review_status"] = "pending"
            created_groups.append(created)
        component_results[component_key]["groups"].extend(created_groups)
        component_results[component_key]["standalone_source_items"].extend(standalones)
        targets = [group_id] + [item["group_id"] for item in created_groups]
        after = {"groups": created_groups, "standalone_source_items": standalones}

    elif decision == "move_item":
        if not source_item_id or not target_group_id:
            raise ValueError("شناسه Source Item و گروه مقصد الزامی است.")
        target_component, target_group = _component_for_group(result, target_group_id)
        before = {"source_item_id": source_item_id}
        source_component, standalone = _find_standalone(component_results, source_item_id) or (None, None)
        if standalone is not None:
            component_results[source_component]["standalone_source_items"].remove(standalone)
            before["standalone"] = deepcopy(standalone)
        else:
            source_component = None
            source_group = None
            for key, component in component_results.items():
                for group in component["groups"]:
                    if source_item_id in group.get("source_item_ids", []):
                        source_component, source_group = key, group
                        break
                if source_group:
                    break
            if source_group is None:
                raise ValueError("Source Item موردنظر پیدا نشد.")
            if source_group.get("group_id") == target_group_id:
                raise ValueError("Source Item از قبل در گروه مقصد قرار دارد.")
            before["source_group"] = deepcopy(source_group)
            source_group["source_item_ids"].remove(source_item_id)
            source_group["evidence_ref_ids"] = _evidence_for_items(source_group["source_item_ids"], source_items)
            source_group["review_status"] = "pending"
            if len(source_group["source_item_ids"]) < 2:
                component_results[source_component]["groups"].remove(source_group)
                component_results[source_component]["standalone_source_items"].extend(
                    {"source_item_id": item_id, "review_status": "pending", "reason": ""}
                    for item_id in source_group["source_item_ids"]
                )
        if source_items.get(source_item_id, {}).get("component_key") != target_component:
            raise ValueError("انتقال Source Item میان مؤلفه‌های پارادایمی ممنوع است.")
        if source_item_id in target_group["source_item_ids"]:
            raise ValueError("Source Item از قبل عضو گروه مقصد است.")
        target_group["source_item_ids"].append(source_item_id)
        target_group["evidence_ref_ids"] = _evidence_for_items(target_group["source_item_ids"], source_items)
        target_group["review_status"] = "pending"
        targets = [source_item_id, target_group_id]
        after = deepcopy(target_group)

    elif decision in {"approve_standalone", "exclude_source_item"}:
        if not source_item_id:
            raise ValueError("شناسه Source Item الزامی است.")
        found = _find_standalone(component_results, source_item_id)
        if found is None:
            raise ValueError("Source Item مستقل پیدا نشد.")
        component_key, standalone = found
        before = deepcopy(standalone)
        standalone["review_status"] = "approved" if decision == "approve_standalone" else "excluded"
        standalone["reason"] = reason.strip()
        targets = [source_item_id]
        after = deepcopy(standalone)

    elif decision == "approve_final":
        unresolved = []
        for component in component_results.values():
            unresolved.extend(
                f"group:{group['group_id']}"
                for group in component["groups"]
                if group.get("review_status") != "approved"
            )
            unresolved.extend(
                f"source_item:{item['source_item_id']}"
                for item in component["standalone_source_items"]
                if item.get("review_status") not in {"approved", "excluded"} or (
                    item.get("review_status") == "excluded" and not item.get("reason")
                )
            )
        if unresolved:
            raise ValueError("همه Source Itemها و گروه‌ها باید تعیین تکلیف و تأیید شوند: " + "، ".join(unresolved))
        before = {"approval_status": latest["approval_status"]}
        _validate_review_coverage(result, source_items)
        saved = _record_decision(
            run_id, latest, result, edits,
            decision=decision, target_ids=[], before=before,
            after={"approval_status": "approved"},
            reason=reason,
            source_items=source_items,
            approval_status="approved",
            result_status="approved",
        )
        update_run_status(
            run_id,
            status="completed",
            current_stage="category_grouping_approved",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        mark_stage_results_stale(
            run_id,
            ("theme_synthesis", "phenomenon_synthesis", "relationship_synthesis", "final_model"),
        )
        return saved

    saved = _record_decision(
        run_id, latest, result, edits,
        decision=decision,
        target_ids=targets,
        before=before,
        after=after,
        reason=reason,
        source_items=source_items,
    )
    mark_stage_results_stale(
        run_id,
        ("theme_synthesis", "phenomenon_synthesis", "relationship_synthesis", "final_model"),
    )
    return saved


def get_approved_category_grouping(run_id: str) -> dict[str, Any]:
    latest = _latest_result(run_id)
    if latest is None or latest.get("approval_status") != "approved":
        raise ValueError("خروجی گروه‌بندی تأییدشده‌ای برای ورود به مرحله بعد وجود ندارد.")
    from app.repositories.synthesis_repository import get_synthesis_run
    run = get_synthesis_run(run_id)
    if run is None or run.get("current_stage") not in {
        "category_grouping_approved",
        "theme_synthesis",
        "theme_synthesis_approved",
        "phenomenon_synthesis",
        "phenomenon_synthesis_approved",
        "relationship_synthesis",
        "relationship_synthesis_approved",
        "final_model",
    }:
        raise ValueError("مرحله گروه‌بندی برای ورود به مرحله بعد تأیید نهایی نشده است.")
    pool, _, _ = _approved_pool(run_id)
    from app.services.synthesis.category_grouping_service import build_grouping_prompt
    if latest.get("input_hash") != build_grouping_prompt(run_id, enforce_size=False)["input_hash"]:
        raise ValueError("خروجی گروه‌بندی برای Pool فعلی منقضی شده است.")
    result = latest.get("output_json") or {}
    source_items = _source_items_by_id(pool)
    _validate_review_coverage(result, source_items)
    for component in result.get("component_results", {}).values():
        if any(group.get("review_status") != "approved" for group in component.get("groups", [])):
            raise ValueError("گروه تأییدنشده در خروجی وجود دارد.")
        if any(
            item.get("review_status") not in {"approved", "excluded"}
            or (item.get("review_status") == "excluded" and not item.get("reason"))
            for item in component.get("standalone_source_items", [])
        ):
            raise ValueError("Source Item مستقل تعیین تکلیف‌نشده در خروجی وجود دارد.")
    approved_result = deepcopy(result)
    for component in approved_result["component_results"].values():
        component["standalone_source_items"] = [
            item
            for item in component["standalone_source_items"]
            if item.get("review_status") == "approved"
        ]
    return approved_result
