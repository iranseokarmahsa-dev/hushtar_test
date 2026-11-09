"""Admin login and logout routes."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import ENVIRONMENT
from app.core.security import verify_admin_credentials

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login")
def login_page(request: Request, error: bool = False):
	return templates.TemplateResponse(request=request, name="login.html", context={"error": error})


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
	if ENVIRONMENT != "production" or not verify_admin_credentials(username, password):
		return templates.TemplateResponse(
			request=request,
			name="login.html",
			context={"error": True},
			status_code=401,
		)
	request.session["admin"] = username
	return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
	request.session.clear()
	return RedirectResponse("/auth/login", status_code=303)
