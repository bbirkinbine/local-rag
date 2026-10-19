"""Tests for local_rag.frontmatter — stdlib parser for note frontmatter."""

from __future__ import annotations

from datetime import date

import pytest
from structlog.testing import capture_logs

from local_rag.frontmatter import (
    DEFAULT_FRONTMATTER_FIELDS,
    FrontmatterFields,
    NoteMeta,
    parse_frontmatter,
)


def _parse(block: str, fields: FrontmatterFields = DEFAULT_FRONTMATTER_FIELDS) -> NoteMeta:
    return parse_frontmatter(block, fields, source_path="/notes/example.md")


# ------------------------------------------------------------------ dates ---


def test_updated_date_beats_created_date() -> None:
    meta = _parse("created: 2026-01-02\nupdated: 2026-10-19\n")
    assert meta.note_date == date(2026, 10, 19)


def test_falls_back_to_created_when_updated_missing() -> None:
    meta = _parse("created: 2026-01-02\n")
    assert meta.note_date == date(2026, 1, 2)


@pytest.mark.parametrize("bad", ["2026-13-40", "soon"])
def test_skips_unparseable_updated_and_uses_created(bad: str) -> None:
    meta = _parse(f"updated: {bad}\ncreated: 2026-01-02\n")
    assert meta.note_date == date(2026, 1, 2)


@pytest.mark.parametrize(
    "value",
    ["2026-10-19", "2026-10-19T09:30", '"2026-10-19 09:30"'],
)
def test_datetime_style_values_give_their_date_part(value: str) -> None:
    meta = _parse(f"updated: {value}\n")
    assert meta.note_date == date(2026, 10, 19)


@pytest.mark.parametrize("value", ["null", "~"])
def test_null_date_is_absent(value: str) -> None:
    meta = _parse(f"updated: {value}\ncreated: 2026-01-02\n")
    assert meta.note_date == date(2026, 1, 2)


def test_no_date_fields_gives_none() -> None:
    meta = _parse("status: draft\n")
    assert meta.note_date is None


# ----------------------------------------------------------------- status ---


@pytest.mark.parametrize("line", ["status: 'Draft'", 'status: "Draft"', "status:   Draft  "])
def test_quoted_and_padded_status_is_unquoted_and_stripped(line: str) -> None:
    meta = _parse(f"{line}\n")
    assert meta.status == "Draft"


def test_status_case_is_preserved() -> None:
    meta = _parse("status: Retired\n")
    assert meta.status == "Retired"


def test_inline_list_status_gives_none() -> None:
    meta = _parse("status: [draft, final]\n")
    assert meta.status is None


def test_block_list_status_gives_none() -> None:
    meta = _parse("status:\n  - draft\n  - final\n")
    assert meta.status is None


def test_empty_status_gives_none() -> None:
    meta = _parse("status:\n")
    assert meta.status is None


@pytest.mark.parametrize("value", ["null", "~"])
def test_null_status_gives_none(value: str) -> None:
    meta = _parse(f"status: {value}\n")
    assert meta.status is None


# ------------------------------------------------------------------- tags ---


def test_inline_tag_list_parses() -> None:
    meta = _parse("tags: [alpha, beta]\n")
    assert meta.tags == ("alpha", "beta")


def test_inline_tag_list_strips_leading_hash() -> None:
    meta = _parse("tags: [#alpha, #beta]\n")
    assert meta.tags == ("alpha", "beta")


def test_block_tag_list_parses() -> None:
    meta = _parse("tags:\n  - alpha\n  - beta\n")
    assert meta.tags == ("alpha", "beta")


def test_scalar_tags_split_on_commas_and_whitespace_dedupe_and_strip_hash() -> None:
    meta = _parse('tags: "#a, b c, a"\n')
    assert meta.tags == ("a", "b", "c")


def test_unquoted_hash_tag_value_is_a_comment() -> None:
    meta = _parse("tags: #alpha\n")
    assert meta.tags == ()


