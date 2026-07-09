"""Tests for local_rag.chunkers — markdown header-aware + code line-window."""

from __future__ import annotations

import itertools

import pytest

from local_rag.chunkers import chunk_code, chunk_file, chunk_markdown
from local_rag.models import Chunk

# --------------------------------------------------------------- markdown ---


def test_markdown_empty_input_returns_empty() -> None:
    assert chunk_markdown("", source_path="/a.md", file_hash="h") == []


def test_markdown_whitespace_only_returns_empty() -> None:
    assert chunk_markdown("   \n\n\t\n", source_path="/a.md", file_hash="h") == []


def test_markdown_no_headings_emits_single_chunk_with_empty_path() -> None:
    text = "just a paragraph\nwith two lines\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    assert len(chunks) == 1
    assert chunks[0].heading_path == ""
    assert chunks[0].text == text
    assert chunks[0].chunk_index == 0


def test_markdown_single_heading_chunk_includes_heading_line() -> None:
    text = "# Title\n\nbody line\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    assert len(chunks) == 1
    assert chunks[0].heading_path == "/Title"
    assert "# Title" in chunks[0].text
    assert "body line" in chunks[0].text


def test_markdown_nested_headings_build_path() -> None:
    text = "# A\n\nintro\n\n## B\n\nmid\n\n### C\n\ndeep\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    paths = [c.heading_path for c in chunks]
    assert paths == ["/A", "/A/B", "/A/B/C"]


def test_markdown_sibling_heading_replaces_last_segment() -> None:
    text = "# A\n\nintro\n\n## B\n\nb body\n\n## C\n\nc body\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    paths = [c.heading_path for c in chunks]
    assert paths == ["/A", "/A/B", "/A/C"]


def test_markdown_pops_on_shallower_heading() -> None:
    text = "# A\n\nintro\n\n## B\n\nb body\n\n# C\n\nc body\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    paths = [c.heading_path for c in chunks]
    assert paths == ["/A", "/A/B", "/C"]


def test_markdown_skipped_levels_nest_anyway() -> None:
    text = "# A\n\nintro\n\n### B\n\nb body\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    paths = [c.heading_path for c in chunks]
    assert paths == ["/A", "/A/B"]


def test_markdown_preheader_body_emits_empty_path_chunk() -> None:
    text = "preamble paragraph\n\nmore preamble\n\n# Title\n\nbody\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    assert len(chunks) == 2
    assert chunks[0].heading_path == ""
    assert "preamble paragraph" in chunks[0].text
    assert chunks[1].heading_path == "/Title"


def test_markdown_strips_yaml_frontmatter() -> None:
    text = "---\ntitle: foo\ntags: [a, b]\n---\n# Title\n\nbody\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    assert len(chunks) == 1
    assert "title: foo" not in chunks[0].text
    assert chunks[0].heading_path == "/Title"


def test_markdown_frontmatter_offsets_skip_fence() -> None:
    """char_start of first chunk is past the closing --- line, not at 0."""
    text = "---\nx: 1\n---\n# Title\n\nbody\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    assert chunks[0].char_start == len("---\nx: 1\n---\n")
    assert text[chunks[0].char_start :].startswith("# Title")


def test_markdown_unclosed_frontmatter_is_treated_as_content() -> None:
    text = "---\nthis never closes\n# Title\n\nbody\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    # The leading "---" is content; the # Title is still a heading.
    assert chunks[0].heading_path == ""
    assert chunks[0].text.startswith("---")


def test_markdown_round_trip_offsets_match_text() -> None:
    text = "# A\n\nalpha body\n\n## B\n\nbeta body\n\n# C\n\ngamma body\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    for c in chunks:
        assert text[c.char_start : c.char_end] == c.text


def test_markdown_chunk_indices_are_monotonic() -> None:
    text = "# A\n\na\n\n## B\n\nb\n\n# C\n\nc\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_markdown_passes_through_source_path_and_hash() -> None:
    chunks = chunk_markdown("# T\n\nx\n", source_path="/zz.md", file_hash="hAAA")

    assert chunks[0].source_path == "/zz.md"
    assert chunks[0].file_hash == "hAAA"


def test_markdown_vectors_are_empty() -> None:
    chunks = chunk_markdown("# T\n\nx\n", source_path="/a.md", file_hash="h")

    assert all(c.vector == [] for c in chunks)


# ------------------------------------------------------------------ code ---


def test_code_empty_input_returns_empty() -> None:
    assert chunk_code("", source_path="/a.py", file_hash="h") == []


def test_code_short_file_emits_single_chunk() -> None:
    text = "line1\nline2\nline3\n"
    chunks = chunk_code(text, source_path="/a.py", file_hash="h", window_lines=60, overlap_lines=10)

    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len(text)


