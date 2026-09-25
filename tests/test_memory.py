from self_improving_agent.config import load_settings
from self_improving_agent.memory import MissedTracker
from self_improving_agent.models import Venue
from self_improving_agent.storage import Storage


def test_skipped_pump_later_3x_is_missed_runner(tmp_path):
    settings = load_settings(data_dir=tmp_path, max_extension_multiple=5)
    storage = Storage(tmp_path)
    now = 1_700_000_000
    storage.upsert_candidate(
        "PumpSkip111111111111111111111111111111111111",
        Venue.pumpfun.value,
        {
            "mint": "PumpSkip111111111111111111111111111111111111",
            "venue": "pumpfun",
            "price_usd": 1.0,
            "extension_multiple": 1.2,
            "reasons": ["below_threshold"],
            "red_flags": ["below_threshold"],
            "score": 40,
        },
        {"pumpfun": {"complete": False}},
        40,
        "skip",
        now,
    )
    tracker = MissedTracker(storage, settings)
    logged = tracker.mark_watchlist(
        {"PumpSkip111111111111111111111111111111111111": 3.0},
        now + 3600,
    )
    assert len(logged) == 1
    assert logged[0]["label"] == "MISSED_RUNNER"
    assert logged[0]["venue"] == "pumpfun"
    assert logged[0]["multiple"] == 3


def test_extended_coin_is_not_a_valid_miss(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    storage = Storage(tmp_path)
    now = 1_700_000_000
    storage.upsert_candidate(
        "Extended11111111111111111111111111111111111",
        Venue.stonkfun.value,
        {
            "price_usd": 1,
            "extension_multiple": 8,
            "reasons": ["already_extended"],
            "red_flags": ["already_extended"],
        },
        {},
        0,
        "skip",
        now,
    )
    logged = MissedTracker(storage, settings).mark_watchlist(
        {"Extended11111111111111111111111111111111111": 20},
        now + 4 * 3600,
    )
    assert logged == []
