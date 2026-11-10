from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FIELD_RE = re.compile(r"^- ([a-z][a-z0-9_]*):(?:[ \t]*(.*))?$")
LIST_FIELDS = {
    "source_item_ids",
    "similarities",
    "differences",
    "evidence_ref_ids",
    "notes",
    "missing_source_item_ids",
    "duplicate_source_item_ids",
    "unknown_source_item_ids",
    "unknown_evidence_ref_ids",
}


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    body: str


class CategoryGroupingMarkdownError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _headings(text: str) -> list[Heading]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    locations: list[tuple[int, int, int, str]] = []
    errors: list[str] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            locations.append((index, len(match.group(1)), index + 1, match.group(2).strip()))
        elif not locations and line.strip():
            errors.append("محتوای متنی پیش از عنوان اصلی مجاز نیست.")
    if not locations or locations[0][1:] != (1, 1, "CATEGORY GROUPING RESULT"):
        errors.append("عنوان اصلی باید دقیقاً `# CATEGORY GROUPING RESULT` باشد.")
    if errors:
        raise CategoryGroupingMarkdownError(errors)
    return [
        Heading(level, title, "\n".join(lines[body_start:locations[index + 1][0] if index + 1 < len(locations) else len(lines)]))
        for index, (_, level, body_start, title) in enumerate(locations)
    ]


def _expect_heading(headings: list[Heading], index: int, level: int, title: str) -> int:
    if index >= len(headings) or headings[index].level != level or headings[index].title != title:
        actual = headings[index].title if index < len(headings) else "پایان سند"
        raise CategoryGroupingMarkdownError([
            f"ترتیب Heading نامعتبر است؛ انتظار `{('#' * level)} {title}` و دریافت `{actual}`."
        ])
    return index + 1


def _parse_fields(
    body: str,
    *,
    list_fields: set[str],
    context: str,
    expected_order: tuple[str, ...] | None = None,
) -> dict[str, object]:
    lines = body.splitlines()
    fields: dict[str, object] = {}
    errors: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        match = FIELD_RE.match(line)
        if not match:
            errors.append(f"{context}: خط ساختاری نامعتبر `{line.strip()}`.")
            index += 1
            continue
        key, initial = match.group(1), (match.group(2) or "").strip()
        if key in fields:
            errors.append(f"{context}: فیلد تکراری `{key}`.")
        is_list = key in list_fields
        values: list[str] = []
        continuation: list[str] = []
        if is_list and initial and initial != "NONE":
            errors.append(f"{context}: فهرست `{key}` باید با Bullet List ارائه شود.")
        elif not is_list and initial:
            continuation.append(initial)
        index += 1
        while index < len(lines) and not FIELD_RE.match(lines[index]):
            nested = lines[index]
            if not nested.strip():
                index += 1
                continue
            bullet = re.match(r"^\s{2,}-\s+(.*)$", nested)
            if is_list and bullet:
                values.append(bullet.group(1).strip())
            elif not is_list and nested.startswith("  "):
                continuation.append(nested.strip())
            else:
                errors.append(f"{context}: مقدار فیلد `{key}` قالب Markdown معتبر ندارد.")
            index += 1
        if is_list and initial == "NONE" and values:
            errors.append(f"{context}: مقدار NONE برای فیلد `{key}` با موارد فهرست هم‌زمان مجاز نیست.")
        if is_list:
            if values == ["NONE"] or (initial == "NONE" and not values):
                values = []
            fields[key] = values
        else:
            fields[key] = "\n".join(continuation).strip()
    if expected_order is not None and tuple(fields) != expected_order:
        errors.append(
            f"{context}: ترتیب فیلدها باید دقیقاً {', '.join(expected_order)} باشد."
        )
    if errors:
        raise CategoryGroupingMarkdownError(errors)
    return fields


def _parse_standalone(body: str, component_key: str) -> list[str]:
    values: list[str] = []
    errors: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"- source_item_id:\s*(.+)", line.strip())
        if match:
            value = match.group(1).strip()
            if value != "NONE":
                values.append(value)
        else:
            errors.append(f"{component_key}: فهرست مستقل باید با `- source_item_id:` تعریف شود.")
    if errors:
        raise CategoryGroupingMarkdownError(errors)
    return values


