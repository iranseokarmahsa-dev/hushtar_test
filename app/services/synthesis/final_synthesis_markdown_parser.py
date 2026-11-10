from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FIELD_RE = re.compile(r"^- ([a-z][a-z0-9_]*):(?:[ \t]*(.*))?$")
LIST_FIELDS = {
    "supporting_node_ids",
    "supporting_source_item_ids",
    "supporting_category_unit_ids",
    "supporting_theme_ids",
    "supporting_source_phenomenon_ids",
    "evidence_ref_ids",
    "strengths",
    "limitations",
    "source_phenomenon_ids",
    "reviewed_source_phenomenon_ids",
    "reviewed_node_ids",
    "node_ids",
    "source_item_ids",
    "missing_supporting_references",
    "unsupported_claims",
    "unsupported_relationships",
    "analysis_limitations",
    "supporting_source_relationship_ids",
    "unlinked_node_ids",
    "source_relationship_ids",
    "duplicate_relationships",
    "invalid_node_references",
    "invalid_evidence_references",
}


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    body: str


class FinalSynthesisMarkdownError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _headings(text: str, expected_title: str) -> list[Heading]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    locations = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            locations.append((index, len(match.group(1)), match.group(2).strip()))
        elif not locations and line.strip():
            raise FinalSynthesisMarkdownError(["محتوای متنی پیش از عنوان اصلی مجاز نیست."])
    if not locations or locations[0][1:] != (1, expected_title):
        raise FinalSynthesisMarkdownError([f"عنوان اصلی باید دقیقاً `# {expected_title}` باشد."])
    return [
        Heading(
            level,
            title,
            "\n".join(lines[start + 1:locations[index + 1][0] if index + 1 < len(locations) else len(lines)]),
        )
        for index, (start, level, title) in enumerate(locations)
    ]


