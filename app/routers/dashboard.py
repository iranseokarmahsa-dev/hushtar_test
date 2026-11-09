"""Dashboard and project overview."""

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", include_in_schema=False)
def dashboard(request: Request):
	return templates.TemplateResponse(
		request=request,
		name="dashboard.html",
		context={},
	)