def test_missing_tags_give_empty_tuple() -> None:
    meta = _parse("status: draft\n")
    assert meta.tags == ()


# ------------------------------------------------------- partial parsing ---


def test_nested_map_under_one_key_leaves_other_keys_parsed() -> None:
    block = "status:\n  phase: draft\n  owner: someone\nupdated: 2026-10-19\ntags: [alpha]\n"
    meta = _parse(block)
    assert meta == NoteMeta(note_date=date(2026, 10, 19), status=None, tags=("alpha",))


@pytest.mark.parametrize("indicator", ["|", ">"])
def test_multiline_string_value_is_absent_for_that_key(indicator: str) -> None:
    block = f"status: {indicator}\n  line one\n  line two\nupdated: 2026-10-19\n"
    meta = _parse(block)
    assert meta == NoteMeta(note_date=date(2026, 10, 19))


def test_anchor_value_is_absent_for_that_key() -> None:
    meta = _parse("status: &s draft\nupdated: 2026-10-19\n")
    assert meta == NoteMeta(note_date=date(2026, 10, 19))


def test_trailing_comment_is_ignored() -> None:
    meta = _parse("status: draft # still working on it\n")
    assert meta.status == "draft"


def test_comment_lines_and_blank_lines_are_ignored() -> None:
    meta = _parse("# a comment\n\nstatus: draft\n\n# another\n")
    assert meta.status == "draft"


# ---------------------------------------------------------------- garbage ---


GARBAGE = "this is not frontmatter at all\n{{{ ]]] ::\n"


def test_garbage_frontmatter_gives_empty_meta() -> None:
    assert _parse(GARBAGE) == NoteMeta()


def test_garbage_frontmatter_logs_one_warning_naming_the_file() -> None:
    with capture_logs() as logs:
        _parse(GARBAGE)
    warnings = [e for e in logs if e["log_level"] == "warning"]
    assert [(e["event"], e.get("path")) for e in warnings] == [
        ("frontmatter.unparseable", "/notes/example.md")
    ]


def test_well_formed_frontmatter_logs_no_warning() -> None:
    with capture_logs() as logs:
        _parse("status: draft\n")
    assert [e for e in logs if e["log_level"] == "warning"] == []


# ------------------------------------------------------------ custom names ---


def test_custom_field_names_are_honored() -> None:
    fields = FrontmatterFields(date_fields=("modified",), status_field="state", tags_field="labels")
    block = "modified: 2026-10-19\nupdated: 2025-01-01\nstate: final\nstatus: draft\nlabels: [x]\n"
    meta = _parse(block, fields)
    assert meta == NoteMeta(note_date=date(2026, 10, 19), status="final", tags=("x",))


def test_default_fields_match_documented_defaults() -> None:
    assert (
        FrontmatterFields(
            date_fields=("updated", "created"), status_field="status", tags_field="tags"
        )
        == DEFAULT_FRONTMATTER_FIELDS
    )


# ------------------------------------------------------- unusual key names ---


@pytest.mark.parametrize("key", ["review (Q3)", '"quoted key"', "emoji ✨ key"])
def test_unusual_key_name_leaves_sibling_keys_parsed(key: str) -> None:
    meta = _parse(f"{key}: yes\nupdated: 2026-10-19\ntags: [alpha]\n")
    assert meta == NoteMeta(note_date=date(2026, 10, 19), tags=("alpha",))


def test_quoted_key_is_unquoted() -> None:
    meta = _parse('"status": draft\n')
    assert meta.status == "draft"


# ------------------------------------------------------- inline list quotes ---


def test_inline_list_comma_inside_quotes_stays_in_one_item() -> None:
    meta = _parse('tags: ["a, b", c]\n')
    assert meta.tags == ("a, b", "c")


# ------------------------------------------------------------ date bounds ---


def test_date_followed_by_more_digits_is_not_a_date() -> None:
    meta = _parse("updated: 2026-10-1999\ncreated: 2026-01-02\n")
    assert meta.note_date == date(2026, 1, 2)
