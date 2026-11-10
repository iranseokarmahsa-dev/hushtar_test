from fastapi.testclient import TestClient

from app import main
from app.main import app
from app.routers import projects as projects_router


def test_history_and_delete_are_public_in_production(monkeypatch):
	monkeypatch.setattr(main, "ENVIRONMENT", "production")
	monkeypatch.setattr(main, "ADMIN_PASSWORD", "")
	project = {
		"project_id": "interview-1",
		"title": "مصاحبه آزمایشی",
		"topic": "موضوع",
		"created_at": "2026-11-10T00:00:00+00:00",
		"analysis": None,
		"paragraphs": [],
	}
	monkeypatch.setattr(projects_router, "list_projects", lambda: [project])
	monkeypatch.setattr(
		projects_router,
		"summarize_project_eligibility",
		lambda _project: {"eligible": False, "status": "نامعتبر", "reason": "آزمون"},
	)
	deleted_ids = []
	monkeypatch.setattr(
		projects_router,
		"delete_project",
		lambda project_id: deleted_ids.append(project_id) or True,
	)
	client = TestClient(app)

	history = client.get("/projects")
	deletion = client.post("/projects/interview-1/delete", follow_redirects=False)

	assert history.status_code == 200
	assert "مصاحبه آزمایشی" in history.text
	assert 'formaction="/projects/interview-1/delete"' in history.text
	assert deletion.status_code == 303
	assert deletion.headers["location"] == "/projects"
	assert deleted_ids == ["interview-1"]
	assert client.get("/projects/interview-1").status_code == 503
