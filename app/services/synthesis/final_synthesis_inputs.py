from __future__ import annotations

from typing import Any

from app.repositories.synthesis_repository import (
    get_run_sources,
    get_synthesis_run,
    list_stage_results,
)
from app.services.synthesis.category_grouping_service import _approved_pool
from app.services.synthesis.normalization_service import stable_hash, stable_source_item_id
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS
from app.services.synthesis.source_snapshot_service import load_run_snapshot_context
from app.services.synthesis_service import summarize_project_eligibility
from app.services.synthesis.theme_synthesis_prompt import (
    STAGE_COMPONENT_KEY as THEME_COMPONENT_KEY,
    STAGE_KEY as THEME_STAGE_KEY,
    _make_category_units,
    latest_theme_result,
)

PHENOMENON_STAGE_KEY = "phenomenon_synthesis"
RELATIONSHIP_STAGE_KEY = "relationship_synthesis"
FINAL_MODEL_STAGE_KEY = "final_model"
FINAL_MODEL_COMPONENT_KEY = "paradigm_model"


def _latest(run_id: str, stage_key: str, component_key: str) -> dict[str, Any] | None:
    rows = [
        row for row in list_stage_results(run_id)
        if row["stage_key"] == stage_key and row["component_key"] == component_key
    ]
    return max(rows, key=lambda row: row["version"]) if rows else None


def _source_phenomenon_id(project_id: str, local_id: str) -> str:
    return f"iphen_{stable_hash([project_id, local_id])[:24]}"


def _source_relationship_id(project_id: str, local_id: str) -> str:
    return f"srel_{stable_hash([project_id, local_id])[:24]}"


