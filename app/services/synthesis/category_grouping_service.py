from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.repositories.synthesis_repository import (
    get_run_sources,
    get_synthesis_run,
    get_next_stage_version,
    list_stage_results,
    mark_stage_results_stale,
    save_stage_result,
)
from app.services.synthesis.category_grouping_markdown_parser import (
    CategoryGroupingMarkdownError,
    parse_category_grouping_markdown,
)
from app.services.synthesis.category_grouping_prompt import (
    build_category_grouping_prompt,
)
from app.services.synthesis.category_grouping_validator import validate_category_grouping_result
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS

STAGE_KEY = "category_grouping"
STAGE_COMPONENT_KEY = "semantic_groups"
POOL_STAGE_KEY = "pool_extraction"
POOL_COMPONENT_KEY = "interview_pools"
ERROR_MESSAGES = {
    "markdown_parse_error": "ساختار Markdown قابل پردازش نیست.",
    "invalid_metadata_fields": "فیلدهای METADATA با قرارداد سازگار نیستند.",
    "metadata_mismatch": "فراداده خروجی با Run یا ورودی جاری مطابقت ندارد.",
    "component_set_mismatch": "پنج مؤلفه الزامی کامل و دقیق ارائه نشده‌اند.",
    "invalid_component_result": "ساختار نتیجه مؤلفه نامعتبر است.",
    "invalid_component_sections": "بخش‌های گروه، مستقل یا تناقض قالب معتبر ندارند.",
    "duplicate_input_source_item_id": "شناسه Source Item در خود Pool ورودی تکراری است.",
    "invalid_group": "ساختار گروه معتبر نیست.",
    "missing_required_field": "فیلد الزامی وجود ندارد.",
    "unknown_field": "فیلدی خارج از قرارداد ارائه شده است.",
    "group_heading_id_mismatch": "شناسه گروه در Heading و فیلد group_id متفاوت است.",
    "duplicate_group_id": "شناسه گروه تکراری است.",
    "group_component_mismatch": "گروه به مؤلفه نادرستی نسبت داده شده است.",
    "group_has_fewer_than_two_members": "هر گروه باید دست‌کم دو Source Item داشته باشد.",
    "duplicate_source_item_in_group": "یک Source Item در یک گروه تکرار شده است.",
    "duplicate_evidence_ref_id": "شناسه شاهد در یک بخش بیش از یک بار تکرار شده است.",
    "unknown_source_item_id": "شناسه Source Item در Pool تأییدشده وجود ندارد.",
    "duplicate_source_item_id": "Source Item بیش از یک بار تعیین تکلیف شده است.",
    "missing_source_item_id": "Source Item از خروجی جا افتاده است.",
    "source_item_component_mismatch": "Source Item در مؤلفه اصلی خود قرار ندارد.",
    "unknown_evidence_ref_id": "شناسه شاهد در ورودی وجود ندارد.",
    "evidence_not_owned_by_group_member": "شاهد گروه متعلق به اعضای آن گروه نیست.",
    "evidence_not_owned_by_contradiction_item": "شاهد تناقض متعلق به Source Itemهای ارجاع‌شده نیست.",
    "invalid_confidence": "مقدار confidence باید عددی میان صفر و یک باشد.",
    "invalid_boolean": "مقدار needs_researcher_review باید true یا false باشد.",
    "empty_required_value": "یک مقدار متنی الزامی خالی است.",
    "empty_group_analysis": "شباهت‌ها و تفاوت‌های گروه باید توضیح داده شوند.",
    "contradiction_heading_id_mismatch": "شناسه تناقض در Heading و فیلد contradiction_id متفاوت است.",
    "duplicate_contradiction_id": "شناسه تناقض تکراری است.",
    "contradiction_component_mismatch": "تناقض به مؤلفه نادرستی نسبت داده شده است.",
    "contradiction_without_source_items": "تناقض باید به Source Item واقعی ارجاع دهد.",
    "invalid_contradiction": "ساختار تناقض معتبر نیست.",
    "duplicate_contradiction_source_item_id": "Source Item تناقض تکراری است.",
    "invalid_quality_count": "یکی از شمارنده‌های کنترل کیفیت عدد معتبر نیست.",
    "quality_count_mismatch": "شمارنده کنترل کیفیت با داده واقعی مطابقت ندارد.",
    "quality_audit_reports_issues": "خروجی در Quality Audit شناسه ناقص، تکراری یا ناشناخته گزارش کرده است.",
    "evidence_quote_repeated_in_output": "متن شاهد در خروجی تکرار شده است؛ فقط شناسه شاهد مجاز است.",
}


