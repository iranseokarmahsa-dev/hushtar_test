from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.repositories.synthesis_repository import (
    get_next_stage_version,
    list_stage_results,
    mark_stage_results_stale,
    save_stage_result,
)
from app.services.synthesis.category_grouping_service import _approved_pool
from app.services.synthesis.category_integration_markdown_parser import (
    CategoryIntegrationMarkdownError,
    parse_category_integration_markdown,
)
from app.services.synthesis.normalization_service import stable_hash
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS

STAGE_KEY = "category_integration"
DEPENDENT_STAGE_KEY = "theme_abstraction"
PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "synthesis" / "category_integration.txt"
COMPONENT_LABELS = {
    "causal_conditions": "شرایط علّی",
    "contextual_conditions": "شرایط زمینه‌ای",
    "intervening_conditions": "شرایط مداخله‌گر",
    "action_interaction_strategies": "راهبردهای کنش و تعامل",
    "consequences": "پیامدها",
}


def _text_block(value: Any, *, indentation: int = 2) -> str:
    text = "" if value is None else str(value)
    prefix = " " * indentation
    if not text:
        return f"{prefix}NONE"
    fence_length = max(3, max((len(run) for run in re.findall(r"~+", text)), default=0) + 1)
    fence = "~" * fence_length
    content = "\n".join(f"{prefix}{line}" for line in text.splitlines())
    return f"{prefix}{fence}text\n{content}\n{prefix}{fence}"


def _render_source_item(
    item: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
) -> str:
    lines = [
        f"### SOURCE ITEM: {item['source_item_id']}",
        f"- source_item_id: {item['source_item_id']}",
        "- interview:",
        _text_block(item.get("source_title") or item.get("source_interview_id") or "مصاحبه"),
        "- initial_category:",
        _text_block(
            item.get("researcher_approved_category_name")
            or item.get("original_category_name")
            or ""
        ),
        "- definition:",
        _text_block(item.get("definition") or ""),
        "- concepts:",
    ]
    concepts = item.get("source_concepts") or []
    if concepts:
        for concept in concepts:
            lines.extend((
                f"  - concept_id: {concept.get('concept_id') or 'NONE'}",
                "    - text:",
                _text_block(concept.get("text") or "", indentation=6),
            ))
    else:
        lines.append("  - NONE")
    lines.append("- evidence:")
    references = item.get("evidence_refs") or []
    if references:
        for reference in references:
            evidence_id = str(reference.get("evidence_ref_id") or "")
            evidence = evidence_by_id.get(evidence_id, {})
            lines.extend((
                f"  - evidence_ref_id: {evidence_id}",
                "    - quote:",
                _text_block(evidence.get("quote") or "", indentation=6),
                "    - interpretation:",
                _text_block(evidence.get("interpretation") or "", indentation=6),
            ))
    else:
        lines.append("  - NONE")
    return "\n".join(lines)


