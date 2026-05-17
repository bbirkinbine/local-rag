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
```

Every source is opt-in. Third-party clones don't get indexed unless they have
a matching `[[sources]]` block.

## Full key reference

The complete schema — every key, the extension allowlist, the 1 MB per-file
size cap, the incremental SHA-256 hashing rules — lives in the
[spec](specs/local-rag.md).
