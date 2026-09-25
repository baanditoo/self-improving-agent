"""Tests for the self-improving agent."""

from __future__ import annotations

import pytest

from self_improving_agent.agent import Agent, Generation, evolve


def test_evolve_solves_target_and_is_deterministic_with_seed():
    target = "weasel"
    history = evolve(target, seed=1)
    assert history[-1].candidate == target
    assert history[-1].matches == history[-1].total

    # Same seed -> identical run.
    again = evolve(target, seed=1)
    assert [g.candidate for g in history] == [g.candidate for g in again]


def test_fitness_is_monotonically_non_decreasing():
    history = evolve("improve me", seed=7)
    scores = [g.matches for g in history]
    assert scores == sorted(scores)
    assert scores[0] <= scores[-1]


def test_history_starts_at_generation_zero():
    history = evolve("abc", seed=3)
    assert history[0].index == 0
    assert all(isinstance(g, Generation) for g in history)


def test_empty_target_returns_trivial_history():
    history = evolve("", seed=0)
    assert len(history) == 1
    assert history[0].fitness == 1.0


def test_generation_to_dict_shape():
    g = Generation(index=2, candidate="hi", matches=1, total=2)
    d = g.to_dict()
    assert d == {
        "index": 2,
        "candidate": "hi",
        "matches": 1,
        "total": 2,
        "fitness": 0.5,
    }


def test_agent_solve_returns_target():
    agent = Agent(seed=99)
    assert agent.solve("hello") == "hello"


@pytest.mark.parametrize("phrase", ["a", "AI", "Self improvement!", "12345"])
def test_agent_solves_various_phrases(phrase):
    agent = Agent(seed=5)
    assert agent.solve(phrase) == phrase
