from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.repositories.synthesis_repository import (
    get_next_stage_version,
    mark_stage_results_stale,
    save_stage_result,
    update_run_status,
)
from app.services.synthesis.theme_synthesis_markdown_parser import (
    ThemeSynthesisMarkdownError,
    parse_theme_synthesis_markdown,
)
from app.services.synthesis.theme_synthesis_prompt import (
    STAGE_COMPONENT_KEY,
    STAGE_KEY,
    ThemePromptSizeLimitError,
    build_theme_synthesis_prompt,
    latest_theme_result,
)
from app.services.synthesis.theme_synthesis_validator import validate_theme_synthesis_result


ERROR_MESSAGES = {
    "invalid_metadata_fields": "فیلدهای METADATA با قرارداد سازگار نیستند.",
    "metadata_mismatch": "فراداده خروجی با Run، Hash یا نسخه فاز چهارم مطابقت ندارد.",
    "component_set_mismatch": "پنج مؤلفه الزامی کامل و دقیق ارائه نشده‌اند.",
    "invalid_component_result": "ساختار نتیجه مؤلفه نامعتبر است.",
    "invalid_component_sections": "بخش‌های تم، واحدهای بدون تم یا تناقض قالب معتبر ندارند.",
    "unknown_category_unit_id": "شناسه Category Unit در ورودی تأییدشده وجود ندارد.",
    "duplicate_category_unit_id": "Category Unit بیش از یک بار تعیین تکلیف شده است.",
    "missing_category_unit_id": "Category Unit از خروجی جا افتاده است.",
    "unknown_evidence_ref_id": "شناسه شاهد در ورودی وجود ندارد.",
    "evidence_not_owned_by_theme_unit": "شاهد تم متعلق به Category Unitهای آن تم نیست.",
    "invalid_confidence": "مقدار confidence باید عددی میان صفر و یک باشد.",
    "invalid_boolean": "needs_researcher_review باید true یا false باشد.",
    "empty_required_value": "یک مقدار متنی الزامی خالی است.",
    "quality_count_mismatch": "شمارنده کنترل کیفیت با داده واقعی مطابقت ندارد.",
    "quality_audit_reports_issues": "Quality Audit شناسه ناقص، تکراری یا ناشناخته گزارش کرده است.",
    "evidence_quote_repeated_in_output": "متن شاهد در خروجی تکرار شده است؛ فقط شناسه شاهد مجاز است.",
}


def _report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# گزارش اعتبارسنجی استخراج تم",
        "",
        f"- معتبر: {'بله' if report.get('valid') else 'خیر'}",
        f"- Category Unit بررسی‌شده: {report.get('checked_category_unit_count', 0)}",
        f"- شاهد بررسی‌شده: {report.get('checked_evidence_count', 0)}",
        f"- تم بررسی‌شده: {report.get('checked_theme_count', 0)}",
        f"- تناقض بررسی‌شده: {report.get('checked_contradiction_count', 0)}",
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
    return "\n".join(lines)


def _with_internal_ids(
    parsed: dict[str, Any],
    prior: dict[str, Any] | None,
) -> dict[str, Any]:
    previous_components = (prior or {}).get("output_json", {}).get("component_results", {})
    for component_key, component in parsed["component_results"].items():
        old_component = previous_components.get(component_key, {})
        old_themes = old_component.get("themes", [])
        for theme in component["themes"]:
            member_ids = set(theme["constituent_category_unit_ids"])
            exact = next(
                (item for item in old_themes if set(item.get("constituent_category_unit_ids", [])) == member_ids),
                None,
            )
            theme["ai_theme_id"] = theme["theme_id"]
            theme["theme_id"] = exact.get("theme_id") if exact else f"theme_{uuid4().hex}"
            theme["supersedes_theme_ids"] = [
                item["theme_id"]
                for item in old_themes
                if member_ids.intersection(item.get("constituent_category_unit_ids", []))
            ] if exact is None else []
            theme["review_status"] = "pending"
        component["unthemed_category_units"] = [
            {
                **item,
                "review_status": "pending",
            }
            for item in component["unthemed_category_units"]
        ]
        old_contradictions = old_component.get("contradictions", [])
        for contradiction in component["contradictions"]:
            member_ids = set(contradiction["category_unit_ids"])
            exact = next(
                (
                    item for item in old_contradictions
                    if set(item.get("category_unit_ids", [])) == member_ids
                    and set(item.get("evidence_ref_ids", [])) == set(contradiction["evidence_ref_ids"])
                ),
                None,
            )
            contradiction["ai_contradiction_id"] = contradiction["contradiction_id"]
            contradiction["contradiction_id"] = (
                exact.get("contradiction_id") if exact else f"contradiction_{uuid4().hex}"
            )
            contradiction["review_status"] = "pending"
    return parsed


