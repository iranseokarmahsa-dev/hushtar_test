"""Routes for multi-interview synthesis selection and run creation."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from graphviz.backend import ExecutableNotFound
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Any

from app.core.database import list_projects
from app.repositories.synthesis_repository import list_synthesis_runs
from app.services.synthesis_service import create_synthesis_run_from_selection, summarize_project_eligibility
from app.services.synthesis.collector_service import collect_run_pools
from app.services.synthesis.category_grouping_service import (
    correction_prompt,
    grouping_page_data,
    latest_grouping_result,
    submit_grouping_output,
)
from app.services.synthesis.category_grouping_prompt import PromptSizeLimitError
from app.services.synthesis.category_review_service import record_category_review_decision
from app.services.synthesis.review_service import record_pool_review_decision
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


@router.get("/select")
def synthesis_select_page(request: Request):
    projects = list_projects()
    eligibility = {project["project_id"]: summarize_project_eligibility(project) for project in projects}
    return templates.TemplateResponse(
        request=request,
        name="projects.html",
        context={
            "projects": projects,
            "eligibility": eligibility,
            "selection_mode": True,
        },
    )


@router.post("/runs")
def create_synthesis_run_route(
    title: str = Form(...),
    study_topic: str = Form(""),
    selected_ids: list[str] = Form([]),
    confirm_shared_topic: bool = Form(False),
):
    try:
        payload = create_synthesis_run_from_selection(
            title=title,
            study_topic=study_topic,
            selected_ids=selected_ids,
            confirm_shared_topic=confirm_shared_topic,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return JSONResponse(status_code=201, content=payload)


@router.get("/runs/{run_id}/pools")
def get_synthesis_pools(run_id: str):
    try:
        return collect_run_pools(run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


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
        raise HTTPException(status_code=409, detail=str(error)) from error
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
    if latest and context.get("latest_is_current") and latest.get("approval_status") == "approved":
        diagram = prepare_final_model_diagram(latest["output_json"])
    return templates.TemplateResponse(
        request=request,
        name="final_model.html",
        context={"request": request, **context, "diagram": diagram},
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