def _latest_result(run_id: str, stage_key: str, component_key: str) -> dict[str, Any] | None:
    results = [
        item for item in list_stage_results(run_id)
        if item["stage_key"] == stage_key and item["component_key"] == component_key
    ]
    return max(results, key=lambda item: item["version"]) if results else None


def _approved_pool(run_id: str) -> tuple[dict[str, Any], int, int]:
    run = get_synthesis_run(run_id)
    if run is None:
        raise ValueError("اجرای تلفیقی پیدا نشد.")
    if run.get("status") == "source_stale":
        raise ValueError("Snapshot یکی از مصاحبه‌های این Run منقضی شده است؛ برای ادامه یک Run تازه بسازید.")
    sources = get_run_sources(run_id)
    if not sources:
        raise ValueError("برای این Run هیچ منبع ثبت‌شده‌ای وجود ندارد.")
    pool_result = _latest_result(run_id, POOL_STAGE_KEY, POOL_COMPONENT_KEY)
    if pool_result is None:
        raise ValueError("ابتدا Pool فاز سوم را تولید و بازبینی کنید.")
    pool = pool_result.get("output_json") or {}
    validation = pool_result.get("validation_report_json") or pool.get("validation_report") or {}
    metadata = pool.get("metadata") if isinstance(pool.get("metadata"), dict) else {}
    pools = pool.get("pools")
    if pool_result.get("approval_status") != "approved" or pool_result.get("status") not in {"ready", "approved"}:
        raise ValueError("Pool فاز سوم هنوز تأیید نهایی پژوهشگر را ندارد.")
    if not isinstance(validation, dict) or not validation.get("valid"):
        raise ValueError("Pool فاز سوم دارای خطای اعتبارسنجی است و قابل استفاده نیست.")
    if pool.get("run_id") != run_id or metadata.get("source_count") != len(sources):
        raise ValueError("تعداد منابع Pool با منابع ثبت‌شده Run جاری مطابقت ندارد.")
    registered_ids = {row["source_project_id"] for row in sources}
    source_coverage = pool.get("source_coverage") if isinstance(pool.get("source_coverage"), list) else []
    covered_ids = {
        row.get("source_project_id")
        for row in source_coverage
        if isinstance(row, dict)
        and row.get("processed")
    }
    if registered_ids != covered_ids:
        raise ValueError("منابع ثبت‌شده Run با منابع پردازش‌شده Pool مطابقت ندارند.")
    registered_hashes = {row["source_project_id"]: row["source_hash"] for row in sources}
    covered_hashes = {
        row["source_project_id"]: row.get("source_hash")
        for row in source_coverage
        if isinstance(row, dict) and row.get("processed")
    }
    if registered_hashes != covered_hashes:
        raise ValueError("Snapshot یکی از منابع پس از تأیید Pool تغییر کرده است؛ فاز سوم را دوباره بررسی کنید.")
    required_pool_keys = set((*POOL_COMPONENT_KEYS, "central_phenomenon"))
    if not isinstance(pools, dict) or not required_pool_keys.issubset(pools):
        raise ValueError("ساختار Pool فاز سوم ناقص است.")
    if any(not isinstance(pools.get(key), list) for key in required_pool_keys):
        raise ValueError("ساختار مجموعه‌های Pool فاز سوم معتبر نیست.")
    return pool, pool_result["version"], len(sources)