def submit_theme_synthesis_output(run_id: str, raw_output_text: str) -> dict[str, Any]:
    current = build_theme_synthesis_prompt(run_id)
    parsed = None
    try:
        parsed = parse_theme_synthesis_markdown(raw_output_text)
        report = validate_theme_synthesis_result(
            parsed,
            run_id=run_id,
            input_hash=current["input_hash"],
            source_phase_version=current["source_phase_version"],
            category_units=current["category_units"],
            raw_output_text=raw_output_text,
        )
    except ThemeSynthesisMarkdownError as error:
        report = {
            "valid": False,
            "errors": [{"issue_type": "markdown_parse_error", "message": message} for message in error.errors],
            "warnings": [],
            "checked_category_unit_count": len(current["category_units"]),
            "checked_evidence_count": len({
                evidence_id for unit in current["category_units"]
                for evidence_id in unit.get("evidence_ref_ids", [])
            }),
            "checked_theme_count": 0,
            "checked_contradiction_count": 0,
        }
    if parsed is not None and report["valid"]:
        prior = latest_theme_result(run_id)
        parsed = _with_internal_ids(parsed, prior)
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
        source_phase_version=current["source_phase_version"],
        researcher_edits_json={"decisions": []},
        approval_status="pending",
    )
    mark_stage_results_stale(
        run_id,
        ("phenomenon_synthesis", "relationship_synthesis", "final_model"),
    )
    if report["valid"]:
        update_run_status(run_id, status="in_progress", current_stage=STAGE_KEY)
    return {
        "stage_result_id": saved["stage_result_id"],
        "run_id": run_id,
        "version": version,
        "status": saved["status"],
        "input_hash": current["input_hash"],
        "source_phase_version": current["source_phase_version"],
        "valid": report["valid"],
        "parsed_result": parsed,
        "validation_report": report,
        "validation_report_text": saved["validation_report_text"],
        "size_report": current["size_report"],
    }


def theme_synthesis_page_data(run_id: str) -> dict[str, Any]:
    latest = latest_theme_result(run_id)
    try:
        prompt = build_theme_synthesis_prompt(run_id)
    except ThemePromptSizeLimitError as error:
        return {
            "prompt": None,
            "latest": latest,
            "evidence_by_id": {},
            "page_error": str(error),
            "size_error_report": error.report,
        }
    except ValueError as error:
        return {
            "prompt": None,
            "latest": latest,
            "evidence_by_id": {},
            "page_error": str(error),
            "size_error_report": None,
        }
    evidence_by_id = {
        reference["evidence_ref_id"]: reference
        for unit in prompt["category_units"]
        for source_item in unit.get("source_items", [])
        for reference in source_item.get("evidence_refs", [])
    }
    return {
        "prompt": prompt,
        "latest": latest,
        "evidence_by_id": evidence_by_id,
        "page_error": None,
        "size_error_report": None,
    }


def theme_correction_prompt(run_id: str) -> str:
    latest = latest_theme_result(run_id)
    if latest is None:
        raise ValueError("هنوز خروجی استخراج تم برای این Run ثبت نشده است.")
    if latest.get("stale"):
        raise ValueError("نسخه تأییدشده فاز چهارم تغییر کرده است؛ خروجی اصلاحی برای ورودی قدیمی مجاز نیست.")
    return (
        "# THEME SYNTHESIS CORRECTION REQUEST\n\n"
        "سند Markdown قبلی را فقط با توجه به خطاهای اعتبارسنجی زیر اصلاح کن. ورودی، شناسه‌ها و قرارداد را تغییر نده؛ "
        "همه Category Unitها را دقیقاً یک‌بار در تم یا فهرست بدون تم تعیین تکلیف کن و خروجی کامل Markdown بده.\n\n"
        "## VALIDATION ERRORS\n\n"
        f"{latest.get('validation_report_text') or ''}"
    )
