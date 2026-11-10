from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.synthesis.reference_mapper import POOL_COMPONENT_KEYS

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FIELD_RE = re.compile(r"^- ([a-z][a-z0-9_]*):(?:[ \t]*(.*))?$")
LIST_FIELDS = {
    "constituent_category_unit_ids",
    "similarities",
    "differences",
    "evidence_ref_ids",
    "category_unit_ids",
    "missing_category_unit_ids",
    "duplicate_category_unit_ids",
    "unknown_category_unit_ids",
    "unknown_evidence_ref_ids",
    "notes",
}


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    body: str


class ThemeSynthesisMarkdownError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _headings(text: str) -> list[Heading]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    locations = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            locations.append((index, len(match.group(1)), match.group(2).strip()))
        elif not locations and line.strip():
            raise ThemeSynthesisMarkdownError(["محتوای متنی پیش از عنوان اصلی مجاز نیست."])
    if not locations or locations[0][1:] != (1, "THEME SYNTHESIS RESULT"):
        raise ThemeSynthesisMarkdownError(["عنوان اصلی باید دقیقاً `# THEME SYNTHESIS RESULT` باشد."])
    return [
        Heading(level, title, "\n".join(lines[start + 1:locations[index + 1][0] if index + 1 < len(locations) else len(lines)]))
        for index, (start, level, title) in enumerate(locations)
    ]


def _expect(headings: list[Heading], index: int, level: int, title: str) -> int:
    if index >= len(headings) or (headings[index].level, headings[index].title) != (level, title):
        actual = headings[index].title if index < len(headings) else "پایان سند"
        raise ThemeSynthesisMarkdownError([f"ترتیب Heading نامعتبر است؛ انتظار `{('#' * level)} {title}` و دریافت `{actual}`."])
    return index + 1


def _empty(body: str, context: str) -> None:
    if body.strip() not in {"", "NONE", "- NONE"}:
        raise ThemeSynthesisMarkdownError([f"{context}: بخش خالی باید بدون متن یا با مقدار NONE باشد."])


def _fields(body: str, *, context: str, list_fields: set[str], order: tuple[str, ...]) -> dict[str, Any]:
    lines = body.splitlines()
    values: dict[str, Any] = {}
    errors = []
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
        key, initial = match.group(1), (match.group(2) or "").strip()
        if key in values:
            errors.append(f"{context}: فیلد تکراری `{key}`.")
        is_list = key in list_fields
        if is_list and initial not in {"", "NONE"}:
            errors.append(f"{context}: فهرست `{key}` باید با Bullet List ارائه شود.")
        if not is_list and initial == "NONE":
            errors.append(f"{context}: مقدار NONE برای فیلد متنی `{key}` مجاز نیست.")
        entries = []
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
        raise ThemeSynthesisMarkdownError(errors)
    return values


def parse_theme_synthesis_markdown(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ThemeSynthesisMarkdownError(["سند Markdown خالی است."])
    headings = _headings(text)
    _empty(headings[0].body, "عنوان اصلی")
    index = _expect(headings, 1, 2, "METADATA")
    metadata = _fields(
        headings[index - 1].body,
        context="METADATA",
        list_fields=set(),
        order=("schema_version", "run_id", "stage_key", "input_hash", "source_phase_version"),
    )
    index = _expect(headings, index, 2, "COMPONENT RESULTS")
    _empty(headings[index - 1].body, "COMPONENT RESULTS")
    components: dict[str, Any] = {}
    for component_key in POOL_COMPONENT_KEYS:
        index = _expect(headings, index, 3, f"COMPONENT: {component_key}")
        _empty(headings[index - 1].body, component_key)
        index = _expect(headings, index, 4, "THEMES")
        _empty(headings[index - 1].body, f"{component_key}.THEMES")
        themes = []
        while index < len(headings) and headings[index].level == 5 and headings[index].title.startswith("THEME: "):
            node = headings[index]
            theme = _fields(
                node.body,
                context=node.title,
                list_fields=LIST_FIELDS,
                order=(
                    "theme_id", "component_key", "proposed_theme_name", "definition",
                    "constituent_category_unit_ids", "reasoning", "conceptual_scope",
                    "similarities", "differences", "evidence_ref_ids", "confidence",
                    "needs_researcher_review",
                ),
            )
            theme["_heading_id"] = node.title.removeprefix("THEME: ").strip()
            themes.append(theme)
            index += 1
        index = _expect(headings, index, 4, "UNTHEMED CATEGORY UNITS")
        _empty(headings[index - 1].body, f"{component_key}.UNTHEMED CATEGORY UNITS")
        unthemed = []
        while index < len(headings) and headings[index].level == 5 and headings[index].title.startswith("UNTHEMED: "):
            node = headings[index]
            item = _fields(
                node.body,
                context=node.title,
                list_fields=set(),
                order=("category_unit_id", "reason"),
            )
            item["_heading_id"] = node.title.removeprefix("UNTHEMED: ").strip()
            unthemed.append(item)
            index += 1
        index = _expect(headings, index, 4, "CONTRADICTIONS")
        _empty(headings[index - 1].body, f"{component_key}.CONTRADICTIONS")
        contradictions = []
        while index < len(headings) and headings[index].level == 5 and headings[index].title.startswith("CONTRADICTION: "):
            node = headings[index]
            item = _fields(
                node.body,
                context=node.title,
                list_fields=LIST_FIELDS,
                order=(
                    "contradiction_id", "component_key", "category_unit_ids",
                    "evidence_ref_ids", "description", "reasoning", "needs_researcher_review",
                ),
            )
            item["_heading_id"] = node.title.removeprefix("CONTRADICTION: ").strip()
            contradictions.append(item)
            index += 1
        components[component_key] = {
            "themes": themes,
            "unthemed_category_units": unthemed,
            "contradictions": contradictions,
        }
    index = _expect(headings, index, 2, "QUALITY AUDIT")
    quality = _fields(
        headings[index - 1].body,
        context="QUALITY AUDIT",
        list_fields=LIST_FIELDS,
        order=(
            "total_category_units", "total_themes", "themed_category_units",
            "unthemed_category_units", "total_contradictions",
            "missing_category_unit_ids", "duplicate_category_unit_ids",
            "unknown_category_unit_ids", "unknown_evidence_ref_ids", "notes",
        ),
    )
    if index != len(headings):
        raise ThemeSynthesisMarkdownError(["پس از بخش QUALITY AUDIT هیچ Heading دیگری مجاز نیست."])
    return {"metadata": metadata, "component_results": components, "quality_audit": quality}
