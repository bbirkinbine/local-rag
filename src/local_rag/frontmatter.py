"""Stdlib parser for the YAML subset notes use in frontmatter.

Reads three things from a note's frontmatter block: a date, a status, and
tags. Only the shapes notes actually use are understood, per key:

- flat ``key: value`` scalars, optionally quoted (``'…'`` / ``"…"``);
- inline lists (``[a, b]``) and block lists (``- a`` lines under the key);
- comments (``#`` at the start of a value or after whitespace) and blank
  lines, which are ignored.

Anything else for a key — a nested map, an anchor or alias, a multi-line
(``|`` / ``>``) string, a ``null`` — reads as absent for that key only. A
line that isn't a key, a comment, or a continuation makes the whole block
unparseable: the result is empty metadata and one warning, never an
exception, so a note is never skipped because of its frontmatter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import structlog

log = structlog.get_logger()


@dataclass(frozen=True)
class FrontmatterFields:
    """Which frontmatter keys hold the note's date, status, and tags.

    ``date_fields`` is tried in order; the first parseable date wins.
    """

    date_fields: tuple[str, ...] = ("updated", "created")
    status_field: str = "status"
    tags_field: str = "tags"


DEFAULT_FRONTMATTER_FIELDS = FrontmatterFields()


@dataclass(frozen=True)
class NoteMeta:
    """Metadata read from one note's frontmatter. Defaults mean "absent"."""

    note_date: date | None = None
    status: str | None = None
    tags: tuple[str, ...] = ()


class _UnparseableError(Exception):
    """A line fits none of the understood shapes."""


# A parsed value: a scalar, a list, or None for "absent / not understood".
_Value = str | list[str] | None

# A key is anything up to the first colon that is followed by whitespace or
# the end of the line; it may not start like a list item or a comment.
_KEY_RE = re.compile(r"^([^\s#\-][^:]*?)\s*:(?:[ \t]+(.*))?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?!\d)")
_NULLS = frozenset({"null", "Null", "NULL", "~"})
# Value indicators this parser doesn't follow: multi-line strings, anchors,
# aliases, flow maps, tags.
_UNSUPPORTED_STARTS = ("|", ">", "&", "*", "{", "!")


def parse_frontmatter(
    block: str, fields: FrontmatterFields, *, source_path: str | None = None
) -> NoteMeta:
    """Read the configured date, status, and tags from a frontmatter block.

    Args:
        block: The text between the opening and closing ``---`` fences.
        fields: Which keys to read.
        source_path: The note's path, named in the warning when the block
            can't be parsed.

    Returns:
        The note's metadata. Empty ``NoteMeta()`` if the block is
        unparseable; never raises.
    """
    try:
        values = _parse_block(block)
        return NoteMeta(
            note_date=_first_date(values, fields.date_fields),
            status=_scalar(values.get(fields.status_field)),
            tags=_tags(values.get(fields.tags_field)),
        )
    except Exception as e:  # bad frontmatter must never fail indexing
        log.warning("frontmatter.unparseable", path=source_path, error=str(e))
        return NoteMeta()


def _parse_block(block: str) -> dict[str, _Value]:
    """Map each top-level key to its value; see the module docstring."""
    values: dict[str, _Value] = {}
    current: str | None = None
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[0] in " \t" or stripped.startswith("-"):
            if current is None:
                raise _UnparseableError(f"continuation line before any key: {stripped!r}")
            values[current] = _continue(values[current], stripped)
            continue
        m = _KEY_RE.match(line)
        if m is None:
            raise _UnparseableError(f"not a key line: {stripped!r}")
        current = _unquote(m.group(1))
        values[current] = _value(_strip_comment(m.group(2) or ""))
    return values


def _value(raw: str) -> _Value:
    """Parse the text after ``key:``. Empty opens a (possibly empty) block list."""
    if not raw:
        return []
    if raw.startswith(_UNSUPPORTED_STARTS) or raw in _NULLS:
        return None
    if raw.startswith("["):
        if not raw.endswith("]"):
            return None  # flow list spanning lines
        items = (_unquote(item.strip()) for item in _split_flow(raw[1:-1]))
        return [item for item in items if item]
    return _unquote(raw)


def _continue(value: _Value, stripped: str) -> _Value:
    """Fold a continuation line into the current key's value.

    Only ``- item`` lines extending a block list are understood; anything
    else (a nested map, a multi-line scalar) makes the key absent.
    """
    if not isinstance(value, list) or not (stripped == "-" or stripped.startswith("- ")):
        return None
    item = _strip_comment(stripped[1:].strip())
    if _KEY_RE.match(item) or item.startswith(("[", *_UNSUPPORTED_STARTS)):
        return None
    unquoted = _unquote(item)
    return [*value, unquoted] if unquoted else value


def _split_flow(inner: str) -> list[str]:
    """Split an inline list's contents on commas outside quotes."""
    items: list[str] = []
    quote: str | None = None
    start = 0
    for i, ch in enumerate(inner):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"" and not inner[start:i].strip():
            quote = ch
        elif ch == ",":
            items.append(inner[start:i])
            start = i + 1
    items.append(inner[start:])
    return items


def _strip_comment(raw: str) -> str:
    """Drop a ``#`` comment that starts the value or follows whitespace.

    A ``#`` inside quotes or brackets is content, so ``[#a, #b]`` and
    ``"#a"`` survive.
    """
    quote: str | None = None
    depth = 0
    for i, ch in enumerate(raw):
        prev = raw[i - 1] if i else " "
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"" and (prev.isspace() or prev in "[,"):
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(depth - 1, 0)
        elif ch == "#" and depth == 0 and prev.isspace():
            return raw[:i].strip()
    return raw.strip()


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("''", "'").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1].strip()
    return raw


def _first_date(values: dict[str, _Value], keys: tuple[str, ...]) -> date | None:
    for key in keys:
        value = values.get(key)
        if isinstance(value, str) and _DATE_RE.match(value):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                continue
    return None


def _scalar(value: _Value) -> str | None:
    """A non-empty scalar, or ``None`` for lists and absent values."""
    return value if isinstance(value, str) and value else None


def _tags(value: _Value) -> tuple[str, ...]:
    """Normalize tags: split scalars, strip a leading ``#``, dedupe in order."""
    if value is None:
        return ()
    raw = re.split(r"[,\s]+", value) if isinstance(value, str) else value
    tags = (tag.strip().lstrip("#") for tag in raw)
    return tuple(dict.fromkeys(tag for tag in tags if tag))
