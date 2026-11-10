"""Selection and validation logic for multi-interview synthesis runs."""

from __future__ import annotations

from typing import Any

from app.core.database import get_project
from app.repositories.synthesis_repository import create_synthesis_run, save_run_sources


def summarize_project_eligibility(project: dict[str, Any]) -> dict[str, Any]:
    """Return structured eligibility and a Persian reason for each project."""
    if project is None:
        return {"eligible": False, "status": "ناموجود", "reason": "رکورد مصاحبه پیدا نشد."}

    if not project.get("paragraphs"):
        return {"eligible": False, "status": "نامعتبر", "reason": "paragraphs خالی است."}
    if not project.get("concepts"):
        return {"eligible": False, "status": "نامعتبر", "reason": "concepts خالی است."}

    analysis = project.get("analysis")
    if not analysis:
        return {"eligible": False, "status": "نامعتبر", "reason": "تحلیل ثبت نشده است."}
    if analysis.get("schema_version") != "2.0":
        return {"eligible": False, "status": "نامعتبر", "reason": "schema_version برابر 2.0 نیست."}

    interview_analysis = analysis.get("interview_analysis")
    if not isinstance(interview_analysis, dict):
        return {"eligible": False, "status": "نامعتبر", "reason": "interview_analysis وجود ندارد."}

    quality = interview_analysis.get("quality_audit") or {}
    for flag_name in (
        "all_concepts_reviewed",
        "all_ids_preserved",
        "all_evidence_verifiable",
    ):
        if not quality.get(flag_name):
            return {"eligible": False, "status": "نامعتبر", "reason": f"{flag_name} برابر true نیست."}
    for flag_name in (
        "unsupported_relationships_detected",
        "contradictions_detected",
    ):
        if quality.get(flag_name):
            return {"eligible": False, "status": "نامعتبر", "reason": f"{flag_name} برابر false نیست."}

    concept_ids = [str(item.get("concept_id") or "") for item in project.get("concepts", [])]
    if not all(concept_ids) or len(concept_ids) != len(set(concept_ids)):
        return {"eligible": False, "status": "نامعتبر", "reason": "شناسه مفاهیم ورودی خالی یا تکراری است."}
    review_ids = [
        str(item.get("concept_id") or "")
        for item in analysis.get("concept_reviews", [])
        if isinstance(item, dict)
    ]
    if len(review_ids) != len(concept_ids) or set(review_ids) != set(concept_ids):
        return {
            "eligible": False,
            "status": "نامعتبر",
            "reason": "بازبینی تحلیل برای همه مفاهیم ورودی ثبت نشده است؛ خروجی مرحله اول را اصلاح و دوباره وارد کنید.",
        }
    source_scope = analysis.get("source_scope") or {}
    if (
        source_scope.get("total_paragraphs") != len(project.get("paragraphs", []))
        or source_scope.get("total_concepts_received") != len(concept_ids)
        or source_scope.get("total_concepts_reviewed") != len(concept_ids)
    ):
        return {
            "eligible": False,
            "status": "نامعتبر",
            "reason": "شمارنده پوشش پاراگراف‌ها یا مفاهیم با ورودی مصاحبه مطابقت ندارد.",
        }

    researcher_edits = project.get("researcher_edits") or {}
    interview_edit = researcher_edits.get("interview_analysis") or {}
    if not interview_edit.get("confirmed"):
        return {"eligible": False, "status": "نامعتبر", "reason": "researcher_edits.interview_analysis.confirmed برابر true نیست."}

    concept_reviews = researcher_edits.get("concept_reviews") or {}
    for concept_id in concept_ids:
        if not concept_reviews.get(concept_id, {}).get("confirmed"):
            return {"eligible": False, "status": "نامعتبر", "reason": f"concept_id {concept_id} تأیید پژوهشگر ندارد."}

    categories = {item["category_id"] for item in interview_analysis.get("categories", []) if item.get("category_id")}
    for item in (interview_analysis.get("central_phenomenon") or {}).get("supporting_category_ids", []):
        if item not in categories:
            return {"eligible": False, "status": "نامعتبر", "reason": "ارجاع پدیده محوری به مقوله ناموجود اشاره می‌کند."}

    paradigm_model = interview_analysis.get("paradigm_model") or {}
    for component_key, value in paradigm_model.items():
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            category_id = item.get("category_id")
            if category_id and category_id not in categories:
                return {"eligible": False, "status": "نامعتبر", "reason": f"ارجاع پارادایمی به مقوله {category_id} ناموجود است."}

    deferred_single_interview_questions = (
        analysis.get("analysis_status") != "complete"
        or quality.get("final_status") != "complete"
        or quality.get("missing_data_detected") is True
    )
    if deferred_single_interview_questions:
        return {
            "eligible": True,
            "status": "مجاز با هشدار",
            "reason": (
                "پوشش مفاهیم، شناسه‌ها و شواهد تأیید شده است. ابهام نظریِ تحلیل یک مصاحبه "
                "مانع تلفیق نیست و در مقایسهٔ بین‌مصاحبه‌ای بررسی می‌شود."
            ),
        }
    return {"eligible": True, "status": "مجاز", "reason": "مصاحبه برای تحلیل تلفیقی آماده است."}


