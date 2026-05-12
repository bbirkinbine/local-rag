# local-rag — Local Semantic Search over Vault + Source Repos

A small Python project that indexes my Obsidian vault and selected GitHub repos into a local LanceDB store, embeds with Ollama (`nomic-embed-text`), and exposes search over MCP so both **Cowork** and **Claude Code in VS Code** can query notes + code semantically.

## Problem

Two blind spots in how I work today:

- Updating one vault note rarely surfaces the dependent notes. Obsidian backlinks + grep miss semantically related material that doesn't share keywords.
- Bouncing between code in `~/Downloads/src/<repo>/` and notes in the vault, neither agent (Cowork, Claude Code) can pull from "the other side" without me copy-pasting.

A local vector index addresses both: one MCP-callable search across vault and code.

## Decisions (locked in)

| Decision | Choice | Why |
|---|---|---|
| Vector store | LanceDB | Multi-corpus (one table per source) cleanly; PyArrow-native; future room for code + screenshots + PDFs in one store. sqlite-vec was the close runner-up but doesn't scale as cleanly past one or two corpora. |
| Embeddings | Ollama `nomic-embed-text` (768-dim, local) | Free, private, works for both prose and code. No API key, no quota. |
| Distance metric | Cosine | nomic-embed output is normalized; cosine matches RAG conventions. |
| Source selection | Config-driven allowlist | Each `[[sources]]` block names exactly one path. Third-party clones under `~/Downloads/src/` stay invisible unless explicitly added. No folder reorg required. |
| MCP transport | stdio | Works for both Cowork (Cowork MCP settings) and Claude Code (`claude mcp add`). |
| Re-indexing | On-demand CLI, incremental via SHA-256 file hash | Watcher can come later. Manual re-index = no surprise CPU/embedding load. |
| Project layout | `pyproject.toml` + `src/local_rag/`, structured for GitHub | Matches repo → clone → populate workflow. |

## Decisions (deferred / open questions)

- **Code chunking:** v1 = line-based windows (~60 lines, 10 overlap). Tree-sitter for function-level chunking is the right v2 move, especially for Python. Skipped now to keep dep footprint thin.
- **Hybrid search (BM25 + vector):** LanceDB has Tantivy FTS built in. Worth adding once vector-only retrieval shows recall gaps for exact-term queries (project names, function names, `[[wikilink]]` targets).
- **Reindex trigger:** start manual. If I run it multiple times a day, add a watch mode using `watchdog`.
- **Per-repo query scoping defaults:** `search(query, sources=["vault","homelab"])` already supported. VS Code Claude Code may want a smarter default ("if I'm in repo X, prioritize that source"). Defer until usage tells us.

## Architecture

```
   sources                       indexer                store              server               clients
──────────────────         ───────────────────       ──────────       ────────────────       ───────────────
 Obsidian vault    ─┐                                                                       ┌─ Cowork
 ~/Downloads/src/A ─┼─►  chunker → embedder ─►       LanceDB    ─►   MCP stdio server  ────┤
 ~/Downloads/src/B ─┘    (md headings,                one table          tools:             └─ Claude Code
                          code line-windows)         per source       - search                  (VS Code)
                                                                      - list_sources
                                                                      - index_status
```

One LanceDB table per source. Each row: `{id, source_path, file_hash, chunk_index, char_start, char_end, heading_path, text, vector}`.

## Sources — initial config

```toml
db_path = "~/.local/share/local-rag/db"

[embedding]
provider = "ollama"
model = "nomic-embed-text"
url = "http://localhost:11434"
dim = 768

[[sources]]
name = "vault"
path = "~/Downloads/obsidian-vault"
type = "markdown"
ignore = [".obsidian/**", ".trash/**", "_resources/*.pdf", "_resources/*.png"]

[[sources]]
name = "local-rag"
path = "~/Downloads/src/local-rag"
type = "code"
respect_gitignore = true

# Add personal repos here as you go. Third-party clones under src/
# are NOT indexed unless they appear in this list.
# [[sources]]
# name = "homelab"
# path = "~/Downloads/src/homelab"
# type = "code"
# respect_gitignore = true
```

## CLI surface

```
local-rag index             # incremental reindex of all sources
local-rag index <source>    # reindex one
local-rag search "query"    # one-shot CLI search
local-rag list              # show sources + chunk counts
local-rag mcp               # run MCP server (stdio)
```

## MCP wiring

Once installed (`uv pip install -e .`):

- **Cowork** — add to Cowork's MCP settings as a stdio server. Command: `local-rag`, args: `["mcp"]`.
- **Claude Code (VS Code)** — `claude mcp add local-rag -- local-rag mcp` from any project, or per-project via `.mcp.json`.

## Don't (rules for the implementing agent)

- Don't add a vector index (`create_index`) for v1. Brute-force scan is fine for <100k chunks; the trigger to revisit is query latency >500 ms.
- Don't pull in `langchain` or `llama-index`. Do the chunking inline — both libraries are too heavy for what is essentially a 200-line job.
- Don't embed binary files. Use a strict extension allowlist (`.md`, `.txt`, `.rst`, `.py`, `.js`, `.ts`, `.go`, `.rs`, `.toml`, `.yaml`, `.json`, etc.); skip files >1 MB.
- Don't assume Ollama is running. Health-check on CLI startup; surface a clear error.
- Don't index by default — every source must be opt-in via config (the allowlist rule).

## Open work / current state (2026-05-11)

- Spec written; bootstrap landed in the repo (CLAUDE.md, pyproject, hooks, subagents). No implementation code yet.
- Project lives at `~/Downloads/src/local-rag/`.
- Next: vertical-slice implementation via the agentic loop (planner → test-first → implement → reviewer). Proposed order: config/paths → embedder client → store → chunkers → indexer → CLI → MCP server.

## References

The agentic methodology this project follows lives in the vault under `Research/Programming/Agentic Programming/`:
- `01 Context Management for Coding Agents`
- `02 Agentic Methodology Loop`
- `04 MD Files for Coding Agents`
- `starter-files/README` — bootstrap templates used to scaffold this project