def test_code_exact_window_size_emits_single_chunk() -> None:
    text = "".join(f"line{i}\n" for i in range(60))
    chunks = chunk_code(text, source_path="/a.py", file_hash="h", window_lines=60, overlap_lines=10)

    assert len(chunks) == 1
    assert chunks[0].text == text


def test_code_one_over_window_emits_two_chunks_with_overlap() -> None:
    text = "".join(f"line{i:02d}\n" for i in range(61))
    chunks = chunk_code(text, source_path="/a.py", file_hash="h", window_lines=60, overlap_lines=10)

    assert len(chunks) == 2
    # Second chunk starts at line (60 - 10) = 50 in the original.
    expected_second_start = sum(len(f"line{i:02d}\n") for i in range(50))
    assert chunks[1].char_start == expected_second_start


def test_code_round_trip_offsets_match_text() -> None:
    text = "".join(f"row {i}\n" for i in range(150))
    chunks = chunk_code(text, source_path="/a.py", file_hash="h", window_lines=60, overlap_lines=10)

    for c in chunks:
        assert text[c.char_start : c.char_end] == c.text


def test_code_chunk_indices_are_monotonic() -> None:
    text = "".join(f"x{i}\n" for i in range(200))
    chunks = chunk_code(text, source_path="/a.py", file_hash="h", window_lines=60, overlap_lines=10)

    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_code_consecutive_chunks_overlap_by_overlap_lines() -> None:
    text = "".join(f"L{i}\n" for i in range(120))
    chunks = chunk_code(text, source_path="/a.py", file_hash="h", window_lines=60, overlap_lines=10)

    assert len(chunks) >= 2
    first_lines = chunks[0].text.splitlines()
    second_lines = chunks[1].text.splitlines()
    # Last 10 lines of chunk 0 are the first 10 lines of chunk 1.
    assert first_lines[-10:] == second_lines[:10]


def test_code_heading_path_is_empty() -> None:
    text = "".join(f"x{i}\n" for i in range(100))
    chunks = chunk_code(text, source_path="/a.py", file_hash="h", window_lines=60, overlap_lines=10)

    assert all(c.heading_path == "" for c in chunks)


def test_code_raises_when_window_not_greater_than_overlap() -> None:
    with pytest.raises(ValueError, match="greater than"):
        chunk_code("a\nb\n", source_path="/a.py", file_hash="h", window_lines=10, overlap_lines=10)


def test_code_round_trip_holds_for_crlf_endings() -> None:
    text = "".join(f"row {i}\r\n" for i in range(80))
    chunks = chunk_code(text, source_path="/a.py", file_hash="h", window_lines=30, overlap_lines=5)

    assert len(chunks) >= 2
    for c in chunks:
        assert text[c.char_start : c.char_end] == c.text


def test_markdown_round_trip_holds_for_crlf_endings() -> None:
    text = "# A\r\n\r\nalpha\r\n\r\n## B\r\n\r\nbeta\r\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    assert [c.heading_path for c in chunks] == ["/A", "/A/B"]
    for c in chunks:
        assert text[c.char_start : c.char_end] == c.text


def test_code_custom_window_parameters_honored() -> None:
    text = "".join(f"x{i}\n" for i in range(25))
    chunks = chunk_code(text, source_path="/a.py", file_hash="h", window_lines=10, overlap_lines=2)

    # 25 lines, window 10, step 8 → starts at 0, 8, 16 → 3 chunks.
    assert len(chunks) == 3


def test_code_vectors_are_empty() -> None:
    text = "a\nb\nc\n"
    chunks = chunk_code(text, source_path="/a.py", file_hash="h")

    assert all(c.vector == [] for c in chunks)


def test_code_passes_through_source_path_and_hash() -> None:
    chunks = chunk_code("a\nb\n", source_path="/x.py", file_hash="hZ")

    assert chunks[0].source_path == "/x.py"
    assert chunks[0].file_hash == "hZ"


# ----------------------------------------------------------- dispatcher ---


def test_chunk_file_md_uses_markdown_chunker() -> None:
    text = "# Title\n\nbody\n"
    chunks = chunk_file(text, source_path="/a.md", file_hash="h")

    assert any(c.heading_path == "/Title" for c in chunks)


def test_chunk_file_py_uses_code_chunker() -> None:
    text = "# Title\n\nbody\n"  # `#` here is a Python comment, not a heading.
    chunks = chunk_file(text, source_path="/a.py", file_hash="h")

    assert all(c.heading_path == "" for c in chunks)


def test_chunk_file_no_extension_uses_code_chunker() -> None:
    chunks = chunk_file("# Title\n\nbody\n", source_path="/Makefile", file_hash="h")

    assert all(c.heading_path == "" for c in chunks)


def test_chunk_file_extension_case_insensitive() -> None:
    chunks = chunk_file("# T\n\nb\n", source_path="/a.MD", file_hash="h")

    assert any(c.heading_path == "/T" for c in chunks)


