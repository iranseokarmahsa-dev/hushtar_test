from __future__ import annotations

import hashlib
import json
from typing import Any

from app.repositories.synthesis_repository import get_run_sources, get_synthesis_run


def load_run_snapshot_context(run_id: str) -> dict[str, Any]:
    run = get_synthesis_run(run_id)
    if run is None:
        raise ValueError("Synthesis run not found.")

    rows = get_run_sources(run_id)
    snapshots: list[dict[str, Any]] = []
    source_issues: list[dict[str, Any]] = []
    seen_project_ids: set[str] = set()

    for row in rows:
        issue: str | None = None
        raw_snapshot_hash = hashlib.sha256((row.get("snapshot_json") or "").encode("utf-8")).hexdigest()
        snapshot: Any = None
        try:
            snapshot = json.loads(row["snapshot_json"])
        except (TypeError, json.JSONDecodeError):
            issue = "invalid_snapshot_json"

        if issue is None and not isinstance(snapshot, dict):
            issue = "snapshot_not_object"
        if issue is None:
            actual_hash = raw_snapshot_hash
            if actual_hash != row.get("source_hash"):
                issue = "snapshot_hash_mismatch"
        if issue is None and snapshot.get("project_id") != row.get("source_project_id"):
            issue = "snapshot_project_id_mismatch"
        if row.get("source_project_id") in seen_project_ids:
            issue = "duplicate_source_project_id"
        seen_project_ids.add(row.get("source_project_id"))

        if issue:
            source_issues.append({
                "source_project_id": row.get("source_project_id"),
                "source_title": row.get("source_title"),
                "source_hash": row.get("source_hash"),
                "actual_snapshot_hash": raw_snapshot_hash,
                "issue_type": issue,
                "source_order": row.get("source_order"),
            })
            continue

        snapshots.append({
            "source_project_id": row["source_project_id"],
            "source_title": row["source_title"],
            "source_hash": row["source_hash"],
            "source_order": row["source_order"],
            "snapshot": snapshot,
        })

    return {
        "run": run,
        "registered_source_count": len(rows),
        "snapshots": snapshots,
        "source_issues": source_issues,
    }
