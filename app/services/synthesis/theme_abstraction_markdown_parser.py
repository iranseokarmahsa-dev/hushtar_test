from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.synthesis.component_markdown_sanitizer import (
    unwrap_outer_markdown_code_fence,
)


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FIELD_RE = re.compile(r"^- ([a-z][a-z0-9_]*):(?:[ \t]*(.*))?$")


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    body: str


class ThemeAbstractionMarkdownError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _headings(text: str) -> list[Heading]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    locations: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            locations.append((index, len(match.group(1)), match.group(2).strip()))
        elif not locations and line.strip():
            raise ThemeAbstractionMarkdownError(["متن پیش از عنوان اصلی مجاز نیست."])
    if not locations or locations[0][1:] != (1, "THEME ABSTRACTION RESULT"):
        raise ThemeAbstractionMarkdownError([
            "عنوان اصلی باید دقیقاً `# THEME ABSTRACTION RESULT` باشد."
        ])
    return [
        Heading(
            level,
            title,
            "\n".join(lines[start + 1:locations[index + 1][0] if index + 1 < len(locations) else len(lines)]),
        )
        for index, (start, level, title) in enumerate(locations)
    ]


def _expect(headings: list[Heading], index: int, level: int, title: str) -> int:
    if index >= len(headings) or (headings[index].level, headings[index].title) != (level, title):
        actual = headings[index].title if index < len(headings) else "پایان سند"
        raise ThemeAbstractionMarkdownError([
            f"ترتیب Heading نامعتبر است؛ انتظار `{('#' * level)} {title}` و دریافت `{actual}`."
        ])
    return index + 1


def _empty(body: str, context: str) -> None:
    if body.strip() not in {"", "NONE", "- NONE"}:
        raise ThemeAbstractionMarkdownError([
            f"{context}: بخش خالی باید بدون متن یا با مقدار NONE باشد."
        ])


def _fields(
    body: str,
    *,
    context: str,
    list_fields: set[str],
    order: tuple[str, ...],
) -> dict[str, Any]:
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
        raise ThemeAbstractionMarkdownError(errors)
    return values


def parse_theme_abstraction_markdown(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ThemeAbstractionMarkdownError(["سند Markdown خالی است."])
    text = unwrap_outer_markdown_code_fence(text)
    headings = _headings(text)
    _empty(headings[0].body, "عنوان اصلی")

    index = _expect(headings, 1, 2, "METADATA")
    metadata = _fields(
        headings[index - 1].body,
        context="METADATA",
        list_fields=set(),
        order=("run_id", "component_key", "input_hash", "source_phase_version"),
    )
    index = _expect(headings, index, 2, "THEMES")
    _empty(headings[index - 1].body, "THEMES")
    themes = []
    while index < len(headings) and headings[index].level == 3 and headings[index].title.startswith("THEME: "):
        node = headings[index]
        theme = _fields(
            node.body,
            context=node.title,
            list_fields={"integrated_category_ids"},
            order=("theme_id", "title", "definition", "integrated_category_ids"),
        )
        theme["_heading_id"] = node.title.removeprefix("THEME: ").strip()
        themes.append(theme)
        index += 1

    index = _expect(headings, index, 2, "CATEGORIES WITHOUT A THEME")
    _empty(headings[index - 1].body, "CATEGORIES WITHOUT A THEME")
    unthemed = []
    while index < len(headings) and headings[index].level == 3 and headings[index].title.startswith("CATEGORY: "):
        node = headings[index]
        category = _fields(
            node.body,
            context=node.title,
            list_fields=set(),
            order=("integrated_category_id", "reason"),
        )
        category["_heading_id"] = node.title.removeprefix("CATEGORY: ").strip()
        unthemed.append(category)
        index += 1

    index = _expect(headings, index, 2, "QUALITY AUDIT")
    quality = _fields(
        headings[index - 1].body,
        context="QUALITY AUDIT",
        list_fields={"warnings"},
        order=(
            "total_input_categories",
            "total_assigned_categories",
            "duplicate_category_ids",
            "missing_category_ids",
            "unknown_category_ids",
            "status",
            "warnings",
        ),
    )
    index = _expect(headings, index, 2, "END OF RESULT")
    _empty(headings[index - 1].body, "END OF RESULT")
    if index != len(headings):
        raise ThemeAbstractionMarkdownError([
            "پس از END OF RESULT هیچ Heading دیگری مجاز نیست."
        ])
    return {
        "metadata": metadata,
        "themes": themes,
        "categories_without_a_theme": unthemed,
        "quality_audit": quality,
    }