def parse_selected_ids(selected_ids: Any, *, deduplicate: bool = True) -> list[str]:
    if selected_ids is None:
        return []
    if isinstance(selected_ids, str):
        items = selected_ids.split(",")
    elif isinstance(selected_ids, (list, tuple, set)):
        items = list(selected_ids)
    else:
        items = [selected_ids]
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item is None:
            continue
        candidates = str(item).split(",") if isinstance(item, str) else [item]
        for candidate in candidates:
            value = str(candidate).strip()
            if not value:
                continue
            if deduplicate:
                if value in seen:
                    continue
                seen.add(value)
            cleaned.append(value)
    return cleaned


def _shared_topic_or_error(selected_projects: list[dict[str, Any]]) -> None:
    topics = {str(project.get("topic") or "").strip() for project in selected_projects}
    if len(topics) > 1:
        raise ValueError("تفاوت موضوع‌های مصاحبه‌های انتخاب‌شده وجود دارد. برای ادامه باید تأیید صریح کاربر را بدهید.")


def create_synthesis_run_from_selection(
    *,
    title: str,
    study_topic: str,
    selected_ids: Any,
    confirm_shared_topic: bool,
) -> dict[str, Any]:
    raw_ids = parse_selected_ids(selected_ids, deduplicate=False)
    if len(raw_ids) != len(set(raw_ids)):
        raise ValueError("شناسه‌های انتخاب‌شده نباید تکراری باشند.")
    normalized_ids = parse_selected_ids(selected_ids)
    if not title.strip():
        raise ValueError("عنوان تحلیل تلفیقی الزامی است.")
    if len(normalized_ids) < 2:
        raise ValueError("حداقل دو مصاحبه برای ایجاد Run لازم است.")

    selected_projects: list[dict[str, Any]] = []
    for project_id in normalized_ids:
        project = get_project(project_id)
        if project is None:
            raise ValueError(f"مصاحبه با شناسه {project_id} پیدا نشد.")
        selected_projects.append(project)

    for project in selected_projects:
        eligibility = summarize_project_eligibility(project)
        if not eligibility["eligible"]:
            raise ValueError(f"مصاحبه '{project.get('title','نامشخص')}' نامعتبر است: {eligibility['reason']}")

    _shared_topic_or_error(selected_projects)
    if not confirm_shared_topic:
        raise ValueError("تفاوت موضوع‌های مصاحبه‌های انتخاب‌شده وجود دارد. برای ادامه باید تأیید صریح کاربر را بدهید.")

    run = create_synthesis_run(
        title=title.strip(),
        study_topic=study_topic.strip(),
        expected_source_count=len(normalized_ids),
    )
    sources = save_run_sources(run["run_id"], normalized_ids)
    return {
        "run_id": run["run_id"],
        "title": run["title"],
        "study_topic": run["study_topic"],
        "status": run["status"],
        "current_stage": run["current_stage"],
        "schema_version": run["schema_version"],
        "selected_source_count": len(sources),
        "expected_source_count": len(normalized_ids),
        "sources": sources,
    }
