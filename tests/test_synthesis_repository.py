import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import database as database_module
from app.core.database import create_project, init_db
from app.repositories.synthesis_repository import (
    create_synthesis_run,
    get_next_stage_version,
    get_run_sources,
    get_synthesis_run,
    list_stage_results,
    list_synthesis_runs,
    save_run_sources,
    save_stage_result,
    update_run_status,
)


def _reset_db(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("HOOSHYAR_DATA_DIR", str(data_dir))
    database_module.DATA_DIR = data_dir
    database_module.DATABASE_PATH = data_dir / "hooshyar.sqlite3"
    init_db()
    return data_dir


def test_create_and_get_run(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)

    run = create_synthesis_run(
        title="تحلیل تلفیقی آزمایشی",
        study_topic="تجربه‌های کاری",
    )

    assert run["title"] == "تحلیل تلفیقی آزمایشی"
    assert run["study_topic"] == "تجربه‌های کاری"
    assert run["expected_source_count"] == 0
    assert run["selected_source_count"] == 0
    assert run["status"] == "draft"
    assert run["current_stage"] == "source_selection"
    assert run["schema_version"] == "1.0"

    persisted = get_synthesis_run(run["run_id"])
    assert persisted["run_id"] == run["run_id"]


def test_save_run_sources_and_snapshot_persistence(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    p1 = create_project("مصاحبه اول", "مبحث اول")
    p2 = create_project("مصاحبه دوم", "مبحث دوم")
    run = create_synthesis_run(title="تحلیل", study_topic="موضوع")

    saved = save_run_sources(run["run_id"], [p1["project_id"], p2["project_id"]])

    assert len(saved) == 2
    assert len(get_run_sources(run["run_id"])) == 2
    assert get_run_sources(run["run_id"])[0]["source_title"] == "مصاحبه اول"
    assert json.loads(get_run_sources(run["run_id"])[0]["snapshot_json"])["project_id"] == p1["project_id"]

    delete_result = database_module.delete_project(p1["project_id"])
    assert delete_result is True
    snapshot_after_delete = get_run_sources(run["run_id"])[0]["snapshot_json"]
    assert json.loads(snapshot_after_delete)["project_id"] == p1["project_id"]
    assert json.loads(snapshot_after_delete)["title"] == "مصاحبه اول"


def test_stage_versions_and_unique_stage_component(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    run = create_synthesis_run(title="تحلیل", study_topic="موضوع")

    first = save_stage_result(run["run_id"], "semantic_grouping", "categories", prompt_text="مرحله اول", raw_output_text="نتیجه اول")
    second = save_stage_result(run["run_id"], "semantic_grouping", "themes", prompt_text="مرحله دوم", raw_output_text="نتیجه دوم")
    with pytest.raises(ValueError):
        save_stage_result(
            run["run_id"],
            "semantic_grouping",
            "categories",
            version=1,
            prompt_text="تکراری",
            raw_output_text="تکراری",
        )

    assert first["version"] == 1
    assert second["version"] == 1
    assert first["stage_key"] == "semantic_grouping"
    assert second["component_key"] == "themes"
    assert len(list_stage_results(run["run_id"])) == 2

    assert get_next_stage_version(run["run_id"], "semantic_grouping", "categories") == 2
    assert get_next_stage_version(run["run_id"], "theme_extraction", "themes") == 1


def test_duplicate_source_prevented_and_rollback_on_error(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    run = create_synthesis_run(title="تحلیل", study_topic="موضوع")
    project = create_project("مصاحبه گزینشی", "گروه اساسی")

    saved = save_run_sources(run["run_id"], [project["project_id"]])
    assert len(saved) == 1

    with pytest.raises(ValueError):
        save_run_sources(run["run_id"], [project["project_id"], project["project_id"]])

    assert len(get_run_sources(run["run_id"])) == 1

    with pytest.raises(ValueError):
        save_run_sources(run["run_id"], [project["project_id"], "missing-project-id"])

    assert len(get_run_sources(run["run_id"])) == 1


def test_init_db_idempotent_and_projects_are_preserved(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    project = create_project("پروژه اصلی", "موضوع اصلی")
    init_db()
    init_db()

    with sqlite3.connect(database_module.DATABASE_PATH) as connection:
        table_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('projects','synthesis_runs','synthesis_run_sources','synthesis_stage_results')"
        ).fetchone()[0]
        projects_columns = connection.execute("PRAGMA table_info(projects)").fetchall()
        project_row = connection.execute(
            "SELECT title, topic FROM projects WHERE project_id = ?",
            (project["project_id"],),
        ).fetchone()

    assert table_count == 4
    assert project_row == ("پروژه اصلی", "موضوع اصلی")
    assert any(column[1] == "project_id" for column in projects_columns)


def test_child_foreign_keys_and_run_status_updates(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    run = create_synthesis_run(title="تحلیل", study_topic="موضوع")
    update_run_status(run["run_id"], status="in_progress", current_stage="semantic_grouping")

    updated = get_synthesis_run(run["run_id"])
    assert updated["status"] == "in_progress"
    assert updated["current_stage"] == "semantic_grouping"

    with sqlite3.connect(database_module.DATABASE_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO synthesis_run_sources (run_id, source_project_id, source_title, source_hash, snapshot_json, source_order, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                ("missing-run", "proj-1", "مصاحبه", "hash", "{}", 1),
            )


def test_concurrent_version_numbers_are_unique(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    run = create_synthesis_run(title="تحلیل", study_topic="موضوع")

    def save_version(_index):
        result = save_stage_result(
            run["run_id"],
            "semantic_grouping",
            "categories",
            prompt_text=f"prompt-{_index}",
            raw_output_text=f"output-{_index}",
        )
        return result["version"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        versions = list(executor.map(save_version, range(8)))

    assert len(set(versions)) == len(versions)
    assert set(versions) == {1, 2, 3, 4, 5, 6, 7, 8}


def test_list_synthesis_runs_and_stage_results(monkeypatch, tmp_path):
    _reset_db(monkeypatch, tmp_path)
    run_one = create_synthesis_run(title="اول", study_topic="موضوع اول")
    run_two = create_synthesis_run(title="دوم", study_topic="موضوع دوم")

    save_stage_result(run_one["run_id"], "semantic_grouping", "categories", prompt_text="A", raw_output_text="B")
    save_stage_result(run_two["run_id"], "theme_extraction", "themes", prompt_text="C", raw_output_text="D")

    runs = list_synthesis_runs()
    stage_results = list_stage_results(run_one["run_id"])

    assert [item["run_id"] for item in runs] == [run_two["run_id"], run_one["run_id"]]
    assert stage_results[0]["run_id"] == run_one["run_id"]
    assert stage_results[0]["component_key"] == "categories"
