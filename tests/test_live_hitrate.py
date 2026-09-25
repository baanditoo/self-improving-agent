from self_improving_agent.live_hitrate import summarize


def test_summarize_counts_peak_at_least_2x():
    rows = [{"mult": 2.1}] * 7 + [{"mult": 1.1}] * 3
    stats = summarize(rows)
    assert stats["n"] == 10
    assert stats["initials_2x"] == 7
    assert stats["hit_ratio"] == 0.7