def _approved_phase_five(run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], int]:
    latest = latest_theme_result(run_id)
    if latest is None or latest.get("stale"):
        raise ValueError("خروجی معتبر و به‌روز فاز پنجم وجود ندارد.")
    if latest.get("approval_status") != "approved" or latest.get("status") != "approved":
        raise ValueError("فاز پنجم هنوز به‌طور نهایی تأیید نشده است.")
    if not (latest.get("validation_report_json") or {}).get("valid"):
        raise ValueError("گزارش اعتبارسنجی فاز پنجم معتبر نیست.")
    units, source_phase_version, _ = _make_category_units(run_id)
    if source_phase_version != latest.get("source_phase_version") and latest.get("source_phase_version") is not None:
        raise ValueError("نسخه مبدأ فاز چهارم در نتیجه فاز پنجم با ورودی جاری مطابقت ندارد.")
    result = latest.get("output_json") or {}
    component_results = result.get("component_results")
    if not isinstance(component_results, dict) or set(component_results) != set(POOL_COMPONENT_KEYS):
        raise ValueError("ساختار پنج مؤلفه تأییدشده فاز پنجم کامل نیست.")
    unit_by_id = {str(unit["category_unit_id"]): unit for unit in units}
    theme_nodes: list[dict[str, Any]] = []
    standalone_nodes: list[dict[str, Any]] = []
    unit_to_node: dict[str, str] = {}
    source_item_to_node: dict[str, str] = {}
    for component_key in POOL_COMPONENT_KEYS:
        component = component_results[component_key]
        if not isinstance(component, dict):
            raise ValueError("ساختار مؤلفه فاز پنجم معتبر نیست.")
        for theme in component.get("themes", []):
            if theme.get("review_status") != "approved":
                raise ValueError("تم تأییدنشده در خروجی فاز پنجم وجود دارد.")
            unit_ids = [str(value) for value in theme.get("constituent_category_unit_ids", [])]
            if not unit_ids or any(unit_id not in unit_by_id for unit_id in unit_ids):
                raise ValueError("تم فاز پنجم به Category Unit نامعتبر ارجاع می‌دهد.")
            node_id = str(theme.get("theme_id") or "")
            if not node_id:
                raise ValueError("شناسه پایدار تم فاز پنجم موجود نیست.")
            member_units = [unit_by_id[unit_id] for unit_id in unit_ids]
            node = {
                "node_id": node_id,
                "node_type": "theme",
                "component_key": component_key,
                "title": theme.get("proposed_theme_name") or "",
                "definition": theme.get("definition") or "",
                "source_ids": [str(unit.get("source_group_id")) for unit in member_units if unit.get("source_group_id")],
                "source_item_ids": sorted({
                    str(item_id) for unit in member_units for item_id in unit.get("source_item_ids", [])
                }),
                "category_unit_ids": unit_ids,
                "theme_ids": [node_id],
                "evidence_ref_ids": sorted({
                    str(evidence_id) for unit in member_units for evidence_id in unit.get("evidence_ref_ids", [])
                }),
                "approval_status": "approved",
                "source_phase": THEME_STAGE_KEY,
                "source_phase_version": latest["version"],
                "unit_kind": None,
                "source_items": [
                    source_item
                    for unit in member_units
                    for source_item in unit.get("source_items", [])
                ],
            }
            theme_nodes.append(node)
            for unit_id in unit_ids:
                unit_to_node[unit_id] = node_id
            for item_id in node["source_item_ids"]:
                source_item_to_node[item_id] = node_id
        for item in component.get("unthemed_category_units", []):
            if item.get("review_status") != "approved":
                raise ValueError("Category Unit مستقلِ بدون تم تأییدنشده است.")
            unit_id = str(item.get("category_unit_id") or "")
            unit = unit_by_id.get(unit_id)
            if unit is None or unit.get("component_key") != component_key:
                raise ValueError("Category Unit مستقل به ورودی معتبر فاز پنجم تعلق ندارد.")
            node = {
                "node_id": unit_id,
                "node_type": "category_unit",
                "component_key": component_key,
                "title": unit.get("category_name") or "",
                "definition": unit.get("definition") or "",
                "source_ids": [str(unit.get("source_group_id"))] if unit.get("source_group_id") else [],
                "source_item_ids": list(unit.get("source_item_ids", [])),
                "category_unit_ids": [unit_id],
                "theme_ids": [],
                "evidence_ref_ids": list(unit.get("evidence_ref_ids", [])),
                "approval_status": "approved",
                "source_phase": THEME_STAGE_KEY,
                "source_phase_version": latest["version"],
                "unit_kind": unit.get("unit_kind"),
                "source_items": unit.get("source_items", []),
            }
            standalone_nodes.append(node)
            unit_to_node[unit_id] = unit_id
            for item_id in node["source_item_ids"]:
                source_item_to_node[item_id] = unit_id
    covered_units = set(unit_to_node)
    if covered_units != set(unit_by_id) or len(covered_units) != len(unit_to_node):
        raise ValueError("پوشش تم‌ها و Category Unitهای مستقل فاز پنجم کامل یا یکتا نیست.")
    return theme_nodes, standalone_nodes, {
        "unit_by_id": unit_by_id,
        "unit_to_node": unit_to_node,
        "source_item_to_node": source_item_to_node,
        "result": result,
    }, int(latest["version"])


