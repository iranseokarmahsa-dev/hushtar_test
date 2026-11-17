from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.synthesis.component_markdown_sanitizer import (
    sanitize_theme_abstraction_markdown,
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
    if not locations or locations[0][1] != 1 or locations[0][2] not in {
        "COMPONENT THEMES RESULT",
        "THEME ABSTRACTION RESULT",
    }:
        raise ThemeAbstractionMarkdownError([
            "عنوان اصلی باید `# COMPONENT THEMES RESULT` باشد."
        ])
    return [
        Heading(
            level,
            title,
            "\n".join(lines[start + 1:locations[index + 1][0] if index + 1 < len(locations) else len(lines)]),
        )
        for index, (start, level, title) in enumerate(locations)
    ]


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
        entries: list[str] = []
        if is_list and initial not in {"", "NONE"}:
            entries.extend(
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
    if order and tuple(values) != order:
        errors.append(f"{context}: ترتیب فیلدها باید دقیقاً {', '.join(order)} باشد.")
    if errors:
        raise ThemeAbstractionMarkdownError(errors)
    return values


def parse_theme_abstraction_markdown(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ThemeAbstractionMarkdownError(["سند Markdown خالی است."])
    text = sanitize_theme_abstraction_markdown(text)
    headings = _headings(text)
    _empty(headings[0].body, "عنوان اصلی")
    section_index = next(
        (
            index for index, heading in enumerate(headings)
            if heading.level == 2 and heading.title == "THEMES"
        ),
        None,
    )
    if section_index is None:
        raise ThemeAbstractionMarkdownError(["بخش THEMES پیدا نشد."])

    themes = []
    for node in headings[section_index + 1:]:
        if node.level <= 2:
            break
        if node.level != 3 or not node.title.startswith("THEME: "):
            continue
        try:
            theme = _fields(
                node.body,
                context=node.title,
                list_fields={
                    "integrated_category_ids",
                    "source_category_ids",
                    "category_ids",
                    "items",
                    "item_ids",
                    "source_item_ids",
                },
                order=(),
            )
        except ThemeAbstractionMarkdownError:
            continue
        theme_id = node.title.removeprefix("THEME: ").strip()
        theme["theme_id"] = theme.get("theme_id") or theme_id
        theme["_heading_id"] = theme_id
        theme["integrated_category_ids"] = (
            theme.get("items")
            or theme.get("item_ids")
            or theme.get("integrated_category_ids")
            or theme.get("source_category_ids")
            or theme.get("category_ids")
            or theme.get("source_item_ids")
            or []
        )
        themes.append(theme)
    return {
        "themes": themes,
        "categories_without_a_theme": [],
    }
