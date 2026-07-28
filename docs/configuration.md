# Configuration

`local-rag` reads its config from `~/.config/local-rag/config.toml`. Override
the path with `--config <path>` or the `LOCAL_RAG_CONFIG` env var.

## Example

```toml
db_path = "~/.local/share/local-rag/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

[[sources]]
name = "vault"
path = "~/Downloads/obsidian-vault"
type = "markdown"
ignore = [".obsidian/**", ".trash/**"]

[[sources]]
name = "local-rag"
path = "~/Downloads/src/local-rag"
type = "code"
respect_gitignore = true

[store]
keep_runs = 24
```

Every source is opt-in. Third-party clones don't get indexed unless they have
a matching `[[sources]]` block.

## `[store]`

Optional — omit the whole block to take the defaults.

| Key | Default | Meaning |
| --- | --- | --- |
| `keep_runs` | `24` | How many past indexing runs you can undo. `0` keeps only the current index. |

### `keep_runs`

Every indexing run writes a fresh copy of the index and leaves the previous
one behind. LanceDB never deletes those leftovers on its own, so `local-rag
index` does it — this setting says how many to keep.

Keeping more costs disk. Keeping fewer means less ability to recover from a
bad indexing run. That's the whole trade-off, and it doesn't depend on your
schedule: `keep_runs = 24` means 24 runs of undo whether you index every five
minutes or once a week. Nothing to calculate.

Roughly what it costs: on a 37k-chunk vault, each run leaves about 6-7 copies
behind at ~8.5 MB each, so 24 runs settles at ~1.3 GB. It scales with how much
actually changes between runs, not with how much time passes.

Until you have `keep_runs` runs on record, nothing is pruned — there is no
run N-ago to prune back to yet. A fresh store therefore grows for its first
few runs and then plateaus.

These copies are **not backups**. The index is derived entirely from your
source files, so `local-rag index --force` rebuilds it from scratch at any
time. They exist only to undo a recent bad run. See
[deployment](deployment.md) for the disk-growth symptom this addresses.

Run boundaries are recorded in `.run_log.json` beside the database. Deleting
it is harmless: indexing starts a new record and keeps everything until it
has `keep_runs` runs to work with again.

## Full key reference

The complete schema — every key, the extension allowlist, the 1 MB per-file
size cap, the incremental SHA-256 hashing rules — lives in the
[spec](specs/local-rag.md).