def _component_input(
    pool: dict[str, Any],
    component_key: str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if component_key not in POOL_COMPONENT_KEYS:
        raise ValueError("مؤلفهٔ درخواستی معتبر نیست.")
    evidence_by_id = {
        str(item["evidence_ref_id"]): item
        for item in pool.get("evidence", [])
        if item.get("evidence_ref_id")
    }
    source_items = [
        item
        for item in pool.get("pools", {}).get(component_key, [])
        if isinstance(item, dict) and item.get("source_item_id")
    ]
    return source_items, evidence_by_id


def build_category_integration_prompt(run_id: str, component_key: str) -> dict[str, Any]:
    pool, pool_version, source_count = _approved_pool(run_id, allow_partial_pool=True)
    source_items, evidence_by_id = _component_input(pool, component_key)
    source_markdown = "\n\n".join(
        _render_source_item(item, evidence_by_id) for item in source_items
    ) or "NONE"
    template = PROMPT_PATH.read_text(encoding="utf-8")
    input_hash = stable_hash({
        "run_id": run_id,
        "component_key": component_key,
        "pool_version": pool_version,
        "pool_input_hash": pool.get("input_hash"),
        "source_items": source_items,
        "prompt_template": template,
    })
    substitutions = {
        "COMPONENT_KEY": component_key,
        "COMPONENT_LABEL": COMPONENT_LABELS[component_key],
        "RUN_ID": run_id,
        "INPUT_HASH": input_hash,
        "SOURCE_ITEMS_MARKDOWN": source_markdown,
    }
    prompt_text = re.sub(
        r"\{\{(COMPONENT_KEY|COMPONENT_LABEL|RUN_ID|INPUT_HASH|SOURCE_ITEMS_MARKDOWN)\}\}",
        lambda match: substitutions[match.group(1)],
        template,
    )
    return {
        "run_id": run_id,
        "stage_key": STAGE_KEY,
        "component_key": component_key,
        "component_label": COMPONENT_LABELS[component_key],
        "input_hash": input_hash,
        "pool_version": pool_version,
        "source_count": source_count,
        "source_items": source_items,
        "evidence_by_id": evidence_by_id,
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


def validate_category_integration_result(
    parsed: dict[str, Any],
    *,
    run_id: str,
    component_key: str,
    input_hash: str,
    source_items: list[dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    if set(metadata) != {"run_id", "component_key", "input_hash"}:
        errors.append("فیلدهای METADATA باید دقیقاً run_id، component_key و input_hash باشند.")
    for field, expected in {
        "run_id": run_id,
        "component_key": component_key,
        "input_hash": input_hash,
    }.items():
        if str(metadata.get(field) or "").strip() != expected:
            errors.append(f"فرادادهٔ {field} با اجرای جاری مطابقت ندارد.")

    expected_ids = [str(item["source_item_id"]) for item in source_items]
    expected_set = set(expected_ids)
    categories = parsed.get("integrated_categories")
    if not isinstance(categories, list):
        categories = []
        errors.append("بخش INTEGRATED CATEGORIES باید فهرست مقوله‌ها باشد.")
    if len(expected_ids) != len(expected_set):
        errors.append("شناسهٔ Source Item در ورودی جاری تکراری است.")
    if not expected_ids and categories:
        errors.append("برای ورودی خالی نباید مقوله‌ای ساخته شود.")

    category_ids: set[str] = set()
    seen_item_ids: list[str] = []
    for index, category in enumerate(categories, start=1):
        if not isinstance(category, dict):
            errors.append(f"ساختار مقولهٔ شمارهٔ {index} معتبر نیست.")
            continue
        category_id = str(category.get("category_id") or "").strip()
        expected_category_id = f"cat_{index}"
        if category_id != expected_category_id:
            errors.append(
                f"شناسهٔ مقولهٔ شمارهٔ {index} باید `{expected_category_id}` باشد."
            )
        if category_id in category_ids:
            errors.append(f"شناسهٔ مقولهٔ تکراری است: {category_id}")
        category_ids.add(category_id)
        if not str(category.get("title") or "").strip():
            errors.append(f"عنوان مقولهٔ شمارهٔ {index} خالی است.")
        if not str(category.get("definition") or "").strip():
            errors.append(f"تعریف مقولهٔ شمارهٔ {index} خالی است.")
        item_ids = category.get("source_item_ids")
        if not isinstance(item_ids, list) or not item_ids:
            errors.append(f"مقولهٔ شمارهٔ {index} باید دست‌کم یک Source Item داشته باشد.")
            continue
        seen_item_ids.extend(str(item_id) for item_id in item_ids)

    counts: dict[str, int] = {}
    unknown_ids = []
    for item_id in seen_item_ids:
        if item_id not in expected_set:
            unknown_ids.append(item_id)
        counts[item_id] = counts.get(item_id, 0) + 1
    duplicate_count = sum(max(count - 1, 0) for item_id, count in counts.items() if item_id in expected_set)
    duplicate_count += sum(max(count - 1, 0) for item_id, count in counts.items() if item_id not in expected_set)
    missing_count = len(expected_set - set(counts))
    unknown_count = len(unknown_ids)
    if missing_count:
        errors.append(f"{missing_count} Source Item از خروجی جا افتاده است.")
    if unknown_count:
        errors.append(f"{unknown_count} ارجاع به Source Item ناشناخته وجود دارد.")

    quality = parsed.get("quality_audit") if isinstance(parsed.get("quality_audit"), dict) else {}
    quality_values = {
        "total_input_items": len(expected_ids),
        "total_assigned_items": len(seen_item_ids),
        "duplicate_source_items": duplicate_count,
        "missing_source_items": missing_count,
        "unknown_source_items": unknown_count,
    }
    for field, expected in quality_values.items():
        actual = _as_count(quality.get(field), field, errors)
        if actual is not None and actual != expected:
            errors.append(f"شمارندهٔ `{field}` با دادهٔ واقعی مطابقت ندارد (واقعی: {expected}).")
    quality_status = str(quality.get("status") or "").strip().upper()
    expected_status = "PASS" if not (missing_count or unknown_count) else "FAIL"
    if quality_status not in {"PASS", "FAIL"}:
        errors.append("مقدار status در QUALITY AUDIT باید PASS یا FAIL باشد.")
    elif quality_status != expected_status:
        errors.append(f"status در QUALITY AUDIT باید {expected_status} باشد.")
    warnings = quality.get("warnings") if isinstance(quality.get("warnings"), list) else []
    warnings = [str(item).strip() for item in warnings if str(item).strip()]
    warnings = [item for item in warnings if item.upper() != "NONE"]
    if duplicate_count:
        warnings.append(
            f"{duplicate_count} ارجاع تکراری به Source Item ثبت شد؛ این مورد مانع پذیرش خروجی نیست."
        )
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checked_source_item_count": len(expected_ids),
        "assigned_source_item_count": len(seen_item_ids),
        "duplicate_source_item_count": duplicate_count,
        "missing_source_item_count": missing_count,
        "unknown_source_item_count": unknown_count,
    }


def _latest_results(run_id: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for result in list_stage_results(run_id):
        if result["stage_key"] != STAGE_KEY or result["component_key"] not in POOL_COMPONENT_KEYS:
            continue
        component_key = result["component_key"]
        if component_key not in latest or result["version"] > latest[component_key]["version"]:
            latest[component_key] = result
    return latest


def _latest_approved_results(run_id: str) -> dict[str, dict[str, Any]]:
    approved: dict[str, dict[str, Any]] = {}
    for result in list_stage_results(run_id):
        if (
            result["stage_key"] != STAGE_KEY
            or result["component_key"] not in POOL_COMPONENT_KEYS
            or result.get("status") != "approved"
            or result.get("approval_status") != "approved"
            or not any(
                decision.get("operation") == "approve_final"
                for decision in (result.get("researcher_edits_json") or {}).get("decisions", [])
                if isinstance(decision, dict)
            )
        ):
            continue
        component_key = result["component_key"]
        if component_key not in approved or result["version"] > approved[component_key]["version"]:
            approved[component_key] = result
    return approved


def _current_hash(run_id: str, component_key: str) -> tuple[str, int]:
    prompt = build_category_integration_prompt(run_id, component_key)
    return prompt["input_hash"], prompt["pool_version"]


def _validation_text(report: dict[str, Any]) -> str:
    lines = ["خروجی Markdown: " + ("معتبر" if report.get("valid") else "نامعتبر")]
    lines.extend(f"- خطا: {error}" for error in report.get("errors", []))
    lines.extend(f"- هشدار: {warning}" for warning in report.get("warnings", []))
    return "\n".join(lines)


def submit_category_integration_output(
    run_id: str,
    component_key: str,
    raw_output_text: str,
) -> dict[str, Any]:
    prompt = build_category_integration_prompt(run_id, component_key)
    parsed = None
    try:
        parsed = parse_category_integration_markdown(raw_output_text)
        report = validate_category_integration_result(
            parsed,
            run_id=run_id,
            component_key=component_key,
            input_hash=prompt["input_hash"],
            source_items=prompt["source_items"],
        )
    except CategoryIntegrationMarkdownError as error:
        report = {
            "valid": False,
            "errors": error.errors,
            "warnings": [],
            "checked_source_item_count": len(prompt["source_items"]),
            "assigned_source_item_count": 0,
            "duplicate_source_item_count": 0,
            "missing_source_item_count": len(prompt["source_items"]),
            "unknown_source_item_count": 0,
        }
    if parsed is not None and report["valid"]:
        for category in parsed["integrated_categories"]:
            category["review_status"] = "pending"
    version = get_next_stage_version(run_id, STAGE_KEY, component_key)
    saved = save_stage_result(
        run_id,
        STAGE_KEY,
        component_key,
        version=version,
        status="ready_for_researcher_review" if report["valid"] else "invalid",
        input_hash=prompt["input_hash"],
        prompt_text=prompt["prompt_text"],
        raw_output_text=raw_output_text,
        output_json=parsed if parsed is not None else {},
        validation_report_json=report,
        validation_report_text=_validation_text(report),
        source_phase_version=prompt["pool_version"],
        researcher_edits_json={"decisions": []},
        approval_status="pending",
    )
    return saved


def category_integration_page_data(run_id: str, component_key: str) -> dict[str, Any]:
    if component_key not in POOL_COMPONENT_KEYS:
        raise ValueError("مؤلفهٔ درخواستی معتبر نیست.")
    prompt = build_category_integration_prompt(run_id, component_key)
    latest_results = _latest_results(run_id)
    approved_results = _latest_approved_results(run_id)
    latest = latest_results.get(component_key)
    history = [
        result
        for result in list_stage_results(run_id)
        if result["stage_key"] == STAGE_KEY and result["component_key"] == component_key
    ]
    latest_hash = prompt["input_hash"]
    if latest:
        latest["stale"] = (
            latest.get("status") == "stale"
            or latest.get("approval_status") == "stale"
            or latest.get("input_hash") != latest_hash
        )
    component_rows = []
    for key in POOL_COMPONENT_KEYS:
        item = latest_results.get(key)
        try:
            current_hash, _ = _current_hash(run_id, key)
        except ValueError:
            current_hash = ""
        stale = bool(
            item
            and (
                item.get("status") == "stale"
                or item.get("approval_status") == "stale"
                or item.get("input_hash") != current_hash
            )
        )
        approved_result = approved_results.get(key)
        if approved_result:
            try:
                approved_hash, _ = _current_hash(run_id, key)
            except ValueError:
                approved_hash = ""
            if approved_result.get("input_hash") != approved_hash:
                approved_result = None
        component_rows.append({
            "key": key,
            "label": COMPONENT_LABELS[key],
            "latest": item,
            "approved_result": approved_result,
            "stale": stale,
            "approved": approved_result is not None,
        })
    return {
        "run_id": run_id,
        "components": component_rows,
        "active_component_key": component_key,
        "active_prompt": prompt,
        "latest": latest,
        "history": history,
        "source_items_by_id": {
            str(item["source_item_id"]): item for item in prompt["source_items"]
        },
        "evidence_by_id": prompt["evidence_by_id"],
    }


def get_approved_category_integrations(run_id: str) -> dict[str, dict[str, Any]]:
    approved_results = _latest_approved_results(run_id)
    approved: dict[str, dict[str, Any]] = {}
    for component_key in POOL_COMPONENT_KEYS:
        result = approved_results.get(component_key)
        if not result:
            continue
        current_hash, _ = _current_hash(run_id, component_key)
        if result.get("input_hash") != current_hash:
            continue
        categories = result.get("output_json", {}).get("integrated_categories", [])
        if not all(item.get("review_status") == "approved" for item in categories):
            continue
        approved[component_key] = {
            "component_key": component_key,
            "stage_result_id": result["stage_result_id"],
            "version": result["version"],
            "input_hash": result["input_hash"],
            "source_phase_version": result.get("source_phase_version"),
            "integrated_categories": categories,
        }
    return approved


def get_registered_category_integrations(run_id: str) -> dict[str, dict[str, Any]]:
    registered: dict[str, dict[str, Any]] = {}
    latest_results = _latest_results(run_id)
    for component_key in POOL_COMPONENT_KEYS:
        result = latest_results.get(component_key)
        if (
            not result
            or result.get("status") not in {"ready_for_researcher_review", "approved"}
            or result.get("approval_status") not in {"pending", "approved"}
            or not (result.get("validation_report_json") or {}).get("valid")
        ):
            continue
        try:
            current_hash, _ = _current_hash(run_id, component_key)
        except ValueError:
            continue
        if result.get("input_hash") != current_hash:
            continue
        categories = (result.get("output_json") or {}).get("integrated_categories")
        if not isinstance(categories, list):
            continue
        registered[component_key] = {
            "component_key": component_key,
            "stage_result_id": result["stage_result_id"],
            "version": result["version"],
            "input_hash": result["input_hash"],
            "source_phase_version": result.get("source_phase_version"),
            "integrated_categories": categories,
        }
    return registered
