"""Best-effort raise of the process's open-file soft limit.

LanceDB opens every FTS index partition file (``_docs``/``_invert``/``_tokens``
per partition) while merging index deltas. On a 36,739-chunk table that peaked
at 290 simultaneously open files (measured 2026-07-27) — above the 256 soft
limit launchd hands its jobs, and above the default a cron job inherits. The
result is not a crash but a silent quality regression: Lance logs
``Cannot open index on column 'text': Too many open files`` and *skips the
index merge*, leaving BM25 blind to the newest chunks until a later run gets
lucky.

Demand scales with partition count, which grows with both corpus size and
indexing history, so this is not a fixed budget we can size once and forget.
Rather than hard-code a number into every scheduler's config, the process
raises its own soft limit at startup — no privileges required, and it works
identically under launchd, cron, and a manual shell.
"""

from __future__ import annotations

import resource

import structlog

log = structlog.get_logger()

# What we ask for. Chosen as ~200x the measured 290-file peak so that corpora
# far larger than the author's clear it without another round of tuning, while
# staying an order of magnitude under a typical macOS kern.maxfilesperproc
# (245,760 here). Requests above the kernel ceiling are refused outright, so
# _BACKOFF handles the platforms where even this is too generous.
_TARGET_NOFILE = 65_536

# Successive fractions to retry on refusal. macOS rejects anything over
# kern.maxfilesperproc even when the hard limit is unlimited, and that ceiling
# is not portably readable — so probe downward instead of guessing it.
_BACKOFF = (1, 2, 4, 8, 16, 64)


def raise_open_file_limit(target: int = _TARGET_NOFILE) -> int:
    """Raise this process's ``RLIMIT_NOFILE`` soft limit toward ``target``.

    Never lowers an existing limit and never raises on failure — a refusal
    leaves the process running with whatever limit it already had, which at
    worst reproduces the pre-existing skipped-merge behaviour.

    Args:
        target: Soft limit to request, clamped to the hard limit.

    Returns:
        The soft limit in effect afterwards (``resource.RLIM_INFINITY`` if
        unlimited).
    """
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)

    if soft == resource.RLIM_INFINITY:
        return soft

    want = target if hard == resource.RLIM_INFINITY else min(target, hard)
    if soft >= want:
        return soft

    for divisor in _BACKOFF:
        attempt = want // divisor
        if attempt <= soft:
            break
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (attempt, hard))
        except (OSError, ValueError):
            continue
        log.debug("rlimit.raised", resource="nofile", old=soft, new=attempt)
        return attempt

    log.warning(
        "rlimit.raise_failed",
        resource="nofile",
        soft=soft,
        hard=hard,
        wanted=want,
        hint="large indexes may skip FTS index merges; raise the limit in your scheduler",
    )
    return soft
