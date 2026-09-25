"""A tiny self-improving agent.

The package exposes an evolutionary optimizer that iteratively improves a
candidate solution toward a target, demonstrating measurable "self
improvement" over successive generations.
"""

from .agent import Agent, Generation, evolve

__all__ = ["Agent", "Generation", "evolve"]
__version__ = "0.1.0"
