"""Tests for the Flask web app."""

from __future__ import annotations

import pytest

from self_improving_agent.app import create_app


@pytest.fixture()
def client():
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_renders(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"Self-Improving Agent" in res.data


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json() == {"status": "ok"}


def test_improve_solves_target(client):
    res = client.post("/api/improve", json={"target": "hello", "seed": 1})
    assert res.status_code == 200
    data = res.get_json()
    assert data["solved"] is True
    assert data["generations"][-1]["candidate"] == "hello"
    assert data["target"] == "hello"


def test_improve_rejects_empty_target(client):
    res = client.post("/api/improve", json={"target": "  "})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_improve_rejects_too_long_target(client):
    res = client.post("/api/improve", json={"target": "x" * 200})
    assert res.status_code == 400
