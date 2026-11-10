"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import ADMIN_PASSWORD, ENVIRONMENT, SECRET_KEY
from app.routers import analysis, auth, dashboard, exports, projects, results, synthesis

app = FastAPI(title="Hooshyar")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

for router in (auth.router, dashboard.router, projects.router, synthesis.router, analysis.router, results.router, exports.router):
    app.include_router(router)


@app.middleware("http")
async def require_admin_in_production(request, call_next):
    if ENVIRONMENT == "production":
        path_parts = request.url.path.strip("/").split("/")
        public_history_route = (
            request.method == "GET" and request.url.path.rstrip("/") == "/projects"
        ) or (
            request.method == "POST"
            and len(path_parts) == 3
            and path_parts[0] == "projects"
            and path_parts[2] == "delete"
        )
        if public_history_route:
            return await call_next(request)
        if not ADMIN_PASSWORD:
            return JSONResponse(status_code=503, content={"detail": "ADMIN_PASSWORD must be configured."})
        if request.url.path not in {"/auth/login", "/health"} and not request.url.path.startswith("/static/"):
            if not request.session.get("admin"):
                return RedirectResponse("/auth/login", status_code=303)
    return await call_next(request)


app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=ENVIRONMENT == "production")


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
