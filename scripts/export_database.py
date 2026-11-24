"""Create a consistent SQLite snapshot for moving Hooshyar data to another machine."""

import argparse
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
	sys.path.insert(0, str(ROOT_DIR))

from app.core.database import DATA_DIR, DATABASE_PATH


def backup_database(source: Path, destination: Path) -> int:
	source = source.resolve(strict=True)
	destination = destination.resolve()
	if source == destination:
		raise ValueError("خروجی پشتیبان نمی‌تواند همان فایل پایگاه دادهٔ اصلی باشد.")
	if destination.exists():
		raise FileExistsError(f"فایل خروجی از قبل وجود دارد: {destination}")

	destination.parent.mkdir(parents=True, exist_ok=True)
	file_descriptor, temporary_name = tempfile.mkstemp(
		prefix=f".{destination.name}.",
		suffix=".tmp",
		dir=destination.parent,
	)
	os.close(file_descriptor)
	temporary_path = Path(temporary_name)
	try:
		with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as source_db:
			with closing(sqlite3.connect(temporary_path)) as destination_db:
				source_db.backup(destination_db)
				integrity = destination_db.execute("PRAGMA integrity_check").fetchone()[0]
				if integrity != "ok":
					raise sqlite3.DatabaseError(f"اعتبارسنجی پشتیبان ناموفق بود: {integrity}")
				project_count = destination_db.execute(
					"SELECT COUNT(*) FROM projects"
				).fetchone()[0]
		os.link(temporary_path, destination)
	finally:
		temporary_path.unlink(missing_ok=True)
	return project_count


def main() -> None:
	parser = argparse.ArgumentParser(description="Export a consistent Hooshyar database snapshot.")
	parser.add_argument(
		"--output",
		type=Path,
		default=DATA_DIR / "hooshyar-transfer.sqlite3",
		help="Destination path (default: data/hooshyar-transfer.sqlite3)",
	)
	args = parser.parse_args()
	try:
		project_count = backup_database(DATABASE_PATH, args.output)
	except (OSError, sqlite3.Error, ValueError) as exc:
		parser.error(str(exc))
	print(f"Valid backup created: {args.output.resolve()}")
	print(f"Project count in backup: {project_count}")


if __name__ == "__main__":
	main()
