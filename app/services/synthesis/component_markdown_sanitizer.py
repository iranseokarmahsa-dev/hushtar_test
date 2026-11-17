from __future__ import annotations

import html
import re


ROOT_HEADING = "COMPONENT SYNTHESIS RESULT"
THEME_ROOT_HEADING = "COMPONENT THEMES RESULT"
HEADING_LEVELS = {
    ROOT_HEADING: 1,
    THEME_ROOT_HEADING: 1,
    "THEME ABSTRACTION RESULT": 1,
    "METADATA": 2,
    "INTEGRATED CATEGORIES": 2,
    "THEMES": 2,
    "QUALITY AUDIT": 2,
    "END OF RESULT": 2,
}
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
OUTER_FENCE_OPEN_RE = re.compile(r"^\s*(`{3,}|~{3,})(?:[ \t]*[A-Za-z0-9_+-]+)?[ \t]*$")
OUTER_FENCE_CLOSE_RE = re.compile(r"^\s*(`{3,}|~{3,})[ \t]*$")
CATEGORY_HEADING_RE = re.compile(r"^CATEGORY:\s*(\S.*?)\s*$", re.IGNORECASE)
THEME_HEADING_RE = re.compile(r"^THEME:\s*(\S.*?)\s*$", re.IGNORECASE)


def unwrap_outer_markdown_code_fence(text: str) -> str:
    """Remove one matching code fence surrounding the complete document."""
    text = html.unescape(text).translate(str.maketrans({
        "\u200b": None,
        "\u061c": None,
        "\u200e": None,
        "\u200f": None,
        "\u202a": None,
        "\u202b": None,
        "\u202c": None,
        "\u202d": None,
        "\u202e": None,
        "\u2060": None,
        "\u2066": None,
        "\u2067": None,
        "\u2068": None,
        "\u2069": None,
        "\ufeff": None,
        "\u00ad": None,
    }))
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    first = next((index for index, line in enumerate(lines) if line.strip()), None)
    last = next((index for index in range(len(lines) - 1, -1, -1) if lines[index].strip()), None)
    if first is None or last is None or first == last:
        return text

    opening = OUTER_FENCE_OPEN_RE.fullmatch(lines[first])
    closing = OUTER_FENCE_CLOSE_RE.fullmatch(lines[last])
    if not opening or not closing:
        return text
    opener = opening.group(1)
    closer = closing.group(1)
    if opener[0] != closer[0] or len(closer) < len(opener):
        return text
    return "\n".join(lines[first + 1:last])


def _heading(line: str) -> str | None:
    candidate = line.strip()
    candidate = re.sub(r"^#{1,6}\s*", "", candidate)
    candidate = re.sub(r"\s*#{1,6}\s*$", "", candidate).strip()
    normalized = re.sub(r"\s+", " ", candidate).upper()
    level = HEADING_LEVELS.get(normalized)
    if level is not None:
        return f"{'#' * level} {normalized}"
    match = CATEGORY_HEADING_RE.fullmatch(candidate)
    if match:
        return f"### CATEGORY: {match.group(1).strip()}"
    match = THEME_HEADING_RE.fullmatch(candidate)
    if match:
        return f"### THEME: {match.group(1).strip()}"
    return None


def _normalize_lines(text: str) -> list[str]:
    text = html.unescape(text)
    text = text.translate(str.maketrans({
        "\u00a0": " ",
        "\u202f": " ",
        "\u200b": None,
        "\u061c": None,
        "\u200e": None,
        "\u200f": None,
        "\u202a": None,
        "\u202b": None,
        "\u202c": None,
        "\u202d": None,
        "\u202e": None,
        "\u2060": None,
        "\u2066": None,
        "\u2067": None,
        "\u2068": None,
        "\u2069": None,
        "\ufeff": None,
        "\u00ad": None,
    }))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized: list[str] = []
    fence_char = None
    fence_length = 0
    for raw_line in lines:
        line = raw_line.expandtabs(2).rstrip()
        fence_match = FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence_char is None:
                fence_char, fence_length = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length:
                fence_char, fence_length = None, 0
            normalized.append(line)
            continue
        if fence_char is None:
            line = re.sub(r"^(\s*)\\+(?=#{1,6}\s*)", r"\1", line)
            line = re.sub(r"^(\s*)\\-(?=\s)", r"\1-", line)
            repaired_heading = _heading(line)
            if repaired_heading:
                line = repaired_heading
        normalized.append(line)
    while normalized and not normalized[0].strip():
        normalized.pop(0)
    while normalized and not normalized[-1].strip():
        normalized.pop()
    return normalized


def sanitize_component_synthesis_markdown(text: str) -> str:
    """Repair copied headings and return only the first complete result block."""
    lines = _normalize_lines(unwrap_outer_markdown_code_fence(text))
    root_positions = [
        index for index, line in enumerate(lines)
        if line.strip() == f"# {ROOT_HEADING}"
    ]
    if not root_positions:
        return "\n".join(lines)

    end_positions = [
        index for index, line in enumerate(lines)
        if line.strip() == "## END OF RESULT"
    ]
    for index, root_position in enumerate(root_positions):
        next_root = root_positions[index + 1] if index + 1 < len(root_positions) else len(lines)
        end_position = next(
            (position for position in end_positions if root_position < position < next_root),
            None,
        )
        if end_position is not None:
            return "\n".join(lines[root_position:end_position + 1])
        if index + 1 < len(root_positions):
            return "\n".join(lines[root_position:next_root]).rstrip()

    return "\n".join(lines[root_positions[0]:])


def sanitize_theme_abstraction_markdown(text: str) -> str:
    """Repair copied headings and remove an outer code fence from theme output."""
    lines = _normalize_lines(unwrap_outer_markdown_code_fence(text))
    root = f"# {THEME_ROOT_HEADING}"
    root_positions = [
        index for index, line in enumerate(lines)
        if line.strip() == root
    ]
    if not root_positions:
        return "\n".join(lines)

    end_positions = [
        index for index, line in enumerate(lines)
        if line.strip() == "## END OF RESULT"
    ]
    for index, root_position in enumerate(root_positions):
        next_root = root_positions[index + 1] if index + 1 < len(root_positions) else len(lines)
        end_position = next(
            (position for position in end_positions if root_position < position < next_root),
            None,
        )
        if end_position is not None:
            return "\n".join(lines[root_position:end_position + 1])
        if index + 1 < len(root_positions):
            return "\n".join(lines[root_position:next_root]).rstrip()

    return "\n".join(lines[root_positions[0]:])
