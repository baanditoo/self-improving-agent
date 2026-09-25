"""Watchlist of scored names we did not buy."""

from __future__ import annotations

from .models import TokenSnapshot
from .storage import Storage


def remember_skip(storage: Storage, snapshot: TokenSnapshot, now: float) -> None:
    storage.upsert_candidate(
        snapshot.mint,
        snapshot.venue.value,
        snapshot.model_dump(mode="json"),
        snapshot.raw,
        snapshot.score,
        "skip",
        now,
    )


def remember_entry(storage: Storage, snapshot: TokenSnapshot, now: float) -> None:
    storage.upsert_candidate(
        snapshot.mint,
        snapshot.venue.value,
        snapshot.model_dump(mode="json"),
        snapshot.raw,
        snapshot.score,
        "enter",
        now,
    )