def _fields(
    body: str,
    *,
    context: str,
    order: tuple[str, ...],
    list_fields: set[str] = LIST_FIELDS,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    errors = []
    lines = body.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        match = FIELD_RE.match(lines[index])
        if not match:
            errors.append(f"{context}: خط ساختاری نامعتبر `{lines[index].strip()}`.")
            index += 1
            continue
        key = match.group(1)
        initial = (match.group(2) or "").strip()
        if key in values:
            errors.append(f"{context}: فیلد تکراری `{key}`.")
        is_list = key in list_fields
        if is_list and initial not in {"", "NONE"}:
            errors.append(f"{context}: فهرست `{key}` باید با Bullet List ارائه شود.")
        if not is_list and initial == "NONE":
            errors.append(f"{context}: مقدار NONE برای فیلد متنی `{key}` مجاز نیست.")
        entries: list[str] = []
        text_lines = [initial] if initial and not is_list else []
        index += 1
        while index < len(lines) and not FIELD_RE.match(lines[index]):
            line = lines[index]
            if not line.strip():
                index += 1
                continue
            bullet = re.match(r"^\s{2,}-\s+(.*)$", line)
            if is_list and bullet:
                entries.append(bullet.group(1).strip())
            elif not is_list and line.startswith("  "):
                text_lines.append(line.strip())
            else:
                errors.append(f"{context}: مقدار فیلد `{key}` قالب Markdown معتبر ندارد.")
            index += 1
        if is_list:
            if entries == ["NONE"] or (initial == "NONE" and not entries):
                entries = []
            if initial == "NONE" and entries:
                errors.append(f"{context}: مقدار NONE و موارد فهرست `{key}` هم‌زمان مجاز نیست.")
            values[key] = entries
        else:
            values[key] = "\n".join(text_lines).strip()
    if tuple(values) != order:
        errors.append(f"{context}: ترتیب فیلدها باید دقیقاً {', '.join(order)} باشد.")
    if errors:
        raise FinalSynthesisMarkdownError(errors)
    return values


def _empty(body: str, context: str) -> None:
    if body.strip() not in {"", "NONE", "- NONE"}:
        raise FinalSynthesisMarkdownError([f"{context}: بخش خالی باید بدون متن یا با مقدار NONE باشد."])


def _section(headings: list[Heading], title: str, *, start: int) -> tuple[Heading, int]:
    if start >= len(headings) or (headings[start].level, headings[start].title) != (2, title):
        actual = headings[start].title if start < len(headings) else "پایان سند"
        raise FinalSynthesisMarkdownError([f"انتظار `## {title}` و دریافت `{actual}`."])
    return headings[start], start + 1


def _records(
    headings: list[Heading],
    *,
    start: int,
    prefix: str,
    field_order: tuple[str, ...],
) -> tuple[list[dict[str, Any]], int]:
    items = []
    while (
        start < len(headings)
        and headings[start].level == 5
        and headings[start].title.startswith(prefix)
    ):
        heading = headings[start]
        item = _fields(
            heading.body,
            context=heading.title,
            order=field_order,
        )
        item["_heading_id"] = heading.title.removeprefix(prefix).strip()
        items.append(item)
        start += 1
    return items, start


def parse_phenomenon_markdown(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise FinalSynthesisMarkdownError(["سند Markdown خالی است."])
    headings = _headings(text, "FINAL PHENOMENON RESULT")
    _empty(headings[0].body, "عنوان اصلی")
    index = 1
    metadata_heading, index = _section(headings, "METADATA", start=index)
    metadata = _fields(
        metadata_heading.body,
        context="METADATA",
        list_fields=set(),
        order=("schema_version", "run_id", "stage_key", "input_hash", "source_phase_version"),
    )
    central_heading, index = _section(headings, "CENTRAL PHENOMENON", start=index)
    central = _fields(
        central_heading.body,
        context="CENTRAL PHENOMENON",
        order=(
            "phenomenon_id", "title", "definition", "selection_reason",
            "supporting_node_ids", "supporting_source_item_ids", "supporting_category_unit_ids",
            "supporting_theme_ids", "supporting_source_phenomenon_ids", "evidence_ref_ids",
            "confidence", "needs_researcher_review",
        ),
    )
    central["_heading_id"] = central.get("phenomenon_id")
    alternatives_heading, index = _section(headings, "ALTERNATIVE INTERPRETATIONS", start=index)
    _empty(alternatives_heading.body, "ALTERNATIVE INTERPRETATIONS")
    alternatives, index = _records(
        headings,
        start=index,
        prefix="ALTERNATIVE: ",
        field_order=(
            "alternative_id", "title", "definition", "supporting_node_ids",
            "supporting_source_item_ids", "supporting_category_unit_ids", "supporting_theme_ids",
            "supporting_source_phenomenon_ids", "evidence_ref_ids", "strengths", "limitations",
            "selection_comparison", "confidence", "needs_researcher_review",
        ),
    )
    contradictions_heading, index = _section(headings, "CONTRADICTIONS", start=index)
    _empty(contradictions_heading.body, "CONTRADICTIONS")
    contradictions, index = _records(
        headings,
        start=index,
        prefix="CONTRADICTION: ",
        field_order=(
            "contradiction_id", "source_phenomenon_ids", "node_ids", "source_item_ids",
            "evidence_ref_ids", "description", "reasoning", "needs_researcher_review",
        ),
    )
    quality_heading, index = _section(headings, "QUALITY AUDIT", start=index)
    quality = _fields(
        quality_heading.body,
        context="QUALITY AUDIT",
        order=(
            "input_source_count", "input_phenomenon_count",
            "reviewed_source_phenomenon_ids", "reviewed_node_ids",
            "input_theme_count", "input_category_unit_count", "supporting_reference_count",
            "missing_supporting_references", "unsupported_claims", "contradictions",
            "analysis_limitations", "final_status",
        ),
    )
    if index != len(headings):
        raise FinalSynthesisMarkdownError(["پس از QUALITY AUDIT بخش یا Heading اضافه مجاز نیست."])
    return {
        "metadata": metadata,
        "central_phenomenon": central,
        "alternatives": alternatives,
        "contradictions": contradictions,
        "quality_audit": quality,
    }


def parse_relationship_markdown(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise FinalSynthesisMarkdownError(["سند Markdown خالی است."])
    headings = _headings(text, "FINAL RELATIONSHIP RESULT")
    _empty(headings[0].body, "عنوان اصلی")
    index = 1
    metadata_heading, index = _section(headings, "METADATA", start=index)
    metadata = _fields(
        metadata_heading.body,
        context="METADATA",
        list_fields=set(),
        order=(
            "schema_version", "run_id", "stage_key", "input_hash",
            "source_phase_version", "phenomenon_version",
        ),
    )
    relations_heading, index = _section(headings, "RELATIONSHIPS", start=index)
    _empty(relations_heading.body, "RELATIONSHIPS")
    relationships, index = _records(
        headings,
        start=index,
        prefix="RELATIONSHIP: ",
        field_order=(
            "relationship_id", "source_node_id", "target_node_id", "relationship_type",
            "direction", "description", "reasoning", "supporting_source_relationship_ids",
            "supporting_source_phenomenon_ids", "supporting_source_item_ids",
            "supporting_category_unit_ids", "evidence_ref_ids", "confidence",
            "needs_researcher_review",
        ),
    )
    unlinked_heading, index = _section(headings, "UNLINKED NODES", start=index)
    _empty(unlinked_heading.body, "UNLINKED NODES")
    unlinked, index = _records(
        headings,
        start=index,
        prefix="UNLINKED: ",
        field_order=("node_id", "component_key", "reason", "needs_researcher_review"),
    )
    contradictions_heading, index = _section(headings, "CONTRADICTIONS", start=index)
    _empty(contradictions_heading.body, "CONTRADICTIONS")
    contradictions, index = _records(
        headings,
        start=index,
        prefix="CONTRADICTION: ",
        field_order=(
            "contradiction_id", "source_node_id", "target_node_id",
            "source_relationship_ids", "source_item_ids", "evidence_ref_ids",
            "description", "reasoning", "needs_researcher_review",
        ),
    )
    quality_heading, index = _section(headings, "QUALITY AUDIT", start=index)
    quality = _fields(
        quality_heading.body,
        context="QUALITY AUDIT",
        order=(
            "input_node_count", "proposed_relationship_count", "direct_relationship_count",
            "interpretive_relationship_count", "hypothetical_relationship_count",
            "unlinked_node_count", "missing_supporting_references", "unsupported_relationships",
            "duplicate_relationships", "invalid_node_references", "invalid_evidence_references",
            "contradictions", "analysis_limitations", "final_status",
        ),
    )
    if index != len(headings):
        raise FinalSynthesisMarkdownError(["پس از QUALITY AUDIT بخش یا Heading اضافه مجاز نیست."])
    return {
        "metadata": metadata,
        "relationships": relationships,
        "unlinked_nodes": unlinked,
        "contradictions": contradictions,
        "quality_audit": quality,
    }
