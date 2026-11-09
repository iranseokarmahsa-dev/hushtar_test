"""Small SQLite repository for local research projects."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("HOOSHYAR_DATA_DIR", ROOT_DIR / "data"))
DATABASE_PATH = DATA_DIR / "hooshyar.sqlite3"
JSON_FIELDS = {
	"paragraphs": "paragraphs_json",
	"concepts": "concepts_json",
	"analysis": "analysis_json",
	"raw_analysis": "raw_analysis_json",
	"researcher_edits": "researcher_edits_json",
	"processing_issues": "processing_issues_json",
}


def _connect() -> sqlite3.Connection:
	DATA_DIR.mkdir(parents=True, exist_ok=True)
	connection = sqlite3.connect(DATABASE_PATH, timeout=15)
	connection.row_factory = sqlite3.Row
	connection.execute("PRAGMA journal_mode=WAL")
	return connection


def init_db() -> None:
	with _connect() as connection:
		connection.execute(
			"""CREATE TABLE IF NOT EXISTS projects (
				project_id TEXT PRIMARY KEY,
				title TEXT NOT NULL,
				topic TEXT NOT NULL DEFAULT '',
				created_at TEXT NOT NULL,
				updated_at TEXT NOT NULL,
				source_filename TEXT NOT NULL DEFAULT '',
				paragraphs_json TEXT NOT NULL DEFAULT '[]',
				concepts_json TEXT NOT NULL DEFAULT '[]',
				analysis_json TEXT,
				raw_analysis_json TEXT,
				researcher_edits_json TEXT NOT NULL DEFAULT '{}',
				processing_issues_json TEXT NOT NULL DEFAULT '[]'
			)"""
		)
		columns = {row["name"] for row in connection.execute("PRAGMA table_info(projects)")}
		if "processing_issues_json" not in columns:
			connection.execute(
				"ALTER TABLE projects ADD COLUMN processing_issues_json TEXT NOT NULL DEFAULT '[]'"
			)
		if "researcher_edits_json" not in columns:
			connection.execute(
				"ALTER TABLE projects ADD COLUMN researcher_edits_json TEXT NOT NULL DEFAULT '{}'"
			)
		if "raw_analysis_json" not in columns:
			connection.execute("ALTER TABLE projects ADD COLUMN raw_analysis_json TEXT")


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
	if row is None:
		return None
	project = dict(row)
	for field, column in JSON_FIELDS.items():
		raw = project.pop(column)
		project[field] = json.loads(raw) if raw is not None else None
	return project


def create_project(title: str, topic: str = "") -> dict[str, Any]:
	init_db()
	project_id = uuid4().hex
	now = datetime.now(timezone.utc).isoformat()
	with _connect() as connection:
		connection.execute(
			"INSERT INTO projects (project_id, title, topic, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
			(project_id, title.strip(), topic.strip(), now, now),
		)
	return get_project(project_id)  # type: ignore[return-value]


def get_project(project_id: str) -> dict[str, Any] | None:
	init_db()
	with _connect() as connection:
		row = connection.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
	return _decode(row)


def list_projects() -> list[dict[str, Any]]:
	init_db()
	with _connect() as connection:
		rows = connection.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
	return [_decode(row) for row in rows if row is not None]  # type: ignore[misc]


def update_project(project_id: str, **changes: Any) -> dict[str, Any] | None:
	init_db()
	allowed = {"title", "topic", "source_filename", *JSON_FIELDS}
	if not changes or changes.keys() - allowed:
		raise ValueError("Invalid project fields")
	assignments = []
	values = []
	for field, value in changes.items():
		column = JSON_FIELDS.get(field, field)
		assignments.append(f"{column} = ?")
		values.append(json.dumps(value, ensure_ascii=False) if field in JSON_FIELDS else value)
	assignments.append("updated_at = ?")
	values.extend([datetime.now(timezone.utc).isoformat(), project_id])
	with _connect() as connection:
		cursor = connection.execute(
			f"UPDATE projects SET {', '.join(assignments)} WHERE project_id = ?", values
		)
	return get_project(project_id) if cursor.rowcount else None


def delete_project(project_id: str) -> bool:
	init_db()
	with _connect() as connection:
		cursor = connection.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
	return cursor.rowcount > 0