def _require_empty_or_none(body: str, context: str) -> None:
    value = body.strip()
    if value not in {"", "NONE", "- NONE"}:
        raise CategoryGroupingMarkdownError([f"{context}: بخش خالی باید بدون متن یا با مقدار NONE باشد."])


def parse_category_grouping_markdown(text: str) -> dict[str, object]:
    if not isinstance(text, str) or not text.strip():
        raise CategoryGroupingMarkdownError(["سند Markdown خالی است."])
    headings = _headings(text)
    _require_empty_or_none(headings[0].body, "عنوان اصلی")
    index = 1
    index = _expect_heading(headings, index, 2, "METADATA")
    metadata = _parse_fields(
        headings[index - 1].body,
        list_fields=set(),
        context="METADATA",
        expected_order=("schema_version", "run_id", "stage_key", "input_hash"),
    )
    index = _expect_heading(headings, index, 2, "COMPONENT RESULTS")
    _require_empty_or_none(headings[index - 1].body, "COMPONENT RESULTS")

    component_results: dict[str, object] = {}
    for component_key in POOL_COMPONENT_KEYS:
        index = _expect_heading(headings, index, 3, f"COMPONENT: {component_key}")
        _require_empty_or_none(headings[index - 1].body, f"COMPONENT: {component_key}")
        index = _expect_heading(headings, index, 4, "GROUPS")
        groups_heading = headings[index - 1]
        _require_empty_or_none(groups_heading.body, f"{component_key}.GROUPS")
        groups: list[dict[str, object]] = []
        while index < len(headings) and headings[index].level == 5 and headings[index].title.startswith("GROUP: "):
            node = headings[index]
            values = _parse_fields(
                node.body,
                list_fields=LIST_FIELDS,
                context=node.title,
                expected_order=(
                    "group_id",
                    "component_key",
                    "proposed_category_name",
                    "definition",
                    "source_item_ids",
                    "reasoning",
                    "similarities",
                    "differences",
                    "evidence_ref_ids",
                    "confidence",
                    "needs_researcher_review",
                ),
            )
            values["_heading_id"] = node.title.removeprefix("GROUP: ").strip()
            groups.append(values)
            index += 1
        index = _expect_heading(headings, index, 4, "STANDALONE SOURCE ITEMS")
        standalone = _parse_standalone(headings[index - 1].body, component_key)
        index = _expect_heading(headings, index, 4, "CONTRADICTIONS")
        contradictions_heading = headings[index - 1]
        _require_empty_or_none(contradictions_heading.body, f"{component_key}.CONTRADICTIONS")
        contradictions: list[dict[str, object]] = []
        while index < len(headings) and headings[index].level == 5 and headings[index].title.startswith("CONTRADICTION: "):
            node = headings[index]
            values = _parse_fields(
                node.body,
                list_fields=LIST_FIELDS,
                context=node.title,
                expected_order=(
                    "contradiction_id",
                    "source_item_ids",
                    "evidence_ref_ids",
                    "component_key",
                    "description",
                    "reasoning",
                    "needs_researcher_review",
                ),
            )
            values["_heading_id"] = node.title.removeprefix("CONTRADICTION: ").strip()
            contradictions.append(values)
            index += 1
        component_results[component_key] = {
            "groups": groups,
            "standalone_source_items": standalone,
            "contradictions": contradictions,
        }

    index = _expect_heading(headings, index, 2, "QUALITY AUDIT")
    quality_audit = _parse_fields(
        headings[index - 1].body,
        list_fields=LIST_FIELDS,
        context="QUALITY AUDIT",
        expected_order=(
            "total_source_items",
            "grouped_source_items",
            "standalone_source_items",
            "total_groups",
            "total_contradictions",
            "missing_source_item_ids",
            "duplicate_source_item_ids",
            "unknown_source_item_ids",
            "unknown_evidence_ref_ids",
            "notes",
        ),
    )
    if index != len(headings):
        raise CategoryGroupingMarkdownError(["پس از بخش QUALITY AUDIT هیچ Heading دیگری مجاز نیست."])
    return {
        "metadata": metadata,
        "component_results": component_results,
        "quality_audit": quality_audit,
    }
