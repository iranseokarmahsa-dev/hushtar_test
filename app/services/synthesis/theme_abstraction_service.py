from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.repositories.synthesis_repository import (
    get_next_stage_version,
    list_stage_results,
    save_stage_result,
)
from app.services.synthesis.category_integration_service import (
    COMPONENT_LABELS,
    get_approved_category_integrations,
    get_registered_category_integrations,
)
from app.services.synthesis.category_grouping_service import _approved_pool
from app.services.synthesis.component_markdown_sanitizer import (
    sanitize_theme_abstraction_markdown,
)
from app.services.synthesis.normalization_service import stable_hash
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS
from app.services.synthesis.theme_abstraction_markdown_parser import (
    ThemeAbstractionMarkdownError,
    parse_theme_abstraction_markdown,
)

STAGE_KEY = "theme_abstraction"
PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "synthesis" / "theme_abstraction.txt"


def _text_block(value: Any, *, indentation: int = 2) -> str:
    text = "" if value is None else str(value)
    prefix = " " * indentation
    if not text:
        return f"{prefix}NONE"
    fence_length = max(3, max((len(run) for run in re.findall(r"~+", text)), default=0) + 1)
    fence = "~" * fence_length
    content = "\n".join(f"{prefix}{line}" for line in text.splitlines())
    return f"{prefix}{fence}text\n{content}\n{prefix}{fence}"


def build_theme_abstraction_prompt(run_id: str, component_key: str) -> dict[str, Any]:
    if component_key not in POOL_COMPONENT_KEYS:
        raise ValueError("مؤلفهٔ درخواستی معتبر نیست.")
    registered = get_registered_category_integrations(run_id).get(component_key)
    if registered is None:
        raise ValueError("ابتدا خروجی معتبر همین مؤلفه را در مرحلهٔ ۳ ثبت کنید.")

    pool, _, _ = _approved_pool(run_id, allow_partial_pool=True)
    pool_report = pool.get("validation_report") or {}
    pool_errors = pool_report.get("errors", [])
    if not isinstance(pool_errors, list) or (not pool_report.get("valid") and not pool_errors) or any(
        not isinstance(error, dict) or error.get("issue_type") != "invalid_evidence"
        for error in pool_errors
    ):
        raise ValueError("خطاهای ساختاری Pool باید پیش از استخراج تم بررسی شوند.")
    evidence_warnings = {
        str(item.get("evidence_ref_id")): {
            "evidence_ref_id": str(item.get("evidence_ref_id")),
            "source_project_id": item.get("source_project_id"),
            "paragraph_id": item.get("paragraph_id"),
            "issue_type": item.get("validation_issue") or "invalid_evidence",
        }
        for item in pool.get("evidence", [])
        if isinstance(item, dict) and item.get("evidence_ref_id") and not item.get("valid")
    }
    source_items_by_id = {
        str(item["source_item_id"]): item
        for item in pool.get("pools", {}).get(component_key, [])
        if isinstance(item, dict) and item.get("source_item_id")
    }
    categories = [
        {
            "integrated_category_id": str(item["category_id"]),
            "title": str(item["title"]),
            "definition": str(item["definition"]),
            "source_item_ids": sorted({str(value) for value in item.get("source_item_ids", [])}),
            "source_origins": [
                {
                    "source_item_id": str(source_item_id),
                    "source_project_id": str(source.get("source_project_id") or ""),
                    "source_interview_id": str(source.get("source_interview_id") or ""),
                    "source_title": str(source.get("source_title") or ""),
                    "source_category_id": str(source.get("source_category_id") or ""),
                    "original_category_name": str(source.get("original_category_name") or ""),
                    "approved_category_name": str(
                        source.get("researcher_approved_category_name") or ""
                    ),
                    "source_subcategory_ids": sorted(
                        str(value) for value in source.get("source_subcategory_ids", [])
                    ),
                    "evidence_warnings": [
                        evidence_warnings[str(reference["evidence_ref_id"])]
                        for reference in source.get("evidence_refs", [])
                        if isinstance(reference, dict)
                        and str(reference.get("evidence_ref_id")) in evidence_warnings
                    ],
                }
                for source_item_id in sorted(str(value) for value in item.get("source_item_ids", []))
                if (source := source_items_by_id.get(str(source_item_id))) is not None
            ],
        }
        for item in registered["integrated_categories"]
    ]
    if any(len(category["source_origins"]) != len(category["source_item_ids"]) for category in categories):
        raise ValueError("منشأ یکی از Source Itemهای مرحلهٔ ۳ در Pool تأییدشده پیدا نشد.")
    source_phase_version = int(registered["version"])
    template = PROMPT_PATH.read_text(encoding="utf-8")
    input_hash = stable_hash({
        "run_id": run_id,
        "component_key": component_key,
        "source_stage_result_id": registered["stage_result_id"],
        "source_phase_version": source_phase_version,
        "source_input_hash": registered["input_hash"],
        "categories": categories,
        "prompt_template": template,
    })
    category_id_map = {
        f"ITEM_{index}": category["integrated_category_id"]
        for index, category in enumerate(categories, start=1)
    }
    category_lines = []
    for index, category in enumerate(categories, start=1):
        item_alias = f"ITEM_{index}"
        category_lines.extend([
            f"### INPUT ITEM: {item_alias}",
            f"- item_id: {item_alias}",
            "- title:",
            _text_block(category["title"]),
            "- definition:",
            _text_block(category["definition"]),
            "",
        ])
    substitutions = {
        "COMPONENT_LABEL": COMPONENT_LABELS[component_key],
        "CATEGORIES": "\n".join(category_lines).strip() or "NONE",
    }
    prompt_text = re.sub(
        r"\{\{(COMPONENT_LABEL|CATEGORIES)\}\}",
        lambda match: substitutions[match.group(1)],
        template,
    )
    return {
        "run_id": run_id,
        "stage_key": STAGE_KEY,
        "component_key": component_key,
        "component_label": COMPONENT_LABELS[component_key],
        "input_hash": input_hash,
        "source_phase_version": source_phase_version,
        "source_stage_result_id": registered["stage_result_id"],
        "source_categories": categories,
        "category_id_map": category_id_map,
        "inherited_warnings": list(evidence_warnings.values()),
        "source_stage_result_id": registered["stage_result_id"],
        "prompt_text": prompt_text,
    }


