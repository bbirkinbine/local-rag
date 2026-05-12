"""Canonical filesystem paths for local-rag.

All paths returned are absolute and `~`-expanded. None of them require the
target to exist.
"""

from __future__ import annotations

import os
from pathlib import Path


def default_config_path() -> Path:
    """Return the default path to the local-rag config file."""
    return Path("~/.config/local-rag/config.toml").expanduser().resolve()


def config_path_from_env_or_default() -> Path:
    """Return ``$LOCAL_RAG_CONFIG`` (if set and non-empty) else the default.

    The env value is `~`-expanded and resolved to an absolute path.
    """
    override = os.environ.get("LOCAL_RAG_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return default_config_path()
