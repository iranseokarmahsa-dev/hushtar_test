import sqlite3

import pytest

from scripts.export_database import backup_database


def test_backup_database_creates_valid_snapshot(tmp_path):
	source = tmp_path / "source.sqlite3"
	destination = tmp_path / "transfer" / "snapshot.sqlite3"
	with sqlite3.connect(source) as connection:
		connection.execute("CREATE TABLE projects (project_id TEXT PRIMARY KEY)")
		connection.executemany(
			"INSERT INTO projects (project_id) VALUES (?)",
			[("project-1",), ("project-2",)],
		)

	assert backup_database(source, destination) == 2
	with sqlite3.connect(destination) as connection:
		assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
		assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 2


def test_backup_database_does_not_overwrite_existing_file(tmp_path):
	source = tmp_path / "source.sqlite3"
	destination = tmp_path / "snapshot.sqlite3"
	with sqlite3.connect(source) as connection:
		connection.execute("CREATE TABLE projects (project_id TEXT PRIMARY KEY)")
	destination.write_bytes(b"keep this backup")

	with pytest.raises(FileExistsError):
		backup_database(source, destination)

	assert destination.read_bytes() == b"keep this backup"