def _as_count(value: Any, field: str, errors: list[str]) -> int | None:
    digits = str(value or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    try:
        count = int(digits)
    except (TypeError, ValueError):
        errors.append(f"شمارندهٔ `{field}` عدد صحیح معتبر نیست.")
        return None
    if count < 0:
        errors.append(f"شمارندهٔ `{field}` نمی‌تواند منفی باشد.")
        return None
    return count


def _lineage_for_category_ids(
    source_categories: list[dict[str, Any]],
    category_ids: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    categories_by_id = {
        str(category["integrated_category_id"]): category
        for category in source_categories
    }
    source_item_ids = sorted({
        str(source_item_id)
        for category_id in category_ids
        for source_item_id in categories_by_id.get(category_id, {}).get("source_item_ids", [])
    })
    origins_by_id = {
        str(origin["source_item_id"]): origin
        for category in source_categories
        for origin in category.get("source_origins", [])
        if origin.get("source_item_id")
    }
    return source_item_ids, [
        origins_by_id[source_item_id]
        for source_item_id in source_item_ids
        if source_item_id in origins_by_id
    ]


def validate_theme_abstraction_result(
    parsed: dict[str, Any],
    *,
    run_id: str,
    component_key: str,
    input_hash: str,
    source_phase_version: int,
    source_categories: list[dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    is_component_themes_contract = parsed.get("_contract") == "component_themes_v1"
    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    expected_metadata = {"run_id", "component_key", "input_hash"}
    if not is_component_themes_contract:
        expected_metadata.add("source_phase_version")
    if set(metadata) != expected_metadata:
        metadata_fields = "run_id، component_key و input_hash"
        if not is_component_themes_contract:
            metadata_fields += " و source_phase_version"
        errors.append(f"فیلدهای METADATA باید دقیقاً {metadata_fields} باشند.")
    expected_metadata_values = {
        "run_id": run_id,
        "component_key": component_key,
        "input_hash": input_hash,
    }
    if not is_component_themes_contract:
        expected_metadata_values["source_phase_version"] = str(source_phase_version)
    for field, expected in expected_metadata_values.items():
        if str(metadata.get(field) or "").strip() != expected:
            errors.append(f"فرادادهٔ {field} با اجرای جاری مطابقت ندارد.")

    expected_ids = [str(category["integrated_category_id"]) for category in source_categories]
    expected_set = set(expected_ids)
    source_items_by_category = {
        str(category["integrated_category_id"]): {
            str(source_item_id)
            for source_item_id in category.get("source_item_ids", [])
        }
        for category in source_categories
    }
    if len(expected_ids) != len(expected_set):
        errors.append("شناسهٔ مقولهٔ ورودی تکراری است.")
    themes = parsed.get("themes")
    unthemed = parsed.get("categories_without_a_theme")
    if not isinstance(themes, list):
        themes = []
        errors.append("بخش THEMES باید فهرست تم‌ها باشد.")
    if not isinstance(unthemed, list):
        unthemed = []
        errors.append("بخش CATEGORIES WITHOUT A THEME باید فهرست مقوله‌ها باشد.")
    if not expected_ids and (themes or unthemed):
        errors.append("برای ورودی خالی نباید تم یا مقولهٔ فاقد تم ساخته شود.")

    theme_ids: set[str] = set()
    seen_category_ids: list[str] = []
    for index, theme in enumerate(themes, start=1):
        if not isinstance(theme, dict):
            errors.append(f"ساختار تم شمارهٔ {index} معتبر نیست.")
            continue
        theme_id = str(theme.get("theme_id") or "").strip()
        expected_theme_id = f"theme_{index}"
        if theme_id != expected_theme_id or theme.get("_heading_id") != theme_id:
            errors.append(f"شناسهٔ تم شمارهٔ {index} باید `{expected_theme_id}` باشد و با Heading تطبیق کند.")
        if theme_id in theme_ids:
            errors.append(f"شناسهٔ تم تکراری است: {theme_id}")
        theme_ids.add(theme_id)
        if not str(theme.get("title") or "").strip():
            errors.append(f"عنوان تم شمارهٔ {index} خالی است.")
        if not str(theme.get("definition") or "").strip():
            errors.append(f"تعریف تم شمارهٔ {index} خالی است.")
        if not is_component_themes_contract and not str(theme.get("rationale") or "").strip():
            errors.append(f"دلیل معنایی تم شمارهٔ {index} خالی است.")
        if not is_component_themes_contract and not str(theme.get("abstraction_level") or "").strip():
            errors.append(f"توضیح سطح انتزاع تم شمارهٔ {index} خالی است.")
        if not is_component_themes_contract and not str(theme.get("preserved_dimensions") or "").strip():
            errors.append(f"ابعاد و تفاوت‌های حفظ‌شدهٔ تم شمارهٔ {index} خالی است.")
        category_ids = theme.get("integrated_category_ids")
        if not isinstance(category_ids, list) or not category_ids:
            errors.append(f"تم شمارهٔ {index} باید دست‌کم یک مقولهٔ تلفیقی داشته باشد.")
            continue
        normalized_category_ids = [str(item_id) for item_id in category_ids]
        if not is_component_themes_contract:
            theme_type = str(theme.get("theme_type") or "").strip()
            if theme_type == "shared" and len(normalized_category_ids) < 2:
                errors.append(f"تم مشترک شمارهٔ {index} باید دست‌کم دو مقوله داشته باشد.")
            elif theme_type == "standalone" and len(normalized_category_ids) != 1:
                errors.append(f"تم مستقل شمارهٔ {index} باید دقیقاً یک مقوله داشته باشد.")
            elif theme_type not in {"shared", "standalone"}:
                errors.append(f"نوع تم شمارهٔ {index} باید shared یا standalone باشد.")
        if is_component_themes_contract:
            seen_category_ids.extend(normalized_category_ids)
            continue
        reported_source_ids = theme.get("source_item_ids")
        expected_source_ids = sorted({
            source_item_id
            for category_id in normalized_category_ids
            for source_item_id in source_items_by_category.get(category_id, set())
        })
        if not isinstance(reported_source_ids, list):
            errors.append(f"تم شمارهٔ {index} باید فهرست Source Itemها را داشته باشد.")
        else:
            normalized_source_ids = [str(item_id) for item_id in reported_source_ids]
            if len(normalized_source_ids) != len(set(normalized_source_ids)):
                errors.append(f"Source Item تکراری در تم شمارهٔ {index} وجود دارد.")
            if set(normalized_source_ids) != set(expected_source_ids):
                errors.append(f"Source Itemهای تم شمارهٔ {index} با منشأ مقوله‌های عضو تطبیق ندارد.")
        seen_category_ids.extend(normalized_category_ids)

    for index, category in enumerate(unthemed, start=1):
        if not isinstance(category, dict):
            errors.append(f"ساختار مقولهٔ فاقد تم شمارهٔ {index} معتبر نیست.")
            continue
        category_id = str(category.get("integrated_category_id") or "").strip()
        if category.get("_heading_id") != category_id:
            errors.append(f"شناسهٔ Heading مقولهٔ فاقد تم شمارهٔ {index} مطابقت ندارد.")
        if not str(category.get("reason") or "").strip():
            errors.append(f"دلیل مقولهٔ تعیین‌تکلیف‌نشدهٔ شمارهٔ {index} خالی است.")
        reported_source_ids = category.get("source_item_ids")
        expected_source_ids = sorted(source_items_by_category.get(category_id, set()))
        if not isinstance(reported_source_ids, list):
            errors.append(f"مقولهٔ تعیین‌تکلیف‌نشدهٔ شمارهٔ {index} باید فهرست Source Itemها را داشته باشد.")
        else:
            normalized_source_ids = [str(item_id) for item_id in reported_source_ids]
            if len(normalized_source_ids) != len(set(normalized_source_ids)):
                errors.append(f"Source Item تکراری برای مقولهٔ تعیین‌تکلیف‌نشدهٔ شمارهٔ {index} وجود دارد.")
            if set(normalized_source_ids) != set(expected_source_ids):
                errors.append(f"Source Itemهای مقولهٔ تعیین‌تکلیف‌نشدهٔ شمارهٔ {index} با منشأ آن تطبیق ندارد.")
        seen_category_ids.append(category_id)

    counts: dict[str, int] = {}
    for category_id in seen_category_ids:
        counts[category_id] = counts.get(category_id, 0) + 1
    duplicate_count = sum(max(count - 1, 0) for count in counts.values())
    missing_count = len(expected_set - set(counts))
    unknown_count = sum(1 for category_id in seen_category_ids if category_id not in expected_set)
    if duplicate_count:
        errors.append(f"{duplicate_count} ارجاع تکراری به مقولهٔ تلفیقی وجود دارد.")
    if missing_count:
        errors.append(f"{missing_count} مقولهٔ تلفیقی تعیین‌تکلیف‌نشده وجود دارد.")
    if unknown_count:
        errors.append(f"{unknown_count} ارجاع به مقولهٔ تلفیقی ناشناخته وجود دارد.")

    if is_component_themes_contract:
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": [],
            "checked_category_count": len(expected_ids),
            "assigned_category_count": len(seen_category_ids),
            "duplicate_category_count": duplicate_count,
            "missing_category_count": missing_count,
            "unknown_category_count": unknown_count,
        }

    quality = parsed.get("quality_audit") if isinstance(parsed.get("quality_audit"), dict) else {}
    expected_quality = {
        "total_input_categories": len(expected_ids),
        "total_assigned_categories": len(seen_category_ids),
        "duplicate_category_ids": duplicate_count,
        "missing_category_ids": missing_count,
        "unknown_category_ids": unknown_count,
    }
    for field, expected in expected_quality.items():
        actual = _as_count(quality.get(field), field, errors)
        if actual is not None and actual != expected:
            errors.append(f"شمارندهٔ `{field}` با دادهٔ واقعی مطابقت ندارد (واقعی: {expected}).")
    expected_status = "PASS" if not (duplicate_count or missing_count or unknown_count) else "FAIL"
    status = str(quality.get("status") or "").strip().upper()
    if status not in {"PASS", "FAIL"}:
        errors.append("مقدار status در QUALITY AUDIT باید PASS یا FAIL باشد.")
    elif status != expected_status:
        errors.append(f"status در QUALITY AUDIT باید {expected_status} باشد.")
    warnings = quality.get("warnings") if isinstance(quality.get("warnings"), list) else []
    warnings = [str(item).strip() for item in warnings if str(item).strip() and str(item).strip().upper() != "NONE"]
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checked_category_count": len(expected_ids),
        "assigned_category_count": len(seen_category_ids),
        "duplicate_category_count": duplicate_count,
        "missing_category_count": missing_count,
        "unknown_category_count": unknown_count,
    }


def _latest_results(run_id: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for result in list_stage_results(run_id):
        if result["stage_key"] != STAGE_KEY or result["component_key"] not in POOL_COMPONENT_KEYS:
            continue
        key = result["component_key"]
        if key not in latest or result["version"] > latest[key]["version"]:
            latest[key] = result
    return latest


def _latest_approved_results(run_id: str) -> dict[str, dict[str, Any]]:
    approved: dict[str, dict[str, Any]] = {}
    for result in list_stage_results(run_id):
        if (
            result["stage_key"] != STAGE_KEY
            or result["component_key"] not in POOL_COMPONENT_KEYS
            or result.get("status") != "approved"
            or result.get("approval_status") != "approved"
        ):
            continue
        key = result["component_key"]
        if key not in approved or result["version"] > approved[key]["version"]:
            approved[key] = result
    return approved


def next_theme_abstraction_component(
    run_id: str,
    component_key: str,
    *,
    only_unapproved: bool = False,
) -> str | None:
    if component_key not in POOL_COMPONENT_KEYS:
        return None
    available = get_registered_category_integrations(run_id)
    completed = get_registered_theme_abstractions(run_id)
    approved = _latest_approved_results(run_id) if only_unapproved else {}
    start_index = POOL_COMPONENT_KEYS.index(component_key)
    for offset in range(1, len(POOL_COMPONENT_KEYS) + 1):
        candidate = POOL_COMPONENT_KEYS[(start_index + offset) % len(POOL_COMPONENT_KEYS)]
        library = available.get(candidate)
        if not library or not library["integrated_categories"] or candidate in completed:
            continue
        if candidate in approved:
            continue
        return candidate
    return component_key


def _validation_text(report: dict[str, Any]) -> str:
    lines = ["خروجی Markdown: " + ("معتبر" if report.get("valid") else "نامعتبر")]
    lines.extend(f"- خطا: {error}" for error in report.get("errors", []))
    lines.extend(f"- هشدار: {warning}" for warning in report.get("warnings", []))
    return "\n".join(lines)


def submit_theme_abstraction_output(
    run_id: str,
    component_key: str,
    raw_output_text: str,
) -> dict[str, Any]:
    prompt = build_theme_abstraction_prompt(run_id, component_key)
    sanitized_output_text = raw_output_text
    parsed = None
    try:
        parsed = parse_theme_abstraction_markdown(raw_output_text)
        sanitized_output_text = sanitize_theme_abstraction_markdown(raw_output_text)
        categories_by_id = {
            str(category["integrated_category_id"]): category
            for category in prompt["source_categories"]
        }
        category_id_map = prompt["category_id_map"]
        id_map = {
            **category_id_map,
            **{category_id: category_id for category_id in categories_by_id},
        }
        themes = []
        seen_category_ids: set[str] = set()
        duplicate_count = 0
        for item in parsed.get("themes", []):
            raw_ids = item.get("integrated_category_ids") or []
            category_ids = [
                id_map[str(category_id).strip()]
                for category_id in raw_ids
                if str(category_id).strip() in id_map
            ]
            if not category_ids:
                continue
            duplicate_count += len(category_ids) - len(set(category_ids))
            category_ids = list(dict.fromkeys(category_ids))
            first_category = categories_by_id[category_ids[0]]
            title = str(item.get("title") or "").strip() or first_category["title"]
            definition = (
                str(item.get("definition") or "").strip()
                or first_category["definition"]
            )
            source_item_ids, source_origins = _lineage_for_category_ids(
                prompt["source_categories"],
                category_ids,
            )
            themes.append({
                "theme_id": f"theme_{len(themes) + 1}",
                "theme_type": "standalone" if len(category_ids) == 1 else "shared",
                "title": title,
                "definition": definition,
                "rationale": "",
                "abstraction_level": "",
                "preserved_dimensions": "",
                "integrated_category_ids": category_ids,
                "source_item_ids": source_item_ids,
                "source_origins": source_origins,
                "review_status": "pending",
            })
            seen_category_ids.update(category_ids)

        missing_category_ids = [
            str(category["integrated_category_id"])
            for category in prompt["source_categories"]
            if str(category["integrated_category_id"]) not in seen_category_ids
        ]
        for category_id in missing_category_ids:
            category = categories_by_id[category_id]
            source_item_ids, source_origins = _lineage_for_category_ids(
                prompt["source_categories"],
                [category_id],
            )
            themes.append({
                "theme_id": f"theme_{len(themes) + 1}",
                "theme_type": "standalone",
                "title": category["title"],
                "definition": category["definition"],
                "rationale": "",
                "abstraction_level": "",
                "preserved_dimensions": "",
                "integrated_category_ids": [category_id],
                "source_item_ids": source_item_ids,
                "source_origins": source_origins,
                "review_status": "pending",
            })

        parsed = {
            "metadata": {
                "run_id": run_id,
                "component_key": component_key,
                "input_hash": prompt["input_hash"],
            },
            "source_stage_result_id": prompt["source_stage_result_id"],
            "inherited_warnings": prompt["inherited_warnings"],
            "system_validation_status": "system_validated",
            "researcher_review_status": "pending_researcher_review",
            "themes": themes,
            "categories_without_a_theme": [],
        }
        report = {
            "valid": True,
            "errors": [],
            "warnings": (
                [f"{len(missing_category_ids)} مقولهٔ جاافتاده به‌صورت تم مستقل افزوده شد."]
                if missing_category_ids else []
            ),
            "checked_category_count": len(prompt["source_categories"]),
            "assigned_category_count": sum(
                len(theme["integrated_category_ids"]) for theme in themes
            ),
            "duplicate_category_count": duplicate_count,
            "missing_category_count": 0,
            "unknown_category_count": 0,
        }
    except ThemeAbstractionMarkdownError as error:
        report = {
            "valid": False,
            "errors": error.errors,
            "warnings": [],
            "checked_category_count": len(prompt["source_categories"]),
            "assigned_category_count": 0,
            "duplicate_category_count": 0,
            "missing_category_count": len(prompt["source_categories"]),
            "unknown_category_count": 0,
        }
    version = get_next_stage_version(run_id, STAGE_KEY, component_key)
    return save_stage_result(
        run_id,
        STAGE_KEY,
        component_key,
        version=version,
        status="ready_for_researcher_review" if report["valid"] else "invalid",
        input_hash=prompt["input_hash"],
        prompt_text=prompt["prompt_text"],
        raw_output_text=sanitized_output_text,
        output_json=parsed if parsed is not None else {},
        validation_report_json=report,
        validation_report_text=_validation_text(report),
        source_phase_version=prompt["source_phase_version"],
        researcher_edits_json={"decisions": []},
        approval_status="pending",
    )


def theme_abstraction_page_data(run_id: str, component_key: str) -> dict[str, Any]:
    if component_key not in POOL_COMPONENT_KEYS:
        raise ValueError("مؤلفهٔ درخواستی معتبر نیست.")
    latest_results = _latest_results(run_id)
    latest = latest_results.get(component_key)
    approved_results = _latest_approved_results(run_id)
    history = [
        result
        for result in list_stage_results(run_id)
        if result["stage_key"] == STAGE_KEY and result["component_key"] == component_key
    ]
    registered_library = get_registered_category_integrations(run_id)
    registered_theme_libraries = get_registered_theme_abstractions(run_id)
    complete_components = {
        key for key in registered_library
        if not registered_library[key]["integrated_categories"]
        or key in registered_theme_libraries
    }
    component_rows = [
        {
            "key": key,
            "label": COMPONENT_LABELS[key],
            "category_library": registered_library.get(key),
            "latest": latest_results.get(key),
            "approved_result": approved_results.get(key),
            "registered_theme": registered_theme_libraries.get(key),
        }
        for key in POOL_COMPONENT_KEYS
    ]
    page_error = None
    try:
        prompt = build_theme_abstraction_prompt(run_id, component_key)
    except ValueError as error:
        prompt = None
        page_error = str(error)
    if latest:
        latest["stale"] = (
            latest.get("status") == "stale"
            or latest.get("approval_status") == "stale"
            or prompt is None
            or latest.get("input_hash") != prompt["input_hash"]
        )
    categories_by_id = {
        category["integrated_category_id"]: category
        for category in (prompt["source_categories"] if prompt else [])
    }
    return {
        "run_id": run_id,
        "components": component_rows,
        "active_component_key": component_key,
        "active_prompt": prompt,
        "page_error": page_error,
        "latest": latest,
        "history": history,
        "categories_by_id": categories_by_id,
        "stage_four_complete": (
            set(registered_library) == set(POOL_COMPONENT_KEYS)
            and complete_components == set(registered_library)
        ),
    }


def get_registered_theme_abstractions(run_id: str) -> dict[str, dict[str, Any]]:
    """Return current valid stage-four outputs without requiring researcher approval."""
    category_libraries = get_registered_category_integrations(run_id)
    latest_results = _latest_results(run_id)
    result: dict[str, dict[str, Any]] = {}
    for component_key in POOL_COMPONENT_KEYS:
        category_library = category_libraries.get(component_key)
        if category_library is None:
            continue
        prompt = build_theme_abstraction_prompt(run_id, component_key)
        theme_result = latest_results.get(component_key)
        if not category_library["integrated_categories"]:
            output = (theme_result or {}).get("output_json") or {}
            result[component_key] = {
                "component_key": component_key,
                "stage_result_id": theme_result.get("stage_result_id") if theme_result else None,
                "version": theme_result.get("version") if theme_result else None,
                "input_hash": prompt["input_hash"],
                "source_phase_version": category_library["version"],
                "source_stage_result_id": category_library["stage_result_id"],
                "source_categories": [],
                "integrated_category_ids": [],
                "themes": output.get("themes", []),
                "categories_without_a_theme": output.get("categories_without_a_theme", []),
                "no_data": True,
            }
            continue
        if (
            theme_result is None
            or theme_result.get("status") not in {"ready_for_researcher_review", "approved"}
            or theme_result.get("approval_status") not in {"pending", "approved"}
            or not (theme_result.get("validation_report_json") or {}).get("valid")
            or theme_result.get("input_hash") != prompt["input_hash"]
        ):
            continue
        output = theme_result.get("output_json") or {}
        result[component_key] = {
            "component_key": component_key,
            "stage_result_id": theme_result["stage_result_id"],
            "version": theme_result["version"],
            "input_hash": theme_result["input_hash"],
            "source_phase_version": theme_result.get("source_phase_version"),
            "source_stage_result_id": output.get("source_stage_result_id"),
            "source_categories": category_library["integrated_categories"],
            "integrated_category_ids": [
                category["category_id"]
                for category in category_library["integrated_categories"]
            ],
            "themes": output.get("themes", []),
            "categories_without_a_theme": output.get("categories_without_a_theme", []),
            "no_data": False,
        }
    return result


def get_approved_theme_abstractions(run_id: str) -> dict[str, dict[str, Any]]:
    approved_results = _latest_approved_results(run_id)
    approved_categories = get_approved_category_integrations(run_id)
    result: dict[str, dict[str, Any]] = {}
    for component_key in POOL_COMPONENT_KEYS:
        theme_result = approved_results.get(component_key)
        category_library = approved_categories.get(component_key)
        if not theme_result or not category_library:
            continue
        prompt = build_theme_abstraction_prompt(run_id, component_key)
        if theme_result.get("input_hash") != prompt["input_hash"]:
            continue
        output = theme_result.get("output_json") or {}
        themes = output.get("themes", [])
        unthemed = output.get("categories_without_a_theme", [])
        if not all(item.get("review_status") == "approved" for item in [*themes, *unthemed]):
            continue
        result[component_key] = {
            "component_key": component_key,
            "stage_result_id": theme_result["stage_result_id"],
            "version": theme_result["version"],
            "input_hash": theme_result["input_hash"],
            "source_phase_version": theme_result.get("source_phase_version"),
            "source_stage_result_id": output.get("source_stage_result_id"),
            "source_categories": category_library["integrated_categories"],
            "integrated_category_ids": [
                category["category_id"]
                for category in category_library["integrated_categories"]
            ],
            "themes": themes,
            "categories_without_a_theme": unthemed,
        }
    return result