def test_code_splits_very_long_single_line_into_multiple_chunks() -> None:
    """Real-world hazard: transcripts often arrive as one giant unbroken line.
    The line-window chunker treats it as a single chunk; we then can't embed
    it. Verify we split such chunks down to a manageable char budget."""
    text = "x" * 40_000  # one line, no newlines, 40k chars
    chunks = chunk_code(text, source_path="/transcript.txt", file_hash="h")

    assert len(chunks) >= 2
    # No single chunk exceeds the embed budget headroom (24000 by default).
    assert all(len(c.text) <= 24_000 for c in chunks)
    # Round-trip still holds piecewise: concatenating in order rebuilds text.
    rebuilt = "".join(c.text for c in chunks)
    assert rebuilt == text
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_code_long_chunk_split_prefers_whitespace_boundaries() -> None:
    """When a long chunk must be split, the cut should fall at whitespace
    where possible, so we don't break in the middle of words."""
    text = ("word " * 6000).rstrip()  # ~30k chars, all whitespace-separated
    chunks = chunk_code(text, source_path="/log.txt", file_hash="h")

    assert len(chunks) >= 2
    # Every non-final chunk should end on whitespace.
    for c in chunks[:-1]:
        assert c.text[-1].isspace() or c.text.endswith("word")


def test_markdown_splits_oversized_section_chunk() -> None:
    """A single heading section whose body is larger than the embed budget
    also gets split."""
    big_body = "x" * 35_000
    text = f"# Section\n\n{big_body}\n"
    chunks = chunk_markdown(text, source_path="/big.md", file_hash="h")

    assert len(chunks) >= 2
    assert all(len(c.text) <= 24_000 for c in chunks)
    # First sub-chunk carries the heading_path; siblings inherit it.
    assert all(c.heading_path == "/Section" for c in chunks)


def test_oversized_split_preserves_round_trip_offsets(
    tmp_path: object,  # unused but pytest fixture-style
) -> None:
    """text[char_start:char_end] must equal chunk.text for every sub-chunk."""
    del tmp_path  # silence "unused"
    text = "abcdefghij" * 5000  # 50k chars, no whitespace
    chunks = chunk_code(text, source_path="/blob.txt", file_hash="h")

    for c in chunks:
        assert text[c.char_start : c.char_end] == c.text


def test_chunk_file_returns_chunk_instances() -> None:
    chunks = chunk_file("hello\n", source_path="/a.txt", file_hash="h")

    assert all(isinstance(c, Chunk) for c in chunks)


# ------------------------------------------------- markdown retrieval cap ---
# Markdown chunks are capped at ~1,200 chars for retrieval quality (TODO.md
# "ranking quality"): unbounded heading sections beat topical notes in BM25.


def _paragraphs(n: int, width: int = 280) -> str:
    """n distinct paragraphs of ~width chars each, blank-line separated."""
    return "\n\n".join(f"para{i:03d} " + ("lorem ipsum " * 40)[: width - 8] for i in range(n))


def test_markdown_caps_section_chunks_for_retrieval() -> None:
    text = f"# Section\n\n{_paragraphs(20)}\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    assert len(chunks) >= 4
    assert all(len(c.text) <= 1_200 for c in chunks)


def test_markdown_capped_chunks_keep_heading_path() -> None:
    text = f"# Top\n\n## Sub\n\n{_paragraphs(15)}\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    sub_chunks = [c for c in chunks if c.heading_path == "/Top/Sub"]
    assert len(sub_chunks) >= 3


def test_markdown_capped_chunks_overlap() -> None:
    text = f"# Section\n\n{_paragraphs(20)}\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    assert len(chunks) >= 2
    for prev, nxt in itertools.pairwise(chunks):
        assert nxt.char_start < prev.char_end


def test_markdown_capped_split_prefers_paragraph_boundaries() -> None:
    text = f"# Section\n\n{_paragraphs(20)}\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    assert len(chunks) >= 2
    # Every non-final chunk should end at a paragraph break.
    for c in chunks[:-1]:
        assert c.text.endswith("\n\n")


def test_markdown_capped_round_trip_offsets() -> None:
    text = f"# Section\n\n{_paragraphs(20)}\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    for c in chunks:
        assert text[c.char_start : c.char_end] == c.text


def test_markdown_short_sections_stay_whole() -> None:
    text = "# A\n\nshort body\n\n# B\n\nanother short body\n"
    chunks = chunk_markdown(text, source_path="/a.md", file_hash="h")

    assert [c.heading_path for c in chunks] == ["/A", "/B"]


def test_code_chunks_not_subject_to_markdown_cap() -> None:
    # ~3,000 chars across 50 lines: one 60-line window, under the 24k embed
    # budget — must stay a single chunk (the 1,200 cap is markdown-only).
    text = "".join(f"line {i} " + "x" * 50 + "\n" for i in range(50))
    chunks = chunk_code(text, source_path="/a.py", file_hash="h")

    assert len(chunks) == 1
