"""Tests for the Flask REST API endpoints."""

import pytest
from jobqueue.api import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_enqueue_valid_job(client):
    response = client.post(
        "/enqueue",
        json={"type": "sleep", "payload": {"seconds": 0.5}, "priority": "high"},
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data["created"] is True
    assert "id" in data


def test_enqueue_missing_type(client):
    response = client.post("/enqueue", json={"payload": {}})
    assert response.status_code == 400
    assert "missing required field 'type'" in response.get_json()["error"]


def test_enqueue_unknown_type(client):
    response = client.post("/enqueue", json={"type": "invalid_unknown_type"})
    assert response.status_code == 400
    assert "unknown job type" in response.get_json()["error"]


def test_enqueue_invalid_priority(client):
    response = client.post("/enqueue", json={"type": "sleep", "priority": "super-urgent"})
    assert response.status_code == 400
    assert "invalid priority" in response.get_json()["error"]


def test_enqueue_idempotency(client):
    key = "payment-order-12345"
    payload = {"type": "email", "payload": {"to": "buyer@example.com"}, "idempotency_key": key}

    # First request
    resp1 = client.post("/enqueue", json=payload)
    assert resp1.status_code == 201
    data1 = resp1.get_json()
    assert data1["created"] is True

    # Second duplicate request
    resp2 = client.post("/enqueue", json=payload)
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert data2["created"] is False
    assert data1["id"] == data2["id"]


def test_get_job_by_id(client):
    # Enqueue a job first
    create_resp = client.post("/enqueue", json={"type": "sleep", "payload": {"seconds": 1}})
    job_id = create_resp.get_json()["id"]

    # Fetch it
    get_resp = client.get("/jobs/%s" % job_id)
    assert get_resp.status_code == 200
    job = get_resp.get_json()
    assert job["id"] == job_id
    assert job["type"] == "sleep"
    assert job["status"] == "ready"


def test_get_nonexistent_job(client):
    response = client.get("/jobs/00000000000000000000000000000000")
    assert response.status_code == 404
    assert "job not found" in response.get_json()["error"]


def test_stats_endpoint(client):
    client.post("/enqueue", json={"type": "sleep"})
    response = client.get("/stats")
    assert response.status_code == 200
    stats = response.get_json()
    assert "ready" in stats
    assert "ready_total" in stats
    assert "inflight" in stats
    assert "dead" in stats
    assert stats["ready_total"] >= 1


def test_metrics_endpoint(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "jobqueue_queue_depth" in text

