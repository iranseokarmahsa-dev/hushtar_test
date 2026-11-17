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


class CategoryIntegrationMarkdownError(ValueError):
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
            raise CategoryIntegrationMarkdownError(["متن پیش از عنوان اصلی مجاز نیست."])
    if not locations or locations[0][1:] != (1, "COMPONENT SYNTHESIS RESULT"):
        raise CategoryIntegrationMarkdownError([
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
        raise CategoryIntegrationMarkdownError([
            f"ترتیب Heading نامعتبر است؛ انتظار `{('#' * level)} {title}` و دریافت `{actual}`."
        ])
    return index + 1


def _empty(body: str, context: str) -> None:
    if body.strip() not in {"", "NONE", "- NONE"}:
        raise CategoryIntegrationMarkdownError([
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
        items: list[str] = []
        if is_list and initial not in {"", "NONE"}:
            items.extend(
                item.strip()
                for item in re.split(r"[,،]\s*|\s+", initial)
                if item.strip()
            )
        if not is_list and initial == "NONE":
            errors.append(f"{context}: مقدار NONE برای فیلد متنی `{key}` مجاز نیست.")
        text_lines = [initial] if initial and not is_list else []
        index += 1
        while index < len(lines) and not FIELD_RE.match(lines[index]):
            line = lines[index]
            if not line.strip():
                index += 1
                continue
            bullet = re.match(r"^\s{2,}-\s+(.*)$", line)
            if is_list and bullet:
                items.append(bullet.group(1).strip())
            elif not is_list and line.startswith("  "):
                text_lines.append(line.strip())
            else:
                errors.append(f"{context}: مقدار فیلد `{key}` قالب Markdown معتبر ندارد.")
            index += 1
        if is_list:
            if items == ["NONE"] or (initial == "NONE" and not items):
                items = []
            if initial == "NONE" and items:
                errors.append(f"{context}: مقدار NONE و موارد فهرست `{key}` هم‌زمان مجاز نیست.")
            values[key] = items
        else:
            values[key] = "\n".join(text_lines).strip()
    if order and tuple(values) != order:
        errors.append(f"{context}: ترتیب فیلدها باید دقیقاً {', '.join(order)} باشد.")
    if errors:
        raise CategoryIntegrationMarkdownError(errors)
    return values


def parse_category_integration_markdown(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise CategoryIntegrationMarkdownError(["سند Markdown خالی است."])
    text = sanitize_component_synthesis_markdown(text)
    headings = _headings(text)
    _empty(headings[0].body, "عنوان اصلی")
    section_index = next(
        (
            index for index, heading in enumerate(headings)
            if heading.level == 2 and heading.title == "INTEGRATED CATEGORIES"
        ),
        None,
    )
    if section_index is None:
        raise CategoryIntegrationMarkdownError(["بخش INTEGRATED CATEGORIES پیدا نشد."])

    categories = []
    for node in headings[section_index + 1:]:
        if node.level <= 2:
            break
        if node.level != 3 or not node.title.startswith("CATEGORY: "):
            continue
        try:
            category = _fields(
                node.body,
                context=node.title,
                list_fields={"items", "item_ids", "source_item_ids"},
                order=(),
            )
        except CategoryIntegrationMarkdownError:
            continue
        item_ids = (
            category.get("items")
            or category.get("item_ids")
            or category.get("source_item_ids")
            or []
        )
        heading_id = node.title.removeprefix("CATEGORY: ").strip()
        categories.append(category)
        category["category_id"] = heading_id
        category["source_item_ids"] = item_ids
    return {
        "integrated_categories": categories,
    }