def build_grouping_prompt(run_id: str, *, enforce_size: bool = True) -> dict[str, Any]:
    pool, _, source_count = _approved_pool(run_id)
    result = build_category_grouping_prompt(
        run_id=run_id,
        pool_result=pool,
        source_count=source_count,
        enforce_size=enforce_size,
    )
    return {
        "run_id": run_id,
        "stage_key": STAGE_KEY,
        "input_hash": result["input_hash"],
        "prompt_text": result["prompt_text"],
        "size_report": result["size_report"],
        "source_item_count": result["size_report"]["source_item_count"],
    }


def grouping_page_data(run_id: str) -> dict[str, Any]:
    prompt = build_grouping_prompt(run_id)
    pool, _, _ = _approved_pool(run_id)
    latest = _latest_result(run_id, STAGE_KEY, STAGE_COMPONENT_KEY)
    return {
        "prompt": prompt,
        "latest": latest,
        "evidence_by_id": {
            str(item.get("evidence_ref_id")): {
                **item,
                "source_title": pool.get("source_metadata", {}).get(item.get("source_project_id"), {}).get("title"),
            }
            for item in pool.get("evidence", [])
            if item.get("evidence_ref_id")
        },
        "source_items": {
            str(item["source_item_id"]): item
            for key in POOL_COMPONENT_KEYS
            for item in pool.get("pools", {}).get(key, [])
        },
    }


def _report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# گزارش اعتبارسنجی گروه‌بندی مقوله‌ها",
        "",
        f"- وضعیت: {'معتبر' if report.get('valid') else 'نامعتبر'}",
        f"- تعداد Source Item بررسی‌شده: {report.get('checked_source_item_count', 0)}",
        f"- تعداد گروه بررسی‌شده: {report.get('checked_group_count', 0)}",
        f"- تعداد تناقض بررسی‌شده: {report.get('checked_contradiction_count', 0)}",
        "",
        "## خطاها",
    ]
    errors = report.get("errors") or []
    if not errors:
        lines.append("- موردی نیست.")
    for index, error in enumerate(errors, start=1):
        issue_type = str(error.get("issue_type") or "")
        message = error.get("message") or ERROR_MESSAGES.get(issue_type, "خطای اعتبارسنجی رخ داده است.")
        details = ", ".join(
            f"{key}={value}"
            for key, value in error.items()
            if key not in {"issue_type", "message"}
        )
        lines.append(f"- {index}. {message} (`{issue_type}`)" + (f" — {details}" if details else ""))
    lines.extend(("", "## هشدارها"))
    warnings = report.get("warnings") or []
    if not warnings:
        lines.append("- موردی نیست.")
    for warning in warnings:
        lines.append(f"- نیازمند بررسی پژوهشگر: {warning.get('group_id', 'گروه نامشخص')}")
    return "\n".join(lines)


def _with_internal_group_ids(
    parsed: dict[str, Any],
    prior_result: dict[str, Any] | None,
) -> dict[str, Any]:
    previous_components = (prior_result or {}).get("output_json", {}).get("component_results", {})
    for component_key, component_result in parsed["component_results"].items():
        previous_groups = previous_components.get(component_key, {}).get("groups", [])
        for group in component_result["groups"]:
            member_ids = set(group["source_item_ids"])
            exact_match = next(
                (
                    previous
                    for previous in previous_groups
                    if set(previous.get("source_item_ids", [])) == member_ids
                ),
                None,
            )
            group["ai_group_id"] = group["group_id"]
            group["group_id"] = exact_match.get("group_id") if exact_match else f"cgrp_{uuid4().hex}"
            group["supersedes_group_ids"] = [
                previous["group_id"]
                for previous in previous_groups
                if member_ids.intersection(previous.get("source_item_ids", []))
            ] if exact_match is None else []
            group["review_status"] = "pending"
        component_result["standalone_source_items"] = [
            {"source_item_id": item_id, "review_status": "pending", "reason": ""}
            for item_id in component_result["standalone_source_items"]
        ]
    return parsed


