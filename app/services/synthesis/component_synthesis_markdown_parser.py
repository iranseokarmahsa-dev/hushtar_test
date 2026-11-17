from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.synthesis.component_markdown_sanitizer import (
    sanitize_component_synthesis_markdown,
)


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FIELD_RE = re.compile(r"^- ([a-z][a-z0-9_]*):(?:[ \t]*(.*))?$")


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    body: str


class ComponentSynthesisMarkdownError(ValueError):
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
            raise ComponentSynthesisMarkdownError(["محتوای متنی پیش از عنوان اصلی مجاز نیست."])
    if not locations or locations[0][1:] != (1, "COMPONENT SYNTHESIS RESULT"):
        raise ComponentSynthesisMarkdownError([
            "عنوان اصلی باید دقیقاً `# COMPONENT SYNTHESIS RESULT` باشد."
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
        raise ComponentSynthesisMarkdownError([
            f"ترتیب Heading نامعتبر است؛ انتظار `{('#' * level)} {title}` و دریافت `{actual}`."
        ])
    return index + 1


def _empty(body: str, context: str) -> None:
    if body.strip() not in {"", "NONE", "- NONE"}:
        raise ComponentSynthesisMarkdownError([
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
        raise ComponentSynthesisMarkdownError(errors)
    return values


def parse_component_synthesis_markdown(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ComponentSynthesisMarkdownError(["سند Markdown خالی است."])
    text = sanitize_component_synthesis_markdown(text)
    headings = _headings(text)
    _empty(headings[0].body, "عنوان اصلی")
    index = _expect(headings, 1, 2, "METADATA")
    metadata = _fields(
        headings[index - 1].body,
        context="METADATA",
        list_fields=set(),
        order=("run_id", "component_key", "input_hash"),
    )
    index = _expect(headings, index, 2, "INTEGRATED CATEGORIES")
    category_section_body = headings[index - 1].body
    _empty(category_section_body, "INTEGRATED CATEGORIES")
    categories: list[dict[str, Any]] = []
    while index < len(headings) and headings[index].level == 3 and headings[index].title.startswith("CATEGORY: "):
        node = headings[index]
        category = _fields(
            node.body,
            context=node.title,
            list_fields={"source_item_ids"},
            order=("category_id", "title", "definition", "source_item_ids"),
        )
        heading_id = node.title.removeprefix("CATEGORY: ").strip()
        if category["category_id"] != heading_id:
            raise ComponentSynthesisMarkdownError([
                "شناسه مقوله در Heading و فیلد category_id باید یکسان باشد."
            ])
        categories.append(category)
        index += 1
    if categories and category_section_body.strip() in {"NONE", "- NONE"}:
        raise ComponentSynthesisMarkdownError([
            "مقدار NONE در بخش INTEGRATED CATEGORIES با تعریف مقوله‌ها هم‌زمان مجاز نیست."
        ])
    if not categories:
        _empty(headings[index - 1].body, "INTEGRATED CATEGORIES")

    index = _expect(headings, index, 2, "THEMES")
    theme_section_body = headings[index - 1].body
    _empty(theme_section_body, "THEMES")
    themes: list[dict[str, Any]] = []
    while index < len(headings) and headings[index].level == 3 and headings[index].title.startswith("THEME: "):
        node = headings[index]
        theme = _fields(
            node.body,
            context=node.title,
            list_fields={"integrated_category_ids"},
            order=("theme_id", "title", "definition", "integrated_category_ids"),
        )
        heading_id = node.title.removeprefix("THEME: ").strip()
        if theme["theme_id"] != heading_id:
            raise ComponentSynthesisMarkdownError([
                "شناسه تم در Heading و فیلد theme_id باید یکسان باشد."
            ])
        themes.append(theme)
        index += 1
    if themes and theme_section_body.strip() in {"NONE", "- NONE"}:
        raise ComponentSynthesisMarkdownError([
            "مقدار NONE در بخش THEMES با تعریف تم‌ها هم‌زمان مجاز نیست."
        ])
    if index != len(headings):
        raise ComponentSynthesisMarkdownError(["پس از بخش THEMES هیچ Heading دیگری مجاز نیست."])
    return {
        "run_id": metadata["run_id"],
        "component_key": metadata["component_key"],
        "input_hash": metadata["input_hash"],
        "integrated_categories": categories,
        "themes": themes,
    }
