"""Interview-level axial coding state and quality audit.

This module only adds a stability layer over the saved interview analysis.  AI
output remains in ``project["analysis"]`` and structural decisions remain in
``project["researcher_edits"]["axial_coding"]``.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


REVIEW_STATUSES = ("بررسی‌نشده", "در حال بررسی", "تأیید", "اصلاح و تأیید", "رد", "نامشخص")
VALIDITY_STATUSES = ("معتبر", "ناقص", "دارای تعارض", "نیازمند بازبینی", "نامعتبر از نظر ساختاری")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def axial_state(project: dict[str, Any]) -> dict[str, Any]:
    edits = project.get("researcher_edits") or {}
    return edits.setdefault("axial_coding", {
        "version": 1,
        "entities": {},
        "category_overrides": {},
        "subcategory_overrides": {},
        "property_overrides": {},
        "dimension_overrides": {},
        "concept_assignments": {},
        "placement_decisions": {},
        "relationship_decisions": {},
        "phenomenon_decision": {},
        "change_history": [],
        "quality_resolution": {},
    })


def effective_categories(project: dict[str, Any]) -> list[dict[str, Any]]:
    analysis = (project.get("analysis") or {}).get("interview_analysis") or {}
    categories = deepcopy(analysis.get("categories") or [])
    state = axial_state(project)
    overrides = state.get("category_overrides") or {}
    assignments = state.get("concept_assignments") or {}
    deletions = state.get("logical_deletions") or {}
    subcategory_assignments = state.get("subcategory_assignments") or {}

    for category in categories:
        category_id = category["category_id"]
        if deletions.get(category_id, {}).get("status") == "deleted":
            category["existence_status"] = "deleted"
        category.update(overrides.get(category_id, {}))
        for subcategory in category.get("subcategories") or []:
            subcategory_key = f"{category_id}:{subcategory['subcategory_id']}"
            suboverride = (state.get("subcategory_overrides") or {}).get(subcategory_key, {})
            if suboverride:
                subcategory["subcategory_name"] = suboverride.get("title", subcategory.get("subcategory_name", ""))
                subcategory["definition"] = suboverride.get("definition", subcategory.get("definition", ""))
        category["subcategories"] = [
            subcategory for subcategory in (category.get("subcategories") or [])
            if not deletions.get(subcategory.get("subcategory_id"), {}).get("status") == "deleted"
        ]
        for property_item in category.get("properties") or []:
            property_key = f"{category_id}:{property_item.get('property_id', '')}"
            propoverride = (state.get("property_overrides") or {}).get(property_key, {})
            if propoverride:
                property_item["title"] = propoverride.get("title", property_item.get("title", ""))
                property_item["definition"] = propoverride.get("definition", property_item.get("definition", ""))
        category["properties"] = [
            prop for prop in (category.get("properties") or [])
            if not deletions.get(prop.get("property_id"), {}).get("status") == "deleted"
        ]
        for dimension_item in category.get("dimensions") or []:
            dimension_id = dimension_item.get("dimension_id", dimension_item.get("property_id", ""))
            dimension_key = f"{category_id}:{dimension_id}"
            dimension_override = (state.get("dimension_overrides") or {}).get(dimension_key, {})
            if dimension_override:
                dimension_item["title"] = dimension_override.get("title", dimension_item.get("title", ""))
                dimension_item["range"] = dimension_override.get("range", dimension_item.get("dimension_range", ""))
        category["dimensions"] = [
            dimension for dimension in (category.get("dimensions") or [])
            if not deletions.get(
                dimension.get("dimension_id", dimension.get("property_id")),
                {},
            ).get("status") == "deleted"
        ]

    categories = [
        item for item in categories
        if item.get("existence_status") not in {"merged", "deleted"}
    ]

    for concept_id, assignment in assignments.items():
        source_id = assignment.get("previous_category_id")
        target_id = assignment.get("category_id")
        for category in categories:
            category_id = category["category_id"]
            concept_ids = list(category.get("concept_ids") or [])
            if source_id == category_id:
                concept_ids = [item for item in concept_ids if item != concept_id]
                category["concept_ids"] = concept_ids
            if target_id == category_id and concept_id not in concept_ids:
                category.setdefault("concept_ids", []).append(concept_id)

    for split_id, split in (state.get("splits") or {}).items():
        categories.append({
            "category_id": split_id,
            "category_name": split["title"],
            "definition": split["definition"],
            "concept_ids": split["concept_ids"],
            "subcategories": [],
            "properties": [],
            "dimensions": [],
            "existence_status": "split",
            "source_category_id": split["source_category_id"],
        })

    for subcategory_id, assignment in subcategory_assignments.items():
        source_id = assignment.get("previous_category_id")
        target_id = assignment.get("category_id")
        moved = None
        for category in categories:
            if category["category_id"] == source_id:
                subcategories = [
                    item for item in (category.get("subcategories") or [])
                    if item.get("subcategory_id") != subcategory_id
                ]
                original = next(
                    (item for item in (category.get("subcategories") or []) if item.get("subcategory_id") == subcategory_id),
                    None,
                )
                category["subcategories"] = subcategories
                moved = original
        if moved is not None:
            target = next((item for item in categories if item["category_id"] == target_id), None)
            if target is not None and moved not in (target.get("subcategories") or []):
                target.setdefault("subcategories", []).append(moved)

    for category_id, created in (overrides or {}).items():
        if created.get("existence_status") == "created" and not deletions.get(category_id, {}).get("status") == "deleted":
            categories.append(deepcopy(created))

    for subcategory_id, created in (state.get("created_subcategories") or {}).items():
        if deletions.get(subcategory_id, {}).get("status") == "deleted":
            continue
        parent = next((item for item in categories if item["category_id"] == created["category_id"]), None)
        if parent is not None:
            parent.setdefault("subcategories", []).append(deepcopy(created))

    for property_id, created in (state.get("created_properties") or {}).items():
        if deletions.get(property_id, {}).get("status") == "deleted":
            continue
        parent = next((item for item in categories if item["category_id"] == created["category_id"]), None)
        if parent is not None:
            parent.setdefault("properties", []).append(deepcopy(created))

    for dimension_id, created in (state.get("created_dimensions") or {}).items():
        if deletions.get(dimension_id, {}).get("status") == "deleted":
            continue
        parent = next((item for item in categories if item["category_id"] == created["category_id"]), None)
        if parent is not None:
            parent.setdefault("dimensions", []).append({
                "dimension_id": dimension_id,
                "title": created["title"],
                "dimension_range": created["range"],
            })

    return categories


def record_change(
    state: dict[str, Any],
    *,
    record_type: str,
    record_id: str,
    field: str,
    old_value: Any,
    new_value: Any,
    reason: str = "",
    factor: str = "پژوهشگر",
) -> None:
    if old_value == new_value:
        return
    state.setdefault("change_history", []).append({
        "history_id": uuid4().hex,
        "record_type": record_type,
        "record_id": record_id,
        "field": field,
        "old_value": deepcopy(old_value),
        "new_value": deepcopy(new_value),
        "reason": reason.strip(),
        "actor": factor,
        "changed_at": _now(),
    })


def set_three_axis_status(
    state: dict[str, Any],
    entity_type: str,
    entity_id: str,
    *,
    ai_status: str | None = None,
    researcher_status: str | None = None,
    validity_status: str | None = None,
) -> dict[str, Any]:
    entities = state.setdefault("entities", {})
    entity = entities.setdefault(entity_id, {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "ai_status": "تولید شده",
        "researcher_status": "بررسی‌نشده",
        "validity_status": "معتبر",
    })
    if ai_status is not None:
        entity["ai_status"] = ai_status
    if researcher_status is not None:
        entity["researcher_status"] = researcher_status
    if validity_status is not None:
        entity["validity_status"] = validity_status
    return entity


def move_concept(state: dict[str, Any], concept_id: str, source_id: str, target_id: str, reason: str) -> None:
    assignments = state.setdefault("concept_assignments", {})
    old = assignments.get(concept_id, {})
    previous_source = old.get("category_id", source_id)
    record_change(
        state,
        record_type="concept_assignment",
        record_id=concept_id,
        field="category_id",
        old_value=previous_source,
        new_value=target_id,
        reason=reason,
    )
    assignments[concept_id] = {
        "concept_id": concept_id,
        "previous_category_id": previous_source,
        "category_id": target_id,
        "reason": reason.strip(),
        "changed_at": _now(),
        "history": [
            *old.get("history", []),
            {"from": previous_source, "to": target_id, "reason": reason.strip(), "changed_at": _now()},
        ],
    }
    set_three_axis_status(state, "concept", concept_id, researcher_status="اصلاح و تأیید", validity_status="نیازمند بازبینی")


def run_quality_audit(project: dict[str, Any]) -> dict[str, Any]:
    analysis = (project.get("analysis") or {}).get("interview_analysis") or {}
    categories = effective_categories(project)
    state = axial_state(project)
    resolved = state.get("quality_resolution") or {}
    issues: list[dict[str, Any]] = []
    paragraph_ids = {
        item.get("paragraph_id")
        for item in (project.get("paragraphs") or [])
        if item.get("paragraph_id")
    }

    def add(issue_id: str, kind: str, location: str, description: str, severity: str, action: str) -> None:
        resolution = resolved.get(issue_id, {})
        issues.append({
            "issue_id": issue_id,
            "type": kind,
            "location": location,
            "description": description,
            "severity": severity,
            "suggested_action": action,
            "resolution_status": resolution.get("status", "باز"),
            "resolution_note": resolution.get("note", ""),
        })

    concepts = {item.get("concept_id") for item in (project.get("concepts") or []) if item.get("concept_id")}
    seen_concepts: set[str] = set()
    assigned: set[str] = set()
    for category in categories:
        category_id = category.get("category_id") or ""
        if not category_id:
            add("CAT-MISSING-ID", "شناسه نامعتبر", "مقوله", "مقوله‌ای بدون شناسه پیدا شد.", "high", "شناسه پایدار به مقوله اختصاص دهید.")
            continue
        if not category.get("concept_ids"):
            add(f"CAT-NO-CONCEPT-{category_id}", "مقوله فاقد مفهوم", category_id, "مقوله مفهوم پشتیبان ندارد.", "medium", "مفهوم یا شاهد مرتبط اضافه کنید.")
        if category.get("existence_status") not in {"created", "split"} and not category.get("evidence"):
            add(f"CAT-NO-SOURCE-{category_id}", "مقوله فاقد منشأ", category_id, "مقوله شاهد یا شناسه پاراگراف منشأ ندارد.", "high", "منشأ مقوله را بازبینی و شاهد معتبر اضافه کنید.")
        for evidence in category.get("evidence") or []:
            paragraph_id = evidence.get("paragraph_id")
            if paragraph_id and paragraph_id not in paragraph_ids:
                add(f"EVIDENCE-PARAGRAPH-{paragraph_id}", "شناسه پاراگراف نامعتبر", category_id, f"شاهد به پاراگراف {paragraph_id} اشاره دارد که در مصاحبه نیست.", "high", "شناسه پاراگراف یا متن شاهد را اصلاح کنید.")
        subcategories = category.get("subcategories") or []
        if subcategories:
            for subcategory in subcategories:
                if not subcategory.get("concept_ids"):
                    add(
                        f"SUBCAT-NO-CONCEPT-{subcategory.get('subcategory_id', 'UNKNOWN')}",
                        "زیرمقوله فاقد مفهوم",
                        subcategory.get("subcategory_id", "UNKNOWN"),
                        "زیرمقوله مفهومی ندارد.",
                        "medium",
                        "مفهوم مرتبط اضافه کنید.",
                    )
        properties = category.get("properties") or []
        dimensions = category.get("dimensions") or []
        if properties:
            for prop in properties:
                if not prop.get("title") and not prop.get("name"):
                    add(
                        f"PROPERTY-NOT-COMPLETE-{prop.get('property_id', 'UNKNOWN')}",
                        "ویژگی ناقص",
                        prop.get("property_id", "UNKNOWN"),
                        "عنوان ویژگی تکمیل نشده است.",
                        "medium",
                        "عنوان و وضعیت بررسی ویژگی را تکمیل کنید.",
                    )
        if dimensions:
            for dimension in dimensions:
                if not dimension.get("title") and not dimension.get("name"):
                    add(
                        f"DIMENSION-NOT-COMPLETE-{dimension.get('dimension_id', dimension.get('property_id', 'UNKNOWN'))}",
                        "بعد ناقص",
                        dimension.get("dimension_id", dimension.get("property_id", "UNKNOWN")),
                        "عنوان بُعد تکمیل نشده است.",
                        "medium",
                        "عنوان و وضعیت بررسی بُعد را تکمیل کنید.",
                    )
        for concept_id in category.get("concept_ids") or []:
            if concept_id in seen_concepts:
                add(f"DUP-CONCEPT-{concept_id}", "تخصیص تکراری مفهوم", concept_id, "مفهوم به بیش از یک مقوله اصلی واگذار شده است.", "high", "تخصیص نهایی را یک‌بار تعیین کنید.")
            seen_concepts.add(concept_id)
            assigned.add(concept_id)
            if concept_id not in concepts:
                add(f"INVALID-CONCEPT-{concept_id}", "شناسه مفهوم نامعتبر", concept_id, "شناسه مفهوم در ورودی مرحله اول یافت نشد.", "high", "شناسه را اصلاح کنید و شناسه اولیه را حفظ کنید.")

    unassigned = sorted(concepts - assigned)
    for concept_id in unassigned:
        add(f"UNASSIGNED-{concept_id}", "مفهوم تعیین‌تکلیف‌نشده", concept_id, "مفهوم در هیچ مقوله فعلی قرار ندارد.", "high", "مفهوم را به مقوله مناسب منتقل کنید.")

    category_ids = {item.get("category_id") for item in categories if item.get("category_id")}
    valid_relationship = True
    for relation in analysis.get("relationships") or []:
        relation_id = relation.get("relationship_id") or "UNKNOWN"
        source = relation.get("source_category_id")
        target = relation.get("target_category_id")
        if source not in category_ids or target not in category_ids:
            valid_relationship = False
            add(f"REL-INVALID-{relation_id}", "مبدأ یا مقصد نامعتبر", relation_id, "مبدأ یا مقصد رابطه در مقوله‌های فعلی نیست.", "high", "مقوله‌های رابطه را اصلاح یا نیازمند بازبینی علامت بزنید.")
        evidence = relation.get("evidence") or []
        if not evidence:
            add(f"REL-NOEVIDENCE-{relation_id}", "شاهد ناکافی رابطه", relation_id, "رابطه شاهد معتبر ندارد.", "medium", "شاهد را بازبینی یا رابطه را رد کنید.")
        for evidence_item in evidence:
            paragraph_id = evidence_item.get("paragraph_id")
            if paragraph_id and paragraph_id not in paragraph_ids:
                add(f"EVIDENCE-PARAGRAPH-{paragraph_id}", "شناسه پاراگراف نامعتبر", relation_id, f"رابطه به پاراگراف {paragraph_id} اشاره دارد که در مصاحبه نیست.", "high", "شناسه پاراگراف یا شاهد را اصلاح کنید.")
        relation_decision = (state.get("relationship_decisions") or {}).get(relation_id, {})
        if relation_decision:
            researcher_status = relation_decision.get("researcher_status")
            validity_status = relation_decision.get("validity_status")
            if researcher_status in {"تأیید", "اصلاح و تأیید"} and validity_status in {
                "شاهد ناکافی",
                "تعارض شواهد",
                "نقص ساختاری",
                "نامعتبر از نظر ساختاری",
            }:
                add(
                    f"REL-CONFLICT-{relation_id}",
                    "تعارض وضعیت تصمیم و اعتبار",
                    relation_id,
                    "رابطه تأیید شده در حالی که اعتبار ساختاری آن ناقص یا نامعتبر است.",
                    "high",
                    "تصمیم پژوهشگر یا اعتبار داده را مجدداً تعیین کنید.",
                )

    placements = state.get("placement_decisions") or {}
    for category in categories:
        category_id = category["category_id"]
        if not placements.get(category_id) and not (project.get("researcher_edits") or {}).get("interview_analysis", {}).get("paradigm_placements", {}).get(category_id):
            add(f"PLACEMENT-{category_id}", "جایگاه پارادایمی ناقص", category_id, "مقوله جایگاه قطعی ندارد.", "medium", "جایگاه را مشخص، نامشخص یا نیازمند بازبینی ثبت کنید.")
        entity_status = (state.get("entities") or {}).get(category_id, {})
        if category.get("existence_status") in {"created", "split"}:
            add(
                f"CATEGORY-REVIEW-{category_id}",
                "مقوله نیازمند بازبینی پس از تغییر",
                category_id,
                "مقوله جدید یا تفکیک‌شده باید پس از تغییرات بازبینی شود.",
                "medium",
                "ویژگی‌ها، جایگاه و روابط وابسته را بازبینی کنید.",
            )

    changed_category_ids = {
        item["record_id"]
        for item in state.get("change_history", [])
        if item.get("record_type") in {"category", "category_merge", "category_split"}
    }
    for relation in analysis.get("relationships") or []:
        relation_id = relation.get("relationship_id") or "UNKNOWN"
        if changed_category_ids.intersection({
            relation.get("source_category_id"),
            relation.get("target_category_id"),
        }):
            add(
                f"REL-DEPENDENCY-{relation_id}",
                "رابطه وابسته به تغییر مقوله",
                relation_id,
                "یکی از مقوله‌های مبدأ یا مقصد بعداً تغییر کرده و رابطه نیازمند بازبینی است.",
                "high",
                "رابطه و شاهد آن را بازبینی کنید.",
            )

    phenomenon = (project.get("researcher_edits") or {}).get("interview_analysis", {}).get("central_phenomenon") or {}
    if not phenomenon.get("decision"):
        add("PHENOMENON-DECISION", "پدیده محوری مصاحبه", "central_phenomenon", "تصمیم پژوهشگر برای پدیده محوری مصاحبه ثبت نشده است.", "high", "پدیده را تأیید، اصلاح، رد یا نامشخص اعلام کنید.")

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda item: (severity_rank.get(item["severity"], 3), item["issue_id"]))
    return {
        "status": "invalid" if any(item["severity"] == "high" for item in issues) else "warning" if issues else "valid",
        "issue_count": len(issues),
        "open_count": sum(item["resolution_status"] == "باز" for item in issues),
        "issues": issues,
        "groups": [
            {"title": "ضروری برای اصلاح ساختاری", "severity": "high", "findings": [item for item in issues if item["severity"] == "high"]},
            {"title": "نیازمند تصمیم پژوهشگر", "severity": "medium", "findings": [item for item in issues if item["severity"] == "medium"]},
            {"title": "پیشنهادهای بازبینی", "severity": "low", "findings": [item for item in issues if item["severity"] == "low"]},
        ],
    }