def submit_grouping_output(run_id: str, raw_output_text: str) -> dict[str, Any]:
    current = build_grouping_prompt(run_id)
    pool, _, source_count = _approved_pool(run_id)
    try:
        parsed = parse_category_grouping_markdown(raw_output_text)
        report = validate_category_grouping_result(
            parsed,
            run_id=run_id,
            input_hash=current["input_hash"],
            source_items_by_component={
                key: list(pool["pools"].get(key, []))
                for key in POOL_COMPONENT_KEYS
            },
            evidence=pool.get("evidence", []),
            raw_output_text=raw_output_text,
        )
    except CategoryGroupingMarkdownError as error:
        parsed = None
        report = {
            "valid": False,
            "errors": [{"issue_type": "markdown_parse_error", "message": message} for message in error.errors],
            "warnings": [],
            "checked_source_item_count": sum(len(pool["pools"].get(key, [])) for key in POOL_COMPONENT_KEYS),
            "checked_evidence_count": len(pool.get("evidence", [])),
            "checked_group_count": 0,
            "checked_contradiction_count": 0,
        }
    if parsed is not None and report["valid"]:
        parsed = _with_internal_group_ids(
            parsed,
            _latest_result(run_id, STAGE_KEY, STAGE_COMPONENT_KEY),
        )
    version = get_next_stage_version(run_id, STAGE_KEY, STAGE_COMPONENT_KEY)
    saved = save_stage_result(
        run_id,
        STAGE_KEY,
        STAGE_COMPONENT_KEY,
        version=version,
        status="ready_for_researcher_review" if report["valid"] else "invalid",
        input_hash=current["input_hash"],
        prompt_text=current["prompt_text"],
        raw_output_text=raw_output_text,
        output_json=parsed if parsed is not None else {},
        validation_report_json=report,
        validation_report_text=_report_markdown(report),
        researcher_edits_json={"decisions": []},
        approval_status="pending",
    )
    mark_stage_results_stale(
        run_id,
        ("theme_synthesis", "phenomenon_synthesis", "relationship_synthesis", "final_model"),
    )
    return {
        "stage_result_id": saved["stage_result_id"],
        "run_id": run_id,
        "version": version,
        "status": saved["status"],
        "input_hash": current["input_hash"],
        "valid": report["valid"],
        "parsed_result": parsed,
        "validation_report": report,
        "validation_report_text": saved["validation_report_text"],
        "size_report": current["size_report"],
        "source_count": source_count,
    }


def latest_grouping_result(run_id: str) -> dict[str, Any] | None:
    latest = _latest_result(run_id, STAGE_KEY, STAGE_COMPONENT_KEY)
    if latest is None:
        return None
    try:
        current = build_grouping_prompt(run_id, enforce_size=False)
    except ValueError:
        latest["stale"] = True
        return latest
    latest["stale"] = latest["input_hash"] != current["input_hash"]
    return latest


def correction_prompt(run_id: str) -> str:
    latest = latest_grouping_result(run_id)
    if latest is None:
        raise ValueError("هنوز خروجی گروه‌بندی برای این Run ثبت نشده است.")
    if latest.get("stale"):
        raise ValueError("Pool فاز سوم پس از تولید این خروجی تغییر کرده است؛ خروجی اصلاحی برای ورودی قدیمی مجاز نیست.")
    report = latest.get("validation_report_text") or ""
    return (
        "# CATEGORY GROUPING CORRECTION REQUEST\n\n"
        "خروجی Markdown قبلی را فقط با توجه به خطاهای زیر اصلاح کن. داده‌ها، شناسه‌ها و قرارداد اصلی را تغییر نده. "
        "Source Item یا Evidence جدید نساز و خروجی را فقط به‌صورت سند Markdown کامل مطابق الگوی درخواست اصلی ارائه بده.\n\n"
        "## VALIDATION ERRORS\n\n"
        f"{report}"
    )
