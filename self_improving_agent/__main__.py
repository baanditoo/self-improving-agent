"""Command-line interface for the self-improving agent.

Usage:
    python -m self_improving_agent "target phrase" [--seed N] [--rate R]
"""

from __future__ import annotations

import argparse

from .agent import Agent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the self-improving agent.")
    parser.add_argument("target", help="Target phrase to evolve toward.")
    parser.add_argument("--seed", type=int, default=None, help="Random seed.")
    parser.add_argument(
        "--rate", type=float, default=0.12, help="Mutation rate (0-1)."
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final result, not every improvement.",
    )
    args = parser.parse_args(argv)

    agent = Agent(mutation_rate=args.rate, seed=args.seed)
    history = agent.improve(args.target)

    if not args.quiet:
        for g in history:
            bar = "#" * int(g.fitness * 20)
            print(f"gen {g.index:>5}  [{bar:<20}] {g.fitness*100:5.1f}%  {g.candidate}")

    final = history[-1]
    solved = final.matches == final.total
    print()
    print(f"{'Solved' if solved else 'Best effort'}: {final.candidate!r}")
    print(f"Improvement steps: {len(history)}")
    return 0 if solved else 1


if __name__ == "__main__":
    raise SystemExit(main())
