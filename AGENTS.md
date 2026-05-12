# AGENTS.md

This repository was built collaboratively with AI coding agents. This file
documents *how* — for humans browsing the codebase who want to understand the
workflow, what role the agents play, and where the human stays in control.

For a one-line summary, see the [AI-assisted codebase](./README.md#ai-assisted-codebase)
section in the README.

## TL;DR

- The author drives every design and approval decision.
- Claude (Anthropic) does implementation and code review under a documented
  spec → failing-tests → implement → independent-review → commit loop.
- Each commit carries `Co-Authored-By: Claude` trailers so `git log` is the
  audit trail.
- Per-slice spec markdown in [`docs/specs/`](./docs/specs/) shows the trail
  for every feature that landed.

## The loop

Every implementation slice followed the same six steps. None were skipped.

1. **Spec sentence.** A short markdown file in
   [`docs/specs/slice-NN-<name>.md`](./docs/specs/) defining success
   criteria, non-goals, the test list, and verification commands. Written
   by the author, occasionally refined with Claude's input. **No code is
   written until the spec is agreed.**
2. **Test-first.** pytest tests that exercise the spec's success criteria,
   written *before* the implementation and executed to confirm they fail
   (`ModuleNotFoundError` counts as a starting position, but most slices
   produced richer failures). No tautological tests; one behavior per test.
3. **Implementation.** The smallest code that makes the failing tests pass.
   Lint (`ruff`), strict type-check (`mypy`), and the full test suite must
   all be green before review.
4. **Reviewer subagent.** A second Claude instance, with no visibility into
   the implementer's reasoning or chat history, receives the diff plus the
   slice spec and the project conventions in
   [`CLAUDE.md`](./CLAUDE.md). It returns a structured report:
   **BLOCKERS** (must fix), **NITS** (optional), **LOOKS GOOD** (specific
   things verified correct).
5. **Address findings.** The author triages each blocker — fix, defer,
   reject. Real bugs the reviewer caught (and got fixed before commit)
   include the indexer's oversize-vs-orphan double-counting (slice 05) and
   the CLI's silent `search --sources <bad>` swallow (slice 06).
6. **Commit.** Only after the reviewer is satisfied or the findings are
   documented. Then `/compact` or `/clear` before the next slice so context
   doesn't bleed between concerns.

## Files agents read

- [`CLAUDE.md`](./CLAUDE.md) — the project's instructions to the AI:
  stack choices, code conventions (≤ 300 lines per file, type hints
  required, no bare `except`, Google-style docstrings, no `langchain` /
  `llama-index`, no `create_index` in v1, etc.), test-first rule, and the
  "Don't" list lifted from the spec.
- [`docs/specs/local-rag.md`](./docs/specs/local-rag.md) — the master spec.
- [`docs/specs/slice-*.md`](./docs/specs/) — one spec per implementation
  slice, written before the slice started. These show the *intended*
  shape; the commits show the *delivered* shape.

## Boundaries — what the agents do and don't do

**Agents do:**

- Read the existing code and tests.
- Propose implementations that match the slice spec.
- Run lint, type-check, and tests; fix what they break.
- Review each other's diffs.
- Suggest design improvements (which the human accepts or rejects).

**Agents don't:**

- Open new pull requests, push to remotes, or release artifacts without
  explicit human invocation.
- Modify `pyproject.toml`'s `[tool.uv]` section without asking first.
- Touch the system `git config`.
- Add dependencies beyond what `CLAUDE.md` permits.
- Make decisions about scope, licensing, or external integrations.

**Human-only decisions** that anchored this project:

- Stack: LanceDB, Ollama, MCP, structlog, pytest, ruff, mypy.
- Embedding model: `bge-m3` (1024-dim, L2-normalized) — chosen after
  weighing `nomic-embed-text` against `bge-m3` given the target hardware.
- v1 hard rules: no vector index, no langchain/llama-index, allowlist-only
  source opt-in, 1 MB file cap, no binaries.
- Vertical-slice order: config/paths → embedder → store → chunkers →
  indexer → CLI → MCP.

## Reproducing the workflow

The methodology this project follows is one strand of a broader agentic-
programming practice the author documents privately. Reconstructing the
loop in your own project doesn't need any private material — the public
artifacts are enough:

1. Write a `CLAUDE.md` (or equivalent) describing your stack, conventions,
   and "Don't" list. Keep it under ~150 lines so it fits in the agent's
   context every session.
2. For each feature, write a per-slice spec in `docs/specs/`. Define
   success criteria, non-goals, and the test list before any code.
3. Have one agent write failing tests, then implementation. Have a
   *different* agent (or instance, with no shared history) review the
   diff against the spec.
4. Read the reviewer's findings. You — not the agent — decide what's a
   blocker.
5. Commit with `Co-Authored-By` trailers so the audit trail survives.

## Models used

- Implementation and review: Claude Opus 4.7 (1M context) via Claude Code.
- The reviewer and implementer subagents are the same model class but
  separate process instances — they don't share conversation state, which
  is what makes the review independent.

## License

See [LICENSE](./LICENSE) once added. Until then, all rights reserved by
the author. The AI's contributions are derivative work generated under
the author's direction; per Anthropic's terms, the author owns the output.
