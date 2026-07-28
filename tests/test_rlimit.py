"""Tests for local_rag.rlimit — best-effort raise of the open-file soft limit."""

from __future__ import annotations

import resource

import pytest

from local_rag.rlimit import raise_open_file_limit


class _FakeRlimit:
    """Stand-in for the resource module's get/setrlimit pair.

    Mirrors kernel behaviour: soft may not exceed hard, and a request above
    ``ceiling`` (macOS ``kern.maxfilesperproc``) is refused outright.
    """

    def __init__(self, soft: int, hard: int, ceiling: int | None = None) -> None:
        self.soft = soft
        self.hard = hard
        self.ceiling = ceiling
        self.attempts: list[int] = []

    def getrlimit(self, _which: int) -> tuple[int, int]:
        return (self.soft, self.hard)

    def setrlimit(self, _which: int, limits: tuple[int, int]) -> None:
        want, hard = limits
        self.attempts.append(want)
        if self.hard != resource.RLIM_INFINITY and want > self.hard:
            raise ValueError("current limit exceeds maximum limit")
        if self.ceiling is not None and want > self.ceiling:
            raise ValueError("invalid argument")
        self.soft, self.hard = want, hard


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> object:
    def _install(soft: int, hard: int, ceiling: int | None = None) -> _FakeRlimit:
        f = _FakeRlimit(soft, hard, ceiling)
        monkeypatch.setattr(resource, "getrlimit", f.getrlimit)
        monkeypatch.setattr(resource, "setrlimit", f.setrlimit)
        return f

    return _install


# ------------------------------------------------------------------ raising ---


def test_raises_soft_limit_from_launchd_default(fake) -> None:  # type: ignore[no-untyped-def]
    """The 256 soft limit launchd hands its jobs is what caused EMFILE during
    FTS index merge; it must come up well clear of the measured 290 peak."""
    f = fake(256, resource.RLIM_INFINITY)

    new = raise_open_file_limit()

    assert new > 4096
    assert f.soft == new


def test_caps_at_finite_hard_limit(fake) -> None:  # type: ignore[no-untyped-def]
    """An unprivileged process cannot exceed its hard limit, so the request
    must be clamped rather than attempted and lost to an exception."""
    f = fake(1024, 4096)

    new = raise_open_file_limit()

    assert new == 4096
    assert f.soft == 4096
    assert max(f.attempts) <= 4096


def test_backs_off_below_kernel_ceiling(fake) -> None:  # type: ignore[no-untyped-def]
    """macOS refuses any request above kern.maxfilesperproc even when the hard
    limit is unlimited, so we must retry downward instead of giving up."""
    f = fake(256, resource.RLIM_INFINITY, ceiling=10_000)

    new = raise_open_file_limit()

    assert new > 256
    assert new <= 10_000
    assert f.soft == new


# -------------------------------------------------------------- no-op paths ---


def test_no_op_when_already_high_enough(fake) -> None:  # type: ignore[no-untyped-def]
    f = fake(1_048_576, resource.RLIM_INFINITY)

    new = raise_open_file_limit()

    assert new == 1_048_576
    assert f.attempts == []


def test_never_lowers_an_existing_limit(fake) -> None:  # type: ignore[no-untyped-def]
    f = fake(200_000, resource.RLIM_INFINITY)

    raise_open_file_limit()

    assert f.soft >= 200_000


def test_treats_infinite_soft_limit_as_sufficient(fake) -> None:  # type: ignore[no-untyped-def]
    f = fake(resource.RLIM_INFINITY, resource.RLIM_INFINITY)

    new = raise_open_file_limit()

    assert new == resource.RLIM_INFINITY
    assert f.attempts == []


# ------------------------------------------------------------- best effort ---


def test_returns_current_limit_when_every_attempt_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal must never take the indexing run down with it."""
    monkeypatch.setattr(resource, "getrlimit", lambda _w: (256, resource.RLIM_INFINITY))

    def _refuse(_which: int, _limits: tuple[int, int]) -> None:
        raise OSError("nope")

    monkeypatch.setattr(resource, "setrlimit", _refuse)

    assert raise_open_file_limit() == 256


def test_real_call_does_not_raise_or_lower() -> None:
    """Integration: against the real process limits, on whatever platform."""
    before, _ = resource.getrlimit(resource.RLIMIT_NOFILE)

    new = raise_open_file_limit()

    after, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    assert new == after
    assert after == resource.RLIM_INFINITY or after >= before