def load_final_synthesis_inputs(run_id: str) -> dict[str, Any]:
    run = get_synthesis_run(run_id)
    if run is None:
        raise ValueError("اجرای تلفیقی پیدا نشد.")
    if run.get("status") == "source_stale":
        raise ValueError("Snapshot یکی از مصاحبه‌های این Run منقضی شده است؛ برای تحلیل جدید یک Run تازه بسازید.")
    theme_nodes, standalone_nodes, mappings, theme_version = _approved_phase_five(run_id)
    pool, pool_version, source_count = _approved_pool(run_id)
    pool_result = _latest(run_id, "pool_extraction", "interview_pools")
    if pool_result is None or pool_result.get("approval_status") != "approved":
        raise ValueError("Pool تأییدشده برای بازیابی شواهد وجود ندارد.")
    context = load_run_snapshot_context(run_id)
    if context.get("source_issues"):
        raise ValueError("Snapshot یک یا چند مصاحبه معتبر نیست؛ اجرا متوقف شد.")
    sources = context["snapshots"]
    if (
        len(sources) != source_count
        or len(sources) != run.get("selected_source_count")
        or (
            run.get("expected_source_count")
            and len(sources) != run.get("expected_source_count")
        )
    ):
        raise ValueError("تعداد Snapshotهای منابع با Run و Pool تأییدشده سازگار نیست.")
    source_ids = [source["source_project_id"] for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("شناسه منبع در Snapshotهای Run تکراری است.")
    evidence_by_id = {
        str(item.get("evidence_ref_id")): item
        for item in pool.get("evidence", [])
        if item.get("evidence_ref_id") and item.get("valid")
    }
    if len(evidence_by_id) != len([
        item for item in pool.get("evidence", [])
        if item.get("evidence_ref_id") and item.get("valid")
    ]):
        raise ValueError("شناسه Evidence در Pool تکراری است.")

    nodes = theme_nodes + standalone_nodes
    for node in nodes:
        node["source_items"] = [
            source_item
            for source_item in node["source_items"]
            if source_item.get("source_item_id") in {
                str(item_id) for item_id in node["source_item_ids"]
            }
        ]
        if not node["title"] or not node["definition"]:
            raise ValueError(f"تعریف یا عنوان نود {node['node_id']} خالی است.")
        for evidence_id in node["evidence_ref_ids"]:
            if evidence_id not in evidence_by_id:
                raise ValueError(f"شاهد نود {node['node_id']} نامعتبر است: {evidence_id}")

    source_phenomena: list[dict[str, Any]] = []
    source_relationships: list[dict[str, Any]] = []
    source_item_by_project_category: dict[tuple[str, str], list[str]] = {}
    for component_key in POOL_COMPONENT_KEYS:
        for item in pool.get("pools", {}).get(component_key, []):
            source_item_by_project_category.setdefault(
                (str(item.get("source_project_id")), str(item.get("source_category_id"))), []
            ).append(str(item.get("source_item_id")))

    source_phenomenon_pool = {
        str(item.get("source_project_id")): item
        for item in pool.get("pools", {}).get("central_phenomenon", [])
    }
    relationship_pool_by_key = {
        (str(item.get("source_project_id")), str(item.get("source_relationship_id"))): item
        for item in pool.get("relationships", [])
    }

    for source in sources:
        project_id = str(source["source_project_id"])
        snapshot = source["snapshot"]
        analysis = snapshot.get("analysis") or {}
        if not isinstance(analysis, dict) or analysis.get("schema_version") != "2.0":
            raise ValueError(f"تحلیل مستقل مصاحبه {source.get('source_title')} نسخه معتبر ۲.۰ نیست.")
        interview = analysis.get("interview_analysis")
        if not isinstance(interview, dict):
            raise ValueError(f"تحلیل مستقل مصاحبه {source.get('source_title')} وجود ندارد.")
        eligibility = summarize_project_eligibility(snapshot)
        if not eligibility["eligible"]:
            raise ValueError(
                f"تحلیل مستقل مصاحبه {source.get('source_title')} آماده نیست: "
                f"{eligibility['reason']}"
            )
        edits = (snapshot.get("researcher_edits") or {}).get("interview_analysis") or {}
        phenomenon = interview.get("central_phenomenon")
        if not isinstance(phenomenon, dict) or phenomenon.get("identified") is False:
            phenomenon = None
        if phenomenon is not None:
            if not phenomenon.get("title") or not phenomenon.get("definition"):
                raise ValueError(
                    f"پدیده محوری مصاحبه {source.get('source_title')} ناقص است؛ "
                    "آن را تکمیل کنید یا صریحاً به‌عنوان شناسایی‌نشده ثبت کنید."
                )
            local_id = str(phenomenon.get("category_id") or "central_phenomenon")
            phenomenon_id = _source_phenomenon_id(project_id, local_id)
            phenomenon_item = source_phenomenon_pool.get(project_id)
            if phenomenon_item is None or phenomenon_item.get("source_review_status") != "researcher_confirmed":
                raise ValueError(f"منشأ پدیده محوری مصاحبه {source.get('source_title')} معتبر نیست.")
            phenomenon_evidence_ids = [
                str(reference["evidence_ref_id"])
                for reference in phenomenon_item.get("evidence_refs", [])
                if reference.get("evidence_ref_id") in evidence_by_id
            ]
            if not phenomenon_evidence_ids:
                raise ValueError(f"پدیده محوری مصاحبه {source.get('source_title')} شاهد معتبر ندارد.")
            phenomenon_edit = edits.get("central_phenomenon") or {}
            source_phenomena.append({
                "node_id": phenomenon_id,
                "source_project_id": project_id,
                "source_interview_id": str(snapshot.get("interview_id") or project_id),
                "source_title": str(snapshot.get("title") or source.get("source_title") or ""),
                "title": phenomenon_edit.get("title") or phenomenon.get("title") or "",
                "definition": phenomenon_edit.get("definition") or phenomenon.get("definition") or "",
                "supporting_source_item_ids": [phenomenon_item["source_item_id"]],
                "supporting_category_ids": [
                    str(value) for value in phenomenon.get("supporting_category_ids", [])
                    if value
                ],
                "supporting_node_ids": [
                    mappings["source_item_to_node"][source_item_id]
                    for category_id in phenomenon.get("supporting_category_ids", [])
                    for source_item_id in source_item_by_project_category.get((project_id, str(category_id)), [])
                    if source_item_id in mappings["source_item_to_node"]
                ],
                "evidence_ref_ids": sorted(set(phenomenon_evidence_ids)),
                "source_phase": "interview_analysis",
                "approval_status": "approved",
            })
            if not source_phenomena[-1]["title"] or not source_phenomena[-1]["definition"]:
                raise ValueError(f"عنوان یا تعریف پدیده محوری مصاحبه {source.get('source_title')} خالی است.")
        for relation in interview.get("relationships", []):
            if not isinstance(relation, dict) or relation.get("needs_researcher_review") is True:
                continue
            local_relationship_id = str(relation.get("relationship_id") or "")
            pool_relation = relationship_pool_by_key.get((project_id, local_relationship_id))
            if not local_relationship_id or pool_relation is None:
                raise ValueError(f"رابطه تأییدشده‌ای در Pool برای مصاحبه {source.get('source_title')} قابل بازیابی نیست.")
            evidence_ids = [
                str(reference["evidence_ref_id"])
                for reference in pool_relation.get("evidence_refs", [])
                if reference.get("evidence_ref_id") in evidence_by_id
            ]
            source_category_ids = [
                str(relation.get("source_category_id") or ""),
                str(relation.get("target_category_id") or ""),
            ]
            source_item_ids = []
            for category_id in source_category_ids:
                candidate_ids = source_item_by_project_category.get((project_id, category_id), [])
                source_item_ids.extend(
                    item_id for item_id in candidate_ids
                    if item_id in mappings["source_item_to_node"]
                )
            if not evidence_ids or len(source_item_ids) < 2:
                raise ValueError(f"رابطه تأییدشده {local_relationship_id} فاقد دو سر یا شاهد معتبر است.")
            relation_edits = (edits.get("relationships") or {}).get(local_relationship_id, {})
            nodes_for_relation = [
                mappings["source_item_to_node"][item_id]
                for item_id in source_item_ids
            ]
            if nodes_for_relation[0] == nodes_for_relation[1]:
                mapped_source = mapped_target = nodes_for_relation[0]
            else:
                mapped_source, mapped_target = nodes_for_relation[0], nodes_for_relation[1]
            source_relationships.append({
                "source_relationship_id": _source_relationship_id(project_id, local_relationship_id),
                "local_relationship_id": local_relationship_id,
                "source_project_id": project_id,
                "source_title": str(snapshot.get("title") or source.get("source_title") or ""),
                "source_node_id": mapped_source,
                "target_node_id": mapped_target,
                "source_category_ids": source_category_ids,
                "source_item_ids": list(dict.fromkeys(source_item_ids)),
                "relationship_type": relation.get("relationship_type") or "",
                "description": relation_edits.get("description") or relation.get("description") or "",
                "reasoning": relation.get("reasoning") or relation.get("selection_reason") or "",
                "evidence_ref_ids": sorted(set(evidence_ids)),
                "approval_status": "approved",
            })

    source_phenomenon_ids = {item["node_id"] for item in source_phenomena}
    if len(source_phenomenon_ids) != len(source_phenomena):
        raise ValueError("شناسه پدیده‌های محوری مستقل تکراری است.")
    source_relationship_ids = {item["source_relationship_id"] for item in source_relationships}
    if len(source_relationship_ids) != len(source_relationships):
        raise ValueError("شناسه روابط تأییدشده مصاحبه‌های مستقل تکراری است.")
    evidence_to_source_items: dict[str, set[str]] = {}
    source_item_to_component: dict[str, str] = {}
    for item in pool.get("pools", {}).get("central_phenomenon", []):
        source_item_to_component[str(item.get("source_item_id"))] = "central_phenomenon"
        for ref in item.get("evidence_refs", []):
            evidence_to_source_items.setdefault(str(ref.get("evidence_ref_id")), set()).add(
                str(item.get("source_item_id"))
            )
    for component_key in POOL_COMPONENT_KEYS:
        for item in pool.get("pools", {}).get(component_key, []):
            source_item_to_component[str(item.get("source_item_id"))] = component_key
            for ref in item.get("evidence_refs", []):
                evidence_to_source_items.setdefault(str(ref.get("evidence_ref_id")), set()).add(
                    str(item.get("source_item_id"))
                )
    source_title_by_project = {
        str(source["source_project_id"]): str(source.get("source_title") or "")
        for source in sources
    }
    for evidence_id, evidence in evidence_by_id.items():
        source_item_ids = sorted(evidence_to_source_items.get(evidence_id, set()))
        evidence["source_title"] = source_title_by_project.get(
            str(evidence.get("source_project_id") or ""), ""
        )
        evidence["source_item_ids"] = source_item_ids
        evidence["source_component_keys"] = sorted({
            source_item_to_component[source_item_id]
            for source_item_id in source_item_ids
            if source_item_id in source_item_to_component
        })
        evidence["evidence_type"] = (
            evidence.get("raw_evidence", {}).get("evidence_type")
            or evidence.get("raw_evidence", {}).get("type")
            or "interview_evidence"
        )
    for phenomenon in source_phenomena:
        for evidence_id in phenomenon["evidence_ref_ids"]:
            if evidence_to_source_items.get(evidence_id, set()).isdisjoint(phenomenon["supporting_source_item_ids"]):
                raise ValueError("شاهد پدیده محوری به Source Item ادعاشده تعلق ندارد.")

    project_metadata = {
        "title": run.get("title") or "",
        "topic": run.get("study_topic") or "",
        "problem": "",
        "research_question": "",
        "objectives": [],
    }
    base_input = {
        "run_id": run_id,
        "run": project_metadata,
        "source_count": source_count,
        "source_phase_version": theme_version,
        "pool_version": pool_version,
        "pool_input_hash": pool_result.get("input_hash"),
        "sources": [
            {
                "source_project_id": source["source_project_id"],
                "source_title": source["source_title"],
                "source_hash": source["source_hash"],
                "analysis": source["snapshot"].get("analysis"),
                "researcher_edits": source["snapshot"].get("researcher_edits"),
            }
            for source in sources
        ],
        "themes": theme_nodes,
        "standalone_category_units": standalone_nodes,
        "source_phenomena": source_phenomena,
        "source_relationships": source_relationships,
        "evidence": [
            {
                key: value
                for key, value in item.items()
                if key != "raw_evidence"
            }
            for item in evidence_by_id.values()
        ],
    }
    return {
        "run": run,
        "project_metadata": project_metadata,
        "source_count": source_count,
        "source_phase_version": theme_version,
        "pool_version": pool_version,
        "nodes": nodes,
        "source_phenomena": source_phenomena,
        "source_relationships": source_relationships,
        "evidence_by_id": evidence_by_id,
        "evidence_to_source_items": evidence_to_source_items,
        "source_item_to_node": mappings["source_item_to_node"],
        "unit_to_node": mappings["unit_to_node"],
        "base_input": base_input,
        "base_input_hash": stable_hash(base_input),
        "phase_five_result": _latest(run_id, THEME_STAGE_KEY, THEME_COMPONENT_KEY),
        "pool_result": pool_result,
        "source_rows": get_run_sources(run_id),
    }


def final_phenomenon_node_id(run_id: str) -> str:
    return f"fphen_{stable_hash(['final_phenomenon', run_id])[:24]}"
