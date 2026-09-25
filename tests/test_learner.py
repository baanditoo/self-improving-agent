from self_improving_agent.config import load_settings
from self_improving_agent.learner import learn
from self_improving_agent.scorer import DEFAULT_WEIGHTS
from self_improving_agent.storage import Storage


def test_failed_2x_high_top10_makes_only_that_venue_stricter(tmp_path):
    settings = load_settings(data_dir=tmp_path, min_learn_samples=30)
    storage = Storage(tmp_path)
    now = 1_700_000_000.0
    for idx in range(30):
        storage.add_missed(
            {
                "mint": f"Fail{idx}",
                "venue": "pumpfun",
                "label": "FAILED_2X",
                "ts": now - 3600,
                "multiple": 0.7,
                "reasons": ["holder_concentration"],
                "detail": {"top10_pct": 0.72, "components": {}},
            }
        )
    before = DEFAULT_WEIGHTS["concentration"]
    result = learn(settings, storage, hours=48, now=now)
    pump = result["weights"]["venues"]["pumpfun"]["weights"]["concentration"]
    stonk = result["weights"]["venues"]["stonkfun"]["weights"]["concentration"]
    gmgn = result["weights"]["venues"]["gmgn_other"]["weights"]["concentration"]
    assert pump < before
    assert stonk == before
    assert gmgn == before
    assert pump >= -25
