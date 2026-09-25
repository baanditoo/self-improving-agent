"""Core logic for the self-improving agent.

The agent uses a small mutation-and-selection loop (a cut-down evolutionary
algorithm in the spirit of Dawkins' "weasel" program). Starting from a random
guess, each generation mutates the current best candidate and keeps the
mutation only when it scores at least as well as the incumbent. Fitness is the
number of characters that match the target, so the score is monotonically
non-decreasing and the agent visibly improves over time.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass

# Characters the agent is allowed to use when guessing.
ALPHABET = string.ascii_letters + string.digits + string.punctuation + " "


@dataclass(frozen=True)
class Generation:
    """A single step in the agent's improvement history."""

    index: int
    candidate: str
    matches: int
    total: int

    @property
    def fitness(self) -> float:
        """Fraction of characters matched, in the range [0, 1]."""
        if self.total == 0:
            return 1.0
        return self.matches / self.total

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "candidate": self.candidate,
            "matches": self.matches,
            "total": self.total,
            "fitness": round(self.fitness, 4),
        }


def _score(candidate: str, target: str) -> int:
    """Count how many positions in ``candidate`` match ``target``."""
    return sum(1 for c, t in zip(candidate, target) if c == t)


def _random_candidate(length: int, rng: random.Random) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(length))


def _mutate(candidate: str, target: str, rng: random.Random, rate: float) -> str:
    """Return a copy of ``candidate`` with some characters randomly changed.

    Only positions that do not yet match the target are eligible to change, so
    correct characters are never discarded. At least one eligible position is
    always mutated to guarantee forward progress is possible.
    """
    chars = list(candidate)
    eligible = [i for i in range(len(chars)) if chars[i] != target[i]]
    if not eligible:
        return candidate

    mutated = False
    for i in eligible:
        if rng.random() < rate:
            chars[i] = rng.choice(ALPHABET)
            mutated = True
    if not mutated:
        i = rng.choice(eligible)
        chars[i] = rng.choice(ALPHABET)
    return "".join(chars)


def evolve(
    target: str,
    *,
    mutation_rate: float = 0.1,
    max_generations: int = 2000,
    seed: int | None = None,
) -> list[Generation]:
    """Evolve a random string toward ``target``.

    Returns the list of generations that represented an improvement (plus the
    initial guess), ending once the target is matched exactly or
    ``max_generations`` is reached.
    """
    if target == "":
        return [Generation(index=0, candidate="", matches=0, total=0)]

    rng = random.Random(seed)
    total = len(target)

    best = _random_candidate(total, rng)
    best_score = _score(best, target)
    history = [Generation(index=0, candidate=best, matches=best_score, total=total)]

    for generation in range(1, max_generations + 1):
        if best_score == total:
            break
        challenger = _mutate(best, target, rng, mutation_rate)
        challenger_score = _score(challenger, target)
        if challenger_score > best_score:
            best, best_score = challenger, challenger_score
            history.append(
                Generation(
                    index=generation,
                    candidate=best,
                    matches=best_score,
                    total=total,
                )
            )

    return history


class Agent:
    """A stateful wrapper around :func:`evolve`."""

    def __init__(
        self,
        *,
        mutation_rate: float = 0.1,
        max_generations: int = 2000,
        seed: int | None = None,
    ) -> None:
        self.mutation_rate = mutation_rate
        self.max_generations = max_generations
        self.seed = seed

    def improve(self, target: str) -> list[Generation]:
        """Run the improvement loop against ``target``."""
        return evolve(
            target,
            mutation_rate=self.mutation_rate,
            max_generations=self.max_generations,
            seed=self.seed,
        )

    def solve(self, target: str) -> str:
        """Return the best candidate the agent produced for ``target``."""
        history = self.improve(target)
        return history[-1].candidate if history else ""
