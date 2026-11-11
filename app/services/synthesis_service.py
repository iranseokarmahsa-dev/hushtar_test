"""Selection and validation logic for multi-interview synthesis runs."""

from __future__ import annotations

from typing import Any

from app.core.database import get_project
from app.repositories.synthesis_repository import create_synthesis_run, save_run_sources


def _eligibility_failure(
    project: dict[str, Any], reason: str, review_path: str, review_label: str
) -> dict[str, Any]:
    return {
        "eligible": False,
        "status": "نامعتبر",
        "reason": reason,
        "review_url": review_path.format(project_id=project["project_id"]),
        "review_label": review_label,
    }


def summarize_project_eligibility(
    project: dict[str, Any], *, allow_incomplete: bool = False
) -> dict[str, Any]:
    result = _summarize_project_eligibility(project)
    if not allow_incomplete or project is None or result.get("eligible"):
        return result
    return {
        **result,
        "eligible": True,
        "status": "مجاز با هشدار",
        "reason": "این مصاحبه تحلیل یا بازبینی کاملی ندارد، اما می‌توانید آن را وارد اجرای تلفیقی کنید. "
        + result.get("reason", ""),
    }


def _summarize_project_eligibility(project: dict[str, Any]) -> dict[str, Any]:
    """Return structured eligibility and a Persian reason for each project."""
    if project is None:
        return {"eligible": False, "status": "ناموجود", "reason": "رکورد مصاحبه پیدا نشد."}

    if not project.get("paragraphs"):
        return _eligibility_failure(
            project, "متن مصاحبه ثبت نشده است.", "/analysis/{project_id}", "افزودن متن مصاحبه"
        )
    if not project.get("concepts"):
        return _eligibility_failure(
            project, "مفهومی برای بررسی ثبت نشده است.", "/analysis/{project_id}", "بررسی ورودی مصاحبه"
        )

    analysis = project.get("analysis")
    if not analysis:
        return _eligibility_failure(
            project, "تحلیل مصاحبه ثبت نشده است.", "/analysis/{project_id}#prompt-panel", "رفتن به دریافت و ورود تحلیل"
        )
    if analysis.get("schema_version") != "2.0":
        return _eligibility_failure(
            project, "خروجی تحلیل با قرارداد مرحله‌ای فعلی سازگار نیست.",
            "/analysis/{project_id}#prompt-panel", "بازبینی و ورود خروجی تحلیل"
        )

    interview_analysis = analysis.get("interview_analysis")
    if not isinstance(interview_analysis, dict):
        return _eligibility_failure(
            project, "تحلیل مستقل مصاحبه وجود ندارد.",
            "/analysis/{project_id}#stage-import-stage2", "ورود خروجی مرحله دوم"
        )

    quality = interview_analysis.get("quality_audit") or {}
    for flag_name in (
        "all_concepts_reviewed",
        "all_ids_preserved",
        "all_evidence_verifiable",
    ):
        if not quality.get(flag_name):
            review_path = f"/results/{project['project_id']}/interview-analysis?show_quality=1#quality-audit"
            review_label = "رفتن به کنترل کیفیت"
            if flag_name == "all_concepts_reviewed":
                reason = "بازبینی همه مفاهیم کامل نشده است."
                review_path = f"/results/{project['project_id']}/concept-review#concepts"
                review_label = "رفتن به مفاهیم نیازمند بازبینی"
            elif flag_name == "all_ids_preserved":
                reason = "شناسه‌ها یا ارجاع‌های تحلیل با ورودی مصاحبه تطبیق ندارند."
                review_path = f"/results/{project['project_id']}/interview-analysis?show_quality=1#quality-validation"
                review_label = "دیدن خطاهای ارجاع و ساختار"
            else:
                reason = "شواهد باید با متن اصلی مصاحبه تطبیق داده شوند."
            if flag_name == "all_evidence_verifiable":
                warnings = quality.get("evidence_warnings") or []
                warning_refs = [
                    f"{item.get('record_type')} {item.get('record_id')} در {item.get('paragraph_id')} ({item.get('status')})"
                    for item in warnings[:3]
                    if isinstance(item, dict)
                ]
                if warning_refs:
                    remaining = len(warnings) - len(warning_refs)
                    reason = "شواهد نیازمند بازبینی: " + "؛ ".join(warning_refs)
                    if remaining > 0:
                        reason += f"؛ و {remaining} مورد دیگر"
                    reason += "."
            return {
                "eligible": False,
                "status": "نامعتبر",
                "reason": reason,
                "review_url": review_path,
                "review_label": review_label,
            }
    for flag_name in (
        "unsupported_relationships_detected",
        "contradictions_detected",
    ):
        if quality.get(flag_name):
            reason = (
                "رابطه‌ای بدون پشتوانهٔ کافی ثبت شده است."
                if flag_name == "unsupported_relationships_detected"
                else "در طبقه‌بندی یا جای‌دهی مؤلفه‌ها تناقض پیدا شده است."
            )
            return _eligibility_failure(
                project, reason,
                f"/results/{project['project_id']}/interview-analysis?show_quality=1#quality-validation",
                "رفتن به خطاهای ممیزی",
            )

    concept_ids = [str(item.get("concept_id") or "") for item in project.get("concepts", [])]
    if not all(concept_ids) or len(concept_ids) != len(set(concept_ids)):
        return _eligibility_failure(
            project, "شناسهٔ مفاهیم ورودی خالی یا تکراری است.",
            f"/analysis/{project['project_id']}#stage-import-stage1", "بازبینی خروجی مرحله اول"
        )
    review_ids = [
        str(item.get("concept_id") or "")
        for item in analysis.get("concept_reviews", [])
        if isinstance(item, dict)
    ]
    if len(review_ids) != len(concept_ids) or set(review_ids) != set(concept_ids):
        return _eligibility_failure(
            project, "بازبینی همه مفاهیم ورودی ثبت نشده؛ خروجی مرحله اول را اصلاح و دوباره وارد کنید.",
            f"/analysis/{project['project_id']}#stage-import-stage1", "رفتن به ورود مرحله اول"
        )
    source_scope = analysis.get("source_scope") or {}
    if (
        source_scope.get("total_paragraphs") != len(project.get("paragraphs", []))
        or source_scope.get("total_concepts_received") != len(concept_ids)
        or source_scope.get("total_concepts_reviewed") != len(concept_ids)
    ):
        return _eligibility_failure(
            project, "شمار مفاهیم یا پاراگراف‌ها با ورودی مصاحبه مطابقت ندارد.",
            f"/analysis/{project['project_id']}#stage-import-stage1", "بازبینی ورودی و مرحله اول"
        )

    researcher_edits = project.get("researcher_edits") or {}
    interview_edit = researcher_edits.get("interview_analysis") or {}
    if not interview_edit.get("confirmed"):
        return _eligibility_failure(
            project, "تحلیل مصاحبه هنوز تأیید پژوهشگر نشده است.",
            f"/results/{project['project_id']}/interview-analysis#researcher-confirmation",
            "رفتن به تأیید تحلیل",
        )

    concept_reviews = researcher_edits.get("concept_reviews") or {}
    for concept_id in concept_ids:
        if not concept_reviews.get(concept_id, {}).get("confirmed"):
            return _eligibility_failure(
                project, f"مفهوم {concept_id} تأیید پژوهشگر ندارد.",
                f"/results/{project['project_id']}/concept-review#concept-{concept_id}",
                f"رفتن به بازبینی مفهوم {concept_id}",
            )

    categories = {item["category_id"] for item in interview_analysis.get("categories", []) if item.get("category_id")}
    for item in (interview_analysis.get("central_phenomenon") or {}).get("supporting_category_ids", []):
        if item not in categories:
            return _eligibility_failure(
                project, "ارجاع پدیده محوری به مقوله‌ای ناموجود اشاره می‌کند.",
                f"/results/{project['project_id']}/interview-analysis?show_quality=1#quality-validation",
                "رفتن به خطاهای ارجاع",
            )

    paradigm_model = interview_analysis.get("paradigm_model") or {}
    for component_key, value in paradigm_model.items():
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            category_id = item.get("category_id")
            if category_id and category_id not in categories:
                return _eligibility_failure(
                    project, f"ارجاع پارادایمی به مقوله {category_id} ناموجود است.",
                    f"/results/{project['project_id']}/interview-analysis?show_quality=1#quality-validation",
                    "رفتن به خطاهای ارجاع",
                )

    deferred_single_interview_questions = (
        analysis.get("analysis_status") != "complete"
        or quality.get("final_status") != "complete"
        or quality.get("missing_data_detected") is True
    )
    if deferred_single_interview_questions:
        if not interview_analysis.get("central_phenomenon"):
            reason = (
                "پدیده محوری این مصاحبه جداگانه تعیین نشده و در فاز ششم تحلیل تلفیقی بررسی می‌شود؛ "
                "پوشش مفاهیم، شناسه‌ها و شواهد تأیید شده است."
            )
        else:
            reason = (
                "پوشش مفاهیم، شناسه‌ها و شواهد تأیید شده است. تحلیل تک‌مصاحبه‌ای ناتمام است؛ "
                "ابهام‌های باقی‌مانده در مراحل تلفیقی بررسی می‌شوند."
            )
        return {
            "eligible": True,
            "status": "مجاز با هشدار",
            "reason": reason,
            "review_url": f"/results/{project['project_id']}/interview-analysis?show_quality=1#quality-audit",
            "review_label": "مشاهدهٔ جزئیات تحلیل ناتمام",
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


def _shared_topic_or_error(selected_projects: list[dict[str, Any]]) -> bool:
    topics = {str(project.get("topic") or "").strip() for project in selected_projects}
    return len(topics) > 1


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

    eligibility_warnings = [
        {
            "project_id": project["project_id"],
            "title": project.get("title", "مصاحبه نامشخص"),
            "reason": summarize_project_eligibility(project, allow_incomplete=True)["reason"],
        }
        for project in selected_projects
        if summarize_project_eligibility(project, allow_incomplete=True)["status"] == "مجاز با هشدار"
    ]

    topics_differ = _shared_topic_or_error(selected_projects)
    if topics_differ and not confirm_shared_topic:
        raise ValueError(
            "موضوع مصاحبه‌های انتخاب‌شده متفاوت است. اگر با وجود این تفاوت قصد ادامه دارید، "
            "تأیید آگاهانهٔ تفاوت موضوع را علامت بزنید."
        )

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
        "eligibility_warnings": eligibility_warnings,
        "sources": sources,
    }
