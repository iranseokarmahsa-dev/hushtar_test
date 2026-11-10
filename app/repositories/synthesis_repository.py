"""Repository layer for independent cross-interview synthesis storage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.database import _connect, get_project, init_db


def _now() -> str:
	return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
	return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(raw: str | None, default: Any = None) -> Any:
	if raw in (None, ""):
		return default
	try:
		return json.loads(raw)
	except json.JSONDecodeError:
		return default


def _ensure_run_exists(run_id: str) -> dict[str, Any]:
	init_db()
	with _connect() as connection:
		row = connection.execute(
			"SELECT * FROM synthesis_runs WHERE run_id = ?",
			(run_id,),
		).fetchone()
	if row is None:
		raise ValueError("اجرای تلفیقی پیدا نشد.")
	return {
		"run_id": row["run_id"],
		"title": row["title"],
		"study_topic": row["study_topic"],
		"expected_source_count": row["expected_source_count"],
		"selected_source_count": row["selected_source_count"],
		"status": row["status"],
		"current_stage": row["current_stage"],
		"schema_version": row["schema_version"],
		"input_hash": row["input_hash"],
		"created_at": row["created_at"],
		"updated_at": row["updated_at"],
		"completed_at": row["completed_at"],
	}


def create_synthesis_run(
	title: str,
	study_topic: str = "",
	expected_source_count: int = 0,
	status: str = "draft",
	current_stage: str = "source_selection",
	schema_version: str = "1.0",
	input_hash: str = "",
) -> dict[str, Any]:
	init_db()
	run_id = uuid4().hex
	now = _now()
	with _connect() as connection:
		connection.execute(
			"""
			INSERT INTO synthesis_runs (
				run_id, title, study_topic, expected_source_count,
				selected_source_count, status, current_stage, schema_version,
				input_hash, created_at, updated_at, completed_at
			) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, NULL)
			""",
			(
				run_id,
				title.strip() or "تحلیل تلفیقی",
				study_topic.strip(),
				expected_source_count,
				status,
				current_stage,
				schema_version,
				input_hash,
				now,
				now,
			),
		)
	return get_synthesis_run(run_id)


def get_synthesis_run(run_id: str) -> dict[str, Any] | None:
	init_db()
	with _connect() as connection:
		row = connection.execute(
			"SELECT * FROM synthesis_runs WHERE run_id = ?",
			(run_id,),
		).fetchone()
	if row is None:
		return None
	return {
		"run_id": row["run_id"],
		"title": row["title"],
		"study_topic": row["study_topic"],
		"expected_source_count": row["expected_source_count"],
		"selected_source_count": row["selected_source_count"],
		"status": row["status"],
		"current_stage": row["current_stage"],
		"schema_version": row["schema_version"],
		"input_hash": row["input_hash"],
		"created_at": row["created_at"],
		"updated_at": row["updated_at"],
		"completed_at": row["completed_at"],
	}


def list_synthesis_runs() -> list[dict[str, Any]]:
	init_db()
	with _connect() as connection:
		rows = connection.execute(
			"SELECT * FROM synthesis_runs ORDER BY updated_at DESC, created_at DESC"
		).fetchall()
	return [
		{
			"run_id": row["run_id"],
			"title": row["title"],
			"study_topic": row["study_topic"],
			"expected_source_count": row["expected_source_count"],
			"selected_source_count": row["selected_source_count"],
			"status": row["status"],
			"current_stage": row["current_stage"],
			"schema_version": row["schema_version"],
			"input_hash": row["input_hash"],
			"created_at": row["created_at"],
			"updated_at": row["updated_at"],
			"completed_at": row["completed_at"],
		}
		for row in rows
	]


def save_run_sources(run_id: str, source_project_ids: list[str]) -> list[dict[str, Any]]:
	init_db()
	run = get_synthesis_run(run_id)
	if run is None:
		raise ValueError("اجرای تلفیقی پیدا نشد.")
	if not source_project_ids:
		return []
	unique_ids = list(dict.fromkeys(source_project_ids))
	if len(unique_ids) != len(source_project_ids):
		raise ValueError("مصاحبه‌های انتخاب‌شده نباید تکراری باشند.")
	current_rows = get_run_sources(run_id)
	current_ids = {row["source_project_id"] for row in current_rows}
	new_ids = [project_id for project_id in unique_ids if project_id not in current_ids]
	if not new_ids:
		return current_rows
	limit = run["expected_source_count"]
	if limit and len(current_rows) + len(new_ids) > limit:
		raise ValueError(f"تعداد مصاحبه‌های انتخاب‌شده از حد مجاز {limit} بیشتر می‌شود.")
	with _connect() as connection:
		connection.execute("BEGIN IMMEDIATE")
		try:
			for index, project_id in enumerate(new_ids, start=len(current_rows) + 1):
				project = get_project(project_id)
				if project is None:
					raise ValueError(f"مصاحبه‌ای با شناسه {project_id} پیدا نشد.")
				snapshot_text = _json_dumps(project)
				source_hash = hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest()
				connection.execute(
					"""
					INSERT INTO synthesis_run_sources (
						run_id, source_project_id, source_title, source_hash,
						snapshot_json, source_order, created_at
					) VALUES (?, ?, ?, ?, ?, ?, ?)
					""",
					(
						run_id,
						project_id,
						project.get("title", "").strip() or "مصاحبه",
						source_hash,
						snapshot_text,
						index,
						_now(),
					),
				)
			updated_count = len(current_rows) + len(new_ids)
			connection.execute(
				"UPDATE synthesis_runs SET selected_source_count = ?, updated_at = ?, status = ? WHERE run_id = ?",
				(updated_count, _now(), "source_selection" if updated_count < limit else "ready", run_id),
			)
			connection.commit()
		except Exception:
			connection.rollback()
			raise
	return get_run_sources(run_id)


def get_run_sources(run_id: str) -> list[dict[str, Any]]:
	init_db()
	with _connect() as connection:
		rows = connection.execute(
			"SELECT * FROM synthesis_run_sources WHERE run_id = ? ORDER BY source_order ASC",
			(run_id,),
		).fetchall()
	return [
		{
			"id": row["id"],
			"run_id": row["run_id"],
			"source_project_id": row["source_project_id"],
			"source_title": row["source_title"],
			"source_hash": row["source_hash"],
			"snapshot_json": row["snapshot_json"],
			"snapshot": _json_loads(row["snapshot_json"], {}),
			"source_order": row["source_order"],
			"created_at": row["created_at"],
		}
		for row in rows
	]


def get_stage_result(stage_result_id: str) -> dict[str, Any] | None:
	init_db()
	with _connect() as connection:
		row = connection.execute(
			"SELECT * FROM synthesis_stage_results WHERE stage_result_id = ?",
			(stage_result_id,),
		).fetchone()
	if row is None:
		return None
	return _stage_result_row_to_dict(row)


def list_stage_results(run_id: str | None = None) -> list[dict[str, Any]]:
	init_db()
	with _connect() as connection:
		if run_id is None:
			rows = connection.execute(
				"SELECT * FROM synthesis_stage_results ORDER BY created_at ASC, stage_key ASC, component_key ASC, version ASC"
			).fetchall()
		else:
			rows = connection.execute(
				"SELECT * FROM synthesis_stage_results WHERE run_id = ? ORDER BY created_at ASC, stage_key ASC, component_key ASC, version ASC",
				(run_id,),
			).fetchall()
	return [_stage_result_row_to_dict(row) for row in rows]


def _stage_result_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
	return {
		"stage_result_id": row["stage_result_id"],
		"run_id": row["run_id"],
		"stage_key": row["stage_key"],
		"component_key": row["component_key"],
		"version": row["version"],
		"status": row["status"],
		"input_hash": row["input_hash"],
		"prompt_text": row["prompt_text"],
		"raw_output_text": row["raw_output_text"],
		"output_json": _json_loads(row["output_json"], {}),
		"validation_report_json": _json_loads(row["validation_report_json"], {}),
		"validation_report_text": row["validation_report_text"],
		"source_phase_version": row["source_phase_version"],
		"researcher_edits_json": _json_loads(row["researcher_edits_json"], {}),
		"approval_status": row["approval_status"],
		"created_at": row["created_at"],
		"updated_at": row["updated_at"],
		"approved_at": row["approved_at"],
	}


def get_next_stage_version(run_id: str, stage_key: str, component_key: str | None = None) -> int:
	init_db()
	with _connect() as connection:
		connection.execute("BEGIN IMMEDIATE")
		try:
			if component_key is None:
				row = connection.execute(
					"SELECT COALESCE(MAX(version), 0) FROM synthesis_stage_results WHERE run_id = ? AND stage_key = ?",
					(run_id, stage_key),
				).fetchone()
			else:
				row = connection.execute(
					"SELECT COALESCE(MAX(version), 0) FROM synthesis_stage_results WHERE run_id = ? AND stage_key = ? AND component_key = ?",
					(run_id, stage_key, component_key),
				).fetchone()
			result = int(row[0]) + 1
			connection.commit()
			return result
		except Exception:
			connection.rollback()
			raise


def save_stage_result(
	run_id: str,
	stage_key: str,
	component_key: str,
	*,
	version: int | None = None,
	status: str = "draft",
	input_hash: str = "",
	prompt_text: str = "",
	raw_output_text: str = "",
	output_json: Any | None = None,
	validation_report_json: Any | None = None,
	validation_report_text: str = "",
	source_phase_version: int | None = None,
	researcher_edits_json: Any | None = None,
	approval_status: str = "pending",
) -> dict[str, Any]:
	init_db()
	_ensure_run_exists(run_id)
	with _connect() as connection:
		connection.execute("BEGIN IMMEDIATE")
		try:
			if version is None:
				row = connection.execute(
					"SELECT COALESCE(MAX(version), 0) FROM synthesis_stage_results WHERE run_id = ? AND stage_key = ? AND component_key = ?",
					(run_id, stage_key, component_key),
				).fetchone()
				version = int(row[0]) + 1
			if connection.execute(
				"SELECT 1 FROM synthesis_stage_results WHERE run_id = ? AND stage_key = ? AND component_key = ? AND version = ?",
				(run_id, stage_key, component_key, version),
			).fetchone():
				raise ValueError(
					f"نسخه {version} برای مرحله {stage_key} و مؤلفه {component_key} در اجرای {run_id} از قبل وجود دارد."
				)
			stage_result_id = uuid4().hex
			now = _now()
			payload = output_json if output_json is not None else {}
			validation_report = validation_report_json if validation_report_json is not None else {}
			researcher_edits = researcher_edits_json if researcher_edits_json is not None else {}
			input_value = input_hash or hashlib.sha256(
				(json.dumps({
					"prompt_text": prompt_text,
					"raw_output_text": raw_output_text,
					"output_json": payload,
				}, ensure_ascii=False, sort_keys=True).encode("utf-8"))
			).hexdigest()
			connection.execute(
				"""
				INSERT INTO synthesis_stage_results (
					stage_result_id, run_id, stage_key, component_key, version,
					status, input_hash, prompt_text, raw_output_text,
					output_json, validation_report_json, validation_report_text, source_phase_version, researcher_edits_json,
					approval_status, created_at, updated_at, approved_at
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
				""",
				(
					stage_result_id,
					run_id,
					stage_key,
					component_key,
					version,
					status,
					input_value,
					prompt_text,
					raw_output_text,
					_json_dumps(payload),
					_json_dumps(validation_report),
					validation_report_text,
					source_phase_version,
					_json_dumps(researcher_edits),
					approval_status,
					now,
					now,
					now if approval_status == "approved" else None,
				),
			)
			connection.execute(
				"UPDATE synthesis_runs SET updated_at = ? WHERE run_id = ?",
				(now, run_id),
			)
			connection.commit()
		except Exception:
			connection.rollback()
			raise
	return get_stage_result(stage_result_id)


def update_run_status(
	run_id: str,
	*,
	status: str | None = None,
	current_stage: str | None = None,
	completed_at: str | None = None,
	selected_source_count: int | None = None,
) -> dict[str, Any] | None:
	init_db()
	run = get_synthesis_run(run_id)
	if run is None:
		return None
	changes: list[str] = []
	values: list[Any] = []
	if status is not None:
		changes.append("status = ?")
		values.append(status)
	if current_stage is not None:
		changes.append("current_stage = ?")
		values.append(current_stage)
	if completed_at is not None:
		changes.append("completed_at = ?")
		values.append(completed_at)
	if selected_source_count is not None:
		changes.append("selected_source_count = ?")
		values.append(selected_source_count)
	if not changes:
		return run
	changes.append("updated_at = ?")
	values.extend([_now(), run_id])
	with _connect() as connection:
		connection.execute(
			f"UPDATE synthesis_runs SET {', '.join(changes)} WHERE run_id = ?",
			values,
		)
	return get_synthesis_run(run_id)


def mark_stage_results_stale(run_id: str, stage_keys: tuple[str, ...]) -> None:
	if not stage_keys:
		return
	init_db()
	placeholders = ", ".join("?" for _ in stage_keys)
	with _connect() as connection:
		connection.execute(
			f"""
			UPDATE synthesis_stage_results
			SET status = 'stale', approval_status = 'stale', updated_at = ?
			WHERE run_id = ? AND stage_key IN ({placeholders})
			""",
			(_now(), run_id, *stage_keys),
		)


def mark_synthesis_runs_stale_for_source(source_project_id: str) -> list[str]:
	"""Invalidate frozen synthesis runs when a selected interview is edited."""
	init_db()
	with _connect() as connection:
		run_ids = [
			row["run_id"]
			for row in connection.execute(
				"SELECT run_id FROM synthesis_run_sources WHERE source_project_id = ?",
				(source_project_id,),
			).fetchall()
		]
		if not run_ids:
			return []
		now = _now()
		stage_keys = (
			"pool_extraction",
			"category_grouping",
			"theme_synthesis",
			"phenomenon_synthesis",
			"relationship_synthesis",
			"final_model",
		)
		placeholders = ", ".join("?" for _ in stage_keys)
		for run_id in run_ids:
			connection.execute(
				f"""
				UPDATE synthesis_stage_results
				SET status = 'stale', approval_status = 'stale', updated_at = ?
				WHERE run_id = ? AND stage_key IN ({placeholders})
				""",
				(now, run_id, *stage_keys),
			)
		run_placeholders = ", ".join("?" for _ in run_ids)
		connection.execute(
			f"""
			UPDATE synthesis_runs
			SET status = 'source_stale', current_stage = 'source_snapshot_stale', updated_at = ?
			WHERE run_id IN ({run_placeholders})
			""",
			(now, *run_ids),
		)
	return run_ids
