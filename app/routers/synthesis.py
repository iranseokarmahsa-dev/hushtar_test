"""Routes for multi-interview synthesis selection and run creation."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from graphviz.backend import ExecutableNotFound
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Any
from urllib.parse import urlencode

from app.core.database import list_projects
from app.services.coding_table_service import (
    EXPORT_FORMATS,
    build_final_model_hierarchy_rows,
    build_synthesis_coding_table,
    build_stage5_excel,
    final_model_to_docx,
)
from app.repositories.synthesis_repository import list_stage_results, list_synthesis_runs
from app.services.synthesis_service import create_synthesis_run_from_selection, summarize_project_eligibility
from app.services.synthesis.collector_service import collect_run_pools
from app.services.synthesis.category_grouping_service import (
    correction_prompt,
    grouping_page_data,
    latest_grouping_result,
    submit_grouping_output,
)
from app.services.synthesis.category_grouping_prompt import PromptSizeLimitError
from app.services.synthesis.category_integration_service import (
    COMPONENT_LABELS as INTEGRATION_COMPONENT_LABELS,
    build_category_integration_prompt,
    category_integration_page_data,
    submit_category_integration_output,
)
from app.services.synthesis.category_integration_review_service import (
    record_category_integration_review_decision,
)
from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS
from app.services.synthesis.theme_abstraction_service import (
    build_theme_abstraction_prompt,
    get_approved_theme_abstractions,
    next_theme_abstraction_component,
    submit_theme_abstraction_output,
    theme_abstraction_page_data,
)
from app.services.synthesis.theme_abstraction_review_service import (
    record_theme_abstraction_review_decision,
)
from app.services.synthesis.component_synthesis_service import (
    COMPONENTS as COMPONENT_SYNTHESIS_COMPONENTS,
    build_component_prompt,
    component_synthesis_final_data,
    component_synthesis_page_data,
    submit_component_output,
)
from app.services.synthesis.category_review_service import record_category_review_decision
from app.services.synthesis.review_service import record_pool_force_proceed, record_pool_review_decision
from app.services.synthesis.theme_review_service import record_theme_review_decision
from app.services.synthesis.theme_synthesis_prompt import ThemePromptSizeLimitError
from app.services.synthesis.theme_synthesis_service import (
    submit_theme_synthesis_output,
    theme_correction_prompt,
    theme_synthesis_page_data,
)
from app.services.synthesis.phenomenon_prompt_builder import FinalSynthesisPromptSizeLimitError
from app.services.synthesis.final_synthesis_service import (
    build_final_model,
    final_model_page_data,
    latest_final_model,
    phenomenon_correction_prompt,
    phenomenon_page_data,
    record_phenomenon_review_decision,
    record_relationship_review_decision,
    relationship_correction_prompt,
    relationship_page_data,
    save_final_model_layout,
    submit_phenomenon_output,
    submit_relationship_output,
)
from app.services.synthesis.final_diagram_service import (
    prepare_final_model_diagram,
    render_final_model_diagram as render_paradigm_diagram,
)

router = APIRouter(prefix="/synthesis", tags=["synthesis"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/runs")
def synthesis_runs_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="synthesis_runs.html",
        context={"runs": list_synthesis_runs()},
    )


@router.get("/runs/{run_id}/pool-preview")
def pool_preview_page(request: Request, run_id: str, error: str = "", notice: str = ""):
    try:
        pool = collect_run_pools(run_id)
        pool_results = [
            item for item in list_stage_results(run_id)
            if item["stage_key"] == "pool_extraction" and item["component_key"] == "interview_pools"
        ]
        latest = max(pool_results, key=lambda item: item["version"]) if pool_results else None
        components = [
            {"key": key, "label": label, "items": pool.get("pools", {}).get(key, [])}
            for key, label in COMPONENT_SYNTHESIS_COMPONENTS
        ]
        coverage = pool.get("source_coverage", [])
        metadata = pool.get("metadata", {})
        can_continue = (
            metadata.get("run_id") == run_id
            and metadata.get("source_count") == len(coverage)
            and all(item.get("processed") for item in coverage)
            and all(isinstance(pool.get("pools", {}).get(key), list) for key, _ in COMPONENT_SYNTHESIS_COMPONENTS)
        )
        validation = pool.get("validation_report", {})
        validation_errors = validation.get("errors", []) if isinstance(validation, dict) else []
        if not can_continue:
            error = error or "داده‌های ورودی اجرا یا پوشش Snapshotها کامل نیست."
        elif validation_errors:
            notice = notice or (
                f"{len(validation_errors)} مورد نیازمند بررسی خودکار شناسایی شد؛ "
                "با این حال می‌توانید پرامپت هر مؤلفه را جداگانه تکمیل کنید."
            )
    except ValueError as pool_error:
        pool = None
        latest = None
        components = []
        can_continue = False
        error = error or str(pool_error)
    return templates.TemplateResponse(
        request=request,
        name="pool_preview.html",
        context={
            "request": request,
            "run_id": run_id,
            "pool": pool,
            "latest": latest,
            "components": components,
            "can_continue": can_continue,
            "page_error": error,
            "page_notice": notice,
            "evidence_by_id": {
                item["evidence_ref_id"]: item
                for item in (pool or {}).get("evidence", [])
                if item.get("evidence_ref_id")
            },
        },
    )


class PoolReviewDecision(BaseModel):
    category_id: str
    source_project_id: str
    review_item_id: str | None = None
    decision: str
    reason: str
    approved: bool
    pool_version: int
    component_key: str | None = None
    replacement_category_id: str | None = None


class CategoryReviewDecision(BaseModel):
    decision: str
    reason: str
    group_id: str | None = None
    target_group_id: str | None = None
    source_item_id: str | None = None
    proposed_category_name: str | None = None
    definition: str | None = None
    partitions: list[dict[str, Any]] | None = None


class CategoryIntegrationReviewDecision(BaseModel):
    component_key: str
    decision: str
    reason: str
    category_id: str | None = None
    target_category_id: str | None = None
    source_item_id: str | None = None
    title: str | None = None
    definition: str | None = None
    partitions: list[dict[str, Any]] | None = None


class ThemeAbstractionReviewDecision(BaseModel):
    component_key: str
    decision: str
    reason: str
    theme_id: str | None = None
    target_theme_id: str | None = None
    integrated_category_id: str | None = None
    title: str | None = None
    definition: str | None = None
    rationale: str | None = None
    abstraction_level: str | None = None
    preserved_dimensions: str | None = None
    partitions: list[dict[str, Any]] | None = None


class ThemeReviewDecision(BaseModel):
    decision: str
    reason: str
    theme_id: str | None = None
    target_theme_id: str | None = None
    contradiction_id: str | None = None
    category_unit_id: str | None = None
    proposed_theme_name: str | None = None
    definition: str | None = None
    reasoning: str | None = None
    conceptual_scope: str | None = None
    partitions: list[dict[str, Any]] | None = None


class PhenomenonReviewDecision(BaseModel):
    decision: str
    reason: str
    target_id: str | None = None
    title: str | None = None
    definition: str | None = None
    selection_reason: str | None = None
    description: str | None = None
    reasoning: str | None = None


class RelationshipReviewDecision(BaseModel):
    decision: str
    reason: str
    target_id: str | None = None
    source_node_id: str | None = None
    target_node_id: str | None = None
    relationship_type: str | None = None
    direction: str | None = None
    description: str | None = None
    reasoning: str | None = None
    supporting_source_relationship_ids: list[str] | None = None
    supporting_source_phenomenon_ids: list[str] | None = None
    supporting_source_item_ids: list[str] | None = None
    supporting_category_unit_ids: list[str] | None = None
    evidence_ref_ids: list[str] | None = None


class FinalModelLayoutPayload(BaseModel):
    positions: dict[str, dict[str, float]]


def _project_eligibility(project: dict):
    try:
        return summarize_project_eligibility(project, require_pool_ready=True)
    except TypeError as error:
        if "require_pool_ready" not in str(error):
            raise
        return summarize_project_eligibility(project)


@router.get("/select")
def synthesis_select_page(request: Request):
    return RedirectResponse("/projects", status_code=303)


@router.post("/runs")
def create_synthesis_run_route(
    request: Request,
    title: str = Form(""),
    study_topic: str = Form(""),
    selected_ids: list[str] = Form([]),
):
    try:
        payload = create_synthesis_run_from_selection(
            title=title,
            study_topic=study_topic,
            selected_ids=selected_ids,
        )
    except ValueError as error:
        if "text/html" in request.headers.get("accept", ""):
            projects = list_projects()
            eligibility = {
                project["project_id"]: _project_eligibility(project)
                for project in projects
            }
            return templates.TemplateResponse(
                request=request,
                name="projects.html",
                context={
                    "projects": projects,
                    "eligibility": eligibility,
                    "selection_mode": True,
                    "selection_error": str(error),
                    "selection_form": {
                        "selected_ids": selected_ids,
                    },
                },
                status_code=400,
            )
        raise HTTPException(status_code=400, detail=str(error)) from error

    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(f"/synthesis/runs/{payload['run_id']}/pool-preview", status_code=303)
    return JSONResponse(status_code=201, content=payload)


@router.get("/runs/{run_id}/pools")
def get_synthesis_pools(run_id: str):
    try:
        return collect_run_pools(run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/runs/{run_id}/category-integration")
def category_integration_page(
    request: Request,
    run_id: str,
    component_key: str = "causal_conditions",
):
    try:
        context = category_integration_page_data(run_id, component_key)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return templates.TemplateResponse(
        request=request,
        name="category_integration.html",
        context={"request": request, **context},
    )


@router.get("/runs/{run_id}/category-integration/prompt.md")
def download_category_integration_prompt(run_id: str, component_key: str):
    try:
        prompt = build_category_integration_prompt(run_id, component_key)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return PlainTextResponse(
        prompt["prompt_text"],
        headers={
            "Content-Disposition": (
                f'attachment; filename="category-integration-{component_key}-{run_id[:8]}.md"'
            )
        },
    )


@router.post("/runs/{run_id}/category-integration/results")
async def submit_category_integration_result(
    run_id: str,
    component_key: str = Form(...),
    raw_output_text: str = Form(""),
    markdown_file: UploadFile | None = File(None),
):
    if component_key not in INTEGRATION_COMPONENT_LABELS:
        raise HTTPException(status_code=400, detail="مؤلفهٔ درخواستی معتبر نیست.")
    if markdown_file is not None and markdown_file.filename:
        if raw_output_text.strip():
            raise HTTPException(status_code=400, detail="متن را جای‌گذاری کنید یا فایل Markdown بدهید، نه هر دو.")
        if not markdown_file.filename.lower().endswith((".md", ".markdown")):
            raise HTTPException(status_code=400, detail="فقط فایل Markdown با پسوند md یا markdown پذیرفته می‌شود.")
        content = await markdown_file.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="حجم فایل Markdown باید کمتر از ۵ مگابایت باشد.")
        try:
            raw_output_text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise HTTPException(status_code=400, detail="فایل Markdown باید با کدگذاری UTF-8 باشد.") from error
    try:
        result = submit_category_integration_output(run_id, component_key, raw_output_text)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if result.get("status") == "ready_for_researcher_review":
        component_index = POOL_COMPONENT_KEYS.index(component_key)
        if component_index + 1 < len(POOL_COMPONENT_KEYS):
            next_url = f"/synthesis/runs/{run_id}/category-integration?{urlencode({'component_key': POOL_COMPONENT_KEYS[component_index + 1], 'received': '1'})}"
        else:
            next_url = f"/synthesis/runs/{run_id}/theme-abstraction?{urlencode({'component_key': POOL_COMPONENT_KEYS[0], 'received': '1'})}"
    else:
        next_url = f"/synthesis/runs/{run_id}/category-integration?{urlencode({'component_key': component_key})}"
    return RedirectResponse(next_url, status_code=303)


@router.post("/runs/{run_id}/category-integration/review")
def submit_category_integration_review(
    run_id: str,
    decision: CategoryIntegrationReviewDecision,
):
    try:
        result = record_category_integration_review_decision(
            run_id,
            component_key=decision.component_key,
            decision=decision.decision,
            reason=decision.reason,
            category_id=decision.category_id,
            target_category_id=decision.target_category_id,
            source_item_id=decision.source_item_id,
            title=decision.title,
            definition=decision.definition,
            partitions=decision.partitions,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return JSONResponse({
        "stage_result_id": result["stage_result_id"],
        "version": result["version"],
        "status": result["status"],
        "approval_status": result["approval_status"],
    })


@router.get("/runs/{run_id}/theme-abstraction")
def theme_abstraction_page(
    request: Request,
    run_id: str,
    component_key: str = "causal_conditions",
):
    try:
        context = theme_abstraction_page_data(run_id, component_key)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return templates.TemplateResponse(
        request=request,
        name="theme_abstraction.html",
        context={"request": request, **context},
    )


@router.get("/runs/{run_id}/theme-abstraction/prompt.md")
def download_theme_abstraction_prompt(run_id: str, component_key: str):
    try:
        prompt = build_theme_abstraction_prompt(run_id, component_key)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return PlainTextResponse(
        prompt["prompt_text"],
        headers={
            "Content-Disposition": (
                f'attachment; filename="theme-abstraction-{component_key}-{run_id[:8]}.md"'
            )
        },
    )


@router.post("/runs/{run_id}/theme-abstraction/results")
async def submit_theme_abstraction_result(
    run_id: str,
    component_key: str = Form(...),
    raw_output_text: str = Form(""),
    markdown_file: UploadFile | None = File(None),
):
    if markdown_file is not None and markdown_file.filename:
        if raw_output_text.strip():
            raise HTTPException(status_code=400, detail="متن را جای‌گذاری کنید یا فایل Markdown بدهید، نه هر دو.")
        if not markdown_file.filename.lower().endswith((".md", ".markdown")):
            raise HTTPException(status_code=400, detail="فقط فایل Markdown با پسوند md یا markdown پذیرفته می‌شود.")
        content = await markdown_file.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="حجم فایل Markdown باید کمتر از ۵ مگابایت باشد.")
        try:
            raw_output_text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise HTTPException(status_code=400, detail="فایل Markdown باید با کدگذاری UTF-8 باشد.") from error
    try:
        result = submit_theme_abstraction_output(run_id, component_key, raw_output_text)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    next_component = (
        next_theme_abstraction_component(run_id, component_key)
        if result["status"] != "invalid"
        else None
    )
    params = {
        "component_key": next_component or component_key,
    }
    if result["status"] != "invalid":
        params["received"] = component_key
    return RedirectResponse(
        f"/synthesis/runs/{run_id}/theme-abstraction?{urlencode(params)}",
        status_code=303,
    )


@router.post("/runs/{run_id}/theme-abstraction/review")
def submit_theme_abstraction_review(
    run_id: str,
    decision: ThemeAbstractionReviewDecision,
):
    try:
        result = record_theme_abstraction_review_decision(
            run_id,
            component_key=decision.component_key,
            decision=decision.decision,
            reason=decision.reason,
            theme_id=decision.theme_id,
            target_theme_id=decision.target_theme_id,
            integrated_category_id=decision.integrated_category_id,
            title=decision.title,
            definition=decision.definition,
            rationale=decision.rationale,
            abstraction_level=decision.abstraction_level,
            preserved_dimensions=decision.preserved_dimensions,
            partitions=decision.partitions,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    redirect_url = None
    if decision.decision == "approve_final":
        approved = get_approved_theme_abstractions(run_id)
        if set(approved) == set(POOL_COMPONENT_KEYS):
            redirect_url = f"/synthesis/runs/{run_id}/component-synthesis"
        else:
            next_component = next_theme_abstraction_component(
                run_id,
                decision.component_key,
                only_unapproved=True,
            )
            if next_component:
                redirect_url = (
                    f"/synthesis/runs/{run_id}/theme-abstraction?"
                    f"{urlencode({'component_key': next_component, 'approved': decision.component_key})}"
                )
    return JSONResponse({
        "stage_result_id": result["stage_result_id"],
        "version": result["version"],
        "status": result["status"],
        "approval_status": result["approval_status"],
        "redirect_url": redirect_url,
    })


@router.get("/runs/{run_id}/component-synthesis")
def component_synthesis_page(request: Request, run_id: str):
    try:
        context = component_synthesis_page_data(run_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if context["complete"]:
        return RedirectResponse(f"/synthesis/runs/{run_id}/component-synthesis/final", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="component_synthesis.html",
        context={"request": request, **context},
    )


@router.get("/runs/{run_id}/component-synthesis/prompt.md")
def download_component_synthesis_prompt(run_id: str, component_key: str):
    try:
        prompt = build_component_prompt(run_id, component_key)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return PlainTextResponse(
        prompt["prompt_text"],
        headers={
            "Content-Disposition": (
                f'attachment; filename="component-{component_key}-{run_id[:8]}.md"'
            )
        },
    )


@router.post("/runs/{run_id}/component-synthesis/results")
async def submit_component_synthesis_result(
    run_id: str,
    component_key: str = Form(...),
    raw_output_text: str = Form(""),
    markdown_file: UploadFile | None = File(None),
):
    if markdown_file is not None and markdown_file.filename:
        if raw_output_text.strip():
            raise HTTPException(status_code=400, detail="متن را جای‌گذاری کنید یا فایل Markdown بدهید، نه هر دو.")
        if not markdown_file.filename.lower().endswith((".md", ".markdown")):
            raise HTTPException(status_code=400, detail="فقط فایل Markdown با پسوند md یا markdown پذیرفته می‌شود.")
        content = await markdown_file.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="حجم فایل Markdown باید کمتر از ۵ مگابایت باشد.")
        try:
            raw_output_text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise HTTPException(status_code=400, detail="فایل Markdown باید با کدگذاری UTF-8 باشد.") from error
    try:
        submit_component_output(run_id, component_key, raw_output_text)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return RedirectResponse(f"/synthesis/runs/{run_id}/component-synthesis", status_code=303)


@router.get("/runs/{run_id}/component-synthesis/final")
def component_synthesis_final_page(request: Request, run_id: str):
    try:
        context = component_synthesis_final_data(run_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return templates.TemplateResponse(
        request=request,
        name="component_synthesis_final.html",
        context={"request": request, **context},
    )


@router.get("/runs/{run_id}/component-synthesis/final/coding-table.{fmt}")
def export_component_synthesis_coding_table(run_id: str, fmt: str):
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=422, detail="فرمت خروجی باید CSV، XLSX یا DOCX باشد.")
    try:
        table = component_synthesis_final_data(run_id)["coding_table"]
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    build, media_type = EXPORT_FORMATS[fmt]
    return Response(
        build(table),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="coding-table-{run_id[:8]}.{fmt}"'},
    )


@router.get("/runs/{run_id}/category-grouping")
def category_grouping_page(request: Request, run_id: str):
    try:
        context = grouping_page_data(run_id)
        context["page_error"] = None
    except PromptSizeLimitError as error:
        context = {
            "prompt": None,
            "latest": latest_grouping_result(run_id),
            "source_items": {},
            "evidence_by_id": {},
            "page_error": str(error),
            "size_error_report": error.report,
        }
    except ValueError as error:
        return RedirectResponse(
            f"/synthesis/runs/{run_id}/pool-preview?{urlencode({'notice': 'ابتدا باید ظروف شش‌گانه برای این اجرا آماده شوند.'})}",
            status_code=303,
        )
    return templates.TemplateResponse(
        request=request,
        name="category_grouping.html",
        context={"request": request, **context},
    )


@router.post("/runs/{run_id}/category-grouping/results")
async def submit_category_grouping_result(
    run_id: str,
    raw_output_text: str = Form(""),
    markdown_file: UploadFile | None = File(None),
):
    if markdown_file is not None and markdown_file.filename:
        if raw_output_text.strip():
            raise HTTPException(status_code=400, detail="متن را جای‌گذاری کنید یا فایل Markdown بدهید، نه هر دو.")
        if not markdown_file.filename.lower().endswith((".md", ".markdown")):
            raise HTTPException(status_code=400, detail="فقط فایل Markdown با پسوند md یا markdown پذیرفته می‌شود.")
        try:
            raw_output_text = (await markdown_file.read()).decode("utf-8")
        except UnicodeDecodeError as error:
            raise HTTPException(status_code=400, detail="فایل Markdown باید با کدگذاری UTF-8 باشد.") from error
    try:
        submit_grouping_output(run_id, raw_output_text)
    except PromptSizeLimitError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return RedirectResponse(f"/synthesis/runs/{run_id}/category-grouping", status_code=303)


@router.get("/runs/{run_id}/category-grouping/correction-prompt")
def category_grouping_correction_prompt(run_id: str):
    try:
        return PlainTextResponse(correction_prompt(run_id), media_type="text/markdown; charset=utf-8")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/runs/{run_id}/category-grouping/review")
def submit_category_review_decision(run_id: str, decision: CategoryReviewDecision):
    try:
        result = record_category_review_decision(
            run_id,
            decision=decision.decision,
            reason=decision.reason,
            group_id=decision.group_id,
            target_group_id=decision.target_group_id,
            source_item_id=decision.source_item_id,
            proposed_category_name=decision.proposed_category_name,
            definition=decision.definition,
            partitions=decision.partitions,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return JSONResponse({
        "stage_result_id": result["stage_result_id"],
        "version": result["version"],
        "status": result["status"],
        "approval_status": result["approval_status"],
    })


@router.get("/runs/{run_id}/theme-synthesis")
def theme_synthesis_page(request: Request, run_id: str):
    try:
        context = theme_synthesis_page_data(run_id)
    except ThemePromptSizeLimitError as error:
        context = {
            "prompt": None,
            "latest": None,
            "page_error": str(error),
            "size_error_report": error.report,
            "units_by_id": {},
            "evidence_by_id": {},
        }
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if context.get("prompt"):
        context["units_by_id"] = {
            unit["category_unit_id"]: unit
            for unit in context["prompt"]["category_units"]
        }
    return templates.TemplateResponse(
        request=request,
        name="theme_synthesis.html",
        context={"request": request, **context},
    )


@router.post("/runs/{run_id}/theme-synthesis/results")
async def submit_theme_synthesis_result(
    run_id: str,
    raw_output_text: str = Form(""),
    markdown_file: UploadFile | None = File(None),
):
    if markdown_file is not None and markdown_file.filename:
        if raw_output_text.strip():
            raise HTTPException(status_code=400, detail="متن را جای‌گذاری کنید یا فایل Markdown بدهید، نه هر دو.")
        if not markdown_file.filename.lower().endswith((".md", ".markdown")):
            raise HTTPException(status_code=400, detail="فقط فایل Markdown با پسوند md یا markdown پذیرفته می‌شود.")
        try:
            raw_output_text = (await markdown_file.read()).decode("utf-8")
        except UnicodeDecodeError as error:
            raise HTTPException(status_code=400, detail="فایل Markdown باید با کدگذاری UTF-8 باشد.") from error
    try:
        submit_theme_synthesis_output(run_id, raw_output_text)
    except ThemePromptSizeLimitError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return RedirectResponse(f"/synthesis/runs/{run_id}/theme-synthesis", status_code=303)


@router.get("/runs/{run_id}/theme-synthesis/correction-prompt")
def theme_synthesis_correction_prompt(run_id: str):
    try:
        return PlainTextResponse(
            theme_correction_prompt(run_id),
            media_type="text/markdown; charset=utf-8",
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/runs/{run_id}/theme-synthesis/review")
def submit_theme_review_decision(run_id: str, decision: ThemeReviewDecision):
    try:
        result = record_theme_review_decision(
            run_id,
            decision=decision.decision,
            reason=decision.reason,
            theme_id=decision.theme_id,
            target_theme_id=decision.target_theme_id,
            contradiction_id=decision.contradiction_id,
            category_unit_id=decision.category_unit_id,
            proposed_theme_name=decision.proposed_theme_name,
            definition=decision.definition,
            reasoning=decision.reasoning,
            conceptual_scope=decision.conceptual_scope,
            partitions=decision.partitions,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return JSONResponse({
        "stage_result_id": result["stage_result_id"],
        "version": result["version"],
        "status": result["status"],
        "approval_status": result["approval_status"],
    })


@router.get("/runs/{run_id}/phenomenon-synthesis")
def phenomenon_synthesis_page(request: Request, run_id: str):
    context = phenomenon_page_data(run_id)
    return templates.TemplateResponse(
        request=request,
        name="phenomenon_synthesis.html",
        context={"request": request, **context},
    )


@router.post("/runs/{run_id}/phenomenon-synthesis/results")
async def submit_phenomenon_synthesis_result(
    run_id: str,
    raw_output_text: str = Form(""),
    markdown_file: UploadFile | None = File(None),
):
    if markdown_file is not None and markdown_file.filename:
        if raw_output_text.strip():
            raise HTTPException(status_code=400, detail="متن را جای‌گذاری کنید یا فایل Markdown بدهید، نه هر دو.")
        if not markdown_file.filename.lower().endswith((".md", ".markdown")):
            raise HTTPException(status_code=400, detail="فقط فایل Markdown با پسوند md یا markdown پذیرفته می‌شود.")
        try:
            raw_output_text = (await markdown_file.read()).decode("utf-8")
        except UnicodeDecodeError as error:
            raise HTTPException(status_code=400, detail="فایل Markdown باید با کدگذاری UTF-8 باشد.") from error
    try:
        submit_phenomenon_output(run_id, raw_output_text)
    except FinalSynthesisPromptSizeLimitError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return RedirectResponse(f"/synthesis/runs/{run_id}/phenomenon-synthesis", status_code=303)


@router.get("/runs/{run_id}/phenomenon-synthesis/correction-prompt")
def phenomenon_synthesis_correction_prompt(run_id: str):
    try:
        return PlainTextResponse(
            phenomenon_correction_prompt(run_id),
            media_type="text/markdown; charset=utf-8",
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/runs/{run_id}/phenomenon-synthesis/review")
def submit_phenomenon_review_decision(run_id: str, decision: PhenomenonReviewDecision):
    try:
        result = record_phenomenon_review_decision(
            run_id,
            decision=decision.decision,
            reason=decision.reason,
            target_id=decision.target_id,
            title=decision.title,
            definition=decision.definition,
            selection_reason=decision.selection_reason,
            description=decision.description,
            reasoning=decision.reasoning,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return JSONResponse({
        "stage_result_id": result["stage_result_id"],
        "version": result["version"],
        "status": result["status"],
        "approval_status": result["approval_status"],
    })


@router.get("/runs/{run_id}/relationship-synthesis")
def relationship_synthesis_page(request: Request, run_id: str):
    context = relationship_page_data(run_id)
    return templates.TemplateResponse(
        request=request,
        name="relationship_synthesis.html",
        context={"request": request, **context},
    )


@router.post("/runs/{run_id}/relationship-synthesis/results")
async def submit_relationship_synthesis_result(
    run_id: str,
    raw_output_text: str = Form(""),
    markdown_file: UploadFile | None = File(None),
):
    if markdown_file is not None and markdown_file.filename:
        if raw_output_text.strip():
            raise HTTPException(status_code=400, detail="متن را جای‌گذاری کنید یا فایل Markdown بدهید، نه هر دو.")
        if not markdown_file.filename.lower().endswith((".md", ".markdown")):
            raise HTTPException(status_code=400, detail="فقط فایل Markdown با پسوند md یا markdown پذیرفته می‌شود.")
        try:
            raw_output_text = (await markdown_file.read()).decode("utf-8")
        except UnicodeDecodeError as error:
            raise HTTPException(status_code=400, detail="فایل Markdown باید با کدگذاری UTF-8 باشد.") from error
    try:
        submit_relationship_output(run_id, raw_output_text)
    except FinalSynthesisPromptSizeLimitError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return RedirectResponse(f"/synthesis/runs/{run_id}/relationship-synthesis", status_code=303)


@router.get("/runs/{run_id}/relationship-synthesis/correction-prompt")
def relationship_synthesis_correction_prompt(run_id: str):
    try:
        return PlainTextResponse(
            relationship_correction_prompt(run_id),
            media_type="text/markdown; charset=utf-8",
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/runs/{run_id}/relationship-synthesis/review")
def submit_relationship_review_decision(run_id: str, decision: RelationshipReviewDecision):
    try:
        result = record_relationship_review_decision(
            run_id,
            decision=decision.decision,
            reason=decision.reason,
            target_id=decision.target_id,
            source_node_id=decision.source_node_id,
            target_node_id=decision.target_node_id,
            relationship_type=decision.relationship_type,
            direction=decision.direction,
            description=decision.description,
            reasoning=decision.reasoning,
            supporting_source_relationship_ids=decision.supporting_source_relationship_ids,
            supporting_source_phenomenon_ids=decision.supporting_source_phenomenon_ids,
            supporting_source_item_ids=decision.supporting_source_item_ids,
            supporting_category_unit_ids=decision.supporting_category_unit_ids,
            evidence_ref_ids=decision.evidence_ref_ids,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return JSONResponse({
        "stage_result_id": result["stage_result_id"],
        "version": result["version"],
        "status": result["status"],
        "approval_status": result["approval_status"],
    })


@router.get("/runs/{run_id}/final-model")
def final_model_page(request: Request, run_id: str):
    context = final_model_page_data(run_id)
    latest = context.get("latest")
    diagram = None
    coding_table = None
    if latest and context.get("latest_is_current") and latest.get("approval_status") == "approved":
        diagram = prepare_final_model_diagram(latest["output_json"])
        coding_table = build_synthesis_coding_table(run_id, latest["output_json"])
    return templates.TemplateResponse(
        request=request,
        name="final_model.html",
        context={"request": request, **context, "diagram": diagram, "coding_table": coding_table},
    )


@router.post("/runs/{run_id}/final-model/layout")
def save_final_model_layout_route(run_id: str, payload: FinalModelLayoutPayload):
    try:
        result = save_final_model_layout(run_id, payload.positions)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return JSONResponse({
        "version": result["version"],
        "status": result["status"],
        "approval_status": result["approval_status"],
    })


@router.get("/runs/{run_id}/final-model/coding-table.{fmt}")
def export_final_model_coding_table(run_id: str, fmt: str):
    latest = latest_final_model(run_id)
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=422, detail="فرمت خروجی باید CSV، XLSX یا DOCX باشد.")
    if latest is None or latest.get("stale") or latest.get("approval_status") != "approved":
        raise HTTPException(status_code=409, detail="مدل نهایی معتبر و به‌روز برای دریافت جدول وجود ندارد.")
    build, media_type = EXPORT_FORMATS[fmt]
    return Response(
        build(build_synthesis_coding_table(run_id, latest["output_json"])),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="coding-table-{run_id[:8]}.{fmt}"'},
    )


@router.get("/runs/{run_id}/final-model/export.docx")
def export_final_model_docx(run_id: str):
    latest = latest_final_model(run_id)
    if latest is None or latest.get("stale") or latest.get("approval_status") != "approved":
        raise HTTPException(status_code=409, detail="مدل نهایی معتبر و به‌روز برای دریافت وجود ندارد.")
    model = latest["output_json"]
    diagram = prepare_final_model_diagram(model)
    try:
        diagram_png = render_paradigm_diagram(diagram, "png")
    except ExecutableNotFound as error:
        raise HTTPException(
            status_code=503,
            detail="Graphviz روی سرور نصب نیست؛ امکان ساخت فایل Word کامل وجود ندارد.",
        ) from error
    table = build_synthesis_coding_table(run_id, model)
    hierarchy_rows = build_final_model_hierarchy_rows(run_id, model)
    return Response(
        content=final_model_to_docx(table, model, diagram_png, hierarchy_rows),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="final-model-{run_id[:8]}.docx"'
        },
    )


@router.get("/runs/{run_id}/final-model/export.xlsx")
def export_final_model_excel(run_id: str):
    latest = latest_final_model(run_id)
    if latest is None or latest.get("stale") or latest.get("approval_status") != "approved":
        raise HTTPException(status_code=409, detail="مدل نهایی معتبر و به‌روز برای دریافت Excel وجود ندارد.")
    model = latest["output_json"]
    hierarchy_rows = build_final_model_hierarchy_rows(run_id, model)
    return Response(
        content=build_stage5_excel(model, hierarchy_rows),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="final-model-{run_id[:8]}.xlsx"'
        },
    )


@router.post("/runs/{run_id}/final-model")
def create_final_model(run_id: str):
    try:
        build_final_model(run_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return RedirectResponse(f"/synthesis/runs/{run_id}/final-model", status_code=303)


@router.get("/runs/{run_id}/final-model/export/{output_format}")
def export_final_model_diagram(run_id: str, output_format: str):
    if output_format not in {"png", "svg"}:
        raise HTTPException(status_code=422, detail="فرمت خروجی باید PNG یا SVG باشد.")
    latest = latest_final_model(run_id)
    if latest is None or latest.get("stale") or latest.get("approval_status") != "approved":
        raise HTTPException(status_code=409, detail="مدل نهایی معتبر و به‌روز برای دریافت نمودار وجود ندارد.")
    diagram = prepare_final_model_diagram(latest["output_json"])
    try:
        image = render_paradigm_diagram(diagram, output_format)
    except ExecutableNotFound as error:
        raise HTTPException(
            status_code=503,
            detail="Graphviz روی سرور نصب نیست؛ امکان ساخت فایل نمودار وجود ندارد.",
        ) from error
    return Response(
        content=image,
        media_type="image/svg+xml" if output_format == "svg" else "image/png",
        headers={
            "Content-Disposition": f'attachment; filename="final-paradigm-{run_id}.{output_format}"'
        },
    )


@router.post("/runs/{run_id}/pool-review")
def submit_pool_review_decision(run_id: str, decision: PoolReviewDecision):
    try:
        return record_pool_review_decision(
            run_id,
            category_id=decision.category_id,
            source_project_id=decision.source_project_id,
            review_item_id=decision.review_item_id,
            decision=decision.decision,
            reason=decision.reason,
            approved=decision.approved,
            pool_version=decision.pool_version,
            component_key=decision.component_key,
            replacement_category_id=decision.replacement_category_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/runs/{run_id}/pool-review/decision")
def submit_pool_review_form(
    run_id: str,
    category_id: str = Form(""),
    source_project_id: str = Form(...),
    review_item_id: str = Form(...),
    decision: str = Form(...),
    reason: str = Form(""),
    pool_version: int = Form(...),
    component_key: str = Form(""),
    replacement_category_id: str = Form(""),
):
    try:
        record_pool_review_decision(
            run_id,
            category_id=category_id,
            source_project_id=source_project_id,
            review_item_id=review_item_id,
            decision=decision,
            reason=reason,
            approved=True,
            pool_version=pool_version,
            component_key=component_key or None,
            replacement_category_id=replacement_category_id or None,
        )
    except ValueError as error:
        return RedirectResponse(
            f"/synthesis/runs/{run_id}/pool-preview?{urlencode({'error': str(error)[:300]})}", status_code=303
        )
    return RedirectResponse(f"/synthesis/runs/{run_id}/pool-preview", status_code=303)


@router.post("/runs/{run_id}/pool-review/force-proceed")
def force_pool_review_proceed(
    run_id: str,
    reason: str = Form(...),
    confirmed: bool = Form(False),
):
    if not confirmed:
        return RedirectResponse(
            f"/synthesis/runs/{run_id}/pool-preview?{urlencode({'error': 'برای ادامهٔ اجباری باید تأیید پژوهشگر را ثبت کنید.'})}",
            status_code=303,
        )
    try:
        record_pool_force_proceed(run_id, reason=reason)
    except ValueError as error:
        return RedirectResponse(
            f"/synthesis/runs/{run_id}/pool-preview?{urlencode({'error': str(error)[:250]})}",
            status_code=303,
        )
    return RedirectResponse(f"/synthesis/runs/{run_id}/pool-preview", status_code=303)
