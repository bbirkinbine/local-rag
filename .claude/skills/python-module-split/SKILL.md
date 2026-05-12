---
name: python-module-split
description: When a Python file exceeds ~300 lines, split it into a package. Use when reading a file and noticing it's too large, or when explicitly asked to split a module.
---

# Splitting a Python module into a package

Trigger: a `.py` file is approaching or past 300 lines, OR the user asks to split.

## Procedure

1. **Identify the natural seams.** Group related classes/functions by:
   - public API vs internal helpers
   - I/O concerns (HTTP, DB, files) vs pure logic
   - distinct responsibilities (parser, validator, executor)

2. **Convert to a package.**
   - Make `oldfile.py` → `oldfile/` directory
   - Create `oldfile/__init__.py` that re-exports the previous public API
   - Move groups into `oldfile/<group>.py`

3. **Preserve imports.** Anything importing `from package.oldfile import X` should still work — `__init__.py` handles that.

4. **Update tests.** Tests usually need no change if they import from the public API. Internal tests may need new paths.

5. **Verify.**
   - `uv run pytest` — green
   - `uv run mypy src/` — green
   - `uv run ruff check .` — green
   - `git diff --stat` — review the rename/move

## Don't

- Don't split by line count alone. Group by *concept*. A 350-line file with one cohesive concept stays as one file.
- Don't break the public API. If you must, that's a separate, larger change.
