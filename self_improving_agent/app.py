"""Flask web app that visualizes the self-improving agent."""

from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from .agent import Agent

MAX_TARGET_LENGTH = 120


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/api/improve")
    def improve():
        payload = request.get_json(silent=True) or {}
        target = str(payload.get("target", "")).strip()
        if not target:
            return jsonify(error="Please provide a non-empty target phrase."), 400
        if len(target) > MAX_TARGET_LENGTH:
            return (
                jsonify(
                    error=f"Target is too long (max {MAX_TARGET_LENGTH} characters)."
                ),
                400,
            )

        try:
            mutation_rate = float(payload.get("mutation_rate", 0.12))
        except (TypeError, ValueError):
            mutation_rate = 0.12
        mutation_rate = min(max(mutation_rate, 0.01), 1.0)

        seed = payload.get("seed")
        try:
            seed = int(seed) if seed is not None else None
        except (TypeError, ValueError):
            seed = None

        agent = Agent(mutation_rate=mutation_rate, max_generations=5000, seed=seed)
        history = agent.improve(target)

        return jsonify(
            target=target,
            solved=history[-1].matches == history[-1].total,
            generations=[g.to_dict() for g in history],
            steps=len(history),
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
